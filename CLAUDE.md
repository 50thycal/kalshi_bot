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
   {"type": "db",   "sql": "select ...", "max_rows": 200}
   {"type": "logs", "limit": 200, "filter": "", "deployment_id": ""}
   {"type": "script", "name": "weather_model_check", "args": ["--sigma", "1.5"]}
   {"type": "noop"}
   ```
   `script` runs an allowlisted self-contained read-only analysis script from
   `scripts/` (see `ALLOWED_SCRIPTS` in `scripts/ops_runner.py`).

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
4. Read the result. The runner commits its full output back to the `ops` branch
   as `ops/result.txt`, so the simplest path is plain git (works even when the
   GitHub MCP tools are down):
   ```bash
   # poll until the result commit lands (~30-90s), then:
   git fetch origin ops && git show FETCH_HEAD:ops/result.txt
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
  `paper_positions`, `account_snapshots`, `system_events`. Weather research:
  live-collected `weather_*` tables vs `backfill_weather_markets` /
  `backfill_weather_candles` (Kalshi REST history — separate provenance, never
  mix them silently in an analysis). `weather_forecast_outcomes` is the persisted
  forecast→settlement join (one labeled row per intraday cycle, materialized at
  settlement from the live `weather_*` tables) — the validation dataset.

### Secrets

Live in **GitHub Actions secrets**, not the repo: `DATABASE_URL_RO`,
`RAILWAY_TOKEN`, `RAILWAY_PROJECT_ID`, `RAILWAY_ENVIRONMENT_ID`,
`RAILWAY_SERVICE_ID`. Human setup instructions are in `docs/REMOTE_ACCESS.md`.

### Gotchas

- Railway's API is behind Cloudflare, which 403s the default `Python-urllib`
  user-agent (error 1010). `scripts/railway_logs.py` sends a browser User-Agent
  to get through — **keep it.**
- `scripts/db_query.py` matches write/DDL keywords on **word boundaries**, so
  columns like `created_at` / `updated_at` are fine in a read-only query.
