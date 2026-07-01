"""Favorite-longshot bias backtest on Kalshi SETTLED binary markets — is there a taker edge?

Research fact: across most prediction/betting markets, cheap longshots are systematically
OVERPRICED (settle YES less often than their price implies) and heavy favorites are
UNDERPRICED. If Kalshi shows the same, a taker could profit by BACKING THE FAVORITE (buying
the >50% side) and/or FADING THE LONGSHOT (buying NO on the cheap side) — held to settlement,
one venue, no forecasting. The open question is only whether the bias exceeds Kalshi's fee.

Method (all from public data, no auth):
  1. Pull settled markets newest-first; keep result in {yes,no} with a close time + series.
  2. For each (sampled to bound API cost) pull 1-min candlesticks and read the REAL yes_bid/
     yes_ask at a fixed horizon before close (default 60 min) — the price a taker faces.
  3. Bin by that entry price. Per band report n, realized YES-settle rate, and the CALIBRATION
     gap (realized - price): FLB predicts gap<0 for longshots, gap>0 for favorites.
  4. Simulate the taker strategy "buy the favorite side at its ask, hold to settlement" per
     band and per category, net of the ceil(7*p*(1-p)) entry fee. Per-trade P&L is the verdict.

Usage: {"type":"script","name":"kalshi_flb","args":["--max","2000","--sample","800","--horizon","60"]}
"""

from __future__ import annotations

import argparse
import calendar
import math
import time

import xvenue_leadlag as xl  # _get (browser-UA fetch), _num

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

# Coarse category from a series ticker so we can see WHERE any bias lives (sports vs not).
_CAT = [
    ("Sports", ("KXNFL", "KXNBA", "KXMLB", "KXNHL", "KXWC", "KXUCL", "KXEPL", "KXLALIGA",
                "KXSERIEA", "KXBUNDES", "KXLIGUE", "KXMLS", "KXGAME", "KXNCAA", "KXUFC",
                "KXTENNIS", "KXATP", "KXWTA", "KXF1", "KXPGA", "KXALLSVENSKAN", "KXCANPL",
                "KXBOX", "KXCRICKET", "KXGOLF", "KXSOCCER")),
    ("Econ", ("KXCPI", "KXPAYROLLS", "KXGDP", "KXFED", "KXRETAIL", "KXTRADEBAL", "KXPCE",
              "KXUNRATE", "KXJOBLESS", "KXPPI", "KXRATE", "KXISM")),
    ("Crypto", ("KXBTC", "KXETH", "KXSOL", "KXDOGE", "KXXRP", "KXCRYPTO")),
    ("Weather", ("KXHIGH", "KXLOW", "KXTEMP", "KXRAIN", "KXSNOW")),
    ("Entertainment", ("KXNETFLIX", "KXROTTEN", "KXOSCAR", "KXEMMY", "KXGRAMMY", "KXBOX",
                       "KXALBUM", "KXMOVIE", "KXSPOTIFY", "KXBILLBOARD", "KXART")),
    ("Politics", ("KXPRES", "KXSENATE", "KXHOUSE", "KXPOTUS", "KXGOV", "KXPARDON", "KXTRUMP",
                  "KXELECTION", "KXAPPROVAL", "KXPOLL")),
]


def category(series: str) -> str:
    for name, prefixes in _CAT:
        if any(series.startswith(p) for p in prefixes):
            return name
    return "Other"


def fee(p: float) -> float:
    """Kalshi taker fee (dollars) per contract at price p (0..1)."""
    return math.ceil(7.0 * p * (1 - p)) / 100.0


