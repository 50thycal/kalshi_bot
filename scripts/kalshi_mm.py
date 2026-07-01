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

    # accumulators: list of (net_pnl, gross_pnl, count, yes_price_c, maker_side, category)
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
            cnt = xl._num(t.get("count")) or 1.0
            if p is None or not (0 < p < 1) or side not in ("yes", "no"):
                continue
            if side == "yes":                 # maker SOLD yes at p (short yes)
                gross, mside = p - settle, "sell"
            else:                             # maker BOUGHT yes at p (long yes)
                gross, mside = settle - p, "buy"
            net = gross - maker_fee(p)
            recs.append((net, gross, cnt, p * 100.0, mside, cat))
            trades_used += 1

    if not recs:
        print("  (no usable trades — check field names with --probe)")
        return 0
    print(f"  tapes fetched {tapes}, trades used {trades_used}\n")

    def block(title, keyfn, keys):
        print(f"  --- {title} (volume-weighted, per contract) ---")
        print(f"    {'bucket':>12} {'trades':>7} {'contracts':>9} {'gross':>8} {'net':>8}")
        from collections import defaultdict
        g = defaultdict(list)
        for net, gross, cnt, pc, mside, cat in recs:
            g[keyfn(net, gross, cnt, pc, mside, cat)].append((net, gross, cnt))
        for k in keys:
            v = g.get(k)
            if not v:
                continue
            contracts = sum(c for _n, _gr, c in v)
            gross = _wavg([(gr, c) for _n, gr, c in v])
            net = _wavg([(n, c) for n, _gr, c in v])
            print(f"    {str(k):>12} {len(v):7d} {contracts:9.0f} {gross:+8.4f} {net:+8.4f}")
        # total row
        contracts = sum(c for _n, _gr, c, _pc, _ms, _ct in recs)
        gross = _wavg([(gr, c) for _n, gr, c, _pc, _ms, _ct in recs])
        net = _wavg([(n, c) for n, _gr, c, _pc, _ms, _ct in recs])
        print(f"    {'ALL':>12} {len(recs):7d} {contracts:9.0f} {gross:+8.4f} {net:+8.4f}\n")

    block("MAKER P&L by yes-price band", lambda n, g, c, pc, ms, ct: _band(pc), _BANDS)
    block("MAKER P&L by side", lambda n, g, c, pc, ms, ct: ms, ["sell", "buy"])
    block("MAKER P&L by category", lambda n, g, c, pc, ms, ct: ct,
          ["Sports", "Crypto", "Weather", "Econ", "Politics", "Entertainment", "Other"])

    # the FLB-implied sweet spot: SELLING cheap longshots (maker sell, low yes-price)
    print("  --- MAKER-SELL of cheap longshots (sell yes at low price = the FLB-implied edge) ---")
    print(f"    {'yes_px':>10} {'trades':>7} {'contracts':>9} {'gross':>8} {'net':>8}")
    from collections import defaultdict
    ls = defaultdict(list)
    for net, gross, cnt, pc, mside, _cat in recs:
        if mside == "sell":
            ls[_band(pc)].append((net, gross, cnt))
    for band in _BANDS:
        v = ls.get(band)
        if not v:
            continue
        contracts = sum(c for _n, _g, c in v)
        gross = _wavg([(g, c) for _n, g, c in v])
        net = _wavg([(n, c) for n, _g, c in v])
        print(f"    {band[0]:2d}-{band[1]:<3d}     {len(v):7d} {contracts:9.0f} {gross:+8.4f} {net:+8.4f}")

    print("\n  maker pnl to SETTLEMENT; net = gross - 0.25x-taker maker fee (rate; real fills")
    print("  round up). +net in a band => resting liquidity there is +EV. If ALL ~0/negative,")
    print("  taker flow is informed enough that MM doesn't beat the fee either.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
