"""GRIDPIN census — is the ERCOT daily-peak ladder (KXTXERCOTPEAKD) testable, and is its
settlement value knowable while the market still trades?

Pre-registered in docs/GRIDPIN_THESIS.md (2026-09-29). Answers only C0-C2; it measures no edge.

  C0 universe   settled event-days, settled rungs with volume, rungs traded after 19:00 CT
  C1 grading    ERCOT NP6-346-CD "Actual System Load by Forecast Zone" (the report the contract
                names, published ~05:50 CT the next morning, ~31 days retained) reproduces
                Kalshi's settled results
  C2 timing     same-day ERCOT dashboard hourly `systemLoad` is posted <= 90 min after the hour
                ends, and does not flip a settled rung when the official report lands
  plus (reported, not decided): today's 5-min dashboard peak vs hourly peak (the P3 mechanics
  gap), and whether the hourly mean of 5-min demand reproduces the posted hourly value.

Sources (provenance kept separate, never merged into one series):
  Kalshi public REST   /events?series_ticker=KXTXERCOTPEAKD, /markets/trades
  ERCOT public         misapp IceDocListJsonWS + misdownload mirDownload (NP6-346-CD zips),
                       api/1/services/read/dashboards/{loadForecastVsActual,supply-demand}.json

Read-only, stdlib only. Places no orders and writes no tables.

Usage: {"type":"script","name":"kalshi_gridpin_census","id":"gridpin-census-1"}
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import re
import statistics
import time
import urllib.error
import urllib.request
import zipfile
from collections import defaultdict

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
ERCOT = "https://www.ercot.com"
SERIES = "KXTXERCOTPEAKD"
NP6346 = "14836"

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Pre-registered constants (docs/GRIDPIN_THESIS.md). Changing any is a new census.
C0_DAYS = 40
C0_RUNGS_WITH_VOL = 150
C0_POST_PEAK_RUNGS = 60
POST_PEAK_HOUR_CT = 19
C1_REPRODUCE = 0.95
C2_MAX_LAG_MIN = 90
C2_MAX_FLIP = 0.02
CT_FALLBACK = dt.timezone(dt.timedelta(hours=-5))
_MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}


def _ct():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/Chicago")
    except Exception:  # noqa: BLE001 — no tzdata: CDT is correct for the Sep-Oct window
        return CT_FALLBACK


def fetch(url: str, *, binary: bool = False, timeout: int = 45):
    for attempt in range(4):
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed hosts)
                body = resp.read()
                return body if binary else json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return None
    return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_TITLE_DAY = re.compile(r"on ([A-Z][a-z]{2})[a-z]* (\d{1,2}), (\d{4})")


def event_date(event_ticker: str, title: str = "") -> dt.date | None:
    """The OPERATING day an event settles on.

    The ticker carries the UTC CLOSE date, not the operating day: KXTXERCOTPEAKD-26SEP28 is
    "peak electricity demand on Sep 27, 2026" and closes 2026-09-28T04:55Z (23:55 CT on the
    27th). The title is authoritative; the ticker date minus one day is the fallback. (The first
    census run read the ticker date as the operating day, which shifted every grade and the
    post-peak window by a day — docs/GRIDPIN_THESIS.md RESULTS.)"""
    m = _TITLE_DAY.search(title or "")
    if m and m.group(1).upper() in _MONTHS:
        return dt.date(int(m.group(3)), _MONTHS[m.group(1).upper()], int(m.group(2)))
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})$", event_ticker or "")
    if not m or m.group(2) not in _MONTHS:
        return None
    close = dt.date(2000 + int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3)))
    return close - dt.timedelta(days=1)


def rung_result_from(value: float, strike: float, strike_type: str | None) -> str | None:
    """What a greater-than rung settles to for a given peak value."""
    st = (strike_type or "greater").lower()
    if st in ("greater", "greater_or_equal"):
        return "yes" if (value > strike if st == "greater" else value >= strike) else "no"
    if st in ("less", "less_or_equal"):
        return "yes" if (value < strike if st == "less" else value <= strike) else "no"
    return None


def parse_np6346(zip_bytes: bytes) -> dict[dt.date, list[float]]:
    """{oper_day: [TOTAL MW by hour]} from one NP6-346-CD csv zip. Tolerant of header case."""
    out: dict[dt.date, list[float]] = defaultdict(list)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".csv"):
                continue
            rows = csv.DictReader(io.StringIO(zf.read(name).decode("utf-8", "replace")))
            for r in rows:
                r = {(k or "").strip().lower(): v for k, v in r.items()}
                day, total = r.get("operday"), _num(r.get("total"))
                if not day or total is None:
                    continue
                try:
                    d = dt.datetime.strptime(day.strip(), "%m/%d/%Y").date()
                except ValueError:
                    continue
                out[d].append(total)
    return dict(out)


def hourly_means_from_5min(points: list[tuple[dt.datetime, float]]) -> dict[int, float]:
    """{hour_ending: mean 5-min demand} for clock hours with all 12 intervals.
    Interval stamped hh:mm belongs to hour-ending hh+1 (00:00-00:55 -> HE1)."""
    buckets: dict[int, list[float]] = defaultdict(list)
    for ts, v in points:
        buckets[ts.hour + 1].append(v)
    return {he: sum(v) / len(v) for he, v in buckets.items() if len(v) == 12}


def verdict(c0: dict, c1: dict, c2: dict) -> tuple[str, list[str]]:
    """Pre-registered order: a C2 KILL dominates, then C1 BLOCKED_DATA, then C0 HOLD."""
    why = []
    if c2.get("lag_min") is not None and c2["lag_min"] > C2_MAX_LAG_MIN:
        why.append(f"C2 KILL: latest posted hour ended {c2['lag_min']:.0f} min ago "
                   f"(> {C2_MAX_LAG_MIN})")
    if c2.get("flip_rate") is not None and c2["flip_rate"] > C2_MAX_FLIP:
        why.append(f"C2 KILL: revision flips {c2['flip_rate']:.1%} of rungs (> {C2_MAX_FLIP:.0%})")
    if why:
        return "KILL (premise)", why
    if not c1.get("reachable"):
        return "BLOCKED_DATA", ["C1: ERCOT NP6-346-CD not fetchable from the runner"]
    if c1.get("rate") is None or c1["rate"] < C1_REPRODUCE:
        r = c1.get("rate")
        return "BLOCKED_DATA", [f"C1: NP6-346 reproduces {('n/a' if r is None else f'{r:.1%}')}"
                                f" of settled rungs (< {C1_REPRODUCE:.0%})"]
    if c2.get("lag_min") is None or c2.get("flip_rate") is None:
        return "HOLD", ["C2 not measurable on this run (dashboard or previous-day data missing)"]
    short = []
    if c0["days"] < C0_DAYS:
        short.append(f"settled days {c0['days']} < {C0_DAYS}")
    if c0["rungs_with_vol"] < C0_RUNGS_WITH_VOL:
        short.append(f"rungs with volume {c0['rungs_with_vol']} < {C0_RUNGS_WITH_VOL}")
    if c0["post_peak_rungs"] < C0_POST_PEAK_RUNGS:
        short.append(f"post-19:00-CT traded rungs {c0['post_peak_rungs']} < {C0_POST_PEAK_RUNGS}")
    if short:
        return "HOLD (accrual)", ["C0: " + "; ".join(short),
                                  "C1 and C2 pass — re-run when settled days reach 60"]
    return "CENSUS CLEARS", ["C0-C2 pass -> build scripts/kalshi_gridpin_study.py (P1-P4)"]


# ---------------------------------------------------------------------------------------------


def kalshi_rungs() -> list[dict]:
    out, cursor = [], ""
    for _ in range(40):
        page = fetch(f"{KALSHI}/events?series_ticker={SERIES}&with_nested_markets=true"
                     f"&limit=200&cursor={cursor}")
        evs = (page or {}).get("events") or []
        for ev in evs:
            day = event_date(ev.get("event_ticker", ""), ev.get("title", ""))
            for m in ev.get("markets") or []:
                out.append({
                    "ticker": m.get("ticker"), "day": day, "status": m.get("status"),
                    "result": (m.get("result") or "").lower() or None,
                    "value": _num(m.get("expiration_value")),
                    "strike": _num(m.get("floor_strike")),
                    "strike_type": m.get("strike_type"),
                    "volume": _num(m.get("volume_fp")) if m.get("volume_fp") is not None
                    else _num(m.get("volume")) or 0.0})
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not evs:
            break
        time.sleep(0.05)
    return out


def post_peak_traded(ticker: str, day: dt.date, ct) -> bool:
    start = dt.datetime.combine(day, dt.time(POST_PEAK_HOUR_CT), ct)
    data = fetch(f"{KALSHI}/markets/trades?ticker={ticker}&min_ts={int(start.timestamp())}"
                 f"&limit=5")
    time.sleep(0.05)
    return bool((data or {}).get("trades"))


def np6346_history(max_docs: int) -> dict[dt.date, list[float]] | None:
    lst = fetch(f"{ERCOT}/misapp/servlets/IceDocListJsonWS?reportTypeId={NP6346}")
    if lst is None:
        return None
    docs = [d.get("Document") or {} for d in
            ((lst.get("ListDocsByRptTypeRes") or {}).get("DocumentList") or [])]
    docs = [d for d in docs if "csv" in (d.get("FriendlyName") or "").lower()][:max_docs]
    hist: dict[dt.date, list[float]] = {}
    for d in docs:
        blob = fetch(f"{ERCOT}/misdownload/servlets/mirDownload?doclookupId={d.get('DocID')}",
                     binary=True)
        if blob:
            try:
                hist.update(parse_np6346(blob))
            except zipfile.BadZipFile:
                pass
        time.sleep(0.1)
    return hist


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--max-docs", type=int, default=40, help="NP6-346 csv files to pull")
    ap.add_argument("--post-peak-sample", type=int, default=400,
                    help="settled rungs with volume to check for post-19:00-CT trades")
    args = ap.parse_args(argv)
    ct = _ct()
    now = dt.datetime.now(dt.timezone.utc)
    print("GRIDPIN census — read-only; pre-registered in docs/GRIDPIN_THESIS.md\n")

    # ---- C0: the universe ----------------------------------------------------------------
    rungs = kalshi_rungs()
    settled = [r for r in rungs if r["result"] in ("yes", "no") and r["day"]]
    days = sorted({r["day"] for r in settled})
    with_vol = [r for r in settled if (r["volume"] or 0) > 0]
    sample = with_vol[: args.post_peak_sample]
    post_peak = sum(post_peak_traded(r["ticker"], r["day"], ct) for r in sample)
    c0 = {"days": len(days), "rungs_with_vol": len(with_vol), "post_peak_rungs": post_peak}
    print("== C0 universe ==")
    print(f"  rungs listed {len(rungs)}  settled {len(settled)} over {len(days)} event-days "
          f"({days[0] if days else '-'} .. {days[-1] if days else '-'})")
    print(f"  settled rungs with volume {len(with_vol)}  total settled volume "
          f"{sum(r['volume'] or 0 for r in settled):,.0f}")
    print(f"  of {len(sample)} checked, traded after {POST_PEAK_HOUR_CT}:00 CT: {post_peak}")

    # ---- C1: grading source ----------------------------------------------------------------
    hist = np6346_history(args.max_docs)
    c1: dict = {"reachable": hist is not None and len(hist) > 0}
    print("\n== C1 grading source (ERCOT NP6-346-CD) ==")
    if c1["reachable"]:
        peaks = {d: max(v) for d, v in hist.items() if len(v) >= 23}
        print(f"  operating days parsed {len(peaks)} ({min(peaks) if peaks else '-'} .. "
              f"{max(peaks) if peaks else '-'})")
        graded = agree = 0
        val_diffs = []
        by_day = defaultdict(list)
        for r in settled:
            by_day[r["day"]].append(r)
        for day, rs in sorted(by_day.items()):
            if day not in peaks:
                continue
            kv = next((r["value"] for r in rs if r["value"] is not None), None)
            if kv is not None:
                val_diffs.append(peaks[day] - kv)
            for r in rs:
                if r["strike"] is None:
                    continue
                res = rung_result_from(peaks[day], r["strike"], r["strike_type"])
                if res is None:
                    continue
                graded += 1
                agree += res == r["result"]
        c1["rate"] = agree / graded if graded else None
        print(f"  settled rungs graded {graded}, reproduced {agree}"
              f" ({c1['rate']:.1%})" if graded else "  no overlap between report days and settled rungs")
        if val_diffs:
            print(f"  NP6-346 daily max - Kalshi expiration_value: median "
                  f"{statistics.median(val_diffs):+.1f} MW, max |diff| "
                  f"{max(abs(x) for x in val_diffs):.1f} MW over {len(val_diffs)} days")
    else:
        peaks = {}
        print("  NOT reachable")

    # ---- C2: same-day timing + revisions ---------------------------------------------------
    print("\n== C2 same-day timing (ERCOT dashboard) ==")
    c2: dict = {"lag_min": None, "flip_rate": None}
    lfa = fetch(f"{ERCOT}/api/1/services/read/dashboards/loadForecastVsActual.json")
    dash_prev_max = None
    cur_hourly: dict[int, float] = {}
    if lfa:
        prev = (lfa.get("previousDay") or {}).get("data") or []
        cur = (lfa.get("currentDay") or {}).get("data") or []
        prev_day = None
        if prev:
            prev_day = dt.datetime.strptime(prev[0]["timestamp"][:10], "%Y-%m-%d").date()
            vals = [_num(p.get("systemLoad")) for p in prev]
            vals = [v for v in vals if v is not None]
            dash_prev_max = max(vals) if vals else None
        posted = [p for p in cur if _num(p.get("systemLoad")) is not None]
        for p in posted:
            cur_hourly[int(p["hourEnding"])] = _num(p["systemLoad"])
        if posted:
            last = posted[-1]
            he_end = dt.datetime.fromtimestamp(last["epoch"] / 1000, dt.timezone.utc)
            c2["lag_min"] = (now - he_end).total_seconds() / 60
            print(f"  now {now.astimezone(ct):%H:%M} CT; hourly systemLoad posted through HE"
                  f"{last['hourEnding']} (ended {he_end.astimezone(ct):%H:%M} CT) -> "
                  f"{c2['lag_min']:.0f} min ago; bar <= {C2_MAX_LAG_MIN}")
        else:
            print("  currentDay has no posted systemLoad (run later in the CT day)")
        if prev_day and dash_prev_max is not None:
            official = peaks.get(prev_day)
            day_rungs = [r for r in settled if r["day"] == prev_day and r["strike"] is not None]
            ref = official if official is not None else next(
                (r["value"] for r in day_rungs if r["value"] is not None), None)
            if ref is not None:
                cand = [r for r in rungs if r["day"] == prev_day and r["strike"] is not None]
                flips = sum(rung_result_from(dash_prev_max, r["strike"], r["strike_type"])
                            != rung_result_from(ref, r["strike"], r["strike_type"]) for r in cand)
                c2["flip_rate"] = flips / len(cand) if cand else 0.0
                print(f"  {prev_day}: dashboard hourly max {dash_prev_max:,.1f} MW vs "
                      f"{'NP6-346' if official is not None else 'Kalshi expiration_value'} "
                      f"{ref:,.1f} MW (diff {dash_prev_max - ref:+.1f}); rungs flipped "
                      f"{flips}/{len(cand)}")
            else:
                print(f"  {prev_day}: no official value yet to measure revisions against")
    else:
        print("  loadForecastVsActual.json NOT reachable")

    # ---- reported only: the 5-min vs hourly mechanics gap (P3) ------------------------------
    print("\n-- reported, never decides: 5-min vs hourly (today) --")
    sd = fetch(f"{ERCOT}/api/1/services/read/dashboards/supply-demand.json")
    pts = []
    for p in (sd or {}).get("data") or []:
        v = _num(p.get("demand"))
        if v and p.get("epoch"):
            pts.append((dt.datetime.fromtimestamp(p["epoch"] / 1000, ct), v))
    today = now.astimezone(ct).date()
    pts = [(t, v) for t, v in pts if t.date() == today]
    if pts and cur_hourly:
        mx5 = max(v for _t, v in pts)
        mxh = max(cur_hourly.values())
        print(f"  5-min peak so far {mx5:,.0f} MW vs hourly peak so far {mxh:,.0f} MW "
              f"(gap {mx5 - mxh:+,.0f} MW; strike spacing ~500 MW)")
        means = hourly_means_from_5min(pts)
        common = sorted(set(means) & set(cur_hourly))
        if common:
            errs = [abs(means[h] - cur_hourly[h]) / cur_hourly[h] for h in common]
            print(f"  hourly mean of 5-min demand vs posted hourly systemLoad over "
                  f"{len(common)} hours: mean |err| {sum(errs) / len(errs):.2%}, max "
                  f"{max(errs):.2%}  (small -> the hourly value can be computed live)")
    else:
        print("  supply-demand.json or today's hourly values unavailable")

    # sibling grid series (sizing only)
    sers = fetch(f"{KALSHI}/series")
    sib = [s.get("ticker") for s in (sers or {}).get("series") or []
           if re.search(r"ERCOT|PJM|CAISO|MISO|NYISO|ISONE|SPP|GRID|POWERDEMAND|PEAKLOAD",
                        (s.get("ticker") or "").upper())]
    print(f"\n  sibling grid-like series on Kalshi: {sib if sib else '(none found / list unavailable)'}")

    v, why = verdict(c0, c1, c2)
    print("\n== verdict ==")
    print(f"  {v}")
    for w in why:
        print(f"   - {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
