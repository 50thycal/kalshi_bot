# WS-024 — Railway RAM budget: ~$15/month, $10 stretch

**Phase:** BUILDING
**Status:** Active
**Created:** 2026-10-08
**Updated:** 2026-10-08 (step 1 built)
**Build OS:** v0.12
**Session role:** Live Ops (`.claude/sessions/live-ops.md`)

## Goal

Bring Railway RAM spend for the `kalshi-bot` project from ~$26/month today (~$55 before
2026-10-07) to **≤ $15/month**, with **$10/month as a stretch**, without changing what any
trading book decides and without weakening a safeguard. RAM is billed at ~$10 per GB-month,
so $15 ≈ 1.5 GB and $10 ≈ 1.0 GB average across all services.

## Context

Operator handoff 2026-10-07/08 (the session that produced PR [#547](https://github.com/50thycal/kalshi_bot/pull/547)).
RAM was 82% of the bill. What was established, with sources a session can re-open:

- **Postgres was the largest cost** (~$33/month), with a 4.85 GB 7-day average against a
  128 MB `shared_buffers`. Most of that was OS cache holding hot data. The DB is ~35 GB on
  disk, but disk is cheap (~$1.6/month).
  - The operator capped Postgres at **2 GB** on 2026-10-07 (Railway → Postgres → Settings →
    Replica Limits). It now runs ~0.8 GB.
- **The hot reads were churn, not the big tables.**
  - `incentive_programs` (520 MB) had 6.3 TB read and 128M updates over its life.
  - `positions` (369 MB) had 544 GB read.
  - The 8.5 GB `incentive_book_events` tape was nearly cold.
  - Sources: ops results `memcost-tables-1`, `memcost-io-1`, `memcost-progs-1`.
- **PR #547 (merged, deployed 2026-10-07 03:48Z)** cut the `current_programs` load: SQL
  end-date filter (36k → 6k rows) and deferred JSON payloads. It also stopped writing
  `incentive_book_events` and `incentive_shadow_events` (config flags, default off).
- **The operator deleted `ollama` (unused; evo routed to OpenRouter) and, unintentionally,
  `evo bot`.** Evo ran only the liquidity-incentive **shadow collector** (`EVO_ENABLED=false`).
  That collector was the **only writer of `incentive_programs`**, which the live `Alimm1`
  book reads. **The list has been frozen since 2026-10-07 02:47Z.** The shadow
  quotes/outcomes evidence also stopped (last row 02:49Z).
  - The operator wants `Alimm1` to **keep running**: it is researching incentive harvesting.
- **Per-service RAM (Railway metrics, 24h to 2026-10-08):**

| Service | Now | Floor | Lever |
|---|---|---|---|
| Postgres | 0.8 GB (2 GB cap) | 0.5–0.7 | smaller hot set, then 1 GB cap |
| main (live worker) | 0.45 GB avg, 1.2 max | 0.45 | **none — leave it** |
| market-catalog | **1.0 GB, pinned at its 1 GB limit** | 0.1–0.3 | scheduled runs, stop loading everything |
| live-dash | 0.3 GB | ~0.05 | Railway sleep (serverless) |
| website | 0.07 GB | ~0.02 | Railway sleep |
| **Total** | **~2.6 GB ≈ $26/mo** | **1.1–1.5 GB ≈ $11–15/mo** | |

## Current Mental Model

```text
main (BOT_MODE=live) ──► IncentiveLiveRunner.cycle ──► progs.current_programs()  ◄── reads
                                                   └─► repo.market_close_time()  ◄── reads (executor slot rule)
incentive_programs  ◄── written ONLY by progs.run_discovery()
                        └─ previously called by the shadow collector on evo (GONE) → frozen

market-catalog (SQLite /data, 20 GB volume) ── collect() every 30 s
   ├─ discovery pages (series/events/updates) → objects (2.1M) + revisions (2.45M) ≈ 7.7 GB+
   ├─ import paper/live from Postgres (currently refused: privilege guard, WS-023)
   └─ every 300 s refresh(): store.evidence() loads ALL evidence into memory
```

## Decisions Made

- **Target ≤ $15/month RAM; $10 is a stretch.** Owner, 2026-10-08. Nothing that trades is cut
  to reach it.
- **`Alimm1` keeps running.** Owner, 2026-10-08. Its program list must be restored, not the
  book stopped.
- **`main` gets no memory cap below 1.5× its observed peak.** It is the real-money worker, and
  an OOM kill mid-cycle strands orders.
- **No `positions` change-detection.** The `max_total_exposure` breaker
  (`live_total_exposure`, 48h lookback) and `live_realized_pnl_today` (since UTC midnight)
  would under-count. Recorded in PR #547.
- **D2 = option B: keep market-catalog continuous; fix its memory and disk use.** Owner,
  2026-10-08. D3 and D4 stay open.
- **`incentive_programs.last_seen_at` bump semantics stay.** The XOS metric
  `incentive_programs_observed` reads it; changing it is Platform Change Review.

## Open Decisions

- **D1. Restore discovery inside `main`.** Recommend **yes**. The armed runner calls
  `progs.run_discovery()` itself when no `incentive_discovery_cycles` row is newer than
  `liquidity_incentive_discovery_seconds` (300 s). Properties:
  - REST only, through `IncentiveReadOnlyKalshi`, inside `session.begin_nested()` so a
    failure rolls back discovery alone.
  - A no-op while any other writer keeps the table fresh.
  - Behind a config flag `liquidity_incentive_runner_discovery` (default true).
  - It touches the live runner, so the **operator approves the edit in-session**; it was
    blocked by the permission policy on 2026-10-07. Owner merges.
- **D2. market-catalog — DECIDED 2026-10-08: option B** (continuous; fix memory and disk).
  Cron (option A) was declined for now. WS-023 owns the catalog's design; this workstream
  changes only its resource use. Revision retention (deleting old snapshots) is **not** part of
  the step-2 PR: it deletes data and changes WS-023's "immutable snapshots" rule, so it is an
  owner decision of its own.
- **D3. Retire the unread tapes.** Archive-then-truncate per `docs/TELEMETRY_ARCHIVE_RUNBOOK.md`
  (bucket `kalshi-bot-research-archive`, empty today), or truncate without archiving.
  - Tables: `incentive_book_events` (8.5 GB) and `incentive_shadow_events` (0.6 GB).
  - Deletion needs **explicit operator approval** either way.
- **D4. Postgres cap 2 GB → 1 GB** after D1–D3. Recommend yes, if the 24h memory after D3
  stays under ~0.8 GB.

## Assumptions

- Railway bills memory as reported (cgroup usage including page cache). The 2 GB Postgres cap
  showing 0.8 GB supports this; re-check against the Usage page after a week.
- The live-dash and website services do no background work, so sleeping is safe. **Verify
  before enabling** (`kalshi_bot/livedash`, `railway.livedash.json`).
- The 2026-10-07 numbers above are a snapshot. Re-measure before acting; never act on them as
  if current.

## Non-Goals

- Changing any trading decision, gate, epoch, metric definition, or live safeguard.
- Recreating the evo service, unless the owner asks.
- Reducing `main`'s memory.
- Catalog feature work (WS-023 owns it).
- CPU, egress and volume costs (small; out of scope unless they move).

## Acceptance Checks

1. **Discovery restored.**
   - `incentive_discovery_cycles` has a row less than 10 min old and stays fresh for 24h.
   - `incentive_programs.last_seen_at` advances.
   - `main` logs show no `incentive smoke cycle failed`.
   - Tests cover fresh/stale/failure paths. PR merged by the owner.
2. **market-catalog under 0.3 GB average** over 24h and no longer pinned at its limit. Volume
   growth rate measured and either bounded or recorded with an owner decision (D2).
3. **live-dash and website sleep when idle.** 24h average RAM ≤ 0.1 GB combined, and both still
   load when opened.
4. **Postgres:** D3 executed as approved and the cap lowered per D4. 24h average ≤ 0.8 GB with
   no query-timeout errors in `main` logs.
5. **Budget guard:** a Railway usage alert or limit is configured (owner does it in the
   dashboard; the session gives exact steps).
   - **Done when** the 7-day all-service RAM average ≤ 1.5 GB (≤ $15/month), measured with
     Railway `get-service-metrics`.
   - Each step records its before/after RAM in this file.

## Build Card

Inline: the Acceptance Checks above, worked in order **1 → 3 → 2 → 4 → 5**. Item 1 is the
real-money/research blocker; 3 is the cheapest; 2 needs D2.

## RAM log (Railway `get-service-metrics`, `MEMORY_USAGE_GB`)

Every step records a before and an after here. Windows end at the stated time.

### Baseline — before step 1 (24h to 2026-10-08 ~02:45Z)

| Service | 24h avg | 24h max | Limit | Note |
|---|---|---|---|---|
| Postgres | **1.24** | 3.43 | 2.0 | 6h avg **1.50** (min 0.75, max 1.77) — above the 0.8 GB the handoff saw; the 24h window still includes pre-cap samples |
| main | 0.42 | 0.85 | 32 | |
| market-catalog | **1.00** | 1.00 | 1.0 | pinned at its limit all day; disk 11.3 GB |
| live-dash | 0.28 | 0.71 | 32 | |
| website | 0.07 | 0.14 | 32 | |
| **Total** | **≈ 3.0 GB ≈ $30/mo** | | | target ≤ 1.5 GB |

### Step 1 — discovery restored in `main`

- Before: as baseline (main 0.42 avg / 0.85 max; Postgres 1.24 avg).
- **Deployed 2026-10-08 04:28Z** ([#553](https://github.com/50thycal/kalshi_bot/pull/553) merged
  04:27Z; carried forward by #551/#554 deploys). First pass, cycle 7115 at 04:30:19Z: listed
  35,674, new 100, **deferred 4,773**, gone 4,385, errors 0, 12.0 s. `max(last_seen_at)` moved
  2026-10-07 02:47Z → 2026-10-08 04:30Z (ops `ws024-check1-c`).
- The 4,385 "gone" are real: of programmes marked gone in that hour, only **3** have not ended —
  the rest ended during the 26h freeze. The 4,773 deferred drain at 100/pass (~4h).
- No `incentive runner discovery failed` / `incentive smoke cycle failed` in `main` logs; the
  same cycle still placed and mirrored (Alimm1 placed 1, twin opened 1).
- Correction: the account is on the **Advanced** API tier (300 reads/s refill, per the worker's
  startup `api limits probe`), not Basic. The per-pass bound still stands for cycle time.
- **Check 1 PASSED over 24h** (ops `ws024-24h-a`, 2026-10-08 04:30Z → 2026-10-09 04:46Z):
  - 233 passes (8–11 per hour; the live cycle is slightly longer than 5 min), **0 errors**.
  - The 4,773-term backlog drained to 0 by 2026-10-08 15:00Z. The longest pass was 18.3 s.
  - The newest pass was 40 s old at read time, and `max(last_seen_at)` was current.
  - No `incentive runner discovery failed` / `incentive smoke cycle failed` in `main` logs.

### 24h after-RAM (2026-10-09 04:45Z, Railway 24h average / max, GB)

| Service | Baseline (10-08) | After (10-09) | Note |
|---|---|---|---|
| Postgres | 1.24 / 3.43 | **1.64 / 1.98** | 6h avg 1.74; pinned near its 2 GB cap. Discovery write churn is back, plus a busier day. |
| main | 0.42 / 0.85 | 0.58 / 0.92 | Up 0.16: the discovery pass, and more deploys (6 merges in 24h). |
| market-catalog | 1.00 / 1.00 | 0.86 / 1.00 | 6h avg 0.73, min 0.35: no longer pinned all day. #557 live since the 10-08 18:32Z deploy. |
| live-dash | 0.28 / 0.71 | 0.19 / 0.36 | `SLEEPING` at 04:25Z. Every merge redeploys and wakes it. |
| website | 0.07 / 0.14 | 0.07 / 0.14 | `SLEEPING` at 04:10Z. The metric repeats its last value while asleep. |
| **Total** | **≈ 3.0** | **≈ 3.3** | **Worse.** Postgres alone gave back more than the other steps saved. |

### Step 3 — live-dash and website sleep (started 2026-10-08)

- **website** (`python -m kalshi_bot.dashboard`): no background work — a `ThreadingHTTPServer`
  that reads Postgres per request. **App sleep enabled 2026-10-08 ~03:06Z** (Railway
  `sleepApplication=true`, redeployed). Before: 0.07 GB avg / 0.14 max. After: _pending 24h._
  The sandbox cannot reach `*.up.railway.app` (egress policy), so "still loads" is checked
  from Railway HTTP logs and by the owner opening the URL.
- **live-dash** (`python -m kalshi_bot.livedash`): **cannot sleep as deployed.**
  `OverviewCache` rebuilds the landing payload from Postgres every 120 s on a daemon thread,
  forever — continuous outbound traffic, and Railway sleeps a service only after ~10 min with
  none. Fix: the refresher idles 15 min after the last request (start counts as one) — the
  step-3 PR. Enable sleep on live-dash only after it deploys.
- website after enabling sleep: no HTTP requests, no network flows and 0 TX bytes from 03:10Z
  to 04:10Z, but Railway still reported the deployment `SUCCESS`, not `SLEEPING`, at 04:10Z.
  Owner to confirm in the dashboard; the 24h average settles it. Before: 0.28 GB avg /
  0.71 max. Open tabs poll every 60 s (`overview.html`, `index.html`), so an open tab keeps it
  awake — by design.
- Both deploy from the default branch, so every merge redeploys (wakes) them.

### Step 2 — market-catalog (D2 = B)

**2026-10-09 — disk emergency.** The database file is growing much faster than its documents:

| When | File size | Growth rate |
|---|---|---|
| 10-08 03:01Z | 8.57 GB | 0.8 GB/day |
| 10-08 12:09Z | 9.36 GB | 2 GB/day |
| 10-09 04:45Z | 12.11 GB | 4 GB/day |
| 10-09 11:54Z | 14.72 GB | about 9–11 GB/day |

- The document tables grew only ~1 GB/day, so the rest is in tables the storage report did not
  measure.
- The acceleration began after the 10-08 04:44Z deploy of #552, the catalog evidence review that
  added `live_economics` and ledger assessments. Assessments went from 183k to 359k rows in 12h.
- Contract-PDF capture (#565) is bounded at about 0.5 GB/day and started later, so it is not the
  driver.
- **Volume resized 20 → 40 GB** (D2-B "more disk", owner-approved) on 10-09 ~11:58Z through
  Railway's agent. Committing it redeployed market-catalog (`885b2a96`, same commit, healthy).
  At the current rate 40 GB buys only ~2–3 days.
- **Diagnostic PR:** the full storage pass now reports `objects_bytes`, the bytes per table and
  index from SQLite `dbstat`, and it runs once on the first boot after deploy. That names the
  growing table. Fixing it is WS-023's code, plus any deletion, which is the owner's call.


**Diagnosis from the code (2026-10-08):**

- `report_storage` ran `Store.storage()` every 300 s, and `storage()` sums
  `length(CAST(document AS BLOB))` over every document table. That is a full read of ~6 GB of
  documents in an 8.5 GB file, every five minutes, which keeps the file's pages hot. The
  service sat at exactly its 1.0 GB limit without being OOM-killed, which is the signature of
  reclaimable file cache, not heap.
- The daily source reconciliation re-reads all ~130k paper evidence rows, 1,000 per cycle.
  Each re-read was `INSERT OR REPLACE`d and counted as "changed", so the full evaluation
  (`store.evidence()` materialises every row) ran almost every cycle for most of the day.
  The heap peak then stayed resident, because CPython/glibc do not return it on their own.

**Step-2 PR (resource use only; no deletion, no semantic change):**

- `storage(detail=False)` measures only the file, pages and volume. Per-table figures carry
  from the last full pass, with `tables_captured_at`. The full pass runs every
  `CATALOG_STORAGE_DETAIL_SECONDS` (default 86,400).
- `evidence_page` skips a row whose stored content is identical, and counts real changes in
  `evidence:changes`. Evaluation triggers on that counter rather than on rows fetched. This
  removes the rewrite churn.
- `release_memory()` (gc + glibc `malloc_trim`) runs after each evaluation, so the evaluation
  peak is returned to the OS instead of staying resident.
- Evaluation cadence is now `CATALOG_EVALUATION_SECONDS`, default 300, which is unchanged.
  The collect loop takes ~7 min, longer than 300 s, so the evaluation still runs every cycle.
  Fewer evaluations needs that variable raised, e.g. to 1800. Real evidence changes would
  still trigger one at once; only time-based changes would wait. That is an owner/WS-023
  call, not part of the PR.

**Disk:** the owner live-resizes the `market-catalog-data` volume in the dashboard (Railway →
market-catalog → Volume → Settings → size). This session's Railway tools cannot. About 1.3 GB
of the volume is a file other than the database, most likely the one-time
`catalog.sqlite3.before-compression-v1` backup. Removing it is a deletion, so it waits for the
owner.

**Evidence before the change:**

- RAM 1.00 GB avg, pinned at its 1.0 GB limit all 24h. CPU ~0.02–0.12 vCPU.
- **Volume growth is the nearer risk.** Disk 7.89 → 11.34 GB over 48h. The SQLite file grew
  8,563,892,224 → 8,568,143,872 bytes in 7.5 min (≈ 0.8 GB/day). Volume 19.7 GB, 9.8 GB free
  at 03:01Z → **full in roughly 10–12 days** at this rate unless bounded (WS-023 owns the
  catalog's design; this is the D2 input).
- Rows at 03:01Z: objects 2.21M (2.07M markets), revisions 2.65M, evidence 133k,
  assessments 183k. An `evaluation` pass over ~146.5k contexts runs every ~7 min.

## Implementation State

PR #547 merged (precursor).

**Step 1 (D1) — merged and deployed 2026-10-08 (#553); check 1 passing at first read, 24h
freshness still to confirm.** Owner approved the live-runner edit in-session 2026-10-08.

- `IncentiveLiveRunner._refresh_programs` runs after exits and before every read of the
  programme list (so a full book still refreshes). It runs only when the runner is armed, the
  flag `LIQUIDITY_INCENTIVE_RUNNER_DISCOVERY` (default true) is on, and no
  `incentive_discovery_cycles` row is newer than `LIQUIDITY_INCENTIVE_DISCOVERY_SECONDS` (300 s).
- REST only, through `IncentiveReadOnlyKalshi`; inside `session.begin_nested()`; a failure is
  logged (`incentive runner discovery failed`), returned in the cycle summary, and retried on
  the cadence, not every cycle.
- **Added beyond D1, for the real-money cycle:** a per-pass bound,
  `LIQUIDITY_INCENTIVE_RUNNER_DISCOVERY_MAX_NEW_TERMS` (default 100). Measured on the evo
  history (ops `ws024-disc-1`, 2026-09-29→10-07): passes averaged 12–17 s, but spiked to
  ~250 s with up to ~4,800 new/changed terms (two GETs each). Past the bound a programme is
  deferred to the next pass with its current row untouched; steady state (~15–25 per pass)
  never reaches it. The catch-up after the 2026-10-07 freeze drains at ≤ 1,200 terms/hour.
- `run_discovery(load_current=False)` skips the unfiltered current-programmes load the runner
  never reads (the read #547 cut elsewhere).

## Review State

Not started. Solo mode (`DEC-011`): no independent review exists; the owner accepts at merge.

## Notes for the implementing session

- **Tools:** Railway MCP (metrics, logs, deployments, `describe-environment`; project
  `3764f048-7be5-4ca1-81a5-8a8a0b81e842`, production env `88509d19-6da5-4f16-b1fb-63dff70ecb7a`)
  and the ops branch (`db`/`env` reads).
  - Railway `delete-volume` timed out twice on 2026-10-07; do destructive Railway actions in
    the dashboard.
- The market-catalog service deploys from branch `codex/catalog-storage-diagnostics-20261005`,
  not the default branch.
- **Possible metric defect, worth an XOS issue for Platform Change Review:**
  `incentive_programs_observed` counts every paid-out programme as "observed" in every window,
  because the paid_out listing re-bumps `last_seen_at` forever.

## Related Decisions

`DEC-011` (solo mode, active-work limit), `DEC-012` (standing authorizations), `DEC-015`/`DEC-016`
(liquidity-incentive shadow and live book), `DEC-029` (market catalog).

## Related PRs

- [#547](https://github.com/50thycal/kalshi_bot/pull/547): incentive read-load cut and tape
  flags (merged).

## Next Step

1. **Catalog disk (urgent, ~2–3 days at 40 GB):** owner merges the diagnostic PR. Live Ops reads
   `objects_bytes` from the first `catalog_storage` log after deploy and names the growing table.
   WS-023 fixes its writer; any deletion is an owner decision.
2. **Postgres** is now the largest lever and is pinned near 2 GB. D3 (retire the unread tapes)
   then D4 (cap step-down) are the owner's calls. Raising the cap is not on the table, since RAM
   is billed on use.
3. Checks 1 and 3 are done (live-dash and website `SLEEPING`). Check 5 (Railway usage alert) is
   still to give the owner as dashboard steps.
