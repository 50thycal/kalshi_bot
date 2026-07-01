"""Market-making / liquidity-provision backtest on Kalshi — does RESTING orders (the maker
side) make money, or does adverse selection eat the spread?

Every executed trade has an aggressor (taker, who crossed the spread) and a passive
counterparty (maker, who was resting the bid/ask that got hit). Takers demonstrably lose the
spread+fee (see kalshi_flb); the maker is the mirror who COLLECTS it — UNLESS the taker flow is
informed, in which case the maker gets picked off (adverse selection) and loses. This is the
one thing all our taker research pointed at but never measured.

Assumption-light method (no queue/fill modeling): for settled markets, replay the real trade
tape and compute the MAKER's hold-to-settlement P&L per contract:
  - taker_side == "yes"  => maker SOLD yes at price p  => maker pnl = p - settle_value
  - taker_side == "no"   => maker BOUGHT yes at price p => maker pnl = settle_value - p
  (settle_value = 1 if market resolved yes else 0), minus the maker fee (0.25x taker, charged).
Volume-weighted, sliced by price band + category + maker side. Positive net => providing
liquidity there is +EV. Per the favorite-longshot finding, expect the edge (if any) on the
maker-SELL side of cheap longshots (selling overpriced longshots to noise buyers).

Read-only public API. Reuses kalshi_flb's liquid-series discovery + settled-market collection.
Usage: {"type":"script","name":"kalshi_mm","args":["--top-series","70","--sample","300"]}
"""

from __future__ import annotations

import argparse
import math

import kalshi_flb as flb  # discover_series, fetch_settled_for_series, category
import xvenue_leadlag as xl  # _get, _num

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"


def taker_fee(p: float) -> float:
    return math.ceil(7.0 * p * (1 - p)) / 100.0


def maker_fee(p: float) -> float:
    """Kalshi maker fee = 0.25x taker, CHARGED (not rebated). Rate form; real fills round up."""
    return 1.75 * p * (1 - p) / 100.0


def maker_fee_ceil(p: float) -> float:
    """Worst-case realistic maker fee: rounded UP to the whole cent per contract (as Kalshi
    bills). Kills thin longshot edges; the honest lower bound on MM profitability."""
    return math.ceil(1.75 * p * (1 - p)) / 100.0


