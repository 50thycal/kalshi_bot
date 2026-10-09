# WS-023 — Market catalog and strategy evidence service
**Build OS:** v0.12 · **Phase:** REVIEW · **Status:** Active
**Implementation actor:** Codex · **Review:** solo, owner acceptance pending
**PR:** [#541](https://github.com/50thycal/kalshi_bot/pull/541) merged; operational follow-up [#542](https://github.com/50thycal/kalshi_bot/pull/542).
Mission: standalone Railway catalog service and additive existing-data import.
Owner direction: Calvin approved D1–D9 A and requested implementation on 2026-10-05 in ChatGPT.
Build Card, spec and pre-implementation impact plan: [MARKET_CATALOG_BUILD.md](../MARKET_CATALOG_BUILD.md).
Open decisions: numerical confidence calibration remains unset; no permission to trade or scale follows a catalog score.
Assumptions: source trading data is read-only, local persistent storage belongs only to the catalog, existing workers do not consume new assessments.
Implementation: standalone service deployed on Railway, registry/discovery verified, initial 200 paper / 200 live records imported through the existing SELECT-only ops channel. Calvin expanded the full 1 GB volume to 20 GB; disk-full restarts recovered. The supplied source URL is refused by the privilege guard. Follow-up preserves content/IDs with lossless compression and a pre-upgrade backup, plus storage and sanitized permission diagnostics. 21 catalog tests pass. See service documentation and follow-up PR for deployment/head. No owner acceptance verdict is inferred from the merge.
Current continuation: the owner corrected the SELECT-only bot_readonly URL and redeployed.
The credential blocker is resolved. Verified runtime observations on 2026-10-08 showed
130,492 paper rows and 2,823 live rows, with live source-ID count/hash matching through
source ID 4,416. These are coverage observations, not scientific qualification.
Next step: review the scoring-readiness continuation described in
[MARKET_CATALOG_SCORING_READINESS.md](../MARKET_CATALOG_SCORING_READINESS.md), then verify
its catalog-only deployment and begin bounded structured review/calibration preparation.
No consumer cutover or trading action is implied.

Historical deployment notes below describe earlier blockers; they are not current status.

Repair validation: Railway deployment 5912e85b-c964-4bb9-af26-865ab443875b SUCCESS, pinned executable commit 420abc498bd2042a4bbad1db1bce03f511ac45e6. Checked pre-compression backup retained (1,269,624,832 bytes); discovery resumed and evidence/assessment seed persisted. Follow-up finalization is documentation only; no independent review or owner verdict claimed.

Continuation: source audit found 126,380 paper rows across 746 series and 2,741 live fills across 249 series, versus the catalog's 200/200 seed. Added source/local ID fingerprint verification so an empty page or a matching count alone cannot claim complete coverage. Coverage and compression acquire the write lock before reading so concurrent imports cannot corrupt a checkpoint or be overwritten by re-encoding. Observed provider 429s prompted persisted shared Retry-After/exponential backoff without blocking independent jobs. 33 catalog tests pass. Baseline and credential diagnosis: [MARKET_CATALOG_SOURCE_COVERAGE.md](../MARKET_CATALOG_SOURCE_COVERAGE.md). Deployment/head verification is recorded on PR #542; earlier finalization is reopened by executable changes.

Final continuation validation: deployment 949a9124-86cc-4729-85c7-ed5d4b18fe4a SUCCESS on executable commit 118b14b9cdfa2a4842375fec9e4807d274e0b410; backup reused, public discovery returned 200 and preserved cursors, source superuser remained refused. Current finalization is documentation only. No independent review or owner acceptance verdict is claimed. The external credential blocker remains.

## Dashboard continuation — 2026-10-06

Calvin authorized a simplified Railway dashboard for the catalog. Scope is a
read-only operator view inside the existing service, with public static assets
and authenticated API reads. It shows discovery, structured series review
coverage, evidence backfill/reconciliation, uncalibrated scoring, consumer status,
storage, job freshness, and a bounded assessment table. It does not mirror
Experiment OS lifecycle or add live trading controls.

Acceptance checks: shell works at the Railway domain root on desktop/mobile;
unauthenticated requests cannot read catalog data; no token is embedded or
persisted in browser storage/URLs; imported history and unknown scores are not
represented as qualification; errors/stale snapshots remain visible; existing
catalog checks pass. Source credential blocker remains separate from dashboard
release readiness. Owner configures the public domain and deploys the dashboard
release; no production deployment is performed in this continuation.

Build OS compatibility checked against canonical v0.12 on 2026-10-06.

Dashboard implementation checks: 34 catalog tests plus 20 session-system checks passed; Ruff, JavaScript syntax, compile and diff checks passed. DOM behavior checks passed for authenticated rendering, unknown scores, escaped text, no token persistence, stale errors, disconnect and invalid tokens. Browser visual verification remains unperformed: agent-browser could not start and Chromium download failed in this environment. Owner acceptance and production deployment remain pending.

Dashboard PR: [#544](https://github.com/50thycal/kalshi_bot/pull/544). Executable implementation head: `48a17e7970435dd20a99159242fd2a96a170cc87`. Owner acceptance, visual verification, and Railway deployment pending. Existing full-history credential blocker remains. No independent review claimed.


## Evidence continuation — 2026-10-08

Calvin explicitly authorized review migration, attributable live economics and scoring work.
All 140 registry rows migrate with provenance; 38 signatures remain partial historical reviews.
The isolated catalog now reconstructs eligible exclusive-owner binary ledgers from actual costs,
records explicit blockers, refreshes live market outcomes including archived markets, and exposes
strategy-specific evidence requirements. Numerical confidence remains withheld pending verified
independence, market rule binding, exchange coverage, calibration and forward validation.
Assessment refresh is atomic and paginated API reads retain bounded output memory.
Acceptance checks and deployment/rollback are in the evidence document. No independent review
or owner acceptance is claimed; current production has not yet run this executable continuation.

Validation: 89 local tests passed, plus Ruff, JavaScript syntax and diff checks. The
150,000-context synthetic storage/API benchmark is recorded in the evidence document;
it is not a production economics audit. Compatibility remains canonical Build OS v0.12.

Evidence PR: [#552](https://github.com/50thycal/kalshi_bot/pull/552).
Initial executable commit: `3a2f114c3597adb5dee816398507dd4e9e5aa53c`, superseded by
the fill/order identity follow-up in PR #552. Final executable validation: 89 tests. Owner acceptance and catalog-only release pending.


## Production verification and lifecycle repair — 2026-10-08

Owner deployed merged default commit `2dc466845801bd092a39ee6a05de40518660e53e`:
Railway deployment `b450a0dd-55fb-4133-9e73-3b578d277c9d` SUCCESS, online with one replica.
Startup reused the checked backup. Live ownership replay imported its first 1,000-row page,
public live outcome refresh fetched five markets, and evaluation processed 146,372 contexts.
These prove pipeline execution, not successful attribution or completed replay.

Direct public market verification found `KXAAAGASD-26AUG11-4.015` with `status=finalized`,
binary $1 notional, result=no, explicit $0 yes payout and a settlement timestamp. The new
catalog accepted only settled; finalized markets were therefore falsely blocked. Repair
accepts both final lifecycle names, consistent with the existing desk settlement adapter,
while still requiring non-provisional final payout/time and all ownership/fill/cost checks.
It adds sanitized migration totals and live-attribution/blocker reason counts to runtime logs.
No source writes, trading controls, confidence qualification or metric formula changes.
Owner acceptance/release of this follow-up remain pending; no attribution totals inferred.

Latest post-release logs at 04:52 UTC: 130,492 paper rows, 2,824 live fills, 14,717 series,
2,076,472 markets and 146,372 current assessment contexts. Structured reviews remain zero.
Database 8.83 GB; volume free 9.57 GB. No runtime error logged in the checked release window.
The storage diagnostic took about five minutes after evaluation; pipeline refresh is currently
slower than its nominal loop interval. This observation does not certify new replay completion.
Follow-up validation: 92 catalog/economics/session tests, Ruff and diff checks passed.

## Canonical fill direction investigation — 2026-10-08

Task-specific catalog continuation, authorized by Calvin. Owner reconnected the
Railway source, removing its old commit pin. Deployment
`cb885981-d853-4ebe-8099-bdc5c7267147` succeeded on default commit `076e7b5`,
which includes #556. Runtime verified 140 migrated series / 38 historical signatures.
The first v2-era diagnostic (before this repair) blocked all 2,771 live markets on
literal order/fill action comparison. These are catalog diagnostics, not XOS verdicts.

Two SELECT-only ops audits established the defect on all 2,832 scoped source fills:
bot intent NO/buy; deprecated fill labels NO/sell; raw exchange order YES/sell;
canonical fill and order both NO/ask. Raw order IDs, markets, stored NO prices,
complementary prices and NO order limits matched on every audited row. Sanitized
receipts: `ops/results/catalog-order-fill-shape-20261008-1456.txt` and
`ops/results/catalog-canonical-direction-20261008-1458.txt`. The request was reset
to noop. No account identifiers or raw private payloads were exported.

Repair stays inside the catalog: import raw order evidence, replay once, verify
canonical direction plus raw identity/price proof, and calculate cashflows using
the bot order's held-contract intent. Missing proof fails closed. Original records
remain intact. Method version becomes exclusive-binary-ledger-v2; confidence stays
null and qualification false. No shared executor, source database, XOS metric,
experiment, trading consumer or exposure changes; no Platform Revision activated.

Validation: 119 catalog/economics/session checks passed plus Ruff. Regressions cover
the production mismatch, all four intents, complementary-leg exits, identity and
price conflicts, legacy ambiguity, actual-fee/loss guards and one-time replay.
Framework compatibility verified against canonical Build OS v0.12.
No independent review or owner acceptance claimed. Next: owner accepts this repair
PR, deploy catalog only, verify complete replay and inspect remaining settlement /
ownership / actual-cost blockers. No numerical score or consumer cutover follows.

## Fractional source evidence repair — 2026-10-08

PR #561 is merged and deployed at `8a94eebec5cc840ca78ce65c9773d1fccdb65e04`,
Railway deployment `0d6b73d8-18f9-4615-a3e0-cefe14597fa8`. Direction-proof replay
caught up with 132,271 paper / 2,833 live source rows. At 16:51 UTC, source-ledger
economics attributed 1,128 of 2,772 live markets and blocked 1,644; reason counts
overlap and most remaining blockers concern settlement evidence. These are catalog
diagnostics, not exchange-account reconciliation or an experiment verdict.

Calvin authorized continued investigation. SELECT-only proof audit at 16:58 UTC
found 85 fills across 47 markets whose raw fractional quantities were rounded by
the source convenience column; all 85 differences reproduced the executor's
rounding, including 41 rounded-zero fills. All 2,833 scoped fills had explicit
nonnegative exchange fees. Receipt:
`ops/results/catalog-fractional-proof-20261008-1700b.txt`.

The isolated v3 ledger restores exact raw quantities only after canonical identity,
direction and price proof, and only for positive counts with at most two decimals
whose source rounding is reproducible. Original records remain intact; unexplained
differences still block. Versioned v3 snapshots preserve v2 history. No additional
import replay is needed. No shared executor, XOS metric, source database, exposure
or consumer changes; no Platform Revision activated.

Ownership proof at 17:00 UTC distinguishes 17 markets with multiple executed owners
and 22 with multiple order owners but only one executed owner. All 39 remain blocked
under the existing exclusive-owner policy. Receipt:
`ops/results/catalog-shared-owner-proof-20261008-1705.txt`. Ops returned to noop.
The public outcome sweep continues successfully in five-market batches, including
archived-market fallback. One fill-after-settlement exception remains blocked;
bounded public ticker diagnostics make it traceable after this release.

Validation: 135 catalog/economics/session checks passed plus Ruff. Tests cover
fractional rounding ties/zero, partial exits, invalid quantities, unchanged evidence,
ownership/identity/actual-fee guards and bounded diagnostic logs. No independent
review or owner acceptance claimed. This PR does not complete WS-023. Next: owner
accepts/merges the catalog-only repair, then verify deployed v3 counts and remaining
exceptions. Confidence stays null, qualification false; calibration and consumer
cutover remain subsequent work under the approved boundary.

Fractional repair PR: [#562](https://github.com/50thycal/kalshi_bot/pull/562).
Executable validation head: `7397c6cbbe01f9d823eb1c4ff3ae3d66eb994155`.
Merge-finalization is documentation-only; WS-023 remains REVIEW / Active on merge.
Review State: solo mode, pending owner acceptance; no independent review claimed.
Owner merge and catalog-only runtime verification remain pending.

## Execution-clock verification and repair — 2026-10-08

Mission: verify #562's production attribution and resolve the remaining catalog execution-time
conflict using preserved evidence. Non-goals: source/executor/XOS changes, mixed-owner allocation,
numerical confidence or consumer activation. Material interrupts: unproven identity/time or future
data must stay blocked. Finish condition: tested isolated repair and reviewable release handoff.
Acceptance checks: distinguish execution from collection, retain original evidence, require
agreeing timezone/epoch proof, preserve actual-after-settlement/future/conflict guards, order
cashflows and measure attributable duration by execution time, preserve old snapshot history.

#562 merged and deployed on `db5ec4534a0be32944ef37b6f7e33da9e6e56509`, Railway
`8f82568e-695f-42be-9a1f-85bcdc52a15b`; finalized-head GitHub CI passed. At 18:18 UTC:
1,358 / 2,775 live markets attributable; 1,417 blocked. All 85 fractional fills restored,
zero quantity/actual-cost exceptions. Settlement remains the main blocker; 39 ownership
exceptions stay blocked. The later unrelated #559 merge was skipped by catalog watch paths;
this does not undo the active #562 deployment. No independent review/acceptance inferred.

SELECT-only receipts `catalog-timestamp-proof-20261008-1821` and
`catalog-execution-time-proof-20261008-1823` proved source `filled_at` is collection time:
the executor passes None, and insertion supplies its current time. All 2,836 scoped fills
have agreeing raw exchange timestamps and later source collection times; maximum lag is
70,975.799750 seconds. The diagnosed public ticker executed before settlement but was
collected afterward. Ops returned to noop. Exact facts and public verification are in the
evidence document; no private identifiers or payloads were exported.

The v4 catalog restores exchange execution time after identity/direction/price/quantity proof,
requires matching explicit timezone/epoch fields, rejects invalid/backward collection time,
and retains future and true after-settlement guards. Legacy clock evidence is not claimed
verified. Cashflow ordering and attributable evidence duration now use execution time.
Method-scoped v4 snapshots preserve v3 history; no replay or shared-system changes.
Confidence remains null and qualification false. No Platform Revision activated.

Validation: 155 catalog/economics/session checks, Ruff and whitespace checks passed.
Review State: solo, pending owner acceptance; no independent review claimed.
WS-023 remains REVIEW / Active. Next: owner merges the clock repair, then verify v4
restoration/exception counts. Structured review, calibration and consumer cutover remain
subsequent work within the approved boundary.

Execution-clock PR: [#563](https://github.com/50thycal/kalshi_bot/pull/563).
Executable validation head: `80fba0ea676e482f65943f000792d6c1f223f588`.
Documentation-only merge-finalization pushed; WS-023 remains REVIEW / Active on merge.
Review State: solo, pending owner acceptance; no independent review claimed.
Production v4 restoration counts and the diagnosed timestamp exception remain release checks.

## Review and calibration preparation — 2026-10-08

Calvin authorized the next scoring-readiness step after #563 release verification.
Mission: exact-rule review packets, strategy-specific calibration inputs, authenticated
APIs and an operator dashboard queue. Acceptance checks, non-goals, material interrupts
and finish condition are in the linked readiness specification. This continues WS-023;
no source writes, shared metric changes, consumer activation or expanded live exposure.

#563 merged/deployed on `1f8dd034f27cc825ab84f6f9546d6ac0ca700960`, Railway
`9ca59b12-474d-465b-a9f5-1e5773bb30e2` SUCCESS. At 22:12 UTC v4 diagnostics attributed
2,098 / 2,780 recorded live markets and blocked 682; all 85 fractional restorations
persisted, 2,841 execution times restored, no quantity/fee/time/order-identity exceptions.
Residual settlement and 39 ownership exceptions stay blocked. Source ID count/hash
matched; payload parity and exchange fill coverage were not certified. These are dated
catalog diagnostics, not experiment verdicts. No acceptance is inferred from merge.
Receipt: #563 post-release PR verification.

Packets combine legacy provenance with current exact rule/review bindings. Known fields,
accepted review and confidence stay separate. Priority is recorded live volume, not
profit. Contexts preserve operational tag/arm/side; unresolved identity stays per-market.
Calibration includes blocked inputs, unverified event hints and unassigned forward
partitions. Future strategies register their own provider/check contract. GETs do not
approve reviews or freeze data. WS-023 remains REVIEW / Active; owner review and
catalog-only rollout pending.

Readiness PR: [#564](https://github.com/50thycal/kalshi_bot/pull/564).
Executable validation head: `5f3ec4fd210d03498a4bd435ed46c4c16646ab67`.
167 catalog/readiness/economics/session checks passed; Ruff, JavaScript syntax,
whitespace and readiness DOM smoke checks passed. Production rollout and browser
visual rendering remain release checks. Numerical confidence remains null.
Merge-finalization is documentation-only; WS-023 stays REVIEW / Active on merge.
Review State: solo, pending owner acceptance; no independent party reviewed this change.
Next: owner reviews/merges after finalized-head CI, then verify market-catalog only.

## Bounded classification pilot — 2026-10-09 UTC

Calvin confirmed #564 merged and authorized continuation. Mission/acceptance,
non-goals, interrupt conditions and finish are in
[the pilot](../MARKET_CATALOG_REVIEW_PILOT.md). #564 deployed SUCCESS on merge
`72c7ebe50b8710e6ee6f6d714f3898eade581db5`, deployment
`f6993ead-5525-4e71-9374-bae5af5eafee`; final CI passed. Health, public queue and
401 guards verified. Authenticated production contents remain unverified because
OAuth withholds variable values; no token was exported or requested publicly.

At 02:05:51 UTC diagnostics attributed 2,731 / 2,791 recorded markets, with 60
blocked; source-ID coverage matched at 01:56 UTC (2,853 live / 133,174 paper).
This is a dated catalog observation, not an experiment verdict. Full verification
is on merged #564. A SELECT-only priority query exported only public ticker/count
aggregates and returned ops to noop.

Three official listing/term proposals cover player stats, index averages and mention
phrases. Drafts include all ten semantic keys, explicit unknowns, source fingerprints,
PDF hashes and qualitative risks; they do not approve semantics or supply scores.
The review found listing hashes bind URLs, not linked PDF bytes or historical versions.
Bar v2 explicitly withholds that proof; stale/matching proposed fields never substitute
for approval. Current strict binary ledgers and ownership guards remain unchanged.
Owner review, catalog-only release, authenticated packet check, full-document binding
and subsequent semantic/independence/calibration work remain. WS-023 stays REVIEW / Active.
