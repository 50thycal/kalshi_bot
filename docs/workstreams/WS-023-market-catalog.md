# WS-023 — Market catalog and strategy evidence service
**Build OS:** v0.12 · **Phase:** BUILD_CARD · **Status:** Active
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
Next step: implement the approved selective-history policy on the same draft PR
linked below, once a coding environment is connected. Preserve broad discovery,
existing history and evidence integrity. See
[MARKET_CATALOG_SELECTIVE_HISTORY.md](../MARKET_CATALOG_SELECTIVE_HISTORY.md).
Historical document binding, semantics and calibration remain subsequent stages.
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

Pilot PR: [#565](https://github.com/50thycal/kalshi_bot/pull/565).
Executable validation head: `c808a00313516be9448ca782dfde70266edb38bd`.
170 catalog/readiness/economics/session checks passed; Ruff, whitespace and public-source
snapshot smoke checks passed. Remote implementation tree matched the local files.
GitHub CI is pending; owner review/merge and catalog-only rollout remain release gates.
Merge-finalization changes only this workstream record and its ACTIVE board row.
Review State: solo, pending owner acceptance; no independent review claimed.

## Contract archive continuation — 2026-10-09 UTC

Calvin reported #565 merged and continued the existing task. Mission, acceptance,
non-goals, interrupts, release and rollback are in
[contract capture](../MARKET_CATALOG_CONTRACT_CAPTURE.md). #565 merged/deployed SUCCESS;
finalized-head CI passed, health and runtime jobs verified. At 04:11 UTC, 2,737 / 2,795
recorded live markets attributed, 58 blocked. Dated source-ID coverage and storage
receipts are in the specification and on merged #565. Authenticated production packet
contents and browser rendering remain unverified; no token was exported.

The bounded collector preserves opaque official PDF bytes and dated observations,
deduplicates shared content, exposes failed refreshes and authenticated history, and
adds dashboard collection progress. Current captures do not prove historical versions,
approve semantics or clear confidence requirements. No source/shared metric/consumer
changes or Platform Revision activation. Existing listing/review identities stay intact.
WS-023 remains REVIEW / Active; owner review and catalog-only rollout remain pending.

Archive PR: [#566](https://github.com/50thycal/kalshi_bot/pull/566).
Executable validation head: `9cd731d6ea195c871cd76ccbd9887a5148485294`.
192 document/catalog/readiness/economics/session checks passed; Ruff, whitespace,
JavaScript syntax, dashboard DOM and three-public-document local capture smoke passed.
Remote implementation tree matched all local files. Finalized-head GitHub CI pending.
Merge-finalization changes only this workstream record and its ACTIVE board row.
Review State: solo, pending owner acceptance; no independent review claimed.
Next: owner reviews/merges after CI, then verify catalog-only archive growth and
authenticated capture metadata. Historical effective versions/full-document review
binding remain unresolved; current archive bytes do not satisfy those requirements.

## Storage headroom interrupt — 2026-10-09 UTC

Calvin reported #566 merged. Deployment/CI/health, archive capture and source-ID coverage
were verified; full dated findings are in [storage guard](../MARKET_CATALOG_STORAGE_GUARD.md)
and merged #566's release receipt. Authenticated production contents remain unverified.
At 11:54 UTC only 3.679 GB free remained on the catalog volume while bulk history loaded;
the PDF archive was about 9 MB. FIX NOW under the existing mission: preserve catalog data
before continuing full-document review binding. No volume resize or deletion is performed.

The collector checks real free bytes with persisted bulk/critical hysteresis. Bulk discovery
can pause while imports and bounded collection continue; critical headroom also defers growing
jobs and review/import API writes. Reads/metrics remain available, paused cursors and requests
stay intact, and recovery resumes automatically. The dashboard distinguishes storage pauses.
No source, trading, score/semantic or consumer changes; no Platform Revision activated.
WS-023 remains REVIEW / Active. Owner review/merge and catalog-only verification pending;
additional capacity or an explicitly approved retention/scope policy remains an owner action.

Storage guard PR: [#568](https://github.com/50thycal/kalshi_bot/pull/568).
Executable validation head: `6bd625d3072da59c9686d368060bfb34bd051176`.
199 headroom/document/catalog/readiness/economics/session checks passed; Ruff, whitespace,
JavaScript syntax and dashboard DOM smoke passed. Remote implementation tree matched
all local files. Finalized-head GitHub CI pending. This finalization changes only this
workstream record and its ACTIVE board row.
Review State: solo, pending owner acceptance; no independent review claimed.
Next: owner reviews/merges after CI, then verifies bulk pause, essential progress and
readable authenticated metadata on the catalog only. Capacity recovery is required to
resume bulk history; no automatic resize, deletion or new variables are introduced.

## Selective history direction — 2026-10-11 America/Chicago

Calvin approved option 3: broad inventory with detailed history concentrated on
traded, reviewed and explicitly research-selected contexts (DEC-034). Mission,
acceptance, non-goals, representation/provenance boundaries and release plan are in
[selective history](../MARKET_CATALOG_SELECTIVE_HISTORY.md). Existing history is retained;
no resize, deletion, confidence approval, source write or consumer activation follows.

The current store already deduplicates identical semantic revisions. The policy
must preserve global observed rule changes while reducing future unrelated full
snapshot writes; savings are unmeasured. Scope/historical gaps must stay explicit.
Design checkpoint only: code, tests and benchmark remain pending because the coding
environment is disconnected. Continue on the same draft PR, not a new workstream.
Framework compatibility checked against canonical v0.12 on 2026-10-11 UTC.
Review State: solo, owner acceptance pending; no independent review claimed.

Design handoff PR: [#586](https://github.com/50thycal/kalshi_bot/pull/586) (draft).
Implementation and executable validation pending; resume on this PR when the coding
environment is connected. No current implementation acceptance verdict is issued.

## Selective history implementation — 2026-10-12 UTC

Task-specific catalog role (Claude Code), continuing PR #586 on the same branch. The coding
environment worked; the branch was merged up to default `adbf5cd` (#595) without undoing the
WS-024 cost changes. Build OS compatibility re-checked against canonical v0.12: no delta.

Built, per [selective history](../MARKET_CATALOG_SELECTIVE_HISTORY.md#implementation-record--2026-10-12-utc):
persisted versioned membership with transactional publication, conservative bootstrap and a
startup re-check for rows written by an older image; compact rules/lifecycle/provenance
revisions behind `CATALOG_HISTORY_POLICY=selective` (default `broad_full`, so merge changes no
history depth); bounded hydration with named gaps; authenticated research selection and an
as-of research cohort API; sweeps and source passes that restart only after completing (the
parked events-sweep finding is fixed and removed from the Parked list); per-series evaluation;
a 15-minute counts log. Dashboard shows collection scope.

Dated baseline (2026-10-11 ~23:30 UTC): catalog RAM 24 h avg 0.69 GB / max 1.00 GB at a 1 GB
limit; revisions +380k rows and +0.42 GB of pages per day; 43k evidence markets would be
selected. Synthetic benchmark: revision bytes ×0.77 and file growth ×0.85 under selective
with dictionary encoding (×0.60 / ×0.71 without); evaluation Python peak 286 → 62 MB at 140k
rows with production's series skew, identical results. Neither is a production result. Selective history is a modest disk lever; the WS-024 RAM target is not
claimed met.

Unchanged and still open, in evidence order: exact-rule reviews (zero structured reviews
exist), historical document binding (captures are dated observations only), independence and
strategy-specific calibration. Confidence stays null. No source writes, executor, XOS, shared
metric, exposure or consumer change; no Platform Revision.

Review State: solo, owner acceptance pending; no independent review claimed.
Next: owner merges #586 after green CI; verify bootstrap, cohorts, sweep continuity and 24 h RAM
on the catalog; then the owner decides whether to set `CATALOG_HISTORY_POLICY=selective`.
WS-023 is not complete on merge.

## Exact-rule review drafts, top 13 series — 2026-10-12 UTC

Calvin asked for review drafts for the top live-traded series. 250 series have live MMSELL fills; the top 13 cover 50%
of live-traded markets (ops `catalog-review-priority-20261012a`). PR #597 (merged) added the read-only
`catalog_review_sources` ops script because the sandbox cannot reach Kalshi. Thirteen proposed drafts
(`*-series-draft-20261012`) quote each series' recent listing rules, compare an early traded market and hash both
contract PDFs. Plain-language table: [review drafts](../MARKET_CATALOG_REVIEW_DRAFTS_20261012.md).

Findings:
- PDF text was not extractable, so PDF contingencies are open questions.
- KXMLBKS participation wording changed during trading.
- Tennis non-start settlement differs between Challenger and ITF tours.
- Seven series have non-binary settlement paths.
- KXNFLTOTAL charges maker fees.

Nothing is approved or scored. Approval is the owner's per series, then posted through the authenticated review API.

## Series approvals — 2026-10-13 UTC

Calvin approved the BTC, WTI, MLB total, MLB spread and rain drafts. He also asked that one series
approval cover all of its strikes and dates. The catalog could not do this: a review bound one
exact rules hash, so it reached one market.

This branch adds series templates (`DEC-035`, `rules-template-v1`). One approval binds the series'
contract wording as shown in two exemplar markets. Every traded and future market with the same
wording then reads as reviewed, and reworded markets stay unreviewed.

Tested on the real 26 sample listings:
- BTC, WTI and World Cup goals each produce one wording version across July–October.
- The KXMLBKS participation change produces two.
- MLB total/spread and rain each produce two (an added disclaimer and a reordered source line).
  Both versions are in the approval.

The five approvals are recorded in `approved_reviews.json`. Calvin chose approve-by-merge: his
merge is the sign-off, and the catalog applies them at start through the review API's
validation. The dashboard panel shows each approval's state and can still post one with the
token.

Coverage per series is logged at start (`catalog_template_coverage`) and served at
`/v1/template-coverage`. It names any unapproved wording versions, with sample markets.

`catalog_review_sources` gains `--pdf-base64 --terms-only`, so the contract PDFs can be read with
a full PDF reader.

Still open:
- Read the PDFs and answer the NFL overtime question.
- Approve the remaining series.
- Historical terms, independence and calibration.

No score, qualification, trading, XOS or shared-metric change.

### Production result and PDF answers — 2026-10-13 UTC

After #601 merged, all five approvals bound (`catalog_template_approvals`). Coverage of
live-traded markets:

| Series | Reviewed |
|---|---|
| BTC | 100/104 |
| MLB total | 105/117 |
| MLB spread | 99/104 |
| Rain | 69/71 |
| WTI | 40/41 |
| **Total** | **413/437 (95%)** |

Sampling the rest showed two kinds of gap:
- **Template artifacts**, fixed here by `rules-template-v2`: a trailing space, and a title date read as a strike.
- **Real wording versions**: MLB doubleheader text ("game 1 of the double header") and early-July MLB listings without the disclaimer. These need the owner's approval.

Three samples showed no difference from the public API. The catalog's stored copy differs; v2's
coverage diff will name the field.

Contract PDFs are read; see the answers table in the [review drafts](../MARKET_CATALOG_REVIEW_DRAFTS_20261012.md).
NFL overtime counts. Fair-price settlement on postponement also applies to MLB total and spread.
Rain counts all precipitation.

### Round 2 — all 13 approved — 2026-10-13 UTC

Template v2 in production raised rain to 71/71 and WTI to 41/41. MLB total went to 108/117; the
remaining gaps are the doubleheader and early-July wordings. Six BTC markets differed only in
encoding, which v3 fixes.

Calvin then approved:
- the remaining eight series;
- the MLB wording variants;
- the contract-PDF facts for the first five.

All 13 are recorded in `approved_reviews.json` with 2–4 exemplars each, and the merge applies
them.

### Template v4 — 2026-10-13 UTC

With all 13 bound, production coverage was 697/1,448. Fully or nearly covered:

| Series | Reviewed |
|---|---|
| KXMLBKS | 42/42 |
| MLB total | 117/117 |
| Rain | 71/71 |
| WTI | 41/41 |
| MLB spread | 103/104 |
| NFL | 38/39 |
| World Cup goals | 41/47 |

The gaps:
- **Tennis (12–55 per tour):** tournament names split the wording.
- **KXMLBHR (0/466):** Kalshi edited the exemplar after it was read, which refused the approval.
- **Six BTC markets and one spread market:** the parent changed after capture.

v4 masking and read-template binding address the first two. Coverage now names the third
separately.

### 24 h after #586 — 2026-10-13 02:15Z

- **Resources** (detail in WS-024): catalog RAM 0.83 avg / 1.54 max against 0.69 / 1.00 before, so not improved. CPU fell 0.21 → 0.09.
- **Evaluation:** runs every ~30 min and takes about 35 s.
- **Events sweep:** continuous across eight redeploys, with no reset. Events 214k → 335k; markets 3.59M → 5.44M.
- **Disk:** the database grew about 2 GB overnight while the sweep reached new history, then slowed to about 0.1 GB/h. 15.3 GB free.
- **Template coverage after #605:** 1,408 of 1,448 live-traded markets in the 13 approved series are reviewed.
  - 9 are invalidated by a parent change.
  - About 29 are tennis listings whose names contain lowercase particles, a birth year, "+" or "(moved from …)".
  - 2 are KXMLBHR doubleheader listings with the newer strike type.
  - Owner follow-up (2026-10-13): fix both. `rules-template-v5` joins the tennis variants (all but one listing with a lowercase surname); the KXMLBHR approval `-20261013b` adds the doubleheader exemplar. Production coverage is read from `catalog_template_coverage` after the merge.

### Series scorecard — 2026-10-13 UTC

Calvin asked for a read-only P&L scorecard for the 13 approved series.

`kalshi_bot/catalog/scorecard.py` (`series-scorecard-v1`) adds up the catalog's existing per-market live source-ledger economics (`exclusive-binary-ledger-v4`). It reports:
- net, fees and contracts;
- cents per contract;
- winning markets;
- **independent bets**: markets that share an approved exposure key, such as one game, count as one bet;
- mean and standard error per bet;
- the best and worst bet;
- a split by book and side;
- blocked markets with their reasons.

It is descriptive only: no confidence score, qualification, gate verdict or trading change. It is logged at start and served at `/v1/scorecard`.

### Scorecard snapshot — 2026-10-13 02:49Z (`series-scorecard-v1`, live MMSELL only)

The first production run after #607 merged. It is descriptive only: no score, qualification or trading change.

**Totals:**
- 1,421 attributed markets, 1,015 independent bets, 27 blocked.
- Net **−$15.85** since 2026-07-13; actual fees $0.05.

| Series | Net $ | Bets (won) | Mean ± s.e. per bet | Note |
|---|---|---|---|---|
| KXRAIN | **+5.37** | 39 (38) | +0.138 ± 0.028 | all cities on one date count as one bet; 5 blocked (fair price / unsettled) |
| KXITFMATCH | +3.88 | 122 (117) | +0.032 ± 0.029 | |
| KXNFLTOTAL | **+2.87** | 24 (24) | +0.120 ± 0.017 | no loss yet; 7 blocked (ownership) |
| KXWCGOAL | +1.77 | 4 (3) | — | 4 games; tournament over |
| KXMLBKS | +1.28 | 38 (36) | +0.034 ± 0.038 | |
| KXWTACHALLENGERMATCH | +0.47 | 40 (37) | +0.012 ± 0.043 | |
| KXWTI | −1.64 | 28 (25) | −0.059 ± 0.112 | |
| KXATPCHALLENGERMATCH | −2.76 | 129 (118) | −0.021 ± 0.037 | |
| KXMLBHR | −3.79 | 240 (211) | −0.016 ± 0.026 | 464 markets |
| KXITFWMATCH | −4.75 | 125 (115) | −0.038 ± 0.044 | book J: −6.12 |
| KXMLBSPREAD | −4.91 | 84 (70) | −0.059 ± 0.045 | |
| KXMLBTOTAL | −5.06 | 88 (75) | −0.057 ± 0.050 | |
| KXBTCD | **−8.59** | 54 (47) | −0.159 ± 0.137 | book Lmmsell8: −8.68 over 8 markets; 11 blocked |

How to read it:
- MMSELL sells expensive favourites. It wins small most of the time and loses about ten times as much when it is wrong.
- The standard error understates risk where few or no losses have happened yet (NFL).
- Correlation across series (an MLB total, spread and home-run market on one game) is not merged.
- These are research questions for the Research Lab / Experiment OS; changing what trades is a recorded transition there.

### Paper beside live in the scorecard — 2026-10-13 UTC

Calvin chose option A: put paper results next to live per series, so paper can be checked
against live before it is used to screen untraded markets. `series-scorecard-v2` adds a `paper`
block to every series row. It covers settled MMSELL paper trades at the source-recorded P&L,
with the same settled statuses Experiment OS uses, and groups one bet per approved exposure key.
Live-paper twins are kept apart. `same_markets_as_live` compares paper and live cents per contract
only on markets both traded. Paper and live are never pooled, and paper fills are assumed, so
paper is an optimistic reference. No score, qualification or trading change.

#### Paper-beside-live snapshot — 2026-10-13 12:30Z (after #616)

Read from `catalog_scorecard_series`. Values are cents per contract. "Same markets" means
only markets both paper and live traded. Descriptive only: no score, qualification or
trading change.

| Series | Live (same mkts) | Twin (same mkts) | Paper, all its markets | Paper settled trades |
|---|---|---|---|---|
| KXRAIN | +5.78 | +5.61 | +5.57 | 2,043 |
| KXNFLTOTAL | +7.24 | +7.23 | +2.76 | 2,158 |
| KXITFMATCH | +2.24 | +2.10 | +2.01 | 2,963 |
| KXATPCHALLENGERMATCH | +0.80 | +0.84 | −0.09 | 3,370 |
| KXWTACHALLENGERMATCH | +0.91 | +0.79 | −0.82 | 771 |
| KXMLBHR | −0.16 | −0.25 | +0.59 | 12,952 |
| KXMLBKS | +1.25 | −0.41 | +1.48 | 1,523 |
| KXMLBSPREAD | −4.28 | −2.75 | +2.10 | 8,000 |
| KXMLBTOTAL | −3.98 | −3.36 | −0.46 | 7,783 |
| KXITFWMATCH | −4.13 | −4.65 | +0.12 | 2,866 |
| KXWTI | +0.79 | +1.33 | −3.75 | 1,461 |
| KXBTCD | −11.72 | −13.63 | −0.46 | 4,182 |

Readings:

- **Twins track live closely** on the same markets (most series within about 1¢), so the
  paper fill assumption is not the main source of paper optimism.
- **Totals: paper +$408 over 51,086 trades; live −$15.57; twins −$5.54.** The gap comes from
  what was traded: about 30 paper variants per series traded far more markets. On the markets
  live traded, paper roughly matched live.
- **Consistent positives across all three views:**
  - rain: about 5–6¢; paper mean $1.84 ± $0.34 per event over 62 events;
  - NFL totals;
  - ITF men.
- **Consistent negatives:** BTC daily, MLB totals and WTI on the paper side.
- **MLB spreads diverge:** paper +$167.85, mean $0.234 ± $0.090 per game over 717 games, while
  live was −$4.91. On live's own markets, paper was also negative (−2.26¢). This is relevant to
  XOS-000046: the selection, not the market type, may be what lost.
- **Caveats:**
  - paper pools about 30 book variants per series, so it carries a multiple-comparison risk;
  - a family member's P&L sums all books on one game;
  - paper P&L is as recorded.

## Retention continuation — 2026-10-21 (DEC-038)

The owner chose to thin data after the disk audit; bulk discovery had been paused since free
space fell under its floor. Built `catalog/retention.py`:
- off / dry run / execute modes;
- the backup retired;
- superseded assessments;
- settled, unselected market revisions as notes or `final_only`;
- the bulk guard counts freelist pages.

Audit figures, mechanics and rollout are in
[selective history § Retention](../MARKET_CATALOG_SELECTIVE_HISTORY.md#retention--owner-decision-2026-10-21-dec-038).
2026-10-28: the dry run ran to completion after
[#681](https://github.com/50thycal/kalshi_bot/pull/681). The owner chose `final_only` with a
24-hour repeat, and the execute pass finished at 18:17 UTC. Free plus reusable space rose from
24.5% to 45% (9.64 → 17.77 GB), and bulk discovery is running again. Measured figures are in
[selective history § Retention — Result](../MARKET_CATALOG_SELECTIVE_HISTORY.md#retention--owner-decision-2026-10-21-dec-038).


## Catalog research study 1 — 2026-10-29

The MMSELL selection-bias audit ran over the whole candidate tape, with outcomes from Kalshi's public
endpoint (D1 option (a)). Verdict: HOLD. Result and reading:
[MMSELL_SELECTION_BIAS_AUDIT.md §8](../MMSELL_SELECTION_BIAS_AUDIT.md#8-results) (PRs #693 and this
one). D1 option (b), catalog access for the ops runner, is still the owner's call before study 2.
