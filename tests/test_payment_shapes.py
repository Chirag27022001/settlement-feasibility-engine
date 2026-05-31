# Feature: settlement-feasibility-engine, Property 4: Exact Sum
# Feature: settlement-feasibility-engine, Property 5: Non-Decreasing Payment Sequence
# Feature: settlement-feasibility-engine, Property 9: Even Pays Shape Correctness
# Feature: settlement-feasibility-engine, Property 10: Balloon Shape Correctness
# Feature: settlement-feasibility-engine, Property 11: Staircase Segment Cap
"""Property-based tests for payment shape generators.

**Validates: Requirements 5.1, 6.1, 10.1, 10.2, 11.1, 12.1, 12.2**

Tests that:
- All generators produce payments summing exactly to offer_total (Property 4)
- All generators produce non-decreasing sequences (Property 5)
- Even pays: max-min difference ≤ 1 cent, larger payments at end (Property 9)
- Balloon: all but last at floor, last absorbs remainder (Property 10)
- Staircase: distinct levels ≤ max_segments (Property 11)
"""

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from feasibility.engine import (
    compute_floors,
    generate_balloon_payments,
    generate_even_payments,
    generate_staircase_payments,
)
from feasibility.models import CreditorRules


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Offer total in cents: realistic range for settlement amounts
offer_totals = st.integers(min_value=1, max_value=5_000_000)

# Payment count k: realistic range
payment_counts = st.integers(min_value=1, max_value=24)

# Min payment in cents
min_payments = st.integers(min_value=100, max_value=50_000)

# Max token pays
max_token_pays_st = st.integers(min_value=0, max_value=24)

# Max segments for staircase
max_segments_st = st.integers(min_value=1, max_value=6)


def _make_rules(
    min_payment_cents: int = 2500,
    max_token_pays: int = 6,
    min_payment_tiers: list[tuple[int, int]] | None = None,
    max_segments: int = 4,
    **kwargs,
) -> CreditorRules:
    """Helper to build a CreditorRules with sensible defaults."""
    defaults = dict(
        max_terms=12,
        max_payments=12,
        min_payment_cents=min_payment_cents,
        max_token_pays=max_token_pays,
        min_payment_tiers=min_payment_tiers or [],
        even_pays=False,
        is_ballooning_allowed=False,
        max_segments=max_segments,
        bank_fee_cents=500,
        program_fee_pct=0.2,
    )
    defaults.update(kwargs)
    return CreditorRules(**defaults)


# Strategy: generate creditor rules with random but consistent parameters
@st.composite
def creditor_rules_st(draw):
    """Generate random but consistent CreditorRules."""
    min_pay = draw(st.integers(min_value=100, max_value=20_000))
    max_tok = draw(st.integers(min_value=0, max_value=12))
    max_seg = draw(st.integers(min_value=1, max_value=6))

    # Generate 0-3 tiers, each with from_payment in [1, 12] and min_cents >= min_pay
    num_tiers = draw(st.integers(min_value=0, max_value=3))
    tiers = []
    for _ in range(num_tiers):
        from_pay = draw(st.integers(min_value=1, max_value=12))
        tier_min = draw(st.integers(min_value=min_pay, max_value=min_pay * 5))
        tiers.append((from_pay, tier_min))

    return _make_rules(
        min_payment_cents=min_pay,
        max_token_pays=max_tok,
        min_payment_tiers=tiers,
        max_segments=max_seg,
    )


# ---------------------------------------------------------------------------
# Property 4: Exact Sum — all generators produce payments summing to offer_total
# ---------------------------------------------------------------------------


