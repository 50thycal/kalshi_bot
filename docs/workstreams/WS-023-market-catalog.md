# WS-023 — Market catalog and strategy evidence service
**Build OS:** v0.12 · **Phase:** BUILDING · **Status:** Active
**Implementation actor:** Codex · **Review:** solo, owner acceptance pending
**PR:** [#541](https://github.com/50thycal/kalshi_bot/pull/541)
Mission: standalone Railway catalog service and additive existing-data import.
Owner direction: Calvin approved D1–D9 A and requested implementation on 2026-10-05 in ChatGPT.
Build Card, spec and pre-implementation impact plan: [MARKET_CATALOG_BUILD.md](../MARKET_CATALOG_BUILD.md).
Open decisions: numerical confidence calibration remains unset; no permission to trade or scale follows a catalog score.
Assumptions: source trading data is read-only, local persistent storage belongs only to the catalog, existing workers do not consume new assessments.
Next step: implement, test, deploy pinned PR commit, verify data import; record validation in the PR.
