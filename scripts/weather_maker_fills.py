"""Realistic maker-fill bracket — how much of the maker edge survives ADVERSE SELECTION?

weather_maker_study showed buying the favorite at the bid (capturing the spread) is +EV in
the unconditional UPPER BOUND (assumes the resting bid always fills). The killer for real
market-making is adverse selection: a resting bid fills mainly when an informed seller hits
it — i.e. disproportionately on the days the favorite is about to LOSE — and you miss the
winners where the price runs up away from your bid.

This brackets the truth by replaying a bid rested at the favorite's entry-time best bid B,
then walking the bucket's subsequent yes-bid path to close:
  optimistic - always filled at B (= the maker_study number). Hold to settlement.
  realistic  - filled at B only if the path later trades down to <= B (a seller reaches us);
               otherwise NO fill (missed). Hold fills to settlement; misses earn 0.
Reports, per city + pooled: fill rate, win% of FILLED vs ALL (the adverse-selection gap),
net/trade over FILLED, and net per EVENT-attempted (misses = 0) — both fee regimes.

Market-only provenance (backfill_weather_*). Self-contained. Usage:
    {"type":"script","name":"weather_maker_fills","args":["--kind","high","--by-city"]}
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
LOOKBACK_HOURS = 30


def fee_cents(price: float) -> float:
    p = max(0.0, min(1.0, price / 100.0))
    return float(math.ceil(7.0 * p * (1.0 - p)))


def net(win: bool, entry: float, charge_fee: bool) -> float:
    entry = max(1.0, min(99.0, entry))
    return (100.0 if win else 0.0) - entry - (fee_cents(entry) if charge_fee else 0.0)


class Acc:
    __slots__ = ("n", "wins", "filled", "fill_wins", "opt", "pess_evt", "pess_fill", "bidsum")

    def __init__(self):
        self.n = self.wins = self.filled = self.fill_wins = 0
        self.opt = self.pess_evt = self.pess_fill = self.bidsum = 0.0

    def add(self, win, bid, did_fill):
        self.n += 1
        self.wins += 1 if win else 0
        self.bidsum += bid
        self.opt += net(win, bid, True)       # always-fill (per event)
        if did_fill:
            self.filled += 1
            self.fill_wins += 1 if win else 0
            self.pess_evt += net(win, bid, True)   # realistic, per event (miss=0)
            self.pess_fill += net(win, bid, True)  # realistic, per filled

    def row(self, label: str) -> str:
        n = self.n or 1
        fr = self.filled / n * 100
        win_all = self.wins / n * 100
        win_fill = self.fill_wins / self.filled * 100 if self.filled else float("nan")
        pess_per_fill = self.pess_fill / self.filled if self.filled else float("nan")
        return (f"  {label:>6} {self.n:5d} {win_all:4.0f}% {fr:4.0f}% {win_fill:4.0f}% |"
                f" {self.opt / n:+6.1f} | {pess_per_fill:+7.1f} {self.pess_evt / n:+7.1f}")


def report(groups) -> None:
    print(f"  {'group':>6} {'n':>5} {'win':>4} {'fill':>4} {'fwin':>4} | {'optEvt':>6} |"
          f" {'pFill':>7} {'pEvt':>7}")
    for label, a in groups:
        if a.n:
            print(a.row(label))
    print("  win=favorite win% (all) | fill=realistic fill rate | fwin=win% of FILLED (lower"
          " than win = adverse selection)")
    print("  optEvt=always-fill net/event | pFill=realistic net per FILLED trade |"
          " pEvt=realistic net per event-attempted (misses=0). All maker-pays-fee, hold to settle.")


def build(markets, candles, window_h):
    by_ev_ts: dict = defaultdict(dict)
    path: dict = defaultdict(list)  # mt -> [(ts, bid)]
    for mt, ts, ask, bid, close in candles:
        m = markets.get(mt)
        if m is None or m["close"] is None:
            continue
        price = close if close is not None else bid
        if price is None or bid is None:
            continue
        by_ev_ts[(m["event"], ts)][mt] = (float(ask) if ask is not None else float(bid),
                                          float(bid), float(price))
        path[mt].append((ts, float(bid)))
    for rows in path.values():
        rows.sort()
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
        _ask, bid, _price = legs[fav]
        m = markets[fav]
        later = [b for (t, b) in path.get(fav, []) if t > ts]
        did_fill = bool(later) and min(later) <= bid  # a seller traded down to our resting bid
        out.append((m["city"], m["result"] == "yes", bid, did_fill))
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
                f" AND c.end_period_ts >= m.close_time - interval '{LOOKBACK_HOURS} hours'", params)
            candles = cur.fetchall()

    trades = build(markets, candles, args.window)
    print(f"=== Maker realistic-fill bracket — kind={args.kind} window=h{args.window:g} "
          f"({len(trades)} events) ===")
    pooled = Acc()
    by_city: dict[str, Acc] = defaultdict(Acc)
    for city, win, bid, did_fill in trades:
        pooled.add(win, bid, did_fill)
        by_city[city].add(win, bid, did_fill)
    groups = [("ALL", pooled)] + (
        [(c, by_city[c]) for c in sorted(by_city)] if args.by_city else [])
    report(groups)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
