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
Next step: owner accepts and deploys the evidence continuation described in
[MARKET_CATALOG_EVIDENCE.md](../MARKET_CATALOG_EVIDENCE.md), verifies replay/outcomes,
then completes structured semantics, independent-outcome verification and calibration.
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