def fetch_trades(ticker: str, cap: int) -> list[dict]:
    """Trade tape for a market (paginated), newest first, capped. Kalshi's endpoint is the
    top-level /markets/trades with ticker as a QUERY param (not /markets/{ticker}/trades)."""
    out: list[dict] = []
    cursor = ""
    for _ in range(cap // 1000 + 2):
        page = xl._get(f"{KALSHI}/markets/trades?ticker={ticker}&limit=1000&cursor={cursor}")
        trs = (page or {}).get("trades") or []
        out.extend(trs)
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not trs or len(out) >= cap:
            break
    return out[:cap]


def trade_yes_price(t: dict):
    """Yes price in dollars (0..1) from a trade, tolerating cents vs _dollars field names."""
    for k in ("yes_price_dollars", "yes_price"):
        v = t.get(k)
        if v is not None:
            v = xl._num(v)
            return v if k.endswith("dollars") else v / 100.0
    return None


_BANDS = [(0, 3), (3, 5), (5, 10), (10, 20), (20, 35), (35, 50),
          (50, 65), (65, 80), (80, 90), (90, 95), (95, 97), (97, 100)]


def _band(p_c: float):
    for lo, hi in _BANDS:
        if lo <= p_c < hi:
            return (lo, hi)
    return (97, 100) if p_c >= 97 else (0, 3)


def _wavg(pairs):
    """volume-weighted average of (value, weight)."""
    tw = sum(w for _v, w in pairs)
    return (sum(v * w for v, w in pairs) / tw) if tw else 0.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top-series", type=int, default=70)
    ap.add_argument("--per-series", type=int, default=120)
    ap.add_argument("--sample", type=int, default=300, help="settled markets to pull tapes for")
    ap.add_argument("--per-market-trades", type=int, default=500)
    ap.add_argument("--min-vol", type=float, default=50.0)
    ap.add_argument("--max-pages", type=int, default=25)
    ap.add_argument("--no-sports", action="store_true")
    ap.add_argument("--probe", action="store_true", help="dump raw trade fields for one market")
    args = ap.parse_args(argv)

    series_list = flb.discover_series(args.top_series, args.max_pages)
    settled: list[dict] = []
    for s in series_list:
        if args.no_sports and flb.category(s) == "Sports":
            continue
        settled.extend(flb.fetch_settled_for_series(s, args.per_series, args.min_vol))
    if not settled:
        print("  (no settled markets collected)")
        return 0

    if args.probe:
        for m in settled[:4]:
            tk = m["ticker"]
            a = (xl._get(f"{KALSHI}/markets/trades?ticker={tk}&limit=5") or {}).get("trades") or []
            b = (xl._get(f"{KALSHI}/markets/{tk}/trades?limit=5") or {}).get("trades") or []
            print(f"{tk} result={m['result']} vol={m['vol']:.0f}: "
                  f"/markets/trades?ticker={len(a)}  /markets/{{tk}}/trades={len(b)}")
            if a:
                print(f"  KEYS: {sorted(a[0].keys())}")
                for t in a[:3]:
                    print(f"    {t}")
                break
        return 0

    step = max(1, len(settled) // args.sample)
    sample = settled[::step][:args.sample]
    print(f"=== Kalshi market-making backtest — {len(series_list)} liquid series, "
          f"{len(settled)} settled markets, tapes for {len(sample)} ===")

    # accumulators: rec = (gross, net_rate, net_ceil, count, yes_price_c, maker_side, cat, ticker)
    recs = []
    tapes = trades_used = 0
    for m in sample:
        trs = fetch_trades(m["ticker"], args.per_market_trades)
        if not trs:
            continue
        tapes += 1
        settle = 1.0 if m["result"] == "yes" else 0.0
        cat = flb.category(m["series"])
        for t in trs:
            p = trade_yes_price(t)
            side = (t.get("taker_side") or "").lower()
            cnt = xl._num(t.get("count_fp")) or 1.0
            if p is None or not (0 < p < 1) or side not in ("yes", "no"):
                continue
            if side == "yes":                 # maker SOLD yes at p (short yes)
                gross, mside = p - settle, "sell"
            else:                             # maker BOUGHT yes at p (long yes)
                gross, mside = settle - p, "buy"
            recs.append((gross, gross - maker_fee(p), gross - maker_fee_ceil(p),
                         cnt, p * 100.0, mside, cat, m["ticker"]))
            trades_used += 1

    if not recs:
        print("  (no usable trades — check field names with --probe)")
        return 0
    print(f"  tapes fetched {tapes}, trades used {trades_used}")
    print("  net_rate = 0.25x-taker maker fee (unrounded); net_ceil = fee rounded up to 1c/"
          "contract (realistic worst case). A band is only believable if BOTH are +.\n")

    from collections import defaultdict

    def _summ(v):
        contracts = sum(r[3] for r in v)
        mkts = len({r[7] for r in v})
        return (len(v), mkts, contracts,
                _wavg([(r[0], r[3]) for r in v]),    # gross
                _wavg([(r[1], r[3]) for r in v]),    # net_rate
                _wavg([(r[2], r[3]) for r in v]))    # net_ceil

    def block(title, keyfn, keys, rows=None):
        rows = recs if rows is None else rows
        print(f"  --- {title} (volume-weighted, per contract) ---")
        print(f"    {'bucket':>12} {'trades':>7} {'mkts':>5} {'contracts':>10} "
              f"{'gross':>8} {'net_rate':>9} {'net_ceil':>9}")
        g = defaultdict(list)
        for r in rows:
            g[keyfn(r)].append(r)
        for k in keys:
            v = g.get(k)
            if not v:
                continue
            n, mkts, c, gr, nr, nc = _summ(v)
            print(f"    {str(k):>12} {n:7d} {mkts:5d} {c:10.0f} {gr:+8.4f} {nr:+9.4f} {nc:+9.4f}")
        n, mkts, c, gr, nr, nc = _summ(rows)
        print(f"    {'ALL':>12} {n:7d} {mkts:5d} {c:10.0f} {gr:+8.4f} {nr:+9.4f} {nc:+9.4f}\n")

    block("MAKER P&L by yes-price band", lambda r: _band(r[4]), _BANDS)
    block("MAKER P&L by side", lambda r: r[5], ["sell", "buy"])
    block("MAKER P&L by category", lambda r: r[6],
          ["Sports", "Crypto", "Weather", "Econ", "Politics", "Entertainment", "Other"])

    sell = [r for r in recs if r[5] == "sell"]
    block("MAKER-SELL by yes-price (rest an ASK; the FLB-implied edge)", lambda r: _band(r[4]),
          _BANDS, rows=sell)
    nonsport_sell = [r for r in sell if r[6] != "Sports"]
    if nonsport_sell:
        block("MAKER-SELL by yes-price, NON-SPORTS ONLY (does the edge survive off sports?)",
              lambda r: _band(r[4]), _BANDS, rows=nonsport_sell)

    # split-half OOS on the headline maker-sell 10-65c pocket (both halves must be +)
    pocket = [r for r in sell if 10 <= r[4] < 65]
    if pocket:
        h1, h2 = pocket[::2], pocket[1::2]
        print("  --- SPLIT-HALF OOS: maker-SELL yes 10-65c pocket (net_ceil) ---")
        for name, hh in (("half-A", h1), ("half-B", h2)):
            n, mkts, c, _gr, _nr, nc = _summ(hh)
            print(f"    {name}: trades={n} mkts={mkts} contracts={c:.0f} net_ceil={nc:+.4f}")
        print()

    print("  maker pnl to SETTLEMENT. +net_ceil in a band => resting liquidity there beats even")
    print("  the worst-case fee. Watch for sports-concentration + few-market drivers (mkts col).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
