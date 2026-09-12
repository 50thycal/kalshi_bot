# Active Work

The project's active-work control board — what is being designed and built right now, and
where each effort is. Read it first on a continuation.

**Updated:** 2026-09-12 (WS-017 bounded probe admitted by operator) · **Build OS v0.12**

| ID | Workstream | Phase | Status | Current Next Step | Related PR |
|---|---|---|---|---|---|
| [WS-017](WS-017-passive-perp-probe.md) | Passive BTC/ETH perp probe | REVIEW | Active | After green CI and approved merge, run the frozen census and record its verdict | [#397](https://github.com/50thycal/kalshi_bot/pull/397) |
| [WS-002](WS-002-mmsell-settlement-taxonomy-repair.md) | MMSELL settlement-taxonomy repair | REVIEW | Blocked | Merge guard: verify in XOS that the revision is registered + impacts accepted | [#257](https://github.com/50thycal/kalshi_bot/pull/257) |
| [WS-003](WS-003-mmsell-noncrypto-settlement-mode-paper-design.md) | MMSELL non-crypto settlement-mode paper design | DECIDE | Blocked | Waiting on WS-002, the crypto-exclusion defect, and the event-correlation measurement | — |
| [WS-004](WS-004-live-canary-gate-addressing.md) | Reconciling the recurring blocked-gate anomaly (historical canaries) | DECIDE | Active | Put the reporting-layer options to the operator (explain / accept-condition / live with it) | — |
| [WS-005](WS-005-freeze-dark-window-universe.md) | A testable universe for the freeze dark-window hypothesis | EXPLORE | Blocked | Commit to a source-based universe search, or abandon explicitly | — |
| [WS-006](WS-006-evo-search-capability.md) | Evo historical search capability (agents search their own strategy space) | REVIEW | Active | D1 CLEAN 2026-08-28 (both runs identical across processes, all three legs). D2 is the remaining prerequisite and is Platform Change Review work; no prospective cohort without it plus explicit operator approval | [#261](https://github.com/50thycal/kalshi_bot/pull/261), [#262](https://github.com/50thycal/kalshi_bot/pull/262), [#263](https://github.com/50thycal/kalshi_bot/pull/263) |
| [WS-007](WS-007-mmsell10-live-canary.md) | mmsell10 Stage-1 live canary + exact paper twin | REVIEW | Active — **LIVE** | ARMED 2026-08-28T14:20:35Z, activated 14:48Z. Real money at risk inside the Stage-1 envelope ($1/order, 1 contract, $5 daily stop, $15 budget). Watch the pre-registered keep/stop clauses; `live_canary_keep` stays BLOCKED_DATA until 150 settled contracts | [#264](https://github.com/50thycal/kalshi_bot/pull/264), [#265](https://github.com/50thycal/kalshi_bot/pull/265), [#266](https://github.com/50thycal/kalshi_bot/pull/266), [#267](https://github.com/50thycal/kalshi_bot/pull/267) |
| [WS-009](WS-009-livedash-load-and-selection.md) | Live-vs-paper dashboard: load cost, run selection, retired-pair landing | REVIEW | Active | Merged (#271, #272, #273). Selection, layout and D3 all verified; one item left — confirm on the deployed livedash that first paint is seconds not half a minute, which needs an operator or a browser on the public URL | [#271](https://github.com/50thycal/kalshi_bot/pull/271) |
| [WS-016](WS-016-standing-authorizations.md) | Standing authorizations: one-request paper tapes, hard stops, the closing brief | REVIEW | Active | Owner merges; then decide which of WS-004/006/009 pauses (board is at five Active, limit four — recommendation WS-009) | this PR |
| [WS-014](WS-014-evo-fleet-health-and-xos-bridge.md) | Evo fleet health: dead peer-visibility path + the evo→XOS bridge | REVIEW | Paused | Fleet paused 2026-09-06 (operator, cost) — `EVO_WEEKLY_LLM_CEILING_USD=0` on the evo service. D1/D2 merged (#328) but never observed running before the pause. Resume: raise the ceiling back to 8, then check whether `evo_listeners`/`evo_influences` populate | [#328](https://github.com/50thycal/kalshi_bot/pull/328) |

*Phase:* IDEA · EXPLORE · MODEL · DECIDE · BUILD_CARD · READY_TO_BUILD · BUILDING · REVIEW
*Status:* Active · Paused · Blocked · Abandoned
Completed and abandoned workstreams leave this table; their files remain.

**Active-work limit: 4.** Build OS v0.12 defaults to three; this project declares four, and
counts only `Active` rows. `Blocked` — which requires a *named* external unblocker, not
"waiting" — and `Paused` are not consuming operator attention, and a research repository
runs several pre-registered threads that wait on evidence rather than on the operator. The
board is **one over** the limit today (`WS-004`, `WS-006`, `WS-007`, `WS-009`, `WS-016`) —
`WS-016` was opened on an explicit operator request, and its D1 asks which row pauses. Reason recorded in `DEC-011`.
The operator explicitly requested WS-017's bounded probe on 2026-09-12, temporarily bringing
the count to six; this session closes it on a measured census result or named data blocker,
and neither silently pauses existing work nor starts a continuing collector.

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

## Parked

- Purchased-tail MMSELL hedge: explore buying a farther-tail YES against a NO threshold position on the same contest with matching settlement rules; evaluate interval loss and hedge cost (Calvin, 2026-09-12).
- Same-asset spot/perp funding carry: explore long spot plus equal-unit short perp, including funding, fees, basis and capital costs; distinct from cross-asset PERP-V1 carry (Calvin, 2026-09-12).

Deferred candidates. One line each — no ID, no phase, no owner, no PR, no estimate. Nothing
here is scheduled and **no agent may start anything in it**; it becomes work only when the
owner promotes it into the table above, under the active-work limit. This is where a `PARK`
disposition lands, and it is deliberately impoverished: a parking lot pleasant to work from
is a backlog, and the backlog is what made the board unreadable. Delete a line that stops
being worth doing.

The *reasons* behind the 2026-08-24 omissions stay in the prose section above and in
[WS-001](WS-001-build-os-adoption.md#appendix--efforts-considered-and-deliberately-not-made-workstreams);
this list is the bare register, not the argument.

- Decide whether PERP-V1 is registered retrospectively in Experiment OS, or stays
  documentation-only history. Owned by **Experiment Control Tower**; a `CLOSE_OUT_RETROSPECTIVE`
  question, not a Build OS one.
- Verify the live `ops` branch ruleset (carried past `WS-012`; needs an admin-scoped token).
- Pin `scripts/mmsell_contest_cap_audit.py`'s committed-status tuple against `repository.py`, the
  way `scripts/live_book_truth.py` now is; its copy carries statuses the enforcing code does not.
- Attribute `live_book_truth`'s never-ordered bucket to the specific gate that refused each
  market, by joining the skip counters per ticker — turning "what the caps cost" into "what the
  contest cap cost".

## Recently completed

| ID | Workstream | Completed | Outcome |
|---|---|---|---|
| [WS-015](WS-015-mmsell10-queue-aware-cancel.md) | mmsell10 queue-aware cancellation: shadow instrument + pre-registered contract | 2026-09-07 | Shipped, ran three days in shadow, **thesis falsified, experiment RETIRED 2026-09-10** (`qac-retire-20260910-1`). The frozen rule's 10% fill-probability threshold did not hold out of sample: 27.5% of would-cancel orders filled (bar 15%) at 1.65c forgone each (bar 1.0c), and the open-position cap never bound on the live book (0%, bar 50%) — the capacity premise came from paper canaries, not the book the instrument observed. Three of four promotion clauses failed; `shadow_kill` never tripped. Nothing was ever cancelled and no real money was touched. Queue telemetry (100% coverage, ~7,800 decisions) survives and is reusable; so does a live-safeguard defect found incidentally and fixed by Live Ops (XOS-000028). PRs [#364](https://github.com/50thycal/kalshi_bot/pull/364), [#365](https://github.com/50thycal/kalshi_bot/pull/365), [#373](https://github.com/50thycal/kalshi_bot/pull/373). Verdict and what survives: §7b of the thesis doc |
| [WS-001](WS-001-build-os-adoption.md) | Build OS adoption, and the v0.4 → v0.12 migration | 2026-09-06 | Two acts on one thread. **v0.4 adopted** in [#258](https://github.com/50thycal/kalshi_bot/pull/258) (merged 2026-08-24) — the framework block, three memory layers, the board, templates, the wired PR handoff, `DEC-001`'s authority boundary; the row then sat in a false `REVIEW` for eleven days awaiting an independent verdict that could not exist. **v0.12 adopted** here, with the four decisions in `DEC-011`: operating mode `solo`, a stated split between the session identity header and the Owner Result, an active-work limit of 4 counting `Active` only, and `SHIP` as a development-gate report whose sequel is a guard rather than an authorization |
| [WS-008](WS-008-epoch-deployment-continuity.md) | An epoch boundary must not silently stop the books (XOS-000011) | 2026-09-03 | Merged [#268](https://github.com/50thycal/kalshi_bot/pull/268) and repaired in production; XOS-000011 RESOLVED with all five validation checks passed. Whether the engine change warrants a Platform Revision is Platform Change Review's question, not this workstream's |
| [WS-010](WS-010-perp-v1-pre-registration.md) | PERP-V1: a research surface for Kalshi perpetual futures | 2026-09-02 | Closed on a **COST** finding. Arm A FAIL — premium reversion is real (+5.63 bps/trade pre-fee, 913 obs, against a −10.13 control) and unreachable, because tier-0 taker is 24 bps round trip, 2.7× the whole bid-ask. Arm B BLOCKED_DATA (no funding source). Arm C NO-GO (null at 300 s; the binding constraint was theta's 5-min ladder cadence, not the collector). Never registered in production, so the docs are the record. The tape collector was stopped by the closing session on 2026-09-02 13:28Z — re-verified from production on 2026-09-07, which is why the parked "turn the collector off" line is gone rather than done ([WS-010 § The collector is off](WS-010-perp-v1-pre-registration.md#the-collector-is-off)) |
| [WS-011](WS-011-marktangle-conditional-reversion.md) | MARKTANGLE: conditional reversion in recurring binary families | 2026-09-03 | Closed by operator decision with its successor. Registered and RETIRED in one act (`CLOSE_OUT_RETROSPECTIVE`, package `marktangle-reversion`) — it had never been in Experiment OS at all, despite three documents saying PAUSED at PROBE. Both gates HOLD under the contract's own frozen thin-holdout rule (best families 13–27 entries against a floor of 100). The directional finding stands as recorded history: daily crypto threshold families are momentum machines, not coin flips |
| [WS-013](WS-013-marktangle-2-conditional-dependence.md) | MARKTANGLE-2: conditional dependence alpha (two tracks, pre-registered) | 2026-09-03 | Both tracks closed by operator decision; RETIRED via `CLOSE_OUT_RETROSPECTIVE` (package `marktangle-2`, which ADOPTS the production contract rather than re-registering). **Track A FAIL** — refuted in all three adequately-powered classes; `prev_dir × ln(k)` is zero within noise twice and wrong-signed once, so streak length carries nothing. **Track B BLOCKED_DATA** — persistence is real, strongly forecastable (98.3% holdout accuracy) and unpriceable: 16 two-sided quotes in ~2,000 fetches, 0% coverage against a 50% floor. Both depart from the instrument's printed HOLD/HOLD; the departure is an operator conclusion, recorded as one in `marktangle2.CLOSE_OUT_VERDICTS` |
| [WS-012](WS-012-ops-channel-vnext.md) | Ops channel vNext: reliability, introspection, verified operations | 2026-09-02 | Merged [#294](https://github.com/50thycal/kalshi_bot/pull/294)/[#306](https://github.com/50thycal/kalshi_bot/pull/306)/[#313](https://github.com/50thycal/kalshi_bot/pull/313). Deployed to `ops` and validated with a real round trip (green on success, RED on a deliberately bad request — the P1 fix proven in production). Two follow-ups carried past close, neither blocking: the live `ops` ruleset is still unverified (needs admin-scoped token), and an XOS issue for the #313 defect is prepared but held for LIVE_OPS to send when safe |
