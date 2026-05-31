# Settlement Feasibility & Fee Engine — Take-home

Welcome, and thanks for taking the time. The full problem is in
[`ASSIGNMENT.md`](./ASSIGNMENT.md). This README is just orientation.

## The task in one line

Given a client's escrow account, a settlement offer, and a creditor's rules,
decide whether the offer is affordable (and schedule it, collecting our fee as
early as allowed) or — if not — compute the minimum extra funding needed.

## Setup

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

## Layout

```
hiring_takehome/
├── ASSIGNMENT.md            # full specification — read this
├── feasibility/
│   ├── models.py            # data models, JSON loaders, date/EOM helpers (provided)
│   └── engine.py            # >>> implement evaluate_offer here <<< (+ Result shape)
├── cases/                   # four example cases (client.json / offer.json / creditor_rules.json)
│   ├── case1_feasible_even
│   ├── case2_infeasible_minima
│   ├── case3_balloon
│   └── case4_tiers
├── tests/
│   ├── test_smoke.py        # scaffolding sanity tests (pass out of the box)
│   └── test_cases.py        # example expectations — make these pass, then add your own
├── run.py                   # python run.py cases/<case>
└── requirements.txt
```

## Run

```bash
# evaluate a single case (prints the Result as JSON)
python run.py cases/case1_feasible_even

# tests
pytest -q
```

Out of the box, `tests/test_smoke.py` passes and `tests/test_cases.py` fails —
the latter is your target. Go beyond those four cases with your own tests.

## What to submit

Your implementation, your tests, and a short README section describing:
- your approach and the alternatives you considered,
- **your interpretation of the payment shapes** (even / staircase / balloon — we
  left these loosely defined on purpose),
- assumptions you made, and known edge cases / limitations.

Budget ~5–6 hours. Prefer a correct, well-tested core over breadth. When in
doubt, write down your assumption and keep going.

---

## Approach

The engine is decomposed into pure functions with a single orchestrator
(`evaluate_offer`) that wires them together:

1. **Foundational utilities** — `round_half_up` (explicit half-away-from-zero via
   `math.floor(v + 0.5)`) and `compute_floors` (composite floor from base min,
   token-pay rule, and tier step-ups).

2. **Payment shape generators** — three functions that each produce a valid payment
   vector given `offer_total`, `k`, and floors. They enforce exact sum and
   non-decreasing order by construction.

3. **Greedy fee placement** — `place_program_fees` runs a forward simulation,
   consuming available surplus on each cadence date for program fee before moving
   to the next. This naturally front-loads fee collection.

4. **Simulation** — `simulate` replays the full ledger (committed entries +
   scheduled payments/fees) date-by-date, credits-before-debits, and returns the
   balance timeline.

5. **Infeasibility path** — `find_min_lump_sum` and `find_min_monthly_increment`
   use binary search over the funding amount, re-running the full feasibility
   check at each candidate.

### Alternatives considered

- **LP/MIP solver** for optimal fee placement: would guarantee global optimality
  but adds a heavy dependency (ortools/PuLP) and is harder to reason about
  correctness. The greedy approach is provably optimal here because fee placement
  is a single-pass forward problem — collecting fee earlier never hurts later
  dates.

- **Iterating k from 1 upward** instead of max_k downward: more payments means
  lower per-payment amounts, which leaves more surplus for fees on early dates.
  Iterating from max_k down finds the best k first and short-circuits.

