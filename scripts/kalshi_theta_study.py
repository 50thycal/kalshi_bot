"""THETA thesis validation probe (docs/THETA_THESIS.md) — read-only, public APIs, stdlib.

Tests the pre-registered predictions on SETTLED hourly crypto ladder markets:
  P1  calibration: are 3-35c tails overpriced (win% < implied) at tradeable horizons?
  P2  tape: is the maker-SELL side +EV net of worst-case fees on these series,
      sliced by minutes-to-expiry, split-half OOS by event date?
  P3  model: does a trailing spot-vol model (Coinbase 1-min candles, empirical
      remaining-window return distribution) separate dead tails from live ones
      better than price alone?
  P4  robustness: do P1/P2 hold per asset and per date-half?

Data: Kalshi public market data (settled markets + 1-min market candlesticks + trade
tape; browser UA clears Cloudflare) and Coinbase Exchange public candles (US-OK, no
auth) as the spot proxy for BRRNY. No lookahead anywhere: market prices are taken
at-or-before the horizon; the model distribution uses strictly-prior spot data.

Usage (ops channel):
  {"type":"script","name":"kalshi_theta_study","args":["--pages","12","--min-vol","200"]}
"""

from __future__ import annotations

import argparse
import calendar
import json
import math
import time
import urllib.request
from collections import defaultdict

import xvenue_leadlag as xl  # _get (browser UA + retries), _num

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
COINBASE = "https://api.exchange.coinbase.com"

# (series -> coinbase product). Wrong guesses fail soft (discovery prints 0 found).
DEFAULT_SERIES = "KXBTCD:BTC-USD,KXBTC:BTC-USD,KXETHD:ETH-USD,KXETH:ETH-USD"

BANDS = [(1, 3), (3, 5), (5, 10), (10, 20), (20, 35), (35, 50), (50, 65),
         (65, 80), (80, 90), (90, 95), (95, 99)]
SELL_LO, SELL_HI = 3, 40          # the thesis band (cents, yes-price)
TTE_SLICES = [(0, 5), (5, 15), (15, 30), (30, 60), (60, 100000)]


def band(c: float):
    for lo, hi in BANDS:
        if lo <= c < hi:
            return (lo, hi)
    return (1, 3) if c < 3 else (95, 99)


def taker_fee_c(p_c: float) -> float:
    return math.ceil(7.0 * (p_c / 100.0) * (1 - p_c / 100.0)) # cents/contract

def maker_fee_ceil_c(p_c: float) -> float:
    """Worst-case maker fee (25% of taker, billed rounded UP to the cent), cents."""
    return math.ceil(1.75 * (p_c / 100.0) * (1 - p_c / 100.0))


