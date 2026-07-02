"""Exit-rule backtest for the maker-SELL edge — does TP/SL beat hold-to-settlement, and does
it cut the all-or-nothing tail?

The markout backtest (kalshi_mm) showed selling yes on overpriced 5-35c contracts is +EV held
to settlement. But settlement is all-or-nothing (a sold 20c longshot that hits costs 80c). This
replays each maker-SELL fill's ACTUAL post-entry yes-price path (from candlesticks) through a
grid of take-profit / stop-loss rules and compares them to holding, reporting not just mean P&L
but the VARIANCE and 5th-percentile tail per rule — so we can pick an exit that keeps the edge
while capping the downside.

Position = short yes (sold at entry price p). It PROFITS when yes falls; to exit we BUY yes
back by crossing to the ask (taker fee). Rules trigger on the mid:
  TP: mid falls to (p - tp)  -> lock profit, buy back at ask.
  SL: mid rises to (p + sl)  -> cap loss, buy back at ask.
  else hold to settlement: pnl = p - settle_value (0/100), no exit fee.
Entry pays the maker fee (worst-case 1c/contract); early exits also pay the taker fee.

Read-only public API. Reuses kalshi_flb (series/settled discovery, candles) + kalshi_mm (tape,
fees). Usage: {"type":"script","name":"kalshi_mm_exits","args":["--sample","250","--band","5,40"]}
"""

from __future__ import annotations

import argparse
import math
import statistics

import kalshi_flb as flb
import kalshi_mm as mm
import xvenue_leadlag as xl

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"


def maker_fee_c(p_c: float) -> float:
    """Worst-case maker fee in CENTS (rounded up per contract), p in cents."""
    p = p_c / 100.0
    return math.ceil(1.75 * p * (1 - p))


def taker_fee_c(p_c: float) -> float:
    p = p_c / 100.0
    return math.ceil(7.0 * p * (1 - p))


def replay_short(entry_c: float, path: list[tuple], settle_c: float,
                 tp: float | None, sl: float | None) -> tuple[float, str, int]:
    """(net_pnl_cents, exit_kind, hold_minutes) for a short-yes sold at entry_c.
    path = [(minute, yes_bid_c, yes_ask_c), ...] strictly AFTER entry, time-ordered."""
    ent_fee = maker_fee_c(entry_c)
    for i, (_mn, yb, ya) in enumerate(path):
        mid = (yb + ya) / 2.0
        gain = entry_c - mid                       # short profits as yes falls
        if tp is not None and gain >= tp:
            return (entry_c - ya - ent_fee - taker_fee_c(ya), "tp", i)
        if sl is not None and gain <= -sl:
            return (entry_c - ya - ent_fee - taker_fee_c(ya), "sl", i)
    return (entry_c - settle_c - ent_fee, "settle", len(path))


