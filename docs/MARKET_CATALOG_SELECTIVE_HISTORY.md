# Catalog selective history — option 3

**Workstream:** WS-023 · **Build OS:** v0.12 · **Status:** implemented and tested on PR #586; owner merge, catalog-only rollout and production verification pending.
**Owner direction:** Calvin approved option 3 in ChatGPT on 2026-10-11 America/Chicago.
**Implementation actors:** Codex (design), Claude Code (implementation, 2026-10-12), task-specific catalog scope. No independent review claimed.

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

## Implementation record — 2026-10-12 UTC

The coding environment worked in this session (the earlier blocker was `environment_offline`).
The branch was brought up to date with the default branch (WS-024 cost work through #595,
including the 3600/1800/86400 s evaluation and storage defaults) by merge; nothing was undone.

### What was built

| Piece | Where | Behaviour |
|---|---|---|
| Policy switch | `CATALOG_HISTORY_POLICY` (default `broad_full`) | `selective` takes effect only when membership for `selection-membership-v1` is complete. Unready, invalid or unset means full history for everything. |
| Membership | `selection_members`, `selection_events` | Scope `market`/`event`/`series`; reason and provider; who/what, source reference, selected/effective times; every select/deselect is an event. Counts kept transactionally in state. |
| Automatic relevance | `Store.evidence_page`, `Store.review`, `Store.migrate_review` | Live and paper evidence select their market; structured reviews select the reviewed object; registry provenance selects the series. Published **in the same transaction** as the write that creates it. |
| Bootstrap | `selection.bootstrap_page` | Registry → reviews → review drafts → evidence, bounded pages (2,000), resumable, atomic. Startup `verify_bootstrap` reopens it if an older image wrote evidence or reviews after completion (rollback safety). |
| Upsert decision | `Store.upsert_revision` | Runs under `BEGIN IMMEDIATE`; a concurrent membership publish cannot split the check from the write (tested with a held lock). |
| Compact observation | `compact-observation-v1` | Rule-bearing row (first sight or any rules/parent change): exact `RULE_FIELDS` (rules hash re-derivable), parent hashes, identity and provenance. Lifecycle row: status, result, settlement and expiry fields only. Never prices, sizes, volume or open interest. Own key namespace and `content_sha256`; `full_payload_retained=false`, `satisfies_payload_proof=false`. Current `objects` rows stay full. |
| Hydration | `hydration_targets`, `selection.hydrate_step` | Enqueued only when compact history actually exists. A few requests a cycle; market → live then historical endpoint; event; series → series then paged markets with persisted cursor. 404 everywhere is `unavailable`, never coverage. Provider throttling raises into the shared backoff without spending attempts. Series targets wait while bulk discovery is storage-paused. |
| Visibility | `/v1/status.history_policy`, dashboard "Collection scope", `/v1/objects/{k}/{t}/coverage`, revisions API `representations` + `coverage` | Policy configured/effective and transitions, membership readiness and phase, counts by reason and scope, full vs compact writes, hydration by status, per-object gaps (`compact_revisions_without_full_payload`, `observed_after_exchange_creation`, `hydration_unavailable_from_exchange`, …). |
| Research selection | `POST /v1/selection` (4 KiB body, bearer token, storage guard) and `GET /v1/selection` | Explicit select/deselect with actor, reason and provider (any strategy, not only MMSELL). Deselect ends only that research membership; automatic relevance and history are untouched. |
| Research cohorts | `GET /v1/research/cohort?series=&as_of=&population=all|selected|unselected&cursor=&limit≤100&include_rows=` | See below. |
| Sweeps | `Discovery.restart_completed_sweeps`, `reset_source_reconciliation` | A series/events sweep restarts only after it **completed** and 24 h passed since it began; a source re-read restarts only after the pass caught up. Legacy cursors keep the old clock, so a deploy does not restart a pass early. |
| Memory | `evaluators.refresh`, `Store.evidence_by_series` | Series-partitioned evaluators (MMSELL) read and evaluate one series at a time; any evaluator registered without `partition="series"` still receives the whole table. Results identical (tested). The `catalog_counts` log (an index scan of every object) runs every 15 min instead of every cycle. |

