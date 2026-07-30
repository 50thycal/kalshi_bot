# CLAUDE.md — guidance for Claude Code in this repo

## Project

Kalshi trading bot (market scanner + paper trading) deployed on **Railway** with
a **Postgres** database. Architecture is in `README.md`; the source is in
`kalshi_bot/`, and the full DB schema is in `kalshi_bot/models.py`.

## Goal (north star)

**Make $100/month in realized profit from any combination of the bot's trading
strategies.** This is the single metric that matters — not win rate, not number of
books, not research volume. Every strategy, experiment, and config change is judged by
whether it moves net realized P&L toward +$100/month. Research that proves a book is
−EV is still a *win* (it tells us what to stop trading); the bottom line is dollars,
across the whole portfolio. Track progress with the **"PnL"** and **"digest"** commands
and the **"full update"** review (the `full-update` skill).

---

## Operating the logs + database access system

This repo has a system that lets **you (Claude)** fetch Railway logs and run
read-only Postgres queries on your own. Read this before trying to inspect logs
or data.

### Why it's built this way

The Claude Code web sandbox can only make **HTTP requests to allow-listed hosts**
— GitHub is allowed, but **Railway and Postgres are not reachable from your
environment.** So everything is routed through **GitHub Actions runners**, which
have open internet. You drive the runners and read their job logs back through
the GitHub MCP tools.

### Preferred channel: the `ops` branch (you can do this with no human help)

You **cannot** trigger a `workflow_dispatch` via the GitHub API — it returns
`403 Resource not accessible by integration`. Instead, you trigger work by
**pushing a request file to the `ops` branch**, which fires the `Ops Runner`
workflow automatically.

To run a request:

1. Work against the `ops` branch without disturbing your current branch — a
   worktree is cleanest:
   ```bash
   git fetch origin ops
   git worktree add /tmp/ops ops
   ```
