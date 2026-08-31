#!/usr/bin/env python3
"""Is the worker alive? Read-only liveness check, run on a schedule (XOS-000015).

On 2026-08-30 the worker crash-looped on an invalid config and nobody noticed for
16h51m — no scanning, no paper trades, no live orders, across every strategy. It
surfaced only because a human asked an unrelated question about fills. Nothing in
the system was watching, so this exists to watch.

Two properties matter more than the check itself:

  * It must not depend on the thing it monitors. This runs as a scheduled GitHub
    Action against a SELECT-only Postgres URL, so a dead, wedged or crash-looping
    worker cannot suppress its own alarm — which a heartbeat emitted BY the worker
    would.
  * It must fail loudly. A non-zero exit fails the scheduled workflow, and GitHub
    emails the repository owner on a failing scheduled run. That is the alert
    channel; no new infrastructure to keep alive (and to also go unwatched).

`bot_runs` is the signal rather than trades or candidates: the worker writes a row
per cycle whether or not anything was tradeable, so a quiet market reads as alive.
Counting trades would have alarmed every quiet night and been muted within a week.
"""

from __future__ import annotations

import os
from datetime import timezone

#: How stale the newest `bot_runs` row may get before we call the worker dead.
#: Production wrote a row every ~156s averaged over 3 hours (measured 2026-08-31),
#: so 30 minutes is about eleven missed cycles: ample headroom for a redeploy
#: (~2-3 min), a slow scan or a transient Kalshi stall, while still catching a real
#: outage inside the hour instead of the seventeen it took by hand. A threshold
#: that cries wolf gets muted, and a muted alarm is the same as no alarm.
MAX_SILENCE_SECONDS = int(os.environ.get("HEARTBEAT_MAX_SILENCE_SECONDS", "1800"))


def main() -> int:
    url = os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL")
    if not url:
        print("::error::DATABASE_URL_RO is not set — cannot check worker liveness")
        return 1

    import psycopg

    with psycopg.connect(url, connect_timeout=20) as conn, conn.cursor() as cur:
        cur.execute(
            "select max(started_at), now() from bot_runs "
            "where started_at > now() - interval '2 days'"
        )
        latest, now = cur.fetchone()

    if latest is None:
        # Not "quiet" — nothing at all in two days. Treated as dead rather than as
        # missing data: a healthy worker cannot produce this.
        print("::error::worker heartbeat: NO bot_runs rows in the last 2 days")
        return 1

    silence = (now - latest).total_seconds()
    stamp = latest.astimezone(timezone.utc).isoformat()
    summary = (
        f"last bot_runs {stamp} — {silence / 60:.1f} min ago "
        f"(threshold {MAX_SILENCE_SECONDS / 60:.0f} min)"
    )

    if silence > MAX_SILENCE_SECONDS:
        print(f"::error::worker heartbeat: WORKER APPEARS DOWN — {summary}")
        print(
            "Check Railway deployment status and the worker log. A crash loop "
            "reports `invalid configuration; refusing to start` with the failing "
            "field named; see docs/OPS_RUNBOOK.md."
        )
        return 1

    print(f"worker heartbeat OK — {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
