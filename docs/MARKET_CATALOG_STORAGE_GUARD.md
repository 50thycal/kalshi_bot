# Catalog storage headroom guard

WS-023 continuation, 2026-10-09 UTC. Implementation actor: Codex; solo mode.
Disposition FIX NOW: material risk of another full catalog volume during bulk history loading.

## Mission and acceptance

Keep the catalog API readable and preserve existing evidence/checkpoints when space
runs low. Finish: tested reversible collector/API write deferrals, clear dashboard status,
reviewable catalog-only PR and rollout handoff. No deletion, retention policy, volume
resize, source/trading changes or effective-term/score approval.

- Measure actual free volume bytes before each potentially growing collector job and
  authenticated review/import write. Measurement failure defers growing writes.
- Bulk discovery pauses below max(2 GiB, 20% of volume), resumes at
  max(3 GiB, 25%); persisted hysteresis prevents restart/threshold oscillation.
- Imports, bounded live outcome refresh, document capture and evaluation continue while
  only bulk discovery is paused. All growing jobs/API writes pause below 1 GiB and resume
  at 1.5 GiB. Metadata/status remain available; no cursor or last-success advancement on deferral.
- Deferred jobs remain distinct from successful work, provider backoff and execution
  errors. Rechecking space automatically permits work after capacity recovers.
- Metrics/checkpoint/status metadata may still write small amounts from the reserve;
  API GETs remain authenticated as before. The reserve is a practical guard, not a
  guarantee against external writers or a single transaction larger than available space.
- Existing data/identities remain intact. Daily cursor resets are postponed during bulk
  deferral; maintenance writes obey the critical floor too.

## Production finding

#566 merge 6064538101d81c6131aaa63f2163b49922236d9b deployed SUCCESS as
e337cd88-adc5-4710-834b-b5a84c590779 at 05:17 UTC; finalized-head CI passed.
At 11:56 UTC: 280 distinct PDF blobs / 282 observations / 9,435,801 bytes; 5 failures,
5,821 pending URLs. Official fetches are succeeding, with some 404s remaining visible.
At 11:54 UTC database 14,719,332,352 bytes; volume free 3,679,129,600 of
19,685,494,784 bytes. At 04:11 UTC the prior deployment had 6.435 GB free. PDF
capture is about 9 MB, so it cannot explain the multi-GB volume growth; bulk public
listing/revision collection continues. Detailed table totals are older observations.
No table-specific growth attribution or full-universe size estimate is claimed.

At 11:56 UTC, 2,746 / 2,805 recorded live markets attributed, 59 blocked. Live source-ID
coverage matched 2,867 records through ID 4,460; paper matched 133,798 through ID 148,335.
Payload parity, account coverage, effective historical terms and authenticated production
packets remain unverified. No independent/scientific acceptance inferred from merge.

## Release, limits and next action

Merge after CI and deploy catalog only. No variables/dependencies/schema changes or manual
DB setup. Verify bulk discovery reports storage deferral, its cursor is retained, essential
jobs continue while above the critical floor, API/status stay responsive and free-space
growth slows. Expansion beyond the resume threshold automatically resumes the collector.
Rollback is the prior image with the same volume; it resumes unguarded writes and therefore
requires adequate headroom first.

No bytes are reclaimed by this repair. The owner controls additional volume capacity or a
separately approved retention/scope policy. Historical document applicability and explicit
full-document semantic binding remain existing WS-023 work after this storage interrupt.

## Validation

199 headroom/document/catalog/readiness/economics/session checks passed in 6.90 seconds.
Tests cover persisted thresholds/recovery, cursor/last-success preservation, real collection
loop behavior at bulk and critical tiers, retained evaluation requests, measurement failures,
authenticated 503 writes, readable GETs and automatic recovery. Ruff, whitespace, JavaScript
syntax and dashboard DOM checks passed. Railway guard behavior and browser visual rendering
remain release checks; the repair is not yet deployed.
