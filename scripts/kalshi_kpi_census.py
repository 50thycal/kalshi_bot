"""EARNBEAT recon census — do Kalshi's company-KPI markets exist in gradeable numbers, and do
their pre-report prices show the consensus-anchoring bias the thesis claims?
(idea-model run 2026-09-12, docs/EARNBEAT_THESIS.md)

WHY THIS CENSUS EXISTS
----------------------
Kalshi's Public Companies Hub (launched 2026-08-04; KPI lines set from Fiscal.ai data, earnings
calendar from Benzinga) lists ladders on individual reported metrics — revenue, EPS,
subscribers, deliveries — that settle systematically off the filing. The thesis: retail anchors
the ladder's centre on the published consensus number, but reported results beat consensus
far more often than they miss (FactSet: 87% of S&P 500 EPS beats in Q2 2026; 76–78% over the
5/10-year average), because companies guide low. If the "above consensus" rung trades near
50¢, it is structurally underpriced.

This is a NEW venue (~5 weeks old at the time of writing), so by the idea-model's own rule it
is HOLD-by-default until a settled census shows a gradeable tape. This script IS that census.
It does not know the consensus number (that is thesis-stage external data); it measures the
general shape — how many KPI markets have settled, at what volume, and whether the YES rate
exceeds the price in the middle bands where an anchoring bias would show.

WHAT IT MEASURES
----------------
  C1 STRUCTURE — settled/open markets classified: kpi_threshold ("above/at least X"),
     kpi_range ("between X and Y"), mention (earnings-call word markets), other. Matched
     (series [category] → count) is dumped so contamination is visible, not silent.
  C2 CAPACITY — settled count + volume, distinct series (≈ companies), settles per ISO week
     (accrual rate → when the thesis n-floor is reachable).
  C3 CALIBRATION PRE-READ — for settled kpi_threshold markets with a result: the yes_ask/yes_bid
     mid from hourly candles at close−7d AND close−48h, bucketed by price band → n, mean price,
     realized YES rate, gap. Two horizons because close_time may fall AFTER the report: a large
     jump between the 7d and 48h reads flags post-release contamination, which the full probe
     must resolve against the earnings-calendar date (Benzinga) — never against close_time.

Verdict: TESTABLE-NOW if settled kpi_threshold markets with volume clear the floor and the
calibration sample is readable; HOLD (accrual) otherwise, naming the next earnings season as
the trigger. Read-only public Kalshi REST, stdlib only. Usage:
    {"type":"script","name":"kalshi_kpi_census","args":["--max-event-pages","80"],"id":"kpi-0"}
"""

from __future__ import annotations

import argparse
import re
import time
from collections import defaultdict
from datetime import datetime, timezone

import xvenue_leadlag as xl  # _get (browser UA + retries), _num

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

KPI_SERIES = re.compile(r"^KX(KPI|EARN|EPS|REV|REVENUE|SUBS|DELIVER|MENTION|EARNINGSMENTION)",
                        re.I)
KPI_CATEGORY = re.compile(r"compan|financ|business|earnings", re.I)
KPI_TITLE = re.compile(r"\b(revenue|eps|earnings per share|net income|subscribers?|deliveries|"
                       r"net adds?|gross margin|operating income|free cash flow|daily active|"
                       r"monthly active|units? (sold|shipped)|guidance|quarter(ly)? results?|"
                       r"q[1-4] (20\d\d )?(revenue|eps|earnings))\b", re.I)
MENTION_TITLE = re.compile(r"\b(mention|say|said|call)\b", re.I)
RANGE_TITLE = re.compile(r"\bbetween\b", re.I)
THRESH_TITLE = re.compile(r"\b(above|below|at least|more than|over|under|exceed|greater|"
                          r"less than|or more|or fewer)\b|[<>≥≤]", re.I)

BANDS = [(0, 10), (10, 30), (30, 50), (50, 70), (70, 90), (90, 101)]
SETTLED_FLOOR = 100      # settled kpi_threshold markets with volume
CALIB_FLOOR = 60         # markets with a readable pre-report quote


def _ts(iso: str | None) -> float:
    if not iso:
        return 0.0
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def _vol(m: dict) -> float:
    return xl._num(m.get("volume_fp")) or xl._num(m.get("volume")) or 0.0


