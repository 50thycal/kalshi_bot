# WS-013 — MARKTANGLE-2: conditional dependence alpha

**Phase:** REVIEW
**Status:** Active
**Created:** 2026-09-02
**Updated:** 2026-09-02 (run 2 — both tracks HOLD)

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

**`m2-register-4` SUCCEEDED (2026-09-02T16:21:24Z).** The experiment reads back at
**PROBE**, v1 frozen, epoch 1 open, deployment `marktangle2-probe-1` started and tagless, and
the four gates recorded with their pre-registration spec hashes:

| gate | spec hash (16) |
|---|---|
| `paper_to_live_canary_a` | `96da296a97574f2b` |
| `paper_keep_a` | `b6c11a8551c8f141` |
| `paper_to_live_canary_b` | `59d807a6ad39860c` |
| `paper_keep_b` | `b2c6228a4e0ce9ec` |

**MARKTANGLE-1 IS NOT IN PRODUCTION, and MARKTANGLE-2 therefore has no predecessor link.**
`predecessor_experiment_id` is null because `get_experiment('marktangle-conditional-reversion')`
found nothing; a direct read confirms it: *no experiment 'marktangle-conditional-reversion'*.
Its own documents say otherwise — `docs/MARKTANGLE_THESIS.md` calls it "v1 frozen at
registration · stage PROBE", the journal says REGISTERED at PROBE (2026-08-29) and PAUSED
(2026-08-30), and WS-011 says the same. None of that is true of the database.

The cause is almost certainly the defect this workstream just found: MARKTANGLE-1's
`REGISTER_PACKAGE` envelope would have raised the same `TypeError` on
`promotion_sample_floor` that killed `m2-register-1`, because its package had the same gap.
An experiment that was never created also could not be paused, so `marktangle_pause.py`
either never ran or failed. What is durable and unaffected: the probe runs, the data findings
and the HOLD reasoning, all of which live in the research document rather than in XOS.

**This is not MARKTANGLE-2's to repair.** Registering MARKTANGLE-1 now would create it at
PROBE, when the operator's recorded decision is PAUSED — a lifecycle move, and an operator's
call. Recommended next write: an Experiment OS issue against the MARKTANGLE-1 lineage, then
register-and-pause if the operator still wants that history in the system. Caveat for whoever
does it: `EXPERIMENT_OS_ISSUE_COMMAND` currently holds another session's envelope, so check
its receipt is terminal before overwriting.

**Run 2 (2026-09-02, ops `m2-run-2`, code `6933763`) — BOTH TRACKS HOLD, and the two HOLDs
mean completely different things.** Package split into `docs/marktangle2/`, trades
fingerprint verified (`51bbdc53…`). The candle fix worked: Track A holdout priced at
95-99% against run 1's 0%.

```
A  HOLD  A3 fails in 3 classes and is under-powered in 2; the track is not adequately answered
B  HOLD  B3 under-powered in every class (4)
```

**Track A's HOLD contains three adequately-powered FAILs**, and they are the substance:

| class | holdout trades | EV/trade | mirror EV | A3 verdict |
|---|---|---|---|---|
| BASEBALL_TOTAL | 2040 | −3.17c | −3.98c | FAIL |
| BASKETBALL_TOTAL | 243 | −5.83c | −1.07c | FAIL |
| SOCCER_TOTAL | 153 | −9.59c | +1.82c | FAIL |
| FOOTBALL_TOTAL | 54 | −9.80c | +2.44c | HOLD (train 237 < 500; trades 54 < 100) |
| WEATHER_HIGH_BUCKET | 0 | — | — | HOLD (train 54 < 500; trades 0 < 100) |

Every FAIL fails the same way and it is not marginal: negative net P&L, negative EV, no
mirror separation, and negative after removing the top family AND the top 1% of trades.
Both the treatment and its mirror lose roughly the cost of trading (fee + 1c slippage ≈
3-4c), which is what "the modelled edge is not there" looks like. In SOCCER the mirror is
**positive** while the treatment loses 9.59c — the model is not merely uninformative there,
it is wrong-signed.

**The hypothesis's own coefficient says the same.** `prev_dir × ln(k)` is the term that
carries "reversal probability rises with streak length" (it must be NEGATIVE):

| class | prev_dir | ln(k) | prev_dir × ln(k) |
|---|---|---|---|
| BASEBALL_TOTAL | −0.078 (z −2.14) | −0.004 (z −0.12) | −0.019 (z **−0.49**) |
| BASKETBALL_TOTAL | −0.382 (z −3.59) | −0.274 (z −1.88) | **+0.466 (z +3.23)** |
| SOCCER_TOTAL | −0.006 (z −0.05) | −0.009 (z −0.08) | +0.193 (z +1.62) |

