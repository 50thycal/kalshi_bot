"""Offline entry-TIMING study on the BACKFILL archive — the large-sample cross-check of
weather_entry_timing_study. Same question (does dynamic entry beat fixed h20/h14/h8
windows?) but replayed over `backfill_weather_markets` + `backfill_weather_candles`
(Kalshi REST history, ~9x the events), so the structural claim is tested on hundreds of
settled events instead of dozens.

PROVENANCE NOTE (deliberate): the backfill is MARKET-ONLY — it has price candles but NO
forecast / ensemble / observation columns. So the obs-confirmed (B) and forecast-
convergence (C) policies CANNOT be reconstructed here; only the price-driven policies can:

  fixed    — the current rule: buy the favorite at the candle nearest each window.
  A:conv   — enter the first hour the favorite's price clears a conviction bar.
  C':stable— price-stability proxy: enter the first hour the favorite bucket has been the
             same for N consecutive hours (the candle-only half of policy C; no forecast).

All policies BUY THE FAVORITE (highest-priced bucket) and HOLD to settlement, so only entry
TIMING differs. Entry price = the favorite's real yes_ask_close candle. Winner = the bucket
whose market settled result='yes'. Self-contained (stdlib + psycopg). Usage:
    {"type":"script","name":"weather_entry_timing_backfill","args":["--kind","high","--by-city"]}
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict

RO_OPTIONS = (
    "-c default_transaction_read_only=on -c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)
LOOKBACK_HOURS = 26  # only need candles within this many hours of close


# --- pricing -----------------------------------------------------------------------


def fee_cents(entry: float) -> int:
    p = max(0.0, min(1.0, entry / 100.0))
    return math.ceil(7.0 * p * (1.0 - p))


def trade_pnl(win: bool, entry: float) -> float:
    return (100.0 if win else 0.0) - entry - fee_cents(entry)


# --- entry policies (operate on one event's time-ordered cycles, far->near close) ---


def _band(cycles, a):
    return [c for c in cycles if a.htc_min <= c["htc"] <= a.htc_max]


def entry_A(cycles, a):
    for c in _band(cycles, a):
        if c["price"] is not None and c["price"] >= a.conv_threshold:
            return c
    return None


def entry_Cprime(cycles, a):
    run, prev = 0, None
    for c in _band(cycles, a):
        run = run + 1 if c["sub"] == prev else 1
        prev = c["sub"]
        if run >= a.stable_cycles:
            return c
    return None


def entry_fixed(cycles, windows):
    out = []
    if not cycles:
        return out
    for w in windows:
        c = min(cycles, key=lambda c: abs(c["htc"] - w))
        if abs(c["htc"] - w) <= 3.0:
            out.append(c)
    return out


# --- aggregation -------------------------------------------------------------------


class Cell:
    __slots__ = ("n", "wins", "pnl", "buy", "htc", "events")

    def __init__(self):
        self.n = self.wins = 0
        self.pnl = self.buy = self.htc = 0.0
        self.events = set()

    def add(self, c: dict):
        entry = c["ask"]
        self.n += 1
        self.wins += 1 if c["win"] else 0
        self.pnl += trade_pnl(c["win"], entry)
        self.buy += entry
        self.htc += c["htc"]
        self.events.add(c["event"])

    def row(self, total_events: int) -> str:
        if not self.n:
            return f"{0:6d} {'--':>5} {'--':>5} {'--':>6} {'--':>8} {'--':>7} {'--':>9}"
        cov = len(self.events) / total_events * 100 if total_events else 0
        return (f"{self.n:6d} {len(self.events):5d} {cov:4.0f}% {self.wins / self.n * 100:4.0f}%"
                f" {self.buy / self.n:5.1f}c {self.pnl / self.n:+7.1f}c {self.htc / self.n:6.1f}h"
                f" {self.pnl / 100:+8.2f}$")


def run_report(events: dict, windows, a, label: str) -> None:
    total_events = len(events)
    cells = {"fixed": Cell(), "A:conv": Cell(), "C':stable": Cell()}
    for cycles in events.values():
        for c in entry_fixed(cycles, windows):
            cells["fixed"].add(c)
        ca = entry_A(cycles, a)
        if ca is not None:
            cells["A:conv"].add(ca)
        cc = entry_Cprime(cycles, a)
        if cc is not None:
            cells["C':stable"].add(cc)
    wtxt = "/".join(f"h{int(w)}" for w in windows)
    print(f"\n=== Backfill entry-timing — {label} ({total_events} settled events) ===")
    print("  policy        trades   ev cov%  win%  avg_buy  net/trd  htc@in  total$")
    for name in ("fixed", "A:conv", "C':stable"):
        tag = name if name != "fixed" else f"fixed({wtxt})"
        print(f"  {tag:<13} {cells[name].row(total_events)}")
    print(f"  A:conv = fav price >= {a.conv_threshold:g}c | C':stable = fav bucket held"
          f" {a.stable_cycles} candles (price-only proxy; no forecast/obs in backfill)")
    print("  all BUY THE FAVORITE and HOLD to settlement; band ="
          f" {a.htc_min:g}-{a.htc_max:g}h to close. net/trade is the deciding number.")


# --- data load: reconstruct per-event hourly cross-bucket cycles from candles -------


def build_events(markets: dict, candles: list) -> dict:
    """markets: market_ticker -> {event, city, sub, result, close}; candles: rows of
    (market_ticker, ts, ask, bid, close). Returns event_ticker -> [cycle...] sorted
    far->near close, each cycle = the favorite bucket at one hourly snapshot."""
    # group candle rows by (event, ts) -> {market_ticker: (ask, price)}
    snaps: dict = defaultdict(dict)
    for mt, ts, ask, bid, close in candles:
        m = markets.get(mt)
        if m is None or m["close"] is None:
            continue
        price = close if close is not None else bid  # canonical bucket price (cents)
        if price is None:
            continue
        ask_c = ask if ask is not None else (close if close is not None else bid)
        snaps[(m["event"], ts)][mt] = (float(ask_c), float(price))
    events: dict = defaultdict(list)
    for (event, ts), legs in snaps.items():
        # favorite = highest-priced bucket at this snapshot
        fav_mt = max(legs, key=lambda mt: legs[mt][1])
        m = markets[fav_mt]
        htc = (m["close"] - ts).total_seconds() / 3600.0
        if htc <= 0:
            continue
        ask, price = legs[fav_mt]
        events[event].append({
            "event": event, "htc": htc, "price": price,
            "ask": max(1.0, min(99.0, ask)), "sub": m["sub"], "win": m["result"] == "yes",
        })
    for cycles in events.values():
        cycles.sort(key=lambda c: -c["htc"])  # far-from-close -> near
    return events


# --- main --------------------------------------------------------------------------


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--windows", default="20,14,8")
    ap.add_argument("--kind", choices=("high", "low", "both"), default="high")
    ap.add_argument("--city", default="ALL")
    ap.add_argument("--conv-threshold", type=float, default=60.0, dest="conv_threshold")
    ap.add_argument("--stable-cycles", type=int, default=2, dest="stable_cycles")
    ap.add_argument("--htc-min", type=float, default=1.0, dest="htc_min")
    ap.add_argument("--htc-max", type=float, default=24.0, dest="htc_max")
    ap.add_argument("--by-city", action="store_true")
    args = ap.parse_args(argv)
    windows = [float(x) for x in args.windows.split(",") if x.strip()]

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    where, params = ["close_time IS NOT NULL", "event_ticker IS NOT NULL"], []
    if args.kind != "both":
        where.append("kind = %s")
        params.append(args.kind)
    if args.city != "ALL":
        where.append("city = %s")
        params.append(args.city)
    clause = " AND ".join(where)

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT market_ticker, event_ticker, city, subtitle, result, close_time"
                f" FROM backfill_weather_markets WHERE {clause}", params)
            markets = {
                r[0]: {"event": r[1], "city": r[2], "sub": r[3], "result": r[4], "close": r[5]}
                for r in cur.fetchall()
            }
            cur.execute(
                "SELECT c.market_ticker, c.end_period_ts, c.yes_ask_close, c.yes_bid_close,"
                " c.price_close FROM backfill_weather_candles c"
                f" JOIN backfill_weather_markets m ON m.market_ticker = c.market_ticker WHERE {clause}"
                f" AND c.end_period_ts >= m.close_time - interval '{LOOKBACK_HOURS} hours'", params)
            candles = cur.fetchall()

    if not markets:
        print("No backfill markets matched.")
        return 0
    events = build_events(markets, candles)
    if not events:
        print(f"No usable events ({len(markets)} markets, {len(candles)} candles scanned).")
        return 0

    print(f"=== Backfill entry-timing study — kind={args.kind} city={args.city} ===")
    print(f"  {len(markets)} bucket-markets, {len(candles)} candles, {len(events)} events"
          f" (REAL candle asks)")
    run_report(events, windows, args, f"kind={args.kind} city={args.city}")
    if args.by_city:
        ev_city = {m["event"]: m["city"] for m in markets.values()}  # event -> city (one pass)
        for city in sorted({c for c in ev_city.values() if c}):
            sub = {ev: cyc for ev, cyc in events.items() if ev_city.get(ev) == city}
            if sub:
                run_report(sub, windows, args, f"city={city}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
