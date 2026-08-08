"""mmsell SCAN HEALTH — is the entry scan actually seeing the market?

WHY THIS EXISTS
---------------
On 2026-08-08 the mmsell books were entering ~2 series/day, down from 32 a fortnight earlier,
while Kalshi still listed 9,656 sports markets across 569 series. The cause was an ordering bug:
the scan ranked ALL open events by volume and cut to the top 150 BEFORE applying the entry
window, so the budget was spent on 2028 election futures and season championships that the
window then discarded.

That took a live market survey and two days of candidate-tick archaeology to establish — because
the counters that would have shown it in one query existed only inside a log line, and Railway's
log endpoint returns the message text while dropping the structured fields. The tracker now
persists its scan funnel to `system_events` every cycle; this reads it back.

WHAT TO LOOK AT, IN ORDER
-------------------------
1. `pagination_exhausted` — must be TRUE. False means the scan stopped at its page cap with more
   events unread, and since pages arrive in Kalshi's order rather than by volume, the truncation
   is arbitrary: high-volume eligible events can be dropped for no reason.
2. `eligible` vs `seen` — `dropped_by_cap` is eligible events the top-N cut never scanned. Zero
   means the cap is not binding. A large number means we are leaving tradeable markets on the
   table and `mmsell_top_events` is now the constraint.
3. `out_of_window` — events discarded for carrying no market inside the entry window. Large is
   FINE and expected (most of Kalshi is long-dated); it is the number that used to be consuming
   the scan budget.
4. `in_band` -> `opened` — the last mile. `in_band` near zero with healthy `seen` means the
   market genuinely has no cheap tails, which is a regime, not a fault.

Read-only, self-contained (stdlib + psycopg); runs locally or via the ops channel:

    DATABASE_URL_RO=postgresql://... python scripts/mmsell_scan_health.py
    # or:  {"type": "script", "name": "mmsell_scan_health"}
    #      {"type": "script", "name": "mmsell_scan_health", "args": ["--hours", "72"]}
"""

from __future__ import annotations

import argparse
import os
import sys

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)

FIELDS = ("pages_fetched", "events_fetched", "events_out_of_window", "events_eligible",
          "events_dropped_by_cap", "events_seen", "markets_considered", "in_band", "opened",
          "skipped_htc", "skipped_illiquid", "capped")


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def load_cycles(cur, hours: float) -> list[dict]:
    cur.execute(
        "SELECT created_at, raw_json FROM system_events"
        " WHERE component = 'mmsell_scan' AND created_at > now() - (%s || ' hours')::interval"
        " ORDER BY created_at",
        (str(hours),),
    )
    return [{"at": at, **(raw or {})} for at, raw in cur.fetchall()]


def report(cycles: list[dict], hours: float) -> None:
    if not cycles:
        print(f"=== mmsell scan health: NO CYCLES in the last {hours:g}h ===")
        print("  Either the worker is not running its mmsell cycle, or it predates the telemetry"
              "\n  (added 2026-08-08). Check the worker is alive before reading anything into it.")
        return

    print(f"=== mmsell scan health — {len(cycles)} cycles in the last {hours:g}h ===")
    print(f"  {'cycle (UTC)':20s} {'pages':>6} {'exh':>4} {'fetched':>8} {'oow':>7} {'elig':>6}"
          f" {'capped':>7} {'seen':>6} {'mkts':>7} {'band':>6} {'open':>5}")
    for c in cycles[-20:]:
        print(f"  {str(c['at'])[:19]:20s} {c.get('pages_fetched', 0):6d}"
              f" {'Y' if c.get('pagination_exhausted') else 'NO':>4}"
              f" {c.get('events_fetched', 0):8d} {c.get('events_out_of_window', 0):7d}"
              f" {c.get('events_eligible', 0):6d} {c.get('events_dropped_by_cap', 0):7d}"
              f" {c.get('events_seen', 0):6d} {c.get('markets_considered', 0):7d}"
              f" {c.get('in_band', 0):6d} {c.get('opened', 0):5d}")

    n = len(cycles)
    avg = {f: sum(c.get(f, 0) or 0 for c in cycles) / n for f in FIELDS}
    truncated = sum(1 for c in cycles if not c.get("pagination_exhausted"))

    print("\n=== VERDICT ===")
    if truncated:
        print(f"  [FAULT] {truncated}/{n} cycles hit the PAGE CAP without exhausting the cursor."
              "\n          The event universe is being truncated before it is ranked, and pages"
              "\n          arrive in Kalshi's order — so which events get dropped is arbitrary."
              "\n          Raise mmsell_event_pages.")
    else:
        print(f"  [OK] all {n} cycles paged to cursor exhaustion — the full universe was ranked.")

    if avg["events_dropped_by_cap"] >= 1:
        print(f"  [CAP BINDING] {avg['events_dropped_by_cap']:.0f} eligible events/cycle go"
              " unscanned."
              "\n          These are tradeable markets the top-N cut discards. Raising"
              "\n          mmsell_top_events would widen the book; it is a capacity choice,"
              "\n          not a fault.")
    else:
        print("  [OK] the top-N cut is not binding — every eligible event is scanned.")

    if avg["events_seen"] < 1:
        print("  [FAULT] the scan is seeing NO events. Nothing downstream can trade.")
    elif avg["in_band"] < 1:
        print(f"  [REGIME] {avg['events_seen']:.0f} events/cycle scanned but ~0 in band — the"
              "\n          market has no cheap tails right now. Not a fault.")
    else:
        print(f"  [OK] {avg['in_band']:.1f} in-band candidates/cycle, {avg['opened']:.1f} opened.")

    print(f"\n  averages/cycle: fetched {avg['events_fetched']:.0f}"
          f" -> out-of-window {avg['events_out_of_window']:.0f}"
          f" -> eligible {avg['events_eligible']:.0f}"
          f" -> scanned {avg['events_seen']:.0f}"
          f" -> markets {avg['markets_considered']:.0f}"
          f" -> in band {avg['in_band']:.1f}"
          f" -> opened {avg['opened']:.1f}")
    print("  A large out-of-window count is EXPECTED and healthy: most of Kalshi is long-dated."
          "\n  It is only a problem when those events are consuming the scan budget, which is"
          "\n  what the window-before-cap ordering prevents.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=float, default=24.0, help="lookback window (default 24)")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cycles = load_cycles(cur, args.hours)
    report(cycles, args.hours)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
