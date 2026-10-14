"""KXRAIN season probe — is the MMSELL rain cell a calibration edge, and does it survive the season?

Thesis and gate: docs/KXRAIN_MMSELL_CELL.md. Read-only public Kalshi REST (no auth, no orders).
Stdlib only; runs on the ops runner (open egress). A browser UA clears Cloudflare's 1010.

What it measures, independent of which markets the bot happened to trade:
  1. LISTING HISTORY — every KXRAIN market Kalshi has listed (live + historical archive), counted
     by settlement month and city. This answers the first winter question directly: does a
     December–February tape exist at all, or does the series start in summer 2026?
  2. BASE RATE — the share of city-days that settled YES, by month and city (Kalshi's own
     settlement, so trace = 0 and missing = 0 are already applied).
  3. CALIBRATION OF THE MMSELL CELL — for each settled market, hourly candles at fixed hours
     before close. A market is "in cell" at hour h when it passes the mmsell10 entry rule:
     5 <= yes mid <= 10 and yes ask <= 7 (the `maxyes=7` ceiling on the price actually sold).
     Per cell: n, implied YES (the yes ask the seller receives), realized YES, and the
     hold-to-settlement P&L of buying NO at the no bid (= 100 - yes ask, the maker price the
     paper books assume) — by hour, month, city and price.
  4. ONE BET PER DAY — the same P&L grouped by settlement date (every city on one date is
     one bet, the scorecard's exposure key), so the standard error is not overstated.
  5. CITY RULES — `rules_primary` for one market per city, so the named station is on record.

Usage (via the ops channel):
    {"type":"script","name":"kxrain_season_probe","args":["--max-candle-markets","900"]}
    {"type":"script","name":"kxrain_season_probe","args":["--listing-only"]}
"""

from __future__ import annotations

import argparse
import calendar
import math
import time
from collections import defaultdict

import xvenue_leadlag as xl  # _get (browser-UA fetch with retry), _num

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = "KXRAIN"
HOURS = (1, 2, 3, 4, 6, 9, 12, 18, 24, 36, 48)
# mmsell10 entry rule (config `Gmmsell0`/`mmsell10`: lo=5, hi=10, maxyes=7).
LO, HI, MAXYES = 5.0, 10.0, 7.0