Mild one-step reversion is real in baseball and basketball totals (`prev_dir` significant),
but streak LENGTH adds nothing: the interaction is indistinguishable from zero in two
classes and, where it is significant, it has the **wrong sign** — persistence strengthens
with k, the opposite of A1. Meanwhile A2/A3 beat the base rate on Brier in several classes
while losing money, which is §22's Outcome 3 exactly: forecastability is not alpha.

**Track B's HOLD is a data fact, not a thin result: the crypto ladder has no executable
price.** Of 9,980 BTC holdout points, the fetch budget reached ~2,000 and **16** returned a
two-sided quote at T−60m (<1%); ETH, SOL and XRP were never reached. Coverage 0% against a
50% floor. This is D1 answered with evidence rather than assumption: pooling all 113 BTC
rungs puts mostly permanently-in/out-of-the-money strikes in the class, and a rung nobody
trades has an empty book an hour before close. A larger `--max-fetch` does not fix a <1%
hit rate — 50% coverage would need ~5,000 priced points.

**What must NOT happen next**, and is why this is recorded before any re-run is designed:
narrowing the crypto class to near-the-money rungs, or re-reading Track A's bar, would both
be post-hoc re-scoping after the holdout was opened (§11, §19 general kill). If the class
definition is wrong it is a new Version, not an edit.

## CLOSED 2026-09-03 — both tracks, on operator decision

The two decisions this workstream left open on 2026-09-02 were answered together:
**close Track A and Track B, and record the results.** Retired through
`CLOSE_OUT_RETROSPECTIVE` (package `marktangle-2`), which *adopts* the contract
already registered in production — it refuses to author a second one, because the
verdicts belong to the objects `m2-register-4` created.

| gate | verdict | what it records |
|---|---|---|
| `paper_to_live_canary_a` | **FAIL** | the primary lost money in all three adequately-powered classes on untouched holdout, failing net P&L, EV/trade, the 3c mirror separation, and staying negative after dropping both the top family and the top 1% of trades |
| `paper_keep_a` | **FAIL** | the mechanism: `prev_dir × ln(k)` measured −0.019 (z −0.49), **+0.466 (z +3.23)**, +0.193 (z +1.62). Streak length carries nothing |
| `paper_to_live_canary_b` | **BLOCKED_DATA** | 16 two-sided quotes at T−60m in ~2,000 reached BTC holdout points; coverage 0% against a 50% floor. D1 answered with evidence |
| `paper_keep_b` | **BLOCKED_DATA** | the prediction was never the problem — 98.3% holdout accuracy, Brier 0.015 vs 0.045. Predictable and unpriceable |

**Where this departs from the instrument, stated rather than smoothed over.** The
frozen track rule printed `A HOLD` / `B HOLD`. These rows say FAIL and BLOCKED_DATA,
and that gap is an operator conclusion:

- Track A printed HOLD *only* because two of five classes were under-powered. §19's
  Track A kill rule and the track-verdict rule pointed in opposite directions — a
  defect in the frozen rule, not an open question about the evidence. Recording HOLD
  would have filed the line's one real falsification as "no result".
- Track B printed HOLD on the coverage floor. HOLD invites "wait for more evidence";
  more evidence will never come, because the class has no book rather than a thin one.
  BLOCKED_DATA is the same call, on the same reasoning, as PERP-V1's funding arm.

The departure is written into `marktangle2.CLOSE_OUT_VERDICTS` above the table itself,
and a test asserts it stays there: a silent relabel of a frozen rule's output is
precisely the post-hoc repricing §11 forbids, and saying so out loud is what makes an
operator conclusion legible as one.

**Nothing was re-scoped.** Narrowing the crypto class to near-the-money rungs, or
re-reading Track A's bar, would both be post-hoc re-scoping after the holdout was
opened. If the class definition is wrong the remedy is a new Version or forward quote
collection — neither was done here, and neither is proposed.

**Why the receipts read `TASK_SPECIFIC`.** `CLOSE_OUT_RETROSPECTIVE` is closed to
`RESEARCH_LAB` on purpose — the session that ran an experiment should not also be the
one that writes down its own verdict — and the session that built and ran this
instrument is a Research Lab session, so the guard is aimed squarely at it. Put to the
operator on 2026-09-03, who directed that this session submit both envelopes as
`TASK_SPECIFIC` rather than open a separate Live Ops window. The receipts therefore
name the role the act was performed under, not the role the session was following, and
`approved_by` names the operator who made that call. Recorded here because an
unexplained `TASK_SPECIFIC` on a retrospective verdict is exactly the kind of audit row
a later reader should be able to account for.

## Next Step

None. Both experiments in the MARKTANGLE line are RETIRED in Experiment OS with every
gate carrying a verdict. Any successor is a new question and a new workstream, and
starts from what this line established: mild one-step reversion in sports totals is
real and does not survive its own trading cost; streak length adds nothing; crypto
threshold persistence is real, strongly forecastable, and unpriceable where it exists.
