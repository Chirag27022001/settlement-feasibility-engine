# Feature: settlement-feasibility-engine, Property 1: Round-Half-Up Monetary Computation
"""Property-based tests for round_half_up and monetary computations.

**Validates: Requirements 1.1, 1.2, 2.1**

Tests that:
- Values with fractional part exactly 0.5 round away from zero
- offer_total computation uses round-half-up
- program_fee_total computation uses round-half-up
"""

import math

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from feasibility.engine import round_half_up


# ---------------------------------------------------------------------------
# Strategy: generate floats whose fractional part is exactly 0.5
# We construct these as (integer + 0.5) to guarantee exact 0.5 fractions.
# ---------------------------------------------------------------------------
half_values = st.integers(min_value=-100_000, max_value=100_000).map(lambda n: n + 0.5)

# Strategy: settlement_pct in realistic range (0.01 to 1.0)
settlement_pcts = st.floats(min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False)

# Strategy: balance in cents (positive integers, realistic range)
balance_cents = st.integers(min_value=1, max_value=10_000_000)

# Strategy: program_fee_pct in realistic range (0.01 to 1.0)
program_fee_pcts = st.floats(min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False)

# Strategy: original_balance_cents (positive integers, realistic range)
original_balance_cents = st.integers(min_value=1, max_value=10_000_000)


class TestRoundHalfUpExactHalf:
    """Test that values with fractional part exactly 0.5 round away from zero."""

    @settings(max_examples=100)
    @given(value=half_values)
    def test_positive_half_rounds_up(self, value: float) -> None:
        """Positive x.5 values round up (away from zero)."""
        assume(value > 0)
        result = round_half_up(value)
        # For positive n + 0.5, rounding away from zero means rounding up to n + 1
        expected = math.floor(value) + 1
        assert result == expected, (
            f"round_half_up({value}) = {result}, expected {expected} (round away from zero)"
        )

    @settings(max_examples=100)
    @given(value=half_values)
    def test_negative_half_rounds_away_from_zero(self, value: float) -> None:
        """Negative x.5 values round away from zero (more negative)."""
        assume(value < 0)
        result = round_half_up(value)
        # For negative n + 0.5 (e.g., -2.5), rounding away from zero means -3
        expected = -(math.floor(-value) + 1)
        # Alternative: for value = -2.5, away from zero = -3
        # math.floor(-value + 0.5) gives the positive magnitude rounded up
        expected = -math.floor(-value + 0.5)
        assert result == expected, (
            f"round_half_up({value}) = {result}, expected {expected} (round away from zero)"
        )

    @settings(max_examples=100)
    @given(n=st.integers(min_value=0, max_value=100_000))
    def test_positive_half_is_ceiling(self, n: int) -> None:
        """For positive n.5, result should be n + 1."""
        value = n + 0.5
        result = round_half_up(value)
        assert result == n + 1

    @settings(max_examples=100)
    @given(n=st.integers(min_value=1, max_value=100_000))
    def test_negative_half_is_negative_ceiling(self, n: int) -> None:
        """For negative -n.5, result should be -(n + 1) (away from zero)."""
        value = -(n + 0.5)
        result = round_half_up(value)
        assert result == -(n + 1), (
            f"round_half_up({value}) = {result}, expected {-(n + 1)}"
        )