- **Exhaustive search over lump-sum dates**: the current implementation places the
  lump sum on the earliest draft date and binary-searches the amount. An earlier
  credit is weakly more useful (it's available for all subsequent dates), so
  trying the earliest date first is sufficient.

---

## Payment Shape Interpretation

### Even (`even_pays = true`)

All creditor payments are equal. When `offer_total` is not evenly divisible by `k`,
the remainder `r` cents are distributed to the **last** `r` payments (+1 cent each),
keeping the sequence non-decreasing. This satisfies the "as equal as possible"
requirement. The engine tries all valid `k` values (max_k down to 1) and picks the
first feasible one — larger `k` means smaller per-payment amounts, which leaves
more room for early fee collection.

### Balloon (`is_ballooning_allowed = true`, `even_pays = false`)

Payments 1 through k-1 are set to their **effective floors** (the minimum allowed
at each position, considering base min, token-pay rule, and tier step-ups). The
final payment absorbs the entire remainder: `offer_total - sum(floors[0:k-1])`.

This maximizes early surplus for fee collection — early payments are as small as
the rules allow, so the balance available for program fee on those dates is
maximized.

**Token pays and tiers interact with balloon as follows:** the first
`max_token_pays` positions may sit at `min_payment_cents`; positions beyond that
get `min_payment_cents + 1` as their floor. Tier step-ups override both. The
balloon's early payments respect all of these floors, and the final payment must
also meet its own floor (otherwise that `k` is skipped).

### Staircase (`even_pays = false`, `is_ballooning_allowed = false`)

A non-decreasing sequence with at most `max_segments` distinct payment levels. The
engine uses a recursive search over block boundaries:

1. Start all payments at their effective floors (enforced non-decreasing).
2. Compute the deficit: `offer_total - sum(floors)`.
3. Partition positions into at most `max_segments` contiguous blocks, each with a
   uniform level ≥ the maximum floor in that block.
4. The last block absorbs the surplus.
5. Among valid partitions, select the one that is **lexicographically smallest**
   (lowest early payments), which maximizes early fee collection.

When the deficit doesn't divide evenly into the last block, a two-level split
(base / base+1) is used if the segment budget allows it.

**Step placement under `max_segments`:** steps are placed as late as possible. With
`max_segments = 2`, the typical result is: a low level for the first N positions
(at floor), then a higher level for the remaining positions that absorbs the
surplus. This keeps early payments minimal.

---

## Assumptions

1. **`creditor_balance_cents` vs `current_balance_cents` on Offer:** The assignment
   notes a rename. The provided `models.py` uses `current_balance_cents` on the
   Offer dataclass. I use that field for computing `offer_total`.

2. **Lump sum date selection:** The assignment says "on a date of your choosing
   (≤ horizon)." I place the lump sum on the earliest future draft date because
   an earlier credit is weakly more useful — it's available for all subsequent
   payment dates.

3. **Monthly increment scope:** "Every future draft" means every credit entry in
   the ledger dated after `as_of_date`. The increment is added as additional
   credits on those same dates.

4. **Fee-only dates after last creditor payment:** When the program fee can't be
   fully collected alongside creditor payments, the engine extends to subsequent
   cadence dates (still within horizon) as fee-only months with no bank fee.

5. **Shape selection is deterministic:** The shape is determined solely by the
   `even_pays` and `is_ballooning_allowed` flags. When both are false, staircase
   is used. When `even_pays` is true, ballooning is irrelevant.

6. **k iteration order:** For all shapes, the engine tries k from max_k down to 1
   and takes the first feasible result. More payments generally means lower
   per-payment amounts and better fee front-loading.

---

## Known Edge Cases and Limitations

1. **Staircase search complexity:** The recursive search over block boundaries is
   O(k^max_segments). For typical values (k ≤ 12, max_segments ≤ 4) this is fast,
   but could be slow for very large k with many segments.

2. **Binary search upper bound:** The infeasibility binary search uses `offer_total`
   as the upper bound. In extreme cases where the program fee is very large relative
   to available funds, this bound might be insufficient for the monthly increment.
   The implementation handles this by reporting the upper bound with a guardrail
   failure.

3. **Single lump-sum date:** The implementation only tries the earliest draft date
   for the lump sum. In theory, a later date could yield a smaller L if the
   deficit is concentrated later in the timeline. This is unlikely in practice
   (earlier cash is always at least as useful) but not impossible with unusual
   ledger debit patterns.

4. **Offer total = 0:** Handled as trivially feasible. If there's still a program
   fee to collect, fee-only cadence dates are used.

5. **Balance hitting exactly $0:** The engine considers this feasible (balance ≥ 0,
   not strictly > 0). This is tested explicitly in the integration tests.

6. **Existing ledger debits:** These are respected without modification. They can
   cause infeasibility even when the payment structure alone would fit — the
   simulation includes all committed debits from other settlements.