def _close_unix(iso: str) -> int:
    try:
        return calendar.timegm(time.strptime((iso or "")[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return 0


def fetch_settled(max_markets: int, min_vol: float, status: str, max_pages: int) -> list[dict]:
    """Settled binary markets newest-first with a yes/no result + parseable close + series.
    Skips KXMVE parlays (which dominate the recent feed and aren't clean single binaries)."""
    out: list[dict] = []
    cursor = ""
    for _ in range(max_pages):
        page = xl._get(f"{KALSHI}/markets?status={status}&limit=1000&cursor={cursor}")
        mkts = (page or {}).get("markets") or []
        for m in mkts:
            res = (m.get("result") or "").lower()
            if res not in ("yes", "no"):
                continue
            tk = m.get("ticker") or ""
            if tk.startswith("KXMVE"):                 # multi-leg parlay noise
                continue
            vol = xl._num(m.get("volume_fp"))
            if vol < min_vol:
                continue
            close = _close_unix(m.get("close_time"))
            if not close:
                continue
            series = m.get("series_ticker") or tk.split("-")[0]
            out.append({"ticker": tk, "series": series, "close": close, "result": res, "vol": vol})
            if len(out) >= max_markets:
                return out
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not mkts:
            break
    return out


def price_at_horizon(series: str, ticker: str, close: int, horizon_min: int):
    """Return (yes_bid, yes_ask) in dollars nearest to `horizon_min` before close, or None."""
    target = close // 60 - horizon_min
    start, end = close - 3 * 3600, close + 60
    s = start
    best = None
    while s < end:
        e = min(s + 4800 * 60, end)
        data = xl._get(f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks"
                       f"?start_ts={s}&end_ts={e}&period_interval=1")
        for c in (data or {}).get("candlesticks") or []:
            ts = c.get("end_period_ts")
            yb = (c.get("yes_bid") or {}).get("close_dollars")
            ya = (c.get("yes_ask") or {}).get("close_dollars")
            if ts is None or yb is None or ya is None:
                continue
            m = int(ts) // 60
            if m > target + 1:          # only look at/ before the horizon (no lookahead)
                continue
            d = abs(m - target)
            if best is None or d < best[0]:
                best = (d, xl._num(yb), xl._num(ya))
        s = e
    if best is None:
        return None
    return best[1], best[2]


# entry-price bands (cents) for the calibration / P&L table
_BANDS = [(0, 3), (3, 5), (5, 10), (10, 20), (20, 35), (35, 50),
          (50, 65), (65, 80), (80, 90), (90, 95), (95, 97), (97, 100)]


def _band(mid_c: float):
    for lo, hi in _BANDS:
        if lo <= mid_c < hi:
            return (lo, hi)
    return (95, 100) if mid_c >= 95 else (0, 3)


def fav_trade_pnl(yb: float, ya: float, result: str):
    """Buy the favorite side (>50%) at its ask, hold to settlement. Returns (mid, pnl$) or None."""
    mid = (yb + ya) / 2.0
    if not (0 < yb <= ya < 1):
        return None
    if mid >= 0.5:                       # favorite = YES, pay yes_ask
        entry, win = ya, (result == "yes")
    else:                                # favorite = NO, pay no_ask = 1 - yes_bid
        entry, win = 1.0 - yb, (result == "no")
    if not (0 < entry < 1):
        return None
    pnl = (1.0 if win else 0.0) - entry - fee(entry)
    return mid, pnl


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max", type=int, default=2000, help="settled markets to collect")
    ap.add_argument("--sample", type=int, default=800, help="markets to pull candles for")
    ap.add_argument("--horizon", type=int, default=60, help="minutes before close to price")
    ap.add_argument("--min-vol", type=float, default=50.0, help="skip thin markets (volume_fp)")
    ap.add_argument("--max-pages", type=int, default=25, help="settled-feed pages to scan")
    ap.add_argument("--no-sports", action="store_true", help="drop Sports category")
    ap.add_argument("--status", default="settled", help="market status filter")
    ap.add_argument("--probe", action="store_true", help="dump raw fields of a few markets")
    args = ap.parse_args(argv)

    if args.probe:
        for st in ("settled", "finalized", "closed"):
            page = xl._get(f"{KALSHI}/markets?status={st}&limit=5")
            mkts = (page or {}).get("markets") or []
            print(f"status={st!r}: {len(mkts)} markets; cursor={bool((page or {}).get('cursor'))}")
            for m in mkts[:3]:
                print(f"  ticker={m.get('ticker')} series={m.get('series_ticker')} "
                      f"result={m.get('result')!r} status={m.get('status')!r} "
                      f"close={m.get('close_time')} vol={m.get('volume')} vol_fp={m.get('volume_fp')}")
            if mkts:
                print(f"  KEYS: {sorted(mkts[0].keys())}")
        return 0

    settled = fetch_settled(args.max, args.min_vol, args.status, args.max_pages)
    if args.no_sports:
        settled = [m for m in settled if category(m["series"]) != "Sports"]
    # sample evenly across the collected set so we aren't biased to the newest slice
    step = max(1, len(settled) // args.sample)
    sample = settled[::step][:args.sample]
    print(f"=== Kalshi favorite-longshot backtest — {len(settled)} settled markets, "
          f"pricing {len(sample)} at T-{args.horizon}min ===\n")

    rows = []       # (mid_c, pnl, result_yes, category)
    priced = 0
    for m in sample:
        px = price_at_horizon(m["series"], m["ticker"], m["close"], args.horizon)
        if px is None:
            continue
        yb, ya = px
        r = fav_trade_pnl(yb, ya, m["result"])
        if r is None:
            continue
        mid, pnl = r
        priced += 1
        rows.append((mid * 100.0, pnl, m["result"] == "yes", category(m["series"])))

    if not rows:
        print("  (no priceable markets — candlestick coverage empty)")
        return 0

    # ---- calibration curve: realized YES rate vs entry price ----
    print(f"  priced {priced} markets\n")
    print("  --- CALIBRATION: does entry price predict settle rate? (gap<0 longshot overpriced) ---")
    print(f"  {'band(c)':>9} {'n':>5} {'avg_px':>7} {'yes_rate':>8} {'gap':>7}")
    from collections import defaultdict
    by_band = defaultdict(list)
    for mid_c, _pnl, yes, _cat in rows:
        by_band[_band(mid_c)].append((mid_c, yes))
    for band in _BANDS:
        v = by_band.get(band)
        if not v:
            continue
        n = len(v)
        avg_px = sum(x[0] for x in v) / n
        yr = 100.0 * sum(1 for x in v if x[1]) / n
        print(f"  {band[0]:2d}-{band[1]:<3d}   {n:5d} {avg_px:6.1f}c {yr:7.1f}% {yr - avg_px:+6.1f}")

    # ---- taker P&L of backing the favorite, by entry band ----
    print("\n  --- BACK-THE-FAVORITE taker P&L per contract (net of entry fee), by fav price ---")
    print(f"  {'fav_px(c)':>9} {'n':>5} {'win%':>6} {'pnl/trade':>10} {'total$':>8}")
    fav_bands = [(50, 65), (65, 80), (80, 90), (90, 95), (95, 97), (97, 100)]
    fb = defaultdict(list)
    for mid_c, pnl, _yes, _cat in rows:
        favpx = mid_c if mid_c >= 50 else 100 - mid_c
        for lo, hi in fav_bands:
            if lo <= favpx < hi:
                fb[(lo, hi)].append(pnl)
                break
    allpnl = [pnl for _mc, pnl, _y, _c in rows]
    for band in fav_bands:
        v = fb.get(band)
        if not v:
            continue
        n = len(v)
        win = 100.0 * sum(1 for p in v if p > 0) / n
        print(f"  {band[0]:2d}-{band[1]:<3d}   {n:5d} {win:5.1f}% {sum(v) / n:+9.3f} {sum(v):+8.2f}")
    print(f"  {'ALL':>9} {len(allpnl):5d} {'':6} {sum(allpnl) / len(allpnl):+9.3f} "
          f"{sum(allpnl):+8.2f}")

    # ---- by category ----
    print("\n  --- BACK-THE-FAVORITE P&L by category (per-trade is the verdict) ---")
    print(f"  {'category':>14} {'n':>5} {'win%':>6} {'pnl/trade':>10} {'total$':>8}")
    bc = defaultdict(list)
    for _mc, pnl, _yes, cat in rows:
        bc[cat].append(pnl)
    for cat in sorted(bc, key=lambda c: -sum(bc[c]) / len(bc[c])):
        v = bc[cat]
        win = 100.0 * sum(1 for p in v if p > 0) / len(v)
        print(f"  {cat:>14} {len(v):5d} {win:5.1f}% {sum(v) / len(v):+9.3f} {sum(v):+8.2f}")

    print("\n  pnl = payoff(0/1) - entry_ask - ceil(7p(1-p))c fee; settlement has no fee.")
    print("  +per-trade in a band => a real taker edge there; ~ -fee everywhere => efficient.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
