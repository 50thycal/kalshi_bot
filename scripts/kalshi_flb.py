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


def discover_series(top_n: int, max_pages: int) -> list[str]:
    """Liquid series tickers, ranked by open-event volume (skips KXMVE parlays). We query each
    series' SETTLED history — this guarantees clean, liquid single binaries across categories,
    unlike the global settled feed which is ~all parlays + thin markets near 'now'."""
    vol_by_series: dict[str, float] = {}
    cursor = ""
    for _ in range(max_pages):
        page = xl._get(f"{KALSHI}/events?status=open&with_nested_markets=true&limit=200&cursor={cursor}")
        evs = (page or {}).get("events") or []
        for e in evs:
            s = (e.get("series_ticker") or "").upper()
            if not s or s.startswith("KXMVE"):
                continue
            v = sum(xl._num(m.get("volume_fp")) for m in e.get("markets") or [])
            vol_by_series[s] = vol_by_series.get(s, 0.0) + v
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not evs:
            break
    return sorted(vol_by_series, key=lambda s: -vol_by_series[s])[:top_n]


def fetch_settled_for_series(series: str, per_series: int, min_vol: float) -> list[dict]:
    """Settled single binaries for one series (newest first), with yes/no result + close."""
    out: list[dict] = []
    cursor = ""
    for _ in range(3):
        page = xl._get(f"{KALSHI}/markets?series_ticker={series}&status=settled"
                       f"&limit=1000&cursor={cursor}")
        mkts = (page or {}).get("markets") or []
        for m in mkts:
            res = (m.get("result") or "").lower()
            tk = m.get("ticker") or ""
            if res not in ("yes", "no") or tk.startswith("KXMVE"):
                continue
            vol = xl._num(m.get("volume_fp"))
            if vol < min_vol:
                continue
            close = _close_unix(m.get("close_time"))
            if not close:
                continue
            out.append({"ticker": tk, "series": series, "close": close, "result": res, "vol": vol})
            if len(out) >= per_series:
                return out
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not mkts:
            break
    return out


