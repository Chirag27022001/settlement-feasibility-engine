"""Unit tests for compute_floors.

Tests the effective floor computation for each payment position, covering:
- Base minimum enforcement (Req 7.1)
- Token-pay rule (Req 7.2)
- Tier step-ups (Req 7.3)
- Composite floor — max of all applicable rules (Req 7.4)
"""

import pytest

from feasibility.engine import compute_floors
from feasibility.models import CreditorRules


def _make_rules(
    min_payment_cents: int = 2500,
    max_token_pays: int = 6,
    min_payment_tiers: list[tuple[int, int]] | None = None,
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
        max_segments=4,
        bank_fee_cents=500,
        program_fee_pct=0.2,
    )
    defaults.update(kwargs)
    return CreditorRules(**defaults)


# ---------------------------------------------------------------------------
# Base minimum enforcement (Req 7.1)
# ---------------------------------------------------------------------------


class TestBaseMinimum:
    """Every payment position must be at least min_payment_cents."""

    def test_all_positions_at_least_base_min(self):
        rules = _make_rules(min_payment_cents=5000, max_token_pays=100)
        floors = compute_floors(5, rules)
        assert all(f >= 5000 for f in floors)

    def test_single_payment(self):
        rules = _make_rules(min_payment_cents=1000, max_token_pays=10)
        floors = compute_floors(1, rules)
        assert floors == [1000]

    def test_zero_payments(self):
        rules = _make_rules(min_payment_cents=1000, max_token_pays=10)
        floors = compute_floors(0, rules)
        assert floors == []

    def test_base_min_with_no_tiers_and_generous_token(self):
        """When max_token_pays >= k and no tiers, all floors equal base min."""
        rules = _make_rules(min_payment_cents=3000, max_token_pays=10)
        floors = compute_floors(5, rules)
        assert floors == [3000] * 5


# ---------------------------------------------------------------------------
# Token-pay rule (Req 7.2)
# ---------------------------------------------------------------------------


class TestTokenPayRule:
    """Positions beyond max_token_pays must strictly exceed min_payment_cents."""

    def test_positions_within_token_budget_at_base(self):
        """First max_token_pays positions may sit at base min."""
        rules = _make_rules(min_payment_cents=2500, max_token_pays=3)
        floors = compute_floors(5, rules)
        # Positions 1-3 can be at base min
        assert floors[0] == 2500
        assert floors[1] == 2500
        assert floors[2] == 2500

    def test_positions_beyond_token_budget_exceed_base(self):
        """Positions beyond max_token_pays must be at least base min + 1."""
        rules = _make_rules(min_payment_cents=2500, max_token_pays=3)
        floors = compute_floors(5, rules)
        # Positions 4-5 must exceed base min
        assert floors[3] == 2501
        assert floors[4] == 2501

    def test_token_budget_zero(self):
        """When max_token_pays=0, all positions must exceed base min."""
        rules = _make_rules(min_payment_cents=1000, max_token_pays=0)
        floors = compute_floors(4, rules)
        assert all(f == 1001 for f in floors)

    def test_token_budget_equals_k(self):
        """When max_token_pays >= k, all positions can be at base min."""
        rules = _make_rules(min_payment_cents=2000, max_token_pays=5)
        floors = compute_floors(5, rules)
        assert all(f == 2000 for f in floors)

    def test_token_budget_exceeds_k(self):
        """When max_token_pays > k, all positions can be at base min."""
        rules = _make_rules(min_payment_cents=2000, max_token_pays=10)
        floors = compute_floors(3, rules)
        assert all(f == 2000 for f in floors)

    def test_token_budget_one(self):
        """Only the first position may sit at base min."""
        rules = _make_rules(min_payment_cents=5000, max_token_pays=1)
        floors = compute_floors(4, rules)
        assert floors[0] == 5000
        assert floors[1] == 5001
        assert floors[2] == 5001
        assert floors[3] == 5001


# ---------------------------------------------------------------------------
# Tier step-ups (Req 7.3)
# ---------------------------------------------------------------------------


