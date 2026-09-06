# Active Work

The project's active-work control board — what is being designed and built right now, and
where each effort is. Read it first on a continuation.

**Updated:** 2026-09-06 (evo fleet paused for cost) · **Build OS v0.4**

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
| [WS-010](WS-010-perp-v1-pre-registration.md) | PERP-V1: a research surface for Kalshi perpetual futures | **CLOSED** | Done | Closed 2026-09-02 on a COST finding. Arm A FAIL (premium reversion is real — +5.63 bps/trade pre-fee, 913 obs, vs a −10.13 control — but tier-0 taker is 24 bps round trip, 2.7x the whole bid-ask). Arm B BLOCKED_DATA (no funding source). Arm C NO-GO (null at 300 s; fast horizons untested, and the binding constraint was theta's 5-min ladder cadence, not the perp collector). Never registered in production, so there is no XOS record — docs are the record. Open: turn the collector off (Live Ops), and whether to register retrospectively (Control Tower) | [#275](https://github.com/50thycal/kalshi_bot/pull/275), [#277](https://github.com/50thycal/kalshi_bot/pull/277), [#280](https://github.com/50thycal/kalshi_bot/pull/280), [#291](https://github.com/50thycal/kalshi_bot/pull/291), [#305](https://github.com/50thycal/kalshi_bot/pull/305), [#307](https://github.com/50thycal/kalshi_bot/pull/307), [#308](https://github.com/50thycal/kalshi_bot/pull/308), [#310](https://github.com/50thycal/kalshi_bot/pull/310), this PR |
| [WS-011](WS-011-marktangle-conditional-reversion.md) | MARKTANGLE: conditional reversion in recurring binary families | **CLOSED** | Done | Closed 2026-09-03 by operator decision, together with its successor. Registered and RETIRED in one act (`CLOSE_OUT_RETROSPECTIVE`, package `marktangle-reversion`) — it had never been in Experiment OS at all, despite three documents saying PAUSED at PROBE. Both gates HOLD, by the contract's own frozen thin-holdout rule (best families 13–27 entries against a floor of 100). The directional finding stands as recorded history: daily crypto threshold families are momentum machines, not coin flips | [#287](https://github.com/50thycal/kalshi_bot/pull/287), this PR |
| [WS-013](WS-013-marktangle-2-conditional-dependence.md) | MARKTANGLE-2: conditional dependence alpha (two tracks, pre-registered) | **CLOSED** | Done | Both tracks closed 2026-09-03 by operator decision; RETIRED via `CLOSE_OUT_RETROSPECTIVE` (package `marktangle-2`, which ADOPTS the contract already in production rather than re-registering). **Track A FAIL** — refuted in all three adequately-powered classes, and `prev_dir × ln(k)` is zero within noise twice and wrong-signed once, so streak length carries nothing. **Track B BLOCKED_DATA** — persistence is real and strongly forecastable (98.3% holdout accuracy) and unpriceable: 16 two-sided quotes in ~2,000 fetches, 0% coverage against a 50% floor. Both depart from the instrument's printed `HOLD`/`HOLD`; the departure is an operator conclusion and is recorded as one in `marktangle2.CLOSE_OUT_VERDICTS`. Nothing re-scoped | [#315](https://github.com/50thycal/kalshi_bot/pull/315), [#316](https://github.com/50thycal/kalshi_bot/pull/316), [#318](https://github.com/50thycal/kalshi_bot/pull/318), [#319](https://github.com/50thycal/kalshi_bot/pull/319), [#320](https://github.com/50thycal/kalshi_bot/pull/320), this PR |
| [WS-014](WS-014-evo-fleet-health-and-xos-bridge.md) | Evo fleet health: dead peer-visibility path + the evo→XOS bridge | REVIEW | Paused | Fleet paused 2026-09-06 (operator, cost) — `EVO_WEEKLY_LLM_CEILING_USD=0` on the evo service. D1/D2 merged (#328) but never observed running before the pause. Resume: raise the ceiling back to 8, then check whether `evo_listeners`/`evo_influences` populate | this PR |

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

| ID | Workstream | Completed | Outcome |
|---|---|---|---|
| [WS-012](WS-012-ops-channel-vnext.md) | Ops channel vNext: reliability, introspection, verified operations | 2026-09-02 | Merged [#294](https://github.com/50thycal/kalshi_bot/pull/294)/[#306](https://github.com/50thycal/kalshi_bot/pull/306)/[#313](https://github.com/50thycal/kalshi_bot/pull/313). Deployed to `ops` and validated with a real round trip (green on success, RED on a deliberately bad request — the P1 fix proven in production). Two follow-ups carried past close, neither blocking: the live `ops` ruleset is still unverified (needs admin-scoped token), and an XOS issue for the #313 defect is prepared but held for LIVE_OPS to send when safe |
