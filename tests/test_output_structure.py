# Feature: settlement-feasibility-engine, Property 3: Payment Placement and Count
# Feature: settlement-feasibility-engine, Property 6: Composite Floor Enforcement
# Feature: settlement-feasibility-engine, Property 13: Output Structure Invariants
"""Property-based tests for feasible output structure.

**Validates: Requirements 4.1, 4.2, 4.3, 7.1, 7.2, 7.3, 7.4, 14.1, 14.2, 17.1, 17.2, 19.1, 19.2**

Tests that:
- Property 3: Creditor payment dates are consecutive cadence dates, k within bounds, all ≤ horizon
- Property 6: Each payment ≥ effective floor, token count ≤ max_token_pays
- Property 13: Feasible → schedule not None, pay_shape_used valid, additional_funds None;
              Infeasible → schedule None, pay_shape_used None, additional_funds not None
"""

from __future__ import annotations

from datetime import date

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from feasibility.engine import evaluate_offer, compute_floors, round_half_up
from feasibility.models import (
    Client,
    CreditorRules,
    LedgerEntry,
    Offer,
    monthly_payment_dates,
    default_first_payment_date,
)


# ---------------------------------------------------------------------------
# Hypothesis strategies for generating valid inputs
# ---------------------------------------------------------------------------


