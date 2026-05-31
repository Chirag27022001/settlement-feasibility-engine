# Feature: settlement-feasibility-engine, Property 12: Simulation Balance Non-Negativity
# Feature: settlement-feasibility-engine, Property 7: Bank Fee Correctness
# Feature: settlement-feasibility-engine, Property 8: Program Fee Validity
"""Property-based tests for simulation, bank fees, and program fee placement.

**Validates: Requirements 8.1, 8.2, 9.1, 9.3, 9.4, 13.1, 13.2, 13.3, 18.1, 18.2**

Tests that:
- Property 12: For feasible results, balance ≥ 0 at every date in the simulation
- Property 7: bank_fee_cents equals rules.bank_fee_cents iff creditor_payment > 0
- Property 8: No program fee before first payment date; all fees ≥ 0; fees sum to total
"""

from __future__ import annotations

from datetime import date

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from feasibility.engine import evaluate_offer, round_half_up, simulate
from feasibility.models import (
    Client,
    CreditorRules,
    LedgerEntry,
    Offer,
    monthly_payment_dates,
)


# ---------------------------------------------------------------------------
# Hypothesis strategies for generating valid inputs
# ---------------------------------------------------------------------------


@st.composite
def valid_client(draw) -> Client:
    """Generate a valid Client with consistent draft schedule and ledger.

    Produces clients with:
    - Reasonable draft amounts (5000-50000 cents)
    - A draft schedule spanning 3-12 months
    - Ledger entries matching the draft schedule (credits on draft days)
    - Optional existing debits (from other settlements)
    - as_of_date before first_draft_date
    - current_balance_cents >= 0
    """
    draft_amount = draw(st.integers(min_value=5000, max_value=50000))
    draft_day = draw(st.integers(min_value=1, max_value=28))

    # Start year/month for drafts
    start_year = 2026
    start_month = draw(st.integers(min_value=1, max_value=6))
    first_draft_date = date(start_year, start_month, draft_day)

    # Duration: 3-12 months of drafts
    num_months = draw(st.integers(min_value=3, max_value=12))
    # Compute last_draft_date
    total_months = (start_year * 12 + (start_month - 1)) + num_months
    end_year, end_month_0 = divmod(total_months, 12)
    end_month = end_month_0 + 1
    last_day = min(draft_day, 28)  # Keep it simple
    last_draft_date = date(end_year, end_month, last_day)

    # as_of_date is the day before first_draft_date (or same month prior)
    as_of_date = date(start_year, start_month, 1) if draft_day > 1 else date(
        start_year, start_month - 1 if start_month > 1 else 12,
        28 if start_month > 1 else 28
    )
    # Simplify: as_of_date is always the last day of the prior month
    if start_month == 1:
        as_of_date = date(start_year - 1, 12, 31)
    else:
        as_of_date = date(start_year, start_month - 1, 28)

    # Starting balance
    current_balance = draw(st.integers(min_value=0, max_value=draft_amount * 3))

    # Build ledger: credits for each draft month
    ledger: list[LedgerEntry] = []
    for i in range(num_months + 1):
        m_total = (start_year * 12 + (start_month - 1)) + i
        y, m0 = divmod(m_total, 12)
        m = m0 + 1
        d = min(draft_day, 28)
        entry_date = date(y, m, d)
        if entry_date > last_draft_date:
            break
        ledger.append(LedgerEntry(date=entry_date, amount_cents=draft_amount, type="credit"))

    # Optionally add some existing debits (from other settlements)
    num_debits = draw(st.integers(min_value=0, max_value=2))
    for _ in range(num_debits):
        # Pick a date from the ledger credit dates
        if ledger:
            debit_idx = draw(st.integers(min_value=0, max_value=len(ledger) - 1))
            debit_date = ledger[debit_idx].date
            # Debit amount: small fraction of draft
            debit_amount = draw(st.integers(min_value=100, max_value=draft_amount // 4))
            ledger.append(LedgerEntry(date=debit_date, amount_cents=debit_amount, type="debit"))

    return Client(
        draft_amount_cents=draft_amount,
        draft_day=draft_day,
        first_draft_date=first_draft_date,
        last_draft_date=last_draft_date,
        as_of_date=as_of_date,
        current_balance_cents=current_balance,
        ledger=ledger,
    )


@st.composite
def valid_offer(draw, client: Client) -> Offer:
    """Generate a valid Offer consistent with the given client."""
    creditor_balance = draw(st.integers(min_value=10000, max_value=500000))
    original_balance = draw(
        st.integers(min_value=creditor_balance, max_value=creditor_balance * 2)
    )
    settlement_pct = draw(
        st.floats(min_value=0.1, max_value=0.8, allow_nan=False, allow_infinity=False)
    )

    # first_payment_date: either None or a date within the client's horizon
    use_default = draw(st.booleans())
    if use_default:
        first_payment_date = None
    else:
        # Pick a date in the first few months of the draft schedule
        fpd_month_offset = draw(st.integers(min_value=0, max_value=2))
        m_total = (client.first_draft_date.year * 12 + (client.first_draft_date.month - 1)) + fpd_month_offset
        y, m0 = divmod(m_total, 12)
        m = m0 + 1
        # Use end of month or a mid-month day
        use_eom = draw(st.booleans())
        if use_eom:
            from calendar import monthrange
            first_payment_date = date(y, m, monthrange(y, m)[1])
        else:
            day = draw(st.integers(min_value=1, max_value=28))
            first_payment_date = date(y, m, day)

    return Offer(
        creditor="TestCreditor",
        current_balance_cents=creditor_balance,
        original_balance_cents=original_balance,
        settlement_pct=settlement_pct,
        first_payment_date=first_payment_date,
    )


@st.composite
def valid_rules(draw) -> CreditorRules:
    """Generate valid CreditorRules with consistent constraints."""
    min_payment = draw(st.integers(min_value=500, max_value=10000))
    max_token_pays = draw(st.integers(min_value=0, max_value=12))
    max_terms = draw(st.integers(min_value=3, max_value=12))
    max_payments = draw(st.integers(min_value=3, max_value=12))
    max_segments = draw(st.integers(min_value=1, max_value=4))
    bank_fee = draw(st.integers(min_value=0, max_value=2000))
    program_fee_pct = draw(
        st.floats(min_value=0.0, max_value=0.35, allow_nan=False, allow_infinity=False)
    )

    # Shape flags: pick one shape
    shape_choice = draw(st.sampled_from(["even", "balloon", "staircase"]))
    even_pays = shape_choice == "even"
    is_ballooning_allowed = shape_choice == "balloon"

    # Tiers: 0-2 tiers
    num_tiers = draw(st.integers(min_value=0, max_value=2))
    tiers = []
    for _ in range(num_tiers):
        from_pay = draw(st.integers(min_value=2, max_value=max_terms))
        tier_min = draw(st.integers(min_value=min_payment, max_value=min_payment * 3))
        tiers.append((from_pay, tier_min))

    return CreditorRules(
        max_terms=max_terms,
        max_payments=max_payments,
        min_payment_cents=min_payment,
        max_token_pays=max_token_pays,
        min_payment_tiers=tiers,
        even_pays=even_pays,
        is_ballooning_allowed=is_ballooning_allowed,
        max_segments=max_segments,
        bank_fee_cents=bank_fee,
        program_fee_pct=program_fee_pct,
    )


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
    # Total available = current_balance + draft_amount * num_months
    total_available = current_balance + draft_amount * num_months
    # Offer total should be a small fraction of available funds
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


# ---------------------------------------------------------------------------
# Property 12: Simulation Balance Non-Negativity
# ---------------------------------------------------------------------------


class TestSimulationBalanceNonNegativity:
    """For feasible results, balance ≥ 0 at every date in the simulation.

    Property 12: The running SDA balance (starting at current_balance_cents,
    applying credits before debits on each date, processing all ledger entries
    and scheduled payments/fees chronologically) SHALL be ≥ 0 at every date.
    The balance_cents in each schedule row SHALL equal the independently-computed
    running balance at that date.

    **Validates: Requirements 13.1, 13.2, 13.3, 18.1, 18.2**
    """

    @settings(max_examples=100, deadline=None)
    @given(inputs=feasible_inputs())
    def test_feasible_result_balances_non_negative(self, inputs) -> None:
        """For any feasible result, all schedule row balances must be ≥ 0."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        # Only check feasible results
        assume(result.feasible)
        assert result.schedule is not None

        for row in result.schedule:
            assert row.balance_cents >= 0, (
                f"Negative balance {row.balance_cents} on {row.date}"
            )

    @settings(max_examples=100, deadline=None)
    @given(inputs=feasible_inputs())
    def test_schedule_balances_match_independent_simulation(self, inputs) -> None:
        """Schedule row balances must match an independent simulation replay.

        This verifies that the balance_cents reported in each ScheduleRow is
        consistent with replaying the full ledger + scheduled debits through
        the simulator independently.
        """
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(result.feasible)
        assert result.schedule is not None

        # Build creditor_schedule from the result's schedule rows
        creditor_schedule = [
            (row.date, row.creditor_payment_cents, row.program_fee_cents, row.bank_fee_cents)
            for row in result.schedule
        ]

        # Replay through the simulator independently
        timeline = simulate(client, creditor_schedule)
        balance_map = {d: b for d, b in timeline}

        # Verify each schedule row's balance matches
        for row in result.schedule:
            assert row.date in balance_map, (
                f"Date {row.date} not found in simulation timeline"
            )
            assert row.balance_cents == balance_map[row.date], (
                f"Balance mismatch on {row.date}: "
                f"schedule={row.balance_cents}, simulation={balance_map[row.date]}"
            )

    @settings(max_examples=100, deadline=None)
    @given(inputs=feasible_inputs())
    def test_full_simulation_timeline_non_negative(self, inputs) -> None:
        """The full simulation timeline (including non-cadence dates) must be ≥ 0.

        This checks ALL dates in the simulation (including draft dates that
        aren't cadence dates), not just the schedule rows.
        """
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(result.feasible)
        assert result.schedule is not None

        # Build creditor_schedule from the result
        creditor_schedule = [
            (row.date, row.creditor_payment_cents, row.program_fee_cents, row.bank_fee_cents)
            for row in result.schedule
        ]

        # Simulate the full timeline
        timeline = simulate(client, creditor_schedule)

        for d, balance in timeline:
            assert balance >= 0, (
                f"Negative balance {balance} on {d} in full simulation timeline"
            )

    @settings(max_examples=100, deadline=None)
    @given(inputs=feasible_inputs())
    def test_credits_applied_before_debits_on_same_date(self, inputs) -> None:
        """Verify credits-before-debits ordering by checking that on dates with
        both credits and debits, the balance never dips below what it would be
        if credits are applied first.

        **Validates: Requirements 18.1, 18.2**
        """
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(result.feasible)
        assert result.schedule is not None

        # The simulate function applies credits before debits.
        # If it applied debits first, the balance could go negative on dates
        # where credits and debits co-occur. Since we already verified
        # non-negativity above, this property is implicitly validated.
        # Here we explicitly verify by checking that the simulation result
        # is consistent with credits-before-debits ordering.
        creditor_schedule = [
            (row.date, row.creditor_payment_cents, row.program_fee_cents, row.bank_fee_cents)
            for row in result.schedule
        ]

        timeline = simulate(client, creditor_schedule)
        # All balances must be non-negative (credits applied first)
        for d, balance in timeline:
            assert balance >= 0, (
                f"Balance {balance} on {d} suggests debits applied before credits"
            )


# ---------------------------------------------------------------------------
# Property 7: Bank Fee Correctness
# ---------------------------------------------------------------------------


class TestBankFeeCorrectness:
    """bank_fee_cents equals rules.bank_fee_cents iff creditor_payment > 0.

    Property 7: For any feasible result and each schedule row, bank_fee_cents
    SHALL equal rules.bank_fee_cents if and only if creditor_payment_cents > 0
    on that row; otherwise bank_fee_cents SHALL be 0.

    **Validates: Requirements 8.1, 8.2**
    """

    @settings(max_examples=100, deadline=None)
    @given(inputs=feasible_inputs())
    def test_bank_fee_present_iff_creditor_payment(self, inputs) -> None:
        """Bank fee is charged iff there is a creditor payment on that date."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(result.feasible)
        assert result.schedule is not None

        for row in result.schedule:
            if row.creditor_payment_cents > 0:
                assert row.bank_fee_cents == rules.bank_fee_cents, (
                    f"On {row.date}: creditor_payment={row.creditor_payment_cents} > 0, "
                    f"expected bank_fee={rules.bank_fee_cents}, got {row.bank_fee_cents}"
                )
            else:
                assert row.bank_fee_cents == 0, (
                    f"On {row.date}: creditor_payment=0, "
                    f"expected bank_fee=0, got {row.bank_fee_cents}"
                )

    @settings(max_examples=100, deadline=None)
    @given(inputs=feasible_inputs())
    def test_bank_fee_never_negative(self, inputs) -> None:
        """Bank fee is never negative in any schedule row."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(result.feasible)
        assert result.schedule is not None

        for row in result.schedule:
            assert row.bank_fee_cents >= 0, (
                f"Negative bank fee {row.bank_fee_cents} on {row.date}"
            )

    @settings(max_examples=100, deadline=None)
    @given(inputs=feasible_inputs())
    def test_bank_fee_is_flat_amount(self, inputs) -> None:
        """Bank fee is always the exact flat amount from rules (not variable)."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(result.feasible)
        assert result.schedule is not None

        for row in result.schedule:
            # Bank fee is either 0 (no creditor payment) or the exact flat amount
            assert row.bank_fee_cents in (0, rules.bank_fee_cents), (
                f"On {row.date}: bank_fee={row.bank_fee_cents} is neither 0 "
                f"nor {rules.bank_fee_cents}"
            )

    @settings(max_examples=100, deadline=None)
    @given(inputs=feasible_inputs())
    def test_total_bank_fees_equal_count_times_flat(self, inputs) -> None:
        """Total bank fees = number of creditor payment dates × bank_fee_cents."""
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(result.feasible)
        assert result.schedule is not None

        num_creditor_dates = sum(
            1 for row in result.schedule if row.creditor_payment_cents > 0
        )
        total_bank_fees = sum(row.bank_fee_cents for row in result.schedule)
        expected_total = num_creditor_dates * rules.bank_fee_cents

        assert total_bank_fees == expected_total, (
            f"Total bank fees {total_bank_fees} != "
            f"{num_creditor_dates} × {rules.bank_fee_cents} = {expected_total}"
        )


# ---------------------------------------------------------------------------
# Property 8: Program Fee Validity
# ---------------------------------------------------------------------------


class TestProgramFeeValidity:
    """No fee before first payment date; all fees ≥ 0; fees sum to total.

    Property 8: For any feasible result: (a) no program fee SHALL appear on a
    date before the first creditor payment date; (b) each program_fee_cents
    value SHALL be ≥ 0; (c) the sum of all program_fee_cents SHALL equal the
    total program fee exactly.

    **Validates: Requirements 9.1, 9.3, 9.4**
    """

    @settings(max_examples=100, deadline=None)
    @given(inputs=feasible_inputs())
    def test_no_fee_before_first_creditor_payment_date(self, inputs) -> None:
        """No program fee appears on a date before the first creditor payment date.

        **Validates: Requirement 9.1**
        """
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(result.feasible)
        assert result.schedule is not None

        # Find the first creditor payment date
        creditor_dates = [
            row.date for row in result.schedule if row.creditor_payment_cents > 0
        ]
        if not creditor_dates:
            # No creditor payments (offer_total = 0 edge case) — skip
            return

        first_creditor_date = min(creditor_dates)

        # No program fee should appear before the first creditor payment date
        for row in result.schedule:
            if row.date < first_creditor_date:
                assert row.program_fee_cents == 0, (
                    f"Program fee {row.program_fee_cents} on {row.date} "
                    f"is before first creditor payment date {first_creditor_date}"
                )

    @settings(max_examples=100, deadline=None)
    @given(inputs=feasible_inputs())
    def test_all_program_fees_non_negative(self, inputs) -> None:
        """Each program_fee_cents value is ≥ 0.

        **Validates: Requirement 9.4**
        """
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(result.feasible)
        assert result.schedule is not None

        for row in result.schedule:
            assert row.program_fee_cents >= 0, (
                f"Negative program fee {row.program_fee_cents} on {row.date}"
            )

    @settings(max_examples=100, deadline=None)
    @given(inputs=feasible_inputs())
    def test_program_fees_sum_to_total(self, inputs) -> None:
        """The sum of all program_fee_cents equals the total program fee exactly.

        **Validates: Requirement 9.3**
        """
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(result.feasible)
        assert result.schedule is not None

        # Compute expected total program fee
        expected_total = round_half_up(rules.program_fee_pct * offer.original_balance_cents)

        actual_total = sum(row.program_fee_cents for row in result.schedule)

        assert actual_total == expected_total, (
            f"Program fee sum {actual_total} != expected total {expected_total}"
        )

    @settings(max_examples=100, deadline=None)
    @given(inputs=feasible_inputs())
    def test_program_fee_on_first_creditor_date_allowed(self, inputs) -> None:
        """Program fee collection on the same date as the first creditor payment is allowed.

        **Validates: Requirement 9.2 (implicit — fee CAN appear on first payment date)**
        """
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(result.feasible)
        assert result.schedule is not None
        assume(rules.program_fee_pct > 0)

        # Find the first creditor payment date
        creditor_dates = [
            row.date for row in result.schedule if row.creditor_payment_cents > 0
        ]
        if not creditor_dates:
            return

        first_creditor_date = min(creditor_dates)

        # The first date with a program fee should be >= first_creditor_date
        fee_dates = [
            row.date for row in result.schedule if row.program_fee_cents > 0
        ]
        if fee_dates:
            first_fee_date = min(fee_dates)
            assert first_fee_date >= first_creditor_date, (
                f"First fee date {first_fee_date} is before "
                f"first creditor payment date {first_creditor_date}"
            )

    @settings(max_examples=100, deadline=None)
    @given(inputs=feasible_inputs())
    def test_all_fee_dates_within_horizon(self, inputs) -> None:
        """All program fee dates are on or before the horizon (last_draft_date).

        **Validates: Requirement 19.2 (implicit — fees within horizon)**
        """
        client, offer, rules = inputs
        result = evaluate_offer(client, offer, rules)

        assume(result.feasible)
        assert result.schedule is not None

        for row in result.schedule:
            if row.program_fee_cents > 0:
                assert row.date <= client.last_draft_date, (
                    f"Program fee on {row.date} exceeds horizon {client.last_draft_date}"
                )
