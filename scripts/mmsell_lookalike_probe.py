"""MMSELL look-alike probe — does the rain cell's edge exist in series MMSELL has not traded?

Thesis and pre-registered gate: docs/MMSELL_LOOKALIKE_PROBE.md. Read-only public Kalshi REST
(no auth, no orders). Stdlib only; runs on the ops runner (open egress).

The rain cell (docs/KXRAIN_MMSELL_CELL.md) sells cheap YES on a measured-quantity threshold
ladder: a station reading, settled once a day. This probe replays the same mmsell10 entry rule
on historical prices for other series and reports, per series and per family:

  1. LISTING — markets listed (live + historical archive), settled, close-month span.
  2. FIRST IN-CELL ENTRY — for each settled market, hourly candles at fixed hours before close;
     the earliest hour that passes the cell (lo <= yes mid <= hi and yes ask <= maxyes) is the
     entry a scanner takes. P&L holds to settlement after buying NO at the no bid (100 - yes
     ask), gross of fees, one contract — the same convention as kxrain_season_probe.
  3. ONE BET PER FAMILY-DAY — every market of a family (e.g. every KXHIGH city) closing on one
     UTC date is one bet, so the standard error is not overstated by correlated cities.
  4. GATE — the pre-registered PROMOTE / HOLD / KILL reading per family, with KXRAIN as the
     positive control that must reproduce for the run to count.

Usage (via the ops channel):
    {"type":"script","name":"mmsell_lookalike_probe","args":["--category","Climate and Weather"]}
    {"type":"script","name":"mmsell_lookalike_probe","args":["--series","KXRAIN,KXHIGHNY"]}
    {"type":"script","name":"mmsell_lookalike_probe","args":["--series","KXRAIN,KXHIGHNY","--timing"]}

`--timing` (option B, 2026-10-15) adds the ENTRY-WINDOW read: the same markets replayed under
"enter only early (T-48..T-24h)", "only mid (T-24..T-6h)" and "only late (T-6..T-1h)", each
compared with the shipped first-entry rule day by day. Every run also prints LIFETIME: the share
of settled markets that had a quote at least 1h before close, which is the bot's global entry
floor (`mmsell_min_hours_to_close`) — a series listed later than that cannot be traded by any
book without changing the floor (option A, hourly temperature).
"""

from __future__ import annotations

import argparse
import calendar
import math
import time
import urllib.parse
from collections import defaultdict

import xvenue_leadlag as xl  # _get (browser-UA fetch with retry), _num

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
HOURS = (1, 2, 3, 4, 6, 9, 12, 18, 24, 36, 48)
#: Entry windows for `--timing`, in hours before close (exclusive low, inclusive high), and the
#: pre-registered reading against the shipped first-entry rule (docs/MMSELL_LOOKALIKE_PROBE.md
#: section 6): BETTER/WORSE needs a day-paired difference of at least 1c with |t| >= 2 on
#: >= 30 shared family-days; anything else is SAME.
WINDOWS = (("early", 24, 48), ("mid", 6, 24), ("late", 0, 6))
TIMING_CENTS, TIMING_T, TIMING_DAYS = 1.0, 2.0, 30
#: Option A feasibility: at least this share of settled markets must carry a quote >= 1h before
#: close, or the series is untradeable under the global entry floor whatever its edge.
FEASIBLE_SHARE = 0.5
# Families pool per-city series into one bet per day. Longest prefix wins.
FAMILIES = ("KXRAINWKND", "KXRAIN", "KXHIGHT", "KXHIGH", "KXLOWT", "KXLOW", "KXSNOW",
            "KXTEMP", "KXHMONTH", "KXHURR", "KXTORNADO", "KXWIND")
CONTROL = "KXRAIN"
# Pre-registered gate (docs/MMSELL_LOOKALIKE_PROBE.md). Gross cents per contract.
MIN_ENTRIES, MIN_DAYS, MIN_CENTS, MIN_T, MONTH_SHARE = 100, 30, 2.0, 2.5, 2 / 3


