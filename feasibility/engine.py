"""Candidate implementation goes here.

Implement ``evaluate_offer`` so that it satisfies the rules in ASSIGNMENT.md and
the example expectations in tests/test_cases.py. The dataclasses below define the
required OUTPUT shape (see ASSIGNMENT.md "Output"). You may add helpers, modules,
or rewrite internals freely, but keep ``evaluate_offer``'s signature and the
serialized shape of ``Result`` (so the runner and tests work).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date

from feasibility.models import Client, CreditorRules, LedgerEntry, Offer


def round_half_up(value: float) -> int:
    """Round a float to the nearest integer using round-half-up semantics.

    For positive values, 0.5 rounds up (away from zero).
    For negative values, 0.5 rounds down (away from zero).
    This avoids Python's default banker's rounding (round-half-to-even).
    """
    if value >= 0:
        return math.floor(value + 0.5)
    else:
        # For negative values, round away from zero:
        # negate, round the positive value, negate back
        return -math.floor(-value + 0.5)


def compute_floors(k: int, rules: CreditorRules) -> list[int]:
    """Compute the effective floor for each payment position (1-based).

    For each position i in [1, k], the floor is:
        max(min_payment_cents, tier_floor(i), token_exceeded_floor(i))

    - Token rule: the first max_token_pays positions may sit at min_payment_cents;
      positions beyond that must strictly exceed it (floor = min_payment_cents + 1).
    - Tier rule: for each (from_payment, min_cents) in min_payment_tiers,
      if i >= from_payment, the floor is at least min_cents.

    Returns a list of k integers representing the floor for each position.
    """
    floors: list[int] = []
    for i in range(1, k + 1):
        # Base floor
        floor = rules.min_payment_cents

        # Token-exceeded rule: positions beyond max_token_pays must exceed base min
        if i > rules.max_token_pays:
            token_floor = rules.min_payment_cents + 1
            floor = max(floor, token_floor)

        # Tier rule: apply step-up floors from min_payment_tiers
        for from_payment, min_cents in rules.min_payment_tiers:
            if i >= from_payment:
                floor = max(floor, min_cents)

        floors.append(floor)
    return floors


def generate_even_payments(offer_total: int, k: int) -> list[int]:
    """Divide offer_total into k nearly-equal payments (non-decreasing).

    Uses divmod to get a base amount and remainder r. The last r payments
    each receive +1 cent, keeping the sequence non-decreasing.

    Example: offer_total=100, k=3 → [33, 33, 34]
    """
    base, r = divmod(offer_total, k)
    # First (k - r) payments get the base amount, last r payments get base + 1
    return [base] * (k - r) + [base + 1] * r


def generate_balloon_payments(offer_total: int, k: int, floors: list[int]) -> list[int] | None:
    """Generate a balloon payment schedule: minimum payments early, large final payment.

    Sets payments 1 through k-1 to their respective floors (minimum allowed amounts).
    The final payment absorbs the remainder: offer_total - sum(floors[0:k-1]).

    Returns None if the final payment would be less than its own floor (floors[k-1]),
    which would violate the floor constraint.

    The result is non-decreasing because the final payment >= all earlier floors
    (guaranteed by the validation that final >= floors[k-1], and floors represent
    minimum values that the earlier payments are set to).

    Requirements: 11.1, 11.2, 5.1, 6.1
    """
    if k == 1:
        # Single payment: the entire offer_total is the "balloon"
        if offer_total < floors[0]:
            return None
        return [offer_total]

    # Payments 1 through k-1 are set to their respective floors
    early_payments = floors[: k - 1]
    early_sum = sum(early_payments)

    # Final payment absorbs the remainder
    final_payment = offer_total - early_sum

    # Validate: final payment must be at least its own floor
    if final_payment < floors[k - 1]:
        return None

    return early_payments + [final_payment]


def generate_staircase_payments(
    offer_total: int, k: int, floors: list[int], max_segments: int
) -> list[int] | None:
    """Generate a staircase payment schedule: non-decreasing with at most max_segments distinct levels.

    Strategy:
    - Start all payments at their floors (enforced non-decreasing).
    - Compute the deficit: offer_total - sum(floors).
    - Partition positions into at most max_segments contiguous blocks.
    - Each block has a uniform level >= the max floor in that block.
    - The last block absorbs the surplus to hit exact sum.
    - Among valid partitions, choose the one that minimizes early payments.

    The objective is to keep early payments as low as floors allow to maximize
    early program fee collection (Req 12.3, 21.2).

    Returns None if:
    - The deficit is negative (floors sum exceeds offer_total)
    - The constraints cannot be satisfied

    Requirements: 12.1, 12.2, 12.3, 5.1, 6.1
    """
    if k == 0:
        return [] if offer_total == 0 else None

    if max_segments < 1:
        return None

    # Step 1: Enforce non-decreasing floors
    min_levels = list(floors)
    for i in range(1, k):
        if min_levels[i] < min_levels[i - 1]:
            min_levels[i] = min_levels[i - 1]

    # Check if the non-decreasing floors already exceed offer_total
    floor_sum = sum(min_levels)
    if floor_sum > offer_total:
        return None

    deficit = offer_total - floor_sum

    if deficit == 0:
        # No surplus to distribute. Check segment constraint.
        if len(set(min_levels)) <= max_segments:
            return min_levels
        # Too many natural distinct levels with exact sum - can't reduce segments
        # without raising some values (which would exceed offer_total).
        return None

    # Step 2: Find the best partition of k positions into at most max_segments blocks.
    # Each block [a..b] has level = min_levels[b] (minimum feasible for that block).
    # The last block's level is raised to absorb the surplus.
    # We want to minimize early payments (lexicographically smallest result).
    #
    # Use recursive/iterative search over block boundaries.
    # Since max_segments is small (typically 2-4), this is efficient.

    best_result: list[int] | None = None

    # Cap max_segments to k (can't have more segments than positions)
    effective_max_segments = min(max_segments, k)

    def _search(
        pos: int, segments_left: int, prev_level: int, prefix: list[int], prefix_sum: int
    ) -> None:
        nonlocal best_result

        if segments_left == 0:
            return

        remaining_positions = k - pos
        if remaining_positions <= 0:
            return

        # Can't use more segments than remaining positions
        seg_left = min(segments_left, remaining_positions)

        if seg_left == 1:
            # Last segment: all remaining positions get a uniform level (+ possible remainder)
            suffix_count = remaining_positions
            suffix_target = offer_total - prefix_sum

            # Minimum level for this block: max floor in range [pos..k-1] = min_levels[k-1]
            # Also must be >= prev_level (non-decreasing)
            suffix_min = max(min_levels[k - 1], prev_level)

            if suffix_min * suffix_count > suffix_target:
                return

            # Can we make it work with one uniform level?
            extra = suffix_target - suffix_min * suffix_count
            base_add, rem = divmod(extra, suffix_count)
            suffix_level = suffix_min + base_add

            if rem == 0:
                # One distinct level in suffix
                candidate = prefix + [suffix_level] * suffix_count
                # Check distinct count
                if len(set(candidate)) <= max_segments:
                    if best_result is None or _is_better(candidate, best_result):
                        best_result = candidate
            else:
                # Two distinct levels needed (suffix_level and suffix_level + 1)
                # This exceeds our segment budget (we only have 1 segment left)
                # Can't do it with 1 segment.
                pass
            return

        if seg_left >= 2:
            # Try different sizes for the current block
            # Current block covers [pos..pos+size-1]
            # Block level = max(min_levels[pos+size-1], prev_level)
            for size in range(1, remaining_positions + 1):
                # Leave at least 1 position for remaining segments
                if remaining_positions - size < seg_left - 1:
                    break

                block_end = pos + size - 1
                block_level = max(min_levels[block_end], prev_level)

                # If this is not the last segment, set block at its minimum level
                new_prefix = prefix + [block_level] * size
                new_sum = prefix_sum + block_level * size

                if new_sum > offer_total:
                    break  # Further sizes will only increase sum

                _search(pos + size, segments_left - 1, block_level, new_prefix, new_sum)

    _search(0, effective_max_segments, 0, [], 0)

    # Try an alternative: use segments_left=2 for the last portion to allow base/base+1
    def _search2(
        pos: int, segments_left: int, prev_level: int, prefix: list[int], prefix_sum: int
    ) -> None:
        nonlocal best_result

        remaining_positions = k - pos
        if remaining_positions <= 0:
            return

        # Cap segments to remaining positions
        seg_left = min(segments_left, remaining_positions)

        if seg_left < 2:
            return

        if seg_left == 2:
            # Last 2 segments: all remaining positions get base/base+1
            suffix_count = remaining_positions
            suffix_target = offer_total - prefix_sum
            suffix_min = max(min_levels[k - 1], prev_level)

            if suffix_min * suffix_count > suffix_target:
                return

            extra = suffix_target - suffix_min * suffix_count
            base_add, rem = divmod(extra, suffix_count)
            suffix_level = suffix_min + base_add

            if rem == 0:
                candidate = prefix + [suffix_level] * suffix_count
                if len(set(candidate)) <= max_segments:
                    if best_result is None or _is_better(candidate, best_result):
                        best_result = candidate
            else:
                # Use 2 levels: suffix_level and suffix_level + 1
                candidate = prefix + [suffix_level] * (suffix_count - rem) + [suffix_level + 1] * rem
                if len(set(candidate)) <= max_segments:
                    if best_result is None or _is_better(candidate, best_result):
                        best_result = candidate
            return

        # seg_left >= 3: try block sizes for current block
        for size in range(1, remaining_positions + 1):
            if remaining_positions - size < 2:  # need at least 2 positions for last 2 segments
                break

            block_end = pos + size - 1
            block_level = max(min_levels[block_end], prev_level)

            new_prefix = prefix + [block_level] * size
            new_sum = prefix_sum + block_level * size

            if new_sum > offer_total:
                break

            _search2(pos + size, seg_left - 1, block_level, new_prefix, new_sum)

    if effective_max_segments >= 2:
        _search2(0, effective_max_segments, 0, [], 0)

    return best_result


def _is_better(candidate: list[int], current_best: list[int]) -> bool:
    """Return True if candidate is better (lower early payments) than current_best.

    Compares lexicographically - lower early payments are preferred.
    """
    for a, b in zip(candidate, current_best):
        if a < b:
            return True
        if a > b:
            return False
    return False


def place_program_fees(
    cadence_dates: list[date],
    creditor_payments: list[int],
    total_fee: int,
    client: Client,
    rules: CreditorRules,
    extra_credits: list[LedgerEntry] | None = None,
) -> list[int] | None:
    """Greedily assign program fee to cadence dates, front-loading collection.

    On each cadence date (starting from the first creditor payment date), the
    available surplus after creditor payment and bank fee determines the maximum
    fee collectible. Fee-only dates (after the last creditor payment) may also
    be used if needed — no bank fee is charged on fee-only dates.

    Args:
        cadence_dates: Monthly cadence dates (may be longer than creditor_payments
            to allow fee-only dates after the last creditor payment).
        creditor_payments: Creditor payment amounts per cadence date (length k).
        total_fee: Total program fee to collect (integer cents).
        client: The client with ledger and balance information.
        rules: Creditor rules (provides bank_fee_cents).
        extra_credits: Optional additional credit entries to inject into the
            simulation (used for lump sum / monthly increment testing).

    Returns:
        A list of fee amounts per cadence date (same length as cadence_dates),
        or None if the total fee cannot be fully placed within the horizon.

    Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 21.1, 21.2
    """
    if total_fee == 0:
        return [0] * len(cadence_dates)

    # Number of creditor payment dates
    num_creditor_dates = len(creditor_payments)

    # Build the combined ledger of future entries (after as_of_date)
    # Include client ledger entries + any extra credits
    future_entries: list[LedgerEntry] = [
        e for e in client.ledger if e.date > client.as_of_date
    ]
    if extra_credits:
        future_entries.extend(
            e for e in extra_credits if e.date > client.as_of_date
        )

    # Separate into credits and debits for efficient lookup
    # Group by date for the simulation
    credits_by_date: dict[date, int] = {}
    debits_by_date: dict[date, int] = {}
    for entry in future_entries:
        if entry.type == "credit":
            credits_by_date[entry.date] = credits_by_date.get(entry.date, 0) + entry.amount_cents
        else:
            debits_by_date[entry.date] = debits_by_date.get(entry.date, 0) + entry.amount_cents

    # Collect all unique dates that matter for the simulation:
    # - All dates with ledger entries (credits/debits)
    # - All cadence dates
    all_dates: set[date] = set()
    all_dates.update(credits_by_date.keys())
    all_dates.update(debits_by_date.keys())
    all_dates.update(cadence_dates)

    # Sort all dates chronologically
    sorted_dates = sorted(all_dates)

    # Build a set of cadence dates for quick lookup
    cadence_date_set = set(cadence_dates)

    # Map cadence date -> index for quick lookup
    cadence_index: dict[date, int] = {d: i for i, d in enumerate(cadence_dates)}

    # Initialize simulation
    balance = client.current_balance_cents
    remaining_fee = total_fee
    fees: list[int] = [0] * len(cadence_dates)

    # Process dates in chronological order
    for d in sorted_dates:
        # Apply credits on this date first (credits before debits)
        balance += credits_by_date.get(d, 0)

        # Apply existing ledger debits on this date
        balance -= debits_by_date.get(d, 0)

        # If this is a cadence date, process creditor payment + bank fee + program fee
        if d in cadence_date_set:
            idx = cadence_index[d]

            # Determine creditor payment and bank fee for this date
            if idx < num_creditor_dates:
                creditor_pay = creditor_payments[idx]
                bank_fee = rules.bank_fee_cents
            else:
                # Fee-only date: no creditor payment, no bank fee
                creditor_pay = 0
                bank_fee = 0

            # Available surplus after creditor payment and bank fee
            available = balance - creditor_pay - bank_fee

            # Greedily assign as much fee as possible
            fee_this_date = min(remaining_fee, max(0, available))
            fees[idx] = fee_this_date
            remaining_fee -= fee_this_date

            # Debit creditor payment + bank fee + fee from balance
            balance -= (creditor_pay + bank_fee + fee_this_date)

    # If fee could not be fully placed, return None
    if remaining_fee > 0:
        return None

    return fees


def simulate(
    client: Client,
    creditor_schedule: list[tuple[date, int, int, int]],
    extra_credits: list[LedgerEntry] | None = None,
) -> list[tuple[date, int]]:
    """Replay the full ledger chronologically and return the balance timeline.

    Simulates the SDA account starting at current_balance_cents as of as_of_date.
    Only processes entries dated strictly after as_of_date. On each date, all
    credits are applied before all debits.

    Args:
        client: The client with ledger and balance information.
        creditor_schedule: List of (date, creditor_payment_cents, program_fee_cents,
            bank_fee_cents) tuples representing scheduled outflows.
        extra_credits: Optional additional credit entries to inject into the
            simulation (used for lump sum / monthly increment testing).

    Returns:
        A list of (date, end-of-date balance) tuples, one per unique date that
        has activity after as_of_date, in chronological order.

    Requirements: 13.1, 13.2, 13.3, 13.4, 18.1, 18.2, 20.1
    """
    balance = client.current_balance_cents

    # Collect all future ledger entries (after as_of_date)
    future_entries: list[LedgerEntry] = [
        e for e in client.ledger if e.date > client.as_of_date
    ]
    if extra_credits:
        future_entries.extend(
            e for e in extra_credits if e.date > client.as_of_date
        )

    # Group credits and debits by date from ledger entries
    credits_by_date: dict[date, int] = {}
    debits_by_date: dict[date, int] = {}
    for entry in future_entries:
        if entry.type == "credit":
            credits_by_date[entry.date] = credits_by_date.get(entry.date, 0) + entry.amount_cents
        else:
            debits_by_date[entry.date] = debits_by_date.get(entry.date, 0) + entry.amount_cents

    # Group scheduled debits by date from creditor_schedule
    scheduled_debits_by_date: dict[date, int] = {}
    for d, creditor_pay, program_fee, bank_fee in creditor_schedule:
        if d > client.as_of_date:
            total_debit = creditor_pay + program_fee + bank_fee
            scheduled_debits_by_date[d] = scheduled_debits_by_date.get(d, 0) + total_debit

    # Collect all unique dates with activity after as_of_date
    all_dates: set[date] = set()
    all_dates.update(credits_by_date.keys())
    all_dates.update(debits_by_date.keys())
    all_dates.update(scheduled_debits_by_date.keys())

    # Sort chronologically
    sorted_dates = sorted(all_dates)

    # Process each date: credits first, then debits
    timeline: list[tuple[date, int]] = []
    for d in sorted_dates:
        # Apply all credits on this date
        balance += credits_by_date.get(d, 0)

        # Apply all debits on this date (ledger debits + scheduled debits)
        balance -= debits_by_date.get(d, 0)
        balance -= scheduled_debits_by_date.get(d, 0)

        # Record end-of-date balance
        timeline.append((d, balance))

    return timeline


@dataclass
class ScheduleRow:
    date: date
    creditor_payment_cents: int
    program_fee_cents: int
    bank_fee_cents: int
    balance_cents: int


@dataclass
class FundsOption:
    amount_cents: int
    within_guardrail: bool
    reason: str
    # lump-sum only:
    date: date | None = None
    # monthly-increment only:
    num_drafts: int | None = None


@dataclass
class AdditionalFunds:
    lump_sum: FundsOption
    monthly_increment: FundsOption


@dataclass
class Result:
    feasible: bool
    # One of "even", "staircase", or "balloon" — the shape your solution produced
    # (driven by the creditor flags). None when infeasible.
    pay_shape_used: str | None = None
    schedule: list[ScheduleRow] | None = None
    additional_funds: AdditionalFunds | None = None

    def to_dict(self) -> dict:
        out: dict = {"feasible": self.feasible, "pay_shape_used": self.pay_shape_used}
        out["schedule"] = (
            [
                {
                    "date": r.date.isoformat(),
                    "creditor_payment_cents": r.creditor_payment_cents,
                    "program_fee_cents": r.program_fee_cents,
                    "bank_fee_cents": r.bank_fee_cents,
                    "balance_cents": r.balance_cents,
                }
                for r in self.schedule
            ]
            if self.schedule is not None
            else None
        )
        if self.additional_funds is None:
            out["additional_funds"] = None
        else:
            def opt(o: FundsOption) -> dict:
                d = {
                    "amount_cents": o.amount_cents,
                    "within_guardrail": o.within_guardrail,
                    "reason": o.reason,
                }
                if o.date is not None:
                    d["date"] = o.date.isoformat()
                if o.num_drafts is not None:
                    d["num_drafts"] = o.num_drafts
                return d

            out["additional_funds"] = {
                "lump_sum": opt(self.additional_funds.lump_sum),
                "monthly_increment": opt(self.additional_funds.monthly_increment),
            }
        return out


def _is_feasible_with_extra_credits(
    client: Client, offer: Offer, rules: CreditorRules,
    extra_credits: list[LedgerEntry] | None = None,
) -> bool:
    """Check if the offer becomes feasible when extra credits are injected.

    Runs the same logic as evaluate_offer's feasible path (shape generation,
    fee placement, simulation) but with extra_credits added to the ledger.
    Returns True if any valid k yields a feasible schedule.
    """
    from feasibility.models import default_first_payment_date, monthly_payment_dates

    # Compute totals
    offer_total = round_half_up(offer.settlement_pct * offer.current_balance_cents)
    program_fee_total = round_half_up(rules.program_fee_pct * offer.original_balance_cents)

    if offer_total == 0:
        return True

    # Determine first payment date
    first_pay_date = (
        offer.first_payment_date
        if offer.first_payment_date is not None
        else default_first_payment_date(client)
    )

    # Generate cadence dates up to horizon
    all_cadence_dates = monthly_payment_dates(first_pay_date, 120)
    cadence_dates = [d for d in all_cadence_dates if d <= client.last_draft_date]

    if not cadence_dates:
        return False

    # Compute max_k
    max_k = min(rules.max_payments, rules.max_terms, len(cadence_dates))

    # Select shape strategy
    if rules.even_pays:
        shape = "even"
    elif rules.is_ballooning_allowed:
        shape = "balloon"
    else:
        shape = "staircase"

    # Iterate k from max_k down to 1
    for k in range(max_k, 0, -1):
        floors = compute_floors(k, rules)

        if shape == "even":
            payments = generate_even_payments(offer_total, k)
            if not all(payments[i] >= floors[i] for i in range(k)):
                continue
        elif shape == "balloon":
            payments_or_none = generate_balloon_payments(offer_total, k, floors)
            if payments_or_none is None:
                continue
            payments = payments_or_none
        else:  # staircase
            payments_or_none = generate_staircase_payments(
                offer_total, k, floors, rules.max_segments
            )
            if payments_or_none is None:
                continue
            payments = payments_or_none

        # Validate non-decreasing
        if not all(payments[i] <= payments[i + 1] for i in range(len(payments) - 1)):
            continue

        # Validate exact sum
        if sum(payments) != offer_total:
            continue

        # Fee placement with extra credits
        fee_cadence_dates = cadence_dates
        fees = place_program_fees(
            fee_cadence_dates, payments, program_fee_total, client, rules,
            extra_credits=extra_credits,
        )
        if fees is None:
            continue

        # Build creditor_schedule for simulation
        creditor_schedule: list[tuple[date, int, int, int]] = []
        for i, d in enumerate(fee_cadence_dates):
            if i < k:
                cp = payments[i]
                bf = rules.bank_fee_cents
                pf = fees[i]
            else:
                cp = 0
                bf = 0
                pf = fees[i]
            if cp > 0 or pf > 0 or bf > 0:
                creditor_schedule.append((d, cp, pf, bf))

        # Simulate with extra credits
        timeline = simulate(client, creditor_schedule, extra_credits=extra_credits)

        # Check feasibility
        if all(b >= 0 for _, b in timeline):
            return True

    return False


def find_min_lump_sum(client: Client, offer: Offer, rules: CreditorRules) -> FundsOption:
    """Find the minimum lump sum that makes the offer feasible.

    Binary searches over L in [1, offer_total] for the smallest single credit
    that, when placed on the earliest useful date, makes a feasible schedule exist.

    The lump sum is tried on each draft date (after as_of_date, ≤ horizon),
    starting from the earliest. For each date, binary search finds the minimum L.
    The overall minimum (smallest L, earliest date) is returned.

    Guardrail: L ≤ round_half_up(0.65 × offer_total).

    Requirements: 15.1, 15.2, 15.3, 15.4
    """
    offer_total = round_half_up(offer.settlement_pct * offer.current_balance_cents)

    # Candidate dates: draft dates after as_of_date, up to horizon
    candidate_dates = sorted(set(
        e.date for e in client.ledger
        if e.date > client.as_of_date and e.date <= client.last_draft_date and e.type == "credit"
    ))

    # If no candidate dates from ledger, use first_draft_date through last_draft_date
    if not candidate_dates:
        from feasibility.models import monthly_payment_dates
        candidate_dates = [
            client.first_draft_date
        ]
        # Filter to after as_of_date and within horizon
        candidate_dates = [d for d in candidate_dates if d > client.as_of_date and d <= client.last_draft_date]

    # Upper bound for binary search: offer_total + program_fee + bank_fees covers
    # the worst case where the lump sum must fund everything in a single period.
    program_fee_total = round_half_up(rules.program_fee_pct * offer.original_balance_cents)
    max_k = min(rules.max_payments, rules.max_terms)
    upper_bound = offer_total + program_fee_total + rules.bank_fee_cents * max_k

    best_amount: int | None = None
    best_date: date | None = None

    for lump_date in candidate_dates:
        # Binary search over L in [1, upper_bound]
        lo, hi = 1, upper_bound

        # Quick check: is the max amount feasible on this date?
        extra = [LedgerEntry(date=lump_date, amount_cents=hi, type="credit")]
        if not _is_feasible_with_extra_credits(client, offer, rules, extra_credits=extra):
            continue

        # Binary search for minimum L on this date
        while lo < hi:
            mid = (lo + hi) // 2
            extra = [LedgerEntry(date=lump_date, amount_cents=mid, type="credit")]
            if _is_feasible_with_extra_credits(client, offer, rules, extra_credits=extra):
                hi = mid
            else:
                lo = mid + 1

        # lo == hi == minimum L for this date
        if best_amount is None or lo < best_amount:
            best_amount = lo
            best_date = lump_date

        # Since earlier dates are weakly more useful, if we found a result
        # on the earliest date, it's optimal. But we still check others
        # in case a later date yields a smaller L (unlikely but possible).
        # For efficiency, break if we found a result on the first date.
        break

    # If no date works (shouldn't happen if offer_total > 0 and there are dates)
    if best_amount is None:
        # Fallback: use upper_bound on the first available date
        best_amount = upper_bound
        best_date = candidate_dates[0] if candidate_dates else client.first_draft_date

    # Apply guardrail
    guardrail = round_half_up(0.65 * offer_total)
    within_guardrail = best_amount <= guardrail
    reason = ""
    if not within_guardrail:
        reason = f"Lump sum {best_amount} exceeds guardrail of {guardrail} (65% of offer total {offer_total})"

    return FundsOption(
        amount_cents=best_amount,
        within_guardrail=within_guardrail,
        reason=reason,
        date=best_date,
    )


def find_min_monthly_increment(client: Client, offer: Offer, rules: CreditorRules) -> FundsOption:
    """Find the minimum monthly increment that makes the offer feasible.

    Binary searches over X in [1, upper_bound] for the smallest uniform amount
    that, when added to every future draft (credit entries dated after as_of_date),
    makes a feasible schedule exist.

    Reports N = number of future drafts affected.

    Guardrail: X ≤ max(10000, round_half_up(0.40 × draft_amount_cents)).

    Requirements: 16.1, 16.2, 16.3, 16.4
    """
    offer_total = round_half_up(offer.settlement_pct * offer.current_balance_cents)

    # Identify future draft dates (credit entries after as_of_date)
    future_draft_dates = sorted(set(
        e.date for e in client.ledger
        if e.date > client.as_of_date and e.type == "credit"
    ))

    num_drafts = len(future_draft_dates)

    if num_drafts == 0:
        # No future drafts to augment — cannot solve via monthly increment
        return FundsOption(
            amount_cents=0,
            within_guardrail=False,
            reason="No future drafts available to augment",
            num_drafts=0,
        )

    # Upper bound for binary search: use offer_total as a generous upper bound
    upper_bound = offer_total

    # Quick check: is the upper bound feasible?
    extra_credits = [
        LedgerEntry(date=d, amount_cents=upper_bound, type="credit")
        for d in future_draft_dates
    ]
    if not _is_feasible_with_extra_credits(client, offer, rules, extra_credits=extra_credits):
        # Even the maximum increment doesn't work — report the upper bound
        guardrail_limit = max(10000, round_half_up(0.40 * client.draft_amount_cents))
        within_guardrail = upper_bound <= guardrail_limit
        reason = ""
        if not within_guardrail:
            reason = (
                f"Monthly increment {upper_bound} exceeds guardrail of {guardrail_limit} "
                f"(max of 10000 and 40% of draft amount {client.draft_amount_cents})"
            )
        return FundsOption(
            amount_cents=upper_bound,
            within_guardrail=within_guardrail,
            reason=reason,
            num_drafts=num_drafts,
        )

    # Binary search for minimum X in [1, upper_bound]
    lo, hi = 1, upper_bound

    while lo < hi:
        mid = (lo + hi) // 2
        extra_credits = [
            LedgerEntry(date=d, amount_cents=mid, type="credit")
            for d in future_draft_dates
        ]
        if _is_feasible_with_extra_credits(client, offer, rules, extra_credits=extra_credits):
            hi = mid
        else:
            lo = mid + 1

    # lo == hi == minimum X
    best_amount = lo

    # Apply guardrail: X <= max(10000, round_half_up(0.40 * draft_amount_cents))
    guardrail_limit = max(10000, round_half_up(0.40 * client.draft_amount_cents))
    within_guardrail = best_amount <= guardrail_limit
    reason = ""
    if not within_guardrail:
        reason = (
            f"Monthly increment {best_amount} exceeds guardrail of {guardrail_limit} "
            f"(max of 10000 and 40% of draft amount {client.draft_amount_cents})"
        )

    return FundsOption(
        amount_cents=best_amount,
        within_guardrail=within_guardrail,
        reason=reason,
        num_drafts=num_drafts,
    )


def evaluate_offer(client: Client, offer: Offer, rules: CreditorRules) -> Result:
    """Evaluate a single offer. See ASSIGNMENT.md for the full specification.

    Return a Result with feasible=True and a schedule when the offer fits, or
    feasible=False with additional_funds (minimum lump sum AND minimum monthly
    increment) when it does not.
    """
    from feasibility.models import default_first_payment_date, monthly_payment_dates

    # Step 1: Compute totals
    offer_total = round_half_up(offer.settlement_pct * offer.current_balance_cents)
    program_fee_total = round_half_up(rules.program_fee_pct * offer.original_balance_cents)

    # Step 2: Trivially feasible if offer_total is 0
    if offer_total == 0:
        # Still need to collect program fee if any
        first_pay_date = (
            offer.first_payment_date
            if offer.first_payment_date is not None
            else default_first_payment_date(client)
        )
        if program_fee_total == 0:
            return Result(feasible=True, pay_shape_used=_shape_name(rules), schedule=[])
        # Generate cadence dates for fee-only collection
        cadence_dates = monthly_payment_dates(first_pay_date, 120)
        cadence_dates = [d for d in cadence_dates if d <= client.last_draft_date]
        if not cadence_dates:
            return Result(feasible=False)
        # Place fees with no creditor payments
        fees = place_program_fees(cadence_dates, [], program_fee_total, client, rules)
        if fees is None:
            return Result(feasible=False)
        # Build schedule with fee-only rows
        schedule_entries: list[tuple[date, int, int, int]] = []
        for i, d in enumerate(cadence_dates):
            if fees[i] > 0:
                schedule_entries.append((d, 0, fees[i], 0))
        # Simulate to get balances
        timeline = simulate(client, schedule_entries)
        balance_map: dict[date, int] = {d: b for d, b in timeline}
        # Check feasibility
        if any(b < 0 for _, b in timeline):
            return Result(feasible=False)
        schedule_rows = [
            ScheduleRow(
                date=d,
                creditor_payment_cents=cp,
                program_fee_cents=pf,
                bank_fee_cents=bf,
                balance_cents=balance_map.get(d, 0),
            )
            for d, cp, pf, bf in schedule_entries
        ]
        return Result(
            feasible=True,
            pay_shape_used=_shape_name(rules),
            schedule=schedule_rows,
        )

    # Step 3: Determine first payment date
    first_pay_date = (
        offer.first_payment_date
        if offer.first_payment_date is not None
        else default_first_payment_date(client)
    )

    # Step 4: Generate cadence dates up to horizon
    # Generate a large number and filter to those <= last_draft_date
    all_cadence_dates = monthly_payment_dates(first_pay_date, 120)
    cadence_dates = [d for d in all_cadence_dates if d <= client.last_draft_date]

    if not cadence_dates:
        return Result(feasible=False)

    # Step 5: Compute max_k
    max_k = min(rules.max_payments, rules.max_terms, len(cadence_dates))

    # Step 6: Select shape strategy
    if rules.even_pays:
        shape = "even"
    elif rules.is_ballooning_allowed:
        shape = "balloon"
    else:
        shape = "staircase"

    # Step 7: Iterate k from max_k down to 1 to find best feasible schedule
    best_result: Result | None = None

    for k in range(max_k, 0, -1):
        # Compute floors for this k
        floors = compute_floors(k, rules)

        # Generate payment vector based on shape
        if shape == "even":
            payments = generate_even_payments(offer_total, k)
            # Validate floors: each payment must meet its floor
            valid = all(payments[i] >= floors[i] for i in range(k))
            if not valid:
                continue
        elif shape == "balloon":
            payments_or_none = generate_balloon_payments(offer_total, k, floors)
            if payments_or_none is None:
                continue
            payments = payments_or_none
        else:  # staircase
            payments_or_none = generate_staircase_payments(
                offer_total, k, floors, rules.max_segments
            )
            if payments_or_none is None:
                continue
            payments = payments_or_none

        # Validate non-decreasing (should be guaranteed by generators, but double-check)
        if not all(payments[i] <= payments[i + 1] for i in range(len(payments) - 1)):
            continue

        # Validate exact sum
        if sum(payments) != offer_total:
            continue

        # Build cadence dates for creditor payments (first k dates)
        # and extend for potential fee-only dates
        creditor_dates = cadence_dates[:k]

        # For fee placement, use all cadence dates up to horizon
        # (allows fee-only dates after last creditor payment)
        fee_cadence_dates = cadence_dates

        # Place program fees greedily
        fees = place_program_fees(
            fee_cadence_dates, payments, program_fee_total, client, rules
        )
        if fees is None:
            continue

        # Build the creditor_schedule for simulation
        # Each entry: (date, creditor_payment, program_fee, bank_fee)
        creditor_schedule: list[tuple[date, int, int, int]] = []
        for i, d in enumerate(fee_cadence_dates):
            if i < k:
                cp = payments[i]
                bf = rules.bank_fee_cents
                pf = fees[i]
            else:
                cp = 0
                bf = 0
                pf = fees[i]
            # Only include dates that have activity
            if cp > 0 or pf > 0 or bf > 0:
                creditor_schedule.append((d, cp, pf, bf))

        # Simulate and check feasibility
        timeline = simulate(client, creditor_schedule)
        balance_map = {d: b for d, b in timeline}

        # Check if all balances >= 0
        if any(b < 0 for _, b in timeline):
            continue

        # Feasible! Build the Result with ScheduleRow entries
        schedule_rows = []
        for d, cp, pf, bf in creditor_schedule:
            schedule_rows.append(
                ScheduleRow(
                    date=d,
                    creditor_payment_cents=cp,
                    program_fee_cents=pf,
                    bank_fee_cents=bf,
                    balance_cents=balance_map.get(d, 0),
                )
            )

        best_result = Result(
            feasible=True,
            pay_shape_used=shape,
            schedule=schedule_rows,
        )
        # For the feasible path, we take the first feasible k (max_k down)
        # which maximizes the number of payments and thus front-loads fees best
        break

    if best_result is not None:
        return best_result

    # No k is feasible — compute minimum additional funds
    lump_option = find_min_lump_sum(client, offer, rules)
    monthly_option = find_min_monthly_increment(client, offer, rules)

    return Result(
        feasible=False,
        pay_shape_used=None,
        schedule=None,
        additional_funds=AdditionalFunds(
            lump_sum=lump_option,
            monthly_increment=monthly_option,
        ),
    )


def _shape_name(rules: CreditorRules) -> str:
    """Determine the shape name based on creditor rules."""
    if rules.even_pays:
        return "even"
    elif rules.is_ballooning_allowed:
        return "balloon"
    else:
        return "staircase"
