# Catalog review and calibration preparation

WS-023 continuation, authorized by Calvin on 2026-10-08. Catalog-only, read-only
preparation; no experiment consumer or source-system changes.

## Mission and acceptance checks

Outcome: prioritize structured reviews and inspect traceable, strategy-specific
live calibration inputs using the existing catalog dashboard/API. Finish condition:
tested authenticated endpoints, usable review packets, explicit scientific limits,
and a reviewable PR with a catalog-only release checklist.
Non-goals: automatic semantic approval, numerical confidence calibration, source
writes, shared metric changes, experiment activation or trading permissions.
Material interrupt: missing identity, reviews, independence, coverage or lineage
must stay unknown/unverified; historical data cannot become prospective validation.

Acceptance checks:

- All 140 legacy series remain visible as provenance; historical signatures do
  not approve current rules or fill missing semantic fields.
- Series with recorded live markets join the queue even outside the registry.
  Priority is live market count, then historical signature, then ticker. It is
  workflow volume, not profitability, edge or confidence.
- Packets expose exact current series rules/hash, all ten semantic fields and
  unknowns; up to three representative live market listings expose their own
  rule bindings, parent changes and economics blockers.
- Operational strategy tag, deployment arm and held side have separate contexts.
  Missing context identity separates each market rather than pooling unknowns.
  Duration uses verified execution clocks only, with offset-aware timestamps.
- Calibration retains attributable and blocked source-recorded markets. Each
  has immutable ledger ID/fingerprint, current review IDs/hashes, event hint,
  versioned evidence checks and an input ID. All partitions are unassigned.
- Future strategies register their own provider and evidence checks; they do not
  inherit MMSELL requirements or its source-coverage proof.
- Endpoints authenticate, bound response pages, and preserve existing history.
  No GET creates reviews, datasets, scores or approvals.

## API and dashboard

| Endpoint | Output |
|---|---|
| `GET /v1/review-packets?strategy=mmsell&limit=20&offset=0` | Prioritized series packets and separate evidence contexts |
| `GET /v1/calibration-inputs?strategy=mmsell&series=KX...&limit=20&offset=0` | Current per-market live inputs, including blocked rows |

Both require the existing bearer token. Limit handling follows the API's existing
behavior, capped at 50; optional `series` filters the scope. Unsupported strategies
return 400. Empty pages retain the total. These are current views, not frozen
datasets: changes between pages can change membership, reviews and counts. A
multi-page export does not certify one consistent snapshot. A later study must
explicitly freeze its inputs.

The dashboard requests ten MMSELL packets and renders full content as plain text
in expandable details. It shows live counts, attributed versus blocked markets,
current series review and unknown fields. Tokens stay in page memory. The display
does not submit reviews or approve classifications.

Field coverage is separate from accepted review: three known core fields and seven
explicit nulls can form a valid review under the existing API, yet the packet still
reports only 30% known fields. That is documentation completeness, not confidence.
Submissions still use `POST /v1/reviews` with actor, rationale, all fields and current
rules hash. Market samples are illustrative, not exhaustive; review every intended
binding or an exact eligible template before a study uses it.

Reads stream pipeline records, retaining aggregate context metadata and at most
five candidate ledgers per series; only requested packet listings are read.
Packet context output is capped at 50 per series with total/truncation flags;
the calibration endpoint retains every context through its market pagination.
Calibration pagination retains only requested documents. No request scans the full
public market universe or loads all paper evidence. Counting totals still decodes
the current pipeline population; this is not a constant-time query.

## Extending strategies

Register an evaluator with its own versioned scoring requirements using
`evaluators.register`. Then opt in with
`readiness.register_readiness(strategy_id, provider, check_provider=None)`.

The provider yields current live ledger records with immutable `record_id`,
`input_fingerprint`, `market_ticker`, `series_ticker`, operational `strategy_version`,
`deployment_arm_id`, `side`, `status`, `source_fill_count`, execution-time fields and
verification flags. It must respect the requested series and deterministic order.
The optional checker receives `(store, record, market_review, series_review)` and
returns explicit boolean facts keyed by that strategy's required checks. Absent or
non-boolean facts fail closed. Without a checker, all requirements stay unverified.
Registration does not supply numerical calibration or permit selection. MMSELL is
the sole built-in readiness provider; the dashboard queue currently uses it.

## Evidence limits

Recorded fills omit assigned-but-unfilled, rejected and never-placed opportunities.
Source-ID agreement proves scoped database ID coverage, not exchange-account fill
coverage or complete payload parity. The ledger describes exclusive-owner source
economics, not account P&L or a policy's expected opportunity edge. Preserve
unsettled/blocked rows to avoid evaluating only attributable winners.

Exchange `event_ticker` is a candidate grouping hint. Missing identity stays null;
neither event count nor exposure labels prove independence. Review shared resolution
sources, observation windows, alternatives, overlapping payouts and cross-series
exposure before assigning independent outcome groups.

Operational tags and arm IDs do not prove full canonical Version/epoch/assignment
lineage; context spans remain descriptive. Known scientific lineage stays false.
Verify mappings against canonical Experiment OS rather than inventing epochs or
copying its state into catalog documentation.

## Calibration sequence after review

1. Review exact rules and semantic bindings for a bounded intended universe. Record
   alternatives/operator/exclusions, observation timing, settlement source, shared
   exposure and risk components; unsupported values remain null.
2. Resolve independent groups and canonical lineage, then reconcile exchange fills,
   fees, exits/settlement and assigned opportunities. Preserve explicit exclusions.
3. Freeze a strategy-specific manifest, grouping map, evaluator/bar versions, study
   target and chronological development/validation partition. Group shared outcomes
   together to prevent leakage. These endpoints prepare inputs; they do not freeze
   them or choose numerical thresholds.
4. Use historical development evidence to justify sample size, live duration and
   uncertainty requirements. Paper stays separate. Volume or duration alone is not
   a calibrated confidence score.
5. Preregister a future boundary and evaluator before observing a forward cohort.
   Historical holdout remains historical validation; existing fills never become
   prospective evidence. Forward collection does not authorize new live exposure.
6. Publish scores only after the full evidence bar is met. Consumer cutover remains
   a separate Platform Change Review with impacts and operator activation.

## Release and recovery

Owner reviews/merges the finalized PR after CI, then Railway builds the default
branch for **market-catalog only**. Verify SUCCESS and a commit containing this
change; retain the existing volume, replica, token and SELECT-only URL. No new
environment variables, storage migration or manual database work is required.

Confirm health, unauthenticated 401s, authenticated packets/calibration inputs and
dashboard queue. Compare hashes to object endpoints; verify unknown fields, stale
parent reviews, blocked inclusion and unassigned partitions. Queue priority is not
a scientific ranking. No calibrated score or selection activation is expected.
Rollback uses the prior catalog commit and same volume; this release adds no tables
and changes no stored economics.

Validation and final head are recorded in the PR. Production rollout and browser
visual verification remain release checks, not claims from fixture tests.
