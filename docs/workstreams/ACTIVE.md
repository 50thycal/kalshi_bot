# Active Work

The project's active-work control board — what is being designed and built right now, and
where each effort is. Read it first on a continuation.

**Updated:** 2026-10-16 (WS-027 opened on the owner's 2026-10-15 design decision) · **Build OS v0.12**

| ID | Workstream | Phase | Status | Current Next Step | Related PR |
|---|---|---|---|---|---|
| [WS-027](WS-027-win-set-shared-canary.md) | WIN-set live canary beside Kmmsell10: fill-level live attribution, shared-market pair, combined bound | REVIEW | Active | Per-fill attribution ACTIVE (2026-10-16 09:34Z). WIN-set paper accruing on `Pmmsell10`; promotion gate expected ~2026-10-22 → preflight → ARM_CANARY with the owner present (`docs/MMSELL_WIN_SET_CANARY.md` §6) | [#644](https://github.com/50thycal/kalshi_bot/pull/644), [#645](https://github.com/50thycal/kalshi_bot/pull/645) merged |
| [WS-026](WS-026-global-experiment-monitor.md) | Global experiment monitor: one snapshot, daily check-in, status page | REVIEW | Active | #637 merged; first production snapshot ran end to end. Owner merges the probe-fix PR; the routine's first 07:45 CT run (2026-10-16) closes check 5. Open: D1 catalog URL, D2 desk diagnostic secret | [#637](https://github.com/50thycal/kalshi_bot/pull/637) merged; probe-fix PR (this branch) |
| [WS-025](WS-025-desk-service-v3.md) | Desk service 3.0: unattended desk agents (subscription runners, event wakes, dry-run week) | BUILDING | Active | PR A (service: cycle cap, wake signal, watchdog, dry run, ntfy; all off by default) in review; next PR B (ChatGPT runner + Claude routines), then the operator activation runbook (hard stops) | #580 merged (design); PR A (this branch) |
| [WS-024](WS-024-railway-ram-budget.md) | Railway RAM budget: ≤ $15/month, $10 stretch | BUILDING | Active | Step 1 merged and live (programme discovery in `main`, 2026-10-08). Owner merges the step-3 PR (live-dash idle refresher); then sleep on live-dash, 24h check-1 + after-RAM, then D2 (catalog volume full in ~10–12 days) | [#547](https://github.com/50thycal/kalshi_bot/pull/547), [#553](https://github.com/50thycal/kalshi_bot/pull/553) merged; step-3 PR (this branch) |
| [WS-023](WS-023-market-catalog.md) | Market catalog and strategy evidence service | REVIEW | Active | Retention (DEC-038, 2026-10-21): dry run of the backup/assessment/revision thinning, then the owner picks `notes` or `final_only` and sets execute. `CATALOG_HISTORY_POLICY=selective` is measured as no disk lever under v2 encoding. Historical binding/calibration remain subsequent stages | #541/#542/#544/#552/#556/#561/#562/#563/#564/#565/#566/#568 merged; [#586](https://github.com/50thycal/kalshi_bot/pull/586) draft |
| [WS-022](WS-022-desk-research-v2.md) | Desk research v2: open market discovery, open web evidence, fresh round | REVIEW | Active | Owner merges/deploys to desk-service, then runs `docs/desks/ROUND_2_CUTOVER.md` (round switch and start are hard stops; balances carry, same DB) | v2 PR (this branch) |
| [WS-021](WS-021-autonomous-desks.md) | Autonomous ChatGPT and Claude desks | REVIEW | Active | Review quote validation clock repair PR; owner decides desk-service deployment after merge | [#517](https://github.com/50thycal/kalshi_bot/pull/517) |
| [WS-020](WS-020-liquidity-incentive-shadow.md) | Liquidity-incentive shadow market maker — Phase 0 instrumentation, no orders | REVIEW | Paused | Paused 2026-10-15 by the owner to make room for WS-026. Shadow stopped since 2026-10-07 (evo service deleted; restoration tracked in WS-024 D1). The live limm-smoke runs on under XOS, unaffected by this pause and watched by the WS-026 monitor. Resume: operator day-one check 3 before any `est_` reward figure is believed | [#415](https://github.com/50thycal/kalshi_bot/pull/415), [#416](https://github.com/50thycal/kalshi_bot/pull/416) merged |
| [WS-019](WS-019-mmsell-queue-fill-telemetry.md) | MMSELL queue / fill telemetry — Phase 1 instrumentation, no behaviour change | RUNNING | Active | Merged and collecting; let ticks accrue and read `execution_telemetry` weekly. Operator decision open: raw-event retention | [#411](https://github.com/50thycal/kalshi_bot/pull/411), [#412](https://github.com/50thycal/kalshi_bot/pull/412), [#417](https://github.com/50thycal/kalshi_bot/pull/417), [#419](https://github.com/50thycal/kalshi_bot/pull/419) |
| [WS-018](WS-018-spot-perp-funding-census.md) | Spot/perp funding carry: $2,000 primary / $4,000 ceiling | REVIEW | Blocked | Sizes/sources resolved; API rate conversion and permission for approved-transport quote check after HTTP403 remain | [#402 merged](https://github.com/50thycal/kalshi_bot/pull/402) |
| [WS-002](WS-002-mmsell-settlement-taxonomy-repair.md) | MMSELL settlement-taxonomy repair | REVIEW | Blocked | Merge guard: verify in XOS that the revision is registered + impacts accepted | [#257](https://github.com/50thycal/kalshi_bot/pull/257) |
| [WS-003](WS-003-mmsell-noncrypto-settlement-mode-paper-design.md) | MMSELL non-crypto settlement-mode paper design | DECIDE | Blocked | Waiting on WS-002, the crypto-exclusion defect, and the event-correlation measurement | — |
| [WS-004](WS-004-live-canary-gate-addressing.md) | Reconciling the recurring blocked-gate anomaly (historical canaries) | DECIDE | Active | Put the reporting-layer options to the operator (explain / accept-condition / live with it) | — |
| [WS-005](WS-005-freeze-dark-window-universe.md) | A testable universe for the freeze dark-window hypothesis | EXPLORE | Blocked | Commit to a source-based universe search, or abandon explicitly | — |
| [WS-006](WS-006-evo-search-capability.md) | Evo historical search capability (agents search their own strategy space) | REVIEW | Active | D1 CLEAN 2026-08-28 (both runs identical across processes, all three legs). D2 is the remaining prerequisite and is Platform Change Review work; no prospective cohort without it plus explicit operator approval | [#261](https://github.com/50thycal/kalshi_bot/pull/261), [#262](https://github.com/50thycal/kalshi_bot/pull/262), [#263](https://github.com/50thycal/kalshi_bot/pull/263) |
| [WS-007](WS-007-mmsell10-live-canary.md) | mmsell10 live canary + exact paper twin | REVIEW | Active — **LIVE** | `Hmmsell10` ARMED 2026-10-04 02:18:55Z (random 1-vs-3 split, `contestkey=split`); re-cut to `Jmmsell10` (epoch 3) with the XOS-000038 cap-scope fix on (`DEC-027`); tennis and KXBTCD paused on real money (XOS-000039/41). Next (`DEC-033`, approved 2026-10-11): successor `Kmmsell10` — 3-lot on the slow-information cell, 1 elsewhere (`docs/MMSELL_CELL_SIZE_CANARY.md`); needs the XOS-000040 held-market gate (#583) live first, then REGISTER_PACKAGE → ARM_CANARY cutover | [#518](https://github.com/50thycal/kalshi_bot/pull/518) |
| [WS-009](WS-009-livedash-load-and-selection.md) | Live-vs-paper dashboard: load cost, run selection, retired-pair landing | REVIEW | Paused | Paused 2026-10-11 by the owner to make room for WS-025 (D4). Remaining item unchanged: confirm on the deployed livedash that first paint is seconds, not half a minute | [#271](https://github.com/50thycal/kalshi_bot/pull/271) |
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
WS-017 temporarily added one explicitly requested bounded probe; it completed the same
day and left this board. The pre-existing five Active rows are unchanged.
WS-019 was opened on an explicit operator handoff (2026-09-16); it is the sixth Active row
and the WS-016 D1 question (which row pauses) is still the operator's to answer.
WS-024 was opened on an explicit operator handoff (2026-10-08, Railway RAM cost); it adds one Active row on top of the existing overage, and the WS-016 D1 question still stands.
WS-020 was opened on a second explicit operator handoff the same day (the liquidity-incentive
Phase 0 build); it is the seventh Active row. The board is three over the limit and no session
has been asked which rows pause — that remains the operator's WS-016 D1 answer.

---

WS-021 is the eighth Active row, admitted on Calvin's explicit 2026-09-20 build request.
The board is four over its declared limit. This admission does not silently pause an existing
workstream or resolve WS-016's allocation question.
WS-022 is the ninth Active row, admitted on Calvin's explicit 2026-10-01 handoff (DEC-024). The
board is five over its declared limit; no existing row is paused by inference.

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

- WIN-set series additions for `Nmmsell10`, **KXTRUMPMENTION first**, then KXAPRPOTUS and the other mention series as their samples grow: needs a `SERIES_TYPES` entry, a manifest graduation (which also opens the series to `Kmmsell10` — a real-money change) and a new `mmsell-win-set` Version. Evidence: `docs/MMSELL_WIN_SET_EXPANSION_RESEARCH.md` §2 (Calvin, 2026-10-18: "add that in eventually").
- `Alimm1` sweep-aware resting sell: after a recorded `book_sweep` on a held market, hold the resting sell's price instead of stepping it down. Ready when the `book_sweep` / `book_sweep_followup` log (§9.60) has ~2 weeks and enough sweeps to say whether bids recover; thesis §9.61 "Parked" has the check to run (Calvin, 2026-10-17).
- `docs/PROJECT_MODEL.md` incentive section says nothing in the trading path reads `incentive_*`; stale — `Alimm1` and the executor close-time slot rule read `incentive_programs` (PR #547, 2026-10-07).
- `Hmmsell10` readout script: compute S0–S3 (size arms recomputed by `ticker_size`) and the contest-key report from `docs/MMSELL_SIZE_SPLIT_CANARY.md` §5, ops-allowlisted; needed before the first readout, ~2–3 weeks after arming (WS-007, PR #518, 2026-10-03).
- Desk PDF evidence capture through an isolated, time-limited parser; refused today so untrusted PDFs are never parsed inside the real-money desk service (WS-022, 2026-10-01).
- ECON-REACT script: enumerate settled econ prints by `series_ticker` (KXCPI, KXPAYROLLS, KXU3, KXGDP, KXJOBLESS/KXICSA) instead of paging all settled events — the 2026-09-29 re-run reached only 13 settled prints (Calvin, 2026-09-29).
- Purchased-tail MMSELL hedge: explore buying a farther-tail YES against a NO threshold position on the same contest with matching settlement rules; evaluate interval loss and hedge cost (Calvin, 2026-09-12).
- Per-book live risk budgets, **prerequisite for a second concurrent live canary**: `MAX_TOTAL_EXPOSURE` and `MAX_DAILY_LOSS` are account-wide and are checked BEFORE the per-ticker dedup gate, so whichever book the scan reaches first can exhaust the budget and gate the other out of the whole slate — the same scan-order bias `part=i/n` (#413) fixed per-ticker, one level up, where a ticker partition cannot reach. Fix is per-book budgets checked IN ADDITION to the account-level ones, never instead of. Touches the live risk path, so operator-gated. Do not arm two live books without it: the second canary's numbers would be the first one's leftovers. Context: `docs/MMSELL_BOOK_PARTITION.md` "What this does NOT fix" (Calvin, 2026-09-16).

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
- NFL preseason overs: MMSELL paper on KXNFLTOTAL was positive in every price band in August 2026
  only (`docs/MMSELL_NFLTOTAL_ITF_FORWARD_TEST.md` §5); pre-register an August 2027 cell before then.
- Classify `KXNFL1HFT` (half-time/full-time double, truly `game_prop`) and `KXNFL1H` (would
  shadow it by longest-prefix). Held out of `football_coverage_2026_10_12` because `game_prop` is in
  `Kmmsell10`'s `bigtypes`; needs the cell-size experiment's decision first
  (`docs/mmsell_taxonomy_repair/PLATFORM_IMPACT_20261012.md` §1).
- Raise an Experiment OS issue for the unregistered 2026-09-06 `KXUE*` taxonomy change (commit
  `d166fb26`): it moved `Tmmsell5`/`Tmmsell6` universes mid-epoch with no recorded boundary. Owner:
  `mmsell-type-tight`'s owner; never back-dated by a revision (same doc, §4).
- ITF men tour-level split (M15 vs M25) from catalog event titles; worth reading only if the
  KXITFMATCH forward test in the same document passes.

## Recently completed

| ID | Workstream | Completed | Outcome |
|---|---|---|---|
| [WS-017](WS-017-passive-perp-probe.md) | Passive BTC/ETH perp price-return census | 2026-09-12 | Instrument shipped and run through read-only ops; frozen result recorded in census/journal/scorecard. No collector or paper deployment. Two unselected strategies parked. [#397](https://github.com/50thycal/kalshi_bot/pull/397) |
| [WS-015](WS-015-mmsell10-queue-aware-cancel.md) | mmsell10 queue-aware cancellation: shadow instrument + pre-registered contract | 2026-09-07 | Shipped, ran three days in shadow, **thesis falsified, experiment RETIRED 2026-09-10** (`qac-retire-20260910-1`). The frozen rule's 10% fill-probability threshold did not hold out of sample: 27.5% of would-cancel orders filled (bar 15%) at 1.65c forgone each (bar 1.0c), and the open-position cap never bound on the live book (0%, bar 50%) — the capacity premise came from paper canaries, not the book the instrument observed. Three of four promotion clauses failed; `shadow_kill` never tripped. Nothing was ever cancelled and no real money was touched. Queue telemetry (100% coverage, ~7,800 decisions) survives and is reusable; so does a live-safeguard defect found incidentally and fixed by Live Ops (XOS-000028). PRs [#364](https://github.com/50thycal/kalshi_bot/pull/364), [#365](https://github.com/50thycal/kalshi_bot/pull/365), [#373](https://github.com/50thycal/kalshi_bot/pull/373). Verdict and what survives: §7b of the thesis doc |
| [WS-001](WS-001-build-os-adoption.md) | Build OS adoption, and the v0.4 → v0.12 migration | 2026-09-06 | Two acts on one thread. **v0.4 adopted** in [#258](https://github.com/50thycal/kalshi_bot/pull/258) (merged 2026-08-24) — the framework block, three memory layers, the board, templates, the wired PR handoff, `DEC-001`'s authority boundary; the row then sat in a false `REVIEW` for eleven days awaiting an independent verdict that could not exist. **v0.12 adopted** here, with the four decisions in `DEC-011`: operating mode `solo`, a stated split between the session identity header and the Owner Result, an active-work limit of 4 counting `Active` only, and `SHIP` as a development-gate report whose sequel is a guard rather than an authorization |
| [WS-008](WS-008-epoch-deployment-continuity.md) | An epoch boundary must not silently stop the books (XOS-000011) | 2026-09-03 | Merged [#268](https://github.com/50thycal/kalshi_bot/pull/268) and repaired in production; XOS-000011 RESOLVED with all five validation checks passed. Whether the engine change warrants a Platform Revision is Platform Change Review's question, not this workstream's |
| [WS-010](WS-010-perp-v1-pre-registration.md) | PERP-V1: a research surface for Kalshi perpetual futures | 2026-09-02 | Closed on a **COST** finding. Arm A FAIL — premium reversion is real (+5.63 bps/trade pre-fee, 913 obs, against a −10.13 control) and unreachable, because tier-0 taker is 24 bps round trip, 2.7× the whole bid-ask. Arm B BLOCKED_DATA (no funding source). Arm C NO-GO (null at 300 s; the binding constraint was theta's 5-min ladder cadence, not the collector). Never registered in production, so the docs are the record. The tape collector was stopped by the closing session on 2026-09-02 13:28Z — re-verified from production on 2026-09-07, which is why the parked "turn the collector off" line is gone rather than done ([WS-010 § The collector is off](WS-010-perp-v1-pre-registration.md#the-collector-is-off)) |
| [WS-011](WS-011-marktangle-conditional-reversion.md) | MARKTANGLE: conditional reversion in recurring binary families | 2026-09-03 | Closed by operator decision with its successor. Registered and RETIRED in one act (`CLOSE_OUT_RETROSPECTIVE`, package `marktangle-reversion`) — it had never been in Experiment OS at all, despite three documents saying PAUSED at PROBE. Both gates HOLD under the contract's own frozen thin-holdout rule (best families 13–27 entries against a floor of 100). The directional finding stands as recorded history: daily crypto threshold families are momentum machines, not coin flips |
| [WS-013](WS-013-marktangle-2-conditional-dependence.md) | MARKTANGLE-2: conditional dependence alpha (two tracks, pre-registered) | 2026-09-03 | Both tracks closed by operator decision; RETIRED via `CLOSE_OUT_RETROSPECTIVE` (package `marktangle-2`, which ADOPTS the production contract rather than re-registering). **Track A FAIL** — refuted in all three adequately-powered classes; `prev_dir × ln(k)` is zero within noise twice and wrong-signed once, so streak length carries nothing. **Track B BLOCKED_DATA** — persistence is real, strongly forecastable (98.3% holdout accuracy) and unpriceable: 16 two-sided quotes in ~2,000 fetches, 0% coverage against a 50% floor. Both depart from the instrument's printed HOLD/HOLD; the departure is an operator conclusion, recorded as one in `marktangle2.CLOSE_OUT_VERDICTS` |
| [WS-012](WS-012-ops-channel-vnext.md) | Ops channel vNext: reliability, introspection, verified operations | 2026-09-02 | Merged [#294](https://github.com/50thycal/kalshi_bot/pull/294)/[#306](https://github.com/50thycal/kalshi_bot/pull/306)/[#313](https://github.com/50thycal/kalshi_bot/pull/313). Deployed to `ops` and validated with a real round trip (green on success, RED on a deliberately bad request — the P1 fix proven in production). Two follow-ups carried past close, neither blocking: the live `ops` ruleset is still unverified (needs admin-scoped token), and an XOS issue for the #313 defect is prepared but held for LIVE_OPS to send when safe |

WS-023 is admitted on Calvin’s explicit 2026-10-05 build request. This adds a tenth Active row; no other row is paused by inference.

WS-023 now has a named external credential unblocker and is Blocked; the admission did not pause another row.

WS-026 was admitted on Calvin's explicit 2026-10-15 request; asked which row pauses, he chose WS-020 (its shadow was already stopped). The board stays over its declared limit; no other row changed.

WS-027 was admitted on the owner's explicit 2026-10-15 handoff (WIN-set canary beside Kmmsell10). It adds one Active row; no other row is paused by inference, and which row pauses remains the owner's call.