class TestTierStepUps:
    """Tier rules override base minimum from a given payment number onward."""

    def test_single_tier_applies_from_position(self):
        """A tier [3, 5000] means positions 3+ have floor at least 5000."""
        rules = _make_rules(
            min_payment_cents=2500,
            max_token_pays=10,
            min_payment_tiers=[(3, 5000)],
        )
        floors = compute_floors(5, rules)
        assert floors[0] == 2500  # position 1: base min
        assert floors[1] == 2500  # position 2: base min
        assert floors[2] == 5000  # position 3: tier kicks in
        assert floors[3] == 5000  # position 4: tier still applies
        assert floors[4] == 5000  # position 5: tier still applies

    def test_multiple_tiers(self):
        """Multiple tiers apply cumulatively (max of all applicable)."""
        rules = _make_rules(
            min_payment_cents=1000,
            max_token_pays=10,
            min_payment_tiers=[(2, 3000), (4, 7000)],
        )
        floors = compute_floors(5, rules)
        assert floors[0] == 1000  # position 1: base min only
        assert floors[1] == 3000  # position 2: tier [2, 3000] applies
        assert floors[2] == 3000  # position 3: tier [2, 3000] still applies
        assert floors[3] == 7000  # position 4: tier [4, 7000] applies (higher)
        assert floors[4] == 7000  # position 5: tier [4, 7000] still applies

    def test_tier_from_position_one(self):
        """A tier starting at position 1 overrides base min for all positions."""
        rules = _make_rules(
            min_payment_cents=1000,
            max_token_pays=10,
            min_payment_tiers=[(1, 4000)],
        )
        floors = compute_floors(3, rules)
        assert all(f == 4000 for f in floors)

    def test_tier_lower_than_base_min_has_no_effect(self):
        """A tier with min_cents < min_payment_cents doesn't lower the floor."""
        rules = _make_rules(
            min_payment_cents=5000,
            max_token_pays=10,
            min_payment_tiers=[(1, 3000)],
        )
        floors = compute_floors(3, rules)
        assert all(f == 5000 for f in floors)

    def test_tier_beyond_k_has_no_effect(self):
        """A tier starting at position 10 doesn't affect a 5-payment schedule."""
        rules = _make_rules(
            min_payment_cents=2000,
            max_token_pays=10,
            min_payment_tiers=[(10, 9000)],
        )
        floors = compute_floors(5, rules)
        assert all(f == 2000 for f in floors)


# ---------------------------------------------------------------------------
# Composite floor — max of all applicable rules (Req 7.4)
# ---------------------------------------------------------------------------


class TestCompositeFloor:
    """The effective floor is the maximum of base min, token rule, and tier."""

    def test_token_rule_dominates_when_higher_than_tier(self):
        """Token-exceeded floor (base+1) can be higher than a low tier."""
        rules = _make_rules(
            min_payment_cents=5000,
            max_token_pays=2,
            min_payment_tiers=[(1, 4000)],  # tier is lower than base min
        )
        floors = compute_floors(4, rules)
        # Positions 1-2: max(5000, 4000) = 5000 (base min dominates)
        assert floors[0] == 5000
        assert floors[1] == 5000
        # Positions 3-4: max(5000, token_floor=5001, 4000) = 5001
        assert floors[2] == 5001
        assert floors[3] == 5001

    def test_tier_dominates_over_token_rule(self):
        """A high tier overrides the token-exceeded floor."""
        rules = _make_rules(
            min_payment_cents=2500,
            max_token_pays=2,
            min_payment_tiers=[(3, 8000)],
        )
        floors = compute_floors(5, rules)
        # Positions 1-2: base min (within token budget, no tier)
        assert floors[0] == 2500
        assert floors[1] == 2500
        # Position 3: max(2500, token=2501, tier=8000) = 8000
        assert floors[2] == 8000
        # Positions 4-5: max(2500, token=2501, tier=8000) = 8000
        assert floors[3] == 8000
        assert floors[4] == 8000

    def test_all_three_rules_interact(self):
        """Complex scenario with base min, token rule, and multiple tiers."""
        rules = _make_rules(
            min_payment_cents=1000,
            max_token_pays=1,
            min_payment_tiers=[(2, 1500), (5, 3000)],
        )
        floors = compute_floors(6, rules)
        # Position 1: max(1000) = 1000 (within token budget, no tier)
        assert floors[0] == 1000
        # Position 2: max(1000, token=1001, tier=1500) = 1500
        assert floors[1] == 1500
        # Position 3: max(1000, token=1001, tier=1500) = 1500
        assert floors[2] == 1500
        # Position 4: max(1000, token=1001, tier=1500) = 1500
        assert floors[3] == 1500
        # Position 5: max(1000, token=1001, tier=3000) = 3000
        assert floors[4] == 3000
        # Position 6: max(1000, token=1001, tier=3000) = 3000
        assert floors[5] == 3000

    def test_case4_tiers_from_assignment(self):
        """Matches the case4_tiers creditor rules from the provided test cases."""
        rules = _make_rules(
            min_payment_cents=2500,
            max_token_pays=6,
            min_payment_tiers=[(7, 5000)],
        )
        floors = compute_floors(8, rules)
        # Positions 1-6: base min (within token budget, tier doesn't apply yet)
        for i in range(6):
            assert floors[i] == 2500, f"Position {i+1} should be 2500"
        # Position 7: max(2500, tier=5000) = 5000 (also token budget not exceeded since max_token_pays=6)
        assert floors[6] == 5000
        # Position 8: max(2500, tier=5000) = 5000
        assert floors[7] == 5000

    def test_output_length_matches_k(self):
        """The returned list always has exactly k elements."""
        rules = _make_rules()
        for k in range(0, 15):
            floors = compute_floors(k, rules)
            assert len(floors) == k
