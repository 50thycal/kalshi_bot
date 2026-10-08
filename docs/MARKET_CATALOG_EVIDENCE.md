# Market catalog: migrated reviews and attributable evidence

WS-023 · Build OS v0.12 · Authorized by Calvin in ChatGPT on 2026-10-08.
Implementation: task-specific WRITE, isolated advisory catalog; solo owner acceptance pending.

## Build Card and boundary

Goal: preserve existing review provenance, reconstruct live economics only where the evidence
supports attribution, and expose the requirements for strategy-specific confidence.
Acceptance: idempotent migration with visible missing fields; explicit ownership/fill/fee/settlement
checks; immutable outcome/assessment snapshots; no confidence from provisional evidence;
restart-safe outcome collection; bounded API memory and atomic assessment refresh.
This extends the existing approved catalog. It does not change source tables, scientific metrics,
experiment snapshots, trading admissions, live variables or strategy universes. Consumer cutover
still requires its separate Platform Change Review. No numerical sample floor, duration,
uncertainty limit or confidence weight is invented in this release.

## What migrates

Startup migrates every registry row (currently 140) into a versioned record. The 38 rows with
both a review date and reviewer retain that signature and their original reason. Classification
hints remain legacy hints. None contains all the required semantic fields, so these are partial
historical reviews or classification-only records, not new approvals. Missing fields stay null;
completion is zero until actual semantic review is supplied. Re-running startup is idempotent.
Changed registry records create immutable migration versions rather than rewriting history.
Current records are available at `GET /v1/review-migrations?limit=100&offset=0&series=...`.
The existing rules-hash-bound `POST /v1/reviews` remains the completion mechanism.

## Live economics

The live import adds source-ledger audits: distinct strategy/deployment owners for each market
and exchange order, and the total number of source fills on each market, including other books.
One statement snapshot computes these audits. The first start with this importer resets only
its live replay cursor; previously verified initial ID coverage is retained, daily reconciliation
becomes incomplete until the replay finishes, and no evidence is deleted.

Each actual live market is revisited through public market endpoints, falling back to the
historical endpoint on 404. Five markets per loop, persisted progress, existing shared provider
backoff. Missing outcomes are retried on subsequent sweeps. Changed settlement values are
included in immutable revisions. No private trading credential is required.

Supported attribution is intentionally narrow: one strategy/deployment owner, one side,
all source fills accounted for, distinct exchange-fill identities, matching order/fill market, side and action, exact raw quantity/price,
explicit exchange `fee_cost`, valid execution times, and final non-provisional $1 binary
settlement. Missing or inconsistent evidence blocks P&L with named reasons. Stored fee values
are not proof of actual fees: the source executor sometimes substitutes an estimate.

For eligible ledgers, sum buys and sells in execution order. An inventory deficit blocks
attribution. Net dollars = sell cash − buy cash + remaining contracts × side payout − actual
fees. Decimal arithmetic preserves costs. Aggregation is by series, strategy version,
deployment arm and side; it uses the full market history, not an entry-price bucket or a
truncated recent window. Net cents per contract uses total purchased contracts as denominator.
This is reconstructed **source-ledger** economics, not independently reconciled exchange
account P&L. Complete source IDs cannot prove every exchange fill was originally ingested.
Mixed-owner/side markets require a later allocation method and remain blocked here.

`GET /v1/live-economics` returns market-level results/reasons, paginated like review migrations.
The corresponding MMSELL assessments use method `exclusive-binary-ledger-v1` and maturity
`live_attributed_source_ledger`. Existing paper and raw live-fill assessments remain distinct.

## Completion, maturity and confidence

Completion measures review documentation. Maturity describes the kind of evidence: paper,
recorded live fills, or attributable source-ledger results. Confidence is a calibrated claim,
not a completion percentage or the size of a positive P&L sample. Paper and live do not pool.
Each strategy retains its own assessment namespace and evaluator/method version, so future
strategies can add assessments without inheriting MMSELL confidence.

`GET /v1/scoring-requirements` exposes the MMSELL evidence bar and unset numerical floors.
Assessments expose measured readiness flags, counts, active days/span where applicable,
current series-review binding and explicit blockers. A reviewed series does not automatically
bind every traded market's rules; current market semantic binding remains unverified.
Source-ID coverage, exchange fill coverage, attributable actual costs, known lineage,
independent outcome groups, forward validation and calibrated duration/sample/precision
requirements are distinct checks. Confidence remains null and qualification false.
The next scientific step is a versioned calibration study with verified independent outcome
groups and a forward holdout; it must justify numerical requirements before scores appear.

Refresh fingerprints include evidence, settlement outcomes, review provenance and bar version.
A changed review/outcome can therefore refresh an assessment without a new execution.
No-op refreshes retain IDs. Snapshot/fingerprint/current-selection writes share a transaction;
failed refreshes retain the preceding assessment selection. HTTP assessment pagination decodes
one snapshot at a time and retains only the requested page. Status omits internal per-context
fingerprints. These changes address the observed ~146,000-context production catalog without
claiming that those contexts are unique markets or validated independent outcomes.

## Release and verification

1. Owner accepts/merges the PR. Deploy its tested commit to **market-catalog only**; Railway
   currently pins the earlier storage-diagnostics branch, so a default-branch merge alone
   does not update that service. Keep the existing volume, token and SELECT-only source URL.
2. Confirm health, seed migration counts, live replay and `discovery:live_outcomes` freshness.
   Use authenticated endpoints; never put tokens in a URL or commit.
3. Verify source-ID coverage again after replay. Inspect attributable/blocked market counts
   and reason frequencies. Compare a few reconstructed ledgers against exchange history
   before claiming exchange coverage or calibrated scores.
4. Complete current structured semantics for the intended strategy universe; validate market
   rule bindings, independent outcome groups and calibration/forward evidence.
5. Compare advisory selections, then separately authorize the experiment consumer cutover.

Rollback: deploy the prior catalog commit with the same volume. All new tables are additive;
existing objects/evidence/reviews/assessments retain their original schema and encoding.
New live projections are additional JSON fields. Do not delete the volume or legacy history.
Local tests exercise migration replay, ownership/fill ambiguity, missing actual fees, truncated
quantity/subpenny price, partial exits, no-side payouts, provisional/conflicting settlement,
review/outcome refresh, transaction rollback, archived discovery/backoff and API auth.
Production attribution and score calibration are not claimed from fixture tests.

Primary API references checked 2026-10-08:
[market](https://docs.kalshi.com/api-reference/market/get-market),
[historical market](https://docs.kalshi.com/api-reference/historical/get-historical-market),
[fills](https://docs.kalshi.com/api-reference/portfolio/get-fills).

Validation: 89 catalog/economics/session-system tests passed; Ruff, JavaScript syntax and
whitespace checks passed. A temporary local benchmark with 150,000 synthetic contexts took
5.64 seconds for an atomic snapshot batch and 1.52 seconds to count/filter a 100-item API page;
peak process RSS was 46.0 MB and status JSON 471 bytes. This tests storage/API mechanics with
small synthetic documents, not production collector memory, source-query plans or attribution.
Production verification remains the release checklist above.
