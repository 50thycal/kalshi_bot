"""Out-of-sample validation of the per-city calibration map (weather_calibration_map).

The map is in-sample: cells that look mispriced over Apr-Jun could be noise. This tests the
honest question -- does TRADING THE MAP generalize? It splits the backfill by date, learns
which (city, price-band) cells are mispriced on the TRAIN period only, then measures the net
cents/trade of trading exactly those cells (same side) on the held-out TEST period. No
hand-picking: the selection is mechanical, so test P&L is a real OOS read.

Selection (train): for each (city, 10c yes-price band) with >= --min-train samples, if the
actual yes-rate beats the implied price by > --margin -> BUY YES (underpriced); if it falls
short by > --margin -> BUY NO (overpriced/fade); else skip. Execution is realistic: YES at
yes_ask, NO at 100-yes_bid, both fee-charged. A buy-ALL-yes test baseline shows selection
has to beat doing nothing clever. Three named hypotheses are also tracked train-vs-test.

Market-only provenance (backfill_weather_*). Self-contained (stdlib + psycopg). Usage:
    {"type":"script","name":"weather_calibration_validate","args":["--window","14","--split","2026-06-01"]}
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict
from datetime import date, timedelta

RO_OPTIONS = (
    "-c default_transaction_read_only=on -c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)
BAND_W = 10


def fee_cents(entry: float) -> int:
    p = max(0.0, min(1.0, entry / 100.0))
    return math.ceil(7.0 * p * (1.0 - p))


def net_yes(win: bool, yes_ask: float) -> float:
    ask = max(1.0, min(99.0, yes_ask))
    return (100.0 if win else 0.0) - ask - fee_cents(ask)


def net_no(win: bool, yes_bid: float) -> float:
    cost = max(1.0, min(99.0, 100.0 - yes_bid))
    return (100.0 if not win else 0.0) - cost - fee_cents(cost)


def band_of(price: float) -> int | None:
    if price is None or price < 0 or price > 100:
        return None
    return min(int(price // BAND_W) * BAND_W, 90)


class Acc:
    __slots__ = ("n", "pnl")

    def __init__(self):
        self.n = 0
        self.pnl = 0.0

    def add(self, pnl: float):
        self.n += 1
        self.pnl += pnl

    @property
    def per(self):
        return self.pnl / self.n if self.n else 0.0


# --- data load ---------------------------------------------------------------------


def nearest_at_window(markets, candles, window_h):
    """market -> (city, target_date, yes_price, yes_ask, yes_bid, win) at ~close-window_h."""
    by_mt = defaultdict(list)
    for mt, ts, ask, bid, close in candles:
        by_mt[mt].append((ts, ask, bid, close))
    out = {}
    for mt, rows in by_mt.items():
        m = markets.get(mt)
        if m is None or m["close"] is None or m["tdate"] is None:
            continue
        target = m["close"] - timedelta(hours=window_h)
        ts, ask, bid, close = min(rows, key=lambda r: abs((r[0] - target).total_seconds()))
        price = close if close is not None else bid
        if price is None:
            continue
        yes_ask = ask if ask is not None else (close if close is not None else bid)
        yes_bid = bid if bid is not None else (close if close is not None else ask)
        out[mt] = (m["city"], m["tdate"], float(price), float(yes_ask), float(yes_bid),
                   m["result"] == "yes")
    return out


def _parse_date(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


# --- named-hypothesis helper -------------------------------------------------------


def pool_side(rows, cities, lo, hi, side):
    acc = Acc()
    for city, _td, price, yes_ask, yes_bid, win in rows:
        if city in cities and lo <= price < hi:
            acc.add(net_yes(win, yes_ask) if side == "YES" else net_no(win, yes_bid))
    return acc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kind", choices=("high", "low"), default="high")
    ap.add_argument("--window", type=float, default=14.0)
    ap.add_argument("--split", default="2026-06-01", help="test = target_date >= this")
    ap.add_argument("--margin", type=float, default=3.0, help="edge (c) over friction to act")
    ap.add_argument("--min-train", type=int, default=12, dest="min_train")
    args = ap.parse_args(argv)
    split = _parse_date(args.split)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    clause = "close_time IS NOT NULL AND kind = %s"
    params = [args.kind]
    lo_h, hi_h = args.window + 3.5, args.window - 3.5
    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute("SELECT market_ticker, city, result, close_time, target_date"
                        f" FROM backfill_weather_markets WHERE {clause}", params)
            markets = {r[0]: {"city": r[1], "result": r[2], "close": r[3], "tdate": _parse_date(r[4])}
                       for r in cur.fetchall()}
            cur.execute(
                "SELECT c.market_ticker, c.end_period_ts, c.yes_ask_close, c.yes_bid_close,"
                " c.price_close FROM backfill_weather_candles c"
                f" JOIN backfill_weather_markets m ON m.market_ticker = c.market_ticker WHERE {clause}"
                f" AND c.end_period_ts BETWEEN m.close_time - interval '{lo_h} hours'"
                f" AND m.close_time - interval '{hi_h} hours'", params)
            candles = cur.fetchall()

    obs = nearest_at_window(markets, candles, args.window)
    train = [v for v in obs.values() if v[1] and v[1] < split]
    test = [v for v in obs.values() if v[1] and v[1] >= split]
    if not train or not test:
        print(f"Need both periods; train={len(train)} test={len(test)}.")
        return 0
    tr_days = sorted({v[1] for v in train})
    te_days = sorted({v[1] for v in test})
    print(f"=== Calibration OOS validation — kind={args.kind} window=h{args.window:g}"
          f" split={split} ===")
    print(f"  train {tr_days[0]}..{tr_days[-1]} ({len(train)} buckets) | "
          f"test {te_days[0]}..{te_days[-1]} ({len(test)} buckets); margin>{args.margin:g}c,"
          f" min_train={args.min_train}")

    # learn selection on TRAIN per (city, band)
    cells = defaultdict(lambda: {"n": 0, "yes": 0, "px": 0.0})
    for city, _td, price, _a, _b, win in train:
        lo = band_of(price)
        if lo is None:
            continue
        c = cells[(city, lo)]
        c["n"] += 1
        c["yes"] += 1 if win else 0
        c["px"] += price
    selection = {}  # (city, lo) -> side
    for key, c in cells.items():
        if c["n"] < args.min_train:
            continue
        edge = c["yes"] / c["n"] * 100 - c["px"] / c["n"]
        if edge > args.margin:
            selection[key] = "YES"
        elif edge < -args.margin:
            selection[key] = "NO"

    # apply the SAME selection to test (and re-measure train for reference)
    def run(rows):
        sel, allyes = Acc(), Acc()
        per_cell = defaultdict(Acc)
        for city, _td, price, yes_ask, yes_bid, win in rows:
            allyes.add(net_yes(win, yes_ask))
            lo = band_of(price)
            side = selection.get((city, lo))
            if side:
                pnl = net_yes(win, yes_ask) if side == "YES" else net_no(win, yes_bid)
                sel.add(pnl)
                per_cell[(city, lo)].add(pnl)
        return sel, allyes, per_cell

    tr_sel, tr_all, tr_cells = run(train)
    te_sel, te_all, te_cells = run(test)

    print(f"\n  selected {len(selection)} (city,band) cells on train:")
    print(f"  {'cell':>11} {'side':>4} | {'train n':>7} {'net/trd':>8} | {'TEST n':>6} {'net/trd':>8}")
    for (city, lo), side in sorted(selection.items(), key=lambda kv: -tr_cells[kv[0]].per):
        tc, ec = tr_cells[(city, lo)], te_cells[(city, lo)]
        et = f"{ec.per:+7.1f}c" if ec.n else "   --"
        print(f"  {city:>4} {lo:3d}-{lo + BAND_W:<3d} {side:>4} | {tc.n:7d} {tc.per:+7.1f}c"
              f" | {ec.n:6d} {et}")
    print(f"\n  POOLED selected:   train {tr_sel.per:+6.1f}c (n={tr_sel.n})"
          f"   ->  TEST {te_sel.per:+6.1f}c (n={te_sel.n})")
    print(f"  baseline buy-ALL:  train {tr_all.per:+6.1f}c (n={tr_all.n})"
          f"   ->  TEST {te_all.per:+6.1f}c (n={te_all.n})")
    print("  (OOS verdict: selected TEST net should stay clearly positive & beat buy-ALL)")

    # named hypotheses train vs test (kind-appropriate)
    all_cities = {"AUS", "CHI", "DEN", "LAX", "MIA", "NYC", "PHIL"}
    if args.kind == "high":
        named = [
            ("AUS fade 40-70 (NO)", {"AUS"}, 40, 70, "NO"),
            ("AUS buy 30-40 (YES)", {"AUS"}, 30, 40, "YES"),
            ("LAX buy 50-70 (YES)", {"LAX"}, 50, 70, "YES"),
            ("LAX fade 70-90 (NO)", {"LAX"}, 70, 90, "NO"),
        ]
    else:
        named = [
            ("low cheap-fade 10-40 (NO)", all_cities, 10, 40, "NO"),
            ("PHIL buy 20-40 (YES)", {"PHIL"}, 20, 40, "YES"),
            ("NYC buy 10-20 (YES)", {"NYC"}, 10, 20, "YES"),
            ("low fav 90-100 (YES)", all_cities, 90, 100, "YES"),
        ]
    print("\n  named hypotheses (fixed, not learned):")
    print(f"  {'hypothesis':>22} | {'train n':>7} {'net/trd':>8} | {'TEST n':>6} {'net/trd':>8}")
    for label, cities, lo, hi, side in named:
        ta = pool_side(train, cities, lo, hi, side)
        ea = pool_side(test, cities, lo, hi, side)
        et = f"{ea.per:+7.1f}c" if ea.n else "   --"
        print(f"  {label:>22} | {ta.n:7d} {ta.per:+7.1f}c | {ea.n:6d} {et}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