### Research cohort contract

One page = markets of one series, keyset-paged by ticker, with what the catalog knew **as of**
the requested time: the latest revision captured at or before `as_of` (with its representation),
whether rules changed afterwards, reviews created by then, the latest archived contract capture by
then (`governing_terms_proven: false`), selection memberships and whether they postdate `as_of`,
history coverage, evidence groups by source/strategy/arm/twin (paper outcomes masked until
`closed_at`; live rows by source collection time), up to 200 source IDs plus a fingerprint, and
current attributable live economics flagged when settlement or fills postdate `as_of`. Envelope:
`cohort_id` (hash of the definition), `page_fingerprint`, index-only population counts
(`prefix_population_complete` names a ticker/series mismatch), policy state, missing denominators
(unfilled live orders, paper candidates not entered, opportunity population) and explicit
statements that selection is not qualification, the selected sample is not an unbiased control,
paper and live are not pooled and XOS epoch poolability is the consumer's to honour. Confidence
stays `null`; nothing is qualified.

### Measurements

Production, dated 2026-10-11 ~23:30 UTC, before this change (Railway metrics and catalog logs):

| Measure | Value |
|---|---|
| market-catalog RAM, 24 h | avg 0.69 GB, max 1.00 GB (limit 1.0 GB); 7-day avg 0.83 GB |
| CPU, 24 h | avg 0.21 vCPU, max 0.44 |
| Collect cycle | ~70 s; evaluation of ~153.7k contexts hourly |
| Revisions | 7.02M → 7.40M rows/day (10-10 17:37 → 10-11 18:08 UTC); table pages +0.42 GB/day, PK index +47 MB/day |
| Objects | 3.72M → 3.80M rows/day; document bytes 4.40 → 2.95 GB (dictionary writes visibly active) |
| File / volume | 18.90 GB file, 1.19 GB reusable pages, 19.2 GB free of 39.4 GB |
| Discovery | events page 200 per cycle over 214k events: one sweep ≈ 21 h, close to the old 24 h reset |
| Source evidence (ops `catalog-sel-size-20261011a`, SELECT-only) | paper 138,024 rows / 42,117 markets / 773 series (729 new markets/24 h); live 2,931 fills / 2,868 markets / 250 series |

`CATALOG_DICTIONARY_COMPRESSION` exists on the service; its value is withheld from this session.
The 4.40 → 2.95 GB fall in object document bytes while rows grew is consistent with writes being on.
Not toggled.

Synthetic benchmark (`scripts/catalog_selective_benchmark.py`, this sandbox; **not** a production
saving and not Railway RSS):

