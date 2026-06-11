"""Dispatch an ops request to the logs or read-only DB runner.

Run by the `Ops Runner` workflow whenever `ops/request.json` changes on the
`ops` branch. This lets Claude self-trigger a logs fetch or a read-only query by
committing a one-line request file (no "Run workflow" click), then read the
result back from the job log.

ops/request.json shapes:
  {"type": "logs", "limit": 200, "filter": "", "deployment_id": ""}
  {"type": "db",   "sql": "select ...", "max_rows": 200}
  {"type": "script", "name": "weather_model_check", "args": ["--sigma", "1.5"]}
  {"type": "noop"}   # placeholder; do nothing

Reuses scripts/railway_logs.py and scripts/db_query.py by setting the env vars
they already read, so all the read-only guards there still apply. Script requests
are allowlisted to self-contained read-only analysis scripts in scripts/.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REQUEST_PATH = os.environ.get("OPS_REQUEST_PATH", "ops/request.json")

# Read-only analysis scripts (stdlib + psycopg only) runnable via the ops channel.
# Each connects with DATABASE_URL_RO and a read-only session, like db_query.py.
ALLOWED_SCRIPTS = ("weather_model_check", "weather_exit_sweep", "weather_entry_study")


def main() -> int:
    try:
        with open(REQUEST_PATH) as f:
            req = json.load(f)
    except FileNotFoundError:
        print(f"No request file at {REQUEST_PATH}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"Invalid JSON in {REQUEST_PATH}: {e}", file=sys.stderr)
        return 1

    rtype = (req.get("type") or "").strip().lower()
    print(f"# ops request: type={rtype!r}")

    if rtype in ("", "noop"):
        print("(noop — nothing to do)")
        return 0

    if rtype == "logs":
        import railway_logs

        if req.get("limit") is not None:
            os.environ["LOG_LIMIT"] = str(req["limit"])
        if req.get("filter"):
            os.environ["LOG_FILTER"] = str(req["filter"])
        if req.get("deployment_id"):
            os.environ["RAILWAY_DEPLOYMENT_ID"] = str(req["deployment_id"])
        return railway_logs.main()

    if rtype == "db":
        sql = req.get("sql")
        if not sql:
            print("db request missing 'sql'", file=sys.stderr)
            return 1
        os.environ["SQL"] = str(sql)
        os.environ["MAX_ROWS"] = str(req.get("max_rows", 200))
        import db_query

        return db_query.main()

    if rtype == "script":
        name = (req.get("name") or "").strip()
        if name not in ALLOWED_SCRIPTS:
            print(f"script {name!r} is not allowlisted (allowed: {ALLOWED_SCRIPTS})", file=sys.stderr)
            return 1
        args = [str(a) for a in (req.get("args") or [])]
        import importlib

        mod = importlib.import_module(name)
        return mod.main(args)

    print(f"Unknown request type: {rtype!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
