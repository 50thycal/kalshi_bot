"""Per-city CALIBRATION & EV map on the backfill archive — where does the market misprice
temperature buckets, and is that edge to BUY (underpriced) or FADE (overpriced)?

The entry-timing studies showed timing the favorite is not an edge; the edge is in WHICH
city/bucket you trade. This maps it directly. At a chosen pre-close decision window (default
h14, the live primary), it takes every bucket's market price and its settled outcome over all
~500 backfill events (~3k bucket-outcomes), bins by price, and per (city, price-band) reports:

  n, avg implied price, actual yes-rate, EDGE (yes-rate - price, in cents), and the net
  cents/trade of BUYING that bucket at the real ask (hold to settlement).

A positive edge/net band is underpriced (buy YES); a strongly negative one is overpriced
(the favorite-overshoot money — fade it / buy NO). A STANDOUTS section ranks the cells.
Market-only provenance (backfill_weather_*). Self-contained (stdlib + psycopg). Usage:
    {"type":"script","name":"weather_calibration_map","args":["--kind","high","--window","14"]}
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict
from datetime import timedelta

RO_OPTIONS = (
    "-c default_transaction_read_only=on -c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)


def fee_cents(entry: float) -> int:
    p = max(0.0, min(1.0, entry / 100.0))
    return math.ceil(7.0 * p * (1.0 - p))


def buy_pnl(win: bool, ask: float) -> float:
    return (100.0 if win else 0.0) - ask - fee_cents(ask)


class Band:
    __slots__ = ("n", "yes", "px", "ask", "pnl")

    def __init__(self):
        self.n = self.yes = 0
        self.px = self.ask = self.pnl = 0.0

    def add(self, win: bool, price: float, ask: float):
        self.n += 1
        self.yes += 1 if win else 0
        self.px += price
        self.ask += ask
        self.pnl += buy_pnl(win, ask)

    def row(self) -> str:
        if not self.n:
            return ""
        yr = self.yes / self.n * 100
        ap = self.px / self.n
        return (f"{self.n:5d} {ap:6.1f}c {yr:5.0f}% {yr - ap:+6.1f}c"
                f" {self.ask / self.n:6.1f}c {self.pnl / self.n:+7.1f}c")


BAND_W = 10
BANDS = list(range(0, 100, BAND_W))


def band_of(price: float) -> int | None:
    if price is None or price < 0 or price > 100:
        return None
    return min(int(price // BAND_W) * BAND_W, 90)


# --- data load ---------------------------------------------------------------------


def nearest_obs(markets: dict, candles: list, window_h: float) -> dict:
    """Per market_ticker, the candle closest to (close_time - window_h): (price, ask, result)."""
    by_mt: dict = defaultdict(list)
    for mt, ts, ask, bid, close in candles:
        by_mt[mt].append((ts, ask, bid, close))
    out: dict = {}
    for mt, rows in by_mt.items():
        m = markets.get(mt)
        if m is None or m["close"] is None:
            continue
        target = m["close"] - timedelta(hours=window_h)
        ts, ask, bid, close = min(rows, key=lambda r: abs((r[0] - target).total_seconds()))
        price = close if close is not None else bid
        if price is None:
            continue
        ask_c = ask if ask is not None else (close if close is not None else bid)
        out[mt] = (float(price), max(1.0, min(99.0, float(ask_c))), m)
    return out


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


# --- reporting ---------------------------------------------------------------------


def report_city(label: str, cells: dict, min_rows: int) -> None:
    print(f"\n=== Calibration & buy-EV — {label} ===")
    print(f"  {'band':>7} {'n':>5} {'impl':>7} {'yes%':>5} {'edge':>7} {'avg_ask':>7} {'net/trd':>8}")
    tot = Band()
    for lo in BANDS:
        b = cells.get(lo)
        if not b or not b.n:
            continue
        flag = " *" if b.n < min_rows else ""
        print(f"  {lo:3d}-{lo + BAND_W:<3d} {b.row()}{flag}")
        tot.n += b.n
        tot.yes += b.yes
        tot.px += b.px
        tot.ask += b.ask
        tot.pnl += b.pnl
    if tot.n:
        print(f"  {'ALL':>7} {tot.row()}")
    print("  (edge = actual yes-rate - implied price; + = underpriced/BUY, - = overpriced/FADE)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kind", choices=("high", "low"), default="high")
    ap.add_argument("--city", default="ALL")
    ap.add_argument("--window", type=float, default=14.0, help="decision hours-to-close")
    ap.add_argument("--min-rows", type=int, default=15)
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    where, params = ["close_time IS NOT NULL", "kind = %s"], [args.kind]
    if args.city != "ALL":
        where.append("city = %s")
        params.append(args.city)
    clause = " AND ".join(where)
    lo_h, hi_h = args.window + 3.5, args.window - 3.5

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT market_ticker, city, result, close_time"
                f" FROM backfill_weather_markets WHERE {clause}", params)
            markets = {r[0]: {"city": r[1], "result": r[2], "close": r[3]} for r in cur.fetchall()}
            cur.execute(
                "SELECT c.market_ticker, c.end_period_ts, c.yes_ask_close, c.yes_bid_close,"
                " c.price_close FROM backfill_weather_candles c"
                f" JOIN backfill_weather_markets m ON m.market_ticker = c.market_ticker WHERE {clause}"
                f" AND c.end_period_ts BETWEEN m.close_time - interval '{lo_h} hours'"
                f" AND m.close_time - interval '{hi_h} hours'", params)
            candles = cur.fetchall()

    if not markets:
        print("No backfill markets matched.")
        return 0
    obs = nearest_obs(markets, candles, args.window)

    # per-city band cells + pooled
    by_city: dict = defaultdict(lambda: defaultdict(Band))
    pooled: dict = defaultdict(Band)
    for price, ask, m in obs.values():
        lo = band_of(price)
        if lo is None:
            continue
        win = m["result"] == "yes"
        by_city[m["city"]][lo].add(win, price, ask)
        pooled[lo].add(win, price, ask)

    print(f"=== Per-city calibration map — kind={args.kind} window=h{args.window:g} ===")
    print(f"  {len(markets)} bucket-markets, {len(obs)} priced at the window"
          f" ({len(by_city)} cities), bands of {BAND_W}c")
    report_city(f"ALL cities (kind={args.kind})", pooled, args.min_rows)
    for city in sorted(by_city):
        report_city(f"city={city}", by_city[city], args.min_rows)

    # standout cells: most mispriced (by net/trade) with enough sample
    cells = [(c, lo, b) for c, bands in by_city.items() for lo, b in bands.items()
             if b.n >= args.min_rows]
    cells.sort(key=lambda x: x[2].pnl / x[2].n)
    print(f"\n=== STANDOUT (city x band) cells, n>={args.min_rows} — what to trade ===")
    print("  most OVERPRICED (buy NO / fade):")
    for c, lo, b in cells[:6]:
        print(f"    {c:>4} {lo:3d}-{lo + BAND_W:<3d}  n={b.n:4d}  yes={b.yes / b.n * 100:3.0f}%"
              f"  impl={b.px / b.n:4.0f}c  net_buy={b.pnl / b.n:+6.1f}c")
    print("  most UNDERPRICED (buy YES):")
    for c, lo, b in reversed(cells[-6:]):
        print(f"    {c:>4} {lo:3d}-{lo + BAND_W:<3d}  n={b.n:4d}  yes={b.yes / b.n * 100:3.0f}%"
              f"  impl={b.px / b.n:4.0f}c  net_buy={b.pnl / b.n:+6.1f}c")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
