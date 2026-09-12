# WS-017 — Passive BTC/ETH perp reversion: probe first

**Build OS:** v0.12 · **Phase:** COMPLETE · **Status:** Complete
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
Finalization: pushed. #397 shipped the instrument; the results closeout records its run
and closes the mission. No architecture or shared metric behavior changes.

## Next Step

None.

## Completion

#397 merged after both full CI runs passed. The default-branch read-only probe executed
successfully as `passive-perp-20260912-1`; the result is linked and interpreted in the
[census Results](../PASSIVE_PERP_CENSUS.md#results--2026-09-12), journal and scorecard.
This standalone census is not an XOS experiment or lifecycle transition. Its frozen
spending rule does not advance to a collector or paper contract. Both alternatives remain
parked. The ops channel was reset to noop after the completed request. No outstanding
in-scope implementation task or live exposure change.

## Parked

The two unselected strategies are one-line entries on ACTIVE.md; neither is active work.
