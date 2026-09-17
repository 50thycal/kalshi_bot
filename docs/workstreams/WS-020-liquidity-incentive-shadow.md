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

## Activation state (2026-09-17)

Merged, **not yet running**. D1 resolved by evidence: the `evo` Railway service is the host —
it runs the same main loop with a read-only Kalshi client, is alive and idle
(`EVO_ENABLED=false`, one "loop disabled" line a minute in its logs, ops `limm-logs-evo-1`),
and a redeploy there cannot touch the live book. The agent session's attempt to set the
variable was refused by its own permission layer (a production env mutation), so the
activation is the operator's one request on the ops channel:

```json
{"type":"env","service":"evo","action":"set","values":{"LIQUIDITY_INCENTIVE_SHADOW_ENABLED":"true"},"id":"limm-on-1"}
```

Ops reads that prepared it: `limm-cap-1` (runner on `ccf4f9d`, the new variables and the
report script allowlisted), `limm-env-evo-1`, `limm-logs-evo-1`. Channel reset to `noop`.

## Next Step

Operator sends the `env set` above (redeploys the idle evo service only); then, within the
first hour, `{"type":"script","name":"liquidity_incentive_report","id":"limm-report-1"}` and
read § COLLECTOR first: alive, tape landing, programs listed, `period_reward_usd` plausible
against kalshi.com/incentives.
