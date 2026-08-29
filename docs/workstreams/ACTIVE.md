# Active Work

The project's active-work control board — what is being designed and built right now, and
where each effort is. Read it first on a continuation.

**Updated:** 2026-08-29 · **Build OS v0.4**

| ID | Workstream | Phase | Status | Current Next Step | Related PR |
|---|---|---|---|---|---|
| [WS-001](WS-001-build-os-adoption.md) | Build OS adoption | REVIEW | Active | Independent review; approval + merge completes it | this PR |
| [WS-002](WS-002-mmsell-settlement-taxonomy-repair.md) | MMSELL settlement-taxonomy repair | REVIEW | Blocked | Merge guard: verify in XOS that the revision is registered + impacts accepted | [#257](https://github.com/50thycal/kalshi_bot/pull/257) |
| [WS-003](WS-003-mmsell-noncrypto-settlement-mode-paper-design.md) | MMSELL non-crypto settlement-mode paper design | DECIDE | Blocked | Waiting on WS-002, the crypto-exclusion defect, and the event-correlation measurement | — |
| [WS-004](WS-004-live-canary-gate-addressing.md) | Reconciling the recurring blocked-gate anomaly (historical canaries) | DECIDE | Active | Put the reporting-layer options to the operator (explain / accept-condition / live with it) | — |
| [WS-005](WS-005-freeze-dark-window-universe.md) | A testable universe for the freeze dark-window hypothesis | EXPLORE | Blocked | Commit to a source-based universe search, or abandon explicitly | — |
| [WS-006](WS-006-evo-search-capability.md) | Evo historical search capability (agents search their own strategy space) | REVIEW | Active | D1 CLEAN 2026-08-28 (both runs identical across processes, all three legs). D2 is the remaining prerequisite and is Platform Change Review work; no prospective cohort without it plus explicit operator approval | [#261](https://github.com/50thycal/kalshi_bot/pull/261), [#262](https://github.com/50thycal/kalshi_bot/pull/262), [#263](https://github.com/50thycal/kalshi_bot/pull/263) |
| [WS-007](WS-007-mmsell10-live-canary.md) | mmsell10 Stage-1 live canary + exact paper twin | REVIEW | Active — **LIVE** | ARMED 2026-08-28T14:20:35Z, activated 14:48Z. Real money at risk inside the Stage-1 envelope ($1/order, 1 contract, $5 daily stop, $15 budget). Watch the pre-registered keep/stop clauses; `live_canary_keep` stays BLOCKED_DATA until 150 settled contracts | [#264](https://github.com/50thycal/kalshi_bot/pull/264), [#265](https://github.com/50thycal/kalshi_bot/pull/265), [#266](https://github.com/50thycal/kalshi_bot/pull/266), [#267](https://github.com/50thycal/kalshi_bot/pull/267) |
| [WS-008](WS-008-epoch-deployment-continuity.md) | An epoch boundary must not silently stop the books (XOS-000011) | REVIEW | Complete | Merged (#268), repaired in production, XOS-000011 RESOLVED 14:14:59Z with all five validation checks passed. Open question — whether the engine change warrants a Platform Revision — belongs to Platform Change Review | [#268](https://github.com/50thycal/kalshi_bot/pull/268) |
| [WS-009](WS-009-livedash-load-and-selection.md) | Live-vs-paper dashboard: load cost, run selection, retired-pair landing | REVIEW | Active | Merged (#271, #272, #273). Selection, layout and D3 all verified; one item left — confirm on the deployed livedash that first paint is seconds not half a minute, which needs an operator or a browser on the public URL | [#271](https://github.com/50thycal/kalshi_bot/pull/271) |

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
