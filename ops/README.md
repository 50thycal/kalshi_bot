# `ops` branch — Claude's self-service request channel

This branch exists so Claude can fetch **Railway logs** and run **read-only DB
queries** on its own, without you clicking "Run workflow".

**How it works:** Claude overwrites `ops/request.json` and pushes it here. That
push fires the `Ops Runner` workflow (`.github/workflows/ops-runner.yml`), which
runs the request and prints the result to the job log; Claude reads it back.

**This branch is intentionally separate from `main`:**
- Request commits never touch `main`, so your real history stays clean.
- Railway only deploys from the default branch, so these pushes **never redeploy
  the worker**.

`request.json` shapes:

```jsonc
{"type": "logs", "limit": 200, "filter": "", "deployment_id": ""}
{"type": "db",   "sql": "select ...", "max_rows": 200}
{"type": "noop"}   // placeholder; do nothing
```

DB requests are read-only three ways over (see `scripts/db_query.py`). You don't
need to do anything with this branch — it's Claude's scratch channel.
