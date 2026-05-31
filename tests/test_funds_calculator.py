# Feature: settlement-feasibility-engine, Property 14: Lump Sum Minimality and Guardrail
# Feature: settlement-feasibility-engine, Property 15: Monthly Increment Minimality and Guardrail
"""Property-based tests for the funds calculator (lump sum and monthly increment).

**Validates: Requirements 15.1, 15.2, 15.3, 15.4, 16.1, 16.2, 16.3, 16.4**

Tests that:
- Property 14: For infeasible results, lump_sum.amount_cents (L) is minimal (L-1 not feasible),
  date ≤ horizon, and guardrail is correctly applied.
- Property 15: For infeasible results, monthly_increment.amount_cents (X) is minimal (X-1 not feasible),
  num_drafts equals count of future credit entries, and guardrail is correctly applied.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from feasibility.engine import evaluate_offer, round_half_up
from feasibility.models import (
    Client,
    CreditorRules,
    LedgerEntry,
    Offer,
)


# ---------------------------------------------------------------------------
# Hypothesis strategy for generating infeasible inputs
# ---------------------------------------------------------------------------


@st.composite
def infeasible_inputs(draw):
    """Generate (client, offer, rules) triples that are likely infeasible.

    Uses a small balance relative to a large offer to ensure infeasibility.
    """
    # Build a client with small balance and short horizon
    draft_amount = draw(st.integers(min_value=5000, max_value=10000))
    draft_day = 1
    start_month = 1
    start_year = 2026
    first_draft_date = date(start_year, start_month, draft_day)
    num_months = draw(st.integers(min_value=3, max_value=5))

    total_months = (start_year * 12 + (start_month - 1)) + num_months
    end_year, end_month_0 = divmod(total_months, 12)
    end_month = end_month_0 + 1
    last_draft_date = date(end_year, end_month, draft_day)

    as_of_date = date(start_year - 1, 12, 31)

    # Small starting balance
    current_balance = draw(st.integers(min_value=0, max_value=1000))

    # Build ledger with credits
    ledger: list[LedgerEntry] = []
    for i in range(num_months + 1):
        m_total = (start_year * 12 + (start_month - 1)) + i
        y, m0 = divmod(m_total, 12)
        m = m0 + 1
        entry_date = date(y, m, draft_day)
        if entry_date > last_draft_date:
            break
        ledger.append(LedgerEntry(date=entry_date, amount_cents=draft_amount, type="credit"))

    client = Client(
        draft_amount_cents=draft_amount,
        draft_day=draft_day,
        first_draft_date=first_draft_date,
        last_draft_date=last_draft_date,
        as_of_date=as_of_date,
        current_balance_cents=current_balance,
        ledger=ledger,
    )

    # Large offer relative to available funds
    total_available = current_balance + draft_amount * num_months
    # Make offer_total much larger than available funds
    creditor_balance = draw(st.integers(min_value=total_available * 3, max_value=total_available * 5))
    settlement_pct = draw(
        st.floats(min_value=0.6, max_value=0.9, allow_nan=False, allow_infinity=False)
    )
    original_balance = draw(
        st.integers(min_value=creditor_balance, max_value=creditor_balance * 2)
    )

    offer = Offer(
        creditor="TestCreditor",
        current_balance_cents=creditor_balance,
        original_balance_cents=original_balance,
        settlement_pct=settlement_pct,
        first_payment_date=date(start_year, start_month, 28),
    )

    # Rules with high fees and tight limits
    min_payment = draw(st.integers(min_value=5000, max_value=15000))
    bank_fee = draw(st.integers(min_value=500, max_value=2000))
    program_fee_pct = draw(
        st.floats(min_value=0.15, max_value=0.35, allow_nan=False, allow_infinity=False)
    )
    shape_choice = draw(st.sampled_from(["even", "balloon", "staircase"]))

    rules = CreditorRules(
        max_terms=draw(st.integers(min_value=3, max_value=5)),
        max_payments=draw(st.integers(min_value=3, max_value=5)),
        min_payment_cents=min_payment,
        max_token_pays=draw(st.integers(min_value=0, max_value=2)),
        min_payment_tiers=[],
        even_pays=(shape_choice == "even"),
        is_ballooning_allowed=(shape_choice == "balloon"),
        max_segments=2,
        bank_fee_cents=bank_fee,
        program_fee_pct=program_fee_pct,
    )

    return client, offer, rules


# ---------------------------------------------------------------------------
# Property 14: Lump Sum Minimality and Guardrail
# ---------------------------------------------------------------------------


class TestLumpSumMinimalityAndGuardrail:
    """Lump sum is minimal (L-1 not feasible), date ≤ horizon, guardrail correct.

    Property 14: For any infeasible result, the lump_sum.amount_cents (L) SHALL be
    the minimum value that makes the offer feasible when added as a credit on the
    reported date. L-1 SHALL NOT make it feasible. The date SHALL be ≤ horizon.
    within_guardrail SHALL be True iff L ≤ round_half_up(0.65 × offer_total).

    **Validates: Requirements 15.1, 15.2, 15.3, 15.4**
    """

    @settings(max_examples=100, deadline=None)
    @given(inputs=infeasible_inputs())
    def test_lump_sum_makes_feasible(self, inputs) -> None:
        """Adding L as a credit on the reported date makes the offer feasible."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(not result.feasible)
        assert result.additional_funds is not None

        af = result.additional_funds
        L = af.lump_sum.amount_cents
        lump_date = af.lump_sum.date

        assert L > 0, "Lump sum amount must be positive"
        assert lump_date is not None, "Lump sum date must be provided"

        # Add L as a credit on the reported date and verify feasibility
        extra_credit = LedgerEntry(date=lump_date, amount_cents=L, type="credit")
        modified_client = replace(client, ledger=client.ledger + [extra_credit])
        new_result = evaluate_offer(modified_client, offer, rules)

        assert new_result.feasible is True, (
            f"Adding lump sum L={L} on {lump_date} did not make the offer feasible"
        )

    @settings(max_examples=100, deadline=None)
    @given(inputs=infeasible_inputs())
    def test_lump_sum_minus_one_not_feasible(self, inputs) -> None:
        """Adding L-1 as a credit on the reported date does NOT make the offer feasible (minimality)."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(not result.feasible)
        assert result.additional_funds is not None

        af = result.additional_funds
        L = af.lump_sum.amount_cents
        lump_date = af.lump_sum.date

        # L-1 should not be feasible (minimality property)
        assume(L > 1)  # If L == 1, L-1 == 0 means no extra credit, which is the original infeasible case

        extra_credit = LedgerEntry(date=lump_date, amount_cents=L - 1, type="credit")
        modified_client = replace(client, ledger=client.ledger + [extra_credit])
        new_result = evaluate_offer(modified_client, offer, rules)

        assert new_result.feasible is False, (
            f"L-1={L-1} on {lump_date} made the offer feasible — L={L} is not minimal"
        )

    @settings(max_examples=100, deadline=None)
    @given(inputs=infeasible_inputs())
    def test_lump_sum_date_within_horizon(self, inputs) -> None:
        """Lump sum date is on or before the horizon (last_draft_date)."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(not result.feasible)
        assert result.additional_funds is not None

        af = result.additional_funds
        lump_date = af.lump_sum.date

        assert lump_date is not None
        assert lump_date <= client.last_draft_date, (
            f"Lump sum date {lump_date} exceeds horizon {client.last_draft_date}"
        )

    @settings(max_examples=100, deadline=None)
    @given(inputs=infeasible_inputs())
    def test_lump_sum_guardrail_correct(self, inputs) -> None:
        """within_guardrail is True iff L ≤ round_half_up(0.65 × offer_total)."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(not result.feasible)
        assert result.additional_funds is not None

        af = result.additional_funds
        L = af.lump_sum.amount_cents
        offer_total = round_half_up(offer.settlement_pct * offer.current_balance_cents)
        guardrail = round_half_up(0.65 * offer_total)

        expected_within = L <= guardrail
        assert af.lump_sum.within_guardrail == expected_within, (
            f"within_guardrail={af.lump_sum.within_guardrail} but expected "
            f"{expected_within} (L={L}, guardrail={guardrail}, offer_total={offer_total})"
        )

    @settings(max_examples=100, deadline=None)
    @given(inputs=infeasible_inputs())
    def test_lump_sum_reason_nonempty_when_guardrail_violated(self, inputs) -> None:
        """When within_guardrail is False, reason is non-empty."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(not result.feasible)
        assert result.additional_funds is not None

        af = result.additional_funds
        if not af.lump_sum.within_guardrail:
            assert af.lump_sum.reason != "", (
                "Lump sum guardrail violated but reason is empty"
            )


