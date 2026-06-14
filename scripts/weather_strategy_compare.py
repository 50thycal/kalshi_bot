"""Compare the favorite weather book across three lenses, side by side:

  1. BACKFILL  — replay "buy the favorite at h{window}, hold to settlement" on the Kalshi
                 REST history (backfill_* tables, 964 events). Does the edge hold historically?
  2. LIVE      — the realized P&L of our actual collected paper trades (paper_trades).
  3. LIVE+TP/SL — the best take-profit/stop-loss exit rule applied to those same live trades
                 (replayed through their recorded bucket-bid paths), vs holding.

Only the FAVORITE book is comparable across all three: the backfill is market-only history
(no NWS/ensemble forecast), so nws/cal/dist can't be replayed on it — they appear in the live
column only via scripts/weather_pnl.py.

Self-contained (stdlib + psycopg). Reuses the sibling ops scripts (ops_runner puts scripts/
on the path): backfill replay from weather_backfill_edges, the TP/SL sweep from
weather_exit_sweep, and book/city/window parsing from weather_pnl. Usage:
    {"type": "script", "name": "weather_strategy_compare"}
    {"type": "script", "name": "weather_strategy_compare", "args": ["--windows", "20,14,8"]}
"""

from __future__ import annotations

import argparse
import os
import sys

import weather_backfill_edges as be
import weather_exit_sweep as es
import weather_pnl as pnl

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=180000 "
    "-c idle_in_transaction_session_timeout=180000"
)

TPSL_GRID = (
    [(None, None)]
    + [(tp, None) for tp in (5, 10, 20)]
    + [(None, sl) for sl in (5, 10, 20)]
    + [(tp, sl) for tp in (5, 10, 20) for sl in (5, 10, 20)]
)


def _ev(cell) -> str:
    n, _wins, cents = cell
    return f"{cents / n:+.1f}c" if n else "n/a"


def _win(cell) -> str:
    n, wins, _cents = cell
    return f"{wins / n * 100:.0f}%" if n else "n/a"


def backfill_favorite(events, windows, fees):
    """{(kind, window): [n, wins, pnl_cents]} for buy-favorite@window, hold to settlement."""
    out: dict[tuple[str, int], list[float]] = {}
    for kind in ("high", "low"):
        evs = [e for e in events if e.kind == kind]
        for w in windows:
            cell = [0, 0, 0.0]
            for ev in evs:
                cyc = be._nearest_cycle(ev, float(w), 3.0)
                if cyc is not None:
                    be._strat_entry(cell, cyc, ev.winner, be._favorite(cyc.buckets), fees)
            out[(kind, w)] = cell
    return out


def backfill_favorite_by_city(events, windows, fees):
    out: dict[tuple[str, str], list[float]] = {}
    for ev in events:
        for w in windows:
            cyc = be._nearest_cycle(ev, float(w), 3.0)
            if cyc is None:
                continue
            cell = out.setdefault((ev.kind, ev.city), [0, 0, 0.0])
            be._strat_entry(cell, cyc, ev.winner, be._favorite(cyc.buckets), fees)
    return out


def backfill_favorite_by_cell(events, windows, fees):
    """{(kind, city, window): [n, wins, pnl_cents]} — the finest backfill granularity."""
    out: dict[tuple[str, str, int], list[float]] = {}
    for ev in events:
        for w in windows:
            cyc = be._nearest_cycle(ev, float(w), 3.0)
            if cyc is None:
                continue
            cell = out.setdefault((ev.kind, ev.city, w), [0, 0, 0.0])
            be._strat_entry(cell, cyc, ev.winner, be._favorite(cyc.buckets), fees)
    return out


def live_favorite(conn):
    """Realized favorite P&L from paper_trades, keyed (kind,window), (kind,city), (kind,city,window)."""
    by_window: dict[tuple[str, int], list[float]] = {}
    by_city: dict[tuple[str, str], list[float]] = {}
    by_cell: dict[tuple[str, str, int], list[float]] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT strategy, market_ticker, resolved_value, pnl FROM paper_trades"
            " WHERE strategy LIKE 'weather%' AND NOT legacy AND status='settled'"
        )
        for strat, ticker, resolved, pnl_d in cur.fetchall():
            kb = pnl.book_of(strat)
            if kb is None or kb[1] != "fav":
                continue
            win = 1 if int(resolved or 0) == 100 else 0
            cents = float(pnl_d or 0.0) * 100.0
            w = pnl.window_of(strat)
            city = pnl.city_of(ticker) or "?"
            if w is not None:
                _acc(by_window, (kb[0], w), win, cents)
                _acc(by_cell, (kb[0], city, w), win, cents)
            _acc(by_city, (kb[0], city), win, cents)
    return by_window, by_city, by_cell


def _acc(table, key, win, cents) -> None:
    c = table.setdefault(key, [0, 0, 0.0])
    c[0] += 1
    c[1] += win
    c[2] += cents


def live_tpsl_best(conn):
    """Best TP/SL per (kind, window) on the live favorite trades vs holding."""
    trades, _skipped = es.fetch_trades(conn, allowed={"high_fav", "low_fav"})
    grouped: dict[tuple[str, int], list] = {}
    for t in trades:
        kb = es.book_of(t.strategy)
        w = pnl.window_of(t.strategy)
        if kb and w is not None:
            grouped.setdefault((kb[0], w), []).append(t)
    out: dict[tuple[str, int], tuple] = {}
    for key, ts in grouped.items():
        res = es.sweep(ts, TPSL_GRID)
        hold = next(r for r in res if r.tp is None and r.sl is None and r.be is None)
        best = max(res, key=lambda r: r.per_trade)
        out[key] = (len(ts), hold.per_trade, best)
    return out


