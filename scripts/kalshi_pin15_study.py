"""PIN15 thesis validation probe — Phase A (docs/PIN15_THESIS.md). Read-only, public APIs, stdlib.

The venue: Kalshi's 15-minute crypto up/down markets (KXBTC15M + twins). A fresh Up/Down pair
opens at the top of each quarter-hour and settles 15 min later on the **60-second AVERAGE** of the
CF Benchmarks index over the final minute (NOT the last tick). The thesis: as a window closes the
settle is a partially-observed deterministic number, so the retail quote — anchored to the flashing
last-tick price — lags it.

Phase A is deliberately bounded by what free historical data can prove. Coinbase / Kalshi candles
are 60-second minimum granularity, so the *sub-minute* running-average reconstruction (the tradeable
SPIKEFADE cell) is Phase B (a live micro-collection, pre-authorized only if P1 clears here). What
Phase A CAN measure at minute resolution — and what it grades:

  STRUCTURE  discovery dump of a few settled KXBTC15M markets (confirm target/strike encoding
             before computing anything — fail-soft, like the theta probe).
  P1-DRIFT   determinism / calibration: binned by the spot displacement from target at T ∈
             {180,120,60}s before close, what is realized P(Up) and the directional hit-rate?
             Grades P1's "de-randomizes early enough to act" at a latency we can actually hit
             (minutes, not the sub-second the theta thesis assumed we couldn't reach).
  SPIKEFADE  mechanism existence: from the final 1-min candle, how often does the last-tick side
             (candle close vs target) DISAGREE with the minute-average side ((o+c)/2 vs target),
             and when they diverge does the settle follow the average (it must — this sizes the
             cell that motivates Phase B). NOT the tradeable P2 (that needs sub-minute quotes).
  P2-COARSE  a LOWER-BOUND P2: taking the drift-favored side at the last available Kalshi 1-min
             quote, net of worst-case taker fees, split-half OOS and per asset. A real Phase-B
             verdict needs the sub-minute quote path; this only bounds it.
  UPBIAS     structural co-check: realized Up-rate vs 50% and the yes/no overround from candles.

No lookahead: displacement/quote at decision time T use only candles ending <= T; the settle result
grades only. Target per window is reconstructed as Coinbase spot at window-open (close-900s) unless
the market record carries an explicit numeric strike (reconciled in STRUCTURE).

Usage (ops channel):
  {"type":"script","name":"kalshi_pin15_study","args":["--pages","8","--assets","BTC,ETH"]}
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

# asset -> (kalshi 15-min series, coinbase product). Wrong guesses fail soft (0 found).
ASSET_MAP = {
    "BTC": ("KXBTC15M", "BTC-USD"),
    "ETH": ("KXETH15M", "ETH-USD"),
    "SOL": ("KXSOL15M", "SOL-USD"),
    "XRP": ("KXXRP15M", "XRP-USD"),
}

WINDOW_SECS = 900                       # 15-minute windows
DECISION_TS = [180, 120, 60]            # seconds before close to test determinism at
# displacement bins in basis points of (spot-target)/target; signed (>0 => Up favored)
DISP_BINS = [-1e9, -50, -20, -10, -5, -2, 2, 5, 10, 20, 50, 1e9]


def _iso_to_unix(iso: str) -> int:
    try:
        return calendar.timegm(time.strptime((iso or "")[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return 0


def taker_fee_c(p_c: float) -> float:
    """Kalshi taker fee, cents/contract, worst-case rounded up."""
    return math.ceil(7.0 * (p_c / 100.0) * (1 - p_c / 100.0))


# ------------------------------------------------------------------ data sources
def fetch_settled(series: str, pages: int, min_vol: float) -> list[dict]:
    """Settled 15-min binaries for a series, newest first, keeping strike + time fields."""
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
            opent = _iso_to_unix(m.get("open_time")) or (close - WINDOW_SECS)
            out.append({
                "ticker": m.get("ticker") or "", "series": series,
                "open": opent, "close": close, "result": res, "vol": vol,
                "strike_type": (m.get("strike_type") or "").lower(),
                "floor": m.get("floor_strike"), "cap": m.get("cap_strike"),
                "sub": m.get("yes_sub_title") or m.get("subtitle") or "",
                "settle": m.get("settlement_value") or m.get("expiration_value"),
            })
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not mkts:
            break
    return out


def coinbase_ohlc(product: str, start: int, end: int) -> dict[int, tuple]:
    """{minute_ts: (open, high, low, close)} from Coinbase Exchange public 1-min candles."""
    out: dict[int, tuple] = {}
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
                    out[int(r[0])] = (float(r[3]), float(r[2]), float(r[1]), float(r[4]))
                break
            except Exception:  # noqa: BLE001 — throttle/5xx: back off, retry
                if attempt == 3:
                    break
                time.sleep(1.5 * (attempt + 1))
        time.sleep(0.15)  # stay under Coinbase public rate limits
        s = e
    return out


def spot_at(ohlc: dict[int, tuple], ts: int) -> float | None:
    """Close of the nearest candle at-or-before ts (up to 6 min stale)."""
    m = ts // 60 * 60
    for k in range(0, 6 * 60, 60):
        if (m - k) in ohlc:
            return ohlc[m - k][3]
    return None


def realized_vol_bps(ohlc: dict[int, tuple], start: int, end: int) -> float | None:
    """Per-minute realized vol over [start,end] as the stdev of 1-min log-returns, in bps.
    This is the regime knob: a high value means spot moved a lot minute-to-minute in the
    window, so a given spot displacement from target is less 'pinned' into settlement."""
    closes = [ohlc[t][3] for t in sorted(ohlc) if start <= t <= end and ohlc[t][3] > 0]
    if len(closes) < 5:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / n
    return math.sqrt(var) * 1e4    # bps/min


def fetch_kalshi_candles(series: str, ticker: str, start: int, end: int) -> dict[int, tuple]:
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
        if 0 <= yb_c <= 100 and 0 <= ya_c <= 100 and ya_c >= yb_c:
            out[int(ts) // 60 * 60] = (yb_c, ya_c)
    return out


def kalshi_quote_at(candles: dict[int, tuple], ts: int, tol_min: int = 1):
    """(yes_bid_c, yes_ask_c, minute_ts) at-or-before ts, within tol (default 1 min so the
    quote is contemporaneous with the displacement — a looser tol grabs a staler, cheaper
    quote and inflates the edge). Else None."""
    target = ts // 60 * 60
    best = None
    for t, (yb, ya) in candles.items():
        if t > target:
            continue
        d = target - t
        if d <= tol_min * 60 and (best is None or d < best[0]):
            best = (d, yb, ya, t)
    return (best[1], best[2], best[3]) if best else None


def disp_bin(bps: float):
    for i in range(len(DISP_BINS) - 1):
        if DISP_BINS[i] <= bps < DISP_BINS[i + 1]:
            return (DISP_BINS[i], DISP_BINS[i + 1])
    return (DISP_BINS[-2], DISP_BINS[-1])


# ------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pages", type=int, default=8, help="settled-market pages per series")
    ap.add_argument("--min-vol", type=float, default=100.0)
    ap.add_argument("--assets", default="BTC,ETH")
    ap.add_argument("--disp-bps", type=float, default=5.0,
                    help="|displacement| threshold (bps) for the P2-COARSE confident entry")
    ap.add_argument("--max-markets", type=int, default=1200,
                    help="cap per series on candle fetches (rate-limit guard)")
    args = ap.parse_args(argv)

    assets = [a.strip().upper() for a in args.assets.split(",") if a.strip()]
    rows: list[dict] = []            # one enriched row per settled window
    struct_samples: list[dict] = []

    for asset in assets:
        if asset not in ASSET_MAP:
            print(f"  (skip unknown asset {asset!r})")
            continue
        series, product = ASSET_MAP[asset]
        mkts = fetch_settled(series, args.pages, args.min_vol)
        print(f"=== {asset} {series}: {len(mkts)} settled windows (vol>={args.min_vol:.0f}) ===")
        if not mkts:
            continue
        struct_samples.extend({**m, "asset": asset} for m in mkts[:3])
        mkts = mkts[:args.max_markets]

        # one Coinbase pull spanning the whole date range (cheap vs per-window)
        lo = min(m["open"] for m in mkts) - 300
        hi = max(m["close"] for m in mkts) + 120
        ohlc = coinbase_ohlc(product, lo, hi)
        if not ohlc:
            print("  (no coinbase spot — skip)\n")
            continue

        for m in mkts:
            close, opent = m["close"], m["open"]
            # target: explicit numeric strike if present, else spot at window open
            target = None
            for key in ("floor", "cap"):
                v = m.get(key)
                if v not in (None, ""):
                    try:
                        target = float(v)
                    except (TypeError, ValueError):
                        target = None
                if target:
                    break
            target_src = "strike" if target else "open"
            if not target:
                target = spot_at(ohlc, opent)
            if not target:
                continue

            up = 1 if m["result"] == "yes" else 0
            disp = {}
            for t in DECISION_TS:
                s = spot_at(ohlc, close - t)
                disp[t] = ((s - target) / target * 1e4) if s else None   # signed bps

            # final-minute candle for the SPIKEFADE mechanism proxy
            fin = ohlc.get((close - 60) // 60 * 60) or ohlc.get(close // 60 * 60)
            last_tick = fin[3] if fin else None                 # candle close
            avg_proxy = ((fin[0] + fin[3]) / 2.0) if fin else None  # (open+close)/2 ≈ 60s mean

            # quote (yes bid, ask) at each decision T (Kalshi 1-min candles, tol 1 min)
            kc = fetch_kalshi_candles(m["series"], m["ticker"], opent - 60, close + 60)
            quote = {}
            for t in DECISION_TS:
                q = kalshi_quote_at(kc, close - t)
                quote[t] = (q[0], q[1]) if q else None    # (yes_bid_c, yes_ask_c)

            rows.append({
                "asset": asset, "close": close, "up": up, "target": target,
                "target_src": target_src, "disp": disp, "last_tick": last_tick,
                "avg_proxy": avg_proxy, "quote": quote,
                "vol": realized_vol_bps(ohlc, opent, close),
            })
        print(f"  enriched {sum(1 for r in rows if r['asset']==asset)} windows "
              f"({sum(1 for r in rows if r['asset']==asset and r['target_src']=='strike')} explicit-strike)\n")

    if not rows:
        print("No enriched windows — nothing to grade.")
        return 0

    # ---- STRUCTURE ----------------------------------------------------------
    print("--- STRUCTURE (sample settled markets — confirm target encoding) ---")
    for m in struct_samples[:6]:
        print(f"  {m['asset']} tk={m['ticker']} result={m['result']} strike_type={m['strike_type']!r}"
              f" floor={m['floor']} cap={m['cap']} settle={m['settle']}")
        print(f"      sub={str(m['sub'])[:70]!r} open={m['open']} close={m['close']}"
              f" dur={m['close']-m['open']}s")
    print()

    # ---- P1-DRIFT: determinism / calibration by displacement -----------------
    print("--- P1-DRIFT: realized P(Up) & directional hit-rate by spot displacement from target ---")
    print("    (hit% = settle matched the sign of displacement; pooled all assets)")
    for t in DECISION_TS:
        print(f"  T-{t}s:")
        print(f"    {'disp_bps':>14} {'n':>5} {'P(Up)%':>7} {'hit%':>6}")
        g = defaultdict(list)
        for r in rows:
            d = r["disp"].get(t)
            if d is None:
                continue
            g[disp_bin(d)].append(r["up"])
        for k in [(DISP_BINS[i], DISP_BINS[i + 1]) for i in range(len(DISP_BINS) - 1)]:
            v = g.get(k)
            if not v:
                continue
            n = len(v)
            pup = sum(v) / n * 100.0
            # sign of displacement: bins are entirely one side of 0 except straddle guard
            side_up = k[0] >= 0
            hit = (sum(v) if side_up else (n - sum(v))) / n * 100.0
            lab = f"[{k[0]:+.0f},{k[1]:+.0f})" if abs(k[0]) < 1e8 else (
                f"<{k[1]:+.0f}" if k[0] < 0 else f">={k[0]:+.0f}")
            print(f"    {lab:>14} {n:5d} {pup:7.1f} {hit:6.1f}")
        # how decided: share of windows already >=5bp displaced at T
        dec = [r for r in rows if r["disp"].get(t) is not None]
        conf = [r for r in dec if abs(r["disp"][t]) >= 5.0]
        if dec:
            print(f"    -> {len(conf)}/{len(dec)} ({100*len(conf)/len(dec):.0f}%) windows >=5bp"
                  f" displaced at T-{t}s")
    print()

    # ---- SPIKEFADE mechanism -------------------------------------------------
    print("--- SPIKEFADE mechanism (final-minute last-tick side vs 60s-average-proxy side) ---")
    mech = [r for r in rows if r["last_tick"] and r["avg_proxy"]]
    if mech:
        div = 0
        div_avg_right = 0
        for r in mech:
            lt_up = r["last_tick"] >= r["target"]
            av_up = r["avg_proxy"] >= r["target"]
            if lt_up != av_up:
                div += 1
                if (r["up"] == 1) == av_up:
                    div_avg_right += 1
        print(f"    n={len(mech)}  divergent(last-tick != avg-proxy)={div} "
              f"({100*div/len(mech):.1f}%)")
        if div:
            print(f"    when divergent, settle followed the AVERAGE side: {div_avg_right}/{div} "
                  f"({100*div_avg_right/div:.0f}%)  [expect high — this is the exploitable cell]")
        print("    NB (o+c)/2 is a coarse 60s-mean proxy; the tradeable sub-minute test is Phase B.")
    print()

    # ---- P2-COARSE: EV taking the drift side at the CONTEMPORANEOUS 1-min ASK ---
    print(f"--- P2-COARSE: EV of taking the drift-favored side at the 1-min quote, paying the "
          f"REAL taker cost (yes-ask up / no-ask down), |disp|>={args.disp_bps:.0f}bp, "
          f"net of worst-case taker fee ---")
    print("    Quote tol = 1 min (contemporaneous with displacement). Still a LOWER-RESOLUTION")
    print("    bound — the true sub-minute ask path is Phase B; here it's the 1-min-candle ask.")
    for t in DECISION_TS:
        print(f"  entry at T-{t}s:")
        _p2_slice(rows, t, args.disp_bps, "pooled", lambda r: True)
        for a in assets:
            _p2_slice(rows, t, args.disp_bps, a, lambda r, a=a: r["asset"] == a)
        # split-half OOS by close time
        ordered = sorted([r for r in rows if r["disp"].get(t) is not None
                          and r["quote"].get(t) is not None], key=lambda r: r["close"])
        if len(ordered) >= 20:
            half = len(ordered) // 2
            _p2_slice(ordered[:half], t, args.disp_bps, "half-1(old)", lambda r: True)
            _p2_slice(ordered[half:], t, args.disp_bps, "half-2(new)", lambda r: True)
    print()

    # ---- VOLREGIME (P4): does the edge survive in higher-vol windows? ---------
    print("--- VOLREGIME (P4 robustness): P1 pin + P2 ask-EV split by per-window realized vol ---")
    print("    Quartiles by realized 2-min-scale vol (stdev of 1-min log-returns over the window,")
    print("    bps/min). The KILL risk: the whole Phase-A sample is one regime; if the +EV lives")
    print("    ONLY in the calm quartiles and dies (or inverts) in the top vol quartile, the")
    print("    favorite-buy is just harvesting a premium a real high-vol regime pays back.")
    volrows = sorted((r for r in rows if r.get("vol") is not None), key=lambda r: r["vol"])
    if len(volrows) >= 40:
        q = len(volrows) // 4
        quartiles = [("Q1 calmest", volrows[:q]), ("Q2", volrows[q:2 * q]),
                     ("Q3", volrows[2 * q:3 * q]), ("Q4 wildest", volrows[3 * q:])]
        for t in (180, 120):
            print(f"  entry at T-{t}s (|disp|>={args.disp_bps:.0f}bp):")
            for name, grp in quartiles:
                vlo = min(r["vol"] for r in grp)
                vhi = max(r["vol"] for r in grp)
                conf = [r for r in grp if r["disp"].get(t) is not None
                        and abs(r["disp"][t]) >= args.disp_bps]
                hit = (sum(1 for r in conf if (r["up"] == 1) == (r["disp"][t] > 0)) / len(conf) * 100.0
                       if conf else 0.0)
                label = f"{name} vol[{vlo:.1f},{vhi:.1f}] pin{hit:.0f}%"
                _p2_slice(grp, t, args.disp_bps, label, lambda r: True)
    else:
        print("    (insufficient sample for a quartile split)")
    print()

    # ---- UPBIAS --------------------------------------------------------------
    up_rate = sum(r["up"] for r in rows) / len(rows) * 100.0
    print(f"--- UPBIAS: realized Up-rate = {up_rate:.1f}% over n={len(rows)} "
          f"(50% = unbiased; persistent skew => a structural directional prior) ---")
    print()

    print("VERDICT KEY (per docs/PIN15_THESIS.md):")
    print("  P1 PASS  if P1-DRIFT is calibrated (hit% tracks displacement) AND a non-trivial")
    print("           share of windows are already decided at T-60s/-120s (actionable latency).")
    print("  P1 KILL  if outcome is ~coin-flip until the final <10s (un-actionable for us).")
    print("  P2 needs Phase B (sub-minute quotes); P2-COARSE only bounds it here.")
    return 0


def _p2_cost(quote, side_up):
    """Real taker cost (cents) to buy the drift side: yes-ask if Up, no-ask (=100-yes-bid) if Down."""
    yb, ya = quote
    cost = ya if side_up else (100.0 - yb)
    return min(max(cost, 1.0), 99.0)


def _p2_slice(rows, t, thr, label, pred):
    sub = [r for r in rows if pred(r) and r["disp"].get(t) is not None
           and r["quote"].get(t) is not None and abs(r["disp"][t]) >= thr]
    if not sub:
        return
    evs, costs, wins = [], [], 0
    for r in sub:
        side_up = r["disp"][t] > 0
        cost = _p2_cost(r["quote"][t], side_up)
        won = (r["up"] == 1) == side_up
        wins += 1 if won else 0
        costs.append(cost)
        evs.append((100.0 if won else 0.0) - cost - taker_fee_c(cost))
    n = len(evs)
    print(f"    {label:>12}: n={n:4d} EV={sum(evs)/n:+6.2f}c/ct  win%={wins/n*100:5.1f}"
          f"  avg_ask_cost={sum(costs)/n:5.1f}c")


if __name__ == "__main__":
    raise SystemExit(main())
