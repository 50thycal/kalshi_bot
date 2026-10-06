# WS-023 — Market catalog and strategy evidence service
**Build OS:** v0.12 · **Phase:** REVIEW · **Status:** Blocked
**Implementation actor:** Codex · **Review:** solo, owner acceptance pending
**PR:** [#541](https://github.com/50thycal/kalshi_bot/pull/541) merged; operational follow-up [#542](https://github.com/50thycal/kalshi_bot/pull/542).
Mission: standalone Railway catalog service and additive existing-data import.
Owner direction: Calvin approved D1–D9 A and requested implementation on 2026-10-05 in ChatGPT.
Build Card, spec and pre-implementation impact plan: [MARKET_CATALOG_BUILD.md](../MARKET_CATALOG_BUILD.md).
Open decisions: numerical confidence calibration remains unset; no permission to trade or scale follows a catalog score.
Assumptions: source trading data is read-only, local persistent storage belongs only to the catalog, existing workers do not consume new assessments.
Implementation: standalone service deployed on Railway, registry/discovery verified, initial 200 paper / 200 live records imported through the existing SELECT-only ops channel. Calvin expanded the full 1 GB volume to 20 GB; disk-full restarts recovered. The supplied source URL is refused by the privilege guard. Follow-up preserves content/IDs with lossless compression and a pre-upgrade backup, plus storage and sanitized permission diagnostics. 21 catalog tests pass. See service documentation and follow-up PR for deployment/head. No owner acceptance verdict is inferred from the merge.
External unblocker: Calvin (or a database administrator) supplies the actual bot_readonly URL, with that role's own password, as CATALOG_SOURCE_DATABASE_URL on market-catalog and redeploys. The existing GitHub secret DATABASE_URL_RO was also found to use elevated postgres, so copying it unchanged will not work. The bot_readonly role itself has the required login/schema/SELECT access with no elevation or memberships. No owner/root credential is accepted. Continuous full historical import and confidence calibration await the proper credential.
Next step: complete historical coverage once scoped credentials are present; calibrate requirements, then plan the first experiment consumer cutover through Platform Impact. Owner acceptance at merge remains pending.

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
