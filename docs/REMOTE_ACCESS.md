# Remote access: Railway logs & read-only DB queries

This lets Claude (running in the Claude Code web environment) fetch **Railway
logs** and run **read-only Postgres queries** on demand, without you copy-pasting
them in.

## Why it works this way

The Claude web environment can only reach an **allowlisted set of hosts over
HTTP**, and that allowlist already includes GitHub but not Railway or your
database. Rather than poke holes in that allowlist (unreliable, and raw Postgres
TCP won't traverse the HTTP proxy anyway), both features run as
**manually-triggered GitHub Actions workflows**:

```
Claude  --(GitHub API: trigger workflow)-->  GitHub Actions runner  --(open internet)-->  Railway API / Postgres
  ^                                                                                              |
  +------------------------ reads the job log via the GitHub API <--------------------------------+
```

GitHub runners have open egress, so they reach Railway fine. Your secrets live in
**GitHub Actions secrets** — never in the repo and never in Claude's context.

## What you need to provision (one time)

### 1. A read-only database role

Run [`scripts/sql/create_readonly_role.sql`](../scripts/sql/create_readonly_role.sql)
against the bot's database **once** (edit the password first). Easiest path:
Railway → your Postgres service → **Data / Query** console, paste, run. Or:

```bash
psql "$DATABASE_URL" -f scripts/sql/create_readonly_role.sql
```

Then build the connection string for the new role:

```
postgresql://bot_readonly:YOUR_PASSWORD@<host>:<port>/<db>
```

Use the **public** host/port for your Postgres (Railway → Postgres → *Connect* →
"Public Network" / TCP proxy, e.g. `<name>.proxy.rlwy.net:<port>`), since the
GitHub runner connects from outside Railway.

### 2. A Railway API token

Railway → **Account/Workspace Settings → Tokens** → create a token. A
**Workspace** (team) token is the most reliable against the GraphQL API. You'll
also need three IDs, all visible in the URL of your service in the Railway
dashboard (`/project/<projectId>/service/<serviceId>?environmentId=<environmentId>`).

### 3. GitHub Actions secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `DATABASE_URL_RO` | The read-only connection string from step 1 |
| `RAILWAY_TOKEN` | The Railway token from step 2 |
| `RAILWAY_PROJECT_ID` | Railway project ID |
| `RAILWAY_ENVIRONMENT_ID` | Railway environment ID (e.g. production) |
| `RAILWAY_SERVICE_ID` | The **main/live** worker service ID (`BOT_MODE=live`) |
| `RAILWAY_EVO_SERVICE_ID` | The **evo** worker service ID (`BOT_MODE=evo`), if you run the evolutionary-agent bot as a second Railway service. Enables `{"service":"evo"}` on `env`/`logs` ops requests so its logs + config are reachable exactly like the main service's. |

Find a service's ID in its Railway URL:
`…/project/<projectId>/service/<serviceId>?environmentId=<environmentId>`. The
project + environment + token are **shared** across services in one project — only
the service ID differs, so the evo service reuses the same `RAILWAY_TOKEN` /
`RAILWAY_PROJECT_ID` / `RAILWAY_ENVIRONMENT_ID`.

### 4. Get the workflows onto the default branch

`workflow_dispatch` workflows can only be **triggered** once they exist on the
repo's **default branch**. Merge this branch (or at least the two files under
`.github/workflows/`) into `main`. Until then the workflows are visible but not
runnable.

## How Claude uses it

Once the above is in place, Claude triggers the workflows through the GitHub API
and reads the results from the job log:

- **Logs** — runs the `Railway Logs` workflow (`railway-logs.yml`), optionally
  with `limit`, a `deployment_id`, or a `filter`.
- **Data** — runs the `DB Query (read-only)` workflow (`db-query.yml`) with a
  single read-only SQL statement, e.g.
  `select count(*), max(captured_at) from market_snapshots`.

Each run takes ~30–60s to spin up. You can also trigger either workflow yourself
from the repo's **Actions** tab.

## Safety

- **Logs** are read-only by nature.
- **DB queries** are read-only three times over: the `bot_readonly` role only has
  `SELECT`; the session sets `default_transaction_read_only=on` with a 30s
  statement timeout; and `scripts/db_query.py` rejects multi-statement or
  write/DDL SQL before it's sent. Output is row-capped so large tables don't
  flood the log.

## If Railway's log query stops working

Railway's GraphQL schema occasionally changes. The queries live at the top of
[`scripts/railway_logs.py`](../scripts/railway_logs.py) and are easy to edit. For
a quick probe, the script also honors `RAILWAY_RAW_QUERY` (+ optional
`RAILWAY_RAW_VARS` JSON) to run an arbitrary GraphQL query — introspection works
from the runner.
