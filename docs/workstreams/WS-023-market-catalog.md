# WS-023 — Market catalog and strategy evidence service
**Build OS:** v0.12 · **Phase:** REVIEW · **Status:** Blocked
**Implementation actor:** Codex · **Review:** solo, owner acceptance pending
**PR:** [#541](https://github.com/50thycal/kalshi_bot/pull/541) merged; operational follow-up on `codex/catalog-storage-diagnostics-20261005`.
Mission: standalone Railway catalog service and additive existing-data import.
Owner direction: Calvin approved D1–D9 A and requested implementation on 2026-10-05 in ChatGPT.
Build Card, spec and pre-implementation impact plan: [MARKET_CATALOG_BUILD.md](../MARKET_CATALOG_BUILD.md).
Open decisions: numerical confidence calibration remains unset; no permission to trade or scale follows a catalog score.
Assumptions: source trading data is read-only, local persistent storage belongs only to the catalog, existing workers do not consume new assessments.
Implementation: standalone service deployed on Railway, registry/discovery verified, initial 200 paper / 200 live records imported through the existing SELECT-only ops channel. Calvin expanded the full 1 GB volume to 20 GB; disk-full restarts recovered. The supplied source URL is refused by the privilege guard. Follow-up preserves content/IDs with lossless compression and a pre-upgrade backup, plus storage and sanitized permission diagnostics. 21 catalog tests pass. See service documentation and follow-up PR for deployment/head. No owner acceptance verdict is inferred from the merge.
External unblocker: Calvin (or a database administrator) replaces CATALOG_SOURCE_DATABASE_URL with a genuinely SELECT-only connection on market-catalog. The variable is present, but its permissions are refused. No owner/root credential is accepted. Continuous full historical import and confidence calibration await this input.
Next step: complete historical coverage once scoped credentials are present; calibrate requirements, then plan the first experiment consumer cutover through Platform Impact. Owner acceptance at merge remains pending.