class TestExactSum:
    """All generators produce payments that sum exactly to offer_total."""

    @settings(max_examples=100)
    @given(offer_total=offer_totals, k=payment_counts)
    def test_even_payments_exact_sum(self, offer_total: int, k: int) -> None:
        """generate_even_payments always produces payments summing to offer_total."""
        payments = generate_even_payments(offer_total, k)
        assert sum(payments) == offer_total, (
            f"Even payments sum {sum(payments)} != offer_total {offer_total} for k={k}"
        )

    @settings(max_examples=100)
    @given(
        offer_total=st.integers(min_value=1000, max_value=2_000_000),
        k=st.integers(min_value=2, max_value=12),
        rules=creditor_rules_st(),
    )
    def test_balloon_payments_exact_sum(
        self, offer_total: int, k: int, rules: CreditorRules
    ) -> None:
        """generate_balloon_payments, when it returns a result, sums to offer_total."""
        floors = compute_floors(k, rules)
        payments = generate_balloon_payments(offer_total, k, floors)
        # Only check when the generator succeeds (returns non-None)
        assume(payments is not None)
        assert sum(payments) == offer_total, (
            f"Balloon payments sum {sum(payments)} != offer_total {offer_total}"
        )

    @settings(max_examples=100)
    @given(
        offer_total=st.integers(min_value=1000, max_value=2_000_000),
        k=st.integers(min_value=1, max_value=12),
        rules=creditor_rules_st(),
    )
    def test_staircase_payments_exact_sum(
        self, offer_total: int, k: int, rules: CreditorRules
    ) -> None:
        """generate_staircase_payments, when it returns a result, sums to offer_total."""
        floors = compute_floors(k, rules)
        payments = generate_staircase_payments(offer_total, k, floors, rules.max_segments)
        # Only check when the generator succeeds (returns non-None)
        assume(payments is not None)
        assert sum(payments) == offer_total, (
            f"Staircase payments sum {sum(payments)} != offer_total {offer_total}"
        )


# ---------------------------------------------------------------------------
# Property 5: Non-Decreasing Payment Sequence
# ---------------------------------------------------------------------------


class TestNonDecreasing:
    """All generators produce non-decreasing payment sequences."""

    @settings(max_examples=100)
    @given(offer_total=offer_totals, k=payment_counts)
    def test_even_payments_non_decreasing(self, offer_total: int, k: int) -> None:
        """generate_even_payments always produces a non-decreasing sequence."""
        payments = generate_even_payments(offer_total, k)
        for i in range(len(payments) - 1):
            assert payments[i] <= payments[i + 1], (
                f"Even payments not non-decreasing at index {i}: "
                f"{payments[i]} > {payments[i + 1]}, payments={payments}"
            )

    @settings(max_examples=100)
    @given(
        offer_total=st.integers(min_value=1000, max_value=2_000_000),
        k=st.integers(min_value=2, max_value=12),
        rules=creditor_rules_st(),
    )
    def test_balloon_payments_non_decreasing(
        self, offer_total: int, k: int, rules: CreditorRules
    ) -> None:
        """generate_balloon_payments, when it returns a result, is non-decreasing."""
        floors = compute_floors(k, rules)
        payments = generate_balloon_payments(offer_total, k, floors)
        assume(payments is not None)
        for i in range(len(payments) - 1):
            assert payments[i] <= payments[i + 1], (
                f"Balloon payments not non-decreasing at index {i}: "
                f"{payments[i]} > {payments[i + 1]}, payments={payments}"
            )

    @settings(max_examples=100)
    @given(
        offer_total=st.integers(min_value=1000, max_value=2_000_000),
        k=st.integers(min_value=1, max_value=12),
        rules=creditor_rules_st(),
    )
    def test_staircase_payments_non_decreasing(
        self, offer_total: int, k: int, rules: CreditorRules
    ) -> None:
        """generate_staircase_payments, when it returns a result, is non-decreasing."""
        floors = compute_floors(k, rules)
        payments = generate_staircase_payments(offer_total, k, floors, rules.max_segments)
        assume(payments is not None)
        for i in range(len(payments) - 1):
            assert payments[i] <= payments[i + 1], (
                f"Staircase payments not non-decreasing at index {i}: "
                f"{payments[i]} > {payments[i + 1]}, payments={payments}"
            )


# ---------------------------------------------------------------------------
# Property 9: Even Pays Shape Correctness
# ---------------------------------------------------------------------------


