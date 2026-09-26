# WS-020 — Liquidity-incentive shadow market maker (Phase 0: instrumentation only)

**Phase:** REVIEW
**Status:** Active
**Created:** 2026-09-16
**Updated:** 2026-09-17
**Build OS:** v0.12

## Goal

Answer one question with evidence instead of a survey: can a small Kalshi account earn
≥ ~$1/day of repeatable *net* value by genuinely quoting both sides of markets in Kalshi's
Liquidity Incentive Program, after fees and single-leg adverse selection, at ≤ $250–$500 of
capital. Phase 0 builds the shadow instrument that measures it; it places no orders.

## Context

Calvin's 2026-09-16 handoff. External research says the program pays resting liquidity, scores
YES and NO separately, and that retail accounts report ~$200/month. Nothing in the repository
touched incentive programs before this; the maker-fill lesson from MMSELL
(`docs/MMSELL_FILL_MODEL.md`) says the risk is the fills we win. Pre-registration:
`docs/LIQUIDITY_INCENTIVE_THESIS.md`; what was inspected and verified:
`docs/LIQUIDITY_INCENTIVE_RESEARCH.md`.

## Current Mental Model

```text
worker (any mode, LIQUIDITY_INCENTIVE_SHADOW_ENABLED=true on ONE service)
  └─ incentive-shadow daemon thread (GET-only client wrapper; writes incentive_* only)
       ├─ every 5 min  GET /incentive_programs?status=active  → versioned terms rows
       │               + GET /markets/{t}, GET /series/{s} once per new terms (fee rule)
       ├─ WebSocket orderbook_delta(use_yes_price) + trade per tracked market (≤150),
       │  market_lifecycle_v2 once; raw tape persisted (capped), local book per market
       ├─ every 60 s per market: market snapshot (field score, reference price, target test)
       │  + one shadow pair per policy(A/B/C) × tier($25..$500), ended only for a
       │  legitimate reason; reward accrual from the scoring model
       ├─ every trade: replay against every resting leg under optimistic / conservative /
       │  queue-aware → fills rows, marks scheduled at 1s/5s/30s/60s/5m
       └─ pair end → one outcome row per fill model; settlement stamped when the market resolves
readers: livedash /incentives · ops script liquidity_incentive_report
```

## Decisions Made

- **Separate instrument, same construction as WS-019.** Its own thread, tables, page and
  wrapper; imports `LocalBook`/parsers/WS plumbing rather than extending the live-money
  collector. `DEC-015`.
- **No XOS package for Phase 0.** Nothing trades, no `paper_trades` tag is written; WS-017 /
  WS-019 precedent. A live POC is a new experiment later, armed only via `arm_live_canary`.
- **Default OFF, one worker.** Operator turns it on through the env channel; two workers would
  double-write the tape.
- **Three fill models, never averaged.** Reported side by side; promotion criteria read the
  conservative one only.
- **Derived vs observed enforced by naming** (`est_*`), and every assumption in the scoring
  model is labelled (A1–A5) with the direction of its bias.
- **Fee model untouched** (Platform component); a per-market `FeeRule` is resolved from the
  series object and stored as data.

## Open Decisions

