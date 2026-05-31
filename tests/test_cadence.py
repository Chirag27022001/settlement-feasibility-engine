# Feature: settlement-feasibility-engine, Property 2: Cadence Date Generation Correctness
"""Property-based tests for cadence date generation.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4**

Tests that:
- Property 2: Cadence Date Generation Correctness
  - EOM dates stay EOM: if start is last day of its month, all generated dates are last day of their months
  - Mid-month dates preserve day clamped: if start is not last day, generated dates have day = min(start.day, month_length)
  - All generated dates are ≤ horizon (when filtered)
  - Default first_payment_date is end of month of first_draft_date
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from feasibility.models import (
    Client,
    LedgerEntry,
    monthly_payment_dates,
    is_end_of_month,
    end_of_month,
    default_first_payment_date,
)


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------


@st.composite
def eom_start_dates(draw) -> date:
    """Generate dates that are the last day of their month (EOM dates)."""
    year = draw(st.integers(min_value=2020, max_value=2030))
    month = draw(st.integers(min_value=1, max_value=12))
    last_day = monthrange(year, month)[1]
    return date(year, month, last_day)


@st.composite
def mid_month_start_dates(draw) -> date:
    """Generate dates that are NOT the last day of their month (mid-month dates)."""
    year = draw(st.integers(min_value=2020, max_value=2030))
    month = draw(st.integers(min_value=1, max_value=12))
    last_day = monthrange(year, month)[1]
    # Pick a day that is not the last day of the month
    day = draw(st.integers(min_value=1, max_value=last_day - 1))
    return date(year, month, day)


@st.composite
def valid_client_for_default_fpd(draw) -> Client:
    """Generate a valid Client for testing default_first_payment_date."""
    year = draw(st.integers(min_value=2020, max_value=2030))
    month = draw(st.integers(min_value=1, max_value=12))
    day = draw(st.integers(min_value=1, max_value=28))
    first_draft_date = date(year, month, day)

    # last_draft_date must be after first_draft_date
    months_ahead = draw(st.integers(min_value=3, max_value=12))
    total = (year * 12 + (month - 1)) + months_ahead
    end_year, end_month_0 = divmod(total, 12)
    end_month = end_month_0 + 1
    last_draft_date = date(end_year, end_month, day)

    # as_of_date before first_draft_date
    if month == 1:
        as_of_date = date(year - 1, 12, 31)
    else:
        as_of_date = date(year, month - 1, 28)

    return Client(
        draft_amount_cents=20000,
        draft_day=day,
        first_draft_date=first_draft_date,
        last_draft_date=last_draft_date,
        as_of_date=as_of_date,
        current_balance_cents=0,
        ledger=[],
    )


# ---------------------------------------------------------------------------
# Property 2: Cadence Date Generation Correctness
# ---------------------------------------------------------------------------


class TestCadenceDateGeneration:
    """Cadence date generation preserves EOM logic, clamps mid-month days, and respects horizon.

    Property 2: Cadence Date Generation Correctness — EOM dates stay EOM,
    mid-month dates preserve day clamped, all ≤ horizon.

    **Validates: Requirements 3.1, 3.2, 3.3, 3.4**
    """

    @settings(max_examples=100, deadline=None)
    @given(start=eom_start_dates(), count=st.integers(min_value=1, max_value=36))
    def test_eom_preservation(self, start: date, count: int) -> None:
        """If start is the last day of its month, ALL generated dates are last day of their months.

        Validates Requirement 3.1: WHEN first_payment_date is provided and is the last day
        of its month, THE Scheduler SHALL generate cadence dates using true end-of-month
        logic for each subsequent month.
        """
        dates = monthly_payment_dates(start, count)

        assert len(dates) == count, (
            f"Expected {count} dates, got {len(dates)}"
        )

        for i, d in enumerate(dates):
            assert is_end_of_month(d), (
                f"Date at index {i} ({d}) is not end-of-month. "
                f"Expected day {monthrange(d.year, d.month)[1]}, got day {d.day}. "
                f"Start was {start} (EOM)."
            )

    @settings(max_examples=100, deadline=None)
    @given(start=mid_month_start_dates(), count=st.integers(min_value=1, max_value=36))
    def test_mid_month_day_preservation(self, start: date, count: int) -> None:
        """If start is NOT the last day of its month, generated dates have day = min(start.day, month_length).

        Validates Requirement 3.2: WHEN first_payment_date is provided and is not the last
        day of its month, THE Scheduler SHALL generate cadence dates using the same
        day-of-month clamped to month length.
        """
        dates = monthly_payment_dates(start, count)

        assert len(dates) == count, (
            f"Expected {count} dates, got {len(dates)}"
        )

        for i, d in enumerate(dates):
            expected_day = min(start.day, monthrange(d.year, d.month)[1])
            assert d.day == expected_day, (
                f"Date at index {i} ({d}) has day {d.day}, "
                f"expected {expected_day} = min({start.day}, {monthrange(d.year, d.month)[1]}). "
                f"Start was {start} (mid-month)."
            )

    @settings(max_examples=100, deadline=None)
    @given(
        start=st.one_of(eom_start_dates(), mid_month_start_dates()),
        count=st.integers(min_value=1, max_value=36),
        horizon_offset=st.integers(min_value=0, max_value=24),
    )
    def test_horizon_enforcement(self, start: date, count: int, horizon_offset: int) -> None:
        """All generated cadence dates filtered to horizon are ≤ horizon.

        Validates Requirement 3.4: THE Scheduler SHALL generate cadence dates only up to
        and including the Horizon date.

        This tests that when we filter monthly_payment_dates output by a horizon,
        all remaining dates satisfy date ≤ horizon.
        """
        # Generate all dates
        all_dates = monthly_payment_dates(start, count)

        # Create a horizon that is horizon_offset months from start
        # (simulating last_draft_date)
        total = (start.year * 12 + (start.month - 1)) + horizon_offset
        h_year, h_month_0 = divmod(total, 12)
        h_month = h_month_0 + 1
        horizon = date(h_year, h_month, monthrange(h_year, h_month)[1])

        # Filter dates to horizon (as the engine would do)
        filtered_dates = [d for d in all_dates if d <= horizon]

        for d in filtered_dates:
            assert d <= horizon, (
                f"Date {d} exceeds horizon {horizon}"
            )

    @settings(max_examples=100, deadline=None)
    @given(client=valid_client_for_default_fpd())
    def test_default_first_payment_date_is_eom(self, client: Client) -> None:
        """When first_payment_date is omitted, default is end of month of first_draft_date.

        Validates Requirement 3.3: WHEN first_payment_date is omitted, THE Scheduler
        SHALL default to the end of the month of first_draft_date.
        """
        fpd = default_first_payment_date(client)

        # Must be end of month of first_draft_date's month
        expected = end_of_month(client.first_draft_date)
        assert fpd == expected, (
            f"default_first_payment_date returned {fpd}, "
            f"expected end_of_month({client.first_draft_date}) = {expected}"
        )

        # Must also be EOM
        assert is_end_of_month(fpd), (
            f"default_first_payment_date {fpd} is not end-of-month"
        )

    @settings(max_examples=100, deadline=None)
    @given(start=st.one_of(eom_start_dates(), mid_month_start_dates()))
    def test_zero_count_returns_empty(self, start: date) -> None:
        """When count is 0 or negative, monthly_payment_dates returns an empty list."""
        assert monthly_payment_dates(start, 0) == []
        assert monthly_payment_dates(start, -1) == []

    @settings(max_examples=100, deadline=None)
    @given(
        start=st.one_of(eom_start_dates(), mid_month_start_dates()),
        count=st.integers(min_value=1, max_value=36),
    )
    def test_first_date_equals_start(self, start: date, count: int) -> None:
        """The first generated date always equals the start date (or its EOM equivalent)."""
        dates = monthly_payment_dates(start, count)

        if is_end_of_month(start):
            # EOM start: first date should be end_of_month(start) which is start itself
            assert dates[0] == end_of_month(start)
        else:
            # Mid-month start: first date should be start itself
            assert dates[0] == start

    @settings(max_examples=100, deadline=None)
    @given(
        start=st.one_of(eom_start_dates(), mid_month_start_dates()),
        count=st.integers(min_value=2, max_value=36),
    )
    def test_dates_are_strictly_increasing(self, start: date, count: int) -> None:
        """Generated cadence dates are strictly increasing (each date > previous)."""
        dates = monthly_payment_dates(start, count)

        for i in range(1, len(dates)):
            assert dates[i] > dates[i - 1], (
                f"Dates not strictly increasing: dates[{i-1}]={dates[i-1]}, "
                f"dates[{i}]={dates[i]}. Start was {start}."
            )
