# WS-004 — Reconciling the recurring blocked-gate anomaly for the historical live canaries

**Phase:** DECIDE
**Status:** Active
**Created:** 2026-08-24
**Updated:** 2026-08-24

## Goal

Decide how the Control Tower should represent a blocked gate that is **permanently true and
deliberately unactioned**, so that a settled decision stops re-presenting itself as
un-ticketed work on every read — without reopening the successor decisions that were
already taken.

## Context

Two historical live-canary Versions carry a `live_canary_keep` gate that cannot render a
verdict. The cause was proven, not guessed: every imported clause omits `deployment_kind`,
so each defaults to `paper`, while the epochs hold `live` and `paper_twin` deployments only.
Every clause resolves to an empty scope, and implementing the missing metric providers would
not change that — the scope would still be empty
(`docs/RESEARCH_LIVE_CANARY_CONTRACT_DEFECT.md`).

**The remedy that finding proposed has since been declined, and those decisions stand:**

- Both live canaries were stood down and stay stood down
  (`docs/RESEARCH_LIVE_FILL_SELECTION_STUDY.md`).
- The proposed successor live-v2 contracts were **withdrawn**
  (`docs/RESEARCH_SUCCESSOR_GATE_DESIGN.md`, WITHDRAWN 2026-08-21, `#251`). The MMSELL
  successor design is not to be frozen, because treatment and control differ in universe,
  entry-price band and settle mode at once and no sample size repairs that
  (`docs/RESEARCH_MMSELL_UNIVERSE_DECONFOUNDING.md`). The theta replacement was rejected;
  the book carries two independent failures and needs research before another canary
  (`docs/RESEARCH_THETA_TAIL_MODEL_DIAGNOSIS.md`).
- Both historical contract findings were closed `NO_ACTION` on exactly that reasoning, with
  the stand-down recorded (`docs/EXPERIMENT_OS_ISSUES.md` → *The two historical contract
  findings*).

So the defect is settled and this workstream does not revisit it. What is **not** settled is
a platform-reporting question the closure exposed: Experiment OS deliberately does not let a
non-open issue suppress a recurring anomaly ("we fixed that once" is not evidence it is
fixed now), and the `gate.blocked` detector has no counterpart to the explained-absence
suppression that `experiment.zero_evidence` gets from a recorded stand-down. A condition
that is permanent by decision therefore keeps arriving as a ticket candidate.

## Current Mental Model

```text
  proven defect ──► settled decision: stand down, no successor Version
                            │
                            ▼
              issue CLOSED_NO_ACTION  ──►  not OPEN
                            │
        by design, a non-open issue does NOT suppress recurrence
                            │
                            ▼
        gate.blocked fires again on the next read  ──►  un-ticketed candidate
                            │
                    ...forever, for a decision already taken

  compare, in the same detector surface:
    experiment.zero_evidence  ──► suppressed when a recorded stand-down
                                   EXPLAINS the absence
    gate.blocked              ──► no such concept
```

The distinction that matters: this is **not** a missing-data problem, **not** a
platform-semantics change, and **not** an unfinished experiment. It is a gap in how the
Tower expresses *"known, decided, will not be repaired"* for a condition that remains
literally true.

And the tension is real in both directions. The recurrence rule exists because suppressing a
still-true anomaly is how a live problem gets lost; a suppression built for these two would
also apply to a genuinely broken gate on a book somebody is still trading.

## Decisions Made

- **Preserve the successor decisions.** The withdrawal of the live-v2 designs and the
  `NO_ACTION` closures are inputs to this workstream, not questions in it. Nothing here
  proposes, authors, registers, arms or promotes a successor Version.
- **Not a Platform Revision.** No shared trading semantic changes — no fees, fills,
  taxonomy, execution, risk or metric definition. Treating it as one would mint a revision
  nobody needs and drag every other experiment through an impact review.
- **Providers are not the fix.** Recorded because it is the intuitive wrong answer and has
  to be argued past every time this resurfaces.

## Open Decisions

- **D1.** How should a deliberately-unactioned blocked gate be represented? Three shapes,
  all confined to the reporting layer:
  **(a)** extend the explained-absence pattern to `gate.blocked`, so a recorded stand-down
  explains a blocked gate the way it already explains zero evidence;
  **(b)** give the Tower an explicit *accepted historical condition* concept, keyed by
  detector fingerprint, so a recorded decision covers the recurrence without asserting the
  problem is fixed;
  **(c)** accept the noise and document it, so readers learn which rows to skip — cheapest,
  and it costs the un-ticketed list the property that makes it worth reading.
  Owner decision; (a) and (b) differ in how much they can over-suppress.
- **D2.** Whichever shape wins, what stops it hiding a *live* book's broken gate? A
  suppression rule with no scope limit is the failure mode that makes this worse than the
  noise it removes.

## Assumptions

- The historical canaries stay stood down. If either is ever re-armed, this stops being a
  reporting question: a book accruing live evidence against a gate that cannot fire is a
  different and more urgent problem.
- The evaluator's addressing diagnosis is correct and complete. It is specific enough to
  build on without re-deriving, and it has not been independently re-verified here.

## Non-Goals

- Creating, proposing, registering, arming or promoting a successor Version — the successor
  designs were withdrawn and this workstream keeps them withdrawn.
- Re-arming either canary, or editing a frozen Version.
- Reopening the closed issues to relitigate the remedy. Reopening on *recurrence* is a
  separate, explicit act with its own reason.
- Implementing metric providers on the assumption that they will help.

## Build Card

Not ready — this is in `DECIDE`, and D1 decides what, if anything, gets built.

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

- `mmsell-scheduled-settle-live` and `theta4-fat-tail` — the two historical live canaries.
- `XOS-000001`, `XOS-000002` — the two historical contract-defect issues, closed on the
  reasoning above.
- The `gate.blocked` detector and its fingerprint — the surface D1 is about.

## Next Step

Put D1 to the operator as a reporting-layer choice between (a), (b) and (c), with D2's
scope-limit constraint attached — explicitly *not* as a question about successor Versions,
which are decided.
