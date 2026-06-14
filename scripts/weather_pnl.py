"""On-demand P&L: the per-window x strategy decision table for the weather books.

This is the lean, dependency-free (stdlib + psycopg) version of the P&L sections in
scripts/weather_score.py, so it runs on the ops runner. It is the "PnL" command:
a per-book rollup (windows summed) plus the decision table — one row per
(window, strategy) with n, win%, total and P&L-per-trade — since a real-money pick
is one window x strategy, and per-trade is the number that must clear the ~4-5c
round-trip cost to make money.

Usage:
    DATABASE_URL_RO=postgresql://... python scripts/weather_pnl.py
    # or via the ops channel:
    {"type": "script", "name": "weather_pnl"}
"""

from __future__ import annotations

import os
import re
import sys

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=60000 "
    "-c idle_in_transaction_session_timeout=60000"
)

# Book prefixes by kind; low prefixes are longer so they match first
# (mirrors scripts/weather_score.py).
BOOKS = {
    "low": {"fav": "weather_low_fav", "nws": "weather_low_nws", "cal": "weather_low_cal",
            "pm": "weather_low_pm", "obs": "weather_low_obs", "dist": "weather_low_dist"},
    "high": {"fav": "weather_fav", "nws": "weather_nws", "cal": "weather_cal",
             "pm": "weather_pm", "cwin": "weather_cwin", "obs": "weather_obs",
             "dist": "weather_dist"},
}
_HOURS = re.compile(r"_h(\d+)$")

# series prefix -> city code (mirrors kalshi_bot/weather/cities.py; this script is
# self-contained so it can't import the package).
CITY_BY_SERIES = {
    "KXHIGHNY": "NYC", "KXLOWTNYC": "NYC", "KXHIGHCHI": "CHI", "KXLOWTCHI": "CHI",
    "KXHIGHMIA": "MIA", "KXLOWTMIA": "MIA", "KXHIGHAUS": "AUS", "KXLOWTAUS": "AUS",
    "KXHIGHLAX": "LAX", "KXLOWTLAX": "LAX", "KXHIGHPHIL": "PHIL", "KXLOWTPHIL": "PHIL",
    "KXHIGHDEN": "DEN", "KXLOWTDEN": "DEN",
}


def city_of(ticker: str | None) -> str | None:
    t = ticker or ""
    for series, code in CITY_BY_SERIES.items():
        if t.startswith(series):
            return code
    return None



def book_of(strategy: str | None) -> tuple[str, str] | None:
    s = strategy or ""
    for kind in ("low", "high"):
        for book, prefix in BOOKS[kind].items():
            if s.startswith(prefix):
                return (kind, book)
    return None


def window_of(strategy: str | None) -> int | None:
    m = _HOURS.search(strategy or "")
    return int(m.group(1)) if m else None


def aggregate(rows: list[tuple]):
    """rows = (strategy, settled, wins, pnl_dollars). Returns (rollup, grid) where
    rollup is keyed (kind, book) and grid is keyed (kind, window, book), each
    accumulating [n, wins, pnl_cents]."""
    rollup: dict[tuple[str, str], list[float]] = {}
    grid: dict[tuple[str, int, str], list[float]] = {}
    for strat, n, wins, pnl in rows:
        kb = book_of(strat)
        if kb is None:
            continue
        n, wins, cents = int(n), int(wins or 0), float(pnl or 0.0) * 100.0
        rb = rollup.setdefault(kb, [0, 0, 0.0])
        rb[0] += n
        rb[1] += wins
        rb[2] += cents
        w = window_of(strat)
        if w is not None:
            gc = grid.setdefault((kb[0], w, kb[1]), [0, 0, 0.0])
            gc[0] += n
            gc[1] += wins
            gc[2] += cents
    return rollup, grid


def _pt(cents: float, n: int) -> str:
    return f"{cents / n:+.1f}c" if n else "n/a"


def report(rows: list[tuple]) -> None:
    rollup, grid = aggregate(rows)
    print("=== Realized P&L by book (all windows summed, settled) ===")
    if not rollup:
        print("  (no settled weather trades yet)")
    else:
        print(f"  {'book':16s} {'settled':>7}  {'win%':>4}  {'total':>9}  {'per-trade':>9}")
        grand = [0, 0, 0.0]
        for kind in ("high", "low"):
            for book in ("fav", "nws", "cal", "pm", "cwin", "obs", "dist"):
                if (kind, book) not in rollup:
                    continue
                n, wins, cents = rollup[(kind, book)]
                grand[0] += n
                grand[1] += wins
                grand[2] += cents
                wr = f"{wins / n * 100:.0f}%" if n else "n/a"
                print(f"  {kind + ' ' + book:16s} {n:7d}  {wr:>4}  "
                      f"{cents / 100.0:+8.2f}$  {_pt(cents, n):>9}")
        gn, _gw, gc = grand
        print(f"  {'TOTAL':16s} {gn:7d}  {'':>4}  {gc / 100.0:+8.2f}$  {_pt(gc, gn):>9}")

    for kind in ("high", "low"):
        print(f"\n=== {kind.upper()} books — by window x strategy (settled) ===")
        windows = sorted({w for (k, w, _b) in grid if k == kind}, reverse=True)
        if not windows:
            print("  (no settled trades yet)")
            continue
        print(f"  {'window':6s} {'strat':5s} {'n':>4} {'win%':>5} {'total':>8} {'per-trade':>9}")
        best = None
        for w in windows:
            for b in ("fav", "nws", "cal", "pm", "cwin", "obs", "dist"):
                if (kind, w, b) not in grid:
                    continue
                n, wins, cents = grid[(kind, w, b)]
                wr = f"{wins / n * 100:.0f}%" if n else "n/a"
                pv = cents / n if n else None
                print(f"  h{w:<5d} {b:5s} {n:4d} {wr:>5} {cents / 100.0:+7.2f}$ {_pt(cents, n):>9}")
                if pv is not None and (best is None or pv > best[0]):
                    best = (pv, w, b, n)
            print()
        if best:
            v, bw, bb, bn = best
            print(f"  best {kind} window x strategy by per-trade: {bb} @ h{bw} ({v:+.1f}c, n={bn})")


