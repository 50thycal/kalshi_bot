# WS-004 — Live-canary gates that cannot produce a verdict

**Phase:** DECIDE
**Status:** Active
**Created:** 2026-08-24
**Updated:** 2026-08-24

## Goal

Give both live-canary experiments a path to an actual gate verdict — or a recorded decision
that they will not get one — instead of leaving two real-money books permanently
un-evaluable.

## Context

Both live canaries carry a `live_canary_keep` gate that is recorded `BLOCKED_DATA`. The
evaluator names the cause precisely: the imported gate clauses omit `deployment_kind`, so
they default to `paper`, while the epochs hold `live` and `paper_twin` deployments only.
Every clause therefore resolves to an **empty scope**, and the metrics named are live-only
by definition and will not substitute a different deployment kind.

The sharp part, from the prior investigation: **implementing the missing providers alone
will not unblock these Versions.** A frozen contract cannot be edited, so a corrected
addressing means a successor Version, authored by Research Lab.

This was investigated once per experiment and both tickets were closed `NO_ACTION`. That
closure did not make the gates evaluable, and the condition is still detected on every
control-tower read as an un-ticketed anomaly.

Both books are currently stood down — the runtime live allowlist is empty — so no new
evidence is accruing and nothing is at risk from the delay. Held positions are still real
money and still settle.

## Current Mental Model

```text
  gate clause  ──►  deployment_kind defaults to "paper"
                          │
  epoch holds:  live ─────┼──── paper_twin          (no paper deployment)
                          ▼
                    EMPTY SCOPE  ──►  BLOCKED_DATA, permanently

  two independent remedies, only one of which is real:
    (a) implement the missing metric providers   ──► does NOT help; scope is still empty
    (b) author a successor Version with correct
        deployment_kind addressing               ──► the actual fix, and a Research Lab act
```

The distinction that matters: this is **not** a missing-data problem wearing a gate's
clothing, and it is **not** a platform-semantics change. Nothing shared changed. A
contract was frozen with an addressing mistake, and only a new contract can correct it.

## Decisions Made

- **Not a Platform Revision.** No shared semantic changed; the evaluator's own routing note
  says so. Treating it as one would mint a revision nobody needs and drag every other
  experiment through an impact review.
- **Providers are not the fix.** Recorded here because it is the intuitive wrong answer and
  has to be argued past every time this resurfaces.

## Open Decisions

- **D1.** Author successor Versions with corrected addressing, retire the canaries, or
  leave them stood down and un-evaluable indefinitely? Each is defensible. Authoring is
  work with no immediate payoff while the books are stood down; retiring discards a live
  canary that was expensive to arm; leaving them is the status quo and quietly costs the
  ability to ever conclude anything about them. Owner + Research Lab.
- **D2.** If successor Versions are authored, does the existing live evidence carry over, or
  does the corrected contract start a fresh count? This is a scientific-contract question,
  not a bookkeeping one.
- **D3.** Should the two un-ticketed `gate.blocked` anomalies be adopted as XOS issues
  first? Adopting carries the detector fingerprint, so the ticket covers the anomaly rather
  than leaving it listed forever. The prior tickets were closed `NO_ACTION`, so re-opening
  needs a reason beyond "it is still there".

## Assumptions

- Both books stay stood down while this is open. If either is re-armed, this stops being
  low-urgency: a live book accruing evidence against a gate that can never fire is worse
  than a stood-down one.
- The evaluator's diagnosis is correct and complete. It is specific enough to act on
  without re-deriving, but it has not been independently re-verified in this workstream.

## Non-Goals

- Re-arming either canary.
- Editing a frozen Version.
- Implementing metric providers on the assumption that they will help.

## Build Card

Not ready — this is in `DECIDE`, and D1 changes what, if anything, gets built.

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

- `mmsell-scheduled-settle-live` and `theta4-fat-tail` — the two live canaries.
- `XOS-000001`, `XOS-000002` — the prior investigations, both `CLOSED_NO_ACTION`.
- Two un-ticketed `gate.blocked` anomalies, recommended owner Research Lab, adoptable by
  fingerprint from the control-tower report.

## Next Step

Put D1 to the operator with the three options and their costs; adopt the two `gate.blocked`
candidates as XOS issues if the answer is anything other than "leave them".
