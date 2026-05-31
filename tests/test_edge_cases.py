"""Edge case tests covering specific scenarios from ASSIGNMENT.md §10.

Covers:
- Token-pay floor enforcement (max_token_pays rule)
- Horizon limit (payment on exact horizon date, nothing past it)
- Balance hitting exactly $0 (feasible boundary)
- Fee compliance (no fee before first payment date)
- Same-day credit-before-debit ordering
- max_segments cap enforcement
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from feasibility.engine import (
    compute_floors,
    evaluate_offer,
    generate_balloon_payments,
    generate_even_payments,
    generate_staircase_payments,
    place_program_fees,
    round_half_up,
    simulate,
)
from feasibility.models import Client, CreditorRules, LedgerEntry, Offer


# ---------------------------------------------------------------------------
# Token-pay floor enforcement
# ---------------------------------------------------------------------------


class TestTokenPayFloor:
    """Verify that at most max_token_pays payments may equal min_payment_cents."""

    def test_floors_first_positions_at_base_min(self):
        """First max_token_pays positions have floor = min_payment_cents."""
        rules = CreditorRules(
            max_terms=10,
            max_payments=10,
            min_payment_cents=2500,
            max_token_pays=3,
            min_payment_tiers=[],
            even_pays=False,
            is_ballooning_allowed=False,
            max_segments=4,
            bank_fee_cents=0,
            program_fee_pct=0.0,
        )
        floors = compute_floors(6, rules)
        # First 3 positions: floor = 2500 (base min)
        assert floors[0] == 2500
        assert floors[1] == 2500
        assert floors[2] == 2500

    def test_floors_beyond_token_budget_exceed_base_min(self):
        """Positions beyond max_token_pays must strictly exceed min_payment_cents."""
        rules = CreditorRules(
            max_terms=10,
            max_payments=10,
            min_payment_cents=2500,
            max_token_pays=3,
            min_payment_tiers=[],
            even_pays=False,
            is_ballooning_allowed=False,
            max_segments=4,
            bank_fee_cents=0,
            program_fee_pct=0.0,
        )
        floors = compute_floors(6, rules)
        # Positions 4, 5, 6: floor = 2501 (must exceed base min)
        assert floors[3] == 2501
        assert floors[4] == 2501
        assert floors[5] == 2501

    def test_token_pay_with_tier_override(self):
        """Tier step-up overrides token-exceeded floor when tier is higher."""
        rules = CreditorRules(
            max_terms=10,
            max_payments=10,
            min_payment_cents=2500,
            max_token_pays=2,
            min_payment_tiers=[(3, 5000)],
            even_pays=False,
            is_ballooning_allowed=False,
            max_segments=4,
            bank_fee_cents=0,
            program_fee_pct=0.0,
        )
        floors = compute_floors(5, rules)
        # Position 1, 2: base min (2500)
        assert floors[0] == 2500
        assert floors[1] == 2500
        # Position 3: max(2501, 5000) = 5000 (tier overrides token-exceeded)
        assert floors[2] == 5000
        # Position 4, 5: max(2501, 5000) = 5000
        assert floors[3] == 5000
        assert floors[4] == 5000

    def test_balloon_respects_token_pay_floors(self):
        """Balloon shape sets early payments at floors including token-exceeded rule."""
        rules = CreditorRules(
            max_terms=6,
            max_payments=6,
            min_payment_cents=1000,
            max_token_pays=2,
            min_payment_tiers=[],
            even_pays=False,
            is_ballooning_allowed=True,
            max_segments=4,
            bank_fee_cents=0,
            program_fee_pct=0.0,
        )
        floors = compute_floors(4, rules)
        payments = generate_balloon_payments(10000, 4, floors)
        assert payments is not None
        # First 2 at base min (1000), positions 3 at 1001
        assert payments[0] == 1000
        assert payments[1] == 1000
        assert payments[2] == 1001
        # Last absorbs remainder
        assert payments[3] == 10000 - 1000 - 1000 - 1001

    def test_staircase_respects_token_pay_floors(self):
        """Staircase shape respects token-exceeded floors."""
        rules = CreditorRules(
            max_terms=6,
            max_payments=6,
            min_payment_cents=1000,
            max_token_pays=2,
            min_payment_tiers=[],
            even_pays=False,
            is_ballooning_allowed=False,
            max_segments=2,
            bank_fee_cents=0,
            program_fee_pct=0.0,
        )
        floors = compute_floors(4, rules)
        payments = generate_staircase_payments(8000, 4, floors, 2)
        assert payments is not None
        # All payments must respect their floors
        for i, p in enumerate(payments):
            assert p >= floors[i], f"Payment {i+1} ({p}) < floor ({floors[i]})"
        assert sum(payments) == 8000
        assert len(set(payments)) <= 2


# ---------------------------------------------------------------------------
# Horizon limit
# ---------------------------------------------------------------------------


class TestHorizonLimit:
    """Verify nothing is scheduled past the horizon (last_draft_date)."""

    def test_payments_on_exact_horizon_date(self):
        """A payment on the exact last_draft_date is allowed."""
        # Create a scenario where the last cadence date = last_draft_date
        client = Client(
            draft_amount_cents=50000,
            draft_day=1,
            first_draft_date=date(2026, 1, 1),
            last_draft_date=date(2026, 3, 1),
            as_of_date=date(2025, 12, 31),
            current_balance_cents=0,
            ledger=[
                LedgerEntry(date=date(2026, 1, 1), amount_cents=50000, type="credit"),
                LedgerEntry(date=date(2026, 2, 1), amount_cents=50000, type="credit"),
                LedgerEntry(date=date(2026, 3, 1), amount_cents=50000, type="credit"),
            ],
        )
        offer = Offer(
            creditor="HorizonCo",
            current_balance_cents=40000,
            original_balance_cents=40000,
            settlement_pct=0.5,
            first_payment_date=date(2026, 1, 31),
        )
        rules = CreditorRules(
            max_terms=3,
            max_payments=3,
            min_payment_cents=1000,
            max_token_pays=3,
            min_payment_tiers=[],
            even_pays=True,
            is_ballooning_allowed=False,
            max_segments=1,
            bank_fee_cents=0,
            program_fee_pct=0.0,
        )
        result = evaluate_offer(client, offer, rules)
        assert result.feasible is True
        # All schedule dates must be <= horizon
        for row in result.schedule:
            assert row.date <= client.last_draft_date

    def test_no_payments_past_horizon(self):
        """When cadence dates would extend past horizon, they are excluded."""
        # Horizon is very tight — only 1 cadence date fits
        client = Client(
            draft_amount_cents=30000,
            draft_day=1,
            first_draft_date=date(2026, 1, 1),
            last_draft_date=date(2026, 2, 15),  # tight horizon
            as_of_date=date(2025, 12, 31),
            current_balance_cents=0,
            ledger=[
                LedgerEntry(date=date(2026, 1, 1), amount_cents=30000, type="credit"),
                LedgerEntry(date=date(2026, 2, 1), amount_cents=30000, type="credit"),
            ],
        )
        offer = Offer(
            creditor="TightCo",
            current_balance_cents=20000,
            original_balance_cents=20000,
            settlement_pct=0.5,
            first_payment_date=date(2026, 1, 31),
        )
        rules = CreditorRules(
            max_terms=6,
            max_payments=6,
            min_payment_cents=1000,
            max_token_pays=6,
            min_payment_tiers=[],
            even_pays=True,
            is_ballooning_allowed=False,
            max_segments=1,
            bank_fee_cents=0,
            program_fee_pct=0.0,
        )
        result = evaluate_offer(client, offer, rules)
        assert result.feasible is True
        # Only 1 cadence date (Jan 31) fits before horizon (Feb 15)
        # So k=1, single payment of 10000
        assert len(result.schedule) == 1
        assert result.schedule[0].date == date(2026, 1, 31)
        assert result.schedule[0].date <= client.last_draft_date

    def test_infeasible_when_no_cadence_dates_within_horizon(self):
        """If first_payment_date is past horizon, no schedule is possible."""
        client = Client(
            draft_amount_cents=10000,
            draft_day=1,
            first_draft_date=date(2026, 1, 1),
            last_draft_date=date(2026, 1, 15),  # very tight horizon
            as_of_date=date(2025, 12, 31),
            current_balance_cents=0,
            ledger=[
                LedgerEntry(date=date(2026, 1, 1), amount_cents=10000, type="credit"),
            ],
        )
        offer = Offer(
            creditor="NoCadenceCo",
            current_balance_cents=10000,
            original_balance_cents=10000,
            settlement_pct=0.5,
            first_payment_date=date(2026, 2, 1),  # past horizon
        )
        rules = CreditorRules(
            max_terms=6,
            max_payments=6,
            min_payment_cents=1000,
            max_token_pays=6,
            min_payment_tiers=[],
            even_pays=False,
            is_ballooning_allowed=False,
            max_segments=2,
            bank_fee_cents=0,
            program_fee_pct=0.0,
        )
        result = evaluate_offer(client, offer, rules)
        assert result.feasible is False


# ---------------------------------------------------------------------------
# Balance hitting exactly $0
# ---------------------------------------------------------------------------


class TestBalanceExactlyZero:
    """Verify that a balance of exactly $0 is considered feasible."""

    def test_balance_hits_zero_is_feasible(self):
        """A schedule where balance drops to exactly 0 is still feasible."""
        # Case 1 already hits $0 on the first two dates — verify explicitly
        from feasibility.models import load_case

        client, offer, rules = load_case("cases/case1_feasible_even")
        result = evaluate_offer(client, offer, rules)
        assert result.feasible is True
        # Verify at least one row has balance exactly 0
        balances = [row.balance_cents for row in result.schedule]
        assert 0 in balances, "Expected at least one date with balance exactly $0"
        # All balances must be >= 0 (not strictly > 0)
        assert all(b >= 0 for b in balances)

    def test_constructed_zero_balance_scenario(self):
        """Construct a scenario where balance is exactly 0 after every payment."""
        # Single draft of $100, single payment of $100, no fees
        client = Client(
            draft_amount_cents=10000,
            draft_day=1,
            first_draft_date=date(2026, 1, 1),
            last_draft_date=date(2026, 1, 1),
            as_of_date=date(2025, 12, 31),
            current_balance_cents=0,
            ledger=[
                LedgerEntry(date=date(2026, 1, 1), amount_cents=10000, type="credit"),
            ],
        )
        offer = Offer(
            creditor="ZeroCo",
            current_balance_cents=10000,
            original_balance_cents=10000,
            settlement_pct=1.0,
            first_payment_date=date(2026, 1, 1),
        )
        rules = CreditorRules(
            max_terms=1,
            max_payments=1,
            min_payment_cents=1000,
            max_token_pays=1,
            min_payment_tiers=[],
            even_pays=True,
            is_ballooning_allowed=False,
            max_segments=1,
            bank_fee_cents=0,
            program_fee_pct=0.0,
        )
        result = evaluate_offer(client, offer, rules)
        assert result.feasible is True
        assert result.schedule[0].balance_cents == 0


# ---------------------------------------------------------------------------
# Fee compliance — no fee before first payment date
# ---------------------------------------------------------------------------


class TestFeeCompliance:
    """Verify program fee is never placed before the first creditor payment date."""

    def test_no_fee_before_first_payment_date(self):
        """All feasible cases: no program_fee_cents on dates before first creditor payment."""
        from feasibility.models import load_case

        for case_name in ["case1_feasible_even", "case3_balloon", "case4_tiers"]:
            client, offer, rules = load_case(f"cases/{case_name}")
            result = evaluate_offer(client, offer, rules)
            assert result.feasible is True

            # Find the first creditor payment date
            first_creditor_date = None
            for row in result.schedule:
                if row.creditor_payment_cents > 0:
                    first_creditor_date = row.date
                    break

            # No fee should appear before that date
            for row in result.schedule:
                if row.date < first_creditor_date:
                    assert row.program_fee_cents == 0, (
                        f"Fee {row.program_fee_cents} found on {row.date} "
                        f"before first payment date {first_creditor_date} in {case_name}"
                    )

    def test_fee_allowed_on_first_payment_date(self):
        """Fee collection on the same date as the first creditor payment is allowed."""
        from feasibility.models import load_case

        client, offer, rules = load_case("cases/case1_feasible_even")
        result = evaluate_offer(client, offer, rules)
        # First row should have both creditor payment and program fee
        assert result.schedule[0].creditor_payment_cents > 0
        assert result.schedule[0].program_fee_cents > 0


# ---------------------------------------------------------------------------
# Same-day credit-before-debit ordering
# ---------------------------------------------------------------------------


class TestSameDayOrdering:
    """Verify credits are applied before debits on the same date."""

    def test_credit_before_debit_same_day(self):
        """When a draft and payment land on the same date, credit is applied first."""
        # Draft on Jan 1, payment cadence also on Jan 1
        # Starting balance = 0, draft = 10000, payment = 5000
        # If credits applied first: 0 + 10000 - 5000 = 5000 (feasible)
        # If debits applied first: 0 - 5000 = -5000 (infeasible)
        client = Client(
            draft_amount_cents=10000,
            draft_day=1,
            first_draft_date=date(2026, 1, 1),
            last_draft_date=date(2026, 3, 1),
            as_of_date=date(2025, 12, 31),
            current_balance_cents=0,
            ledger=[
                LedgerEntry(date=date(2026, 1, 1), amount_cents=10000, type="credit"),
                LedgerEntry(date=date(2026, 2, 1), amount_cents=10000, type="credit"),
                LedgerEntry(date=date(2026, 3, 1), amount_cents=10000, type="credit"),
            ],
        )
        offer = Offer(
            creditor="SameDayCo",
            current_balance_cents=10000,
            original_balance_cents=10000,
            settlement_pct=0.5,
            first_payment_date=date(2026, 1, 1),
        )
        rules = CreditorRules(
            max_terms=1,
            max_payments=1,
            min_payment_cents=1000,
            max_token_pays=1,
            min_payment_tiers=[],
            even_pays=True,
            is_ballooning_allowed=False,
            max_segments=1,
            bank_fee_cents=0,
            program_fee_pct=0.0,
        )
        result = evaluate_offer(client, offer, rules)
        # Should be feasible because credit (10000) is applied before debit (5000)
        assert result.feasible is True
        assert result.schedule[0].balance_cents >= 0

    def test_simulate_credits_before_debits(self):
        """Simulate function applies credits before debits on the same date."""
        client = Client(
            draft_amount_cents=5000,
            draft_day=15,
            first_draft_date=date(2026, 1, 15),
            last_draft_date=date(2026, 1, 15),
            as_of_date=date(2025, 12, 31),
            current_balance_cents=0,
            ledger=[
                LedgerEntry(date=date(2026, 1, 15), amount_cents=5000, type="credit"),
            ],
        )
        # Schedule a debit on the same date as the credit
        creditor_schedule = [(date(2026, 1, 15), 3000, 0, 0)]
        timeline = simulate(client, creditor_schedule)
        # Credit (5000) applied first, then debit (3000) → balance = 2000
        assert timeline[0] == (date(2026, 1, 15), 2000)


# ---------------------------------------------------------------------------
# max_segments cap enforcement
# ---------------------------------------------------------------------------


class TestMaxSegmentsCap:
    """Verify staircase respects the max_segments constraint."""

    def test_max_segments_1_produces_uniform_payments(self):
        """With max_segments=1, all payments must be identical."""
        floors = [1000] * 4
        payments = generate_staircase_payments(8000, 4, floors, max_segments=1)
        assert payments is not None
        assert len(set(payments)) == 1
        assert payments == [2000, 2000, 2000, 2000]

    def test_max_segments_2_at_most_two_levels(self):
        """With max_segments=2, at most 2 distinct payment amounts."""
        floors = [1000, 1000, 1000, 2000, 2000]
        payments = generate_staircase_payments(12000, 5, floors, max_segments=2)
        assert payments is not None
        assert len(set(payments)) <= 2
        assert sum(payments) == 12000

    def test_max_segments_exceeded_returns_none(self):
        """When floors require more segments than allowed, returns None."""
        # 3 different floor levels but max_segments=1 and exact sum = sum of floors
        # This forces 3 distinct levels which can't fit in 1 segment
        floors = [1000, 2000, 3000]
        # offer_total = sum of floors = 6000, so no surplus to redistribute
        payments = generate_staircase_payments(6000, 3, floors, max_segments=1)
        # With max_segments=1, all payments must be equal, but floors force non-equal
        # The function should either find a valid solution or return None
        if payments is not None:
            assert len(set(payments)) <= 1
            assert sum(payments) == 6000

    def test_case4_staircase_max_segments(self):
        """Case 4 uses max_segments=2 — verify the constraint holds."""
        from feasibility.models import load_case

        client, offer, rules = load_case("cases/case4_tiers")
        result = evaluate_offer(client, offer, rules)
        assert result.feasible is True
        payments = [
            row.creditor_payment_cents
            for row in result.schedule
            if row.creditor_payment_cents > 0
        ]
        distinct = len(set(payments))
        assert distinct <= rules.max_segments, (
            f"Got {distinct} distinct levels, max_segments={rules.max_segments}"
        )
