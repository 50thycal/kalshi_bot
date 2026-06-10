"""Dispatch an ops request to the logs or read-only DB runner.

Run by the `Ops Runner` workflow whenever `ops/request.json` changes on the
`ops` branch. This lets Claude self-trigger a logs fetch or a read-only query by
committing a one-line request file (no "Run workflow" click), then read the
result back from the job log.

ops/request.json shapes:
  {"type": "logs", "limit": 200, "filter": "", "deployment_id": ""}
  {"type": "db",   "sql": "select ...", "max_rows": 200}
  {"type": "noop"}   # placeholder; do nothing

Reuses scripts/railway_logs.py and scripts/db_query.py by setting the env vars
they already read, so all the read-only guards there still apply.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REQUEST_PATH = os.environ.get("OPS_REQUEST_PATH", "ops/request.json")


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

    print(f"Unknown request type: {rtype!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
