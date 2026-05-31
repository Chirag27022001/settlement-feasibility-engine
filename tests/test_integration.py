"""Comprehensive integration tests for the Settlement Feasibility Engine.

Goes beyond the basic assertions in test_cases.py to verify:
- Fee fully collected
- Exact sum of creditor payments
- Non-negative balances throughout
- Round-trip simulation consistency
- Infeasible result's lump_sum application makes inputs feasible

Requirements validated: 1.1, 2.1, 5.1, 6.1, 7.4, 8.1, 9.3, 10.1, 11.1, 12.1,
                        13.3, 14.1, 15.1, 16.1, 17.1
"""

from __future__ import annotations

from datetime import date

import pytest

from feasibility.engine import evaluate_offer, round_half_up, simulate
from feasibility.models import LedgerEntry, load_case


def _run(case: str):
    client, offer, rules = load_case(f"cases/{case}")
    return evaluate_offer(client, offer, rules), client, offer, rules


# ---------------------------------------------------------------------------
# Case 1: Feasible Even
# ---------------------------------------------------------------------------


class TestCase1FeasibleEven:
    """Case 1: even_pays=true, bank_fee=1000, program_fee_pct=0.25."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.result, self.client, self.offer, self.rules = _run("case1_feasible_even")
        self.offer_total = round_half_up(
            self.offer.settlement_pct * self.offer.current_balance_cents
        )
        self.program_fee_total = round_half_up(
            self.rules.program_fee_pct * self.offer.original_balance_cents
        )

    def test_feasible_and_even_shape(self):
        assert self.result.feasible is True
        assert self.result.pay_shape_used == "even"
        assert self.result.schedule is not None

    def test_exact_sum_of_creditor_payments(self):
        """Validates: Requirement 5.1 — creditor payments sum exactly to offer_total."""
        creditor_sum = sum(
            row.creditor_payment_cents
            for row in self.result.schedule
            if row.creditor_payment_cents > 0
        )
        assert creditor_sum == self.offer_total  # 50000

    def test_all_payments_equal_or_differ_by_at_most_one_cent(self):
        """Validates: Requirement 10.1 — even pays are all equal (or differ by ≤1 cent)."""
        payments = [
            row.creditor_payment_cents
            for row in self.result.schedule
            if row.creditor_payment_cents > 0
        ]
        assert len(payments) > 0
        assert max(payments) - min(payments) <= 1

    def test_program_fee_fully_collected(self):
        """Validates: Requirement 9.3 — program fee fully collected."""
        fee_sum = sum(row.program_fee_cents for row in self.result.schedule)
        assert fee_sum == self.program_fee_total  # 30000

    def test_bank_fees_correct(self):
        """Validates: Requirement 8.1 — bank_fee_cents on each creditor payment date."""
        for row in self.result.schedule:
            if row.creditor_payment_cents > 0:
                assert row.bank_fee_cents == self.rules.bank_fee_cents  # 1000
            else:
                assert row.bank_fee_cents == 0

    def test_non_negative_balances_throughout(self):
        """Validates: Requirement 13.3 — balance ≥ 0 at every date."""
        assert all(row.balance_cents >= 0 for row in self.result.schedule)

    def test_non_decreasing_payment_sequence(self):
        """Validates: Requirement 6.1 — non-decreasing creditor payments."""
        payments = [
            row.creditor_payment_cents
            for row in self.result.schedule
            if row.creditor_payment_cents > 0
        ]
        for i in range(len(payments) - 1):
            assert payments[i] <= payments[i + 1]

    def test_additional_funds_is_none(self):
        """Validates: Requirement 14.1 — feasible result has no additional_funds."""
        assert self.result.additional_funds is None


# ---------------------------------------------------------------------------
# Case 2: Infeasible
# ---------------------------------------------------------------------------


class TestCase2Infeasible:
    """Case 2: infeasible, must compute lump_sum and monthly_increment."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.result, self.client, self.offer, self.rules = _run(
            "case2_infeasible_minima"
        )
        self.offer_total = round_half_up(
            self.offer.settlement_pct * self.offer.current_balance_cents
        )

    def test_infeasible_output_structure(self):
        """Validates: Requirement 17.1 — infeasible output structure."""
        assert self.result.feasible is False
        assert self.result.schedule is None
        assert self.result.pay_shape_used is None
        assert self.result.additional_funds is not None

    def test_lump_sum_values(self):
        """Validates: Requirement 15.1 — lump sum amount and guardrail."""
        af = self.result.additional_funds
        assert af.lump_sum.amount_cents == 10000
        assert af.lump_sum.within_guardrail is True

    def test_monthly_increment_values(self):
        """Validates: Requirement 16.1 — monthly increment amount and num_drafts."""
        af = self.result.additional_funds
        assert af.monthly_increment.amount_cents == 2500
        assert af.monthly_increment.num_drafts == 5
        assert af.monthly_increment.within_guardrail is True

    def test_lump_sum_makes_feasible(self):
        """Validates: Requirement 15.1 — applying lump_sum makes the offer feasible."""
        af = self.result.additional_funds
        lump_amount = af.lump_sum.amount_cents
        lump_date = af.lump_sum.date

        # Add the lump sum as an extra credit and re-evaluate
        extra_credit = LedgerEntry(
            date=lump_date, amount_cents=lump_amount, type="credit"
        )
        # Create a modified client with the extra credit in the ledger
        from dataclasses import replace

        modified_client = replace(
            self.client, ledger=self.client.ledger + [extra_credit]
        )
        new_result = evaluate_offer(modified_client, self.offer, self.rules)
        assert new_result.feasible is True

    def test_lump_sum_minus_one_not_feasible(self):
        """Validates: Requirement 15.1 — L-1 does NOT make it feasible (minimality)."""
        af = self.result.additional_funds
        lump_amount = af.lump_sum.amount_cents - 1
        lump_date = af.lump_sum.date

        extra_credit = LedgerEntry(
            date=lump_date, amount_cents=lump_amount, type="credit"
        )
        from dataclasses import replace

        modified_client = replace(
            self.client, ledger=self.client.ledger + [extra_credit]
        )
        new_result = evaluate_offer(modified_client, self.offer, self.rules)
        assert new_result.feasible is False

    def test_monthly_increment_makes_feasible(self):
        """Validates: Requirement 16.1 — applying monthly_increment makes the offer feasible."""
        af = self.result.additional_funds
        increment = af.monthly_increment.amount_cents

        # Add the increment to every future draft (credit entries after as_of_date)
        extra_credits = [
            LedgerEntry(date=e.date, amount_cents=increment, type="credit")
            for e in self.client.ledger
            if e.date > self.client.as_of_date and e.type == "credit"
        ]
        from dataclasses import replace

        modified_client = replace(
            self.client, ledger=self.client.ledger + extra_credits
        )
        new_result = evaluate_offer(modified_client, self.offer, self.rules)
        assert new_result.feasible is True

    def test_monthly_increment_minus_one_not_feasible(self):
        """Validates: Requirement 16.1 — X-1 does NOT make it feasible (minimality)."""
        af = self.result.additional_funds
        increment = af.monthly_increment.amount_cents - 1

        extra_credits = [
            LedgerEntry(date=e.date, amount_cents=increment, type="credit")
            for e in self.client.ledger
            if e.date > self.client.as_of_date and e.type == "credit"
        ]
        from dataclasses import replace

        modified_client = replace(
            self.client, ledger=self.client.ledger + extra_credits
        )
        new_result = evaluate_offer(modified_client, self.offer, self.rules)
        assert new_result.feasible is False


