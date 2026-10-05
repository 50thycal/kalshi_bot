# Market catalog service

Standalone service in the existing repository: `python -m kalshi_bot.catalog.service`.
Build Card/spec: [MARKET_CATALOG_BUILD.md](MARKET_CATALOG_BUILD.md). Workstream: WS-023.

## What runs
The service seeds all 140 registry rows, preserving legacy signatures and classifications as provenance. It discovers public Kalshi series and event/market listings with persisted REST cursors, captures raw rules and timing, and detects changed market, event and series semantics. Exact reviewed fingerprints on the same series can be reused; clocks, strikes and wording must match. New semantic templates go to the human review queue. A historical registry signature is not promoted into a new structured review.

It imports MMSELL paper trades and actual live fills from the existing Postgres using bounded, SELECT-only, read-only transactions. Import cursors commit with each local page. Daily replay catches previously open trades that settle later. Originals are retained. Live fills are joined once to order identity; no account-level P&L is guessed into a strategy. Existing paper twins remain paired evidence, excluded from independent paper assessments.

MMSELL is the first registered evaluator. Assessments are distinct by book tag, deployment arm, side, ten-cent entry band, fill policy, paper/live and recent-30-day/history window. Legacy records explicitly lack trusted version/epoch lineage. Event tokens are provisional grouping hints; cross-series independence is unverified. Descriptive net paper economics use source-reported P&L, already net of fees. They are historical realized estimates, not predicted future edge or authorization. Source order fills provide live coverage; attributable live net economics remain null.

The API returns separate review completion, evidence maturity, confidence and edge. All initial assessments are provisional and unqualified, with reasons. Numeric confidence, composite risk, opportunity economics and established-live qualification remain null until calibration, verified exposure grouping, integrity and forward validation are implemented. This is deliberate truthful bootstrap, not a promised confidence ranking. Extending `EVALUATORS` adds another strategy's assessment records, isolated from MMSELL performance. No consumer uses these scores yet.

Current raw quote/depth/volume fields are stored; full quote history is not archived. Semantic/lifecycle revisions, reviews and assessments are retained. There is no new execution telemetry subscription or order endpoint. REST reconciliation supplies automatic discovery and outcome/change refresh; authenticated WebSocket acceleration is a later optimization.

## Railway deployment
Project `kalshi-bot`, production. New service `market-catalog`; existing services are untouched.
One replica in `us-east4-eqdc4a`, 1 CPU / 1 GB memory maximum. Dedicated 1 GB volume `market-catalog-data` at `/data`, SQLite WAL. The source Postgres is not the catalog's write database. Do not scale replicas against this SQLite volume. Backup/migrate storage before a future multi-replica deployment.
Dockerfile `deploy/catalog/Dockerfile`, start `python -m kalshi_bot.catalog.service`, health `/health`, port 8080. Configure through Railway service settings/tools; new services cannot opt into deprecated `railway.json` configuration. Watch only catalog files, registry manifest, taxonomy source and the catalog Dockerfile. Pin a tested commit until owner merge acceptance and subsequent deployment policy are recorded.

Variables:
- `CATALOG_DB_PATH=/data/catalog.sqlite3`
- `CATALOG_SOURCE_DATABASE_URL`: a SELECT-only role URL, such as the existing `DATABASE_URL_RO`; never the owner `Postgres.DATABASE_URL`
- `CATALOG_API_TOKEN`: generated bearer secret; retrieve/share through Railway variables, never commit
- `CATALOG_INTERVAL_SECONDS=30` (minimum 10)
- `PORT=8080`
No Kalshi trading credential, live arming variable or XOS write transport is configured.

Private connection: `http://market-catalog.railway.internal:8080`, bearer token. A generated public domain supports health and authenticated integrations. `/health` is the only unauthenticated route. Public health means process/API is available, not that import is complete; `/v1/status` is authoritative for import/discovery cursors, completion and error types. Daily import reconciliation may still be running while initial completion is true; inspect cursor time and `last_complete_at`.