# ---------------------------------------------------------------------------
# Property 15: Monthly Increment Minimality and Guardrail
# ---------------------------------------------------------------------------


class TestMonthlyIncrementMinimalityAndGuardrail:
    """Monthly increment is minimal (X-1 not feasible), num_drafts correct, guardrail correct.

    Property 15: For any infeasible result, the monthly_increment.amount_cents (X)
    SHALL be the minimum value that makes the offer feasible when added to every
    future draft. X-1 SHALL NOT make it feasible. num_drafts SHALL equal the count
    of credit entries in ledger dated after as_of_date. within_guardrail SHALL be
    True iff X ≤ max(10000, round_half_up(0.40 × draft_amount_cents)).

    **Validates: Requirements 16.1, 16.2, 16.3, 16.4**
    """

    @settings(max_examples=100, deadline=None)
    @given(inputs=infeasible_inputs())
    def test_monthly_increment_makes_feasible(self, inputs) -> None:
        """Adding X to every future draft makes the offer feasible."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(not result.feasible)
        assert result.additional_funds is not None

        af = result.additional_funds
        X = af.monthly_increment.amount_cents

        assume(X > 0)  # Skip cases where no future drafts exist

        # Identify future draft dates (credit entries after as_of_date)
        future_draft_dates = sorted(set(
            e.date for e in client.ledger
            if e.date > client.as_of_date and e.type == "credit"
        ))

        assume(len(future_draft_dates) > 0)

        # Add X to each future draft
        extra_credits = [
            LedgerEntry(date=d, amount_cents=X, type="credit")
            for d in future_draft_dates
        ]
        modified_client = replace(client, ledger=client.ledger + extra_credits)
        new_result = evaluate_offer(modified_client, offer, rules)

        assert new_result.feasible is True, (
            f"Adding monthly increment X={X} to {len(future_draft_dates)} future drafts "
            f"did not make the offer feasible"
        )

    @settings(max_examples=100, deadline=None)
    @given(inputs=infeasible_inputs())
    def test_monthly_increment_minus_one_not_feasible(self, inputs) -> None:
        """Adding X-1 to every future draft does NOT make the offer feasible (minimality)."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(not result.feasible)
        assert result.additional_funds is not None

        af = result.additional_funds
        X = af.monthly_increment.amount_cents

        # X-1 should not be feasible (minimality property)
        assume(X > 1)  # If X == 1, X-1 == 0 means no extra credit

        # Identify future draft dates (credit entries after as_of_date)
        future_draft_dates = sorted(set(
            e.date for e in client.ledger
            if e.date > client.as_of_date and e.type == "credit"
        ))

        assume(len(future_draft_dates) > 0)

        # Add X-1 to each future draft
        extra_credits = [
            LedgerEntry(date=d, amount_cents=X - 1, type="credit")
            for d in future_draft_dates
        ]
        modified_client = replace(client, ledger=client.ledger + extra_credits)
        new_result = evaluate_offer(modified_client, offer, rules)

        assert new_result.feasible is False, (
            f"X-1={X-1} added to {len(future_draft_dates)} future drafts made the offer "
            f"feasible — X={X} is not minimal"
        )

    @settings(max_examples=100, deadline=None)
    @given(inputs=infeasible_inputs())
    def test_monthly_increment_num_drafts_correct(self, inputs) -> None:
        """num_drafts equals the count of credit entries in ledger dated after as_of_date."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(not result.feasible)
        assert result.additional_funds is not None

        af = result.additional_funds

        # Count future credit entries (distinct dates after as_of_date)
        expected_num_drafts = len(sorted(set(
            e.date for e in client.ledger
            if e.date > client.as_of_date and e.type == "credit"
        )))

        assert af.monthly_increment.num_drafts == expected_num_drafts, (
            f"num_drafts={af.monthly_increment.num_drafts} but expected "
            f"{expected_num_drafts} (credit entries after {client.as_of_date})"
        )

    @settings(max_examples=100, deadline=None)
    @given(inputs=infeasible_inputs())
    def test_monthly_increment_guardrail_correct(self, inputs) -> None:
        """within_guardrail is True iff X ≤ max(10000, round_half_up(0.40 × draft_amount_cents))."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(not result.feasible)
        assert result.additional_funds is not None

        af = result.additional_funds
        X = af.monthly_increment.amount_cents

        guardrail_limit = max(10000, round_half_up(0.40 * client.draft_amount_cents))
        expected_within = X <= guardrail_limit

        assert af.monthly_increment.within_guardrail == expected_within, (
            f"within_guardrail={af.monthly_increment.within_guardrail} but expected "
            f"{expected_within} (X={X}, guardrail={guardrail_limit}, "
            f"draft_amount={client.draft_amount_cents})"
        )

    @settings(max_examples=100, deadline=None)
    @given(inputs=infeasible_inputs())
    def test_monthly_increment_reason_nonempty_when_guardrail_violated(self, inputs) -> None:
        """When within_guardrail is False, reason is non-empty."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(not result.feasible)
        assert result.additional_funds is not None

        af = result.additional_funds
        if not af.monthly_increment.within_guardrail:
            assert af.monthly_increment.reason != "", (
                "Monthly increment guardrail violated but reason is empty"
            )
