---
name: kalshi_loop_checker_phase_3
description: Run one iteration of the recurring Kalshi strategy status loop — pull per-book paper P&L and data-collection freshness via the ops channel, update the carried-over suggestion list on the strategy-loop-status branch, and post a banner-delimited report in chat. Use when the 8-hourly strategy-status trigger fires or the user asks for a loop check / strategy status pass.
---

# Kalshi loop checker (phase 3) — one iteration of the 8-hourly strategy status loop

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
################################################################################
################################################################################
################################################################################
            KALSHI LOOP — RUN #<n>  ·  <YYYY-MM-DD hh:mm AM/PM CDT>
################################################################################
################################################################################
################################################################################
```
````

**All times in reports are CENTRAL TIME (CDT/CST — `TZ=America/Chicago`), always.** The
database stores UTC; convert before reporting (`TZ=America/Chicago date`, or in SQL
`... AT TIME ZONE 'America/Chicago'`). Label internal branch-file snapshots the same way.

`<n>` = previous run number from `docs/STRATEGY_LOOP_STATUS.md` + 1 (times there are CDT too). After the banner:
a books table (settled n / P&L / open / one-word trend), a data-health line or table
(fresh / STALE / zero per collector), a 1-3 sentence headline read, then the current
suggestion list — INCLUDING the idea-model queue item (step 3b) so gate-blocked ideas stay
visible every run, not just when something changes. If a gate cleared this run, lead the
headline with it (see step 3b) — don't bury it at the bottom of the suggestion list. Keep it
tight — the durable detail goes in the status file, not chat.

## Procedure

### 1. Pull fresh data via the ops channel

Work on the `ops` branch (worktree at `/tmp/ops`; `git fetch origin ops && git reset
--hard origin/ops` first — another session may have moved it). Write ONE combined
read-only query to `ops/request.json`, push, then poll `ops/result.txt` (~30-90s):

```json
{"type":"db","sql":"WITH books AS (SELECT CASE WHEN strategy LIKE 'weather_concity%' THEN 'book:weather_concity(all)' WHEN strategy LIKE 'weather_con%' THEN 'book:weather_con(all)' WHEN strategy LIKE 'weather%' THEN 'book:weather_other(all)' ELSE 'book:'||strategy END AS item, count(*) FILTER (WHERE status='settled') AS a, round(coalesce(sum(pnl) FILTER (WHERE status='settled'),0)::numeric,2) AS b, count(*) FILTER (WHERE status='open') AS c, max(created_at) AS latest FROM paper_trades WHERE NOT legacy GROUP BY 1), data AS (SELECT 'data:crypto_spot_candles' AS item, count(*) FILTER (WHERE minute_ts>now()-interval '24 hours') AS a, count(distinct product) AS b, 0 AS c, max(minute_ts) AS latest FROM crypto_spot_candles UNION ALL SELECT 'data:crypto_ladder_snapshots', count(*) FILTER (WHERE captured_at>now()-interval '24 hours'), count(*) FILTER (WHERE model_p is not null AND captured_at>now()-interval '24 hours'), 0, max(captured_at) FROM crypto_ladder_snapshots UNION ALL SELECT 'data:weather_forecasts', count(*) FILTER (WHERE captured_at>now()-interval '24 hours'),0,0,max(captured_at) FROM weather_forecasts UNION ALL SELECT 'data:weather_observations', count(*) FILTER (WHERE captured_at>now()-interval '24 hours'),0,0,max(captured_at) FROM weather_observations UNION ALL SELECT 'data:weather_ensembles', count(*) FILTER (WHERE captured_at>now()-interval '24 hours'),0,0,max(captured_at) FROM weather_ensembles UNION ALL SELECT 'data:weather_bucket_snapshots', count(*) FILTER (WHERE captured_at>now()-interval '24 hours'),0,0,max(captured_at) FROM weather_bucket_snapshots UNION ALL SELECT 'data:xgame_matches', count(*) FILTER (WHERE created_at>now()-interval '24 hours'), count(*), 0, max(created_at) FROM game_market_matches UNION ALL SELECT 'data:xgame_tapes', count(*) FILTER (WHERE captured_at>now()-interval '24 hours'), 0, 0, max(captured_at) FROM game_tape_snapshots) SELECT item,a,b,c,latest FROM books UNION ALL SELECT item,a,b,c,latest FROM data ORDER BY item","max_rows":50}
```

Columns: `a` = settled count (or last-24h rows for data:), `b` = settled P&L $ (or a
secondary count for data:), `c` = open positions. Follow-up drill-down queries (e.g. a
theta calibration slice) are allowed when a finding needs them — read-only only.
If a crypto/game table errors as nonexistent, that migration isn't deployed — lead with
that. For `data:xgame_matches` the `b` column is the TOTAL match count (not last-24h): b=0
means the XGAME collector is matching no games — cross-check the `xgame collector` log line
(`kal_games`/`pm_games`/`matched_new`) to see whether it's a no-games-in-window lull or a
broken matcher. If the ops channel is busy (another session's request in flight), wait for its
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

### 3a. Reconcile every book against the registry — flag UNTRACKED books

Read `docs/BOOK_REGISTRY.md` (`git show FETCH_HEAD:docs/BOOK_REGISTRY.md` off the default
branch, or the local checkout). It is the canonical index of every book that should be trading,
with each one's thesis pointer + pre-registered gate. For **every `book:<tag>` row** step 1's
query returned, confirm there is a matching registry row (join on the `tag` column). **Any book
with settled or open trades but NO registry row is UNTRACKED** — a book started trading without
its rationale/gate written down. Do not silently reverse-engineer it: **lead the chat headline
with it** ("UNTRACKED book `<tag>` — N open, no registry row; needs a thesis + gate registered")
and add a suggestion that a fable/`kalshi-strategy` session backfill its `BOOK_REGISTRY.md` row.
This reconciliation is the loop's guard against exactly the case that created this step: a book
appearing from a parallel build session with no visible rationale. (Registry rows also tell you
each book's gate for step 3b, so read it before sweeping the gates.)

### 3b. Check every ACTIVE EXPERIMENT — books, decision points, AND gate-blocked ideas

"Active experiment" is broader than the books table: it is anything with a pre-registered
gate or decision point that hasn't resolved yet, wherever it's tracked. Sweep all of these
every run, not just the paper-trades rows:

- **Trading-book gates** (from step 1's query + `docs/THETA_THESIS.md` / `IDEA_MODEL_*.md`
  pre-registrations) — mmsell3 n≥150, theta4's decision point, weather_concity n≥120, and any
  future A/B variant's own gate. Report `n / gate / distance-to-gate` for each, not just P&L.
- **The idea-model queue** — the carried-over suggestion item that lists ideas held behind one
  of the gates above (e.g. "MMX behind mmsell3", "NEST behind theta4" — see the current queue
  item in `docs/STRATEGY_LOOP_STATUS.md`'s suggestion list, and the source docs
  `docs/IDEA_MODEL_*.md` for the full candidate/thesis detail). **Cross-check: has the gate
  a held idea depends on now cleared?** If mmsell3 just crossed n≥150 (or any other named
  trigger fired), that is NOT a quiet carryover — escalate it in the chat headline as
  **"gate cleared → ready to build"** and name the exact next action (e.g. "re-invoke
  `kalshi-strategy` on MMX"). This is the one thing the loop must never silently miss: an
  idea sitting fully-specified and ready, with nobody told its gate opened.
- **Any other named decision point** referenced in prior suggestions (e.g. "loosen theta4's
  edge or conclude") — carry it until the user's fable session acts, but re-flag it as DUE
  again each run it remains unresolved so it doesn't fade into background noise.

This step exists because gate-blocked ideas are easy to forget between runs — they don't show
up in the books/data query at all until someone remembers to look. Treat the suggestion list
as the durable memory for them, not chat history.

### 4. Update the carried-over suggestions

Keep still-valid ones (note how the picture shifted as n grew), drop resolved or
invalidated ones (say why in the file's footer line), add new ones sparingly. This includes
the idea-model queue item from step 3b — update its per-idea status (still blocked / gate
cleared / built) every run, don't let it go stale while trading-book items get all the
attention. Suggestions are recommendations for the user's fable sessions — concrete,
evidenced, and non-urgent unless something is actually broken or a gate just cleared.

### 5. Persist

Rewrite `docs/STRATEGY_LOOP_STATUS.md` (new snapshot replaces the old; suggestions
carry over) in the `/tmp/loopstate` worktree (`git fetch origin strategy-loop-status &&
git reset --hard origin/strategy-loop-status` first), commit, push to
`strategy-loop-status` ONLY.

### 6. Report + tidy

Post the banner-first chat report (format above). Then reset the ops channel to idle:
write `{"type": "noop"}` to `ops/request.json`, commit, push.

## Research probes vs trading books vs gate-blocked ideas (what shows up where)

Three kinds of thing exist outside the plain books table, and the loop must account for all
three every run — not just re-list whichever ones happen to be currently active:

- **Trading books** (things that write `paper_trades` — theta*, mmsell*, weather con/other) —
  tracked by step 1's query.
- **Data collectors** (freshness of tables the worker writes) — also step 1's query.
- **On-demand research probes** — read-only `scripts/` studies (allowlisted in
  `ops_runner.py`) run via the ops channel, not the loop. They read existing collected data or
  public APIs and never appear as book/data rows on their own. Find the CURRENT set by reading
  the most recent verdicts in `docs/RESEARCH_JOURNAL.md` (do not hardcode a probe list here —
  it goes stale the moment a family is closed; as of 2026-07-10 the standing families TFAV,
  WCPROP, XGAME are all CLOSED/KILLED with zero standing probes, and PINNED/DECAY were probed
  and killed the same day). Report a one-line **"Research probes (on-demand):"** note reflecting
  whatever is ACTUALLY open right now, or "none" if nothing is standing.
- **Gate-blocked ideas** (step 3b) — pre-registered theses/candidates from `IDEA_MODEL_*.md`
  that are fully specified but waiting on a trading-book gate (e.g. MMX waiting on mmsell3,
  NEST waiting on theta4). These are NOT probes (nothing runs for them yet) and NOT books
  (nothing trades yet) — they live only in the carried-over suggestion list. This is the
  category most likely to be silently forgotten, because it produces zero rows in any query;
  the suggestion list is its only home, so step 3b + step 4 must actively maintain it every run.

Do NOT run probes or build gate-blocked ideas from the loop — reporting and flagging only, the
build/probe decision is the operator's fable call (or a `kalshi-strategy` invocation once a
gate clears).