# ---------------------------------------------------------------------------
# Case 3: Balloon
# ---------------------------------------------------------------------------


class TestCase3Balloon:
    """Case 3: is_ballooning_allowed=true, program_fee_pct=0.0."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.result, self.client, self.offer, self.rules = _run("case3_balloon")
        self.offer_total = round_half_up(
            self.offer.settlement_pct * self.offer.current_balance_cents
        )
        self.program_fee_total = round_half_up(
            self.rules.program_fee_pct * self.offer.original_balance_cents
        )

    def test_feasible_and_balloon_shape(self):
        assert self.result.feasible is True
        assert self.result.pay_shape_used == "balloon"
        assert self.result.schedule is not None

    def test_all_payments_except_last_at_floor(self):
        """Validates: Requirement 11.1 — all but last at floor."""
        payments = [
            row.creditor_payment_cents
            for row in self.result.schedule
            if row.creditor_payment_cents > 0
        ]
        assert len(payments) > 1
        # All payments except the last should be at the floor (min_payment_cents = 2500)
        for p in payments[:-1]:
            assert p == self.rules.min_payment_cents  # 2500

    def test_last_payment_absorbs_remainder(self):
        """Validates: Requirement 11.1 — last payment absorbs remainder."""
        payments = [
            row.creditor_payment_cents
            for row in self.result.schedule
            if row.creditor_payment_cents > 0
        ]
        expected_last = self.offer_total - sum(payments[:-1])
        assert payments[-1] == expected_last

    def test_exact_sum(self):
        """Validates: Requirement 5.1 — exact sum = offer_total."""
        creditor_sum = sum(
            row.creditor_payment_cents
            for row in self.result.schedule
            if row.creditor_payment_cents > 0
        )
        assert creditor_sum == self.offer_total  # 30000

    def test_non_negative_balances(self):
        """Validates: Requirement 13.3 — balance ≥ 0 at every date."""
        assert all(row.balance_cents >= 0 for row in self.result.schedule)

    def test_non_decreasing_sequence(self):
        """Validates: Requirement 6.1 — non-decreasing."""
        payments = [
            row.creditor_payment_cents
            for row in self.result.schedule
            if row.creditor_payment_cents > 0
        ]
        for i in range(len(payments) - 1):
            assert payments[i] <= payments[i + 1]


# ---------------------------------------------------------------------------
# Case 4: Staircase (Tiers)
# ---------------------------------------------------------------------------


class TestCase4Staircase:
    """Case 4: staircase with min_payment_tiers=[[7, 5000]], max_segments=2."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.result, self.client, self.offer, self.rules = _run("case4_tiers")
        self.offer_total = round_half_up(
            self.offer.settlement_pct * self.offer.current_balance_cents
        )
        self.program_fee_total = round_half_up(
            self.rules.program_fee_pct * self.offer.original_balance_cents
        )

    def test_feasible_and_staircase_shape(self):
        assert self.result.feasible is True
        assert self.result.pay_shape_used == "staircase"
        assert self.result.schedule is not None

    def test_payments_7_plus_respect_tier_floor(self):
        """Validates: Requirement 7.4 — payments 7+ respect the $50 tier floor (5000 cents)."""
        payments = [
            row.creditor_payment_cents
            for row in self.result.schedule
            if row.creditor_payment_cents > 0
        ]
        # Payments at position 7+ (0-indexed: 6+) must be >= 5000
        assert all(p >= 5000 for p in payments[6:])

    def test_at_most_max_segments_distinct_levels(self):
        """Validates: Requirement 12.1 — at most max_segments (2) distinct payment levels."""
        payments = [
            row.creditor_payment_cents
            for row in self.result.schedule
            if row.creditor_payment_cents > 0
        ]
        distinct_levels = len(set(payments))
        assert distinct_levels <= self.rules.max_segments  # 2

    def test_exact_sum(self):
        """Validates: Requirement 5.1 — exact sum = offer_total."""
        creditor_sum = sum(
            row.creditor_payment_cents
            for row in self.result.schedule
            if row.creditor_payment_cents > 0
        )
        assert creditor_sum == self.offer_total  # 60000

    def test_non_decreasing_sequence(self):
        """Validates: Requirement 6.1 — non-decreasing."""
        payments = [
            row.creditor_payment_cents
            for row in self.result.schedule
            if row.creditor_payment_cents > 0
        ]
        for i in range(len(payments) - 1):
            assert payments[i] <= payments[i + 1]

    def test_program_fee_fully_collected(self):
        """Validates: Requirement 9.3 — program fee fully collected."""
        fee_sum = sum(row.program_fee_cents for row in self.result.schedule)
        assert fee_sum == self.program_fee_total  # 30000

    def test_non_negative_balances(self):
        """Validates: Requirement 13.3 — balance ≥ 0 at every date."""
        assert all(row.balance_cents >= 0 for row in self.result.schedule)

    def test_bank_fees_correct(self):
        """Validates: Requirement 8.1 — bank fee on creditor payment dates only."""
        for row in self.result.schedule:
            if row.creditor_payment_cents > 0:
                assert row.bank_fee_cents == self.rules.bank_fee_cents  # 500
            else:
                assert row.bank_fee_cents == 0


