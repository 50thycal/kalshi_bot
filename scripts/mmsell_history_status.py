"""mmsell settled-history CAPTURE STATUS — is the capture working, and what do we own now?

Kalshi serves only a rolling ~70-day window of settled markets, so `kalshi_bot/mmsell/history.py`
stores them forward, forever (docs/MMSELL_SEASONAL_FORECAST.md). That job is silent by design —
it must never disturb the trading books — so this is how you check it is actually running and
accumulating rather than quietly erroring every cycle.

Four questions:

  1. FRESHNESS — when did the capture last write, and is the pending candle queue draining? A
     queue that only grows means the per-cycle chunk is too small for the enumeration rate.
  2. COVERAGE BY REGIME — markets and candles owned per regime, and how far back they reach.
     The number that matters is **beyond-wall**: markets we hold whose close is already older
     than Kalshi's ~70-day window, i.e. history that no longer exists anywhere else.
  3. WEEKLY ACCUMULATION — settled markets captured per week per regime. This is the seasonal
     supply curve the API cannot give us, being rebuilt one week at a time.
  4. REPLAYABILITY — how many captured markets carry enough of a price path to actually replay
     an mmsell entry against. A market row with no candles cannot be backtested.

Read-only, self-contained (stdlib + psycopg); runs locally or via the ops channel:

    DATABASE_URL_RO=postgresql://... python scripts/mmsell_history_status.py
    # or:  {"type": "script", "name": "mmsell_history_status"}
    #      {"type": "script", "name": "mmsell_history_status", "args": ["--weeks", "20"]}
"""

from __future__ import annotations

import os
import sys

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)

# Kalshi's measured settled-history retention. Markets older than this are gone from the API,
# so anything we hold past it is history we own and nobody can re-derive.
WALL_DAYS = 70


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def _rows(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall()


def _table(headers, widths, rows):
    print("    " + " ".join(str(h).rjust(w) for h, w in zip(headers, widths, strict=False)))
    for r in rows:
        print("    " + " ".join(str(c).rjust(w) for c, w in zip(r, widths, strict=False)))


def report(cur, weeks: int) -> None:
    print("=== mmsell SETTLED-HISTORY CAPTURE STATUS ===")
    print(f"  Kalshi retains ~{WALL_DAYS}d of settled markets; everything past that is ours only")
    print("  because we stored it. See docs/MMSELL_SEASONAL_FORECAST.md.\n")

    # --- 1) freshness -----------------------------------------------------------------
    got = _rows(cur, """
        SELECT count(*),
               count(*) FILTER (WHERE NOT candles_fetched),
               max(fetched_at),
               EXTRACT(EPOCH FROM (now() - max(fetched_at)))/3600.0
        FROM backfill_regime_markets
    """)
    total, pending, last_fetch, age_h = got[0] if got else (0, 0, None, None)
    if not total:
        print("  NO ROWS YET. Either the capture has not run (MMSELL_HISTORY_ENABLED, or the")
        print("  worker has not cycled since deploy), or every enumeration is erroring — check")
        print('  the worker logs for "regime history capture".')
        return
    stale = age_h is not None and age_h > 24
    print(f"=== 1) FRESHNESS ===\n    markets captured: {total}   pending candles: {pending}")
    print(f"    last write: {last_fetch} ({age_h:.1f}h ago)"
          f"{'   <-- STALE, capture may be down' if stale else ''}")

    ncandles = _rows(cur, "SELECT count(*), count(DISTINCT market_ticker) "
                          "FROM backfill_regime_candles")[0]
    print(f"    candles stored: {ncandles[0]} across {ncandles[1]} markets")

    # --- 2) coverage by regime --------------------------------------------------------
    print("\n=== 2) COVERAGE BY REGIME ===")
    rows = _rows(cur, f"""
        SELECT COALESCE(m.regime, '?') AS regime,
               count(*) AS markets,
               count(*) FILTER (WHERE m.candle_count > 0) AS with_path,
               count(*) FILTER (WHERE m.close_time < now() - interval '{WALL_DAYS} days')
                   AS beyond_wall,
               min(m.close_time)::date AS oldest,
               max(m.close_time)::date AS newest,
               COALESCE(sum(m.candle_count), 0) AS candles
        FROM backfill_regime_markets m
        GROUP BY 1 ORDER BY 2 DESC
    """)
    _table(["regime", "markets", "with-path", "BEYOND-WALL", "oldest", "newest", "candles"],
           [12, 8, 10, 12, 11, 11, 9],
           [[r[0], r[1], r[2], r[3], r[4], r[5], r[6]] for r in rows])
    beyond = sum(r[3] for r in rows)
    print(f"    BEYOND-WALL total: {beyond} markets — history the Kalshi API no longer serves.")
    print("    That column is the whole point of this job; it should only ever grow.")

    # --- 3) weekly accumulation -------------------------------------------------------
    print(f"\n=== 3) WEEKLY ACCUMULATION (last {weeks} weeks of settlements) ===")
    rows = _rows(cur, """
        SELECT date_trunc('week', close_time)::date AS wk,
               COALESCE(regime, '?') AS regime, count(*)
        FROM backfill_regime_markets
        WHERE close_time >= now() - (%s || ' weeks')::interval
        GROUP BY 1, 2 ORDER BY 1
    """, (str(weeks),))
    regs = sorted({r[1] for r in rows})
    by_week: dict = {}
    for wk, reg, n in rows:
        by_week.setdefault(wk, {})[reg] = n
    if by_week:
        _table(["week-of", *regs, "ALL"],
               [10, *[max(6, len(r)) for r in regs], 6],
               [[str(wk)] + [by_week[wk].get(r, 0) for r in regs]
                + [sum(by_week[wk].values())] for wk in sorted(by_week)])
        print("    The seasonal supply curve, rebuilt one week at a time. A regime appearing here")
        print("    for the first time is a season arriving — and being captured before it ages out.")
    else:
        print("    (nothing captured in this window yet)")

    # --- 4) replayability -------------------------------------------------------------
    print("\n=== 4) REPLAYABILITY — can these rows actually drive a backtest? ===")
    rows = _rows(cur, """
        SELECT COALESCE(m.regime, '?') AS regime,
               count(*) AS markets,
               count(*) FILTER (WHERE c.n >= 2) AS replayable,
               round(avg(c.n) FILTER (WHERE c.n > 0), 1) AS avg_candles,
               count(*) FILTER (WHERE m.candles_fetched AND COALESCE(c.n, 0) = 0) AS no_path
        FROM backfill_regime_markets m
        LEFT JOIN (
            SELECT market_ticker, count(*) AS n FROM backfill_regime_candles
            GROUP BY 1
        ) c ON c.market_ticker = m.market_ticker
        GROUP BY 1 ORDER BY 2 DESC
    """)
    _table(["regime", "markets", "replayable", "avg-candles", "no-path"],
           [12, 8, 11, 12, 8],
           [[r[0], r[1], r[2], r[3] if r[3] is not None else "-", r[4]] for r in rows])
    print("    replayable = has >=2 candles, the minimum for an entry + a forward path.")
    print("    no-path = fetched but Kalshi served nothing; those are markets we captured too")
    print("    late for their candles, and they are the cost of any delay in starting.")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    weeks = 16
    if "--weeks" in argv:
        i = argv.index("--weeks")
        if i + 1 < len(argv):
            weeks = int(argv[i + 1])

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            report(cur, weeks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