- **D1.** Which worker hosts the thread (the live worker already runs WS-019's socket; a
  paper/scanner worker keeps the two instruments' failure modes apart). Recommendation: the
  worker with the fewest other duties, set by the operator with the enable variable.
- **D2.** Whether to add `upcoming` programs to discovery so a pair can be ready at program
  start (quantfirm's "fresh programs pay 119× more" prior). Recommendation: after the first
  week's data says whether early hours matter; not before.

## Assumptions

- Scoring rules R1–R5 and assumptions A1–A5 in `scoring.py`; `period_reward` is centi-cents
  (OpenAPI 3.30.0) — to be confirmed against the public incentives page on day one.
- One WebSocket connection can carry ≤150 markets' `orderbook_delta` + `trade`; the raw-row cap
  (3,000/min) bounds the DB regardless.
- Unmatched legs settle 100/0 on `result ∈ {yes, no}`; scalar markets are not settled by the pass.

## Non-Goals

- Placing any order, paper or live; any XOS registration; any change to MMSELL or to the fee /
  fill / taxonomy semantics; a fitted fill-probability model (the three models are explicit
  rules, not fits); a portfolio allocator across programs.

## Acceptance Checks

- Unit tests pin the published scoring sentences (R2 worked example = $16.00, R3 depth walk,
  R4 multipliers, R5/R6 shares) and the gallantfox fee anchors.
- The state machine, driven by scripted frames on SQLite, places 3 × tiers pairs per market,
  fills them under the three models differently, writes marks and per-model outcomes, ends
  pairs only with a named reason, and stamps settlement.
- The client wrapper exposes GET/iter/ws methods only (test-enforced); the collector module
  names no write endpoint and no trading table.
- Migration `d9f1c3a7b2e4` applies (sqlite: columns match the ORM; CI: Postgres), one head.
- Livedash `/incentives` + three read-only routes answer; the ops script is allowlisted and its
  SQL survives the psycopg placeholder parser; the enable/cadence vars are allowlisted.
- Full suite green, ruff clean.

## Build Card

Inline: `docs/LIQUIDITY_INCENTIVE_THESIS.md` §1, §3, §4, §8.

## Implementation State

PR [#415](https://github.com/50thycal/kalshi_bot/pull/415) merged 2026-09-16 19:43Z (merge commit `ccf4f9d`), solo mode. Instrument on the default branch; activation recorded below.

## Review State

**Verdict:** Not started
**Reviewed head:** —
**Reviewed PR:** —
**Finalization:** —

## Related Decisions

`DEC-015`.

## Related PRs

[#415](https://github.com/50thycal/kalshi_bot/pull/415)

## Parked

- Discovery of `upcoming` programs and a "program age" feature on quotes (D2).
- A replay tool over `incentive_book_events` / `incentive_trade_events` for re-scoring under a
  revised scoring version without re-collecting.

## Activation state (2026-09-17) — RUNNING

**Enabled 2026-09-17 12:24:54Z.** D1 is closed: the host is the **evo** Railway service — it
runs the same main loop with a read-only Kalshi client, was alive and idle
(`EVO_ENABLED=false`), and a redeploy there cannot touch the live book. The operator
authorized the mutation in this session; ops `limm-on-1` set
`LIQUIDITY_INCENTIVE_SHADOW_ENABLED=true` (BEFORE unset → AFTER true, **VERDICT: VERIFIED**,
redeploy triggered). Ops channel reset to `noop`.

First read (`limm-report-1`, 12:30:02Z, code `dfe40b9d`): collector alive, 3,939 programs
discovered with 0 errors, 1,155 open shadow pairs over 77 markets, no sequence gaps or
throttles. Two day-one checks pass (the centi-cents reward unit; Target Size 1000 /
Discount Factor 0.50 modes). Two findings are recorded in
[the thesis §9.1](../LIQUIDITY_INCENTIVE_THESIS.md): the reward ranking selects **untraded**
markets (zero trades in two hours), and the top reward estimates imply ~3%/day against a
~0.62%/day board rate, so the share model is unvalidated until checked against Kalshi's own
projected-reward display.

## Next Step

Operator: run day-one check 3 — open one named market from the ranking while signed in to
Kalshi and compare its displayed projected reward against our `est_reward_per_hour`. That is
the only external calibration of the share model, and no `est_` reward figure should be
believed until it runs. Then let the shadow accumulate to the §6 window (≥ 14 days) before any
read of the headline table.

## Phase 1a — the one-sided live smoke test (2026-09-17)

Operator authorization, same day: *"I approve you to make a trade to test that out. Build the
Infra needed to allow the trade to happen... keep the trading below 10 dollars and only 3
trades max at a time per test with a limit of one dollar per trade."* The operator also chose
**one-sided now** over waiting for two-sided, on the trade-off below.

**The honest answer to "does anything limit the test".** Yes, two things, and neither is the
dollar figures — those are fine and already match production (`LIVE_MAX_ORDER_DOLLARS=1.0`,
`MAX_MARKET_EXPOSURE=1.0`).

1. **Two-sided quoting is structurally blocked.** The live path refuses a second resting order
   on a ticker that already carries one, and that gate is strategy-agnostic on purpose — it is
   also what stops this book contesting a market the MMSELL canary is in. Changing it is a
   Platform Change Review. Kalshi scores YES and NO separately, so one bid still earns; the
   test therefore proves the pipe and says nothing about two-sided pairing economics.
2. **One order requires a whole experiment.** Under NEW_ONLY an unregistered tag cannot write,
   and the only sanctioned route to a live tag is `arm_live_canary`, which needs a PAPER-stage
   experiment, a frozen version, a pre-registered risk envelope, a mandatory paper twin on
   fresh tags, and a promotion gate that PASSes on a synchronous re-evaluation. "Just place one
   trade" is not available, and that is the system working.

And one the size itself imposes: at $1 the reward is ~3c/day against the ~$3/day the §6 bar
was written for. **This test proves plumbing. It cannot validate the reward model.**

### What was built

- `liquidity_incentive/live.py` — the decision layer. Caps as module constants, ten refusal
  codes, no relaxation path. Side choice is the safety lever: cheapest touch, rest at it.
- `liquidity_incentive/runner.py` — one cycle: pick programmes soonest-ending first, fetch a
  bounded number of books, rank, place up to the open-order cap, mirror each placement to the
  twin. **No cancel branch**, asserted by a test.
- `LiveExecutor.mirror_incentive_entry` — nine gates, intent committed before the POST.
- `LiveExecutor.manage_exits` now skips this book's tags: its contract is hold-to-settlement,
  and production runs `tp_sl`, which would otherwise place undeclared exit orders on a filled
  YES bid.
- `repository.live_strategy_exposure` — the per-strategy budget, in dollars.
- `experiment_os/metrics.py` — eight `incentive_*` providers over the shadow tables, probe-only
  and experiment-wide, refusing any other addressing.
- `experiment_os/liquidity_incentive_mm.py` — the package: experiment, frozen v1 + envelope,
  three gates, e1, tagless probe deployment, `register` (stops at PROBE) and `arm`.

### Findings recorded, not acted on

- `stamp_or_block` admits a tag on its active-deployment arm without checking the deployment
  **kind**, so a PROBE tag could technically write a live order. Using that would circumvent
  `arm_live_canary`, which is the only sanctioned arming path, so it was not used. **This
  belongs in an Experiment OS issue** and is not fixed here.

## Shadow read, hour 1.5 (2026-09-17 13:49Z)

Ops `limm-report-2`. Collector healthy (0 discovery errors; 4 sequence gaps and 2 disconnects
recorded and recovered). Full read in [thesis §9.2](../LIQUIDITY_INCENTIVE_THESIS.md). Three
things changed since day 0:

1. **First outcomes ended, and P(both | one) = 0.000 at n=15.** Not one pair completed. §6
   criterion 6 requires ≥ 0.25 on the stated ground that below it the pair premise is wrong.
   At a 0.06-day span the correct reading is HOLD, and §6 is not retuned — but this is the
   criterion most likely to decide the thesis and it is pointing one way.
2. **Single-leg adverse selection dwarfs the reward on that sample**: mean −$1.96 mark-to-bid
   against $0.0121 of estimated reward, all behind deep competing size.
3. **Still no trades where the rewards are**: 9 public trades in 3h across 167 markets.

Nothing was acted on. The pre-registered window is ≥ 14 days.

## Arming (2026-09-17) — step 1 done, step 2 refused and fixed

Operator signed off on the live test at ~15:20Z. Sequence so far:

1. **`xos package-preflight liquidity-incentive-mm` → GO** (15:08Z). NEW_ONLY, complete
   snapshot `4f9adf15daa6`, experiment unregistered, no tag collisions, transport idle.
   Its suggested envelope templates `approved_by`/`reason` into a `REGISTER_PACKAGE` payload,
   which that action rejects as unknown fields — sent with `package` alone.
2. **`REGISTER_PACKAGE` (`limm-register-1`) SUCCEEDED** 14:11:04Z. v1 frozen, e1 open, three
   gates registered, risk envelope confirmed in the DB (`smoke_test_stage_1a`, 25c, $10),
   experiment at PROBE. Evidence clock starts at the freeze instant.
3. **Gate evidence at 15:22Z, comfortably over every bar**: 14 discovery cycles (0 errors),
   3,330 shadow quotes, 3,240 ended conservative outcomes, 4,138 programmes.
4. **`ARM_CANARY` (`limm-arm-1`) REJECTED** 15:25:54Z — a defect in THIS package, not a
   transient failure:

   > cannot carry ['limm-shadow-probe-1 (probe)'] across an epoch boundary: only ['paper']
   > deployments may be re-registered automatically.

   `arm_live_canary` closes the operating epoch and carries its open deployments across the
   live boundary (XOS-000011); `carry_deployments_forward` admits `paper` only. The package
   left its tagless shadow probe open. **The engine is right to refuse** — a probe is a
   validation instrument and carrying one into a live epoch would claim the shadow collector
   is part of the live lineage. The rejection was atomic: state still PROBE, one open epoch,
   no live lineage, no exposure.

**Fix:** `arm()` now ends the probe deployment at the PROBE→PAPER transition, which is the
moment its stage ends. Reproduced as a test that fails with the verbatim production error
without the fix. Ending it orphans no evidence — metric scopes resolve over ended deployments,
the `incentive_*` metrics read the shadow tables by time window rather than by tag, and the
collector thread is governed by its own env flag.

A retry needs a NEW `command_id` (`limm-arm-2`): `command_id` is the exactly-once key and
`limm-arm-1` is spent.

## LIVE — the smoke test placed (2026-09-17 16:00:47Z)

Full arming sequence completed on the operator's sign-off. `ARM_CANARY` retry (`limm-arm-2`)
SUCCEEDED 15:52:13Z — `limm-smoke-live-1` (`Alimm1`) + `limm-smoke-twin-1` (`Alimm1_pt3`) at one
boundary, `boundary_match: true`. Activation (`limm-activate-1`) VERIFIED 15:57Z.

**Three post-only YES bids rested on `KXHORMUZPEAK-26SEP20` strikes at 1c, 1c and 3c — $0.05 of
real money, all caps held, twin mirrored within 43ms, zero ticker collisions with any other
book.** Detail and the two concentration observations: [thesis §9.3](../LIQUIDITY_INCENTIVE_THESIS.md).

### Defects found in this package during arming, and how each was handled

1. **Probe deployment blocked arming** (`limm-arm-1` REJECTED). `arm_live_canary` will not
   carry a PROBE deployment across an epoch boundary. Fixed in #422 by ending it at the
   PROBE→PAPER transition — NOT by relaxing `_CARRYABLE_KINDS`, which guards the only path
   that creates live lineage. The rejection was atomic; nothing needed repair.
2. **`activation_env()` would have broken another live book.** It sets
   `LIVE_PAPER_TWIN_SUFFIX` from this book's `TWIN_SUFFIX` (`_pt3`); production carries `_pt4`
   and the variable is SHARED, so applying it would have re-cut `Fmmsell10`'s twin to an
   unregistered tag and taken it dark under NEW_ONLY. **Not applied** — three variables were
   set by hand instead and the twin pinned via `LIVE_PAPER_TWINS`. The root cause is that
   `TWIN_SUFFIX` was copied from the mmsell10 canary's docstring and never re-read against
   production, and the test asserting `TWIN_TAG == LIVE_TAG + TWIN_SUFFIX` is self-consistent
   and therefore proved nothing. **`activation_env()` is still wrong for any future use and
   must be fixed.**

## Shadow read, hour 4.5 (2026-09-17 16:54Z)

Ops `limm-report-3`. Span 0.18 days — still HOLD. Two moves in opposite directions
([thesis §9.4](../LIQUIDITY_INCENTIVE_THESIS.md)):

- **P(both | one) is still 0.000 at n=265** under the optimistic model (was n=25 at §9.2),
  and 0.000 under all three. Not one pair has completed. The direction has now survived a
  tenfold sample increase, though the outcomes are correlated so the effective n is smaller.
- **The headline turned positive** (+$6.86/day at $25, +$225/day at $500) and **100% of it is
  `est_reward`**. Paired P&L is 0.0000 because there are no pairs; fees 0.0000 for the same
  reason; settlement n/a; single-leg MTM negative. Every *measured* component is zero or
  negative. Day-one check 3 remains the only external test of that model and is unrun.

Collector healthy (0 discovery errors, 4,250 programmes) but 17 sequence gaps and 3
disconnects, all recovered. Trades 47/3h across 177 markets — still almost nothing.

## Shadow read, hour 7.5 (2026-09-17 19:58Z)

Ops `limm-report-4`, span 0.31 days. **P(both | one) moved off zero — but only in the model
that does not count** ([thesis §9.5](../LIQUIDITY_INCENTIVE_THESIS.md)):

- **optimistic: 0.057** (30 pairs / n=525), legs a **median 17 minutes apart**
- **conservative: still 0.000** (n=45) — and conservative is what §6 gates on
- **queue_aware: still 0.000** (n=75)

The lag matters more than the rate: a second leg filling ~17 minutes after the first is not a
market-making pair, it is two independent fills with a long one-sided interval between them.
§2's mechanism assumed the pair bounds the risk; at that lag it does not.

Headline still climbing (+$279/day at $500) and still 100% `est_reward`. Collector clean on
discovery (0 errors, 4,331 programmes) but **sequence gaps are rising: 4 → 17 → 28**, all
recovered. Watch it; the tape is what the fill models replay.

## Live lifecycle closed (2026-09-17 20:08Z)

All three original bids timeout-cancelled at the 4-hour boundary (`cancel_reason=timeout`) and
the runner re-quoted four minutes later on a different market set — $0.06, 3 open at the cap,
all under 25c, no collisions. **Place → rest → expire → re-place demonstrated end to end.**
The event-concentration pattern repeated (2 of 3 on one event); second observation, still not
acted on, but a sized-up version needs an event cap. [Thesis §9.6](../LIQUIDITY_INCENTIVE_THESIS.md).

## Shadow read, hour 12 (2026-09-18 00:00Z)

Ops `limm-report-5`, span 0.48 days. Two of the material triggers fired
([thesis §9.7](../LIQUIDITY_INCENTIVE_THESIS.md)):

- **The queue-aware model recorded 10 `partial_both` outcomes** — the first two-sided filling
  outside the optimistic model. `P(both | one)` counts only FULL pairs, so it still reads
  0.000 and is now known to be **incomplete**. The metric is NOT being changed: §6 is
  pre-registered and this is precisely the moment that rule exists for. Recorded so no reader
  takes the 0.000 at face value.
- **Queue-aware lag is 16.7s** (vs the optimistic model's 435s median) — in the range that
  would actually bound one-sided exposure. First evidence the §2 mechanism is mechanically
  possible. But 10 observations, no full pairs, 0.48 days. A direction, not a finding.

Sequence-gap rise has **plateaued** (4 → 17 → 28 → 28). Discovery clean, 4,449 programmes,
trades 98/3h and rising but still thin.

## FIRST FILLS, and a twin defect they exposed (2026-09-18 00:26Z)

Two post-only 1c YES bids on `KXBIGGESTQUAKE-17SEP26` strikes **filled** — we were the maker,
a counterparty sold into us, 2c at risk. Kalshi position snapshots confirm both. The pipe is
now proven end to end: select, place, rest, fill, hold, expire.
[Thesis §9.8](../LIQUIDITY_INCENTIVE_THESIS.md).

**Defect found and fixed:** `abandon_open_paper_trades` runs on every live worker start and
keeps paper trades by FAMILY PREFIX. A twin tag carries its parent's generation letter
(`Alimm1_pt3`) and matches no family, so the twin's mirrors of the two filled positions were
marked `abandoned` while live still held them — breaking the comparison instrument
`arm_live_canary` requires. mmsell's twins survive only on the `"mmsell"` substring accident,
so every other book's twin was exposed. Fixed by keeping configured twin tags by exact tag
(`repository.keep_with_configured_twins`). This is the Wmmsell6 failure of 2026-08-04 on a
new book.

## THE CANARY STOPPED QUOTING — universe starvation (2026-09-18 05:04Z)

**CORRECTED — see the next section. The claim that this is permanent is false.**

No order since 00:08:31Z. Nothing is failing: the worker cycles every ~2.5 min, `Fmmsell10`
places normally, caps hold, no rejects, no auth errors. The book is **starved**.

Two rules, each correct alone, compose into a filter that admits nothing:
`_candidate_programs` ranks **soonest-ending first** and fetches only the first 8 books (so the
payout leg can be observed as early as possible); `build_live_quote` refuses below
`MIN_PROGRAM_HOURS_REMAINING = 2.0` (a reward cannot be earned on a programme about to end).
Kalshi now runs a continuous class of **15-minute** liquidity programmes, so the 8 soonest-
ending programmes are always ≤0.2h from ending and always refused. Deterministic, not
intermittent — the other ~3,913 current programmes are unreachable.

**Not fixed here.** The fix changes the book's **universe** on a live, armed arm; that is an
Experiment OS epoch decision, not a patch. OWNER DECISION.
[Thesis §9.9](../LIQUIDITY_INCENTIVE_THESIS.md).

**Fixed here (read path only):** the diagnosis needed a database query and a code read because
the logs could not say it. The runner already computes a per-refusal-code breakdown so that a
cycle placing nothing reports what stopped it, and `scripts/railway_logs.py` rendered only
`exc`. `DETAIL_KEYS` now carries `considered`, `fetched`, `placed`, `outcomes`, and no longer
drops a zero.

Exposure unchanged: 2c in the two unsettled `KXBIGGESTQUAKE-17SEP26` contracts. A book that
quotes nothing takes no new risk — the cost is evidence, not money.

## CORRECTION: the starvation is intermittent, and the book is at its cap (2026-09-18 08:04Z)

The `DETAIL_KEYS` fix from the section above falsified that section within ten minutes of
reaching production. The first cycle line to print its own counters read
`considered=0 fetched=0 placed=0 outcomes={"no_slots":1}` — the cycle never reached candidate
selection; it returned at the open-order cap.

**The book quoted again at 06:00:04Z** on `KXUSLEI-26SEP18-T0.2` at 5c and **filled**, roughly
two hours after the previous section declared it never would. Three filled, unsettled positions
now count as open, which is exactly `MAX_OPEN_ORDERS = 3`. The cap is holding it quiet — design,
not defect.

The composition of the two rules is real and can starve the book for hours (the 00:08Z→06:00Z
gap), but the count of concurrent sub-2-hour programmes varies, so whenever fewer than eight are
live the window admits day-scale ones and the book recovers on its own. **Intermittent, not
deterministic.**

The universe question stays open and is now a throughput question, not a liveness one; it should
be decided on the `outcomes` measurement the logs now carry.
[Thesis §9.10](../LIQUIDITY_INCENTIVE_THESIS.md).

Exposure 7c across three unsettled contracts (1c + 1c + 5c), against a $10 strategy cap and a
3-order cap. Both hold.

## SHADOW: the 16.7s lag was n=1 (2026-09-18 08:08Z)

Two pre-registered materiality criteria fired at the 08:08Z health check, span 0.82 days.

**`partial_both` now appears under CONSERVATIVE too** (0 → 10), and queue_aware doubled (10 →
20). Like-for-like: total span is under a day, so the report's 1d→14d window change covers the
same data. This also settles the staleness worry from 04:03Z — the rows were not stale.

**The queue-aware lag moved from 16.7s to 1102.8s.** Mean equals median in both non-optimistic
models, so each rests on a single observation. §9.7 read the 16.7s as a lag "that would bound
risk"; that was **n=1**, and the next one is 18 minutes. The lag is **not yet estimable** and
§9.7's favourable gloss must not be carried forward. §9.7 is stamped, not rewritten.

Unmoved: `P(both | one)` still 0.000 under both non-optimistic models — the metric counts only
full pairs and now hides thirty partial ones. **Not changing it**; §6 is pre-registered.
Conservative single-leg MTM against deep size is −$1.3802 (n=340) against a mean modelled reward
of +$0.0692, a factor of twenty the wrong way. The positive headline is still entirely derived
from an unvalidated `est_reward`.

Live book unchanged and correct: `LIVE_STRATEGIES=Fmmsell10,Alimm1`, twin pinned, kill switch
off, exposure 7c at the 3-order cap.
[Thesis §9.11](../LIQUIDITY_INCENTIVE_THESIS.md).

## FIRST SETTLEMENT — a full loss of premium (2026-09-18 15:47Z)

`KXUSLEI-26SEP18-T0.2` settled **NO** at 14:47:14Z. The YES contract we held at 5c expired
worthless: **realized −$0.0500**, the maximum loss on the position. Entry fee $0.0000 — we were
the maker.

**The lifecycle is now proven end to end with every leg observed:** select → place → rest →
fill → hold → **settle**.

**§9.10's prediction held.** The settlement freed a slot at 14:47:14Z and the next cycle placed
at **14:48:49Z**, 95 seconds later, on `KXTRUMPAPPROVE-26SEP18-E39.4` at 1c — which filled. The
book was full, not starved, and resumed the instant a slot opened.

**The loss carries no information about the premise.** A 5c YES is a market-implied ~5% event;
losing the premium is the modal outcome. n=1.

What it does make concrete: realized **−$0.05**, open 3c, $0.08 ever committed across four
filled contracts, and **no liquidity reward credited yet**. The adverse-selection leg is paying
out in real money; the reward leg the thesis depends on has produced nothing observable. Too
early to be a finding — Kalshi credits after a programme ends — but `est_reward` is now an
unvalidated assumption being paid against.

`live_canary_keep` needs three settled contracts and is still unreadable. No gate evaluated,
nothing authorized. [Thesis §9.12](../LIQUIDITY_INCENTIVE_THESIS.md).

## THE PAYOUT LEG IS UNOBSERVABLE (2026-09-18 15:55Z)

The scheduled payout-boundary check cannot be run. Three independent reasons, none of them "no
reward was paid":

1. **We stop looking before a programme can pay.** Discovery polls `status="active"`, so a row
   freezes at its last active observation. All six programmes the live book rested in read
   `paid_out = false`, and every one was last seen 1–4 minutes BEFORE its own end.
2. **`paid_out` does not mean what we assumed.** It is real (23 of 7,979 rows; 12 of 4,112 live)
   but is true on programmes that have NOT ended — `KXVOTECLARITY` ends 20 Sep, `KXFEAR` ends
   20:00Z today. So it is not an end-of-programme distribution signal.
3. **The shadow instrument never covered a market the live book traded.** Zero
   `incentive_shadow_outcomes` rows for all nine live tickers. No `est_reward` for them either.

Both sides of estimate-versus-realized are missing for the live book. NOT evidence against the
reward model — evidence the instrument cannot see the payout leg. A null would have been
uninformative anyway: the runner steers away from big-pool programmes, and 1 contract in a
27k–60k book is ~0.5% share.

**OWNER DECISION, not patched:** poll ended programmes so the terminal state is captured;
establish `paid_out` semantics; make the shadow cover the live book's markets.
[Thesis §9.13](../LIQUIDITY_INCENTIVE_THESIS.md).

## P(both | one) LEAVES ZERO — and the headline goes negative (2026-09-18 16:18Z)

Span 1.16 days. Three materiality criteria fired at once, pointing opposite ways.

**FULL `both_filled` appeared under BOTH non-optimistic models** — 4 each. P(both | one) is no
longer 0.000: conservative **0.007** (n=555), queue_aware **0.004** (n=1005). The pre-registered
§6 metric has moved off zero for the first time since §9.2. `partial_both` also grew, 10→16 and
20→36.

**The queue-aware lag is now estimable**: mean 828.0s vs median 553.2s (diverged, so n>1). Nine
to fourteen minutes, NOT 17 seconds. §9.7's reading is conclusively dead.

**The headline flipped hard negative in the same reading.** `A_break_even` is negative at every
tier and model; at $500 conservative, net went +222.77 → **−150.53**. The driver is single-leg
MTM, not reward: reward grew 1.6× since 08:08Z while single-leg MTM grew **10×** (−61.63 →
−613.53). Deep-depth single-leg marks are −$4.98 (n=506) against +$0.10 mean est_reward — fifty
times the wrong way, up from twenty.

**Exactly one of eighteen cells is still positive:** `C_conservative` under the conservative fill
model. The two policies have separated clearly for the first time.

**Two explanations not excluded:** the tail is heavy (worst@bid −$313.10 vs mean −$5.08), and the
collector restarted twice today with a new `throttled` event type appearing. Four hours is short
for a 10× move in one component.

No gate re-interpretation. [Thesis §9.14](../LIQUIDITY_INCENTIVE_THESIS.md).

## SECOND SETTLEMENT + TWO SAFEGUARD DEFECTS (2026-09-18 18:23Z)

`KXTRUMPAPPROVE-26SEP18-E39.4` settled at 17:32:14Z, realized **−$0.0100** — another full loss of
premium, again the modal outcome, again uninformative at this n. Running realized **−$0.0600**
over two settled contracts.

**Defect 1: no event-level cap on this book.** `Fmmsell10` holds `KXRT-RES-97` NO at 93c
(**$0.93** at risk); `Alimm1` is now resting `KXRT-RES-93` at 3c and `KXRT-RES-94` at 10c — same
event `KXRT-RES`, same direction. `LIVE_ONE_POSITION_PER_EVENT=true` and
`repository.event_has_open_live_position` exist for this, and **the incentive package never calls
it**; `build_live_quote` has no event cap among its refusal codes. This is the concentration cap
§9.8 parked — no longer hypothetical.

**Defect 2: the open-order cap under-counts.** `count_live_book_open` skips a ticker whose latest
snapshot is flat. `KXRT-RES-93` has a **quantity-0** snapshot while its order is still **resting**,
so a live resting order is invisible. Real commitments: 4 (2 filled + 2 resting) against
`MAX_OPEN_ORDERS = 3`.

Also new: first NO-side quotes ever, and 10c is the highest price this book has placed.

Committed $0.15 against a $10 cap; exposure, qty and price caps all hold. **Recorded, not
patched** — both fixes change a live armed arm's caps. OWNER DECISION.
[Thesis §9.15](../LIQUIDITY_INCENTIVE_THESIS.md).

## FOUR FIXES SHIPPED (2026-09-18 19:10Z)

Operator-authorised. All four tighten a bound or add an observation; none relaxes anything.

1. **Event cap** — `build_live_quote` gains `REFUSE_EVENT_CAP`, refusing a candidate whose event
   is already held by this book OR by any other live book (via the fleet's existing
   `event_has_open_live_position`). A placement blocks its own event for the rest of the cycle.
2. **Open-order cap** — counting moved to `repository._open_live_tickers`, which checks ORDER
   STATUS BEFORE the position snapshot. A snapshot cannot tell "position closed" from "order not
   filled yet"; both read 0. Strictly tighter, and shared with MMSELL.
3. **Terminal listing** — discovery also polls `status="paid_out"`, deduplicated so a programme in
   both listings records terminal. Failure-tolerant: it never costs the active listing.
4. **Shadow pinning** — the live book's open markets survive the shadow's reward-ranked cap, so
   estimate and realized can finally land on the same market.

**On the API:** programme-level payout state IS visible (`status=paid_out` is a first-class
filter) and always was. Our own credited amount does not appear to have an endpoint; the route to
it is the residual of a balance change against fills and settlements. Not built — not one of the
four.

`ruff` clean; **4,548 passed, 11 skipped**; nine new tests. The universe rule stays open.
[Thesis §9.16](../LIQUIDITY_INCENTIVE_THESIS.md).

## DAY-ONE CHECK 3 DONE — units exact, lifetime rewards $0 (2026-09-18 19:19Z)

The operator found the incentives page.

**`period_reward_usd` is EXACT.** The page sums the pool per event; three independent events match
to the cent and the timestamps to the second: `KXFEATURE` 129 × $100 = **$12,900**;
`KXMLBPLAYOFFS` 18 × $500 = **$9,000**; `KXWAAEROEMP` 13 × $500 = **$6,500**. The centi-cent unit
assumption is now a measurement.

**Lifetime rewards: $0.** Consistent with §9.13 — 1 contract in a 27k–60k book is ~0.5% of a
per-period slice, fractions of a cent, which rounds to zero. It does not refute the mechanism; it
confirms the smoke test was too small to measure one. It is still the only external reading of
realized reward we have, and it is zero.

**Neither validates the headline.** The page confirms the POOL, one input. The positive total is
pool × our modelled SHARE × scoring, and the share model is untouched.

**A dimension we cannot see:** the page's Category column (Low/Medium/High) is not in the API
(`extra_params_json` is empty for every current programme) and is not derivable — the same three
events share `target_size=1000` and `discount_factor_bps=5000` yet read Low/High/Medium. The best
target on that page is Low category with the largest pool, `KXFEATURE` at $12,900, which our
soonest-end runner has never looked at. The universe question now has a price tag.

[Thesis §9.17](../LIQUIDITY_INCENTIVE_THESIS.md).

## THE COLLECTOR GOT WORSE AND THE HEADLINE GOT BETTER (2026-09-18 20:21Z)

Span 1.33 days. Criterion (d) fired, next to a large favourable headline move. The pair is the
finding.

**Collector instability accelerating:** `seq_gap` 127 → 137 (+10) → **169 (+32)** across the last
two checks — the rate roughly TRIPLED. `throttled` 3 → 4. Three reconnects and a thread restart
**with no deployment since 12:46Z**, so the collector is genuinely dropping. Discovery itself is
clean (395 cycles, 0 errors).

**Headline improved sharply in the same window:** A_break_even/$500 conservative −150.53 →
**−69.10**; the mechanism inverted, with reward +20% against single-leg MTM +1.8% (four hours
earlier the ratio was the other way by a factor of six).

**Not read as economic news.** §9.14 already named collector instability as an unexcluded
confound; it has since got worse. A tape with more holes yields fewer and differently-marked
single-leg outcomes — the direction observed. Both recent headline readings are suspect. The
confound cuts both ways, not only against the premise.

**Everything else flat, which is itself the argument:** `both_filled` 4/4 unchanged,
`partial_both` 16/36 unchanged, conservative lag still mean=median=1212.5s (n=1). The
P(both | one) drift (0.007→0.006, 0.004→0.003) is n growing, not numerators moving.

**New:** the shadow settled its first pair (`settled 1`); the `settle` column now carries −$0.75
at $25 and −$15.15 at $500. n=1, worth nothing yet.

Live config re-verified unchanged. [Thesis §9.18](../LIQUIDITY_INCENTIVE_THESIS.md).

## BOTH KXRT-RES ORDERS FILLED (2026-09-18 20:27Z)

§9.15's two resting NO orders have both filled. The fleet now holds THREE filled NO positions on
one event across two books: `Fmmsell10` `KXRT-RES-97` at 93c ($0.93), `Alimm1` `KXRT-RES-94` at
10c ($0.10) and `KXRT-RES-93` at 3c ($0.03) — **$1.06** on one event, all the same direction,
resolving together. That is ~7x the incentive book's entire committed capital.

The open-order under-count did not merely let a fourth order rest; it let a fourth position FILL.
`Alimm1` holds four filled commitments against `MAX_OPEN_ORDERS = 3`.

Nothing else breached: committed $0.15 vs a $10 cap, qty=1, max price 10c vs a 25c cap, no
rejects, no new settlement, no reward. Commitments did not exceed four.

**#428 prevents recurrence but does not unwind this** — the event cap refuses new placements on a
held event; these positions stay until resolution. Factual update to §9.15, not a new finding.
[Thesis §9.19](../LIQUIDITY_INCENTIVE_THESIS.md).

## FILL MODELS SEPARATE (2026-09-19 00:25Z)

Criterion (c) fired, but only on one model. Over four hours queue-aware added **2** `both_filled`
(4→6) and **8** `partial_both` (36→44); conservative added **none** (still 4 / 16) while its n
grew 626→866. Same tape, different queue crediting — model choice is now load-bearing in the
headline, not a rounding difference.

**Cannot be read as "two-sided fills are achievable."** Queue-aware is the model most sensitive
to tape completeness and conservative the least; a missed cancel inflates exactly queue-aware.
The tape carries 200 `seq_gap` events. The asymmetry matches that failure mode precisely, so the
separation is recorded and its cause left unresolved.

Under the gated metric (conservative) the numerator has been frozen for three checks while n
grows, so P(both | one) keeps drifting toward zero by arithmetic: 0.007 → 0.006 → 0.005.

**Collector plateaued, not recovered:** `seq_gap` +31/4h against +32 previously (neither
criterion a nor b), `throttled` 4→5, two reconnects, no thread restart. Discovery clean (446
cycles, 0 errors).

**Headline reversed and is not news:** `A_break_even`/$500 conservative −150.53 → −69.10 →
−158.34/day. Recorded only so §9.18's improvement is not later read as a trend.

Carried, not acted on: the competing-depth split gained a **medium** bucket — single-leg MTM
−$0.9262 with reward +$0.3359 (n=45) against −$4.7731 / +$0.0952 deep (n=817). Points at
selection, but n=45.

Live config re-verified unchanged. #428 still unmerged, so this ran on base code and is a clean
pre-deploy `seq_gap` baseline for fix 4. [Thesis §9.20](../LIQUIDITY_INCENTIVE_THESIS.md).

## THE COLUMN IS COMPETITION, NOT CATEGORY (2026-09-19 01:30Z)

The operator's screenshots show the table header: `End | Program | Competition | ↓ Reward`.
§9.17 called that column "Category" and reasoned about the wrong field for a whole entry.
`Category` is a separate subject filter (Economics, Financials, Crypto, Politics, Climate and
Weather, Entertainment, Science and Technology, Sports, Mentions).

**Not a naming quibble.** §9.17 concluded the page "confirms the pool" and left "the share model
untouched". Wrong: Competition is Kalshi's own published read on the DENOMINATOR of the share
term — the one input the thesis calls structurally unobservable. It has been on the page all
along. Competition = crowding is the plain reading of the word, not a definition we hold.

**Non-derivability survives and is stronger.** A census of every API key across all 5,332 current
programme rows returns exactly eleven, none of them competition. `discount_factor_bps` is 5000 on
every row; `target_size_fp` takes two values. Neither separates the three matched events.

**Sharper cost to the universe rule:** `KXFEATURE` is $12,900 at LOW competition — biggest pool,
lowest crowding, the best cell on the board — and the soonest-end rule means this book has never
looked at it. Strongest argument yet for revisiting the rule. Still an OWNER DECISION.

**Pre-registered before running:** if Competition means crowding, programmes marked High must
carry larger competing depth at placement in our own tape than those marked Low. One query, data
we already hold, written down before looking.

**New open question:** the page has a Predictions / Perps toggle, so Perps incentive programmes
exist as a separate universe. `PERPS_COLLECTOR_ENABLED=false` and we poll `/incentive_programs`,
so whether our listing covers them is unknown. The Rewards filter (All / Volume / Liquidity) maps
to `incentive_type` and IS covered.

Lifetime rewards still $0. [Thesis §9.21](../LIQUIDITY_INCENTIVE_THESIS.md).

## DEPLOYED, AND THREE FIXES VERIFIED IN PRODUCTION (2026-09-19 02:36Z)

#428 merged 01:53:07Z. Runner reports code `95f66108`, no longer the `5ea57bd3` everything up to
§9.21 ran on.

- **Fix 4 (shadow pinning) WORKING** — three consecutive discovery cycles carry `pinned_live: 4`,
  exactly Alimm1's four filled commitments. §9.13's blindness closed at the mechanism.
- **Fix 3 (terminal listing) WORKING, cost under-stated** — `status_observed` now splits 29,985
  `paid_out` against 10,197 `active`. But the PR called it "one paged API call per cycle"; the
  terminal listing is ~6x the active one and `listed` per cycle went 4,908 -> 35,305, about
  seven-fold. Still 0 errors, still on schedule, so working rather than struggling — recorded
  rather than absorbed.
- **Fix 2 (open-order cap) working by inference** — no new Alimm1 order since 18:21:03Z, which is
  what a correct count must produce at four commitments against a cap of three. Absence of an
  action, so weaker evidence than 3 and 4.
- **Fix 1 (event cap) UNVERIFIED live** — only observable when the book places, and it cannot
  place while over the open-order cap. Recorded as untested, not as working.

Unchanged and safe: five filled positions, snapshots fresh 02:33:59Z, all realized_pnl 0.0000,
committed $0.15 vs a $10 cap, no rejects, no auth errors, no reward credited. Ledger still
-$0.0600.

Collector deliberately NOT judged here — forty minutes is not an interval. The 04:30Z shadow
check owns it, entry criterion pre-set above +35/4h against §9.20's pre-deploy baseline of
seq_gap 200 at ~+31/4h. [Thesis §9.22](../LIQUIDITY_INCENTIVE_THESIS.md).

## REWARD LEDGER BUILT, AND THE UNIVERSE RULE IS THE BINDING CONSTRAINT (2026-09-19 04:30Z)

**Operator-authorised.** Two things.

**1. The reward is now measurable.** Kalshi has no endpoint for our credit (§9.21), so it is
recovered as the part of a balance change nothing else explains:
`residual = Dbalance - settlements - sell proceeds + buy cost + fees`. New module
`reward_ledger.py`, new append-only table `incentive_balance_observations`, collector takes a
reading every 15 min, ops script `incentive_reward_ledger_report` reads it.

A residual is a CANDIDATE, never a reward. A deposit is the worst false positive, so >= $1.00 is
marked `presumed_transfer` (two orders of magnitude above what this book could earn). An
unreported fee pushes the residual NEGATIVE. A failed or truncated read marks the window
`residual_untrustworthy`. Integer cents throughout — the signal is the size of a float rounding
error.

**2. §9.13's "too small to earn" was reasoned from the deep books, which are not
representative.** Competing depth at the best bid spans FOUR ORDERS OF MAGNITUDE across active
programmes inside the 25c cap: 7 contracts on `KXBWAYATTENDANCE-27MAY23B-14000000` ($497/day
pool) against 2,563 on `KXNYSECEEMP-27APR30-T230400` (same pool).

The dollar figures from the naive share model are NOT quoted as expectations — that model is the
unvalidated term, and it ignores `target_size` (1000 vs our 1), the distance discount and
time-weighting. What survives regardless is the ORDERING: any monotone share function ranks a
7-deep book far above a 2,563-deep one.

**So the universe rule is the binding constraint.** The runner sorts by soonest programme end and
fetches 8 books out of ~5,300. Nothing in that ordering looks at depth, so the book has been
quoting where rewards are unwinnable. Every market in the top rows was invisible to it.

**Pre-registered before the ledger has recorded anything:** if the book is moved onto thin books
and still records no material residual after a quoted programme ends, that is evidence against
the share model at any size — not merely that the book is small.

No cap, gate or risk envelope touched; the ledger only reads the balance.
[Thesis §9.23](../LIQUIDITY_INCENTIVE_THESIS.md).

## GATED METRIC MOVED, BUT THE POPULATION MOVED TOO (2026-09-19 04:30Z)

First shadow reading after the 01:53:07Z deploy. Criteria (c) and (d) both fired.

**(c)** Conservative `both_filled` 4 -> 24, `partial_both` 16 -> 46, n 866 -> 1329. P(both|one)
TRIPLED, 0.005 -> 0.018, and this time by numerator rather than arithmetic. The two
non-optimistic models re-converged (0.018 vs 0.019) after §9.20 recorded them separating.

**(d)** The conservative lag is estimable at last: mean 1048.4s vs median 993.6s, diverged,
where four consecutive checks read mean = median = 1212.5s at n=1. About seventeen minutes.

**Why it cannot be banked.** Fix 4 pins the live book's markets into the shadow, and the live
book picks by SOONEST PROGRAMME END — short-dated, plausibly thinner, more likely to fill both
sides. The shadow is measuring a different mixture than when 4/16 was recorded, and the change
landed 2.6h before this reading. Only 4 markets pinned against ~201 tracked argues the effect is
small, but 4 fill-prone markets could carry a six-fold jump in a numerator that was 4.
UNRESOLVED. §9.20's "frozen" and this "tripled" are not comparable readings. The next check is
the first like-for-like one.

**(a)/(b) Fix 4 did NOT cost tape quality — the pre-registered question, cleanly answered.**
seq_gap rate +27/4h against a +31/4h pre-deploy baseline, criterion was "clearly above +35";
throttled flat at 5. The added subscriptions did not degrade the tape. The one unconfounded
finding here.

**New and unexplained:** `thread_started` 12 -> 18 and `thread_stopped` appears for the first
time (1). A deploy accounts for one restart, not six. Discovery clean (493 cycles, 0 errors).
Open observation, not a diagnosis.

Headline essentially flat (-158.34 -> -156.24/day) and not news.
[Thesis §9.24](../LIQUIDITY_INCENTIVE_THESIS.md).

## THE FILL RATE HELD, AND THE DECOMPOSITION SPLITS TWO WAYS (2026-09-19 08:35Z)

First reading taken ENTIRELY post-deploy — what §9.24 said was needed. Criterion (a) fired.

**It held.** P(both|one) conservative 0.005 -> 0.018 -> **0.018**; queue_aware 0.019. Over the
clean 04:30-08:35Z window conservative added 4 both_filled on 189 new observations, an
incremental rate of ~0.021 — consistent with 0.018, inconsistent with 0.005. Not a one-off burst.

**So the decomposition was run.** Conservative outcomes split by whether the live book holds the
market:

| group | markets | both_filled | n | P(both\|one) |
|---|---|---|---|---|
| pinned (live book) | 2 | 10 | 168 | **0.0595** |
| rest of shadow | 186 | 18 | 1,350 | **0.0133** |

§9.24's suspicion was RIGHT in direction and bigger than expected: the live book's markets fill
both sides **4.5x** as often.

**But composition does NOT explain the aggregate.** Remove the pinned markets entirely and the
rest still reads 0.0133 — ~2.9x the pre-deploy 0.0046, on 186 markets tracked throughout.
Incrementally: of +24 both_filled since 00:25Z, at most 10 are pinned, leaving >=14 on +484 new
non-pinned observations, a rate near 0.029. TWO effects, one explained.

**Do NOT generalise the pinned finding.** Those 2 markets are KXRT-RES-93 and -94 — two markets
of ONE event. Not a sample of "short-dated markets fill better"; one event, observed twice.

**§9.24's thread churn RESOLVES:** thread_started 18 -> 18, thread_stopped 1 -> 1, no new
restarts in four hours. Deploy-adjacent, did not continue. Closed.

Collector holding: seq_gap rate +31/4h, below the +35 criterion; throttled flat; 546 discovery
cycles, 0 errors. Headline improved sharply (-156.24 -> -96.02/day) and is still not a result.
settled still 1. [Thesis §9.25](../LIQUIDITY_INCENTIVE_THESIS.md).

## Next Step (Phase 1a)

Operator: the four-step arming sequence in [thesis §10.6](../LIQUIDITY_INCENTIVE_THESIS.md).
Step 1 (`REGISTER_PACKAGE`) arms nothing and can go now; steps 2 and 4 are hard stops. Note
that step 4 must name **both** `Alimm1` and the running `Fmmsell10` — `LIVE_STRATEGIES` matches
by prefix and replacing it would stand the MMSELL canary down.

## Update 2026-09-19 17:10Z — the reward ledger was measuring nothing

The ledger shipped in #435 wrote **zero rows**: it was wired into the shadow collector, which is
handed `IncentiveReadOnlyKalshi` — market-data GETs only, by design — and so failed on every
tick with `AttributeError: ... has no attribute 'get_balance'`.

Fixed by moving the ledger to `IncentiveLiveRunner` (which already holds the authenticated
client), **not** by widening the read-only wrapper. It runs before the armed gate, because a
programme's reward is credited after the programme ends and can land after this book is stood
down. A regression test asserts the wrapper still has no portfolio methods.

Same PR also fixes the ops report script, which imported SQLAlchemy into a runner that installs
only `psycopg[binary]`. Shared root cause: the measurement was written against the application's
environment rather than the one it runs in.

No gate, lifecycle state or exposure changes. Lifetime rewards still $0.
[Thesis §9.26](../LIQUIDITY_INCENTIVE_THESIS.md). PR #436.

## Update 2026-09-19 17:25Z — health check: the universe stepped 6.6x

Discovery reads 35,198 liquidity programmes against the 5,332 the §9.21 census enumerated two
days ago; total pool $1.97M; median $/day 35.71 -> 150.16; median hours remaining 33 -> 16. The
opportunity set is materially different from the one the thesis was pre-registered against.
Strengthens the case for the authorised universe-rule change.

First positive net/day appears (C_conservative, conservative model: +6.15 / +63.09 / +167.90 at
$100 / $250 / $500) and is a **model artifact** — the reward term is the unvalidated share
model, actual lifetime rewards are $0. **The gated read (A_break_even, conservative) is
unchanged in sign: -17.16/day at tier 25.** No criterion met.

Collector holding: 297 discovery cycles, 0 errors; loop_error 3 = the §9.26 ledger failures,
fixed and pending merge; market_cap_reached 297, so fix 1 still binding not verified; settled
still 1. P(both|one) conservative 0.018 over 14d.

[Thesis §9.27](../LIQUIDITY_INCENTIVE_THESIS.md).

## Update 2026-09-19 17:55Z — universe rule: refuse books deeper than 3x Target Size

Operator-authorised. `build_live_quote` gains `REFUSE_BOOK_TOO_DEEP` — a market whose THINNER
side rests more than `MAX_COMPETING_DEPTH_TARGET_MULTIPLE = 3.0` x Target Size is refused — and
`rank_candidates` sorts on competing depth after collateral, ahead of programme end.

Rationale (§9.27, bucketed at placement, conservative): `medium` books ran mean single-leg MTM
-0.76 with mean est reward 0.28; `deep` books -3.97 with 0.13. Thinner dominates on both axes.
3.0 is the report's own pre-existing medium/deep boundary, NOT a fitted threshold.

Pre-registered: `book_too_deep` should become a common refusal. If it refuses nearly everything
and the book stops placing, that is a FINDING (the universe offers no book thin enough for a
1-contract order to matter), not a bug.

Risk caps untouched; the XOS envelope test still passes. The rule only ever refuses, so exposure
strictly narrows.

**Epoch guard:** rules change to a live armed arm = a new XOS epoch, recorded by the owning role.
The merge does not record it, and evidence does not pool across it.

[Thesis §9.28](../LIQUIDITY_INCENTIVE_THESIS.md).

## Update 2026-09-19 18:20Z — the ledger records, and its first live window was wrong

#436 merged 17:07Z; the ledger anchored at 17:11Z and has written a row every ~15 min since.

Its first window containing live fills read -184c and labelled it `presumed deposit/withdrawal`.
No deposit happened: the window holds exactly two Fmmsell10 NO buys at 90c and 94c = 184c, and
the row's own `fills_counted=2` with `buy_cost_cents=0` shows the ledger saw them and valued
them at nothing.

Root cause: `_fill_cost_cents` read `no_price`/`count`; the live feed ships `no_price_dollars`
(dollar string), `count_fp` (fixed-point string) and `fee_cost` (dollars) — the shapes
`LiveExecutor.reconcile` has read since its shape probe. Third time this session I wrote against
an assumed payload rather than the repo's existing parser.

Worse than a wrong number: crossing `EXTERNAL_TRANSFER_CENTS` meant the ledger *explained* it
confidently. The safeguard against a false positive concealed a defect.

Fixed, both shapes accepted across fills/fees/settlements/balance, 10 regression tests including
the exact window reconciling to zero. Full suite 4,634 passing.

Still NO observable liquidity reward; lifetime rewards $0. A +$1.00 settlement landed at
18:03:42Z but KXRT-RES and KXBIGGESTQUAKE were all still open at 18:05:26Z, so it is not the
resolution the standing check awaits.

[Thesis §9.29](../LIQUIDITY_INCENTIVE_THESIS.md). Rides PR #439.

## Update 2026-09-19 19:30Z — the universe epoch cannot be recorded without re-arming

Attempted the §9.28 epoch cut. `carry_deployments_forward` refuses: `_CARRYABLE_KINDS` is paper
only, and live lineage may be created ONLY through `arm_live_canary`. Not a gap to route around
— it is the guard against an agent manufacturing live deployment rows.

So recording this epoch costs what `recut_mmsell10_contest_cap` cost: fresh tags
(`Blimm1`/`Blimm1_pt3`), re-registration through the sanctioned path, and a `LIVE_STRATEGIES`
change — i.e. standing the canary down and re-arming it. A DEC-012 hard stop, on a $0.15 book.

And the seam is currently EMPTY: `Alimm1` has placed nothing since 2026-09-18T18:21:03Z (four
commitments vs MAX_OPEN_ORDERS=3), so there is no post-rule live evidence to separate.

Speculative package + tests were written, refused by the guard, and REVERTED. Nothing landed.

**Interim boundary, in lieu of an XOS object:** live evidence under `Alimm1` from
2026-09-19T18:21:57Z onward is post-universe-rule and must not be pooled with anything earlier.

OPERATOR DECISION: re-arm on fresh tags to get a formal epoch, or hold and rely on the recorded
boundary until the book is actually placing again. Recommendation: hold.

OPEN QUESTION (candidate XOS issue): no command action records a universe/rules boundary on a
live arm. Platform revisions get `platform_impact`; book-level rule changes get nothing.

[Thesis §9.30](../LIQUIDITY_INCENTIVE_THESIS.md).

## Update 2026-09-19 20:45Z — second ledger defect: `action` is YES-denominated

§9.29's key-name fix works (prices parse), and exposed a second defect underneath it. Three
post-deploy windows read -186/-188/-186 and each was labelled `presumed deposit/withdrawal`:
the balance FELL by the price while the ledger booked PROCEEDS of the same amount, doubling the
error into the residual.

Ground truth from `fills.raw_fill_json`: `{"side": "no", "action": "sell", "count_fp": "1.00"}`
with cash going OUT. On Kalshi "sell" is YES-denominated — selling YES means acquiring NO and
paying the NO price, which is what every MMSELL maker fill is.

Fixed as a VERIFIED table, not a condition: `CASH_DIRECTION` holds only pairs checked against an
observed balance move (today `("no","sell")` alone, three windows reconciling to exactly zero).
Any other pair prices conservatively as a debit, is named in `unknown_fill_shapes`, sets
`residual_untrustworthy`, and cannot be material. Two tests asserting `yes/sell` credits were
rewritten — they encoded the same guess that caused this.

Third payload assumption to ship in one session; all three silent. Standing rules recorded in
§9.31. Still no reward observed; lifetime $0. Full suite 4,641 passing.

[Thesis §9.31](../LIQUIDITY_INCENTIVE_THESIS.md).

## Update 2026-09-20 02:55Z — cash-direction fix VERIFIED in production

Three consecutive windows with real maker fills reconcile to residual 0: buy_cost 187 / 94 /
187 against balance deltas of -1.87 / -0.94 / -1.87. `notes_json` null throughout, so
`unknown_fill_shapes` never fired — every shape met was the verified ("no","sell") pair.

Pre-fix, these same windows would have read `sells` = price, residual ~ -2x price, flagged
`presumed deposit/withdrawal`.

Closes the three-defect sequence (§9.26 wrong client -> §9.29 zero-valued fills -> §9.31
inverted direction -> balances to the cent). All three were silent wrong numbers; none raised.

Balance 173.85 -> 169.17 over the hour is Fmmsell10 deploying capital, NOT loss. Alimm1
committed $0.15, realized -$0.06, both unchanged.

Still NO reward observed; lifetime $0. Alimm1 still blocked at the order cap; KXBIGGESTQUAKE
closed but unsettled ~2.5 days.

[Thesis §9.32](../LIQUIDITY_INCENTIVE_THESIS.md).

## Update 2026-09-21 01:42Z — the order cap is coupled to the exchange's settlement clock

Pre-registered at the 24h-past-close mark; recorded because the mark passed.

Alimm1's two KXBIGGESTQUAKE-17SEP26 positions filled 2026-09-17T20:04:27Z and were still held,
unsettled, at 2026-09-21T01:37:40Z — ~77.5 hours. Alimm1 has placed nothing since
2026-09-18T18:21:03Z.

`MAX_OPEN_ORDERS = 3` counts open COMMITMENTS (filled positions + resting orders) via
`repo.count_live_book_open`. Four commitments against three is a refusal at the placement path.
The cap is behaving as specified; the finding is what it is coupled to. A resting-order cap is a
limit the bot controls (it can cancel). A commitment cap is a limit the EXCHANGE'S SETTLEMENT
CLOCK controls, set by the slowest series in the universe. The binding resource was slots, not
risk: $0.15 committed against a $10.00 exposure limit, ~1.5%.

Recorded as an OBSERVATION, not a recommendation to change the cap. The operator has decided to
let the book run as configured.

Secondary finding, found while trying to verify the first: the intended "N hours past close"
could not be written honestly. `markets` has no row for either quake ticker,
`incentive_collector_events` none either, and `incentive_market_snapshots` has no `status` or
`close_time` column. The book records the book, quote, fill and balance but NOT the lifecycle of
the markets it trades, so it cannot answer "how long has this been stuck past close?" about
itself. Noted, not fixed — a collector change belongs to a session with that scope.

Still blocked behind this one settlement: the universe rule's refusal-code mix (§9.28), the event
cap (fix 1), and whether the book places at all.

Ledger clean: ~30 consecutive windows at residual 0, `notes_json` null, `unknown_fill_shapes`
never fired. One isolated -1c residual (2026-09-20T17:54:29Z) did not recur.

Lifetime liquidity rewards: $0.

[Thesis §9.33](../LIQUIDITY_INCENTIVE_THESIS.md).

## Update 2026-09-24 16:31 UTC — quake excluded; order cap raised 3->5 outside the formal re-arm path

Two changes, both operator-directed, in response to §9.33 (settlement-lag coupling) and its
consequence: Alimm1 blocked a full week on 3 unsettled KXBIGGESTQUAKE positions.

1. `LIQUIDITY_INCENTIVE_EXCLUDED_SERIES=KXBIGGESTQUAKE` set via ops env channel. Existing
   mechanism, no code change, redeployed and verified. Stops re-entry into the series that
   trapped the book.

2. `MAX_OPEN_ORDERS` raised 3 -> 5, edited directly into `liquidity_incentive/live.py` rather
   than through `service.arm_live_canary`. The formal path was checked and found infeasible
   same-day: the lifecycle model has no LIVE_CANARY -> PAPER transition, so a registered risk
   change here means permanently retiring `liquidity-incentive-mm` and walking a successor
   through IDEA -> PROBE -> PAPER -> LIVE_CANARY from scratch (days of fresh gate evidence).
   Operator instruction, verbatim: "why not just raise the cap on the existing? I approve if it
   'breaks' the experiment, we just need to make that note."

   Checked before shipping: `runtime_config_check`'s drift detector compares `config_json.
   material`, not `risk_envelope`, and this book's `book_params` is None either way — nothing
   live-blocking trips. What breaks, permanently: the deployment's frozen `config_json.
   risk_envelope.max_open_orders` still reads 3; the running constant now reads 5;
   `test_the_registered_envelope_equals_the_running_constants` only ever compared the in-repo
   dict to the in-repo constant, so it can't and doesn't catch this. $10 total exposure
   ceiling unchanged (5 * $1 = $5 <= $10) — this was a slots-only change.

   Tests updated: the pinned-tuple test, the ceiling-test assertion + docstring, one runner
   fixture's slot arithmetic (now derives from the constant instead of hardcoding 3). Full
   suite + `ruff check .` clean.

The 3 existing stuck quake positions are untouched, per explicit operator instruction — they
settle naturally; this change is about what the book does next, not about them.

[Thesis §9.34](../LIQUIDITY_INCENTIVE_THESIS.md).

## Update 2026-09-25 00:49 UTC — second cash-direction shape verified: ("yes","buy")

Direct consequence of the cap raise (previous update): Alimm1's first post-raise trade on a new
market (KXTRUMPMENTIONB-26SEP24-BOEI) filled ("yes","buy") -- a shape CASH_DIRECTION had never
seen. Guard worked as designed: priced it conservatively (debit), still flagged the window
residual_untrustworthy rather than claim a verified answer.

Verified against the window itself: two fills, known ("no","sell") at 93c + this ("yes","buy")
at 5c, balance fell exactly 98c. 93+5=98, zero residual. No trap here unlike the no/sell case --
"buy" means what it says, no acquire-the-other-side inversion. ("yes","buy"): DEBIT added to
CASH_DIRECTION.

What changed: not the number (unverified-shape pricing was already debit-at-price, same as
verified), only whether the window has to call itself untrustworthy to report it.

Tests: table-pin test updated; 3 tests using yes/buy as an "unverified" example moved to
yes/sell (still genuinely unverified); 1 new test reconciles the actual flagged window exactly.
ruff + full suite clean.

Still $0 lifetime rewards. This fixes what the ledger can vouch for, not what it has found.

[Thesis §9.35](../LIQUIDITY_INCENTIVE_THESIS.md).

## Update 2026-09-25 — sizing raised: $1 -> $20/order, $10 -> $50 total, 5 -> 2 concurrent

A week under §9.34/§9.35's regime still had $0 rewards. Root cause, checked in `scoring.py`
before changing anything: a side scores nothing until it reaches `RULE_REFERENCE_FRACTION =
0.2` (one-fifth) of the program's Target Size, and real Target Sizes run 300-1,000 contracts —
so a 1-contract bid could never reach the scoring floor, however long it rested. Operator,
verbatim: "I think we need to do a lot larger sizes for positions to be able to measure and
actually get rewards."

Same wall as §9.34 (no LIVE_CANARY -> PAPER transition; formal re-arm means retiring the
experiment permanently), same operator resolution, put to them again and answered the same way:
"why not just raise the cap on the existing? I approve if it 'breaks' the experiment, we just
need to make that note." This is that note, for the size raise.

Changed, all direct edits to `liquidity_incentive/live.py`:
- `MAX_CONTRACTS_PER_ORDER` 1 -> 500 (a ceiling meant to rarely bind)
- `MAX_ORDER_DOLLARS` $1.00 -> $20.00 (buys tens-to-low-hundreds of contracts at typical touch
  prices — within reach of the 60-200-contract scoring floor for the first time)
- `MAX_OPEN_ORDERS` 5 -> **2** (a drop — fewer, bigger, individually measurable bids is the
  point of a size increase, not more small ones)
- `MAX_STRATEGY_EXPOSURE_USD` $10.00 -> **$50.00** (operator's pick among 3 offered tiers)
- Invariant held: `MAX_OPEN_ORDERS * MAX_ORDER_DOLLARS <= MAX_STRATEGY_EXPOSURE_USD` (2*$20=$40
  <= $50), same guardrail test as §9.34.

Two SHARED live-trading settings also raised via the ops env channel, because they gate every
live book (mmsell, theta included), not just this one — checked line-by-line first:
- `MAX_MARKET_EXPOSURE` 1.0 -> 25.0 (gate 8 of `mirror_incentive_entry`; a $20 order would have
  been refused at the old $1 ceiling before reaching LIMM's own caps)
- `MAX_DAILY_LOSS` 5.0 -> 25.0 (gate 2, the portfolio-wide kill switch; a single bad fill at the
  new size could have tripped the old $5 ceiling and halted every live book, not just LIMM)
- `LIVE_MAX_ORDER_DOLLARS` deliberately left at 1.0 — confirmed by reading the code that it
  gates only the generic mmsell/theta mirror paths, never `mirror_incentive_entry`; raising it
  would have widened those books' sizing for no reason connected to this change.
- `MAX_TOTAL_EXPOSURE` (100.0) checked, already generous, left unchanged.

Checked before shipping, same verification as §9.34: `runtime_config_check` compares
`config_json.material` only, LIMM's `book_params` is None regardless — nothing live-blocking
trips. What breaks, permanently: the deployment's frozen `config_json.risk_envelope` still
reads the old 1/$1/5/$10 numbers; the running code now reads 500-ceiling/$20/2/$50. Also
corrected `max_loss_per_clip_usd` in the XOS package itself — its old formula named the
per-contract price as the per-order downside, true only when quantity was fixed at 1; now names
`MAX_ORDER_DOLLARS`, the real number once quantity is budget-derived.

**A genuine bug found and fixed, not assumed away:** `rank_candidates`'s primary sort key was
`collateral_usd`, which used to be monotonic with price only because quantity was fixed at 1.
Once quantity became budget-derived, collateral_usd converges to roughly the same number for
every candidate whenever the dollar cap binds — which it now does at any real price — so the
"prefer cheapest" ranking had quietly stopped discriminating on price. Caught by a test
(`test_it_places_the_cheapest_downside_first_and_stops_at_the_open_order_cap`) that stated its
own cheapest-first expectation and failed against the real output. Fixed by changing the
primary key to `price_cents`, the number that still varies meaningfully post-cap and is the one
the module's own docstring already calls the safety lever. The test's original expected order
needed no change — it was right; the ranking code was wrong.

Tests updated to derive expected values from the real sizing formula rather than hardcode new
magic numbers, across `test_liquidity_incentive_live.py`, `test_liquidity_incentive_xos_package.py`,
`test_liquidity_incentive_runner.py`, and `test_liquidity_incentive_live_executor.py`. Full
suite + `ruff check .` clean.

The 3 stuck quake positions from §9.33 are untouched. Still $0 lifetime rewards under every
sizing tried so far — this raise makes a reward possible to observe, not a claim one is coming.

[Thesis §9.36](../LIQUIDITY_INCENTIVE_THESIS.md).

## Update 2026-09-25 — two-sided pairs, the book's own exits, close-time window (§9.37)

Operator: "I'm good with the two-sided logic … make each side $10. So each position is still
$20", then "implement all 4 of those ideas along with the ideas we just had". Priorities: protect
capital, then free it fast.

- **Pairs.** YES bid + NO bid, equal quantity, `y + n <= 99`; both filling nets flat at the
  locked edge. $10 is per LEG and binds on the dearer leg, so a pair usually commits < $20. The
  25c cheap-side cap is replaced by a 90c per-leg cap. Rank: thinnest book, soonest close, widest
  edge.
- **Shared dedup gate unchanged** — the pair passes it once, as one decision
  (`LiveExecutor.mirror_incentive_pair`).
- **Exits** (`live.decide_exit`, runner, exits before entries): stop-loss at -40% of entry,
  take-profit at +40% of remaining upside (3c floors), pre-close flatten in the last hour.
  Cancel-then-IOC, mmsell closeout wire shape, 3 attempts max. When a leg is held and nothing
  rests, a post-only opposite-side bid is rested as the profitable exit.
- **Close window** 3–72h for entries (~1,000 of ~6,300 programs qualify). The quake markets
  closed 2026-09-17 and are still unsettled: settlement lag, which only the pre-close flatten
  addresses.
- **Safety:** act only on a this-cycle snapshot that agrees with our own fills; size = min of
  the two; confirmed cancels before any exit; skip shared tickers and closed markets (quake
  positions untouched). Exits bypass the loss breaker, not the switches.
- **Twin** mirrors both legs, never exits — so it measures single-leg cost.
- Fixed `activation_env` still naming MAX_MARKET_EXPOSURE=1.0 / MAX_DAILY_LOSS=5.0 (both 25.0
  in production since §9.36).
- Corrected §9.36's "scoring floor" claim: reward share is proportional to size from contract
  one; the one-fifth rule sets the Reference Price, not a minimum order.

Trade-off on record: per dollar committed, a pair rests fewer contracts on the cheap side than
§9.36's one-sided $20, so it earns less score per dollar. Accepted for the hedge.

Tests rewritten for pairs + exits; `ruff check .` and full suite clean. Still $0 lifetime
rewards.

[Thesis §9.37](../LIQUIDITY_INCENTIVE_THESIS.md).

## Update 2026-09-25 — deployed; first exit filled; closed markets release their slots (§9.38)

PR #466 live at 14:24Z. First cycle: take-profit on the old KXEARNINGSMENTIONCOST NO (17c ->
sold 97c, filled, flat) — the first exit on this book. Then `no_slots` every cycle: the 3
closed, unsettled quake positions + 1 old resting bid filled a cap of 2 — §9.33's block,
reintroduced by §9.36's 5 -> 2 cut (missed at the time). Fix: the open-market cap counts only
markets that have not closed (`count_live_book_open_tradeable`); their money still counts in
the budget. Quake positions untouched (`closed_awaiting_settlement`).

[Thesis §9.38](../LIQUIDITY_INCENTIVE_THESIS.md).

## Update 2026-09-25 — excluded series filtered before fetching (§9.39)

§9.38 deployed, `no_slots` gone (995 candidates), but every cycle fetched the same 8
soonest-closing markets — all in excluded series — and placed nothing. Excluded series are now
dropped before the bounded book fetch. [Thesis §9.39](../LIQUIDITY_INCENTIVE_THESIS.md).

## Update 2026-09-26 — netting credit in both ledgers; shared daily-loss cap 25 -> 50 (§9.40)

First pairs placed after §9.39 (PHIL rain both legs filled, +$0.22; CA and CO gas one-sided,
stopped out). CO gas settled at a real ≈ −$8.33 but was recorded −$24.33 — the settlement
formula omitted Kalshi's $1-per-netted-contract credit — which tripped the SHARED daily-loss
breaker and paused every live book. Fixed in the settlement P&L (with a one-time self-heal of
the stored row) and in the reward ledger (positions carried between readings). Operator raised
`MAX_DAILY_LOSS` 25 -> 50 (VERIFIED in production). [Thesis §9.40](../LIQUIDITY_INCENTIVE_THESIS.md).