# ---------------------------------------------------------------------------
# Round-trip simulation tests
# ---------------------------------------------------------------------------


class TestRoundTripSimulation:
    """For each feasible case, replay the schedule through the simulator and verify balances match."""

    @pytest.mark.parametrize(
        "case_name",
        ["case1_feasible_even", "case3_balloon", "case4_tiers"],
    )
    def test_round_trip_balances(self, case_name):
        """Validates: Requirement 13.3 — schedule replayed through simulator produces same balances."""
        result, client, offer, rules = _run(case_name)
        assert result.feasible is True
        assert result.schedule is not None

        # Build creditor_schedule from the result's schedule rows
        creditor_schedule = [
            (
                row.date,
                row.creditor_payment_cents,
                row.program_fee_cents,
                row.bank_fee_cents,
            )
            for row in result.schedule
        ]

        # Replay through the simulator
        timeline = simulate(client, creditor_schedule)
        balance_map = {d: b for d, b in timeline}

        # Verify each schedule row's balance matches the simulation
        for row in result.schedule:
            assert row.date in balance_map, (
                f"Date {row.date} not found in simulation timeline"
            )
            assert row.balance_cents == balance_map[row.date], (
                f"Balance mismatch on {row.date}: "
                f"schedule={row.balance_cents}, simulation={balance_map[row.date]}"
            )

    @pytest.mark.parametrize(
        "case_name",
        ["case1_feasible_even", "case3_balloon", "case4_tiers"],
    )
    def test_simulation_never_negative(self, case_name):
        """Validates: Requirement 13.3 — simulation balance ≥ 0 at every date."""
        result, client, offer, rules = _run(case_name)
        assert result.feasible is True

        creditor_schedule = [
            (
                row.date,
                row.creditor_payment_cents,
                row.program_fee_cents,
                row.bank_fee_cents,
            )
            for row in result.schedule
        ]

        timeline = simulate(client, creditor_schedule)
        for d, balance in timeline:
            assert balance >= 0, f"Negative balance {balance} on {d}"


