"""Entry-window sweep: what hours-to-close is the best moment to ENTER? Measured by
replaying the collected bucket-ladder snapshots — the entry analog of the TP/SL sweep,
so we map the P&L-vs-entry-hour curve continuously instead of running a live book per
window for months.

For every settled event with ladder coverage, at each candidate window we take the
snapshot nearest that hours-to-close and simulate two books, held to settlement:
  - FAVORITE: buy the max-mid bucket at that snapshot (the market's pick).
  - NWS:      buy the bucket containing the event's morning NWS forecast (a fixed
              bucket per event; only the entry ask moves with the window).
Both buy YES at the snapshot ask, pay the Kalshi entry fee, and settle 0/100 — exactly
how the live books score, so the curve says where to put WEATHER_ENTRY_HOURS.

Read-only, stdlib + psycopg. Usage:
    DATABASE_URL_RO=postgresql://... python scripts/weather_window_sweep.py
    {"type": "script", "name": "weather_window_sweep", "args": ["--windows", "24,12,6,2"]}
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)

DEFAULT_WINDOWS = (24, 22, 20, 18, 16, 14, 12, 10, 8, 6, 4, 2)
WINDOW_TOLERANCE_H = 1.5  # only score a window if a snapshot sits within this of it
MIN_PER_CELL = 8  # below this a (book, window) per-trade is noise — flag it


def fee_cents(price_cents: float, enabled: bool = True) -> float:
    """Kalshi fee in cents at qty=1 (mirrors paper/engine.py::kalshi_fee)."""
    if not enabled:
        return 0.0
    p = price_cents / 100.0
    return float(math.ceil(0.07 * p * (1 - p) * 100))


def value_in_bucket(value: float, low: float | None, high: float | None) -> bool:
    """Same rounding semantics as kalshi_bot/weather/buckets.py::forecast_in_bucket."""
    r = round(value)
    if low is not None and r < low:
        return False
    if high is not None and r > high:
        return False
    return True


@dataclass
class Bucket:
    market_ticker: str | None
    low_f: float | None
    high_f: float | None
    yes_ask: float | None
    mid: float | None


@dataclass
class Cycle:
    hours_to_close: float
    buckets: list[Bucket]


def cluster_cycles(rows: list[dict], gap_seconds: float = 90.0) -> list[Cycle]:
    """Group ladder rows into per-cycle snapshots (one insert batch lands within
    microseconds; cycles are minutes apart)."""
    rows = sorted(rows, key=lambda r: r["captured_at"])
    cycles: list[Cycle] = []
    group: list[dict] = []
    for r in rows:
        if group and (r["captured_at"] - group[-1]["captured_at"]).total_seconds() > gap_seconds:
            cycles.append(_make_cycle(group))
            group = []
        group.append(r)
    if group:
        cycles.append(_make_cycle(group))
    return [c for c in cycles if c is not None]


def _make_cycle(group: list[dict]) -> Cycle | None:
    by_ticker = {r["market_ticker"]: r for r in group}
    htcs = [r["hours_to_close"] for r in group if r["hours_to_close"] is not None]
    if not htcs:
        return None
    buckets = [
        Bucket(r["market_ticker"], r["low_f"], r["high_f"], r["yes_ask_cents"], r["mid_cents"])
        for r in by_ticker.values()
    ]
    return Cycle(hours_to_close=max(htcs), buckets=buckets)


def pick_cycle_nearest(cycles: list[Cycle], window_h: float) -> Cycle | None:
    """The snapshot whose hours-to-close is closest to the window, within tolerance."""
    if not cycles:
        return None
    best = min(cycles, key=lambda c: abs(c.hours_to_close - window_h))
    if abs(best.hours_to_close - window_h) > WINDOW_TOLERANCE_H:
        return None
    return best


def _entry_pnl(bucket: Bucket, winner_ticker, fees: bool) -> float | None:
    """YES at the bucket's ask, held to settlement (0/100), entry fee only."""
    ask = bucket.yes_ask
    if ask is None or not (1 <= ask <= 99):
        return None
    won = bucket.market_ticker == winner_ticker
    return (100.0 if won else 0.0) - ask - fee_cents(ask, fees)


def favorite_bucket(cycle: Cycle) -> Bucket | None:
    priced = [b for b in cycle.buckets if b.mid is not None]
    return max(priced, key=lambda b: b.mid) if priced else None


def forecast_bucket(cycle: Cycle, forecast_f: float | None) -> Bucket | None:
    if forecast_f is None:
        return None
    for b in cycle.buckets:
        if value_in_bucket(forecast_f, b.low_f, b.high_f):
            return b
    return None


def sweep(events: list[dict], windows: list[float], fees: bool):
    """events: [{kind, city, winner, forecast, cycles}]. Returns nested dict
    cell[book][(kind, window)] = [n, wins, pnl_cents] and a per-(city,window) favorite
    breakdown cell_city[(kind, city, window)] = [n, wins, pnl_cents]."""
    cell: dict[str, dict[tuple[str, float], list[float]]] = {"fav": {}, "nws": {}}
    cell_city: dict[tuple[str, str, float], list[float]] = {}
    for ev in events:
        kind, city, winner = ev["kind"], ev["city"], ev["winner"]
        for w in windows:
            cyc = pick_cycle_nearest(ev["cycles"], w)
            if cyc is None:
                continue
            picks = {"fav": favorite_bucket(cyc), "nws": forecast_bucket(cyc, ev["forecast"])}
            for book, bucket in picks.items():
                if bucket is None:
                    continue
                pnl = _entry_pnl(bucket, winner, fees)
                if pnl is None:
                    continue
                c = cell[book].setdefault((kind, w), [0, 0, 0.0])
                c[0] += 1
                c[1] += int(bucket.market_ticker == winner)
                c[2] += pnl
                if book == "fav":
                    cc = cell_city.setdefault((kind, city or "?", w), [0, 0, 0.0])
                    cc[0] += 1
                    cc[1] += int(bucket.market_ticker == winner)
                    cc[2] += pnl
    return cell, cell_city