def report(bf, live_win, live_city, tpsl, bf_city, windows) -> None:
    print("=== Favorite book: backfill vs live vs live+TP/SL (buy favorite, hold) ===")
    print("  (backfill = Kalshi REST history, all events; live = our collected paper trades;")
    print("   live+TP/SL = best take-profit/stop-loss replayed on the live trades' price paths)")
    print(f"\n  {'kind':>4} {'window':>6} | {'backfill':>14} | {'live':>16} | {'live+TP/SL':>20}")
    print(f"  {'':>4} {'':>6} | {'ev/tr (n)':>14} | {'ev/tr (n, win%)':>16} | {'ev/tr  (rule)':>20}")
    for kind in ("high", "low"):
        for w in windows:
            bcell = bf.get((kind, w), [0, 0, 0.0])
            lcell = live_win.get((kind, w), [0, 0, 0.0])
            bf_s = f"{_ev(bcell)} ({bcell[0]})"
            live_s = f"{_ev(lcell)} ({lcell[0]}, {_win(lcell)})"
            tp = tpsl.get((kind, w))
            if tp is None:
                tp_s = "n/a"
            else:
                _n, hold_pt, best = tp
                if best.tp is None and best.sl is None:
                    tp_s = f"{hold_pt:+.1f}c (hold best)"
                else:
                    rule = f"tp={es._lvl(best.tp)}/sl={es._lvl(best.sl)}"
                    tp_s = f"{best.per_trade:+.1f}c ({rule})"
            print(f"  {kind:>4} h{w:<5} | {bf_s:>14} | {live_s:>16} | {tp_s:>20}")

    print("\n=== Favorite book by city: backfill vs live (windows pooled) ===")
    print(f"  {'kind':>4} {'city':>5} | {'backfill ev/tr (n)':>20} | {'live ev/tr (n, win%)':>22}")
    for kind, city in sorted(set(live_city) | set(bf_city)):
        bcell = bf_city.get((kind, city), [0, 0, 0.0])
        lcell = live_city.get((kind, city), [0, 0, 0.0])
        if bcell[0] == 0 and lcell[0] == 0:
            continue
        bf_s = f"{_ev(bcell)} ({bcell[0]})"
        live_s = f"{_ev(lcell)} ({lcell[0]}, {_win(lcell)})"
        print(f"  {kind:>4} {city:>5} | {bf_s:>20} | {live_s:>22}")
    print("\n  Note: nws/cal/dist books are LIVE-only (the backfill is market-only history,")
    print("  no forecast) — see scripts/weather_pnl.py for their realized P&L.")


def report_ranked(bf_cell, live_cell, *, top: int, min_n: int) -> None:
    """Inverse view: rank the BEST backfill cells (kind x city x window) by EV/trade on the
    large historical sample, then show whether live confirmed them."""
    ranked = sorted(
        ((k, v) for k, v in bf_cell.items() if v[0] >= min_n),
        key=lambda kv: kv[1][2] / kv[1][0], reverse=True,
    )[:top]
    print(f"\n=== Best BACKFILL favorite cells (n >= {min_n}) vs live ===")
    print("  (ranked by the robust historical sample; live is the same cell's realized P&L)")
    print(f"  {'rank':>4} {'kind':>4} {'city':>5} {'window':>6} | "
          f"{'backfill ev (n, win%)':>22} | {'live ev (n, win%)':>20}")
    for i, ((kind, city, w), bcell) in enumerate(ranked, 1):
        lcell = live_cell.get((kind, city, w), [0, 0, 0.0])
        bf_s = f"{_ev(bcell)} ({bcell[0]}, {_win(bcell)})"
        live_s = f"{_ev(lcell)} ({lcell[0]}, {_win(lcell)})" if lcell[0] else "no live trades"
        print(f"  {i:>4} {kind:>4} {city:>5} h{w:<5} | {bf_s:>22} | {live_s:>20}")
    print("  (a backfill edge is trustworthy only if live agrees in SIGN — small live n is noise)")


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--windows", default="20,14,8", help="entry windows (hours-to-close)")
    ap.add_argument("--no-fees", action="store_true")
    ap.add_argument("--top", type=int, default=12,
                    help="show the top-N best backfill cells vs live (0 = off)")
    ap.add_argument("--min-n", type=int, default=15,
                    help="min backfill trades for a cell to be ranked")
    args = ap.parse_args(argv)
    windows = [int(w) for w in args.windows.split(",") if w.strip()]
    fees = not args.no_fees

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        events = be.load_events(conn)
        bf = backfill_favorite(events, windows, fees)
        bf_city = backfill_favorite_by_city(events, windows, fees)
        bf_cell = backfill_favorite_by_cell(events, windows, fees)
        live_win, live_city, live_cell = live_favorite(conn)
        tpsl = live_tpsl_best(conn)

    print(f"=== Strategy comparison — {len(events)} backfill events ===")
    report(bf, live_win, live_city, tpsl, bf_city, windows)
    if args.top > 0:
        report_ranked(bf_cell, live_cell, top=args.top, min_n=args.min_n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