2. Overwrite `/tmp/ops/ops/request.json` with **one** of:
   ```jsonc
   {"type": "db",   "sql": "select ...", "max_rows": 200, "id": "pnl-check-1"}
   {"type": "logs", "limit": 200, "filter": "", "deployment_id": "", "id": "logs-1"}
   {"type": "script", "name": "weather_model_check", "args": ["--sigma", "1.5"]}
   {"type": "env"}                                          # read allowlisted Railway vars
   {"type": "env", "set": {"KILL_SWITCH": "false"}}         # set allowlisted vars + redeploy
   {"type": "noop"}
   ```
   `script` runs an allowlisted self-contained read-only analysis script from
   `scripts/` (see `ALLOWED_SCRIPTS` in `scripts/ops_runner.py`).

   **Two Railway services, one channel.** `env` and `logs` requests accept a
   `"service"` field selecting which worker to act on — `"main"`/`"live"` (default,
   the `BOT_MODE=live` trading worker) or `"evo"` (the `BOT_MODE=evo` evolutionary-agent
   worker). So the evo bot's logs/config are reachable the same way as the main bot's:
   ```jsonc
   {"type": "logs", "service": "evo", "limit": 120, "id": "evo-logs-1"}
   {"type": "env",  "service": "evo", "id": "evo-env-1"}                    // read evo vars
   {"type": "env",  "service": "evo", "set": {"EVO_MAX_ACTIVE_AGENTS": "5"}, "id": "evo-cad"}
   ```
   Each service's ID lives in a secret (`RAILWAY_SERVICE_ID` for main,
   `RAILWAY_EVO_SERVICE_ID` for evo — never in this public repo). `db` requests are
   **service-agnostic** (both workers share one Postgres via `DATABASE_URL_RO`).

   **Always set a unique `"id"`** (any short slug — sanitized to `[A-Za-z0-9._-]`).
   The runner writes your output to a durable per-run file `ops/results/<id>.txt`
   in addition to the shared `ops/result.txt`. This is what lets you read back
   **exactly your own result** even when another producer (e.g. a parallel `/loop`
   session) drives the channel at the same time — the shared `result.txt` is only the
   latest-run pointer and can be overwritten by a concurrent run, but `ops/results/<id>.txt`
   is uniquely named, never conflicts, and is always published (the workflow retries
   against concurrent pushes). Omit `id` only for throwaway requests (it then falls back
   to a timestamp+run-number filename you'd have to hunt for).

   Two of these are the standing **commands** the user asks for by name:
   - **"PnL"** -> `{"type":"script","name":"weather_pnl"}` — the per-book rollup
     plus the per-window x strategy decision table (n, win%, total, P&L/trade).
     Present it as the tables discussed: per-trade is the deciding number.
   - **"experiment results"** -> `{"type":"script","name":"weather_experiments"}`
     — runs all three research probes in one shot (model check, exit sweep, entry
     study). Present each as a structured table.
   - **"digest"** (daily) -> `{"type":"script","name":"weather_digest"}` — the live
     operational digest: worker health, today's entries/exits + fill status, open
     positions (with best-effort current price + unrealized), realized P&L today,
     a per-book paper rollup, and an **ANOMALIES** section (bad-status orders, fills
     with no order row, untracked positions, stale worker, stuck orders). Lead with
     ANOMALIES — flag anything there; otherwise confirm "all clear". Meant to be run
     once a day on a schedule (`--hours N`, `--no-prices` to skip the Kalshi lookup).

     Every `weather_digest` run is **archived automatically**: the Ops Runner
     workflow snapshots the full result to `digests/<UTC-timestamp>.md` on the
     long-lived **`digest-archive`** branch (created on first run). That branch is
     the durable running history — append-only, browsable on GitHub, never merged
     into the default branch and separate from the disposable `ops` branch. You do
     **not** need to hand-commit digests anywhere; to review past digests, read from
     `digest-archive` (e.g. `git fetch origin digest-archive && git ls-tree -r --name-only FETCH_HEAD digests/`).
   - **"mmsell fill model"** -> `{"type":"script","name":"mmsell_fill_model"}` — the
     paper→live alignment read for the mmsell books. Paper ASSUMES a resting maker order
     always fills; live it fills ~70% and misses the winners (adverse selection). This
     calibrates the live mmsell3 (price → fill, realizable P&L) relationship and projects
     each book's price mix through it to report **realizable ¢/trade** (the number to gate
     on) vs the optimistic blended paper number, with a coverage %. **Gate variants on the
     realizable column, not blended paper** — see `docs/MMSELL_FILL_MODEL.md`. Companion
     `mmsell_live` is the live execution scorecard (fill rate, fill economics, realized vs
     paper shadow, open footprint).
   - **"mmsell exit study"** -> `{"type":"script","name":"mmsell_exit_study"}` — does a
     stop-loss or volatility exit beat hold-to-settlement, per book, and by how much. Replays
     each settled position's captured intraday path (`mmsell_position_ticks`, recorded live off
     the orderbook) through a grid of confirmed catastrophic stops (yes-mid ≥ L for K ticks) and
     volatility exits (yes-mid range over W ticks ≥ V), reporting mean, **5th-pctile tail**,
     win%, %exit and the **Δ vs hold** per rule. Gate on **Δp5 (tail) up AND Δmean ≥ −0.3¢** at
     n≥100 replayable — see `docs/MMSELL_EXIT_STUDY.md`. Coverage grows after deploy (a position
     must be born + settle inside the capture window); empty early output is a data-maturity wait.

     The **ANCHOR SET** (`mmsellA1`–`mmsellA5`, `docs/MMSELL_ANCHOR_SET.md`) forward-tests the
     three mechanics the backtests liked but couldn't power: a confirmed **yes-BID** stop at
     12/20/30¢ (A1–A3), a volatility **entry** gate (A4), and a two-sided **short strangle** (A5).
     All five sit on the mmsell10 entry, so **mmsell10 is the control** — read them against it, not
     in absolute terms. Paper-only; gates are pre-registered in the doc and `docs/BOOK_REGISTRY.md`.
     They surface in the `mm check 1` skill alongside the other books.
   - **"parity"** / **"live paper parity"** -> `{"type":"script","name":"live_paper_parity"}` — is
     our paper trading system telling the truth about a LIVE book? Every live strategy runs a fresh
     paper **twin** beside it (same start instant, same candidates, the LIVE price/size/cap knobs),
     so the only difference is the fill assumption paper cannot test. Reports decision alignment
     (and the exact gate that stopped live), fill rate + price gap, and settled twin-vs-live P&L —
     including the **matched-market** pairs, which separate a wrong simulator (ACCOUNTING GAP —
     invalidates every paper gate) from a real edge lost to execution (EXECUTION GAP). Lead with
     ANOMALIES. Mechanism + traps: `docs/LIVE_PAPER_TWIN.md`; the arm-and-audit procedure is the
     `live-paper-parallel` skill. **Standing policy: no strategy goes live without a twin.**

   - **"mmsell crypto study"** -> `{"type":"script","name":"mmsell_crypto_study"}` — backtests the
     BTC/ETH cheap-tail sell over **Kalshi's own settled history** (our paper slice is n=38, which
     can't decide anything): a stop-loss grid, volatility entry+exit gates, and the two-sided
     short strangle. See `docs/MMSELL_CRYPTO_STUDY.md`. Key results already banked: the
     bid-triggered stop improves mean AND tail on crypto (continuous-path, unlike sports jumps);
     the vol EXIT gate is dead; the strangle is intriguing but needs ~82 clean pairs.
     **Two traps this script documents — read before trusting any stop backtest:** trigger on the
     yes-**BID** (or require a ≥2-tick confirm), never the raw mid/ask, or thin-book quotes fire
     ~100% of stops as an artifact; and Kalshi only serves ~1h of candles for these series, so the
     backtest population is `htc<1h` while mmsell trades `htc>=1h` — different trades.

   The individual probes can still be run alone:
   `weather_model_check` grades the ensemble forecast distribution against the
   market's bucket prices on settled events (Brier/log-loss + hypothetical EV)
   and prints live model-vs-market disagreements. `weather_exit_sweep` replays
   settled weather paper trades through their recorded bucket price paths under
   a TP/SL grid to find the best exit rule vs holding to settlement.
   `weather_entry_study` runs the entry experiments (market calibration,
   price-band P&L, obs-confirmed entry, limit-entry fills).
   `weather_validation` reports over the persisted forecast→settlement dataset
   (`weather_forecast_outcomes`, materialized at settlement from the raw live tables):
   coverage/growth, forecast-vs-market skill, the ensemble-vs-market probabilistic edge
   on the winning bucket, and market-vs-forecast divergence vs who was right — the
   accumulating data that makes cal/dist/pm validatable.