def _pctile(xs: list[float], q: float) -> float:
    if not xs:
        return math.nan
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(q * (len(s) - 1))))
    return s[k]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top-series", type=int, default=70)
    ap.add_argument("--per-series", type=int, default=120)
    ap.add_argument("--sample", type=int, default=250)
    ap.add_argument("--per-market-trades", type=int, default=400)
    ap.add_argument("--min-vol", type=float, default=50.0)
    ap.add_argument("--max-pages", type=int, default=25)
    ap.add_argument("--path-days", type=float, default=4.0, help="candle window before close")
    ap.add_argument("--band", default="5,40", help="entry yes-price band (cents lo,hi) to test")
    ap.add_argument("--no-sports", action="store_true")
    args = ap.parse_args(argv)
    blo, bhi = (float(x) for x in args.band.split(","))

    series_list = flb.discover_series(args.top_series, args.max_pages)
    settled: list[dict] = []
    for s in series_list:
        if args.no_sports and flb.category(s) == "Sports":
            continue
        settled.extend(flb.fetch_settled_for_series(s, args.per_series, args.min_vol))
    if not settled:
        print("  (no settled markets)")
        return 0
    step = max(1, len(settled) // args.sample)
    sample = settled[::step][:args.sample]
    print(f"=== Maker-SELL exit-rule backtest — {len(settled)} settled, tapes+paths for "
          f"{len(sample)}; entries yes {blo:.0f}-{bhi:.0f}c ===")

    # gather maker-SELL fills with their post-entry path
    # fill = (entry_c, settle_c, cnt, path_after_entry)
    fills = []
    used_mkts = 0
    for m in sample:
        trs = mm.fetch_trades(m["ticker"], args.per_market_trades)
        if not trs:
            continue
        path_start = m["close"] - int(args.path_days * 86400)
        candles = flb.fetch_candles(m["series"], m["ticker"], path_start, m["close"] + 60)
        if not candles:
            continue
        # minute -> (yb_c, ya_c), time-ordered
        cm = sorted((mn, yb * 100.0, ya * 100.0) for mn, (yb, ya) in candles.items())
        settle_c = 100.0 if m["result"] == "yes" else 0.0
        got = False
        for t in trs:
            p = mm.trade_yes_price(t)
            side = (t.get("taker_side") or "").lower()
            cnt = xl._num(t.get("count_fp")) or 1.0
            if p is None or side != "yes":            # only maker-SELL (taker bought yes)
                continue
            p_c = p * 100.0
            if not (blo <= p_c < bhi):
                continue
            em = _iso_min(t.get("created_time"))   # entry minute from created_time ISO
            if em is None:
                continue
            path = [(mn, yb, ya) for mn, yb, ya in cm if mn > em]
            if not path:                               # entry before candle window -> skip
                continue
            fills.append((p_c, settle_c, cnt, path))
            got = True
        if got:
            used_mkts += 1

    if not fills:
        print("  (no maker-sell fills with a usable price path)")
        return 0
    print(f"  {len(fills)} maker-sell fills across {used_mkts} markets\n")

    # rule grid: (tp, sl); None = disabled. include hold-to-settlement.
    tps = [None, 5, 8, 12, 20]
    sls = [None, 8, 12, 20, 30]
    grid = [(tp, sl) for tp in tps for sl in sls]

    print("  rule = TP/SL in cents of yes-price move on the SHORT (TP=yes fell, SL=yes rose).")
    print("  pnl in cents/contract, vol-weighted; p5 = 5th-pctile (the tail you want to cut).\n")
    print(f"  {'TP':>4} {'SL':>4} {'mean':>7} {'std':>6} {'p5':>7} {'win%':>6} "
          f"{'%tp':>5} {'%sl':>5} {'%hold':>6} {'sharpe':>7}")
    best = None
    for tp, sl in grid:
        pnls, wts, kinds = [], [], []
        for entry_c, settle_c, cnt, path in fills:
            pnl, kind, _h = replay_short(entry_c, path, settle_c, tp, sl)
            pnls.append(pnl)
            wts.append(cnt)
            kinds.append(kind)
        tw = sum(wts)
        mean = sum(p * w for p, w in zip(pnls, wts, strict=True)) / tw
        std = statistics.pstdev(pnls) if len(pnls) > 1 else 0.0
        p5 = _pctile(pnls, 0.05)
        win = 100.0 * sum(1 for p in pnls if p > 0) / len(pnls)
        ntp = 100.0 * sum(1 for k in kinds if k == "tp") / len(kinds)
        nsl = 100.0 * sum(1 for k in kinds if k == "sl") / len(kinds)
        nhold = 100.0 * sum(1 for k in kinds if k == "settle") / len(kinds)
        sharpe = mean / std if std else 0.0
        tag = f"{tp if tp else '-':>4} {sl if sl else '-':>4}"
        print(f"  {tag} {mean:+7.2f} {std:6.1f} {p5:+7.1f} {win:5.1f}% "
              f"{ntp:4.0f}% {nsl:4.0f}% {nhold:5.0f}% {sharpe:+7.3f}")
        if best is None or mean > best[1]:
            best = ((tp, sl), mean, p5, sharpe)
    print(f"\n  best mean: TP={best[0][0]} SL={best[0][1]} -> {best[1]:+.2f}c/contract "
          f"(p5={best[2]:+.1f}, sharpe={best[3]:+.3f})")
    print("  Compare the hold row (TP=- SL=-): higher mean but worse p5/std = the all-or-nothing")
    print("  tail. A TP/SL row with similar mean + far better p5 is the risk-adjusted winner.")
    return 0


def _iso_min(iso: str | None) -> int | None:
    """Unix MINUTE from an ISO8601 timestamp like '2026-06-27T21:52:39.459Z'."""
    if not iso:
        return None
    import calendar
    import time
    try:
        return calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")) // 60
    except (ValueError, TypeError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