## Existing-code adapter
`kalshi_bot.catalog.client.CatalogClient(base_url, token)` provides read-only `assessments` and `select` methods. Selection requires qualified evidence by default, and supports settlement mechanism, net edge, confidence and paper/live filters. No existing strategy is wired to it yet. Unknown semantics/confidence never satisfy a numeric/type filter.

## API contract
Every `/v1` endpoint requires `Authorization: Bearer <CATALOG_API_TOKEN>`.

| Endpoint | Purpose |
|---|---|
| `GET /v1/status` | Object/evidence counts, job errors, import and discovery progress |
| `GET /v1/series`, `/v1/events`, `/v1/markets` | Catalog; `series`, `limit` (1–500), `offset` |
| `GET /v1/objects/{kind}/{ticker}` | Facts, timing, raw rules, legacy provenance, current review |
| `GET /v1/objects/{kind}/{ticker}/revisions` | Preserved semantic/lifecycle payloads |
| `GET /v1/objects/{kind}/{ticker}/reviews` | Immutable human review history |
| `GET /v1/review-queue?kind=market` | Missing/currently invalid reviews |
| `POST /v1/import` | Bounded authenticated import of existing read-only exports; IDs/provenance required, does not skip the direct-source cursor |
| `POST /v1/reviews` | Explicit human review tied to current rules hash |
| `GET /v1/assessments?strategy=mmsell&series=KX...` | Current context-specific assessments |
| `GET /v1/assessments/{assessment_id}` | Immutable exact assessment for future decision attribution |
| `GET /v1/select?strategy=mmsell&qualified=true&min_edge=1` | Advisory selection; initially empty because confidence is not calibrated |

Review body: `kind`, `ticker`, `rules_hash`, `actor`, `rationale`, optional `template` (false by default), and `semantics`. Semantics requires explicit `resolution_mechanism`, `observation_start`, `observation_end`, `alternatives`, `alternative_operator`, `exclusions`, `payout_relationship`, `shared_exposure_key`, `settlement_source`, `risk_components`. Unknown values may be null, but resolution/source/exposure must be known. A reusable template cannot contain market-specific observation timestamps. Ongoing rule/parent changes invalidate review; old-hash submissions get 409. There is no automatic interpretation of aliases/OR/AND from text.

## Cutover stages
1. Running now: additive catalog and raw evidence import, with original identifiers; validate source coverage, review missing semantics and calibrate MMSELL qualification.
2. Add verified shared-exposure mapping, opportunity/fee/exit attribution and confidence calibration from paper/live and holdout evidence. Freeze evaluator versions and qualification requirements before using them for decisions.
3. Connect an initial consumer through Platform Change Review: register pending revision, discover pinned affected experiments, classify and accept impacts, explicitly authorize activation at a measured boundary. Preserve exact assessment references and frozen universes. Backfill alone does not perform this cutover.

## Verification and recovery
`python -m pytest tests/test_market_catalog.py`; `python -m ruff check kalshi_bot/catalog tests/test_market_catalog.py`.
The importer checks source role elevation and write grants and refuses privileged connections. A root-credential reference was rejected by automatic approval review; provision the existing SELECT-only connection directly through Railway variables.

Source/discovery exceptions log only exception type; retries resume committed cursors. Inspect authenticated status and Railway logs. Restart preserves data on the volume. Reconcile daily rather than deleting/replacing legacy records. Before any volume operation or storage migration, stop this service and export a consistent SQLite backup; retain originals and historical snapshots.


## Deployment verification — 2026-10-05
The first deployment succeeded, health returned 200 and unauthenticated data returned 401. Registry seed verified all 140 rows. Public discovery returned 14,649 series and has continued paging events/markets. Through existing SELECT-only ops exports, the service accepted 200 recent paper records (including 6 separately preserved twins) and 200 actual live fills; this is explicitly partial coverage, not full historical migration. Ops transport returned to noop. Continuous historical backfill is blocked on configuring the SELECT-only source URL; the owner database URL was rejected by automatic approval review. Calibration and first consumer cutover remain subsequent WS-023 stages.
