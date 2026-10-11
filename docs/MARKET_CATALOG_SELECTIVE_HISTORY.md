# Catalog selective history — option 3

**Workstream:** WS-023 · **Build OS:** v0.12 · **Status:** approved direction; implementation pending.
**Owner direction:** Calvin approved option 3 in ChatGPT on 2026-10-11 America/Chicago.
**Implementation actor:** Codex, task-specific catalog scope. No independent review claimed.

## Build card

Keep a broad inventory of Kalshi series and markets so new opportunities can enter
the pipeline. Concentrate detailed history on markets with imported trading evidence,
explicit reviews, or an explicit research selection. Preserve rule changes, settlement
proof, source economics and the evidence behind existing assessments. Show which
coverage is broad inventory versus detailed collection.

This changes future collection and storage admission. It does not delete existing
history or shrink the volume. Current rules and strategy confidence requirements stay
in force. The expected benefit is slower growth; measured savings are not yet known.

## Mission and boundaries

Goal: implement broad discovery plus selective detailed history with visible provenance
and coverage, inside the isolated catalog. Continue existing WS-023, not a new workstream.

Non-goals: retention cleanup, VACUUM, resizing or purchasing storage, source database
writes, shared taxonomy/fee/fill/metric changes, XOS lifecycle or Platform Revision
activation, trading consumers, approving semantics or numerical confidence.

Finish condition: tested implementation on this same draft PR, durable model update,
documentation-only finalization, green final-head CI and a concrete catalog-only release
handoff. The owner merges; production behavior must then be verified separately.
The current PR is a design checkpoint, not executable implementation.

## Current implementation findings

Reviewed catalog store/ingest/service on 2026-10-11. Store.upsert already inserts
immutable revisions with a semantic identity based on rule/parent bindings, source,
status, result and explicit settlement fields. Repeat price/volume-only changes
normally update the current object without adding another identical revision.
Therefore do not implement a blind "deduplicate repeated snapshots" repair or promise
that removing exact duplicates recovers the observed space.

Broad event discovery currently requests nested markets and stores full objects and
semantic revisions. Incremental market updates also store full payloads. Historical
source import is separate and remains read-only. Contract captures preserve opaque bytes.
Later catalog changes added lossless dictionary compression with separately gated
writes: selective collection must preserve compatibility and all existing encodings.

The 2026-10-09 cost comparison used dated measurements: revisions about 9.52 GB,
current objects about 4.80 GB, assessments about 0.63 GB, PDF blobs about 0.014 GB.
These are neither today's inventory nor a predicted saving. Volume configuration then
reported 40,000 MB. No resize is authorized by this design.

## Collection contract

1. Broad discovery continues for all series and new events/markets through bounded
   pages. Every series remains discoverable and can be promoted to detailed collection.
   Current listing identity and rule/settlement facts needed for classification remain
   available; broad inventory must not claim detailed history completeness.
2. Detailed priority comes from imported live OR paper market evidence, explicit
   structured reviews (including a series review), or an operator/research selection.
   Legacy reviewed signatures/registry provenance should be included conservatively
   where applicable, without calling them approved current semantics.
   Priority is evidence of relevance, not realized profit, current score or a winning
   outcome. Any future strategy can select a market or series without MMSELL coupling.
3. Preserve every observed material rule/parent binding change for all discovered
   objects. Preserve final payout/time facts and lifecycle changes needed for outcome
   attribution. For detailed objects retain the existing complete semantic snapshot.
   For inventory objects store an explicitly versioned compact observation sufficient
   for rules, lifecycle and provenance, with a representation-specific content hash.
   Assessments/reviews pinned to earlier full documents must keep resolving unchanged.
   Never replace an old immutable document or its identity with a compact substitute.
4. Broad current objects and detailed revision snapshots are distinct policies.
   Do not drop fields from current objects until all evaluator/review/API/document
   consumers are inventoried and their required fields tested. Compact revisions
   are not full historical payload proof and must be labelled as such in history APIs.
5. A newly detailed market starts bounded hydration from the exchange's available
   data and records when selection became effective. Preserve explicit pre-selection
   gaps. Current retrieval cannot recreate earlier missing versions, execution-time
   conditions or unobserved market history. Selection is not a backfill-complete verdict.
6. Preserve source imports, source-ID reconciliation, exact costs/fees/quantities,
   live outcome collection and contract bytes/observations. No source rows are excluded
   from catalog evidence merely because their market was not previously selected.
   Document capture may prioritize selected contexts but must preserve due work and
   never delete existing captures or imply historical applicability.
7. No background job may delete existing objects, revisions, reviews, assessments,
   economics or document evidence. Savings affect future writes. Any later retention
   proposal is a separate owner decision with an explicit impact/recovery plan.

## Selection implementation

Use a persisted versioned catalog selection registry, addressed by market or series.
Automatic membership derives from imported evidence and review/provenance records;
explicit research membership uses authenticated bounded input with a reason and
selection timestamp. Preserve who/what selected it and the source reference.