def fetch_candles(series: str, ticker: str, start: int, end: int) -> dict[int, tuple]:
    """{minute: (yes_bid, yes_ask)} over [start,end] (chunked 1-min candles)."""
    out: dict[int, tuple] = {}
    s = start
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
            out[int(ts) // 60] = (xl._num(yb), xl._num(ya))
        s = e
    return out


def pick_at(candles: dict[int, tuple], close: int, horizon_min: int, tol_min: int = 30):
    """Nearest (yb,ya) at-or-before `horizon_min` mins pre-close, within tol. No lookahead."""
    target = close // 60 - horizon_min
    best = None
    for m, (yb, ya) in candles.items():
        if m > target:                  # strictly no lookahead past the horizon
            continue
        d = target - m
        if d <= tol_min and (best is None or d < best[0]):
            best = (d, yb, ya)
    return (best[1], best[2]) if best else None


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
    ap.add_argument("--horizons", default="30,120,360",
                    help="comma minutes-before-close to price (tests if fav edge survives lead time)")
    ap.add_argument("--min-vol", type=float, default=50.0, help="skip thin markets (volume_fp)")
    ap.add_argument("--top-series", type=int, default=40, help="liquid series to sample from")
    ap.add_argument("--per-series", type=int, default=120, help="settled markets per series")
    ap.add_argument("--max-pages", type=int, default=25, help="event pages for series discovery")
    ap.add_argument("--no-sports", action="store_true", help="drop Sports category")
    ap.add_argument("--focus-horizon", type=int, default=120, help="horizon for the focus deep-dive")
    ap.add_argument("--focus-band", default="65,80", help="fav price band to interrogate (lo,hi)")
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

    series_list = discover_series(args.top_series, args.max_pages)
    settled: list[dict] = []
    for s in series_list:
        if args.no_sports and category(s) == "Sports":
            continue
        settled.extend(fetch_settled_for_series(s, args.per_series, args.min_vol))
        if len(settled) >= args.max:
            break
    horizons = [int(x) for x in args.horizons.split(",") if x.strip()]
    max_h = max(horizons)
    print(f"=== Kalshi favorite-longshot backtest — {len(series_list)} liquid series, "
          f"{len(settled)} settled markets collected ===")
    step = max(1, len(settled) // args.sample)
    sample = settled[::step][:args.sample]
    print(f"  pricing {len(sample)} markets at horizons {horizons} min-before-close "
          f"(one candle fetch each)\n")

    from collections import defaultdict
    # rows_by_h[h] = list of (mid_c, pnl, result_yes, category)
    rows_by_h: dict[int, list] = {h: [] for h in horizons}
    fetched = 0
    for m in sample:
        candles = fetch_candles(m["series"], m["ticker"],
                                m["close"] - (max_h + 45) * 60, m["close"] + 60)
        if not candles:
            continue
        fetched += 1
        cat = category(m["series"])
        for h in horizons:
            px = pick_at(candles, m["close"], h)
            if px is None:
                continue
            r = fav_trade_pnl(px[0], px[1], m["result"])
            if r is None:
                continue
            mid, pnl = r
            rows_by_h[h].append((mid * 100.0, pnl, m["result"] == "yes", cat))

    print(f"  fetched candles for {fetched} markets\n")
    fav_bands = [(50, 65), (65, 80), (80, 90), (90, 95), (95, 100)]

    for h in horizons:
        rows = rows_by_h[h]
        if not rows:
            print(f"  [T-{h}min] no priceable markets\n")
            continue
        print(f"  ===================== HORIZON T-{h} min  (n={len(rows)}) =====================")
        # calibration
        print("  CALIBRATION (gap = yes_rate - price; <0 longshot overpriced, >0 fav underpriced)")
        by_band = defaultdict(list)
        for mid_c, _pnl, yes, _cat in rows:
            by_band[_band(mid_c)].append((mid_c, yes))
        print(f"    {'band(c)':>8} {'n':>5} {'avg_px':>7} {'yes_rate':>8} {'gap':>7}")
        for band in _BANDS:
            v = by_band.get(band)
            if not v:
                continue
            n = len(v)
            avg_px = sum(x[0] for x in v) / n
            yr = 100.0 * sum(1 for x in v if x[1]) / n
            print(f"    {band[0]:2d}-{band[1]:<3d}  {n:5d} {avg_px:6.1f}c {yr:7.1f}% {yr - avg_px:+6.1f}")
        # back-the-favorite P&L by fav price band
        print("  BACK-THE-FAVORITE P&L/contract (net entry fee), by fav price:")
        fb = defaultdict(list)
        for mid_c, pnl, _yes, _cat in rows:
            favpx = mid_c if mid_c >= 50 else 100 - mid_c
            for lo, hi in fav_bands:
                if lo <= favpx < hi:
                    fb[(lo, hi)].append(pnl)
                    break
        print(f"    {'fav_px':>8} {'n':>5} {'win%':>6} {'pnl/trade':>10} {'total$':>8}")
        for band in fav_bands:
            v = fb.get(band)
            if not v:
                continue
            win = 100.0 * sum(1 for p in v if p > 0) / len(v)
            print(f"    {band[0]:2d}-{band[1]:<3d}  {len(v):5d} {win:5.1f}% "
                  f"{sum(v) / len(v):+9.3f} {sum(v):+8.2f}")
        allpnl = [p for _mc, p, _y, _c in rows]
        heavy = [p for mc, p, _y, _c in rows if (mc if mc >= 50 else 100 - mc) >= 80]
        print(f"    {'ALL':>8} {len(allpnl):5d} {'':6} {sum(allpnl) / len(allpnl):+9.3f} "
              f"{sum(allpnl):+8.2f}")
        if heavy:
            print(f"    heavy-fav(>=80c) n={len(heavy)} pnl/trade={sum(heavy) / len(heavy):+.3f} "
                  f"total={sum(heavy):+.2f}")
        print()

    # ---- FOCUS: interrogate the one horizon-robust pocket (fav 65-80c) ----
    fh = args.focus_horizon
    frows = rows_by_h.get(fh) or next((r for r in rows_by_h.values() if r), [])
    flo, fhi = (int(x) for x in args.focus_band.split(","))
    if frows:
        print(f"  ===================== FOCUS @ T-{fh}min: is fav {flo}-{fhi}c a REAL pocket? "
              f"=====================")
        # fine 5c sub-bands across the favorite side (monotone? or one lucky bin?)
        print("  fav P&L in 5c sub-bands (favpx = the >=50c side; net fee):")
        print(f"    {'sub':>8} {'n':>5} {'win%':>6} {'gap_pp':>7} {'pnl/trade':>10}")
        for lo in range(50, 100, 5):
            hi = lo + 5
            v = [(p, mc, yes) for mc, p, yes, _c in frows
                 if lo <= (mc if mc >= 50 else 100 - mc) < hi]
            if not v:
                continue
            pnls = [x[0] for x in v]
            # calibration gap on the FAVORITE side: fav-win-rate - avg fav price
            favwin = 100.0 * sum(1 for p, mc, yes in v
                                 if (yes if mc >= 50 else not yes)) / len(v)
            favpx = sum((mc if mc >= 50 else 100 - mc) for _p, mc, _y in v) / len(v)
            win = 100.0 * sum(1 for p in pnls if p > 0) / len(pnls)
            print(f"    {lo:2d}-{hi:<3d}  {len(pnls):5d} {win:5.1f}% {favwin - favpx:+6.1f} "
                  f"{sum(pnls) / len(pnls):+9.3f}")
        # the focus band: by category (sports-only or broad?) + split-half OOS
        band = [(mc, p, yes, c) for mc, p, yes, c in frows
                if flo <= (mc if mc >= 50 else 100 - mc) < fhi]
        print(f"\n  fav {flo}-{fhi}c by CATEGORY (is the pocket sports-only?):")
        print(f"    {'category':>14} {'n':>5} {'win%':>6} {'pnl/trade':>10} {'total$':>8}")
        bc = defaultdict(list)
        for _mc, p, _y, c in band:
            bc[c].append(p)
        for c in sorted(bc, key=lambda c: -sum(bc[c]) / len(bc[c])):
            v = bc[c]
            win = 100.0 * sum(1 for p in v if p > 0) / len(v)
            print(f"    {c:>14} {len(v):5d} {win:5.1f}% {sum(v) / len(v):+9.3f} {sum(v):+8.2f}")
        # split-half out-of-sample (guard vs small-n mirage — we've been fooled before)
        halfp = [p for _mc, p, _y, _c in band]
        h1, h2 = halfp[::2], halfp[1::2]
        print("\n  SPLIT-HALF OOS (both halves must be +EV for a believable edge):")
        for name, hh in (("half-A", h1), ("half-B", h2)):
            if hh:
                print(f"    {name}: n={len(hh)} pnl/trade={sum(hh) / len(hh):+.3f} "
                      f"total={sum(hh):+.2f}")
        print()

    print("  pnl = payoff(0/1) - entry_ask - ceil(7p(1-p))c fee; settlement has no fee.")
    print("  KEY: a fav edge that HOLDS at long horizons is tradeable; one that appears only")
    print("  near close (T-30) but not at T-360 is just 'already-decided favorites win' — not an edge.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
