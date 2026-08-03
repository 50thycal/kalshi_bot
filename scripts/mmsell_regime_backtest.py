"""mmsell REGIME BACKTEST — what was the maker-sell edge on the regimes we have never traded?

Our paper books cover ONE regime: ~16 days of summer sports plus BTC daily. Sept-Nov brings
NFL, MLB playoffs, NBA/NHL and the November midterms, and we have zero settled trades on any of
them. Waiting for the season to teach us costs a season. Kalshi serves its own settled history
for any series, so the mmsell10 trade can be replayed on LAST season's NFL/NBA/NHL games and on
the election ladders — genuinely out-of-sample, before a dollar is committed.

Four outputs, each answering a question the supply forecast cannot:

  1. COVERAGE — can this regime even be measured? Kalshi serves only as much candle history as a
     market was quoted for, and the crypto study was bounded to htc<1h for exactly this reason
     while mmsell trades htc>=1h. A regime whose candles do not reach into the 14-day window is
     reported as UNMEASURABLE rather than quietly backtested on the wrong trades.
  2. BAND YIELD — what fraction of a regime's markets ever become mmsell10-eligible, at what
     hours-to-close, and at what price. This is the conversion rate that turns the supply
     forecast's market counts into expected entry counts.
  3. REGIME EDGE — n, win%, mean cents/trade and the 5th-percentile tail of the hold-to-
     settlement short, per regime. The number that decides whether a regime's supply is an
     opportunity or a liability.
  4. CORRELATED SETTLEMENT — mmsell's risk model assumes many small INDEPENDENT positions.
     This measures the assumption directly: group entries by settlement date and compare the
     spread of per-date loss counts against the binomial spread independence predicts. The ratio
     is an overdispersion factor, and it is what a per-settlement-date position cap should be
     sized on. Election night is the case that motivated it; the measurement is general.

Entry convention matches mmsell10 exactly: the book rests a BUY-NO at the no-bid, which sells
YES at 100-no_bid == the yes ASK, so the ask (capped at maxyes) is the entry price, screened on
the yes MIDPOINT band. Paper's fill assumption (a resting order always fills) applies here too,
so the live maker adverse-selection haircut (docs/MMSELL_FILL_MODEL.md) sits on top of anything
found below — these numbers are comparable to our PAPER books, not to live.

Read-only public API (no auth). Usage:
    {"type": "script", "name": "mmsell_regime_backtest"}
    {"type": "script", "name": "mmsell_regime_backtest", "args": ["--regimes", "NFL,NBA,NHL"]}
    {"type": "script", "name": "mmsell_regime_backtest", "args": ["--interval", "1", "--sample", "60"]}
"""

from __future__ import annotations

import argparse
import datetime as _dt
import statistics
import time
from collections import defaultdict

import mmsell_crypto_study as cs  # fee_c, replay_short, _stats — the tested replay math
import mmsell_seasonal as ms
import mmsell_supply_forecast as sf  # fetch_settled_deep, discover_series, SEED_SERIES
import xvenue_leadlag as xl

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

# The three pre-registered anchor-set stop levels (docs/MMSELL_ANCHOR_SET.md A1/A2/A3), NOT a
# free grid: the levels were fixed in advance, so comparing them per regime is a forward test
# rather than an in-sample optimum. Trigger is the yes-BID — never the mid or ask, which thin
# cheap books contaminate (docs/MMSELL_CRYPTO_STUDY.md).
STOP_LEVELS = (12, 20, 30)

# Regimes backtested by default: everything mmsell has never traded, plus MLB as a control we
# DO have paper history for (if the method reproduces our MLB paper numbers, it is trustworthy
# on the regimes we cannot check).
DEFAULT_REGIMES = ("NFL", "NCAAF", "NBA", "NHL", "NCAAB", "Elections", "MLB")


# ------------------------------------------------------------------ data


