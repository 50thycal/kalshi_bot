# CLAUDE.md — guidance for Claude Code in this repo

## Project

Kalshi trading bot (market scanner + paper trading) deployed on **Railway** with
a **Postgres** database. Architecture is in `README.md`; the source is in
`kalshi_bot/`, and the full DB schema is in `kalshi_bot/models.py`.

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
   {"type": "noop"}
   ```
3. Commit and push:
   ```bash
   cd /tmp/ops && git add ops/request.json && git commit -m "ops: <what>" && git push origin ops
   ```
4. Read the result with the GitHub MCP tools:
   - `actions_list` `method=list_workflow_runs`, `resource_id=ops-runner.yml` → newest run id
   - `actions_list` `method=list_workflow_jobs`, `resource_id=<run id>` → job id
   - `get_job_logs` `job_id=<job id>` `return_content=true` → the output is the query
     table or the log lines
5. Reset `ops/request.json` to `{"type": "noop"}` and push (leave the channel idle).

Notes:
- **Never open a PR merging `ops` into the default branch.** GitHub auto-deletes
  the branch on merge, which removes the trigger. If `ops` is ever missing,
  recreate it: `git checkout -B ops origin/<default-branch> && git push -u origin ops`.
- If `scripts/db_query.py` or `scripts/railway_logs.py` change on the default
  branch, refresh `ops` from the default branch (recreate as above) so it picks
  up the fix.
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
  `paper_positions`, `account_snapshots`, `system_events`.

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
