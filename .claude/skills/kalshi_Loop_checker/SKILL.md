---
name: kalshi_Loop_checker
description: Run one iteration of the recurring Kalshi strategy status loop — pull per-book paper P&L and data-collection freshness via the ops channel, update the carried-over suggestion list on the strategy-loop-status branch, and post a banner-delimited report in chat. Use when the 2-hourly strategy-status trigger fires or the user asks for a loop check / strategy status pass.
---

# Kalshi loop checker — one iteration of the 2-hourly strategy status loop

**Guardrails (absolute):** this loop REPORTS and SUGGESTS only. Never act on the
suggestions, never edit strategy code or config, never flip live switches, and never
push to the default branch (any push there redeploys the Railway worker). The user
reviews the suggestions and acts on them separately (their "fable" sessions). Status
state lives on the dedicated `strategy-loop-status` branch only.

## Chat report format — the banner comes FIRST

Every loop report posted in chat MUST start with a large divider banner inside a fenced
code block (so the `#` wall renders literally, visually separating updates):

````
```
########################################################
##  KALSHI LOOP — RUN #<n> — <YYYY-MM-DD HH:MM UTC>  ##
########################################################
```
````

`<n>` = previous run number from `docs/STRATEGY_LOOP_STATUS.md` + 1. After the banner:
a books table (settled n / P&L / open / one-word trend), a data-health line or table
(fresh / STALE / zero per collector), a 1-3 sentence headline read, then the current
suggestion list. Keep it tight — the durable detail goes in the status file, not chat.

## Procedure

### 1. Pull fresh data via the ops channel

Work on the `ops` branch (worktree at `/tmp/ops`; `git fetch origin ops && git reset
--hard origin/ops` first — another session may have moved it). Write ONE combined
read-only query to `ops/request.json`, push, then poll `ops/result.txt` (~30-90s):

```json
{"type":"db","sql":"WITH books AS (SELECT CASE WHEN strategy LIKE 'theta%' OR strategy='mmsell' THEN 'book:'||strategy WHEN strategy LIKE 'weather_con%' THEN 'book:weather_con(all)' WHEN strategy LIKE 'weather%' THEN 'book:weather_other(all)' ELSE 'book:legacy_ta' END AS item, count(*) FILTER (WHERE status='settled') AS a, round(coalesce(sum(pnl) FILTER (WHERE status='settled'),0)::numeric,2) AS b, count(*) FILTER (WHERE status='open') AS c, max(created_at) AS latest FROM paper_trades WHERE NOT legacy GROUP BY 1), data AS (SELECT 'data:crypto_spot_candles' AS item, count(*) FILTER (WHERE minute_ts>now()-interval '24 hours') AS a, count(distinct product) AS b, 0 AS c, max(minute_ts) AS latest FROM crypto_spot_candles UNION ALL SELECT 'data:crypto_ladder_snapshots', count(*) FILTER (WHERE captured_at>now()-interval '24 hours'), count(*) FILTER (WHERE model_p is not null AND captured_at>now()-interval '24 hours'), 0, max(captured_at) FROM crypto_ladder_snapshots UNION ALL SELECT 'data:weather_forecasts', count(*) FILTER (WHERE captured_at>now()-interval '24 hours'),0,0,max(captured_at) FROM weather_forecasts UNION ALL SELECT 'data:weather_observations', count(*) FILTER (WHERE captured_at>now()-interval '24 hours'),0,0,max(captured_at) FROM weather_observations UNION ALL SELECT 'data:weather_ensembles', count(*) FILTER (WHERE captured_at>now()-interval '24 hours'),0,0,max(captured_at) FROM weather_ensembles UNION ALL SELECT 'data:weather_bucket_snapshots', count(*) FILTER (WHERE captured_at>now()-interval '24 hours'),0,0,max(captured_at) FROM weather_bucket_snapshots) SELECT item,a,b,c,latest FROM books UNION ALL SELECT item,a,b,c,latest FROM data ORDER BY item","max_rows":50}
```

Columns: `a` = settled count (or last-24h rows for data:), `b` = settled P&L $ (or a
secondary count for data:), `c` = open positions. Follow-up drill-down queries (e.g. a
theta calibration slice) are allowed when a finding needs them — read-only only.
If a crypto table errors as nonexistent, the theta migration isn't deployed — lead with
that. If the ops channel is busy (another session's request in flight), wait for its
result commit before pushing yours.

### 2. Read prior state

`git fetch origin strategy-loop-status && git show FETCH_HEAD:docs/STRATEGY_LOOP_STATUS.md`
— gives the previous run number, last snapshot, and the carried-over suggestion list.

### 3. Interpret

- A collector is **STALE** if its latest row is older than ~3× its cadence (spot/ladder
  ~5min, forecasts/obs ~15min, ensembles ~60min, bucket snapshots ~5min); **zero rows**
  in 24h = collector down. Weather P&L moving only ~daily (~14:00 UTC batch) is normal.
- Small-n discipline: never call a book good/bad off one window; cumulative P&L on
  negative-skew books misleads — decompose win-rate vs tail-loss when it matters.
- The theta books (control + revisions) are **correlated** (they overlap on the same
  markets): judge each vs its own modeled-vs-realized tail rate, never sum them.
- Honor pre-registered gates (e.g. theta revision rule in `docs/THETA_THESIS.md`:
  evaluate at ≥~60 settled per book, keep only positive AND calibrated).

### 4. Update the carried-over suggestions

Keep still-valid ones (note how the picture shifted as n grew), drop resolved or
invalidated ones (say why in the file's footer line), add new ones sparingly.
Suggestions are recommendations for the user's fable sessions — concrete, evidenced,
and non-urgent unless something is actually broken.

### 5. Persist

Rewrite `docs/STRATEGY_LOOP_STATUS.md` (new snapshot replaces the old; suggestions
carry over) in the `/tmp/loopstate` worktree (`git fetch origin strategy-loop-status &&
git reset --hard origin/strategy-loop-status` first), commit, push to
`strategy-loop-status` ONLY.

### 6. Report + tidy

Post the banner-first chat report (format above). Then reset the ops channel to idle:
write `{"type": "noop"}` to `ops/request.json`, commit, push.
