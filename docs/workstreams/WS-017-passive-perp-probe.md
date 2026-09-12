# WS-017 — Passive BTC/ETH perp reversion: probe first

**Build OS:** v0.12 · **Phase:** REVIEW · **Status:** Active
**Related PR:** [#397](https://github.com/50thycal/kalshi_bot/pull/397)

## Goal

Run a bounded read-only historical price-return probe, record its honest verdict, and
park Calvin's two unselected strategies. Completion means a measured result or a named
data blocker, not starting a trading book.

## Non-Goals

Live or paper orders, changing PERP-V1's historical contract/metrics, collector deployment,
parameter search, or implementation of either parked idea.

## Build Card

Calvin approved the probe-first path and the ops-runner allowlist change/merge in the
2026-09-12 ChatGPT session. Measure the actual underlying price movement before spending
on passive execution telemetry. Preserve the unknown funding/fill costs as unknown.

## Build Spec

[PASSIVE_PERP_CENSUS.md](../PASSIVE_PERP_CENSUS.md) freezes data, signals, measurements,
decision rules and run request before querying production. Add only the new module name
to the existing ops allowlist; do not alter dispatch or permissions.

## Acceptance Checks

- Economic counterexample proves premium convergence can coexist with a losing short.
- Both quote sides, both fee notionals, prior-only statistics, censorship and empty data tested.
- Compile/import/lint and CI pass; default-branch probe runs via the scoped ops transport.
- Immutable ops result linked; verdict recorded in census, scorecard and journal.
- Two alternatives PARKed; request reset without clobbering another session.

## Assumptions and material interrupt risks

Retained data still exists and RO role can read it. No proof of executable maker fills.
Missing data results in HOLD, not a scope change. User explicitly admitted this bounded
probe while the pre-existing board is over its active limit; no other workstream is paused
or reprioritized by this session, and no perpetual collector is added.

## Open Decisions

None for this census. Prospective trading-contract design is conditional on its result.

## Implementation / Review State

Implementation actor: Codex / passive-perp-20260912. Solo mode; no independent reviewer.
Merge authorization: Calvin's explicit 2026-09-12 approval in this session of the named
read-only allowlist addition, testing and merge. Accepted head will be recorded on the PR.
Framework compatibility checked against canonical Build OS v0.12 on 2026-09-12.
11 targeted tests pass; changed Python files compile/import and pass ruff.
Finalization: pushed. This PR ships the instrument; the mission remains open for its run
and result logging. No architecture or shared metric behavior changes.

## Next Step

After green CI and the approved merge of #397, run the frozen census, log its result
and close this bounded workstream.

## Parked

The two unselected strategies are one-line entries on ACTIVE.md; neither is active work.
