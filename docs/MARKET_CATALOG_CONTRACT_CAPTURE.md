# Catalog contract-document capture

WS-023 continuation, 2026-10-09 UTC. Implementation actor: Codex; solo review.

## Mission and finish

Preserve bounded official contract PDF bytes, content identities and dated fetch
observations in the catalog volume. Surface current captures and failures in
authenticated review packets without declaring historical applicability or approving
semantics. Finish: resumable collector, immutable archive, meaningful validation,
catalog-only PR and rollout/rollback handoff.

## Acceptance checks

- Only HTTPS assets.kalshi.com PDF references are fetched; credentials, query strings,
  alternate hosts/ports and redirects are refused. PDFs are stored, never parsed or executed.
- One streamed request per collector pass, 2 MiB maximum decoded body, 20-second network
  timeout and 256 MiB free-space reserve; one failure does not block other collector jobs.
- Series scan resumes in bounded pages. Shared URLs are deduplicated; successful URLs
  recheck daily, failures retry with persisted delay, rate limits defer all document fetches.
- Bytes are content-addressed and immutable. Repeated identical bytes reuse one blob,
  observations retain fetch timestamps and limited response metadata, changed bytes preserve
  both versions, and failed refreshes visibly mark the previous capture as stale.
- References and archive metadata are authenticated; responses never contain PDF bytes.
  Observation history is paginated at `/v1/contract-documents` with an optional URL filter.
  The dashboard displays distinct saved documents, pending links and failed refreshes.
- Current capture, listing identity, accepted semantic review and effective historical
  version are separate facts. Existing rules hashes, reviews, ledgers and scores stay intact.

## Boundaries and material interrupts

No PDF parsing, backdated proof, source writes, confidence calibration, numerical risk
scores, trading admission, shared metrics or consumer cutover. Authoritative historical
terms remain unknown until supported by dated evidence; HTTP Last-Modified and PDF metadata
are not such proof. No Platform Revision is activated. Stop fetching on low disk space,
untrusted URLs, excessive body size or provider backoff; preserve the previous evidence.

## Architecture and release

Add catalog-only blob/observation/target tables. The ordinary collector scans series
references and fetches one due URL per pass. Current object GETs and readiness packets
show archive identity, latest attempt and historical-proof false. No new dependencies,
variables or manual database setup. Initialization adds tables without changing old rows.
Successful URLs become due after 24 hours; one-request capacity means this is a recheck
schedule, not a guarantee that every URL is revisited within one day. Unsupported hosts
remain visible and require a separately justified allowlist addition.

After owner merge: verify catalog-only Railway SUCCESS, healthy capture/import/evaluation
jobs, bounded archive growth, authentication guards and authenticated reference metadata.
Rollback: previous image with the same volume; additive tables remain unused and evidence
is retained. Existing pre-compression backup is not a fresh archive backup. Normal volume
backups must include the database and its WAL consistently.

Next in this mission: establish authoritative effective versions for historical markets,
then bind the full-document identity to explicitly approved reviews. Current-byte capture
alone cannot satisfy verified_contract_document_binding. Independence, strategy lineage,
opportunity coverage and calibration remain subsequent scoring stages.

## Verified previous release

#565 merge `bb2a554b78ad6d9c510bf4703056727ccaf7ea26` deployed SUCCESS as
`f37a271c-ff98-4a97-a9fa-7e9070758aae` at 04:04 UTC. Finalized-head CI passed.
Health returned 200; runtime evaluation/import/discovery succeeded. At 04:11 UTC,
2,737 / 2,795 recorded live markets were attributed, 58 blocked; 87 fractional and
2,857 execution-clock restorations. Source/local ID fingerprints matched at 04:09 UTC:
2,857 live through ID 4,450 and 133,545 paper through ID 148,080. Payload parity,
exchange account coverage and authenticated production packets remain unverified.
Database 11.963 GB / free volume 6.435 GB; detailed table counts are older measurements.
Full release receipt is on merged #565. No acceptance verdict is inferred from merge.

## Local verification

192 catalog/document/readiness/economics/session checks passed. Ruff, JavaScript
syntax and dashboard DOM smoke passed. Public-source smoke downloaded three current
official term PDFs (133,281 total bytes), then exercised the local streamed capture
path and checked retained SHA-256 identities and unapproved/historical-proof false.
That transport smoke is not a Railway collector or authenticated production audit.
Browser visual rendering, catalog-only release and authenticated production metadata
remain release checks; document parsing and effective-term proof are not performed.
