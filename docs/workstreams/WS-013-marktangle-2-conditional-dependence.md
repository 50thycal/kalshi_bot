# WS-013 — MARKTANGLE-2: conditional dependence alpha

**Phase:** REVIEW
**Status:** Active
**Created:** 2026-09-02
**Updated:** 2026-09-02 (run 1)

## Goal

Stand up MARKTANGLE-2 — a separate, pre-registered historical experiment asking
whether measurable serial dependence in recurring Kalshi markets (reversion in
homogeneous fresh-event classes, continuation in daily crypto thresholds) is
tradeable after fees against the executable price — as frozen Experiment OS
objects plus a runnable instrument that emits the whole research package, so the
question is answered on history before any tag exists.

## Context

MARKTANGLE-1 (WS-011) is PAUSED at PROBE on a genuine HOLD: the coin-like families
(sports totals, weather buckets) are too thin individually, and daily crypto
thresholds turned out to be persistence machines (97.5% repeat on a 49.4% coin).
The operator's 2026-09-02 preregistration turns both findings into a new question
rather than a revision of the old one: pool the thin classes with family effects
(Track A), and treat crypto persistence as a state-duration process with the
underlying's distance to the strike as the structural variable (Track B). The
preregistration is `docs/MARKTANGLE_2_SPEC.md` Part I; Part II freezes every
implementation choice it left open.

## Current Mental Model

```text
  settled history per series ──► families SERIES|SUFFIX ──► STRUCTURAL classifier
                                                                │  (ticker pattern +
                                                                │   strike type only)
            ┌───────────────────────────────────────────────────┴──────────────┐
      TRACK A  fresh-event classes                          TRACK B  CRYPTO_DAILY:<asset>
      (SOCCER_TOTAL, WEATHER_HIGH_BUCKET, …)                 threshold families only
            │                                                       │
   prediction point i: (prev, streak dir, k)          + z_dir = ln(spot/strike)/σ20d
            │                                            (last COMPLETED hourly candle
   70/30 chronological split PER CLASS                    before T-60m; no lookahead)
            │                                                       │
   A0 base ─ A1 one-step ─ A2 streak table ─ A3 hier-logistic   B0 ─ B1 ─ B2 duration ─ B3 z-logistic
            │                     (PRIMARY)                                    (PRIMARY)
            └──────────────► TRAIN fits only ◄─────────────────────────────────┘
                                   │
     T-60m taker quote ─► best side by net edge (fee + 1c slip) ≥ 3c ─► 1 contract
                                   │
                 mirror = same entries, opposite side, same book
                                   │
     HOLDOUT grade per (class, arm): floors → HOLD; 7 clauses → PASS/FAIL
     track verdict = the PRIMARY arm only (A3 / B3), never the best-looking arm
                                   │
     stdout: DATA_REPORT · TRACK_A · TRACK_B · SUMMARY · TRADES.csv + fingerprints
     ──► scripts/marktangle2_package.py splits an ops result into docs/marktangle2/
```

Two facts shape the whole build:

1. **The sandbox cannot reach Kalshi or Coinbase** (proxy 403 on CONNECT), and the
   ops channel persists stdout only. So the instrument prints the entire package
   as marked sections, and the fingerprints are printed with it.
2. **`script` requests install psycopg only**, so the instrument is stdlib: the
   hierarchical logistic is a hand-written penalized Newton–Raphson, deterministic.

## Decisions Made

- **Separate experiment, MARKTANGLE-1 as predecessor.** A new question on a new
  unit with new floors is a new experiment (`predecessor_experiment_id` records
  the lineage). MARKTANGLE-1's contract, floors and HOLD are untouched, and a test
  proves registration leaves it unchanged.
- **70/30, no validation segment.** The spec allows either. Every model-selection
  degree of freedom (shrinkage m, ridge λ, buckets, z bins, cap, floors) is a
  frozen constant, so a validation segment would have nothing to select and would
  only shrink the holdout that MARKTANGLE-1 showed is the binding constraint.
