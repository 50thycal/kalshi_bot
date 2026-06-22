"""Maker vs taker study — does ENTERING AT THE BID (providing liquidity, capturing the
spread) instead of at the ask flip the weather books from -EV to +EV?

Our directional books lose roughly the spread+fee when they TAKE liquidity (buy at the ask,
hold to settlement). This asks the market-making question's first, cleanest cut: replay the
same favorite-buy bet over the backfill, but priced as a MAKER fill at the bid (and at mid),
held to settlement. It's an UPPER BOUND on the directional-maker benefit — it assumes the
resting bid always fills and ignores adverse selection / fill probability — so if even this
isn't +EV, market-making is hopeless; if it is, the harder fill-realism modeling is worth it.

Two fee regimes are shown because Kalshi's MAKER fee treatment is the gating unknown:
  fee=full  - makers pay the same ceil(7*p*(1-p)) per contract as takers (the bot's model).
  fee=zero  - makers are fee-free (the optimistic case; common on some venues).

Market-only provenance (backfill_weather_*). Self-contained (stdlib + psycopg). Usage:
    {"type":"script","name":"weather_maker_study","args":["--kind","high","--by-city"]}
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


def fee_cents(price: float) -> float:
    p = max(0.0, min(1.0, price / 100.0))
    return float(math.ceil(7.0 * p * (1.0 - p)))


def net(win: bool, entry: float, charge_fee: bool) -> float:
    entry = max(1.0, min(99.0, entry))
    return (100.0 if win else 0.0) - entry - (fee_cents(entry) if charge_fee else 0.0)


class Acc:
    __slots__ = ("n", "wins", "ask", "bid", "taker", "mk_bid_f", "mk_bid_0", "mk_mid_f", "mk_mid_0")

    def __init__(self):
        self.n = self.wins = 0
        self.ask = self.bid = 0.0
        self.taker = self.mk_bid_f = self.mk_bid_0 = self.mk_mid_f = self.mk_mid_0 = 0.0

    def add(self, win, ask, bid):
        mid = (ask + bid) / 2.0
        self.n += 1
        self.wins += 1 if win else 0
        self.ask += ask
        self.bid += bid
        self.taker += net(win, ask, True)
        self.mk_bid_f += net(win, bid, True)
        self.mk_bid_0 += net(win, bid, False)
        self.mk_mid_f += net(win, mid, True)
        self.mk_mid_0 += net(win, mid, False)

    def row(self, label: str) -> str:
        n = self.n or 1
        return (f"  {label:>10} {self.n:5d} {self.wins / n * 100:4.0f}% "
                f"{self.bid / n:5.1f}/{self.ask / n:<4.1f} {self.ask / n - self.bid / n:4.1f} |"
                f" {self.taker / n:+6.1f} | {self.mk_bid_f / n:+7.1f} {self.mk_bid_0 / n:+7.1f} |"
                f" {self.mk_mid_f / n:+7.1f} {self.mk_mid_0 / n:+7.1f}")


def report(groups: list[tuple[str, Acc]]) -> None:
    print(f"  {'group':>10} {'n':>5} {'win%':>4} {'bid/ask':>10} {'spr':>4} | {'taker':>6} |"
          f" {'mkBid_f':>7} {'mkBid_0':>7} | {'mkMid_f':>7} {'mkMid_0':>7}")
    for label, a in groups:
        if a.n:
            print(a.row(label))
    print("  taker=buy@ask | mkBid=buy@bid | mkMid=buy@mid; _f=maker pays fee, _0=maker fee-free")
    print("  (all hold to settlement; mkBid is the spread-capture UPPER BOUND — assumes the"
          " resting bid always fills, ignores adverse selection)")


def build(markets, candles, window_h):
    by_ev_ts: dict = defaultdict(dict)
    for mt, ts, ask, bid, close in candles:
        m = markets.get(mt)
        if m is None or m["close"] is None:
            continue
        price = close if close is not None else bid
        if price is None or ask is None or bid is None:
            continue
        by_ev_ts[(m["event"], ts)][mt] = (float(ask), float(bid), float(price))
    events: dict = defaultdict(list)
    for (ev, ts), legs in by_ev_ts.items():
        events[ev].append((ts, legs))
    out = []
    for snaps in events.values():
        close = markets[next(iter(snaps[0][1]))]["close"]
        target = close - timedelta(hours=window_h)
        ts, legs = min(snaps, key=lambda s: abs((s[0] - target).total_seconds()))
        if abs((ts - target).total_seconds()) > 3.5 * 3600:
            continue
        fav = max(legs, key=lambda mt: legs[mt][2])
        ask, bid, _price = legs[fav]
        if ask < bid:
            continue
        out.append((markets[fav]["city"], markets[fav]["result"] == "yes", ask, bid))
    return out


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kind", choices=("high", "low"), default="high")
    ap.add_argument("--window", type=float, default=14.0)
    ap.add_argument("--city", default="ALL")
    ap.add_argument("--by-city", action="store_true")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    where, params = ["close_time IS NOT NULL", "event_ticker IS NOT NULL", "kind = %s"], [args.kind]
    if args.city != "ALL":
        where.append("city = %s")
        params.append(args.city)
    clause = " AND ".join(where)
    lo_h, hi_h = args.window + 3.5, args.window - 3.5
    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute("SELECT market_ticker, event_ticker, city, result, close_time"
                        f" FROM backfill_weather_markets WHERE {clause}", params)
            markets = {r[0]: {"event": r[1], "city": r[2], "result": r[3], "close": r[4]}
                       for r in cur.fetchall()}
            cur.execute(
                "SELECT c.market_ticker, c.end_period_ts, c.yes_ask_close, c.yes_bid_close,"
                " c.price_close FROM backfill_weather_candles c"
                f" JOIN backfill_weather_markets m ON m.market_ticker = c.market_ticker WHERE {clause}"
                f" AND c.end_period_ts BETWEEN m.close_time - interval '{lo_h} hours'"
                f" AND m.close_time - interval '{hi_h} hours'", params)
            candles = cur.fetchall()

    trades = build(markets, candles, args.window)
    print(f"=== Maker vs taker — kind={args.kind} window=h{args.window:g} "
          f"({len(trades)} favorite-buys) ===")
    pooled = Acc()
    by_city: dict[str, Acc] = defaultdict(Acc)
    for city, win, ask, bid in trades:
        pooled.add(win, ask, bid)
        by_city[city].add(win, ask, bid)
    groups = [("ALL", pooled)]
    if args.by_city:
        groups += [(c, by_city[c]) for c in sorted(by_city)]
    report(groups)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
