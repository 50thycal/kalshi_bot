"""Big-sample exit sweep on the BACKFILL archive — would TP/SL (or a break-even stop) have
beaten holding to settlement, replayed over hundreds of events instead of the handful of
live paper trades with recorded paths?

weather_exit_sweep answers this on the live paper trades, but bucket-snapshot paths only
exist since ladder collection began (~Jun 10), so the sample is thin. This reconstructs the
same experiment from backfill_weather_candles: it buys the favorite at a chosen entry window
(default h14, the live primary) at its yes_ask, then walks that bucket's hourly yes_bid path
to close under a (TP, SL) grid + break-even stop. Identical replay semantics to
weather_exit_sweep (exit at the first candle where bid-entry >= TP or <= -SL, sell at that
bid minus fee; otherwise settle 0/100). Hourly candles are coarser than the live 15-min
snapshots, so intrabar spikes that revert are missed — conservative-realistic for binaries.

Market-only provenance (backfill_weather_*). Self-contained (stdlib + psycopg). Usage:
    {"type":"script","name":"weather_exit_backfill","args":["--kind","high","--window","14","--by-city"]}
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

RO_OPTIONS = (
    "-c default_transaction_read_only=on -c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)
LOOKBACK_HOURS = 30


# --- replay semantics (mirror scripts/weather_exit_sweep.py) -----------------------


def fee_cents(price_cents: float) -> float:
    p = price_cents / 100.0
    return float(math.ceil(0.07 * p * (1 - p) * 100))


@dataclass
class Trade:
    city: str
    entry_cents: float
    entry_fee_cents: float
    resolved_value: int  # 100 if the favorite won, else 0
    bids: list[float]    # yes-bid path strictly after entry, time order


def replay(trade: Trade, tp, sl, be=None) -> tuple[float, str]:
    """P&L cents (per contract) and exit kind for an exit rule — identical to the live sweep."""
    def _exit(bid: float) -> float:
        return bid - trade.entry_cents - trade.entry_fee_cents - fee_cents(bid)

    armed = False
    for bid in trade.bids:
        gain = bid - trade.entry_cents
        if tp is not None and gain >= tp:
            return (_exit(bid), "tp")
        if sl is not None and gain <= -sl:
            return (_exit(bid), "sl")
        if be is not None:
            if not armed and gain >= be:
                armed = True
            elif armed and gain <= 0.0:
                return (_exit(bid), "be")
    return (trade.resolved_value - trade.entry_cents - trade.entry_fee_cents, "settle")


@dataclass
class Combo:
    tp: float | None
    sl: float | None
    be: float | None
    n: int = 0
    pnl: float = 0.0
    ntp: int = 0
    nsl: int = 0
    nbe: int = 0
    nst: int = 0

    @property
    def per(self) -> float:
        return self.pnl / self.n if self.n else math.nan


def sweep(trades: list[Trade], grid: list[tuple]) -> list[Combo]:
    out = []
    for tp, sl, be in grid:
        c = Combo(tp, sl, be)
        for t in trades:
            p, kind = replay(t, tp, sl, be)
            c.n += 1
            c.pnl += p
            c.ntp += kind == "tp"
            c.nsl += kind == "sl"
            c.nbe += kind == "be"
            c.nst += kind == "settle"
        out.append(c)
    return out


def _lvl(v) -> str:
    return "hold" if v is None else f"{v:g}c"


def _is_hold(c: Combo) -> bool:
    return c.tp is None and c.sl is None and c.be is None


def _label(c: Combo) -> str:
    s = f"tp={_lvl(c.tp)}/sl={_lvl(c.sl)}"
    return s + f"/be@{_lvl(c.be)}" if c.be is not None else s


# --- entry reconstruction from candles ---------------------------------------------


def build_trades(markets: dict, candles: list, window_h: float) -> list[Trade]:
    """Per event: buy the favorite (highest-priced bucket) at the candle nearest close-window_h,
    then collect that bucket's yes_bid path strictly after the entry time."""
    by_event_ts: dict = defaultdict(dict)   # (event, ts) -> {mt: (ask, bid, price)}
    rows_by_mt: dict = defaultdict(list)    # mt -> [(ts, bid)]
    for mt, ts, ask, bid, close in candles:
        m = markets.get(mt)
        if m is None or m["close"] is None:
            continue
        price = close if close is not None else bid
        if price is None:
            continue
        ask_c = ask if ask is not None else (close if close is not None else bid)
        bid_c = bid if bid is not None else (close if close is not None else ask)
        by_event_ts[(m["event"], ts)][mt] = (float(ask_c), float(bid_c), float(price))
        if bid_c is not None:
            rows_by_mt[mt].append((ts, float(bid_c)))
    for rows in rows_by_mt.values():
        rows.sort()

    # favorite at the entry snapshot per event
    events: dict = defaultdict(list)
    for (event, ts), legs in by_event_ts.items():
        events[event].append((ts, legs))
    trades: list[Trade] = []
    for snaps in events.values():
        m_any = markets[next(iter(snaps[0][1]))]
        close = m_any["close"]
        target = close - timedelta(hours=window_h)
        ts, legs = min(snaps, key=lambda s: abs((s[0] - target).total_seconds()))
        if abs((ts - target).total_seconds()) > 3.5 * 3600:
            continue
        fav_mt = max(legs, key=lambda mt: legs[mt][2])
        ask, _bid, _price = legs[fav_mt]
        m = markets[fav_mt]
        bids = [b for (t, b) in rows_by_mt.get(fav_mt, []) if t > ts]
        trades.append(Trade(
            city=m["city"], entry_cents=max(1.0, min(99.0, ask)),
            entry_fee_cents=fee_cents(max(1.0, min(99.0, ask))),
            resolved_value=100 if m["result"] == "yes" else 0, bids=bids,
        ))
    return trades


