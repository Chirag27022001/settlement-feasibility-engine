# Feature: settlement-feasibility-engine, Property 16: Input Immutability
"""Property-based tests for input immutability.

**Validates: Requirements 20.1, 20.2**

Tests that:
- Property 16: client, offer, and rules objects are unchanged after evaluate_offer returns
- Deep-copies inputs before call, asserts equality after call
- Checks all fields including mutable containers (ledger list, min_payment_tiers list)
- Tests with both feasible and infeasible inputs
"""

from __future__ import annotations

import copy
from datetime import date

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from feasibility.engine import evaluate_offer
from feasibility.models import (
    Client,
    CreditorRules,
    LedgerEntry,
    Offer,
)


# ---------------------------------------------------------------------------
# Hypothesis strategies for generating valid inputs
# ---------------------------------------------------------------------------


@st.composite
def feasible_inputs(draw):
    """Generate (client, offer, rules) triples that are likely feasible.

    Uses a large balance relative to the offer to ensure feasibility.
    """
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

    # Add some debits to make ledger more interesting (tests that debits aren't mutated)
    num_debits = draw(st.integers(min_value=0, max_value=2))
    for _ in range(num_debits):
        if ledger:
            debit_idx = draw(st.integers(min_value=0, max_value=len(ledger) - 1))
            debit_date = ledger[debit_idx].date
            debit_amount = draw(st.integers(min_value=100, max_value=draft_amount // 4))
            ledger.append(LedgerEntry(date=debit_date, amount_cents=debit_amount, type="debit"))

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

    # Add tiers to test min_payment_tiers immutability
    num_tiers = draw(st.integers(min_value=0, max_value=2))
    tiers = []
    for _ in range(num_tiers):
        from_pay = draw(st.integers(min_value=2, max_value=6))
        tier_min = draw(st.integers(min_value=min_payment, max_value=min_payment * 2))
        tiers.append((from_pay, tier_min))

    rules = CreditorRules(
        max_terms=12,
        max_payments=12,
        min_payment_cents=min_payment,
        max_token_pays=12,
        min_payment_tiers=tiers,
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
    current_balance = draw(st.integers(min_value=0, max_value=draft_amount))

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

    # Add debits to further reduce available funds
    num_debits = draw(st.integers(min_value=1, max_value=3))
    for _ in range(num_debits):
        if ledger:
            debit_idx = draw(st.integers(min_value=0, max_value=len(ledger) - 1))
            debit_date = ledger[debit_idx].date
            debit_amount = draw(st.integers(min_value=1000, max_value=draft_amount // 2))
            ledger.append(LedgerEntry(date=debit_date, amount_cents=debit_amount, type="debit"))

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
    creditor_balance = draw(st.integers(min_value=total_available * 2, max_value=total_available * 4))
    settlement_pct = draw(
        st.floats(min_value=0.5, max_value=0.9, allow_nan=False, allow_infinity=False)
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

    # Rules with higher fees to make infeasibility more likely
    min_payment = draw(st.integers(min_value=5000, max_value=15000))
    bank_fee = draw(st.integers(min_value=500, max_value=2000))
    program_fee_pct = draw(
        st.floats(min_value=0.15, max_value=0.35, allow_nan=False, allow_infinity=False)
    )
    shape_choice = draw(st.sampled_from(["even", "balloon", "staircase"]))

    # Add tiers to test min_payment_tiers immutability
    num_tiers = draw(st.integers(min_value=1, max_value=3))
    tiers = []
    for _ in range(num_tiers):
        from_pay = draw(st.integers(min_value=2, max_value=4))
        tier_min = draw(st.integers(min_value=min_payment, max_value=min_payment * 2))
        tiers.append((from_pay, tier_min))

    rules = CreditorRules(
        max_terms=6,
        max_payments=6,
        min_payment_cents=min_payment,
        max_token_pays=3,
        min_payment_tiers=tiers,
        even_pays=(shape_choice == "even"),
        is_ballooning_allowed=(shape_choice == "balloon"),
        max_segments=2,
        bank_fee_cents=bank_fee,
        program_fee_pct=program_fee_pct,
    )

    return client, offer, rules


# ---------------------------------------------------------------------------
# Property 16: Input Immutability
# ---------------------------------------------------------------------------


class TestInputImmutability:
    """Client, offer, and rules are unchanged after evaluate_offer returns.

    Property 16: The engine SHALL NOT mutate any input objects. After
    evaluate_offer returns, the client, offer, and rules objects SHALL be
    identical to their state before the call — including mutable containers
    like client.ledger and rules.min_payment_tiers.

    **Validates: Requirements 20.1, 20.2**
    """

    @settings(max_examples=100, deadline=None)
    @given(inputs=feasible_inputs())
    def test_inputs_unchanged_after_feasible_evaluation(self, inputs) -> None:
        """For feasible inputs, all input objects remain unchanged after evaluate_offer."""
        client, offer, rules = inputs

        # Deep-copy all inputs before the call
        client_before = copy.deepcopy(client)
        offer_before = copy.deepcopy(offer)
        rules_before = copy.deepcopy(rules)

        # Call evaluate_offer
        evaluate_offer(client, offer, rules)

        # Assert client unchanged
        assert client.draft_amount_cents == client_before.draft_amount_cents
        assert client.draft_day == client_before.draft_day
        assert client.first_draft_date == client_before.first_draft_date
        assert client.last_draft_date == client_before.last_draft_date
        assert client.as_of_date == client_before.as_of_date
        assert client.current_balance_cents == client_before.current_balance_cents
        assert len(client.ledger) == len(client_before.ledger), (
            f"Ledger length changed: {len(client_before.ledger)} -> {len(client.ledger)}"
        )
        for i, (entry, entry_before) in enumerate(zip(client.ledger, client_before.ledger)):
            assert entry.date == entry_before.date, (
                f"Ledger entry {i} date changed: {entry_before.date} -> {entry.date}"
            )
            assert entry.amount_cents == entry_before.amount_cents, (
                f"Ledger entry {i} amount changed: {entry_before.amount_cents} -> {entry.amount_cents}"
            )
            assert entry.type == entry_before.type, (
                f"Ledger entry {i} type changed: {entry_before.type} -> {entry.type}"
            )

        # Assert offer unchanged
        assert offer.creditor == offer_before.creditor
        assert offer.current_balance_cents == offer_before.current_balance_cents
        assert offer.original_balance_cents == offer_before.original_balance_cents
        assert offer.settlement_pct == offer_before.settlement_pct
        assert offer.first_payment_date == offer_before.first_payment_date

        # Assert rules unchanged
        assert rules.max_terms == rules_before.max_terms
        assert rules.max_payments == rules_before.max_payments
        assert rules.min_payment_cents == rules_before.min_payment_cents
        assert rules.max_token_pays == rules_before.max_token_pays
        assert rules.even_pays == rules_before.even_pays
        assert rules.is_ballooning_allowed == rules_before.is_ballooning_allowed
        assert rules.max_segments == rules_before.max_segments
        assert rules.bank_fee_cents == rules_before.bank_fee_cents
        assert rules.program_fee_pct == rules_before.program_fee_pct
        assert len(rules.min_payment_tiers) == len(rules_before.min_payment_tiers), (
            f"min_payment_tiers length changed: "
            f"{len(rules_before.min_payment_tiers)} -> {len(rules.min_payment_tiers)}"
        )
        for i, (tier, tier_before) in enumerate(
            zip(rules.min_payment_tiers, rules_before.min_payment_tiers)
        ):
            assert tier == tier_before, (
                f"min_payment_tiers[{i}] changed: {tier_before} -> {tier}"
            )

    @settings(max_examples=100, deadline=None)
    @given(inputs=infeasible_inputs())
    def test_inputs_unchanged_after_infeasible_evaluation(self, inputs) -> None:
        """For infeasible inputs, all input objects remain unchanged after evaluate_offer."""
        client, offer, rules = inputs

        # Deep-copy all inputs before the call
        client_before = copy.deepcopy(client)
        offer_before = copy.deepcopy(offer)
        rules_before = copy.deepcopy(rules)

        # Call evaluate_offer
        evaluate_offer(client, offer, rules)

        # Assert client unchanged
        assert client.draft_amount_cents == client_before.draft_amount_cents
        assert client.draft_day == client_before.draft_day
        assert client.first_draft_date == client_before.first_draft_date
        assert client.last_draft_date == client_before.last_draft_date
        assert client.as_of_date == client_before.as_of_date
        assert client.current_balance_cents == client_before.current_balance_cents
        assert len(client.ledger) == len(client_before.ledger), (
            f"Ledger length changed: {len(client_before.ledger)} -> {len(client.ledger)}"
        )
        for i, (entry, entry_before) in enumerate(zip(client.ledger, client_before.ledger)):
            assert entry.date == entry_before.date, (
                f"Ledger entry {i} date changed: {entry_before.date} -> {entry.date}"
            )
            assert entry.amount_cents == entry_before.amount_cents, (
                f"Ledger entry {i} amount changed: {entry_before.amount_cents} -> {entry.amount_cents}"
            )
            assert entry.type == entry_before.type, (
                f"Ledger entry {i} type changed: {entry_before.type} -> {entry.type}"
            )

        # Assert offer unchanged
        assert offer.creditor == offer_before.creditor
        assert offer.current_balance_cents == offer_before.current_balance_cents
        assert offer.original_balance_cents == offer_before.original_balance_cents
        assert offer.settlement_pct == offer_before.settlement_pct
        assert offer.first_payment_date == offer_before.first_payment_date

        # Assert rules unchanged
        assert rules.max_terms == rules_before.max_terms
        assert rules.max_payments == rules_before.max_payments
        assert rules.min_payment_cents == rules_before.min_payment_cents
        assert rules.max_token_pays == rules_before.max_token_pays
        assert rules.even_pays == rules_before.even_pays
        assert rules.is_ballooning_allowed == rules_before.is_ballooning_allowed
        assert rules.max_segments == rules_before.max_segments
        assert rules.bank_fee_cents == rules_before.bank_fee_cents
        assert rules.program_fee_pct == rules_before.program_fee_pct
        assert len(rules.min_payment_tiers) == len(rules_before.min_payment_tiers), (
            f"min_payment_tiers length changed: "
            f"{len(rules_before.min_payment_tiers)} -> {len(rules.min_payment_tiers)}"
        )
        for i, (tier, tier_before) in enumerate(
            zip(rules.min_payment_tiers, rules_before.min_payment_tiers)
        ):
            assert tier == tier_before, (
                f"min_payment_tiers[{i}] changed: {tier_before} -> {tier}"
            )

    @settings(max_examples=100, deadline=None)
    @given(inputs=st.one_of(feasible_inputs(), infeasible_inputs()))
    def test_ledger_order_preserved(self, inputs) -> None:
        """The ledger list order is preserved (not sorted or reordered) after evaluate_offer."""
        client, offer, rules = inputs

        # Snapshot the ledger order before the call
        ledger_order_before = [(e.date, e.amount_cents, e.type) for e in client.ledger]

        # Call evaluate_offer
        evaluate_offer(client, offer, rules)

        # Assert ledger order unchanged
        ledger_order_after = [(e.date, e.amount_cents, e.type) for e in client.ledger]
        assert ledger_order_after == ledger_order_before, (
            "Ledger order was modified by evaluate_offer"
        )

    @settings(max_examples=100, deadline=None)
    @given(inputs=st.one_of(feasible_inputs(), infeasible_inputs()))
    def test_min_payment_tiers_order_preserved(self, inputs) -> None:
        """The min_payment_tiers list order is preserved after evaluate_offer."""
        client, offer, rules = inputs

        # Snapshot the tiers before the call
        tiers_before = list(rules.min_payment_tiers)

        # Call evaluate_offer
        evaluate_offer(client, offer, rules)

        # Assert tiers unchanged
        assert rules.min_payment_tiers == tiers_before, (
            f"min_payment_tiers was modified: {tiers_before} -> {rules.min_payment_tiers}"
        )