def _row(n, wins, cents):
    wr = f"{wins / n * 100:.0f}%" if n else "n/a"
    pt = f"{cents / n:+.1f}c" if n else "n/a"
    return n, wr, pt, (cents / n if n else None)


def report(cell, cell_city, windows, n_events) -> None:
    print("=== Entry-window sweep — favorite & nws by hours-to-close "
          "(fees on, YES at ask, held to settlement) ===")
    print(f"  settled events with ladder coverage: {n_events}")
    print("  (snapshots start ~Jun 10, so later windows have more coverage than earlier"
          " ones for now; the curve sharpens as data accrues)")

    for kind in ("high", "low"):
        kwins = [w for w in windows if (kind, w) in cell["fav"] or (kind, w) in cell["nws"]]
        if not kwins:
            continue
        print(f"\n  --- {kind.upper()}: P&L per trade by entry window ---")
        print(f"  {'window':6s} | {'fav_n':>5} {'fav_win':>7} {'fav/trade':>9}"
              f" | {'nws_n':>5} {'nws_win':>7} {'nws/trade':>9}")
        best = {"fav": None, "nws": None}
        for w in sorted(kwins, reverse=True):
            parts = [f"  h{int(w):<4d} |"]
            for book in ("fav", "nws"):
                n, wr, pt, pv = _row(*cell[book].get((kind, w), [0, 0, 0.0]))
                flag = "*" if (n and n < MIN_PER_CELL) else " "
                parts.append(f" {n:5d} {wr:>7} {pt:>8}{flag}")
                if pv is not None and n >= MIN_PER_CELL and (
                    best[book] is None or pv > best[book][0]
                ):
                    best[book] = (pv, w, n)
            print(" |".join(parts))
        for book in ("fav", "nws"):
            if best[book]:
                pv, w, n = best[book]
                print(f"  best {kind} {book} entry window: h{int(w)} ({pv:+.1f}c/trade, n={n})"
                      f"  [n>={MIN_PER_CELL} only; * = thinner]")

    # Favorite by city (the star books are city-specific, e.g. low fav).
    print("\n  --- FAVORITE P&L/trade by (kind, city) x window (n>=5) ---")
    keys = sorted({(k, c) for (k, c, _w) in cell_city})
    for kind, city in keys:
        cells = {w: cell_city[(kind, city, w)] for w in windows if (kind, city, w) in cell_city}
        ranked = sorted(
            ((cents / n, w, n) for w, (n, _wins, cents) in cells.items() if n >= 5),
            reverse=True,
        )
        if ranked:
            top = ", ".join(f"h{int(w)} {pv:+.1f}c(n{n})" for pv, w, n in ranked[:3])
            print(f"  {kind:4s} {city:5s}: best windows -> {top}")


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def load_events(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT event_ticker, city, kind, winning_ticker FROM weather_settlements"
            " WHERE winning_ticker IS NOT NULL"
        )
        settlements = {
            r[0]: {"event_ticker": r[0], "city": r[1], "kind": r[2] or "high", "winner": r[3]}
            for r in cur.fetchall()
        }
        if not settlements:
            return []
        tickers = list(settlements)
        cur.execute(
            "SELECT event_ticker, market_ticker, low_f, high_f, yes_ask_cents, mid_cents,"
            " hours_to_close, captured_at FROM weather_bucket_snapshots"
            " WHERE event_ticker = ANY(%s) ORDER BY captured_at",
            (tickers,),
        )
        cols = [d.name for d in cur.description]
        ladders: dict[str, list[dict]] = {}
        for r in cur.fetchall():
            row = dict(zip(cols, r, strict=True))
            ladders.setdefault(row["event_ticker"], []).append(row)
        # earliest (morning) non-null forecast per event — the tradeable value
        cur.execute(
            "SELECT DISTINCT ON (event_ticker) event_ticker, forecast_high_f"
            " FROM weather_forecasts WHERE forecast_high_f IS NOT NULL"
            " AND event_ticker = ANY(%s) ORDER BY event_ticker, captured_at ASC",
            (tickers,),
        )
        forecasts = {r[0]: float(r[1]) for r in cur.fetchall()}

    events = []
    for tk, st in settlements.items():
        rows = ladders.get(tk)
        if not rows:
            continue
        events.append({
            "kind": st["kind"], "city": st["city"], "winner": st["winner"],
            "forecast": forecasts.get(tk), "cycles": cluster_cycles(rows),
        })
    return events


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--windows", default=",".join(str(w) for w in DEFAULT_WINDOWS))
    ap.add_argument("--no-fees", action="store_true")
    args = ap.parse_args(argv)
    windows = [float(w) for w in args.windows.split(",") if w.strip()]

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        events = load_events(conn)

    if not events:
        print("=== Entry-window sweep ===\n  (no settled events with ladder coverage yet)")
        return 0
    cell, cell_city = sweep(events, windows, not args.no_fees)
    report(cell, cell_city, windows, len(events))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
