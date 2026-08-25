# Active Work

The project's active-work control board — what is being designed and built right now, and
where each effort is. Read it first on a continuation.

**Updated:** 2026-08-25 · **Build OS v0.4**

| ID | Workstream | Phase | Status | Current Next Step | Related PR |
|---|---|---|---|---|---|
| [WS-001](WS-001-build-os-adoption.md) | Build OS adoption | REVIEW | Active | Independent review; approval + merge completes it | this PR |
| [WS-002](WS-002-mmsell-settlement-taxonomy-repair.md) | MMSELL settlement-taxonomy repair | REVIEW | Blocked | Merge guard: verify in XOS that the revision is registered + impacts accepted | [#257](https://github.com/50thycal/kalshi_bot/pull/257) |
| [WS-003](WS-003-mmsell-noncrypto-settlement-mode-paper-design.md) | MMSELL non-crypto settlement-mode paper design | DECIDE | Blocked | Waiting on WS-002, the crypto-exclusion defect, and the event-correlation measurement | — |
| [WS-004](WS-004-live-canary-gate-addressing.md) | Reconciling the recurring blocked-gate anomaly (historical canaries) | DECIDE | Active | Put the reporting-layer options to the operator (explain / accept-condition / live with it) | — |
| [WS-005](WS-005-freeze-dark-window-universe.md) | A testable universe for the freeze dark-window hypothesis | EXPLORE | Blocked | Commit to a source-based universe search, or abandon explicitly | — |
| [WS-006](WS-006-evo-population-foundation.md) | Evo population foundation (evolutionary search over strategy genomes) | REVIEW | Active | Operator approval for the first prospective paper cohort, or a real-dataset historical cohort (D1) | this PR |

*Phase:* IDEA · EXPLORE · MODEL · DECIDE · BUILD_CARD · READY_TO_BUILD · BUILDING · REVIEW
*Status:* Active · Paused · Blocked · Abandoned
Completed and abandoned workstreams leave this table; their files remain.

---

## What is deliberately not on this board

The board tracks **design/build threads**, not experiments and not tickets. Running
experiments, their standings, their ticket statuses and their gate verdicts belong to
Experiment OS — ask it (`xos control-tower`), because a copy here would be stale within a
day and believed anyway (`DEC-001`). The `Current Next Step` column states what someone
should *do*, including guards to check before acting; it never states what Experiment OS
currently says.

The specific omissions from the 2026-08-24 seeding inventory, with reasons, are recorded in
[WS-001](WS-001-build-os-adoption.md#appendix--efforts-considered-and-deliberately-not-made-workstreams).

## Recently completed

None yet.

| ID | Workstream | Completed | Outcome |
|---|---|---|---|
| — | — | — | — |