class TestEvenPaysShape:
    """Even payments: max-min difference ≤ 1 cent, larger payments at end."""

    @settings(max_examples=100)
    @given(offer_total=offer_totals, k=payment_counts)
    def test_max_min_difference_at_most_one(self, offer_total: int, k: int) -> None:
        """The difference between max and min payment is at most 1 cent."""
        payments = generate_even_payments(offer_total, k)
        diff = max(payments) - min(payments)
        assert diff <= 1, (
            f"Even payments max-min diff = {diff} > 1: "
            f"min={min(payments)}, max={max(payments)}, payments={payments}"
        )

    @settings(max_examples=100)
    @given(offer_total=offer_totals, k=payment_counts)
    def test_larger_payments_at_end(self, offer_total: int, k: int) -> None:
        """When remainder exists, larger payments (+1 cent) are at the end.

        This ensures the sequence is non-decreasing: [base, base, ..., base+1, base+1].
        """
        payments = generate_even_payments(offer_total, k)
        base, r = divmod(offer_total, k)

        if r == 0:
            # All payments should be equal
            assert all(p == base for p in payments), (
                f"Expected all payments = {base}, got {payments}"
            )
        else:
            # First (k - r) payments should be base, last r should be base + 1
            for i in range(k - r):
                assert payments[i] == base, (
                    f"Position {i}: expected {base}, got {payments[i]}"
                )
            for i in range(k - r, k):
                assert payments[i] == base + 1, (
                    f"Position {i}: expected {base + 1}, got {payments[i]}"
                )

    @settings(max_examples=100)
    @given(offer_total=offer_totals, k=payment_counts)
    def test_output_length_equals_k(self, offer_total: int, k: int) -> None:
        """generate_even_payments always returns exactly k payments."""
        payments = generate_even_payments(offer_total, k)
        assert len(payments) == k, (
            f"Expected {k} payments, got {len(payments)}"
        )

    @settings(max_examples=100)
    @given(k=st.integers(min_value=1, max_value=24))
    def test_evenly_divisible_all_equal(self, k: int) -> None:
        """When offer_total is evenly divisible by k, all payments are equal."""
        offer_total = k * 1000  # Guaranteed divisible
        payments = generate_even_payments(offer_total, k)
        assert all(p == 1000 for p in payments), (
            f"Expected all payments = 1000, got {payments}"
        )


# ---------------------------------------------------------------------------
# Property 10: Balloon Shape Correctness
# ---------------------------------------------------------------------------


