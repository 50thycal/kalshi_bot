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
- **D2. market-catalog freshness.** It is running continuously and pinned at its cap.
  - A scheduled run (Railway cron, a few times a day) reaches the $10 stretch.
  - Continuous running keeps it near $15.
  - Owner chooses; WS-023 owns the catalog's design, so this links there and does not
    restate it.
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
- After: _pending — measure 24h after the PR deploys._ Expect a small rise in `main` (one pass
  every 5 min loads ~36k light terms rows and the ~36k-programme listing, transiently) and in
  Postgres (the discovery write churn the evo service used to generate returns).

## Implementation State

PR #547 merged (precursor).

**Step 1 (D1) — built, PR open, owner approved the live-runner edit in-session 2026-10-08.**

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

Owner merges the step-1 PR. Then Live Ops verifies acceptance check 1 (fresh cycle row < 10 min,
`last_seen_at` advancing, no `incentive runner discovery failed` / `incentive smoke cycle
failed` in `main` logs, `deferred_new_terms` draining to zero), records the after-RAM above,
and moves to step 3 (live-dash / website sleep — verify no background work first).