# ---------------------------------------------------------------------------
# Lump sum application test
# ---------------------------------------------------------------------------


class TestLumpSumApplication:
    """For case2, apply the lump_sum as an extra credit and verify feasibility."""

    def test_lump_sum_applied_makes_feasible(self):
        """Validates: Requirement 15.1 — lump_sum makes infeasible offer feasible."""
        result, client, offer, rules = _run("case2_infeasible_minima")
        assert result.feasible is False

        af = result.additional_funds
        lump_amount = af.lump_sum.amount_cents
        lump_date = af.lump_sum.date

        # Add lump sum as extra credit to the ledger
        extra_credit = LedgerEntry(
            date=lump_date, amount_cents=lump_amount, type="credit"
        )
        from dataclasses import replace

        modified_client = replace(
            client, ledger=client.ledger + [extra_credit]
        )

        # Re-evaluate with the extra credit
        new_result = evaluate_offer(modified_client, offer, rules)
        assert new_result.feasible is True
        assert new_result.schedule is not None

        # Verify the new schedule is valid
        creditor_sum = sum(
            row.creditor_payment_cents
            for row in new_result.schedule
            if row.creditor_payment_cents > 0
        )
        expected_offer_total = round_half_up(
            offer.settlement_pct * offer.current_balance_cents
        )
        assert creditor_sum == expected_offer_total

        # Verify non-negative balances in the new schedule
        assert all(row.balance_cents >= 0 for row in new_result.schedule)