def _ts(iso: str) -> int:
    try:
        return calendar.timegm(time.strptime((iso or "")[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return 0


def _price(value) -> float | None:
    """One price in cents: integer or float cents, or a dollar string ("0.05" -> 5.0). The live
    endpoint returns cents; the historical archive returns dollar strings (same rule as
    kalshi_bot.scanner.metrics.price_to_cents)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return round(100.0 * float(value), 2)
    except (TypeError, ValueError):
        return None


def _close(candle: dict, base: str) -> float | None:
    """Close of one candle side across the API's shapes: nested {close|close_dollars}, a nested
    `<base>_dollars` object, or flat `<base>_close[_dollars]` keys."""
    node = candle.get(base) or candle.get(f"{base}_dollars")
    if isinstance(node, dict):
        if node.get("close") is not None:
            return _price(node["close"])
        if node.get("close_dollars") is not None:
            return _price(str(node["close_dollars"]))
        return None
    for key in (f"{base}_close", f"{base}_close_dollars"):
        if candle.get(key) is not None:
            value = candle[key]
            return _price(str(value) if key.endswith("_dollars") else value)
    return None


def family_of(series: str) -> str:
    for prefix in sorted(FAMILIES, key=len, reverse=True):
        if series.startswith(prefix):
            return prefix
    return series


def discover(category: str, prefixes: tuple[str, ...]) -> list[str]:
    """Series tickers in a Kalshi category, optionally narrowed to ticker prefixes."""
    found: list[str] = []
    cursor = ""
    for _ in range(20):
        query = urllib.parse.urlencode({"category": category, "cursor": cursor})
        page = xl._get(f"{KALSHI}/series?{query}") or {}
        for s in page.get("series") or []:
            ticker = s.get("ticker") or ""
            if ticker and (not prefixes or ticker.startswith(prefixes)):
                found.append(ticker)
        cursor = page.get("cursor") or ""
        if not cursor:
            break
    return sorted(set(found))


def fetch_markets(series: str, max_pages: int) -> list[dict]:
    seen: dict[str, dict] = {}
    for base in (f"{KALSHI}/markets", f"{KALSHI}/historical/markets"):
        cursor = ""
        for _ in range(max_pages):
            page = xl._get(f"{base}?series_ticker={series}&limit=1000&cursor={cursor}") or {}
            mkts = page.get("markets") or []
            for m in mkts:
                tk = m.get("ticker") or ""
                if tk.startswith(series + "-") and tk not in seen:
                    seen[tk] = m
            cursor = page.get("cursor") or ""
            if not cursor or not mkts:
                break
    return list(seen.values())


def candles(series: str, ticker: str, start: int, end: int, period: int = 60) -> list[dict]:
    """Candles at `period` minutes (60 = hourly; 1 = the `--minutes` last-hour mode)."""
    for url in (
        f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks"
        f"?start_ts={start}&end_ts={end}&period_interval={period}",
        f"{KALSHI}/historical/markets/{ticker}/candlesticks"
        f"?start_ts={start}&end_ts={end}&period_interval={period}",
    ):
        cs = (xl._get(url) or {}).get("candlesticks") or []
        if cs:
            return cs
    return []


def quote_at(cs: list[dict], target_ts: int, tol_s: int = 3 * 3600):
    """(yes_bid, yes_ask) of the last candle ending at or before target_ts. No lookahead."""
    best = None
    for c in cs:
        end = int(c.get("end_period_ts") or 0)
        if not end or end > target_ts or target_ts - end > tol_s:
            continue
        yb, ya = _close(c, "yes_bid"), _close(c, "yes_ask")
        if yb is None or ya is None or not (0 < yb <= ya < 100):
            continue
        if best is None or end > best[0]:
            best = (end, yb, ya)
    return (best[1], best[2]) if best else None


def first_entry(cs: list[dict], close: int, lo: float, hi: float, maxyes: float,
                window: tuple[float, float] | None = None,
                offsets: tuple[float, ...] = HOURS, tol_s: int = 3 * 3600):
    """The earliest pre-close offset (hours) at which the market passes the cell:
    (hours, yes_ask). `window` = (low, high] restricts the offsets considered — an entry policy
    that only buys in that window, not a filter applied after the fact. `offsets`/`tol_s` default
    to the hourly replay; `--minutes` passes fractions of an hour and a minutes-scale tolerance."""
    for h in sorted(offsets, reverse=True):
        if window is not None and not (window[0] < h <= window[1]):
            continue
        q = quote_at(cs, close - int(round(h * 3600)), tol_s)
        if q and lo <= (q[0] + q[1]) / 2.0 <= hi and q[1] <= maxyes:
            return h, q[1]
    return None


def _row(yes_ask: float, yes: bool, day: str) -> tuple:
    """(yes_ask, settled_yes, pnl_c, day, month) for a maker NO bought at 100 - yes_ask."""
    return (yes_ask, yes, -(100.0 - yes_ask) if yes else yes_ask, day, day[:7])


def has_quote_by(cs: list[dict], close: int, hours: float = 1.0) -> bool:
    """Did the market carry a two-sided quote at or before `hours` before close? (feasibility)"""
    return quote_at(cs, close - int(hours * 3600), tol_s=10 ** 9) is not None


def day_paired(policy: list[tuple], base: list[tuple]) -> dict:
    """Day-paired difference of mean c/contract, policy minus base, over the family-days on which
    both policies placed a bet. One observation per day, so correlated cities are one bet."""
    def by_day(rows):
        acc = defaultdict(list)
        for r in rows:
            acc[r[3]].append(r[2])
        return {d: sum(v) / len(v) for d, v in acc.items()}
    p, b = by_day(policy), by_day(base)
    diffs = [p[d] - b[d] for d in p.keys() & b.keys()]
    n = len(diffs)
    if n < 2:
        return {"days": n, "diff": float("nan"), "t": float("nan")}
    m = sum(diffs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in diffs) / (n - 1))
    return {"days": n, "diff": m, "t": m / (sd / math.sqrt(n)) if sd > 0 else float("nan")}


def timing_reading(d: dict) -> str:
    if d["days"] < TIMING_DAYS or d["t"] != d["t"]:
        return "THIN"
    if d["diff"] >= TIMING_CENTS and d["t"] >= TIMING_T:
        return "BETTER"
    if d["diff"] <= -TIMING_CENTS and d["t"] <= -TIMING_T:
        return "WORSE"
    return "SAME"


def summarize(rows: list[tuple]) -> dict:
    """rows: (yes_ask, settled_yes, pnl_c, day, month)."""
    n = len(rows)
    if not n:
        return {"n": 0}
    days = defaultdict(float)
    for r in rows:
        days[r[3]] += r[2]
    dv = list(days.values())
    dm = sum(dv) / len(dv)
    se = (math.sqrt(sum((x - dm) ** 2 for x in dv) / (len(dv) - 1)) / math.sqrt(len(dv))
          if len(dv) > 1 else float("nan"))
    months = defaultdict(list)
    for r in rows:
        months[r[4]].append(r[2])
    counted = [v for v in months.values() if len(v) >= 10]
    return {
        "n": n,
        "days": len(dv),
        "implied": sum(r[0] for r in rows) / n,
        "realized": 100.0 * sum(1 for r in rows if r[1]) / n,
        "losses": sum(1 for r in rows if r[1]),
        "cents": sum(r[2] for r in rows) / n,
        "per_day": dm,
        "t": dm / se if se and se == se and se > 0 else float("nan"),
        "months_counted": len(counted),
        "months_positive": sum(1 for v in counted if sum(v) > 0),
        "by_month": {m: (len(v), sum(v) / len(v)) for m, v in sorted(months.items())},
    }


def verdict(s: dict, min_cents: float | None = None) -> str:
    """The §3 gate. `min_cents` raises the per-contract bar where a run pre-registered a stricter
    one (`--min-cents`; the last-hour mode, §9)."""
    bar = MIN_CENTS if min_cents is None else min_cents
    if not s.get("n"):
        return "NO-DATA"
    if s["n"] >= MIN_ENTRIES and s["cents"] <= 0:
        return "KILL"
    share_ok = s["months_counted"] and s["months_positive"] / s["months_counted"] >= MONTH_SHARE
    if (s["n"] >= MIN_ENTRIES and s["days"] >= MIN_DAYS and s["cents"] >= bar
            and s["t"] == s["t"] and s["t"] >= MIN_T and share_ok):
        return "PROMOTE"
    return "HOLD"


def line(label: str, s: dict, min_cents: float | None = None) -> str:
    if not s.get("n"):
        return f"  {label:<22} {0:>5}"
    return (f"  {label:<22} {s['n']:>5} {s['days']:>5} {s['implied']:>7.2f} {s['realized']:>7.2f} "
            f"{s['losses']:>5} {s['cents']:>+7.2f} {s['per_day']:>+8.2f} {s['t']:>6.2f} "
            f"{s['months_positive']}/{s['months_counted']:<3} {verdict(s, min_cents)}")


HDR = (f"  {'':<22} {'n':>5} {'days':>5} {'impl':>7} {'real':>7} {'loss':>5} {'c/ct':>7} "
       f"{'c/day':>8} {'t':>6} {'mo+':>5} verdict")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--series", default="", help="comma list; skips category discovery")
    ap.add_argument("--category", default="Climate and Weather")
    ap.add_argument("--prefixes", default="", help="comma list of ticker prefixes to keep")
    ap.add_argument("--max-series", type=int, default=60)
    ap.add_argument("--per-series", type=int, default=90,
                    help="settled markets priced per series (newest first, stratified by month)")
    ap.add_argument("--max-pages", type=int, default=10)
    ap.add_argument("--lo", type=float, default=5.0)
    ap.add_argument("--hi", type=float, default=10.0)
    ap.add_argument("--maxyes", type=float, default=7.0)
    ap.add_argument("--no-control", action="store_true", help="do not add KXRAIN")
    ap.add_argument("--minutes", default="",
                    help="comma list of MINUTES before close: replay the final hour on 1-minute "
                         "candles instead of the hourly offsets (docs §9)")
    ap.add_argument("--min-cents", type=float, default=None,
                    help="stricter per-contract PROMOTE bar for this run (pre-registered)")
    ap.add_argument("--timing", action="store_true",
                    help="also replay early/mid/late entry windows against the first-entry rule")
    args = ap.parse_args(argv)

    if args.series:
        series = [s.strip() for s in args.series.split(",") if s.strip()]
    else:
        prefixes = tuple(p.strip() for p in args.prefixes.split(",") if p.strip())
        series = discover(args.category, prefixes)
    if not args.no_control and CONTROL not in series:
        series.append(CONTROL)
    series = series[: args.max_series] + ([CONTROL] if CONTROL in series[args.max_series:] else [])
    print(f"=== MMSELL look-alike probe — {len(series)} series; cell {args.lo:g}<=mid<={args.hi:g}"
          f", yes_ask<={args.maxyes:g}; first in-cell entry, gross, 1 contract ===\n")

    if args.minutes:
        offsets = tuple(sorted(float(m) / 60.0 for m in args.minutes.split(",") if m.strip()))
        period, tol_s, floor_h = 1, 10 * 60, max(offsets)
        print(f"  LAST-HOUR MODE: 1-minute candles, entries at {args.minutes} min before close; "
              f"LIFETIME below reads 'quoted >= {max(offsets) * 60:g} min before close'\n")
    else:
        offsets, period, tol_s, floor_h = HOURS, 60, 3 * 3600, 1.0
    per_series: dict[str, list] = {}
    per_window: dict[str, dict[str, list]] = {w[0]: {} for w in WINDOWS}
    feasible: dict[str, tuple[int, int]] = {}
    print(f"  {'series':<22} {'listed':>6} {'settled':>7} {'span':>17} {'priced':>6} "
          f"{'>=1h':>6}")
    for s in series:
        mkts = fetch_markets(s, args.max_pages)
        settled = [m for m in mkts if (m.get("result") or "").lower() in ("yes", "no")]
        by_mon = defaultdict(list)
        for m in sorted(settled, key=lambda x: x.get("close_time") or "", reverse=True):
            by_mon[time.strftime("%Y-%m", time.gmtime(_ts(m.get("close_time"))))].append(m)
        cap = max(1, args.per_series // max(1, len(by_mon)))
        sample = [m for mon in sorted(by_mon) for m in by_mon[mon][:cap]]
        rows, priced, early_enough = [], 0, 0
        wrows: dict[str, list] = {w[0]: [] for w in WINDOWS}
        for m in sample:
            close = _ts(m.get("close_time"))
            opened = _ts(m.get("open_time")) or close - 72 * 3600
            cs = candles(s, m["ticker"],
                         max(opened, close - int((max(offsets) + (0.5 if args.minutes else 4))
                                                 * 3600)), close, period)
            if not cs:
                continue
            priced += 1
            early_enough += has_quote_by(cs, close, floor_h)
            yes = m["result"].lower() == "yes"
            day = time.strftime("%Y-%m-%d", time.gmtime(close))

            hit = first_entry(cs, close, args.lo, args.hi, args.maxyes,
                              offsets=offsets, tol_s=tol_s)
            if hit:
                rows.append(_row(hit[1], yes, day))
            if args.timing:
                for name, low, high in WINDOWS:
                    whit = first_entry(cs, close, args.lo, args.hi, args.maxyes, (low, high),
                                       offsets=offsets, tol_s=tol_s)
                    if whit:
                        wrows[name].append(_row(whit[1], yes, day))
        per_series[s] = rows
        for name in wrows:
            per_window[name][s] = wrows[name]
        feasible[s] = (early_enough, priced)
        span = f"{min(by_mon)}..{max(by_mon)}" if by_mon else "-"
        share = f"{early_enough / priced:.0%}" if priced else "-"
        print(f"  {s:<22} {len(mkts):>6} {len(settled):>7} {span:>17} {priced:>6} {share:>6}")

    print("\n  A. PER SERIES (first in-cell entry)")
    print(HDR)
    for s in series:
        print(line(s, summarize(per_series[s]), args.min_cents))

    families: dict[str, list] = defaultdict(list)
    for s, rows in per_series.items():
        fam = family_of(s)
        # A family-day is one bet: re-key the day with the family so cities pool on one date.
        families[fam].extend((r[0], r[1], r[2], f"{fam}:{r[3]}", r[4]) for r in rows)
    print("\n  B. PER FAMILY (one bet per family-day) — the pre-registered gate reads here")
    print(HDR)
    control = None
    for fam in sorted(families, key=lambda f: -len(families[f])):
        summary = summarize(families[fam])
        if fam == CONTROL:
            control = summary
        print(line(fam, summary, args.min_cents))

    print("\n  C. PER FAMILY BY MONTH (n, c/ct)")
    for fam in sorted(families):
        summary = summarize(families[fam])
        if summary.get("n"):
            cells = ", ".join(f"{m}: {n} @ {c:+.1f}" for m, (n, c) in summary["by_month"].items())
            print(f"  {fam:<22} {cells}")

    print(f"\n  D. LIFETIME — share of priced markets quoted >= 1h before close (the bot's entry "
          f"floor); below {FEASIBLE_SHARE:.0%} a family is untradeable without a global change")
    fam_feas: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for s, (ok, n) in feasible.items():
        fam_feas[family_of(s)][0] += ok
        fam_feas[family_of(s)][1] += n
    for fam, (ok, n) in sorted(fam_feas.items()):
        share = ok / n if n else 0.0
        print(f"  {fam:<22} {ok:>5}/{n:<5} {share:>6.0%} "
              f"{'FEASIBLE' if n and share >= FEASIBLE_SHARE else 'NOT FEASIBLE'}")

    if args.timing:
        print("\n  E. TIMING — entry window vs the first-entry rule, day-paired per family "
              f"(BETTER/WORSE: |diff| >= {TIMING_CENTS:g}c, |t| >= {TIMING_T:g}, "
              f">= {TIMING_DAYS} shared days)")
        print(HDR)
        for fam in sorted(families, key=lambda f: -len(families[f])):
            print(line(f"{fam} first", summarize(families[fam])))
            for name, _low, _high in WINDOWS:
                wf = [(r[0], r[1], r[2], f"{fam}:{r[3]}", r[4])
                      for s, rows in per_window[name].items() if family_of(s) == fam
                      for r in rows]
                d = day_paired(wf, families[fam])
                print(line(f"{fam} {name}", summarize(wf))
                      + f"   vs first: {d['diff']:+.2f}c t {d['t']:.2f} on {d['days']} days "
                        f"-> {timing_reading(d)}")

    valid = bool(control and control.get("n") and control["cents"] >= MIN_CENTS
                 and control["t"] == control["t"] and control["t"] >= 2.0)
    print(f"\n  CONTROL {CONTROL}: {'REPRODUCED' if valid else 'NOT REPRODUCED'} "
          f"(needs >= +{MIN_CENTS:g} c/ct and t >= 2). "
          + ("Gate readings stand." if valid else "Run does not count; gate readings are void."))
    print("\n  Notes: gross of fees, maker price the paper books assume (no fill model); hourly "
          "candles; a series MMSELL skips (KXHIGH, KXLOW) has no paper evidence to compare.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