- **Split per class by time, not per family.** Families in one class share
  regimes (one spot print for every strike on an asset; one weather system across
  a city's rungs). A per-family split would let a holdout period of one family
  overlap the train period of another.
- **Primary arm per track fixed before data: A3, B3.** The other treatments are
  read. A verdict that picks the best of three arms is a three-way search.
- **Structural classifier with a fixed sport table.** Totals pool only within a
  sport; an unknown league is reported and never pooled. Crypto bucket markets
  are not level crossings and are outside Track B.
- **Baseline comparator on two axes.** "Beats the independence comparator" means
  lower holdout Brier on the same prediction points AND higher holdout net P&L.
  One axis alone can be gamed by trade selection.
- **Mirror separation is 3c/trade**, consistent with MARKTANGLE-1's edge bar.
- **Gates registered at IDEA, evidence not started.** Four paper gates
  (`paper_to_live_canary_a/b`, `paper_keep_a/b`) exist on v1 before any evidence
  exists; no arm has a tag, so starting the clock now would floor future windows
  at a boundary predating any book.
- **The probe deployment is tagless.** Under NEW_ONLY, registering the contract
  grants no trading capability; a test asserts every arm key is inadmissible.
- **No maker simulation.** No reliable resting-fill model exists for these books
  and §12 forbids a maker PASS where taker fails; a maker arm would only be a way
  to look better.

## Open Decisions

- **D1.** Kalshi 1-minute candles for settled markets older than the live archive
  window may be served only by the historical endpoint, or not at all. The
  instrument tries both and reports coverage; HOLD on coverage < 50% is the
  preregistered answer. If coverage is low, the option is a forward quote collector
  (a platform change), not a relaxed floor.
- **D2.** The default fetch budget is 6,000 candle requests (holdout first). If a
  run hits the cap before pricing every holdout point, re-run with a larger
  `--max-fetch`; the models and the holdout are unchanged by the budget.
- **D3.** Coinbase as a proxy for Kalshi's settlement index. Adequate for a
  distance feature (the error is a few basis points against z-units of daily vol);
  stated in the spec. If Track B PASSes, the paper design must read the real index.

## Assumptions

- Kalshi's settled listing exposes `strike_type` and `floor_strike`/`cap_strike`
  on daily crypto markets; the ticker suffix (`T2464.99`) is the fallback and is
  tested.
- Coinbase's public candles cover the full history window at hourly and daily
  granularity (300 candles per request, chunked). Missing spot means B3 abstains
  and the count is reported; it never silently imputes.
- The ops runner's 6-hour job limit is enough for ~6,000 candle fetches plus the
  history pull. If not, the budget argument bounds it.

## Non-Goals

- Trading anything. No tag, no paper book, no exposure.
- A validation-set search over model choices.
- Any change to MARKTANGLE-1 (WS-011), including its resume condition.
- Building the prospective paper/twin design — that is the next gate only if a
  track PASSes, and it is a separate workstream.

## Build Card

Delivered in this PR (see Implementation State).

## Implementation State

Code complete and tested; **not yet registered in production and not yet run**.

- `docs/MARKTANGLE_2_SPEC.md` — preregistration (Part I as received; Part II frozen).
- `scripts/marktangle2_probe.py` — the instrument; `scripts/marktangle2_package.py` — splitter.
- `kalshi_bot/experiment_os/marktangle2.py` — contract package, wired as
  `REGISTER_PACKAGE marktangle-2` on the experiment-command transport.
- `scripts/ops_runner.py` — `marktangle2_probe` allowlisted.
- `tests/test_marktangle2_probe.py`, `tests/test_marktangle2_package.py`.

## Review State

Awaiting review.

## Related Decisions

`DEC-001` (two ledgers, one boundary) — this file tracks the build thread; the
experiment's standing and verdicts are Experiment OS's.

## Related PRs

This PR.

## Related Experiment OS objects

Linked, not restated — query Experiment OS for current state.

- `marktangle-2-conditional-dependence` — the experiment (v1, PROBE once registered).
- gates `paper_to_live_canary_a`, `paper_keep_a`, `paper_to_live_canary_b`, `paper_keep_b` on v1.
- deployment `marktangle2-probe-1` (kind PROBE, tagless).
- predecessor `marktangle-conditional-reversion` (MARKTANGLE-1, PAUSED at PROBE).

## Run log

**Run 1 (2026-09-02, ops `m2-run-1`, code `78788a2`) — NO VERDICT, instrument defect.**
The pull worked: 2,458 series enumerated, 29 pulled, 1,756 families seen, 1,015 at the
40-resolution floor, 167 analysed across 9 classes (4 crypto assets, baseball / basketball /
football / soccer totals, weather high buckets). Coinbase spot loaded for all four assets.
Then the price stage priced **nothing**: 6,000 candles fetched, 0 two-sided quotes, because
`decision_quote` read the legacy integer-cent `close` field while Kalshi's candlesticks carry
`close_dollars` — every other candle reader in the repo multiplies `close_dollars` by 100. A
second, smaller defect: the empty-trades robustness record lacked a key the Track A renderer
reads, so the run died before the summary. Both fixed with tests; the settled-history budget
raised from 25 to 60 pages because every crypto series hit the old cap (25,000 markets, data
only back to 2026-06-27). No holdout economics were ever produced, so nothing was read and
nothing frozen has moved. Re-run unchanged as `m2-run-2`.

**Registration: three FAILED envelopes, two defects, no partial rows.** Every attempt was
verified rolled back (`xos show` reported no such experiment after the third), so nothing
was half-registered and the key stayed free.

| envelope | error | cause |
|---|---|---|
| `m2-register-1` | `TypeError: register() got an unexpected keyword argument 'promotion_sample_floor'` | the transport always passes that keyword; neither MARKTANGLE package accepted it |
| `m2-register-2` | same | submitted before the fix reached the default branch — the worker runs default-branch code |
| `m2-register-3` | `AttributeError: 'int' object has no attribute 'version'` | the receipt builder reads identifiers off ORM OBJECTS the package returns; both packages returned the identifiers |

Both are the same class of defect and neither was reachable from any test: the packages and
the transport were each tested alone, and what broke was the **undocumented contract between
them**. `tests/test_xos_package_result_shape.py` now runs the real envelope through the real
executor for every package that registers standalone, which is the seam itself.

The second defect also exposed something worse than a bad receipt: `_result_of` runs inside
the command's transaction, after the package has written everything, so an exception while
FORMATTING the receipt discarded a registration that had already succeeded. It is now
best-effort per field (`fields_unreadable` names what it could not read) — an incomplete
receipt is a small loss, a destroyed write is not.

The retry after the fix merges is `m2-register-4`.

## Next Step

Blocked on merge: the ops runner executes default-branch code only. Then, in order:

1. `REGISTER_PACKAGE marktangle-2` on the experiment-command transport (operator).
2. Run 1, the full instrument:
   `{"type":"script","name":"marktangle2_probe","id":"m2-run-1"}`
   (~history pull for ~30 series + up to 6,000 candle fetches + Coinbase; expect tens
   of minutes). Read `ops/results/m2-run-1.txt`.
3. Split the package into the repo:
   `python scripts/marktangle2_package.py <result> docs/marktangle2` — the splitter
   re-derives the trades fingerprint and refuses a mismatch.
4. Record the per-track verdicts in `docs/RESEARCH_JOURNAL.md` exactly as printed.
   A HOLD is a real outcome. If either track PASSes, open the successor workstream
   for a prospective paper/twin design; never a live canary from a historical PASS.
