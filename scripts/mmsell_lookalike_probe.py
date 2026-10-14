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


def candles(series: str, ticker: str, start: int, end: int) -> list[dict]:
    for url in (
        f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks"
        f"?start_ts={start}&end_ts={end}&period_interval=60",
        f"{KALSHI}/historical/markets/{ticker}/candlesticks"
        f"?start_ts={start}&end_ts={end}&period_interval=60",
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


def first_entry(cs: list[dict], close: int, lo: float, hi: float, maxyes: float):
    """The earliest pre-close hour at which the market passes the cell: (hour, yes_ask)."""
    for h in sorted(HOURS, reverse=True):
        q = quote_at(cs, close - h * 3600)
        if q and lo <= (q[0] + q[1]) / 2.0 <= hi and q[1] <= maxyes:
            return h, q[1]
    return None


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


def verdict(s: dict) -> str:
    if not s.get("n"):
        return "NO-DATA"
    if s["n"] >= MIN_ENTRIES and s["cents"] <= 0:
        return "KILL"
    share_ok = s["months_counted"] and s["months_positive"] / s["months_counted"] >= MONTH_SHARE
    if (s["n"] >= MIN_ENTRIES and s["days"] >= MIN_DAYS and s["cents"] >= MIN_CENTS
            and s["t"] == s["t"] and s["t"] >= MIN_T and share_ok):
        return "PROMOTE"
    return "HOLD"


def line(label: str, s: dict) -> str:
    if not s.get("n"):
        return f"  {label:<22} {0:>5}"
    return (f"  {label:<22} {s['n']:>5} {s['days']:>5} {s['implied']:>7.2f} {s['realized']:>7.2f} "
            f"{s['losses']:>5} {s['cents']:>+7.2f} {s['per_day']:>+8.2f} {s['t']:>6.2f} "
            f"{s['months_positive']}/{s['months_counted']:<3} {verdict(s)}")


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

    per_series: dict[str, list] = {}
    print(f"  {'series':<22} {'listed':>6} {'settled':>7} {'span':>17} {'priced':>6}")
    for s in series:
        mkts = fetch_markets(s, args.max_pages)
        settled = [m for m in mkts if (m.get("result") or "").lower() in ("yes", "no")]
        by_mon = defaultdict(list)
        for m in sorted(settled, key=lambda x: x.get("close_time") or "", reverse=True):
            by_mon[time.strftime("%Y-%m", time.gmtime(_ts(m.get("close_time"))))].append(m)
        cap = max(1, args.per_series // max(1, len(by_mon)))
        sample = [m for mon in sorted(by_mon) for m in by_mon[mon][:cap]]
        rows, priced = [], 0
        for m in sample:
            close = _ts(m.get("close_time"))
            opened = _ts(m.get("open_time")) or close - 72 * 3600
            cs = candles(s, m["ticker"], max(opened, close - (max(HOURS) + 4) * 3600), close)
            if not cs:
                continue
            priced += 1
            hit = first_entry(cs, close, args.lo, args.hi, args.maxyes)
            if not hit:
                continue
            ya = hit[1]
            yes = m["result"].lower() == "yes"
            day = time.strftime("%Y-%m-%d", time.gmtime(close))
            rows.append((ya, yes, -(100.0 - ya) if yes else ya, day, day[:7]))
        per_series[s] = rows
        span = f"{min(by_mon)}..{max(by_mon)}" if by_mon else "-"
        print(f"  {s:<22} {len(mkts):>6} {len(settled):>7} {span:>17} {priced:>6}")

    print("\n  A. PER SERIES (first in-cell entry)")
    print(HDR)
    for s in series:
        print(line(s, summarize(per_series[s])))

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
        print(line(fam, summary))

    print("\n  C. PER FAMILY BY MONTH (n, c/ct)")
    for fam in sorted(families):
        summary = summarize(families[fam])
        if summary.get("n"):
            cells = ", ".join(f"{m}: {n} @ {c:+.1f}" for m, (n, c) in summary["by_month"].items())
            print(f"  {fam:<22} {cells}")

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
