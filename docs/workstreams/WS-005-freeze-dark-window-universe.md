# WS-005 — A testable universe for the freeze dark-window hypothesis

**Phase:** EXPLORE
**Status:** Blocked
**Created:** 2026-08-24
**Updated:** 2026-08-24

## Goal

Establish whether a universe exists on Kalshi that can actually test the freeze
dark-window hypothesis — or record that none does, so the idea stops being re-proposed.

## Context

`freeze-dark-window-pin` is deployed and has produced **zero** evidence. The investigation
concluded that the cause is not a wiring bug: no currently available universe satisfies the
hypothesis, and none has been proposed. The recorded remedy is a stand-down back to the hold
this family was already in, not a corrected Version — a successor Version needs a valid
universe to point at, and freezing one now would register a second contract that can never
trade.

The ticket is `ACTION_REQUIRED` and the anomaly is still being detected, so this is open
rather than concluded.

## Current Mental Model

```text
  hypothesis needs:  a market whose settlement source goes DARK for a stretch
                     while the market keeps trading
                            │
   what was searched by:    crop NAME in title/ticker        ← wrong axis
   what it must be:         SETTLEMENT SOURCE in rules text  ← right axis

  and the capacity test must be DEPTH-INVARIANT: a count that doubles when the
  scan depth doubles was measuring the scan, not the universe.
```

Three things are wrong with how the universe was sought, and they are independent:

1. **Admission was by name, not by source.** A crop name in a title is not evidence that
   settlement references a continuously quoted benchmark.
2. **The probe presupposed its conclusion.** The post-pin cell must infer the side from
   point-in-time information and be graded against the realized result, so that it *can*
   come out flat or negative. Until it can, no run of it falsifies anything and no run may
   be cited as evidence.
3. **The capacity trigger was depth-dependent.** A count that moves materially when scan
   depth doubles is a property of the scan, not of the universe.

## Decisions Made

- **Stand down rather than author a successor Version.** A contract pointing at a universe
  that does not exist is worse than no contract.
- **The resume condition is written down and independently checkable** — it lives in the
  XOS ticket's validation plan, and this workstream links to it rather than restating it.

## Open Decisions

- **D1.** Is anyone going to look for a qualifying universe, or is this effectively
  abandoned? The honest answer changes the status: a real search makes this `EXPLORE`, and
  no search makes it `ABANDONED` with the reasoning preserved. Nobody has decided, which is
  why it currently sits `Blocked` rather than in either.

## Assumptions

- The hypothesis is still worth testing *if* a universe turns up. If that stops being true,
  abandon it explicitly — the reasoning is what stops the idea being re-proposed every
  quarter.
- Settlement source is readable from Kalshi's own rules text at market granularity. This is
  the same assumption WS-002 relied on and found to hold.

## Non-Goals

- Registering a successor Version before a universe exists.
- Fixing the book's code — nothing is wrong with it.
- Re-running the existing probe as-is; it cannot falsify anything.

## Build Card

Not ready.

## Implementation State

None.

## Review State

Not started.

## Related Decisions

None yet.

## Related PRs

None yet.

## Related Experiment OS objects

Linked, not restated — query Experiment OS for current state.

- `XOS-000003` — `ACTION_REQUIRED`, classification `STRATEGY`, owner `RESEARCH_LAB`,
  disposition `PAUSE_OR_STAND_DOWN`. Carries the full resume condition.
- `freeze-dark-window-pin` — the experiment.

## Next Step

Answer D1: commit to a source-based universe search, or abandon the hypothesis explicitly
and record why.