3. Commit and push:
   ```bash
   cd /tmp/ops && git add ops/request.json && git commit -m "ops: <what>" && git push origin ops
   ```
4. Read the result. The runner commits your output back to the `ops` branch as the
   durable per-run file `ops/results/<id>.txt` (and updates the shared `ops/result.txt`
   pointer). Plain git is the simplest path (works even when the GitHub MCP tools are
   down) — read **your own `id`** so a concurrent producer's run can't shadow yours:
   ```bash
   # poll until YOUR result lands (~30-90s), then:
   git fetch origin ops && git show FETCH_HEAD:ops/results/<id>.txt
   # (git show FETCH_HEAD:ops/result.txt is the latest-run pointer — fine when you're
   #  the only producer, but it can be overwritten by a concurrent /loop run.)
   ```
   Alternatively via the GitHub MCP tools:
   - `actions_list` `method=list_workflow_runs`, `resource_id=ops-runner.yml` → newest run id
   - `actions_list` `method=list_workflow_jobs`, `resource_id=<run id>` → job id
   - `get_job_logs` `job_id=<job id>` `return_content=true` → the output is the query
     table or the log lines
5. Reset `ops/request.json` to `{"type": "noop"}` and push (leave the channel idle).

Notes:
- **Never open a PR merging `ops` into the default branch.** GitHub auto-deletes
  the branch on merge, which removes the trigger. If `ops` is ever missing,
  recreate it: `git checkout -B ops origin/<default-branch> && git push -u origin ops`.
- If `scripts/db_query.py`, `scripts/railway_logs.py`, `scripts/ops_runner.py`,
  `.github/workflows/ops-runner.yml`, or an allowlisted analysis script change on
  the default branch, refresh `ops` from the default branch (recreate as above) so
  it picks up the fix (e.g. the digest auto-archive step).
- Latency is ~30–60s per run. Request commits live only on `ops`; they never
  touch the default branch and never redeploy the Railway worker.

### Fallback channel: one-click workflows (need the human)

`DB Query (read-only)` (`db-query.yml`) and `Railway Logs` (`railway-logs.yml`)
are `workflow_dispatch` workflows the human runs from the Actions tab. You can
still read their results with `get_job_logs`. Use only if the `ops` channel is
unavailable.

### Database query rules (read-only — do not attempt writes)

- One **single** read-only statement per request: `SELECT` / `WITH` / `TABLE` /
  `EXPLAIN`. Writes and DDL are rejected by `scripts/db_query.py` and also blocked
  server-side (a SELECT-only role + a read-only transaction). Don't try to write.
- Schema reference: `kalshi_bot/models.py`. Key tables: `bot_runs`, `markets`,
  `market_snapshots`, `orderbook_snapshots`, `signals`, `paper_trades`,
  `paper_positions`, `account_snapshots`, `system_events`. Live/paper parallel runs:
  `live_paper_twins` (one epoch row per twin — `started_at` scopes BOTH sides of a
  paper-vs-live comparison; always filter on it) and `live_paper_parity_events` (the
  per-candidate decision tape: incumbent paper book / twin / real live outcome). Weather research:
  live-collected `weather_*` tables vs `backfill_weather_markets` /
  `backfill_weather_candles` (Kalshi REST history — separate provenance, never
  mix them silently in an analysis). `weather_forecast_outcomes` is the persisted
  forecast→settlement join (one labeled row per intraday cycle, materialized at
  settlement from the live `weather_*` tables) — the validation dataset.

### Secrets

Live in **GitHub Actions secrets**, not the repo: `DATABASE_URL_RO`,
`RAILWAY_TOKEN`, `RAILWAY_PROJECT_ID`, `RAILWAY_ENVIRONMENT_ID`,
`RAILWAY_SERVICE_ID` (main/live worker), and `RAILWAY_EVO_SERVICE_ID` (the evo
worker — enables `{"service":"evo"}` env/logs requests). Human setup instructions
are in `docs/REMOTE_ACCESS.md`.

### Gotchas

- Railway's API is behind Cloudflare, which 403s the default `Python-urllib`
  user-agent (error 1010). `scripts/railway_logs.py` sends a browser User-Agent
  to get through — **keep it.**
- `scripts/db_query.py` matches write/DDL keywords on **word boundaries**, so
  columns like `created_at` / `updated_at` are fine in a read-only query.