| Workload | broad-full | selective | Ratio |
|---|---|---|---|
| 4,000 markets × 4 lifecycle states + 2 quote passes, 5% of series selected, dictionary on | 587 B/revision | 452 B/revision | revision bytes **0.77**, file growth **0.85** |
| Upsert latency (mean / p95) | 4.05 / 5.77 ms | 4.02 / 5.67 ms | unchanged |
| Same, dictionary off (the reader-first default) | 1,025 B/revision, 3.95 ms mean | 619 B/revision, 3.69 ms mean | revision bytes **0.60**, file growth **0.71** |
| Evaluation, 140,621 evidence rows / 773 series, largest series 13,417 rows (production's largest is 13,485: ops `catalog-series-skew-20261012a`) | whole table: 286 MB Python peak, 50 s | per series: 62 MB, 43 s | identical 14,446 contexts |
| Evaluation, 140,132 rows, flatter skew (largest 2,745) | 301 MB, 87 s | 30 MB, 64 s | identical 47,592 contexts |

Reading: selective history cuts future **revision** growth by roughly a quarter (dictionary on) to
two fifths (dictionary off) in this workload,
because every lifecycle change is still recorded, rule-bearing rows still carry the rules, and
dictionary compression had already removed most redundancy. Row count, index size and the
`objects` rewrite cost are unchanged. It is a modest disk lever, not the RAM lever. The larger RAM
effect expected from this PR is the evaluation peak (one series at a time) and the dropped
per-cycle counts scan; neither is a measured Railway result yet.

### Rollout (catalog only)

1. Owner merges after green CI. The catalog redeploys (watch path `kalshi_bot/catalog/**`).
   Policy stays `broad_full`: behaviour equals today's plus membership, cohorts and sweep fixes.
2. Verify on the catalog: `job:selection:bootstrap` progressing without error; within ~1.5 h
   (≈70 cycles at ~70 s) `history_policy.membership_ready=true` with ≈43k evidence markets and the
   registry series; `revision_writes.compact=0`; `discovery:events` cursor not reset mid-sweep;
   `/v1/research/cohort?series=<a traded series>` returns items; 24 h RAM and evaluation duration
   compared against the baseline above.
3. Then, as a separate owner step, set `CATALOG_HISTORY_POLICY=selective` on market-catalog only.
   Verify `policy_effective=true`, compact writes rising, hydration backlog draining, and the daily
   revisions rows/bytes from the `catalog_storage_detail` log against 10-10 → 10-11.
4. Rollback: unset the variable (full history resumes for all writes). Compact rows, membership and
   targets remain and stay readable. An older image can still read and write the store (new tables
   are additive; `evidence` is unchanged); on the next deploy of this code `verify_bootstrap`
   reopens membership if that image wrote evidence or reviews. No space is reclaimed by rollback.

### Limits stated plainly

- Compact rows can never satisfy a full-payload or historical-term requirement; consumers read
  `representation`. Pre-policy rows carry no marker and are full snapshots.
- Hydration recovers what the exchange still serves *now*; it cannot recreate earlier versions,
  execution-time conditions or unobserved history. Archived markets beyond the historical endpoint
  stay `unavailable`. Series hydration uses the live `/markets` listing only.
- Cohort population is the ticker-prefix range of one series; the response counts any mismatch with
  the stored series column. Selection filters use current membership; an item says when its
  selection postdates `as_of`. The catalog keeps the latest version of each imported row, so earlier
  row states are not reconstructable; paper outcomes are masked by `closed_at`.
- Current document captures are dated observations, not proof of the terms that governed an older
  execution. Imported economics do not prove exchange-account coverage. Independence, exact-rule
  reviews and calibration are unchanged by this PR: no new evidence was produced for them, and no
  score was invented.

Validation: catalog, selection, documents, readiness, economics and headroom tests (227) plus the
full repository suite; Ruff; JavaScript syntax; a headless-Chromium render of the dashboard at
1280 px and 390 px (scope panel rendered, no script errors, no horizontal scroll). Remote tree and
final-head CI are recorded on PR #586. This does not complete WS-023.

## Retention — owner decision 2026-10-21 (DEC-038)

The owner chose to thin superseded history after the 2026-10-21 audit. That reverses rule 7 above
("no background job may delete existing ... revisions") for one narrow class only. Code:
`kalshi_bot/catalog/retention.py`; tests: `tests/test_catalog_retention.py`.

**Audit (catalog logs, 2026-10-21 ~12:55Z).**
- The volume is 39.4 GB with 9.7 GB free. Bulk discovery has been paused since free space fell
  below its 20% floor; it resumes at 25% (9.85 GB).
- The database file is 28.4 GB:

| Table or file | On disk | Rows |
|---|---|---|
| `revisions` (+ primary key) | 17.0 + 1.8 GB | 13.2M |
| `objects` (+ indexes) | 7.3 + 0.6 GB | 6.97M (6.08M markets, 873k events, 15k series) |
| `assessments` | 0.68 GB | 554k (161k current contexts) |
| contract PDFs | 0.65 GB | 5.3k blobs |
| other tables | ~0.3 GB | — |
| `catalog.sqlite3.before-compression-v1` (outside the DB) | 1.27 GB | — |

- Selection covers 47,208 markets plus 140 series. Every other market is inventory.
- 5.63M full revisions were written in the 9 days to 10-21, under policy `broad_full`.

**What a pass may remove.** All three items are gated by `CATALOG_RETENTION_MODE`.
1. **The pre-compression backup.** Removed only once `storage:compression.complete` is set.
   `backup_before_compression` then returns `None` instead of rebuilding a full-size copy.
2. **Superseded assessments.** These are rows no `current_assessments` entry points at, with
   `as_of` older than `CATALOG_RETENTION_ASSESSMENT_DAYS` (default 7).
3. **Revisions of settled, unselected markets.** A market qualifies only if its status is
   `finalized` or `settled` and no active member covers it, by market, event or series. The
   treatment comes from `CATALOG_RETENTION_REVISIONS`:
   - `notes` turns each full snapshot into the compact observation above.
   - `final_only` deletes the snapshots, but only when all of them carry the current object's
     rules hash, so no rule or parent change was ever observed. The current object's full final
     document is then the record, and `coverage_in` reports
     `revisions_retired_current_object_only`.
   - A market with a rule change is untouched under `final_only`.

**What every pass keeps.**
- Current objects.
- Every open market.
- Every selected market, event and series.
- All event and series revisions.
- Evidence, reviews, current assessments and contract documents.

**Measured before building (why both treatments exist).** On a realistic market layout, a full
snapshot under storage encoding v2 is about 520 bytes. A rule-bearing compact note is about
710 bytes and a lifecycle note about 350 bytes: the hashes they carry compress poorly, and the
v2 dictionary was built for full layouts. Notes therefore save little on v2 rows and somewhat
more on older v1 rows, and only the dry run can measure it. The same measurement means
`CATALOG_HISTORY_POLICY=selective` is not a meaningful disk lever under v2.

**Mechanics.**
- One background thread runs a resumable pass in ticker order, 100 markets per short write
  transaction. In execute mode the pass state is stored in that same transaction.
- Rows are deleted, and for notes re-inserted in their original order. An in-place `UPDATE` that
  shrinks a row frees no page; measured on a 20k-row table, the freelist stayed at 0.
- Freed pages go to the freelist and are reused before the file grows. `headroom.measure` counts
  them for the **bulk** tier only; the critical writes tier still reads the volume alone. The file
  shrinks only with a VACUUM, which needs free space about equal to the result, and none is run.

**Rollout.**
1. Merge. Set `CATALOG_RETENTION_MODE=dry_run`, then read `catalog_retention_report` (also
   stored as `retention:last_report`). Nothing changes.
2. The owner picks a treatment. Then set `CATALOG_RETENTION_REVISIONS` and
   `CATALOG_RETENTION_MODE=execute`. Optionally set `CATALOG_RETENTION_REPEAT_HOURS=24`, so newly
   settled inventory is thinned daily.
3. Verify:
   - `reusable_bytes` rising in `catalog_storage`;
   - `bulk_headroom_bytes` above the resume point in the storage guard;
   - `discovery:*` running again.
4. Rollback: unset the mode. Removed rows are not recoverable from the catalog. Kalshi serves
   only current market state, which the current object already holds.

**Result — 2026-10-28.**
- **Dry run.** The first attempt (Oct 27) stopped at 1.64M of 6.08M markets: a state write met
  the evaluation job's lock outside the retry. [#681](https://github.com/50thycal/kalshi_bot/pull/681)
  fixed that, and the run resumed from its cursor. Measured notes at **73%** of a full row (a
  27% saving), so the projections were `final_only` ≈ 8.9 GB and `notes` ≈ 4.6 GB.
- **Owner choice:** `final_only` with `CATALOG_RETENTION_REPEAT_HOURS=24`.
- **Execute pass** (14:38–18:17 UTC):
  - removed the 1.27 GB backup;
  - deleted 399,653 superseded assessments;
  - deleted 4,737,257 revision rows from 3,273,093 markets.
- **What it kept:**
  - 1,736,191 selected markets;
  - 164,874 open markets;
  - 933,986 markets whose rules changed.
- **Disk and service after the pass:**
  - free plus reusable space went from 9.64 GB (24.5%) to **17.77 GB (45%)**: 10.90 GB on the
    volume and 6.87 GB on the freelist;
  - bulk discovery has run since 14:43;
  - memory stayed at its 1 GB cap, as before;
  - the lock retry fired about every 30 minutes and recovered each time.
- **Realized vs estimate:** about 8.1 GB of the 8.97 GB estimate. Partially emptied pages stay
  allocated.