def granular(rows: list[tuple]):
    """rows = (strategy, market_ticker, resolved_value, pnl). Returns dicts keyed at three
    granularities: the full (kind, book, window, city) cell, the (kind, book, city) rollup
    (windows summed), and (kind, book, window) — each [n, wins, pnl_cents]."""
    full: dict[tuple, list[float]] = {}
    by_city: dict[tuple, list[float]] = {}
    by_win: dict[tuple, list[float]] = {}
    for strat, ticker, resolved, pnl in rows:
        kb = book_of(strat)
        if kb is None:
            continue
        kind, book = kb
        w = window_of(strat)
        city = city_of(ticker) or "?"
        win = 1 if int(resolved or 0) == 100 else 0
        cents = float(pnl or 0.0) * 100.0
        for key, table in (
            ((kind, book, w, city), full),
            ((kind, book, city), by_city),
            ((kind, book, w), by_win),
        ):
            c = table.setdefault(key, [0, 0, 0.0])
            c[0] += 1
            c[1] += win
            c[2] += cents
    return full, by_city, by_win


def _rank(table: dict, min_n: int, top: int) -> list[tuple]:
    out = [(k, v) for k, v in table.items() if v[0] >= min_n]
    out.sort(key=lambda kv: kv[1][2] / kv[1][0], reverse=True)
    return out[:top]


def report_best(rows: list[tuple], top: int, min_n: int) -> None:
    full, by_city, by_win = granular(rows)
    print(f"\n=== Best strategies (settled, n >= {min_n}, ranked by P&L/trade) ===")
    print("  *** the finer the slice the smaller the sample — treat tiny-n cells as noise,"
          " not a pick ***")

    print(f"\n  --- Top {top}: book x window x city (the most granular cell) ---")
    print(f"  {'rank':>4} {'kind':>4} {'book':>5} {'window':>6} {'city':>5} "
          f"{'n':>4} {'win%':>5} {'total':>8} {'per-trade':>9}")
    best = _rank(full, min_n, top)
    if not best:
        print(f"    (no cells with n >= {min_n})")
    for i, ((kind, book, w, city), (n, wins, cents)) in enumerate(best, 1):
        wstr = f"h{w}" if w is not None else "-"
        print(f"  {i:>4} {kind:>4} {book:>5} {wstr:>6} {city:>5} {n:4d} "
              f"{wins / n * 100:4.0f}% {cents / 100.0:+7.2f}$ {cents / n:+8.1f}c")

    print(f"\n  --- Top {top}: book x city (windows summed) ---")
    print(f"  {'rank':>4} {'kind':>4} {'book':>5} {'city':>5} {'n':>4} {'win%':>5}"
          f" {'total':>8} {'per-trade':>9}")
    for i, ((kind, book, city), (n, wins, cents)) in enumerate(_rank(by_city, min_n, top), 1):
        print(f"  {i:>4} {kind:>4} {book:>5} {city:>5} {n:4d} "
              f"{wins / n * 100:4.0f}% {cents / 100.0:+7.2f}$ {cents / n:+8.1f}c")

    print(f"\n  --- Top {top}: book x window (all cities) ---")
    print(f"  {'rank':>4} {'kind':>4} {'book':>5} {'window':>6} {'n':>4} {'win%':>5}"
          f" {'total':>8} {'per-trade':>9}")
    for i, ((kind, book, w), (n, wins, cents)) in enumerate(_rank(by_win, min_n, top), 1):
        wstr = f"h{w}" if w is not None else "-"
        print(f"  {i:>4} {kind:>4} {book:>5} {wstr:>6} {n:4d} "
              f"{wins / n * 100:4.0f}% {cents / 100.0:+7.2f}$ {cents / n:+8.1f}c")


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--best", type=int, default=5,
                    help="show the top-N best strategy cells (book x window x city); 0 = off")
    ap.add_argument("--min-n", type=int, default=4,
                    help="minimum settled trades for a cell to be ranked (noise guard)")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT strategy,"
                " count(*) FILTER (WHERE status='settled') AS settled,"
                " count(*) FILTER (WHERE status='settled' AND resolved_value=100) AS wins,"
                " sum(pnl) FILTER (WHERE status='settled') AS pnl"
                " FROM paper_trades WHERE strategy LIKE 'weather%' AND NOT legacy"
                " GROUP BY strategy ORDER BY strategy"
            )
            rows = cur.fetchall()
            best_rows = []
            if args.best > 0:
                cur.execute(
                    "SELECT strategy, market_ticker, resolved_value, pnl"
                    " FROM paper_trades WHERE strategy LIKE 'weather%' AND NOT legacy"
                    " AND status='settled'"
                )
                best_rows = cur.fetchall()
    report(rows)
    if args.best > 0:
        report_best(best_rows, args.best, args.min_n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