def fetch_candles(series: str, ticker: str, start: int, end: int,
                  interval: int) -> dict[int, tuple]:
    """{period_start_minute: (yes_bid_c, yes_ask_c)} over [start,end].

    kalshi_flb.fetch_candles hardcodes 1-minute periods, which cannot span mmsell's 14-day
    holding window at a sane request count (20k candles/market). `interval` in minutes lets the
    whole window be pulled in one request at 60 (336 points), with 1 available for a fine pass
    on a small sample."""
    out: dict[int, tuple] = {}
    span = 4800 * interval * 60
    s = start
    while s < end:
        e = min(s + span, end)
        data = xl._get(f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks"
                       f"?start_ts={s}&end_ts={e}&period_interval={interval}")
        for c in (data or {}).get("candlesticks") or []:
            ts = c.get("end_period_ts")
            yb = (c.get("yes_bid") or {}).get("close_dollars")
            ya = (c.get("yes_ask") or {}).get("close_dollars")
            if ts is None or yb is None or ya is None:
                continue
            out[int(ts) // 60] = (xl._num(yb) * 100.0, xl._num(ya) * 100.0)
        s = e
    return out


def build_entry(market: dict, seq: list[tuple], close_min: int):
    """First point that clears the mmsell10 filter -> one entry, or None.

    seq is [(minute, yes_bid_c, yes_ask_c)] ascending. One entry per market, mirroring mmsell's
    one-position-per-ticker rule. Returns (entry, saw_band) so a zero-entry regime can be
    diagnosed as 'never cheap' vs 'cheap only outside the holding window'."""
    saw = False
    for i, (mn, yb, ya) in enumerate(seq):
        mid = (yb + ya) / 2.0
        htc = (close_min - mn) / 60.0
        if not (ms.BAND_LO_C <= mid < ms.BAND_HI_C and ya <= ms.MAX_YES_C):
            continue
        saw = True
        if not (ms.HTC_MIN_H <= htc <= ms.HTC_MAX_H):
            continue
        return {
            "ticker": market["ticker"], "series": market["series"],
            "regime": ms.regime(market["series"]),
            "event": ms.event_of(market["ticker"]),
            "close": market["close"],
            "close_date": _dt.datetime.utcfromtimestamp(market["close"]).date(),
            # Sell YES at the ASK: mmsell rests a BUY-NO at the no-bid == 100 - yes_ask.
            "entry_yes": ya,
            "settle_yes": 100.0 if market["result"] == "yes" else 0.0,
            "htc_at_entry": htc,
            "path": [((b + a) / 2.0, b, a) for _mn, b, a in seq[i + 1:]],
        }, saw
    return None, saw


def collect(series_list, args):
    """Replay every sampled settled market in `series_list`. Returns (entries, coverage rows)."""
    entries, coverage = [], []
    for s in sorted(series_list):
        rows = sf.fetch_settled_deep(s, args.hist_pages, args.min_vol,
                                     int(time.time()) - int(args.lookback_days * 86400))
        rows = [r for r in rows if r["result"] in ("yes", "no")]
        if not rows:
            coverage.append({"series": s, "regime": ms.regime(s), "settled": 0, "sampled": 0,
                             "candled": 0, "span_h": [], "min_mid": [], "entries": 0,
                             "saw_band": 0})
            continue
        step = max(1, len(rows) // args.sample)
        chosen = rows[::step][:args.sample]
        cov = {"series": s, "regime": ms.regime(s), "settled": len(rows),
               "sampled": len(chosen), "candled": 0, "span_h": [], "min_mid": [],
               "entries": 0, "saw_band": 0}
        for m in chosen:
            candles = fetch_candles(s, m["ticker"],
                                    m["close"] - int(args.path_days * 86400), m["close"] + 60,
                                    args.interval)
            if not candles:
                continue
            cov["candled"] += 1
            seq = sorted((mn, yb, ya) for mn, (yb, ya) in candles.items())
            close_min = m["close"] // 60
            cov["span_h"].append((close_min - seq[0][0]) / 60.0)
            cov["min_mid"].append(min((yb + ya) / 2.0 for _mn, yb, ya in seq))
            e, saw = build_entry(m, seq, close_min)
            cov["saw_band"] += 1 if saw else 0
            if e:
                cov["entries"] += 1
                entries.append(e)
            time.sleep(args.sleep)
        coverage.append(cov)
        print(f"  {s:22s} settled={cov['settled']:5d} sampled={cov['sampled']:4d} "
              f"candled={cov['candled']:4d} entries={cov['entries']:4d}")
    return entries, coverage


# ------------------------------------------------------------------ reports


def _med(xs):
    return statistics.median(xs) if xs else float("nan")


def report_coverage(coverage) -> set[str]:
    """Section 1. Returns the set of regimes that are MEASURABLE in mmsell's holding window."""
    print("\n=== 1) COVERAGE — is this regime measurable in mmsell's 1-336h window? ===")
    print("  Kalshi serves candles only for as long as a market was quoted. The crypto study was")
    print("  bounded to htc<1h this way while mmsell trades htc>=1h — a different trade wearing")
    print("  the same name. A regime whose median candle span is under 1h cannot be measured here.")
    by_reg: dict[str, list] = defaultdict(list)
    for c in coverage:
        by_reg[c["regime"]].append(c)
    rows, measurable = [], set()
    for reg in sorted(by_reg, key=lambda r: -sum(c["entries"] for c in by_reg[r])):
        cs_ = by_reg[reg]
        spans = [h for c in cs_ for h in c["span_h"]]
        mids = [m for c in cs_ for m in c["min_mid"]]
        sampled = sum(c["sampled"] for c in cs_)
        candled = sum(c["candled"] for c in cs_)
        ents = sum(c["entries"] for c in cs_)
        span_med = _med(spans)
        ok = candled >= 10 and span_med >= ms.HTC_MIN_H
        if ok:
            measurable.add(reg)
        rows.append([reg, len(cs_), sum(c["settled"] for c in cs_), sampled, candled,
                     f"{span_med:.1f}h" if spans else "-",
                     f"{_med(mids):.0f}c" if mids else "-", ents,
                     "ok" if ok else ("no-candles" if candled < 10 else "SPAN<1h")])
    _table(rows, ["regime", "series", "settled", "sampled", "candled", "med-span",
                  "cheapest", "entries", "verdict"],
           [11, 6, 8, 7, 7, 9, 8, 7, 11])
    print("  med-span = median hours of candle history available before close. cheapest = median")
    print("  of each market's lowest yes-mid, i.e. how close the regime gets to the entry band.")
    return measurable


def report_yield(entries, coverage, measurable) -> None:
    print("\n=== 2) BAND YIELD — how much of a regime's supply converts into an mmsell10 entry ===")
    by_reg: dict[str, list] = defaultdict(list)
    for c in coverage:
        by_reg[c["regime"]].append(c)
    ent_by_reg: dict[str, list] = defaultdict(list)
    for e in entries:
        ent_by_reg[e["regime"]].append(e)
    rows = []
    for reg in sorted(by_reg, key=lambda r: -len(ent_by_reg.get(r, []))):
        cs_ = by_reg[reg]
        candled = sum(c["candled"] for c in cs_)
        saw = sum(c["saw_band"] for c in cs_)
        es = ent_by_reg.get(reg, [])
        rows.append([
            reg, candled, saw, len(es),
            f"{100.0 * len(es) / candled:.1f}%" if candled else "-",
            f"{100.0 * (saw - len(es)) / candled:.1f}%" if candled else "-",
            f"{_med([e['htc_at_entry'] for e in es]):.1f}h" if es else "-",
            f"{_med([e['entry_yes'] for e in es]):.1f}c" if es else "-",
            "" if reg in measurable else "UNMEASURABLE",
        ])
    _table(rows, ["regime", "candled", "ever-cheap", "entries", "YIELD", "cheap-but-late",
                  "med-htc", "med-entry", "flag"],
           [11, 8, 11, 8, 7, 15, 8, 10, 12])
    print("  YIELD = entries / markets with candles — the conversion rate that turns the supply")
    print("  forecast's market counts into expected entries. 'cheap-but-late' reached the band")
    print("  only outside the 1-336h window, so the supply is real but untradeable as configured.")


def report_edge(entries, measurable) -> None:
    print("\n=== 3) REGIME EDGE — hold-to-settlement short, per regime (paper fill assumption) ===")
    by_reg: dict[str, list] = defaultdict(list)
    for e in entries:
        by_reg[e["regime"]].append(e)
    rows = []
    for reg in sorted(by_reg, key=lambda r: -len(by_reg[r])):
        es = by_reg[reg]
        pnls = [e["entry_yes"] - e["settle_yes"] - cs.fee_c(e["entry_yes"]) for e in es]
        st = cs._stats(pnls)
        rows.append([reg, st["n"], len({e["series"] for e in es}), f"{st['win']:.1f}%",
                     f"{st['mean']:+.2f}", f"{st['p5']:+.1f}", f"{st['total'] / 100:+.2f}",
                     "" if reg in measurable else "UNMEASURABLE"])
    if not rows:
        print("  (no entries — see the coverage table for why)")
        return
    _table(rows, ["regime", "n", "series", "win%", "mean_c", "p5_c", "total_$", "flag"],
           [11, 6, 6, 7, 7, 7, 8, 12])
    allp = [e["entry_yes"] - e["settle_yes"] - cs.fee_c(e["entry_yes"]) for e in entries]
    st = cs._stats(allp)
    print(f"    {'POOLED':>11} {st['n']:6d} {'':6} {st['win']:6.1f}% {st['mean']:+7.2f} "
          f"{st['p5']:+7.1f} {st['total'] / 100:+8.2f}")
    print("  Break-even needs the win rate to cover the loss-when-hit: at a 6c entry a single")
    print("  loss costs ~-94c, so ~94% wins is the floor. A regime under it is -EV no matter how")
    print("  much supply it offers. n is per-market, but markets inside one event share a driver —")
    print("  read n as closer to the event count in section 4 than to the raw row count.")


def report_stops(entries, measurable) -> None:
    print("\n=== 4a) ANCHOR MECHANIC — does the pre-registered bid stop help in each regime? ===")
    print("  Levels are A1/A2/A3's 12/20/30c, fixed in advance. The confirm here is ONE candle")
    print("  period; at --interval 60 that is ~2 mmsell cycles, matching the books' K=2 confirm.")
    print("  Crypto (continuous-path) liked the stop; sports losses are JUMPS (a goal is scored)")
    print("  and cannot be stopped out. This is the test of which shape each new regime has.")
    by_reg: dict[str, list] = defaultdict(list)
    for e in entries:
        by_reg[e["regime"]].append(e)
    rows = []
    for reg in sorted(by_reg, key=lambda r: -len(by_reg[r])):
        es = [e for e in by_reg[reg] if e["path"]]
        if len(es) < 10:
            continue
        hold = cs._stats([cs.replay_short(e["entry_yes"], e["settle_yes"], e["path"])[0]
                          for e in es])
        row = [reg, len(es), f"{hold['mean']:+.2f}", f"{hold['p5']:+.1f}"]
        for lvl in STOP_LEVELS:
            out = [cs.replay_short(e["entry_yes"], e["settle_yes"], e["path"],
                                   stop_l=lvl, stop_k=1, trigger="bid") for e in es]
            st = cs._stats([p for p, _ in out])
            ex = 100.0 * sum(1 for _p, k in out if k != "settle") / len(out)
            row += [f"{st['mean'] - hold['mean']:+.2f}", f"{st['p5'] - hold['p5']:+.0f}",
                    f"{ex:.0f}%"]
        rows.append(row)
    if not rows:
        print("  (no regime has 10+ entries with a forward path)")
        return
    heads = ["regime", "n", "hold_mean", "hold_p5"]
    widths = [11, 5, 9, 8]
    for lvl in STOP_LEVELS:
        heads += [f"d_m@{lvl}", f"d_p5@{lvl}", f"ex@{lvl}"]
        widths += [8, 9, 7]
    _table(rows, heads, widths)
    print("  d_m / d_p5 = change vs hold in mean and 5th-pctile tail; ex = % of positions exited.")
    print("  Anchor gate (docs/MMSELL_ANCHOR_SET.md): tail up AND mean >= hold - 0.3c.")


def report_correlation(entries) -> None:
    print("\n=== 4b) CORRELATED SETTLEMENT — is 'many small independent positions' true? ===")
    print("  Group entries by settlement DATE within a regime and count losses per date. Under")
    print("  independence the loss count on a date of size k is Binomial(k, p), whose variance is")
    print("  k*p*(1-p). Measured variance divided by that is the OVERDISPERSION factor: 1.0 means")
    print("  independent, >1 means positions on a date lose together. A per-settlement-date cap")
    print("  should be sized on this, not on intuition.")
    by_reg: dict[str, list] = defaultdict(list)
    for e in entries:
        by_reg[e["regime"]].append(e)
    rows = []
    for reg in sorted(by_reg, key=lambda r: -len(by_reg[r])):
        es = by_reg[reg]
        p = sum(1 for e in es if e["settle_yes"] > 50) / len(es)
        groups: dict[_dt.date, list] = defaultdict(list)
        for e in es:
            groups[e["close_date"]].append(e)
        sized = [(d, g) for d, g in groups.items() if len(g) >= 2]
        if len(sized) < 5 or p <= 0:
            continue
        obs, exp = [], []
        worst = 0.0
        for _d, g in sized:
            losses = sum(1 for e in g if e["settle_yes"] > 50)
            obs.append((losses - len(g) * p) ** 2)
            exp.append(len(g) * p * (1 - p))
            day = sum(e["entry_yes"] - e["settle_yes"] - cs.fee_c(e["entry_yes"]) for e in g)
            worst = min(worst, day)
        ratio = (sum(obs) / sum(exp)) if sum(exp) > 0 else float("nan")
        biggest = max(sized, key=lambda dg: len(dg[1]))
        rows.append([reg, len(sized), f"{statistics.fmean(len(g) for _d, g in sized):.1f}",
                     len(biggest[1]), f"{100.0 * p:.1f}%", f"{ratio:.2f}x",
                     f"{worst / 100:+.2f}",
                     "CLUSTERED" if ratio >= 1.5 else ""])
    if not rows:
        print("  (not enough multi-position settlement dates to measure)")
        return
    _table(rows, ["regime", "dates", "avg/date", "max/date", "loss%", "overdisp",
                  "worst-day_$", "flag"],
           [11, 6, 9, 9, 7, 9, 12, 10])
    print("  worst-day_$ = realized P&L of the single worst settlement date in the sample, at")
    print("  1 contract per market. Multiply by the live position size to read it as real money.")


def _table(rows, headers, widths):
    print("    " + " ".join(h.rjust(w) for h, w in zip(headers, widths, strict=False)))
    for r in rows:
        print("    " + " ".join(str(c).rjust(w) for c, w in zip(r, widths, strict=False)))


# ------------------------------------------------------------------ main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--regimes", default=",".join(DEFAULT_REGIMES),
                    help="comma list of regimes to backtest (see mmsell_seasonal.REGIMES)")
    ap.add_argument("--series", default="", help="explicit comma list of series (skips discovery)")
    ap.add_argument("--sample", type=int, default=120, help="settled markets per series")
    ap.add_argument("--hist-pages", type=int, default=3, help="settled pages per series")
    ap.add_argument("--lookback-days", type=float, default=420.0,
                    help="how far back to accept settled markets (covers a full prior season)")
    ap.add_argument("--path-days", type=float, default=14.0,
                    help="candle window before close (14 = the whole holding window)")
    ap.add_argument("--interval", type=int, default=60,
                    help="candle period in minutes: 60 spans 14 days in one request; 1 is the "
                         "fine pass and needs a small --sample")
    ap.add_argument("--min-vol", type=float, default=50.0)
    ap.add_argument("--sleep", type=float, default=0.0)
    ap.add_argument("--max-series", type=int, default=8, help="series per regime, busiest first")
    args = ap.parse_args(argv)

    want = {r.strip() for r in args.regimes.split(",") if r.strip()}
    if args.series:
        chosen = [s.strip().upper() for s in args.series.split(",") if s.strip()]
    else:
        print("=== mmsell REGIME BACKTEST — discovering series ...")
        known = sf.discover_series(sf.fetch_open_markets(20), 20)
        per_reg: dict[str, list[str]] = defaultdict(list)
        for s in sorted(known):
            if ms.regime(s) in want and not ms.skip_series(s):
                per_reg[ms.regime(s)].append(s)
        chosen = [s for reg in sorted(per_reg) for s in per_reg[reg][:args.max_series]]
    if not chosen:
        print("  (no series matched the requested regimes)")
        return 0

    print(f"=== mmsell REGIME BACKTEST — {len(chosen)} series across {len(want)} regimes, "
          f"{args.interval}-min candles over the last {args.path_days:.0f}d before close ===")
    print(f"  entry = mmsell10: yes-mid {ms.BAND_LO_C:.0f}-{ms.BAND_HI_C:.0f}c, yes-ask <= "
          f"{ms.MAX_YES_C:.0f}c (the maker sell price), htc {ms.HTC_MIN_H:.0f}-{ms.HTC_MAX_H:.0f}h")
    entries, coverage = collect(chosen, args)
    if not coverage:
        print("  (no settled history collected)")
        return 0

    measurable = report_coverage(coverage)
    report_yield(entries, coverage, measurable)
    if entries:
        report_edge(entries, measurable)
        report_stops(entries, measurable)
        report_correlation(entries)
    print("\n  Pair with scripts/mmsell_supply_forecast.py (how many trades each regime offers).")
    print("  Pre-registered per-regime hypotheses and gates: docs/MMSELL_SEASONAL_FORECAST.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