def _iso_to_unix(iso: str) -> int:
    try:
        return calendar.timegm(time.strptime((iso or "")[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return 0


def fetch_settled(series: str, pages: int, min_vol: float) -> list[dict]:
    """Settled binaries for a series, newest first, keeping strike + close fields."""
    out: list[dict] = []
    cursor = ""
    for _ in range(pages):
        page = xl._get(f"{KALSHI}/markets?series_ticker={series}&status=settled"
                       f"&limit=1000&cursor={cursor}")
        mkts = (page or {}).get("markets") or []
        for m in mkts:
            res = (m.get("result") or "").lower()
            if res not in ("yes", "no"):
                continue
            vol = xl._num(m.get("volume_fp")) or xl._num(m.get("volume"))
            if vol < min_vol:
                continue
            close = _iso_to_unix(m.get("close_time"))
            if not close:
                continue
            tk = m.get("ticker") or ""
            out.append({
                "ticker": tk, "series": series, "close": close, "result": res,
                "vol": vol, "event": tk.rsplit("-", 1)[0],
                "strike_type": (m.get("strike_type") or "").lower(),
                "floor": m.get("floor_strike"), "cap": m.get("cap_strike"),
                "sub": m.get("yes_sub_title") or m.get("subtitle") or "",
            })
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not mkts:
            break
    return out


def fetch_market_candles(series: str, ticker: str, start: int, end: int) -> dict[int, tuple]:
    """{minute_ts: (yes_bid_c, yes_ask_c)} from Kalshi 1-min candlesticks."""
    out: dict[int, tuple] = {}
    data = xl._get(f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks"
                   f"?start_ts={start}&end_ts={end}&period_interval=1")
    for c in (data or {}).get("candlesticks") or []:
        ts = c.get("end_period_ts")
        yb = (c.get("yes_bid") or {}).get("close_dollars")
        ya = (c.get("yes_ask") or {}).get("close_dollars")
        if ts is None or yb is None or ya is None:
            continue
        yb_c, ya_c = xl._num(yb) * 100.0, xl._num(ya) * 100.0
        if 0 <= yb_c <= 100 and 0 <= ya_c <= 100:
            out[int(ts) // 60 * 60] = (yb_c, ya_c)
    return out


def pick_at(candles: dict[int, tuple], close: int, horizon_min: int, tol_min: int = 12):
    """(yes_bid_c, yes_ask_c, minute_ts) at-or-before close - horizon, within tol."""
    target = (close - horizon_min * 60) // 60 * 60
    best = None
    for ts, (yb, ya) in candles.items():
        if ts > target:
            continue
        d = target - ts
        if d <= tol_min * 60 and (best is None or d < best[0]):
            best = (d, yb, ya, ts)
    return (best[1], best[2], best[3]) if best else None


def fetch_trades(ticker: str, cap: int) -> list[dict]:
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


# ---------------------------------------------------------------- spot model
def coinbase_candles(product: str, start: int, end: int) -> dict[int, float]:
    """{minute_ts: close} from Coinbase Exchange public 1-min candles (300/call)."""
    out: dict[int, float] = {}
    s = start
    while s < end:
        e = min(s + 300 * 60, end)
        url = (f"{COINBASE}/products/{product}/candles?granularity=60"
               f"&start={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(s))}"
               f"&end={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(e))}")
        for attempt in range(4):
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0",
                "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                    rows = json.loads(resp.read().decode("utf-8"))
                for r in rows or []:
                    # [time, low, high, open, close, volume]
                    out[int(r[0])] = float(r[4])
                break
            except Exception:  # noqa: BLE001 — throttle/5xx: back off, retry
                if attempt == 3:
                    break
                time.sleep(1.5 * (attempt + 1))
        time.sleep(0.15)  # stay well under Coinbase public rate limits
        s = e
    return out


class SpotModel:
    """Empirical remaining-window return distribution from trailing 1-min spot closes.

    P(YES) for a threshold/range strike = fraction of trailing overlapping h-minute
    log-returns that satisfy the strike condition from the current spot. Fat tails
    come straight from the data; no Gaussian assumption."""

    def __init__(self, closes: dict[int, float], trail_days: float = 5.0):
        self.ts_sorted = sorted(closes)
        self.closes = closes
        self.trail = int(trail_days * 86400)

    def spot_at(self, ts: int) -> float | None:
        m = ts // 60 * 60
        for k in range(0, 6 * 60, 60):        # nearest at-or-before, up to 5 min stale
            if (m - k) in self.closes:
                return self.closes[m - k]
        return None

    def returns(self, ts: int, h_min: int) -> list[float]:
        """Trailing overlapping h-minute log returns strictly before ts."""
        lo, hi = ts - self.trail, ts
        h = h_min * 60
        out = []
        for t in self.ts_sorted:
            if t < lo or t + h >= hi:
                continue
            a, b = self.closes.get(t), self.closes.get(t + h)
            if a and b:
                out.append(math.log(b / a))
        return out

    def p_yes(self, ts: int, h_min: int, strike_type: str,
              floor: float | None, cap: float | None) -> float | None:
        s = self.spot_at(ts)
        rets = self.returns(ts, h_min)
        if s is None or len(rets) < 300:
            return None
        n = len(rets)
        if strike_type == "greater" and floor:
            x = math.log(float(floor) / s)
            return sum(1 for r in rets if r > x) / n
        if strike_type == "less" and cap:
            x = math.log(float(cap) / s)
            return sum(1 for r in rets if r < x) / n
        if strike_type == "between" and floor and cap:
            xl_, xh = math.log(float(floor) / s), math.log(float(cap) / s)
            return sum(1 for r in rets if xl_ <= r <= xh) / n
        return None


# ---------------------------------------------------------------- reporting
def wavg(pairs):
    tw = sum(w for _v, w in pairs)
    return sum(v * w for v, w in pairs) / tw if tw else 0.0


def calib_table(rows, title):
    """rows: (mid_c, ask_c, won(0/1), event, series). Sell-EV assumes maker sells YES at
    the ask (== buys NO at no-bid) and holds to settlement; worst-case maker fee."""
    print(f"  --- {title} ---")
    print(f"    {'band':>9} {'n':>5} {'events':>6} {'avg_mid':>8} {'win%':>6} "
          f"{'gap':>6} {'sellEV_c':>9}")
    g = defaultdict(list)
    for r in rows:
        g[band(r[0])].append(r)
    for k in BANDS:
        v = g.get(k)
        if not v:
            continue
        n = len(v)
        ev_cnt = len({r[3] for r in v})
        mid = sum(r[0] for r in v) / n
        win = sum(r[2] for r in v) / n * 100.0
        sell = sum((r[1] - (100.0 if r[2] else 0.0) - maker_fee_ceil_c(r[1])) for r in v) / n
        print(f"    {f'{k[0]}-{k[1]}':>9} {n:5d} {ev_cnt:6d} {mid:8.1f} {win:6.1f} "
              f"{win - mid:+6.1f} {sell:+9.2f}")
    sub = [r for r in rows if SELL_LO <= r[0] < SELL_HI]
    if sub:
        n = len(sub)
        mid = sum(r[0] for r in sub) / n
        win = sum(r[2] for r in sub) / n * 100.0
        sell = sum((r[1] - (100.0 if r[2] else 0.0) - maker_fee_ceil_c(r[1])) for r in sub) / n
        print(f"    {'3-40 ALL':>9} {n:5d} {len({r[3] for r in sub}):6d} {mid:8.1f} "
              f"{win:6.1f} {win - mid:+6.1f} {sell:+9.2f}")
    print()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--series", default=DEFAULT_SERIES,
                    help="comma list of SERIES:COINBASE_PRODUCT")
    ap.add_argument("--pages", type=int, default=12, help="settled pages (1000/mkts) per series")
    ap.add_argument("--min-vol", type=float, default=200.0)
    ap.add_argument("--sample-candles", type=int, default=400, help="per series")
    ap.add_argument("--sample-tapes", type=int, default=180, help="per series")
    ap.add_argument("--per-market-trades", type=int, default=600)
    ap.add_argument("--horizons", default="50,30,15")
    ap.add_argument("--model-edge-c", type=float, default=5.0)
    ap.add_argument("--trail-days", type=float, default=5.0)
    ap.add_argument("--skip-model", action="store_true")
    ap.add_argument("--skip-tape", action="store_true")
    args = ap.parse_args(argv)

    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]
    pairs = [p.split(":") for p in args.series.split(",") if ":" in p]

    # ---- discovery ------------------------------------------------------
    by_series: dict[str, list[dict]] = {}
    products: dict[str, str] = {}
    for series, product in pairs:
        series = series.strip().upper()
        mkts = fetch_settled(series, args.pages, args.min_vol)
        by_series[series] = mkts
        products[series] = product.strip()
        if mkts:
            days = (max(m["close"] for m in mkts) - min(m["close"] for m in mkts)) / 86400.0
            print(f"  {series}: {len(mkts)} settled mkts >= vol {args.min_vol:.0f}, "
                  f"{len({m['event'] for m in mkts})} events, {days:.1f} days span")
        else:
            print(f"  {series}: none found (wrong ticker or too thin)")
    print()

    # ---- spot data (once per product) ------------------------------------
    models: dict[str, SpotModel] = {}
    if not args.skip_model:
        all_m = [m for v in by_series.values() for m in v]
        if all_m:
            lo = min(m["close"] for m in all_m) - int(args.trail_days * 86400) - 4000
            hi = max(m["close"] for m in all_m) + 60
            for product in sorted(set(products.values())):
                closes = coinbase_candles(product, lo, hi)
                print(f"  spot {product}: {len(closes)} 1-min closes "
                      f"({(hi - lo) / 86400.0:.1f} days requested)")
                models[product] = SpotModel(closes, args.trail_days)
            print()

    # ---- P1 + P3: calibration at horizons, with model overlay ------------
    # calib[h] rows: (mid_c, ask_c, won, event, series); model rows add p_model
    calib: dict[int, list] = {h: [] for h in horizons}
    model_rows: list[tuple] = []  # (mid, ask, won, event, series, excess_c)
    for series, mkts in by_series.items():
        step = max(1, len(mkts) // args.sample_candles)
        sample = mkts[::step][: args.sample_candles]
        model = models.get(products.get(series, ""), None)
        for m in sample:
            candles = fetch_market_candles(series, m["ticker"],
                                           m["close"] - 70 * 60, m["close"])
            if not candles:
                continue
            won = 1 if m["result"] == "yes" else 0
            for h in horizons:
                got = pick_at(candles, m["close"], h)
                if not got:
                    continue
                yb, ya, ts = got
                mid = (yb + ya) / 2.0
                if not (0.5 <= mid <= 99.5) or ya - yb > 20:
                    continue
                calib[h].append((mid, ya, won, m["event"], series))
                if model is not None and h == 30:
                    p = model.p_yes(ts, max(1, (m["close"] - ts) // 60),
                                    m["strike_type"], m["floor"], m["cap"])
                    if p is not None:
                        model_rows.append((mid, ya, won, m["event"], series,
                                           mid - p * 100.0))

    for h in horizons:
        rows = calib[h]
        if rows:
            calib_table(rows, f"P1 calibration @ T-{h}min (all series pooled), n={len(rows)}")
    for series in by_series:
        rows = [r for r in calib.get(30, []) if r[4] == series]
        if rows:
            calib_table(rows, f"P1/P4 calibration @ T-30 — {series} only")
    if calib.get(30):
        rows = sorted(calib[30], key=lambda r: r[3])  # by event ticker ~ by date
        half = len(rows) // 2
        calib_table(rows[:half], "P4 calibration @ T-30 — first date-half")
        calib_table(rows[half:], "P4 calibration @ T-30 — second date-half")

    # ---- P3 verdict table -------------------------------------------------
    if model_rows:
        print(f"  --- P3 model split @ T-30 (edge threshold {args.model_edge_c:.0f}c, "
              f"n={len(model_rows)}) ---")
        print(f"    {'band':>9} {'group':>10} {'n':>5} {'win%':>6} {'gap':>6} {'sellEV_c':>9}")
        for k in BANDS:
            for name, cond in (("OVERpriced", lambda e: e >= args.model_edge_c),
                               ("fair/cheap", lambda e: e < args.model_edge_c)):
                v = [r for r in model_rows if band(r[0]) == k and cond(r[5])]
                if len(v) < 8:
                    continue
                n = len(v)
                mid = sum(r[0] for r in v) / n
                win = sum(r[2] for r in v) / n * 100.0
                sell = sum((r[1] - (100.0 if r[2] else 0.0) - maker_fee_ceil_c(r[1]))
                           for r in v) / n
                print(f"    {f'{k[0]}-{k[1]}':>9} {name:>10} {n:5d} {win:6.1f} "
                      f"{win - mid:+6.1f} {sell:+9.2f}")
        sub = [r for r in model_rows if SELL_LO <= r[0] < SELL_HI]
        for name, cond in (("OVERpriced", lambda e: e >= args.model_edge_c),
                           ("fair/cheap", lambda e: e < args.model_edge_c)):
            v = [r for r in sub if cond(r[5])]
            if not v:
                continue
            n = len(v)
            win = sum(r[2] for r in v) / n * 100.0
            mid = sum(r[0] for r in v) / n
            sell = sum((r[1] - (100.0 if r[2] else 0.0) - maker_fee_ceil_c(r[1]))
                       for r in v) / n
            print(f"    {'3-40 ALL':>9} {name:>10} {n:5d} {win:6.1f} {win - mid:+6.1f} "
                  f"{sell:+9.2f}")
        print()

    # ---- P2: maker tape by band x minutes-to-expiry -----------------------
    if not args.skip_tape:
        recs = []  # (net_ceil_c, count, yes_c, tte_min, mside, event)
        tapes = 0
        for _series, mkts in by_series.items():
            step = max(1, len(mkts) // args.sample_tapes)
            for m in mkts[::step][: args.sample_tapes]:
                trs = fetch_trades(m["ticker"], args.per_market_trades)
                if not trs:
                    continue
                tapes += 1
                settle = 100.0 if m["result"] == "yes" else 0.0
                for t in trs:
                    p = None
                    for k in ("yes_price_dollars", "yes_price"):
                        if t.get(k) is not None:
                            v = xl._num(t.get(k))
                            p = v * 100.0 if k.endswith("dollars") else v
                            break
                    side = (t.get("taker_side") or "").lower()
                    cnt = xl._num(t.get("count_fp")) or 1.0
                    ts = _iso_to_unix(t.get("created_time"))
                    if p is None or not (0 < p < 100) or side not in ("yes", "no") or not ts:
                        continue
                    tte = max(0.0, (m["close"] - ts) / 60.0)
                    if side == "yes":     # maker SOLD yes at p
                        gross, mside = p - settle, "sell"
                    else:
                        gross, mside = settle - p, "buy"
                    recs.append((gross - maker_fee_ceil_c(p), cnt, p, tte, mside, m["event"]))
        print(f"  --- P2 maker tape: {tapes} tapes, {len(recs)} trades ---")
        sell = [r for r in recs if r[4] == "sell"]

        def _cell(v):
            return (len(v), sum(r[1] for r in v), wavg([(r[0], r[1]) for r in v]))

        print("    maker-SELL net_ceil (c/contract, vol-weighted) by yes-price x tte(min):")
        hdr = "    " + f"{'band':>9}" + "".join(f"{f'{a}-{b}m':>10}" for a, b in TTE_SLICES)
        print(hdr)
        for k in BANDS:
            row = f"    {f'{k[0]}-{k[1]}':>9}"
            any_cell = False
            for a, b in TTE_SLICES:
                v = [r for r in sell if band(r[2]) == k and a <= r[3] < b]
                if len(v) < 15:
                    row += f"{'-':>10}"
                    continue
                any_cell = True
                _n, _c, net = _cell(v)
                row += f"{net:+10.2f}"
            if any_cell:
                print(row)
        pocket = [r for r in sell if SELL_LO <= r[2] < SELL_HI]
        if pocket:
            n, c, net = _cell(pocket)
            print(f"    3-40c sell pocket: trades={n} contracts={c:.0f} net_ceil={net:+.3f}c")
            ev_sorted = sorted(pocket, key=lambda r: r[5])
            half = len(ev_sorted) // 2
            for name, hh in (("date-half-A", ev_sorted[:half]), ("date-half-B", ev_sorted[half:])):
                n, c, net = _cell(hh)
                print(f"    {name}: trades={n} contracts={c:.0f} net_ceil={net:+.3f}c")
            buy_pocket = [r for r in recs if r[4] == "buy" and SELL_LO <= r[2] < SELL_HI]
            if buy_pocket:
                n, c, net = _cell(buy_pocket)
                print(f"    (mirror maker-BUY 3-40c: net_ceil={net:+.3f}c — expect negative)")
        print()

    # ---- verdicts ----------------------------------------------------------
    print("  === PRE-REGISTERED VERDICTS (see docs/THETA_THESIS.md) ===")
    v30 = [r for r in calib.get(30, []) if SELL_LO <= r[0] < SELL_HI]
    if v30:
        gap = (sum(r[2] for r in v30) / len(v30) * 100.0) - (sum(r[0] for r in v30) / len(v30))
        sell_ev = (
            sum((r[1] - (100.0 if r[2] else 0.0) - maker_fee_ceil_c(r[1])) for r in v30)
            / len(v30)
        )
        print(f"  P1 tails overpriced @T-30 3-40c: gap={gap:+.1f}c (need <= -2), "
              f"sellEV={sell_ev:+.2f}c, n={len(v30)} -> "
              f"{'PASS' if gap <= -2.0 and sell_ev > 0 else 'FAIL/WEAK'}")
    if not args.skip_tape:
        pocket = [r for r in recs if r[4] == "sell" and SELL_LO <= r[2] < SELL_HI]
        if pocket:
            net = wavg([(r[0], r[1]) for r in pocket])
            print(f"  P2 maker-sell tape 3-40c: net_ceil={net:+.3f}c -> "
                  f"{'PASS' if net > 0 else 'FAIL'} (check both date-halves above)")
    if model_rows:
        sub = [r for r in model_rows if SELL_LO <= r[0] < SELL_HI]
        over = [r for r in sub if r[5] >= args.model_edge_c]
        fair = [r for r in sub if r[5] < args.model_edge_c]
        if len(over) >= 20 and len(fair) >= 20:
            wo = sum(r[2] for r in over) / len(over) * 100
            wf = sum(r[2] for r in fair) / len(fair) * 100
            mo = sum(r[0] for r in over) / len(over)
            mf = sum(r[0] for r in fair) / len(fair)
            print(f"  P3 model split 3-40c: OVER win-gap={wo - mo:+.1f}c (n={len(over)}) vs "
                  f"fair win-gap={wf - mf:+.1f}c (n={len(fair)}) -> "
                  f"{'PASS' if (wo - mo) < (wf - mf) - 1.0 else 'FAIL/WEAK'}")
        else:
            print(f"  P3: insufficient model rows (over={len(over)} fair={len(fair)})")
    print("  P4: read the per-asset and date-half tables above — signs must agree.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
