# WS-023 — Market catalog and strategy evidence service
**Build OS:** v0.12 · **Phase:** REVIEW · **Status:** Blocked
**Implementation actor:** Codex · **Review:** solo, owner acceptance pending
**PR:** [#541](https://github.com/50thycal/kalshi_bot/pull/541)
Mission: standalone Railway catalog service and additive existing-data import.
Owner direction: Calvin approved D1–D9 A and requested implementation on 2026-10-05 in ChatGPT.
Build Card, spec and pre-implementation impact plan: [MARKET_CATALOG_BUILD.md](../MARKET_CATALOG_BUILD.md).
Open decisions: numerical confidence calibration remains unset; no permission to trade or scale follows a catalog score.
Assumptions: source trading data is read-only, local persistent storage belongs only to the catalog, existing workers do not consume new assessments.
Implementation: standalone service deployed on Railway, registry/discovery verified, initial 200 paper / 200 live records imported through the existing SELECT-only ops channel. Tests and API authorization checks pass. See service documentation and PR for current deployment/head.
External unblocker: Calvin (or a database administrator) configures the existing SELECT-only URL as CATALOG_SOURCE_DATABASE_URL on market-catalog. No owner/root credential is accepted. Continuous full historical import and confidence calibration await this input.
Next step: complete historical coverage once scoped credentials are present; calibrate requirements, then plan the first experiment consumer cutover through Platform Impact. Owner acceptance at merge remains pending.