Persist indexed membership rather than decoding the entire source evidence table on
each upsert. Membership must survive restart, catch evidence/review changes during
collection, and be published transactionally. Conservative behavior while membership
is unbuilt or invalid: preserve full history; report policy unready, never silently
treat everyone as unselected. Build initial membership in bounded resumable pages.

Support an authenticated GET for collection scope/coverage and a bounded authenticated
research-selection POST. Reuse existing authentication, body limits, storage checks,
sanitized errors and pending evaluation semantics. No new public mutation path or
credential request. Explicit deselection must not delete history or override automatic
membership from trading/review evidence.

Keep membership extensible by reason/provider. Do not add strategy confidence columns
here: existing strategy-specific assessment providers remain separate and unchanged.

## Discovery and scheduling

Separate inventory discovery checkpoints from selected-context hydration checkpoints.
Complete a sweep before resetting its cursor; a daily timer must not repeatedly discard
an unfinished traversal. Bounded hydration must not prevent inventory pages, imports
or outcome updates from progressing. Provider backoff/storage deferral retains due
work, selected membership and cursors. Existing headroom checks remain active.

Do not silently introduce a calendar history cutoff or an exclusion list. Preserve
broad discovery while selecting depth. If the provider cannot provide a required
historical payload, record unavailable/gap rather than treating an empty response as
proof of absence.

## Visibility and evidence integrity

Authenticated status/history and the existing dashboard must show policy version,
selection reason, selection-effective time, inventory/detailed coverage, hydration
backlog, retained full versus compact observations, and missing historical coverage.
Display actual timestamps and storage deferrals; count collected, reviewed, selected
and qualified independently.

Any compact observation that lacks the full raw payload is barred from satisfying
a requirement for exact historical document/payload binding. Preserve existing IDs,
rule hashes, source provenance and approved-review invalidation semantics. If unchanged
confidence/economics consumers cannot safely distinguish observation types, stop and
repair the distinction before enabling compact writes.

Selection based on reviewed/traded markets creates a nonrandom research sample.
Record selection history and report excluded/missing opportunities. This catalog
change does not authorize using the selected cohort as an unbiased strategy control,
pooling epochs, or claiming confidence from incomplete coverage.

## Acceptance checks

- Broad pages discover an unselected new series/market and retain current classification
  inputs plus every observed rule change; full history is retained for selected objects.
- Live and paper evidence, market review, series review, legacy review provenance and
  explicit research selections promote the right contexts. Selection works for a
  provider unrelated to MMSELL and survives restart.
- Membership bootstrap/failure preserves full writes and reports policy unready.
  Selection publication cannot race an upsert into falsely dropping required history.
- Changed prices/volume with identical semantics retain existing idempotency; material
  rule, source, status, payout/time and parent changes remain independently traceable.
- Existing review/assessment/document identities and bytes stay intact. Compact records
  cannot pass full-payload/historical-term proof requirements. Confidence stays withheld.
- Selection after earlier inventory collection shows historical gaps and bounded
  hydration; unavailable archive results do not claim full coverage.
- Actual loop tests prove inventory/hydration/import/outcome fairness and that an
  unfinished sweep is not reset by the daily clock.
- Provider/storage deferral preserves cursors/due requests; metrics and authenticated
  reads work. Unauthorized selection writes fail, body sizes are bounded and automatic
  relevance cannot be removed by manual deselection.
- A representative synthetic workload compares future stored bytes, latency and peak
  memory under broad-full versus selective policies, with cohort counts and assumptions.
  Do not infer production savings or statistical quality from the synthetic benchmark.
- Required existing catalog, documents, economics, readiness and session checks plus
  lint pass. Final-head CI passes; remote tree is verified; release is catalog-only.

## Rollout and rollback

Ship reader support and membership/status visibility before enabling compact writes.
Existing full history remains readable; compression reader/writer gates are unchanged.
Expose a catalog-only policy switch that defaults to broad-full until membership and
compatibility checks are ready. Final rollout values belong in the tested implementation
handoff, not this unvalidated design. Do not change Railway variables in this design PR.

Rollback disables selective future writes and retains all original and compact evidence.
Never roll back to an image unable to read the new representation. No space is reclaimed
by rollback. Verify volume headroom before any resumed broad-full loading.

## Interrupts and outstanding work

Stop for any proposed destructive cleanup, lost rule/document identity, unsupported
consumer semantics, unsafe selection race, proof falsely promoted from compact data,
or scope that touches shared trading/XOS behavior. Resolve within the mission or bring
the genuine owner decision back; do not weaken evidence bars.

The coding environment is disconnected (environment_offline); local implementation,
tests and benchmarks have not run. GitHub documentation access works. The next session
must resume this same PR with a connected coding environment and inspect current
deployed state before implementing. Authenticated production metadata and actual
policy behavior remain future release checks. This does not complete WS-023.
