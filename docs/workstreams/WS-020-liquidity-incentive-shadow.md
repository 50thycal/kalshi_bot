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

## Next Step (Phase 1a)

Operator: the four-step arming sequence in [thesis §10.6](../LIQUIDITY_INCENTIVE_THESIS.md).
Step 1 (`REGISTER_PACKAGE`) arms nothing and can go now; steps 2 and 4 are hard stops. Note
that step 4 must name **both** `Alimm1` and the running `Fmmsell10` — `LIVE_STRATEGIES` matches
by prefix and replacing it would stand the MMSELL canary down.