def is_kpi(ev: dict, m: dict) -> bool:
    series = m.get("series_ticker") or ev.get("series_ticker") or ""
    cat = ev.get("category") or ""
    text = " ".join(filter(None, [ev.get("title"), ev.get("sub_title"), m.get("title"),
                                  m.get("subtitle")]))
    if KPI_SERIES.search(series):
        return True
    # category alone is too broad; require a KPI token with it (precision over recall).
    return bool(KPI_CATEGORY.search(cat) and (KPI_TITLE.search(text) or MENTION_TITLE.search(text)))


def kpi_type_of(text: str) -> str:
    t = text or ""
    if MENTION_TITLE.search(t) and not KPI_TITLE.search(t):
        return "mention"
    if RANGE_TITLE.search(t):
        return "kpi_range"
    if THRESH_TITLE.search(t) or KPI_TITLE.search(t):
        return "kpi_threshold"
    return "other"


def band_of(price_cents: float) -> str:
    for lo, hi in BANDS:
        if lo <= price_cents < hi:
            return f"{lo:02d}-{min(hi, 100):02d}"
    return "?"


def events(status: str, max_pages: int) -> list[dict]:
    out, cursor = [], ""
    for _ in range(max_pages):
        page = xl._get(f"{KALSHI}/events?status={status}&with_nested_markets=true"
                       f"&limit=200&cursor={cursor}")
        evs = (page or {}).get("events") or []
        out.extend(evs)
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not evs:
            break
        time.sleep(0.05)
    return out


def hourly_candles(series: str, ticker: str, start: int, end: int) -> list[dict]:
    data = xl._get(f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks"
                   f"?start_ts={start}&end_ts={end}&period_interval=60")
    return (data or {}).get("candlesticks") or []


def quote_at_or_before(cs: list[dict], ts: float) -> float | None:
    """Mid (cents) of the last candle whose period ended at or before ts — never after."""
    best, best_t = None, -1.0
    for c in cs:
        t = xl._num(c.get("end_period_ts"))
        if t <= ts and t > best_t:
            yb = xl._num((c.get("yes_bid") or {}).get("close_dollars"))
            ya = xl._num((c.get("yes_ask") or {}).get("close_dollars"))
            if yb > 0 and ya > 0:
                best, best_t = (yb + ya) / 2.0 * 100.0, t
    return best


