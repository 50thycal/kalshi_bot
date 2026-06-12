"""Backfill calibration: is Kalshi's temperature-market price honest, measured on the
Kalshi REST history (backfill_weather_markets / backfill_weather_candles) rather than
the handful of live-collected events.

PROVENANCE: this reads ONLY the backfill_* tables (Kalshi archive reconstruction). It
never touches the live weather_* tables — keep the two apart when comparing.

For each backfilled bucket-market we have its settled result and an hourly candle path.
At each hours-to-close window we take the candle nearest that window, read the bucket's
mid price (implied probability) and ask, and pool by price band across every market:

  - actual win rate vs implied price per band  -> favorite-longshot bias
  - EV of buying YES at the ask (fees on)       -> is any band tradeable long
  - EV of buying NO at 100-bid (fees on)        -> is any band tradeable short
  - per-event favorite: buy the top-mid bucket  -> does "buy the favorite" pay at scale

Only EVENTS whose backfilled buckets contain exactly one winner are used (so partial
backfill coverage can't invent or drop the settled outcome). Still preliminary: the
candle history is recent-weeks only and the backfill fills newest-first.

Read-only, stdlib + psycopg. Usage:
    DATABASE_URL_RO=postgresql://... python scripts/weather_backfill_calib.py
    {"type": "script", "name": "weather_backfill_calib", "args": ["--windows", "20,14,8"]}
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

PRICE_BANDS = ((1, 20), (20, 40), (40, 60), (60, 80), (80, 99))
WINDOW_TOLERANCE_H = 2.5
MIN_PER_BAND = 50  # below this a band's win rate is not yet trustworthy


def fee_cents(price_cents: float, enabled: bool = True) -> float:
    """Kalshi fee in cents at qty=1 (mirrors paper/engine.py::kalshi_fee)."""
    if not enabled:
        return 0.0
    p = price_cents / 100.0
    return float(math.ceil(0.07 * p * (1 - p) * 100))


def band_of(price: float) -> tuple[int, int] | None:
    for lo, hi in PRICE_BANDS:
        if lo <= price < hi or (hi == 99 and price >= 99):
            return (lo, hi)
    return None


def wilson(k: int, n: int) -> tuple[float, float]:
    """95% Wilson interval for a binomial rate — honest error bars on win%."""
    if n == 0:
        return (0.0, 0.0)
    z = 1.96
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


@dataclass
class Candle:
    hours_to_close: float
    mid: float | None
    ask: float | None
    bid: float | None


def pick_candle(candles: list[Candle], window_h: float) -> Candle | None:
    """Candle nearest the window (within tolerance) that has a usable mid."""
    usable = [c for c in candles if c.mid is not None]
    if not usable:
        return None
    best = min(usable, key=lambda c: abs(c.hours_to_close - window_h))
    if abs(best.hours_to_close - window_h) > WINDOW_TOLERANCE_H:
        return None
    return best


def calibrate(events: dict[str, list[dict]], windows: list[float]):
    """events: event_ticker -> [market dicts]. Returns:
    band_cells[(window, band)] = [n, implied_sum, wins, yes_ev_sum, no_ev_sum],
    fav_cells[window] = [n, wins, ev_sum]  (per-event favorite at that window)."""
    band_cells: dict[tuple[float, tuple[int, int]], list[float]] = {}
    fav_cells: dict[float, list[float]] = {w: [0, 0, 0.0] for w in windows}
    used_events = 0

    for markets in events.values():
        winners = [m for m in markets if (m["result"] or "").lower() == "yes"]
        if len(winners) != 1:
            continue  # incomplete / ambiguous backfill coverage
        used_events += 1
        winner_ticker = winners[0]["market_ticker"]
        for w in windows:
            picks: list[tuple[dict, Candle]] = []
            for m in markets:
                c = pick_candle(m["candles"], w)
                if c is None:
                    continue
                picks.append((m, c))
                band = band_of(c.mid)
                if band is None:
                    continue
                won = m["market_ticker"] == winner_ticker
                cell = band_cells.setdefault((w, band), [0, 0.0, 0, 0.0, 0.0])
                cell[0] += 1
                cell[1] += c.mid / 100.0
                cell[2] += int(won)
                if c.ask is not None and 1 <= c.ask <= 99:
                    cell[3] += (100.0 if won else 0.0) - c.ask - fee_cents(c.ask)
                if c.bid is not None and 1 <= c.bid <= 99:
                    no_ask = 100.0 - c.bid
                    cell[4] += (0.0 if won else 100.0) - no_ask - fee_cents(no_ask)
            # Per-event favorite = highest-mid bucket at this window.
            if picks:
                fm, fc = max(picks, key=lambda mc: mc[1].mid)
                if fc.ask is not None and 1 <= fc.ask <= 99:
                    won = fm["market_ticker"] == winner_ticker
                    fav = fav_cells[w]
                    fav[0] += 1
                    fav[1] += int(won)
                    fav[2] += (100.0 if won else 0.0) - fc.ask - fee_cents(fc.ask)
    return band_cells, fav_cells, used_events


def report(band_cells, fav_cells, windows, used_events, n_markets) -> None:
    print("=== Backfill calibration (Kalshi REST history — separate from live data) ===")
    print(f"  complete settled events used: {used_events}   backfilled bucket-markets: {n_markets}")
    print("  (recent weeks only; backfill fills newest-first — read as preliminary structure)")

    print("\n  --- Price honesty by band (pooled over all windows) ---")
    print("  (actual >> implied => band UNDERpriced, buy YES; actual << implied => buy NO)")
    print(f"  {'band':>7} {'n':>5} {'implied':>8} {'actual':>7} {'95% CI':>13} {'gap':>6}"
          f" {'yesEV':>7} {'noEV':>6}")
    pooled: dict[tuple[int, int], list[float]] = {}
    for (_w, band), v in band_cells.items():
        c = pooled.setdefault(band, [0, 0.0, 0, 0.0, 0.0])
        for i in range(5):
            c[i] += v[i]
    for band in sorted(pooled):
        n, imp, wins, yev, nev = pooled[band]
        if not n:
            continue
        lo, hi = wilson(int(wins), int(n))
        flag = "" if n >= MIN_PER_BAND else "  (thin)"
        print(f"  {band[0]:3d}-{band[1]:<3d} {int(n):5d} {imp / n:8.2f} {wins / n:7.2f}"
              f"  [{lo:.2f},{hi:.2f}] {wins / n - imp / n:+6.2f}"
              f" {yev / n:+6.1f}c {nev / n:+5.1f}c{flag}")

    print("\n  --- Same, split by entry window ---")
    for w in windows:
        cells = {band: v for (win, band), v in band_cells.items() if win == w}
        if not cells:
            continue
        parts = [
            f"{b[0]}-{b[1]}:{v[2] / v[0]:.2f}v{v[1] / v[0]:.2f}(n{int(v[0])})"
            for b, v in sorted(cells.items()) if v[0]
        ]
        print(f"  h{int(w):<3} " + "  ".join(parts))

    print("\n  --- 'Buy the favorite' at each window (YES at ask, fees on) ---")
    print(f"  {'window':>6} {'n':>5} {'win%':>5} {'EV/trade':>9}")
    for w in windows:
        n, wins, ev = fav_cells.get(w, [0, 0, 0.0])
        if not n:
            continue
        print(f"  h{int(w):<5} {int(n):5d} {wins / n * 100:4.0f}% {ev / n:+8.1f}c")


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def load_events(conn) -> tuple[dict[str, list[dict]], int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT market_ticker, event_ticker, result, close_time"
            " FROM backfill_weather_markets"
            " WHERE candles_fetched AND close_time IS NOT NULL AND event_ticker IS NOT NULL"
        )
        markets = {
            r[0]: {"market_ticker": r[0], "event_ticker": r[1], "result": r[2],
                   "close_time": r[3], "candles": []}
            for r in cur.fetchall()
        }
        if not markets:
            return {}, 0
        cur.execute(
            "SELECT market_ticker, end_period_ts, price_close, yes_bid_close, yes_ask_close"
            " FROM backfill_weather_candles WHERE market_ticker = ANY(%s)",
            (list(markets),),
        )
        for ticker, ts, price_close, bid, ask in cur.fetchall():
            m = markets.get(ticker)
            if m is None:
                continue
            htc = (m["close_time"] - ts).total_seconds() / 3600.0
            if htc < 0:
                continue
            mid = None
            if bid is not None and ask is not None:
                mid = (float(bid) + float(ask)) / 2.0
            elif price_close is not None:
                mid = float(price_close)
            m["candles"].append(Candle(
                hours_to_close=htc, mid=mid,
                ask=float(ask) if ask is not None else None,
                bid=float(bid) if bid is not None else None,
            ))

    events: dict[str, list[dict]] = {}
    for m in markets.values():
        events.setdefault(m["event_ticker"], []).append(m)
    return events, len(markets)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--windows", default="20,14,8")
    args = ap.parse_args(argv)
    windows = [float(w) for w in args.windows.split(",") if w.strip()]

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        events, n_markets = load_events(conn)

    if not events:
        print("=== Backfill calibration ===\n  (no backfilled markets with candles yet)")
        return 0
    band_cells, fav_cells, used = calibrate(events, windows)
    report(band_cells, fav_cells, windows, used, n_markets)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