class TestBalloonShape:
    """Balloon: all but last at floor, last absorbs remainder."""

    @settings(max_examples=100)
    @given(
        k=st.integers(min_value=2, max_value=12),
        rules=creditor_rules_st(),
    )
    def test_early_payments_at_floor(self, k: int, rules: CreditorRules) -> None:
        """All payments except the last are at their respective floor values."""
        floors = compute_floors(k, rules)
        # Ensure offer_total is large enough for balloon to succeed
        # offer_total must be >= sum(floors)
        floor_sum = sum(floors)
        offer_total = floor_sum + 10000  # Generous surplus for the balloon
        payments = generate_balloon_payments(offer_total, k, floors)
        assume(payments is not None)

        # All payments except the last should be at their floor
        for i in range(k - 1):
            assert payments[i] == floors[i], (
                f"Position {i}: expected floor {floors[i]}, got {payments[i]}"
            )

    @settings(max_examples=100)
    @given(
        k=st.integers(min_value=2, max_value=12),
        rules=creditor_rules_st(),
    )
    def test_last_payment_absorbs_remainder(self, k: int, rules: CreditorRules) -> None:
        """The last payment equals offer_total minus sum of early floors."""
        floors = compute_floors(k, rules)
        floor_sum = sum(floors)
        offer_total = floor_sum + 10000
        payments = generate_balloon_payments(offer_total, k, floors)
        assume(payments is not None)

        expected_last = offer_total - sum(floors[: k - 1])
        assert payments[-1] == expected_last, (
            f"Last payment: expected {expected_last}, got {payments[-1]}"
        )

    @settings(max_examples=100)
    @given(
        k=st.integers(min_value=2, max_value=12),
        rules=creditor_rules_st(),
    )
    def test_returns_none_when_final_below_floor(
        self, k: int, rules: CreditorRules
    ) -> None:
        """Returns None when the final payment would be below its floor."""
        floors = compute_floors(k, rules)
        # Set offer_total so that the final payment would be below its floor:
        # final = offer_total - sum(floors[0:k-1]) < floors[k-1]
        # => offer_total < sum(floors[0:k-1]) + floors[k-1] = sum(floors)
        # Use offer_total = sum(floors) - 1 (just barely insufficient)
        offer_total = sum(floors) - 1
        assume(offer_total > 0)
        payments = generate_balloon_payments(offer_total, k, floors)
        assert payments is None, (
            f"Expected None when offer_total={offer_total} < sum(floors)={sum(floors)}, "
            f"got {payments}"
        )

    @settings(max_examples=100)
    @given(
        k=st.integers(min_value=2, max_value=12),
        rules=creditor_rules_st(),
    )
    def test_last_payment_at_least_its_floor(
        self, k: int, rules: CreditorRules
    ) -> None:
        """When balloon succeeds, the last payment is >= its own floor."""
        floors = compute_floors(k, rules)
        # Use offer_total = sum(floors) (minimum valid balloon)
        offer_total = sum(floors)
        payments = generate_balloon_payments(offer_total, k, floors)
        assume(payments is not None)
        assert payments[-1] >= floors[-1], (
            f"Last payment {payments[-1]} < floor {floors[-1]}"
        )

    @settings(max_examples=100)
    @given(rules=creditor_rules_st())
    def test_single_payment_balloon(self, rules: CreditorRules) -> None:
        """With k=1, the single payment is the entire offer_total."""
        k = 1
        floors = compute_floors(k, rules)
        offer_total = floors[0] + 5000  # Ensure it's above the floor
        payments = generate_balloon_payments(offer_total, k, floors)
        assert payments is not None
        assert payments == [offer_total]


# ---------------------------------------------------------------------------
# Property 11: Staircase Segment Cap
# ---------------------------------------------------------------------------