# --- reporting ---------------------------------------------------------------------


def report(trades: list[Trade], grid, be_grid, label: str) -> None:
    print(f"\n=== Exit sweep — {label} ({len(trades)} favorite-buys) ===")
    if not trades:
        print("  (no trades)")
        return
    results = sweep(trades, grid)
    hold = next((r for r in results if _is_hold(r)), None)
    hpt = hold.per if hold else math.nan
    print(f"  {'tp':>5} {'sl':>5} {'n':>4} {'exit_tp':>7} {'exit_sl':>7} {'settle':>6}"
          f" {'total':>9} {'per-trade':>9} {'vs hold':>8}")
    for r in sorted(results, key=lambda r: -r.per):
        print(f"  {_lvl(r.tp):>5} {_lvl(r.sl):>5} {r.n:4d} {r.ntp:7d} {r.nsl:7d} {r.nst:6d}"
              f" {r.pnl / 100.0:+8.2f}$ {r.per:+8.1f}c {r.per - hpt:+7.1f}c")
    if be_grid:
        print("  -- break-even stop (arm at +gain, exit at entry on fallback) --")
        for r in sorted(sweep(trades, be_grid), key=lambda r: -r.per):
            print(f"  be@{_lvl(r.be)} tp={_lvl(r.tp):>4} n={r.n:4d} exit_be={r.nbe:4d}"
                  f" settle={r.nst:4d} {r.per:+6.1f}c (vs hold {r.per - hpt:+.1f}c)")
    print(f"  hold-to-settlement baseline: {hpt:+.1f}c/trade")


# --- main --------------------------------------------------------------------------


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def parse_grid(spec: str) -> list:
    out = []
    for tok in spec.split(","):
        tok = tok.strip().lower()
        if tok:
            out.append(None if tok in ("none", "off") else float(tok))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kind", choices=("high", "low"), default="high")
    ap.add_argument("--window", type=float, default=14.0)
    ap.add_argument("--city", default="ALL")
    ap.add_argument("--tp-grid", default="none,10,20,30", dest="tp_grid")
    ap.add_argument("--sl-grid", default="none,10,20,30", dest="sl_grid")
    ap.add_argument("--be-grid", default="none,5,10", dest="be_grid")
    ap.add_argument("--be-tp-grid", default="none,15", dest="be_tp_grid")
    ap.add_argument("--by-city", action="store_true")
    args = ap.parse_args(argv)

    tps, sls = parse_grid(args.tp_grid), parse_grid(args.sl_grid)
    grid = [(tp, sl, None) for tp in tps for sl in sls]
    if not any(tp is None and sl is None for tp, sl, _ in grid):
        grid.insert(0, (None, None, None))
    bes = [b for b in parse_grid(args.be_grid) if b is not None]
    be_grid = [(tp, None, be) for be in bes for tp in parse_grid(args.be_tp_grid)]

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

    trades = build_trades(markets, candles, args.window)
    print(f"=== Backfill exit sweep — kind={args.kind} window=h{args.window:g} city={args.city} ===")
    print(f"  {len(markets)} markets, {len(candles)} candles -> {len(trades)} favorite-buys")
    report(trades, grid, be_grid, f"kind={args.kind} city={args.city}")
    if args.by_city:
        for city in sorted({t.city for t in trades if t.city}):
            report([t for t in trades if t.city == city], grid, be_grid, f"city={city}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