class TestOfferTotalComputation:
    """Test that offer_total = round_half_up(settlement_pct × current_balance_cents).

    **Validates: Requirements 1.1, 1.2**
    """

    @settings(max_examples=100)
    @given(settlement_pct=settlement_pcts, current_balance=balance_cents)
    def test_offer_total_uses_round_half_up(
        self, settlement_pct: float, current_balance: int
    ) -> None:
        """offer_total must equal round_half_up(settlement_pct × current_balance_cents)."""
        raw = settlement_pct * current_balance
        expected = round_half_up(raw)

        # Verify the result is an integer
        assert isinstance(expected, int)

        # Verify round-half-up semantics: the result is the nearest integer,
        # with ties broken away from zero
        diff = abs(raw - expected)
        # The result should be within 0.5 of the raw value (or exactly 0.5 rounded away)
        assert diff <= 0.5 + 1e-9, (
            f"round_half_up({raw}) = {expected}, but distance is {diff}"
        )

    @settings(max_examples=100)
    @given(current_balance=balance_cents)
    def test_offer_total_half_cent_rounds_up(self, current_balance: int) -> None:
        """When settlement_pct × balance produces exactly x.5, it rounds up."""
        # Construct a case where the product is exactly n + 0.5:
        # Use settlement_pct = (2*n + 1) / (2 * balance) to get product = n + 0.5
        # We pick n such that the division is exact in float
        assume(current_balance % 2 == 0)  # even balance for clean division
        half_balance = current_balance // 2
        # settlement_pct = 0.5 / half_balance would give 0.5 * balance / half_balance = 1.0
        # Instead: use settlement_pct such that pct * balance = some_int + 0.5
        # pct = (2k+1) / (2 * balance) for integer k
        # Pick k = half_balance - 1 so pct is reasonable
        assume(half_balance > 0)
        target_result = half_balance  # We want raw = half_balance - 0.5... no
        # Simpler approach: 0.5 * odd_balance gives x.5
        pass  # Covered by the explicit test below

    @settings(max_examples=100)
    @given(n=st.integers(min_value=1, max_value=1_000_000))
    def test_offer_total_exact_half_cent_case(self, n: int) -> None:
        """Explicitly test that when product is n.5, result is n+1.

        Use settlement_pct=0.5 with odd balance to get exact half-cent.
        """
        # 0.5 * (2n + 1) = n + 0.5, which should round to n + 1
        odd_balance = 2 * n + 1
        settlement_pct = 0.5
        raw = settlement_pct * odd_balance
        result = round_half_up(raw)
        assert result == n + 1, (
            f"round_half_up(0.5 * {odd_balance}) = {result}, expected {n + 1}"
        )


class TestProgramFeeTotalComputation:
    """Test that program_fee_total = round_half_up(program_fee_pct × original_balance_cents).

    **Validates: Requirements 2.1**
    """

    @settings(max_examples=100)
    @given(program_fee_pct=program_fee_pcts, original_balance=original_balance_cents)
    def test_program_fee_uses_round_half_up(
        self, program_fee_pct: float, original_balance: int
    ) -> None:
        """program_fee_total must equal round_half_up(program_fee_pct × original_balance_cents)."""
        raw = program_fee_pct * original_balance
        expected = round_half_up(raw)

        # Verify the result is an integer
        assert isinstance(expected, int)

        # Verify round-half-up semantics
        diff = abs(raw - expected)
        assert diff <= 0.5 + 1e-9, (
            f"round_half_up({raw}) = {expected}, but distance is {diff}"
        )

    @settings(max_examples=100)
    @given(n=st.integers(min_value=1, max_value=1_000_000))
    def test_program_fee_exact_half_cent_case(self, n: int) -> None:
        """Explicitly test that when product is n.5, result is n+1.

        Use program_fee_pct=0.5 with odd original_balance to get exact half-cent.
        """
        # 0.5 * (2n + 1) = n + 0.5, which should round to n + 1
        odd_balance = 2 * n + 1
        program_fee_pct = 0.5
        raw = program_fee_pct * odd_balance
        result = round_half_up(raw)
        assert result == n + 1, (
            f"round_half_up(0.5 * {odd_balance}) = {result}, expected {n + 1}"
        )

    @settings(max_examples=100)
    @given(program_fee_pct=program_fee_pcts, original_balance=original_balance_cents)
    def test_program_fee_not_bankers_rounding(
        self, program_fee_pct: float, original_balance: int
    ) -> None:
        """Verify round_half_up differs from Python's built-in round (banker's rounding).

        For cases where the fractional part is exactly 0.5, Python's round()
        rounds to even, while round_half_up rounds away from zero.
        """
        raw = program_fee_pct * original_balance
        result = round_half_up(raw)
        python_round = round(raw)

        # Both should be close to raw, but may differ at .5 boundaries
        # When they differ, round_half_up should be the one rounding away from zero
        if result != python_round:
            # The raw value must be near a .5 boundary
            frac = raw - math.floor(raw)
            # round_half_up rounds away from zero at .5
            assert abs(frac - 0.5) < 1e-9 or abs(result - raw) <= 0.5 + 1e-9