class TestStaircaseSegmentCap:
    """Staircase: distinct payment levels ≤ max_segments."""

    @settings(max_examples=100)
    @given(
        offer_total=st.integers(min_value=5000, max_value=2_000_000),
        k=st.integers(min_value=1, max_value=12),
        rules=creditor_rules_st(),
    )
    def test_distinct_levels_within_max_segments(
        self, offer_total: int, k: int, rules: CreditorRules
    ) -> None:
        """The number of distinct payment amounts is ≤ max_segments."""
        floors = compute_floors(k, rules)
        payments = generate_staircase_payments(offer_total, k, floors, rules.max_segments)
        assume(payments is not None)

        distinct_levels = len(set(payments))
        assert distinct_levels <= rules.max_segments, (
            f"Staircase has {distinct_levels} distinct levels, "
            f"exceeds max_segments={rules.max_segments}, payments={payments}"
        )

    @settings(max_examples=100)
    @given(
        k=st.integers(min_value=2, max_value=12),
        rules=creditor_rules_st(),
    )
    def test_segment_cap_with_exact_floor_sum(
        self, k: int, rules: CreditorRules
    ) -> None:
        """When offer_total equals sum of floors, segments come from floor structure."""
        floors = compute_floors(k, rules)
        offer_total = sum(floors)
        assume(offer_total > 0)

        # Enforce non-decreasing floors for the check
        min_levels = list(floors)
        for i in range(1, k):
            if min_levels[i] < min_levels[i - 1]:
                min_levels[i] = min_levels[i - 1]
        adjusted_sum = sum(min_levels)

        # Only test if the non-decreasing floors sum to offer_total
        # (otherwise the generator may return None or adjust)
        payments = generate_staircase_payments(offer_total, k, floors, rules.max_segments)
        assume(payments is not None)

        distinct_levels = len(set(payments))
        assert distinct_levels <= rules.max_segments

    @settings(max_examples=100)
    @given(
        k=st.integers(min_value=2, max_value=10),
        max_segments=st.integers(min_value=1, max_value=4),
    )
    def test_segment_cap_with_uniform_floors(
        self, k: int, max_segments: int
    ) -> None:
        """With uniform floors, staircase should use at most max_segments levels."""
        rules = _make_rules(
            min_payment_cents=1000,
            max_token_pays=k,  # All within token budget (uniform floor)
            min_payment_tiers=[],
            max_segments=max_segments,
        )
        floors = compute_floors(k, rules)
        # All floors should be 1000
        assert all(f == 1000 for f in floors)

        # offer_total must be >= sum(floors) = k * 1000
        offer_total = k * 1000 + 5000  # Some surplus to distribute
        payments = generate_staircase_payments(offer_total, k, floors, max_segments)
        assume(payments is not None)

        distinct_levels = len(set(payments))
        assert distinct_levels <= max_segments, (
            f"Staircase has {distinct_levels} distinct levels, "
            f"exceeds max_segments={max_segments}, payments={payments}"
        )

    @settings(max_examples=100)
    @given(
        k=st.integers(min_value=3, max_value=10),
    )
    def test_single_segment_means_all_equal(self, k: int) -> None:
        """With max_segments=1, all payments must be equal (if possible)."""
        rules = _make_rules(
            min_payment_cents=1000,
            max_token_pays=k,
            min_payment_tiers=[],
            max_segments=1,
        )
        floors = compute_floors(k, rules)
        # Use an offer_total that's evenly divisible by k
        offer_total = k * 2000
        payments = generate_staircase_payments(offer_total, k, floors, 1)
        assume(payments is not None)

        # With 1 segment, all payments must be the same value
        assert len(set(payments)) == 1, (
            f"Expected 1 distinct level with max_segments=1, got {payments}"
        )

    @settings(max_examples=100)
    @given(
        k=st.integers(min_value=2, max_value=8),
    )
    def test_two_segments_at_most_two_levels(self, k: int) -> None:
        """With max_segments=2, at most 2 distinct payment levels."""
        rules = _make_rules(
            min_payment_cents=1000,
            max_token_pays=k,
            min_payment_tiers=[],
            max_segments=2,
        )
        floors = compute_floors(k, rules)
        # Use a surplus that's not evenly divisible to force 2 levels
        offer_total = k * 1000 + k + 1
        payments = generate_staircase_payments(offer_total, k, floors, 2)
        assume(payments is not None)

        distinct_levels = len(set(payments))
        assert distinct_levels <= 2, (
            f"Expected ≤ 2 distinct levels with max_segments=2, got {distinct_levels}: {payments}"
        )


# ---------------------------------------------------------------------------
# Combined property: floor enforcement for all generators
# ---------------------------------------------------------------------------


class TestFloorEnforcement:
    """All generators respect the computed floors when they return a result."""

    @settings(max_examples=100)
    @given(
        k=st.integers(min_value=2, max_value=12),
        rules=creditor_rules_st(),
    )
    def test_balloon_respects_floors(self, k: int, rules: CreditorRules) -> None:
        """Balloon payments are all >= their respective floors."""
        floors = compute_floors(k, rules)
        offer_total = sum(floors) + 10000
        payments = generate_balloon_payments(offer_total, k, floors)
        assume(payments is not None)

        for i in range(k):
            assert payments[i] >= floors[i], (
                f"Balloon position {i}: payment {payments[i]} < floor {floors[i]}"
            )

    @settings(max_examples=100)
    @given(
        offer_total=st.integers(min_value=5000, max_value=2_000_000),
        k=st.integers(min_value=1, max_value=12),
        rules=creditor_rules_st(),
    )
    def test_staircase_respects_floors(
        self, offer_total: int, k: int, rules: CreditorRules
    ) -> None:
        """Staircase payments are all >= their respective floors."""
        floors = compute_floors(k, rules)
        payments = generate_staircase_payments(offer_total, k, floors, rules.max_segments)
        assume(payments is not None)

        for i in range(k):
            assert payments[i] >= floors[i], (
                f"Staircase position {i}: payment {payments[i]} < floor {floors[i]}"
            )