@st.composite
def feasible_inputs(draw):
    """Generate (client, offer, rules) triples that are likely feasible.

    Uses a large balance relative to the offer to ensure feasibility.
    """
    # Build a client with generous balance
    draft_amount = draw(st.integers(min_value=20000, max_value=50000))
    draft_day = 1
    start_month = 1
    start_year = 2026
    first_draft_date = date(start_year, start_month, draft_day)
    num_months = draw(st.integers(min_value=6, max_value=12))

    total_months = (start_year * 12 + (start_month - 1)) + num_months
    end_year, end_month_0 = divmod(total_months, 12)
    end_month = end_month_0 + 1
    last_draft_date = date(end_year, end_month, draft_day)

    as_of_date = date(start_year - 1, 12, 31)

    # Large starting balance to help feasibility
    current_balance = draw(st.integers(min_value=draft_amount, max_value=draft_amount * 3))

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

    # Small offer relative to available funds
    total_available = current_balance + draft_amount * num_months
    max_offer_total = total_available // 3
    creditor_balance = draw(st.integers(min_value=10000, max_value=max(10001, max_offer_total)))
    settlement_pct = draw(
        st.floats(min_value=0.1, max_value=0.5, allow_nan=False, allow_infinity=False)
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

    # Rules with small fees and generous limits
    min_payment = draw(st.integers(min_value=500, max_value=2000))
    bank_fee = draw(st.integers(min_value=0, max_value=500))
    program_fee_pct = draw(
        st.floats(min_value=0.0, max_value=0.15, allow_nan=False, allow_infinity=False)
    )
    shape_choice = draw(st.sampled_from(["even", "balloon", "staircase"]))

    rules = CreditorRules(
        max_terms=12,
        max_payments=12,
        min_payment_cents=min_payment,
        max_token_pays=12,
        min_payment_tiers=[],
        even_pays=(shape_choice == "even"),
        is_ballooning_allowed=(shape_choice == "balloon"),
        max_segments=3,
        bank_fee_cents=bank_fee,
        program_fee_pct=program_fee_pct,
    )

    return client, offer, rules


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
# Property 3: Payment Placement and Count
# ---------------------------------------------------------------------------


class TestPaymentPlacementAndCount:
    """Creditor payment dates are consecutive cadence dates, k within bounds, all ≤ horizon.

    Property 3: For any feasible result, the creditor payment dates SHALL be
    consecutive cadence dates starting at first_payment_date, the payment count k
    SHALL satisfy 1 ≤ k ≤ min(max_payments, max_terms), and all payment and fee
    dates SHALL be ≤ last_draft_date (horizon).

    **Validates: Requirements 4.1, 4.2, 4.3, 19.1, 19.2**
    """

    @settings(max_examples=100, deadline=None)
    @given(inputs=feasible_inputs())
    def test_creditor_dates_are_consecutive_cadence_dates(self, inputs) -> None:
        """Creditor payment dates are consecutive monthly cadence dates with no gaps."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(result.feasible)
        assert result.schedule is not None

        # Extract creditor payment dates (rows with creditor_payment > 0)
        creditor_dates = [
            row.date for row in result.schedule if row.creditor_payment_cents > 0
        ]

        if not creditor_dates:
            # Zero offer_total edge case — no creditor payments needed
            return

        # Determine first_payment_date
        first_pay_date = (
            offer.first_payment_date
            if offer.first_payment_date is not None
            else default_first_payment_date(client)
        )

        # Generate expected cadence dates
        k = len(creditor_dates)
        expected_dates = monthly_payment_dates(first_pay_date, k)

        assert creditor_dates == expected_dates, (
            f"Creditor dates {creditor_dates} do not match expected consecutive "
            f"cadence dates {expected_dates} starting at {first_pay_date}"
        )

    @settings(max_examples=100, deadline=None)
    @given(inputs=feasible_inputs())
    def test_payment_count_within_bounds(self, inputs) -> None:
        """Payment count k satisfies 1 ≤ k ≤ min(max_payments, max_terms)."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(result.feasible)
        assert result.schedule is not None

        # Count creditor payments
        k = sum(1 for row in result.schedule if row.creditor_payment_cents > 0)

        # If offer_total is 0, k can be 0
        offer_total = round_half_up(offer.settlement_pct * offer.current_balance_cents)
        if offer_total == 0:
            assert k == 0
            return

        max_allowed = min(rules.max_payments, rules.max_terms)
        assert 1 <= k <= max_allowed, (
            f"Payment count k={k} not in [1, {max_allowed}]"
        )

    @settings(max_examples=100, deadline=None)
    @given(inputs=feasible_inputs())
    def test_all_payment_dates_within_horizon(self, inputs) -> None:
        """All creditor payment dates ≤ last_draft_date (horizon)."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(result.feasible)
        assert result.schedule is not None

        for row in result.schedule:
            if row.creditor_payment_cents > 0:
                assert row.date <= client.last_draft_date, (
                    f"Creditor payment on {row.date} exceeds horizon {client.last_draft_date}"
                )

    @settings(max_examples=100, deadline=None)
    @given(inputs=feasible_inputs())
    def test_all_fee_dates_within_horizon(self, inputs) -> None:
        """All fee dates ≤ last_draft_date (horizon)."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(result.feasible)
        assert result.schedule is not None

        for row in result.schedule:
            if row.program_fee_cents > 0:
                assert row.date <= client.last_draft_date, (
                    f"Program fee on {row.date} exceeds horizon {client.last_draft_date}"
                )


# ---------------------------------------------------------------------------
# Property 6: Composite Floor Enforcement
# ---------------------------------------------------------------------------


class TestCompositeFloorEnforcement:
    """Each payment ≥ effective floor, token count ≤ max_token_pays.

    Property 6: For any feasible result and each payment position i (1-based),
    the creditor payment SHALL be ≥ the effective floor, where the effective floor
    is max(min_payment_cents, applicable_tier_floor(i), token_exceeded_floor(i)).
    Additionally, at most max_token_pays payments SHALL equal min_payment_cents.

    **Validates: Requirements 7.1, 7.2, 7.3, 7.4**
    """

    @settings(max_examples=100, deadline=None)
    @given(inputs=feasible_inputs())
    def test_each_payment_meets_effective_floor(self, inputs) -> None:
        """Each creditor payment ≥ effective floor at its position."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(result.feasible)
        assert result.schedule is not None

        # Extract creditor payments in order
        payments = [
            row.creditor_payment_cents
            for row in result.schedule
            if row.creditor_payment_cents > 0
        ]

        if not payments:
            return

        k = len(payments)
        floors = compute_floors(k, rules)

        for i, (payment, floor) in enumerate(zip(payments, floors)):
            assert payment >= floor, (
                f"Payment at position {i+1}: {payment} < floor {floor} "
                f"(min_payment={rules.min_payment_cents}, "
                f"max_token_pays={rules.max_token_pays}, "
                f"tiers={rules.min_payment_tiers})"
            )

    @settings(max_examples=100, deadline=None)
    @given(inputs=feasible_inputs())
    def test_token_pay_count_within_limit(self, inputs) -> None:
        """At most max_token_pays payments equal min_payment_cents."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(result.feasible)
        assert result.schedule is not None

        # Extract creditor payments
        payments = [
            row.creditor_payment_cents
            for row in result.schedule
            if row.creditor_payment_cents > 0
        ]

        if not payments:
            return

        # Count payments exactly at min_payment_cents (token pays)
        token_count = sum(1 for p in payments if p == rules.min_payment_cents)

        assert token_count <= rules.max_token_pays, (
            f"Token pay count {token_count} exceeds max_token_pays {rules.max_token_pays}"
        )

    @settings(max_examples=100, deadline=None)
    @given(inputs=feasible_inputs())
    def test_each_payment_at_least_min_payment(self, inputs) -> None:
        """Each creditor payment is at least min_payment_cents (base floor)."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(result.feasible)
        assert result.schedule is not None

        payments = [
            row.creditor_payment_cents
            for row in result.schedule
            if row.creditor_payment_cents > 0
        ]

        for i, payment in enumerate(payments):
            assert payment >= rules.min_payment_cents, (
                f"Payment at position {i+1}: {payment} < min_payment_cents "
                f"{rules.min_payment_cents}"
            )


# ---------------------------------------------------------------------------
# Property 13: Output Structure Invariants
# ---------------------------------------------------------------------------


class TestOutputStructureInvariants:
    """Output structure invariants for feasible and infeasible results.

    Property 13: For any result: if feasible is true, then schedule SHALL not be
    None, pay_shape_used SHALL be one of "even", "balloon", "staircase", and
    additional_funds SHALL be None. If feasible is false, then schedule SHALL be
    None, pay_shape_used SHALL be None, and additional_funds SHALL not be None
    (containing both lump_sum and monthly_increment).

    **Validates: Requirements 14.1, 14.2, 17.1, 17.2**
    """

    @settings(max_examples=100, deadline=None)
    @given(inputs=feasible_inputs())
    def test_feasible_schedule_not_none(self, inputs) -> None:
        """If feasible=True, schedule is not None."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(result.feasible)
        assert result.schedule is not None, (
            "Feasible result has schedule=None"
        )

    @settings(max_examples=100, deadline=None)
    @given(inputs=feasible_inputs())
    def test_feasible_pay_shape_valid(self, inputs) -> None:
        """If feasible=True, pay_shape_used is one of 'even', 'balloon', 'staircase'."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(result.feasible)
        valid_shapes = {"even", "balloon", "staircase"}
        assert result.pay_shape_used in valid_shapes, (
            f"pay_shape_used={result.pay_shape_used!r} not in {valid_shapes}"
        )

    @settings(max_examples=100, deadline=None)
    @given(inputs=feasible_inputs())
    def test_feasible_additional_funds_none(self, inputs) -> None:
        """If feasible=True, additional_funds is None."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(result.feasible)
        assert result.additional_funds is None, (
            f"Feasible result has additional_funds={result.additional_funds}"
        )

    @settings(max_examples=100, deadline=None)
    @given(inputs=infeasible_inputs())
    def test_infeasible_schedule_none(self, inputs) -> None:
        """If feasible=False, schedule is None."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(not result.feasible)
        assert result.schedule is None, (
            "Infeasible result has schedule not None"
        )

    @settings(max_examples=100, deadline=None)
    @given(inputs=infeasible_inputs())
    def test_infeasible_pay_shape_none(self, inputs) -> None:
        """If feasible=False, pay_shape_used is None."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(not result.feasible)
        assert result.pay_shape_used is None, (
            f"Infeasible result has pay_shape_used={result.pay_shape_used!r}"
        )

    @settings(max_examples=100, deadline=None)
    @given(inputs=infeasible_inputs())
    def test_infeasible_additional_funds_not_none(self, inputs) -> None:
        """If feasible=False, additional_funds is not None with both lump_sum and monthly_increment."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(not result.feasible)
        assert result.additional_funds is not None, (
            "Infeasible result has additional_funds=None"
        )
        assert result.additional_funds.lump_sum is not None, (
            "Infeasible result missing lump_sum in additional_funds"
        )
        assert result.additional_funds.monthly_increment is not None, (
            "Infeasible result missing monthly_increment in additional_funds"
        )