def calibration(rows: list[tuple[float, str]]) -> dict[str, dict]:
    """rows = [(price_cents, result)] → per band {n, mean_price, yes_rate, gap}."""
    out: dict[str, dict] = {}
    by = defaultdict(list)
    for p, r in rows:
        by[band_of(p)].append((p, 1.0 if r == "yes" else 0.0))
    for band, xs in by.items():
        n = len(xs)
        mp = sum(p for p, _ in xs) / n
        yr = sum(y for _, y in xs) / n * 100.0
        out[band] = {"n": n, "mean_price": mp, "yes_rate": yr, "gap": yr - mp}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EARNBEAT recon census (read-only)")
    ap.add_argument("--max-event-pages", type=int, default=80)
    ap.add_argument("--calib-samples", type=int, default=80,
                    help="settled kpi_threshold markets to fetch hourly candles for")
    args = ap.parse_args(argv)

    print("EARNBEAT recon census — Kalshi company-KPI markets: structure, capacity, and a "
          "pre-report calibration read (read-only)")
    settled_evs = events("settled", args.max_event_pages)
    open_evs = events("open", args.max_event_pages)
    print(f"scanned events: settled={len(settled_evs)} open={len(open_evs)}")

    settled: dict[str, dict] = defaultdict(lambda: {"n": 0, "vol": 0.0, "series": set()})
    open_ct: dict[str, dict] = defaultdict(lambda: {"n": 0, "series": set()})
    matched: dict[str, int] = defaultdict(int)
    weekly: dict[str, int] = defaultdict(int)
    calib_pool: list[tuple[str, str, float, str, float]] = []

    for ev in settled_evs:
        for m in ev.get("markets") or []:
            if not is_kpi(ev, m):
                continue
            series = m.get("series_ticker") or ev.get("series_ticker") or ""
            matched[f"{series or '?'} [{ev.get('category') or '?'}]"] += 1
            text = " ".join(filter(None, [ev.get("title"), ev.get("sub_title"), m.get("title"),
                                          m.get("subtitle")]))
            t = kpi_type_of(text)
            settled[t]["n"] += 1
            settled[t]["vol"] += _vol(m)
            settled[t]["series"].add(series)
            close_ts = _ts(m.get("close_time"))
            if close_ts:
                weekly[datetime.fromtimestamp(close_ts, tz=timezone.utc).strftime("%G-W%V")] += 1
            result = (m.get("result") or "").lower()
            if t == "kpi_threshold" and result in ("yes", "no") and _vol(m) > 0 and close_ts:
                calib_pool.append((series, m.get("ticker") or "", close_ts, result, _vol(m)))
    for ev in open_evs:
        for m in ev.get("markets") or []:
            if not is_kpi(ev, m):
                continue
            series = m.get("series_ticker") or ev.get("series_ticker") or ""
            text = " ".join(filter(None, [ev.get("title"), m.get("title")]))
            t = kpi_type_of(text)
            open_ct[t]["n"] += 1
            open_ct[t]["series"].add(series)

    total_settled = sum(r["n"] for r in settled.values())
    total_open = sum(r["n"] for r in open_ct.values())
    print(f"\nKPI-hub markets found (strict filter): settled={total_settled} open={total_open}")
    print("  matched (series [category] -> count) — verify these are genuine KPI markets:")
    for k, c in sorted(matched.items(), key=lambda kv: -kv[1])[:20]:
        print(f"    {c:5d}  {k}")
    if not total_settled and not total_open:
        print("\nVERDICT: HOLD (UNTESTABLE) — nothing matched. Refresh KPI_SERIES/KPI_CATEGORY "
              "against the live hub tickers first. Not a kill.")
        return 0

    print("\n== C1/C2 structure + capacity ==")
    print(f"  {'type':14s} {'settled':>8s} {'open':>6s} {'settled_vol':>12s} {'series':>7s}")
    for t in sorted(set(settled) | set(open_ct), key=lambda k: -(settled[k]['n'] + open_ct[k]['n'])):
        print(f"  {t:14s} {settled[t]['n']:8d} {open_ct[t]['n']:6d} {settled[t]['vol']:12.0f} "
              f"{len(settled[t]['series'] | open_ct[t]['series']):7d}")
    print("  settles per ISO week (accrual rate):")
    for wk in sorted(weekly)[-10:]:
        print(f"    {wk}  {weekly[wk]}")

    print("\n== C3 calibration pre-read (kpi_threshold; mid at close-7d and close-48h) ==")
    calib_pool.sort(key=lambda r: -r[4])
    rows_7d, rows_48h, jumps = [], [], 0
    for series, ticker, close_ts, result, _ in calib_pool[:args.calib_samples]:
        cs = hourly_candles(series, ticker, int(close_ts) - 8 * 86400, int(close_ts))
        q7, q48 = quote_at_or_before(cs, close_ts - 7 * 86400), quote_at_or_before(cs, close_ts - 48 * 3600)
        if q7 is not None:
            rows_7d.append((q7, result))
        if q48 is not None:
            rows_48h.append((q48, result))
        if q7 is not None and q48 is not None and abs(q48 - q7) >= 30:
            jumps += 1
        time.sleep(0.05)
    for label, rows in (("close-7d", rows_7d), ("close-48h", rows_48h)):
        print(f"  [{label}] readable n={len(rows)}")
        print(f"    {'band':8s} {'n':>5s} {'mean_px':>8s} {'yes%':>7s} {'gap':>7s}")
        for band, r in sorted(calibration(rows).items()):
            print(f"    {band:8s} {r['n']:5d} {r['mean_price']:8.1f} {r['yes_rate']:7.1f} "
                  f"{r['gap']:+7.1f}")
    print(f"  markets whose mid moved >= 30c between the two reads: {jumps} "
          "(post-release contamination flag — the full probe must key on the earnings-calendar "
          "date, not close_time)")

    n_thresh = len(calib_pool)
    readable = len(rows_48h)
    print("\n== census verdict (pre-stage; a THESIS probe follows only if this promotes) ==")
    print(f"  settled kpi_threshold w/ volume >= {SETTLED_FLOOR}: {n_thresh >= SETTLED_FLOOR} ({n_thresh})")
    print(f"  readable pre-report quotes >= {CALIB_FLOOR}: {readable >= CALIB_FLOOR} ({readable})")
    if n_thresh >= SETTLED_FLOOR and readable >= CALIB_FLOOR:
        print("  VERDICT: TESTABLE-NOW — write the EARNBEAT probe (consensus-keyed, "
              "earnings-date point-in-time, both-leg fees) against the pre-registered P1–P3.")
    else:
        print("  VERDICT: HOLD (ACCRUAL) — the hub is too new to grade. Trigger: the Q3 "
              "earnings season (mid-October → mid-November 2026) should add hundreds of "
              "settles; re-run this census the week of 2026-11-09. Not a kill.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