def _ts(iso: str) -> int:
    try:
        return calendar.timegm(time.strptime((iso or "")[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return 0


def _cents(side: dict | None) -> float | None:
    """Close price of one candle side in cents, across both API shapes (cents or dollars)."""
    if not side:
        return None
    if side.get("close") is not None:
        return xl._num(side.get("close"))
    if side.get("close_dollars") is not None:
        # Rounded: 100 * 0.07 is 7.000000000000001, which would fail the `ya <= 7` ceiling.
        return round(100.0 * xl._num(side.get("close_dollars")), 2)
    return None


def city_of(ticker: str) -> str:
    parts = ticker.split("-")
    return parts[2] if len(parts) > 2 else "?"


def fetch_markets(max_pages: int) -> list[dict]:
    """Every KXRAIN market from the live set and the historical archive, de-duplicated."""
    seen: dict[str, dict] = {}
    for base in (f"{KALSHI}/markets", f"{KALSHI}/historical/markets"):
        cursor = ""
        for _ in range(max_pages):
            page = xl._get(f"{base}?series_ticker={SERIES}&limit=1000&cursor={cursor}")
            mkts = (page or {}).get("markets") or []
            for m in mkts:
                tk = m.get("ticker") or ""
                if tk.startswith(SERIES + "-") and tk not in seen:
                    m["_source"] = "historical" if "historical" in base else "live"
                    seen[tk] = m
            cursor = (page or {}).get("cursor") or ""
            if not cursor or not mkts:
                break
    return list(seen.values())


def candles(ticker: str, start: int, end: int) -> list[dict]:
    for url in (
        f"{KALSHI}/series/{SERIES}/markets/{ticker}/candlesticks"
        f"?start_ts={start}&end_ts={end}&period_interval=60",
        f"{KALSHI}/historical/markets/{ticker}/candlesticks"
        f"?start_ts={start}&end_ts={end}&period_interval=60",
    ):
        data = xl._get(url)
        cs = (data or {}).get("candlesticks") or []
        if cs:
            return cs
    return []


def quote_at(cs: list[dict], target_ts: int, tol_s: int = 3 * 3600):
    """(yes_bid, yes_ask) of the last candle ending at or before target_ts, within tol. No lookahead."""
    best = None
    for c in cs:
        end = int(c.get("end_period_ts") or 0)
        if not end or end > target_ts or target_ts - end > tol_s:
            continue
        yb, ya = _cents(c.get("yes_bid")), _cents(c.get("yes_ask"))
        if yb is None or ya is None or not (0 < yb <= ya < 100):
            continue
        if best is None or end > best[0]:
            best = (end, yb, ya)
    return (best[1], best[2]) if best else None


def in_cell(yb: float, ya: float) -> bool:
    return LO <= (yb + ya) / 2.0 <= HI and ya <= MAXYES


def _row(label: str, rows: list[tuple]) -> str:
    """rows: (implied_yes_c, realized_yes_bool, pnl_c, day)"""
    n = len(rows)
    if not n:
        return f"    {label:<14} {0:>5}"
    imp = sum(r[0] for r in rows) / n
    real = 100.0 * sum(1 for r in rows if r[1]) / n
    pnl = [r[2] for r in rows]
    mean = sum(pnl) / n
    days = defaultdict(float)
    for r in rows:
        days[r[3]] += r[2]
    dv = list(days.values())
    dm = sum(dv) / len(dv)
    se = (math.sqrt(sum((x - dm) ** 2 for x in dv) / (len(dv) - 1)) / math.sqrt(len(dv))
          if len(dv) > 1 else float("nan"))
    losses = sum(1 for r in rows if r[1])
    return (f"    {label:<14} {n:>5} {len(dv):>5} {imp:>8.2f} {real:>8.2f} {losses:>6} "
            f"{mean:>+8.2f} {dm:>+9.2f} {se:>8.2f}")


HDR = (f"    {'':<14} {'n':>5} {'days':>5} {'impl_yes':>8} {'real_yes':>8} {'losses':>6} "
       f"{'c/ct':>8} {'c/day':>9} {'se/day':>8}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-pages", type=int, default=30)
    ap.add_argument("--max-candle-markets", type=int, default=900,
                    help="settled markets to price (newest first, stratified by month)")
    ap.add_argument("--listing-only", action="store_true")
    ap.add_argument("--rules", action="store_true", help="print rules_primary per city")
    args = ap.parse_args(argv)

    mkts = fetch_markets(args.max_pages)
    print(f"=== KXRAIN season probe — {len(mkts)} markets listed (live + historical) ===\n")
    by_month = defaultdict(lambda: defaultdict(int))
    cities = defaultdict(int)
    for m in mkts:
        close = _ts(m.get("close_time"))
        mon = time.strftime("%Y-%m", time.gmtime(close)) if close else "?"
        res = (m.get("result") or "").lower() or (m.get("status") or "?")
        by_month[mon][res] += 1
        by_month[mon]["_vol"] += int(xl._num(m.get("volume_fp") or m.get("volume")))
        cities[city_of(m.get("ticker") or "")] += 1
    print("  LISTING HISTORY by close month (result counts; vol = summed contract volume)")
    for mon in sorted(by_month):
        d = by_month[mon]
        yes, no = d.get("yes", 0), d.get("no", 0)
        other = sum(v for k, v in d.items() if k not in ("yes", "no", "_vol"))
        rate = f"{100.0 * yes / (yes + no):5.1f}%" if yes + no else "   - "
        print(f"    {mon}  yes={yes:<4} no={no:<4} other={other:<4} yes_rate={rate}  vol={d['_vol']}")
    print(f"\n  CITIES ({len(cities)}): " + ", ".join(f"{c}:{n}" for c, n in sorted(cities.items())))

    settled = [m for m in mkts if (m.get("result") or "").lower() in ("yes", "no")]
    print("\n  BASE RATE by city (settled; % of city-days YES)")
    bc = defaultdict(lambda: [0, 0])
    for m in settled:
        c = city_of(m["ticker"])
        bc[c][0] += m["result"].lower() == "yes"
        bc[c][1] += 1
    for c in sorted(bc):
        y, n = bc[c]
        print(f"    {c:<6} n={n:<4} yes_rate={100.0 * y / n:5.1f}%")

    if args.rules:
        print("\n  RULES (one market per city)")
        done = set()
        for m in sorted(mkts, key=lambda x: x.get("close_time") or "", reverse=True):
            c = city_of(m["ticker"])
            if c in done:
                continue
            done.add(c)
            print(f"    {c}: {(m.get('rules_primary') or '').strip()[:400]}")

    if args.listing_only:
        return 0

    # Stratify by close month so a long summer does not crowd out a short winter.
    by_mon = defaultdict(list)
    for m in sorted(settled, key=lambda x: x.get("close_time") or "", reverse=True):
        by_mon[time.strftime("%Y-%m", time.gmtime(_ts(m["close_time"])))].append(m)
    per = max(1, args.max_candle_markets // max(1, len(by_mon)))
    sample = [m for mon in sorted(by_mon) for m in by_mon[mon][:per]]
    print(f"\n  pricing {len(sample)} settled markets ({per}/month cap) at hours {list(HOURS)} "
          f"before close; cell = {LO:g}<=mid<={HI:g} and yes_ask<={MAXYES:g}\n")

    # rows[h] -> list of (implied_yes_c, realized_yes, pnl_c, day, city, month, ya)
    rows: dict[int, list] = defaultdict(list)
    first_entry: list[tuple] = []   # one row per market: its EARLIEST in-cell hour (what a scanner buys)
    priced = 0
    for m in sample:
        close = _ts(m["close_time"])
        opened = _ts(m.get("open_time")) or close - 72 * 3600
        cs = candles(m["ticker"], max(opened, close - (max(HOURS) + 4) * 3600), close)
        if not cs:
            continue
        priced += 1
        yes = m["result"].lower() == "yes"
        day = m["ticker"].split("-")[1]
        mon = time.strftime("%Y-%m", time.gmtime(close))
        earliest = None
        for h in HOURS:
            q = quote_at(cs, close - h * 3600)
            if q is None or not in_cell(*q):
                continue
            ya = q[1]
            pnl = (-(100.0 - ya)) if yes else ya   # buy NO at no_bid = 100 - ya, hold
            r = (ya, yes, pnl, day, city_of(m["ticker"]), mon, ya)
            rows[h].append(r)
            earliest = (h, r)
        if earliest:
            first_entry.append(earliest)

    print(f"  candles found for {priced}/{len(sample)} markets\n")
    print("  A. CELL CALIBRATION by hours before close (each market can appear at several hours)")
    print(HDR)
    for h in HOURS:
        print(_row(f"T-{h}h", rows[h]))

    fe = [r for _h, r in first_entry]
    print("\n  B. FIRST IN-CELL QUOTE per market (the entry a scanner takes) — by month")
    print(HDR)
    bym = defaultdict(list)
    for r in fe:
        bym[r[5]].append(r)
    for mon in sorted(bym):
        print(_row(mon, bym[mon]))
    print(_row("ALL", fe))

    print("\n  C. FIRST IN-CELL QUOTE — by city")
    print(HDR)
    byc = defaultdict(list)
    for r in fe:
        byc[r[4]].append(r)
    for c in sorted(byc):
        print(_row(c, byc[c]))

    print("\n  D. FIRST IN-CELL QUOTE — by yes ask (price sold)")
    print(HDR)
    byp = defaultdict(list)
    for r in fe:
        byp[f"ya={int(r[6])}"].append(r)
    for k in sorted(byp, key=lambda s: int(s.split('=')[1])):
        print(_row(k, byp[k]))

    print("\n  E. FIRST IN-CELL QUOTE — by entry hour band")
    print(HDR)
    byh = defaultdict(list)
    for h, r in first_entry:
        byh["<=4h" if h <= 4 else "6-12h" if h <= 12 else "18-24h" if h <= 24 else ">24h"].append(r)
    for k in ("<=4h", "6-12h", "18-24h", ">24h"):
        print(_row(k, byh.get(k, [])))

    print("\n  Losing markets (first in-cell entry settled YES):")
    for h, r in first_entry:
        if r[1]:
            print(f"    {r[3]} {r[4]:<6} entry T-{h}h ya={r[6]:.0f}")
    print("\n  Notes: P&L is gross of fees, one contract, at the maker price the paper books assume "
          "(no fill model). Hourly candles: quotes are the last hourly close at or before the hour.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
