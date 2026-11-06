"""GRIDPIN full probe (P1-P4) — does the ERCOT daily-peak ladder (KXTXERCOTPEAKD) still sell
the side the PUBLISHED hourly running max has already decided, at a price that clears cost?

Pre-registered in docs/GRIDPIN_THESIS.md (2026-09-29); the census (C0-C2) cleared on 2026-11-06
(run 3). This probe measures P1-P4 exactly as written there, with the operational choices
recorded in the thesis's "Full-probe implementation notes" BEFORE the first run.

  P1  decided-side entries (YES when the posted hourly running max already exceeds the strike;
      NO when the strike is >= 1,000 MW above it after 21:00 CT), taker at ask, held to
      settlement, net of the taker fee: mean >= +2.0c, n >= 60, accuracy >= 98%.
      KILL at mean <= +0.5c or accuracy < 95%.
  P2  the P1 entries quoted 85-97c beat UNDECIDED rung-sides quoted 85-97c in the same CT hour
      of the same day by >= 1.5c (PINNED's favorite-drift control). KILL if not.
  P3  gap class (hourly max < strike <= 5-min max): NO at ask nets >= +3c at n >= 20.
      Graded only where a 5-minute demand history exists; otherwise UNTESTABLE.
  P4  median tape depth at or better than the entry price >= 5 contracts, and
      >= 1 qualifying entry per 2 event-days. Otherwise HOLD (a hobby, never a book).

No lookahead. At decision time t the only load information used is NP6-346 hourly values
whose hour ended at least LAG minutes before t (LAG = 90, the C2 bar; the census measured
40-67). The rung result is used only to SCORE an entry, never to select one. The entry price
is a Kalshi 1-min candle close ask at or after t (YES ask = yes_ask; NO ask = 1 - yes_bid) —
never a settle and never a backward-filled quote.

Sources (provenance kept separate; nothing is written anywhere):
  Kalshi public REST   /events?series_ticker=KXTXERCOTPEAKD, /series/.../candlesticks,
                       /markets/trades, /markets/{t}/orderbook (live description only)
  ERCOT public         NP6-346-CD csv zips (hourly TOTAL; ~31 days retained, so the graded
                       window is ROLLING), supply-demand.json (5-min, P3 only)

Read-only, stdlib only. Places no orders and writes no tables.

Usage: {"type":"script","name":"kalshi_gridpin_study","id":"gridpin-study-1"}
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import math
import statistics
import time
import zipfile
from collections import defaultdict

import kalshi_gridpin_census as gc  # fetch (browser UA + retries), event_date, rung grading

KALSHI = gc.KALSHI
ERCOT = gc.ERCOT
SERIES = gc.SERIES

# Pre-registered constants (docs/GRIDPIN_THESIS.md). Changing any is a new probe.
LAG_MIN = 90                 # hourly value usable LAG minutes after its hour ends (C2 bar)
LAG_SENSITIVITY = (60, 120)  # reported only, never decides
NO_MARGIN_MW = 1000.0        # NO side: strike >= running max + 1,000 MW ...
NO_AFTER_HOUR_CT = 21        # ... and only from 21:00 CT
ENTRY_CAP_C = 97.0           # a decided side is taken only when its ask <= 97c
BAND_LO_C, BAND_HI_C = 85.0, 97.0
P1_PASS_MEAN_C = 2.0
P1_KILL_MEAN_C = 0.5
P1_MIN_N = 60
P1_PASS_ACC = 0.98
P1_KILL_ACC = 0.95
P2_MARGIN_C = 1.5
P2_MIN_N = 20                # each side of the P2 comparison
P3_PASS_C = 3.0
P3_MIN_N = 20
P4_DEPTH = 5.0
P4_PER_DAY = 0.5
DEPTH_WINDOW_S = 30 * 60     # P4 tape window after entry
MARGIN_BUCKETS = ((0, 500), (500, 1500), (1500, 3000), (3000, 10 ** 9))


def fee_cents(price_c: float, qty: int = 1) -> int:
    """Kalshi taker fee in cents: ceil(0.07 * qty * P * (1 - P) * 100), P in dollars."""
    p = min(max(price_c / 100.0, 0.0), 1.0)
    return math.ceil(round(0.07 * qty * p * (1.0 - p) * 100, 9))


def net_pnl_c(ask_c: float, won: bool) -> float:
    """One contract bought at ask_c and held to settlement, net of the entry fee."""
    return (100.0 - ask_c if won else -ask_c) - fee_cents(ask_c)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _iso_ts(s) -> float | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ---- ERCOT hourly path ------------------------------------------------------------------------


def parse_np6346_hourly(zip_bytes: bytes) -> dict[dt.date, list[tuple[int, str, float]]]:
    """{oper_day: [(hour_ending, dst_flag, TOTAL MW)]} from one NP6-346-CD csv zip."""
    out: dict[dt.date, list[tuple[int, str, float]]] = defaultdict(list)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".csv"):
                continue
            for r in csv.DictReader(io.StringIO(zf.read(name).decode("utf-8", "replace"))):
                r = {(k or "").strip().lower(): v for k, v in r.items()}
                day, total, he = r.get("operday"), _num(r.get("total")), r.get("hourending")
                if not day or total is None or not he:
                    continue
                try:
                    d = dt.datetime.strptime(day.strip(), "%m/%d/%Y").date()
                    h = int(str(he).strip().split(":")[0])
                except ValueError:
                    continue
                out[d].append((h, (r.get("dstflag") or "N").strip().upper(), total))
    return dict(out)


def hour_path(rows: list[tuple[int, str, float]], day: dt.date, tz) -> list[tuple[float, float]]:
    """[(hour_end_unix, MW)] for one operating day, in clock order.

    Hour-end times are counted in ABSOLUTE hours from local midnight, so the 23- and 25-hour
    DST days come out right: the repeated hour (DSTFlag Y) sorts after its first occurrence."""
    start = dt.datetime.combine(day, dt.time(0), tz).astimezone(dt.timezone.utc)
    ordered = sorted(rows, key=lambda r: (r[0], r[1] == "Y"))
    return [((start + dt.timedelta(hours=i + 1)).timestamp(), mw)
            for i, (_h, _f, mw) in enumerate(ordered)]


def running_max_at(path: list[tuple[float, float]], t: float, lag_s: float) -> float | None:
    """Max of the hourly values PUBLISHED by t: hour ended at least lag_s before t."""
    vals = [mw for end, mw in path if end + lag_s <= t]
    return max(vals) if vals else None


def decided_side(strike: float, strike_type: str, m: float | None, t_local_hour: int) -> str | None:
    """The side the published running max m has already decided at t, or None.

    Running max only rises, so for a greater-than rung m > strike decides YES for good. NO is
    a near-decision: only after 21:00 CT and only when the strike is >= 1,000 MW above m."""
    if m is None:
        return None
    if gc.rung_result_from(m, strike, strike_type) == "yes" and \
            (strike_type or "greater").lower().startswith("greater"):
        return "yes"
    if (strike_type or "greater").lower().startswith("greater") and \
            t_local_hour >= NO_AFTER_HOUR_CT and strike >= m + NO_MARGIN_MW:
        return "no"
    return None


def first_decision(path, strike, strike_type, lag_s, tz, t_from: float, t_to: float, side: str):
    """(t, running max) — the first moment in [t_from, t_to] at which `side` is decided.

    Decisions only change when an hour's value becomes published (hour_end + lag) or when
    the clock crosses 21:00 CT, so only those instants are checked."""
    instants = sorted({end + lag_s for end, _ in path if t_from <= end + lag_s <= t_to}
                      | {t_from})
    day = dt.datetime.fromtimestamp(t_from, tz).date()
    nine = dt.datetime.combine(day, dt.time(NO_AFTER_HOUR_CT), tz).timestamp()
    if t_from <= nine <= t_to:
        instants = sorted(set(instants) | {nine})
    for t in instants:
        m = running_max_at(path, t, lag_s)
        if decided_side(strike, strike_type, m, dt.datetime.fromtimestamp(t, tz).hour) == side:
            return t, m
    return None, None


# ---- Kalshi quotes ----------------------------------------------------------------------------


def side_ask_c(yes_bid, yes_ask, side: str) -> float | None:
    """Taker cost in cents for one contract of `side`, or None when that side has no ask."""
    if side == "yes":
        a = _num(yes_ask)
        return a * 100.0 if a is not None and 0.0 < a < 1.0 else None
    b = _num(yes_bid)
    return (1.0 - b) * 100.0 if b is not None and 0.0 < b < 1.0 else None


def parse_candles(data) -> list[tuple[float, float | None, float | None]]:
    """[(end_ts, yes_bid $, yes_ask $)], ts-sorted, from a candlesticks response."""
    rows = []
    for c in (data or {}).get("candlesticks") or []:
        ts = _num(c.get("end_period_ts"))
        if ts is None:
            continue
        yb = (c.get("yes_bid") or {}).get("close_dollars")
        ya = (c.get("yes_ask") or {}).get("close_dollars")
        if yb is None:
            yb = _cents_to_dollars((c.get("yes_bid") or {}).get("close"))
        if ya is None:
            ya = _cents_to_dollars((c.get("yes_ask") or {}).get("close"))
        rows.append((ts, _num(yb), _num(ya)))
    rows.sort(key=lambda r: r[0])
    return rows


def _cents_to_dollars(v):
    n = _num(v)
    return None if n is None else n / 100.0


def first_entry(candles, side: str, t_from: float, t_to: float, lo_c: float, hi_c: float,
                ok=None):
    """(ts, ask_c) — the first OBSERVED candle close in [t_from, t_to] whose `side` ask is in
    [lo_c, hi_c] (and, if given, `ok(ts)` holds then). No backward fill: a quote is used only
    at a candle that was observed."""
    for ts, yb, ya in candles:
        if ts < t_from:
            continue
        if ts > t_to:
            break
        a = side_ask_c(yb, ya, side)
        if a is not None and lo_c <= a <= hi_c and (ok is None or ok(ts)):
            return ts, a
    return None, None


def parse_trade(t: dict):
    """(unix ts, taker_side, our-side price c, contracts) from a public trade, or None."""
    ts = _iso_ts(t.get("created_time"))
    side = (t.get("taker_side") or "").lower()
    yp = _num(t.get("yes_price_dollars"))
    yp = yp * 100.0 if yp is not None else _num(t.get("yes_price"))
    cnt = _num(t.get("count_fp"))
    if cnt is None:
        cnt = _num(t.get("count"))
    if ts is None or side not in ("yes", "no") or yp is None or not cnt:
        return None
    return ts, side, (yp if side == "yes" else 100.0 - yp), cnt


def tape_depth(trades, side: str, entry_ts: float, entry_c: float, window_s: float) -> float:
    """P4 proxy: contracts takers bought on OUR side at or better than our price, from 60 s
    before our entry to window_s after — size that demonstrably rested there."""
    return sum(c for ts, s, p, c in trades
               if s == side and p <= entry_c + 1e-9 and entry_ts - 60 <= ts <= entry_ts + window_s)


# ---- measurement ------------------------------------------------------------------------------


def build_entries(rungs, paths, candles, tz, lag_s: float):
    """P1 entries: one per (rung, side), the first in-cap observed ask after the decision."""
    out = []
    for r in rungs:
        path = paths.get(r["day"])
        cs = candles.get(r["ticker"])
        if not path or not cs or r["strike"] is None:
            continue
        day_start = dt.datetime.combine(r["day"], dt.time(0), tz).timestamp()
        t_to = r["close_ts"]
        for side in ("yes", "no"):
            t_dec, m = first_decision(path, r["strike"], r["strike_type"], lag_s, tz,
                                      day_start, t_to, side)
            if t_dec is None:
                continue
            def still_decided(ts, r=r, path=path, side=side):
                # a NO decision is a near-decision: it must still hold when we buy
                m_t = running_max_at(path, ts, lag_s)
                return decided_side(r["strike"], r["strike_type"], m_t,
                                    dt.datetime.fromtimestamp(ts, tz).hour) == side
            ts, ask = first_entry(cs, side, t_dec, t_to, 0.0, ENTRY_CAP_C, still_decided)
            if ts is None:
                continue
            m_now = running_max_at(path, ts, lag_s)
            won = r["result"] == side
            out.append({"ticker": r["ticker"], "day": r["day"], "side": side, "ts": ts,
                        "ask": ask, "t_dec": t_dec, "m": m_now if m_now is not None else m,
                        "strike": r["strike"], "result": r["result"], "won": won,
                        "pnl": net_pnl_c(ask, won),
                        "hour": dt.datetime.fromtimestamp(ts, tz).hour})
    return out


def build_control(rungs, paths, candles, tz, lag_s: float, p1_band):
    """P2 control: UNDECIDED rung-sides on the same day, first in-band (85-97c) observed ask
    within a CT clock hour that holds a P1 in-band entry. One per (rung, side)."""
    hours = defaultdict(set)
    for e in p1_band:
        hours[e["day"]].add(e["hour"])
    out = []
    for r in rungs:
        path, cs = paths.get(r["day"]), candles.get(r["ticker"])
        if not path or not cs or r["strike"] is None or r["day"] not in hours:
            continue
        for side in ("yes", "no"):
            for h in sorted(hours[r["day"]]):
                h0 = dt.datetime.combine(r["day"], dt.time(h), tz).timestamp()
                t_from, t_to = h0, min(h0 + 3600 - 1, r["close_ts"])
                hit = None
                for ts, yb, ya in cs:
                    if ts < t_from:
                        continue
                    if ts > t_to:
                        break
                    a = side_ask_c(yb, ya, side)
                    if a is None or not BAND_LO_C <= a <= BAND_HI_C:
                        continue
                    m = running_max_at(path, ts, lag_s)
                    if decided_side(r["strike"], r["strike_type"], m,
                                    dt.datetime.fromtimestamp(ts, tz).hour) is None:
                        hit = (ts, a)
                        break
                if hit:
                    won = r["result"] == side
                    out.append({"ticker": r["ticker"], "day": r["day"], "side": side,
                                "ts": hit[0], "ask": hit[1], "won": won,
                                "pnl": net_pnl_c(hit[1], won)})
                    break
    return out


def summarize(entries) -> dict:
    n = len(entries)
    if not n:
        return {"n": 0, "mean": None, "acc": None, "sd": None}
    pn = [e["pnl"] for e in entries]
    return {"n": n, "mean": sum(pn) / n, "acc": sum(e["won"] for e in entries) / n,
            "sd": statistics.pstdev(pn) if n > 1 else 0.0}


def _fmt(s: dict) -> str:
    if not s["n"]:
        return "n=0"
    se = s["sd"] / math.sqrt(s["n"]) if s["n"] > 1 else 0.0
    return f"n={s['n']:<4d} mean {s['mean']:+.2f}c (se {se:.2f})  win {s['acc']:.1%}"


def p1_verdict(s: dict) -> tuple[str, str]:
    """Pre-registered P1. Below n=60 there is no verdict either way (HOLD, accrual)."""
    if s["n"] < P1_MIN_N:
        return "HOLD (accrual)", f"n={s['n']} < {P1_MIN_N}"
    if s["acc"] < P1_KILL_ACC:
        return "KILL", f"accuracy {s['acc']:.1%} < {P1_KILL_ACC:.0%}"
    if s["mean"] <= P1_KILL_MEAN_C:
        return "KILL", f"mean {s['mean']:+.2f}c <= +{P1_KILL_MEAN_C}c"
    if s["mean"] >= P1_PASS_MEAN_C and s["acc"] >= P1_PASS_ACC:
        return "PASS", f"mean {s['mean']:+.2f}c, accuracy {s['acc']:.1%}, n={s['n']}"
    return "HOLD", (f"mean {s['mean']:+.2f}c / accuracy {s['acc']:.1%} between the PASS and "
                    f"KILL bars")


def p2_verdict(band: dict, ctrl: dict) -> tuple[str, str]:
    if band["n"] < P2_MIN_N or ctrl["n"] < P2_MIN_N:
        return "UNTESTABLE", f"in-band P1 n={band['n']}, control n={ctrl['n']} (need {P2_MIN_N} each)"
    d = band["mean"] - ctrl["mean"]
    if d >= P2_MARGIN_C:
        return "PASS", f"P1 band {band['mean']:+.2f}c - control {ctrl['mean']:+.2f}c = {d:+.2f}c"
    return "KILL", f"P1 band {band['mean']:+.2f}c - control {ctrl['mean']:+.2f}c = {d:+.2f}c " \
                   f"< +{P2_MARGIN_C}c"


def p3_verdict(s: dict) -> tuple[str, str]:
    if s["n"] < P3_MIN_N:
        return "UNTESTABLE", f"gap-class n={s['n']} < {P3_MIN_N} (no 5-min history)"
    if s["mean"] >= P3_PASS_C:
        return "PASS", f"NO at ask mean {s['mean']:+.2f}c, n={s['n']}"
    return "FAIL", f"NO at ask mean {s['mean']:+.2f}c < +{P3_PASS_C}c, n={s['n']}"


def p4_verdict(depths: list[float], n_entries: int, n_days: int) -> tuple[str, str]:
    per_day = n_entries / n_days if n_days else 0.0
    med = statistics.median(depths) if depths else 0.0
    why = f"median tape depth {med:.0f} (bar {P4_DEPTH:.0f}); {per_day:.2f} entries/day " \
          f"(bar {P4_PER_DAY})"
    return ("PASS" if med >= P4_DEPTH and per_day >= P4_PER_DAY else "FAIL"), why


def decision(p1: str, p2: str, p3: str, p4: str) -> tuple[str, str]:
    """The thesis's decision rule. Any KILL closes the ERCOT pin."""
    if p1 == "KILL" or p2 == "KILL":
        return "KILL", "a pre-registered kill criterion fired: the ERCOT pin closes"
    if p1 == "PASS" and p2 == "PASS" and p4 == "PASS":
        if p3 == "PASS":
            return "PROPOSE PAPER", "P1, P2, P3, P4 pass"
        if p3 == "UNTESTABLE":
            return "PROPOSE PAPER", "P1, P2, P4 pass; P3 untestable -> forward P3 check at n=20"
        return "HOLD", "P1 passes but P3 FAILS: the attention shape; needs a fresh pre-registration"
    if p1 == "PASS" and p2 == "PASS" and p4 == "FAIL":
        return "HOLD", "P4 fails: a hobby, never a book"
    return "HOLD", f"P1 {p1}, P2 {p2}, P4 {p4}"


# ---- fetching ---------------------------------------------------------------------------------


def kalshi_rungs() -> list[dict]:
    out, cursor = [], ""
    for _ in range(40):
        page = gc.fetch(f"{KALSHI}/events?series_ticker={SERIES}&with_nested_markets=true"
                        f"&limit=200&cursor={cursor}")
        evs = (page or {}).get("events") or []
        for ev in evs:
            day = gc.event_date(ev.get("event_ticker", ""), ev.get("title", ""))
            for m in ev.get("markets") or []:
                vol = _num(m.get("volume_fp"))
                out.append({
                    "ticker": m.get("ticker"), "day": day, "status": m.get("status"),
                    "result": (m.get("result") or "").lower() or None,
                    "strike": _num(m.get("floor_strike")),
                    "strike_type": m.get("strike_type") or "greater",
                    "close_ts": _iso_ts(m.get("close_time")),
                    "volume": vol if vol is not None else (_num(m.get("volume")) or 0.0)})
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not evs:
            break
        time.sleep(0.05)
    return out


def np6346_paths(max_docs: int, tz) -> dict[dt.date, list[tuple[float, float]]]:
    lst = gc.fetch(f"{ERCOT}/misapp/servlets/IceDocListJsonWS?reportTypeId={gc.NP6346}")
    docs = [d.get("Document") or {} for d in
            (((lst or {}).get("ListDocsByRptTypeRes") or {}).get("DocumentList") or [])]
    docs = [d for d in docs if "csv" in (d.get("FriendlyName") or "").lower()][:max_docs]
    rows: dict[dt.date, list] = {}
    for d in docs:
        blob = gc.fetch(f"{ERCOT}/misdownload/servlets/mirDownload?doclookupId={d.get('DocID')}",
                        binary=True)
        if blob:
            try:
                for day, rs in parse_np6346_hourly(blob).items():
                    if len(rs) > len(rows.get(day, [])):
                        rows[day] = rs
            except zipfile.BadZipFile:
                pass
        time.sleep(0.1)
    return {day: hour_path(rs, day, tz) for day, rs in rows.items() if len(rs) >= 23}


def fetch_candles(ticker: str, t0: int, t1: int):
    data = gc.fetch(f"{KALSHI}/series/{SERIES}/markets/{ticker}/candlesticks"
                    f"?start_ts={t0}&end_ts={t1}&period_interval=1")
    time.sleep(0.05)
    return None if data is None else parse_candles(data)


def fetch_tape(ticker: str, t0: int, t1: int) -> list:
    out, cursor = [], ""
    for _ in range(20):
        page = gc.fetch(f"{KALSHI}/markets/trades?ticker={ticker}&min_ts={t0}&max_ts={t1}"
                        f"&limit=1000&cursor={cursor}")
        for t in (page or {}).get("trades") or []:
            p = parse_trade(t)
            if p:
                out.append(p)
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not (page or {}).get("trades"):
            break
        time.sleep(0.05)
    return out


def five_min_by_day(tz) -> dict[dt.date, list[tuple[float, float]]]:
    sd = gc.fetch(f"{ERCOT}/api/1/services/read/dashboards/supply-demand.json")
    out = defaultdict(list)
    for p in (sd or {}).get("data") or []:
        v = _num(p.get("demand"))
        if v and p.get("epoch"):
            t = p["epoch"] / 1000
            out[dt.datetime.fromtimestamp(t, tz).date()].append((t, v))
    return {d: sorted(v) for d, v in out.items()}


def live_book_depth(rungs) -> list[float]:
    """Description only: resting size at the best ask for open rung-sides priced 85-97c."""
    sizes = []
    for r in rungs[:60]:
        ob = gc.fetch(f"{KALSHI}/markets/{r['ticker']}/orderbook")
        time.sleep(0.05)
        book = (ob or {}).get("orderbook_fp") or (ob or {}).get("orderbook") or {}
        for other in ("no", "yes"):
            # a side's ask is 100 minus the best bid on the OTHER side
            lv = book.get(f"{other}_dollars") or book.get(other) or []
            best = None
            for lvl in lv:
                try:
                    p, q = float(lvl[0]), float(lvl[1])
                except (TypeError, ValueError, IndexError):
                    continue
                pc = p * 100.0 if p <= 1.0 else p
                if best is None or pc > best[0]:
                    best = (pc, q)
            if best and BAND_LO_C <= 100.0 - best[0] <= BAND_HI_C:
                sizes.append(best[1])
    return sizes


# ---- report -----------------------------------------------------------------------------------


def _slice(entries, key, label):
    groups = defaultdict(list)
    for e in entries:
        groups[key(e)].append(e)
    print(f"  by {label}:")
    for k in sorted(groups):
        print(f"    {str(k):>14}  {_fmt(summarize(groups[k]))}")


def _margin_bucket(e) -> str:
    gap = abs((e["m"] or 0.0) - e["strike"])
    for lo, hi in MARGIN_BUCKETS:
        if lo <= gap < hi:
            return f"{lo}-{hi if hi < 10 ** 8 else 'inf'} MW"
    return "?"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--max-docs", type=int, default=75, help="NP6-346 csv files to pull")
    ap.add_argument("--max-rungs", type=int, default=1200, help="bounds candle API cost")
    ap.add_argument("--no-dump", action="store_true", help="omit the per-entry ledger")
    args = ap.parse_args(argv)
    tz = gc._ct()
    lag_s = LAG_MIN * 60
    print("GRIDPIN full probe (P1-P4) — read-only; pre-registered in docs/GRIDPIN_THESIS.md\n")

    rungs = kalshi_rungs()
    paths = np6346_paths(args.max_docs, tz)
    settled = [r for r in rungs if r["result"] in ("yes", "no") and r["day"] in paths
               and r["strike"] is not None and r["close_ts"]]
    settled = settled[: args.max_rungs]
    days = sorted({r["day"] for r in settled})
    print("== data ==")
    print(f"  rungs listed {len(rungs)}; NP6-346 hourly days {len(paths)} "
          f"({min(paths) if paths else '-'} .. {max(paths) if paths else '-'})")
    print(f"  graded universe: {len(settled)} settled rungs over {len(days)} operating days "
          f"(strike types {sorted({r['strike_type'] for r in settled})})")
    if not settled:
        print("\n== verdict ==\n  BLOCKED_DATA — no settled rung overlaps the NP6-346 window")
        return 0

    # C1 re-check on this window (the grade the whole probe rests on)
    agree = sum(gc.rung_result_from(max(mw for _e, mw in paths[r["day"]]), r["strike"],
                                    r["strike_type"]) == r["result"] for r in settled)
    print(f"  NP6-346 daily max reproduces {agree}/{len(settled)} results "
          f"({agree / len(settled):.1%})")

    candles, n_c, n_quoted = {}, 0, 0
    for r in settled:
        t0 = int(dt.datetime.combine(r["day"], dt.time(0), tz).timestamp())
        cs = fetch_candles(r["ticker"], t0, int(r["close_ts"]) + 60)
        if cs:
            candles[r["ticker"]] = cs
            n_c += len(cs)
            n_quoted += sum(1 for _ts, yb, ya in cs
                            if side_ask_c(yb, ya, "yes") or side_ask_c(yb, ya, "no"))
    print(f"  candles: {len(candles)}/{len(settled)} rungs returned candles; {n_c:,} candles, "
          f"{n_quoted:,} with a two-sided-or-one-sided quote")

    # ---- P1 ----
    p1 = build_entries(settled, paths, candles, tz, lag_s)
    s1 = summarize(p1)
    v1, why1 = p1_verdict(s1)
    print(f"\n== P1 decided-side discount (lag {LAG_MIN} min, cap {ENTRY_CAP_C:.0f}c) ==")
    print(f"  all      {_fmt(s1)}")
    for side in ("yes", "no"):
        print(f"  {side:<8} {_fmt(summarize([e for e in p1 if e['side'] == side]))}")
    if p1:
        _slice(p1, lambda e: f"{e['hour']:02d}h CT", "entry hour")
        _slice(p1, _margin_bucket, "margin to strike")
        _slice(p1, lambda e: "<85c" if e["ask"] < BAND_LO_C else "85-97c", "ask band")
        mid = days[len(days) // 2]
        h1 = summarize([e for e in p1 if e["day"] < mid])
        h2 = summarize([e for e in p1 if e["day"] >= mid])
        print(f"  split-half: first ({days[0]}..) {_fmt(h1)}")
        print(f"              second ({mid}..) {_fmt(h2)}")
        lagged = sorted((e["ts"] - e["t_dec"]) / 60 for e in p1)
        print(f"  minutes from decision to entry: median {statistics.median(lagged):.0f}, "
              f"share entered at the decision minute or the next {sum(x <= 1 for x in lagged) / len(lagged):.0%}")
        wrong = [e for e in p1 if not e["won"]]
        for e in wrong[:10]:
            print(f"    MISS {e['ticker']} {e['side']} @ {e['ask']:.0f}c  M={e['m']:,.0f} "
                  f"strike={e['strike']:,.0f} result={e['result']}")
    print(f"  P1: {v1} — {why1}")
    for lag in LAG_SENSITIVITY:
        print(f"  (sensitivity, never decides) lag {lag} min: "
              f"{_fmt(summarize(build_entries(settled, paths, candles, tz, lag * 60)))}")

    # ---- P2 ----
    band = [e for e in p1 if BAND_LO_C <= e["ask"] <= BAND_HI_C]
    ctrl = build_control(settled, paths, candles, tz, lag_s, band)
    sb, sc = summarize(band), summarize(ctrl)
    v2, why2 = p2_verdict(sb, sc)
    print("\n== P2 not just favorite drift (same day, same CT hour, 85-97c) ==")
    print(f"  P1 in-band  {_fmt(sb)}")
    print(f"  control     {_fmt(sc)}")
    print(f"  P2: {v2} — {why2}")

    # ---- P3 ----
    p3 = []
    five = five_min_by_day(tz)
    full_days = {d: v for d, v in five.items() if d in paths and len(v) >= 280}
    for r in settled:
        v5 = full_days.get(r["day"])
        cs = candles.get(r["ticker"])
        if not v5 or not cs:
            continue
        hmax = max(mw for _e, mw in paths[r["day"]])
        if not hmax < r["strike"] <= max(v for _t, v in v5):
            continue
        cross = next((t for t, v in v5 if v >= r["strike"]), None)
        ts, ask = first_entry(cs, "no", cross, r["close_ts"], 0.0, 99.0)
        if ts is not None:
            won = r["result"] == "no"
            p3.append({"won": won, "pnl": net_pnl_c(ask, won)})
    s3 = summarize(p3)
    v3, why3 = p3_verdict(s3)
    print("\n== P3 mechanics sub-claim (5-min vs hourly gap class) ==")
    print(f"  5-min demand days available {sorted(five)}; full graded days {len(full_days)}")
    print(f"  gap class NO at ask  {_fmt(s3)}")
    print(f"  P3: {v3} — {why3}")

    # ---- P4 ----
    depths = []
    tapes: dict[str, list] = {}
    for e in p1:
        if e["ticker"] not in tapes:
            r = next(x for x in settled if x["ticker"] == e["ticker"])
            t0 = int(dt.datetime.combine(r["day"], dt.time(0), tz).timestamp())
            tapes[e["ticker"]] = fetch_tape(e["ticker"], t0, int(r["close_ts"]) + 60)
        depths.append(tape_depth(tapes[e["ticker"]], e["side"], e["ts"], e["ask"],
                                 DEPTH_WINDOW_S))
    v4, why4 = p4_verdict(depths, len(p1), len(days))
    print("\n== P4 capacity (tape proxy: same-side taker prints at <= our price, -60 s..+30 min) ==")
    if depths:
        q = sorted(depths)
        print(f"  depth per entry: median {statistics.median(q):.0f}, p25 {q[len(q) // 4]:.0f}, "
              f"p75 {q[(3 * len(q)) // 4]:.0f}; share with any print {sum(d > 0 for d in q) / len(q):.0%}")
    open_rungs = [r for r in rungs if r["status"] in ("active", "open")]
    live = live_book_depth(open_rungs)
    print(f"  (description) live book, open rung-sides asked 85-97c: n={len(live)}, median best-ask "
          f"size {statistics.median(live) if live else 0:.0f}")
    print(f"  P4: {v4} — {why4}")

    v, why = decision(v1, v2, v3, v4)
    print("\n== verdict ==")
    print(f"  {v} — {why}")
    print(f"   P1 {v1} | P2 {v2} | P3 {v3} | P4 {v4}")
    print(f"   graded window {days[0]} .. {days[-1]} ({len(days)} days; NP6-346 retention is "
          f"rolling, so a re-run sees a later window)")

    if not args.no_dump and p1:
        print("\n-- P1 ledger (day,ticker,side,entry_ct,ask_c,decided_ct,m_mw,strike,result,pnl_c) --")
        for e in sorted(p1, key=lambda e: (e["day"], e["ts"])):
            print(f"  {e['day']},{e['ticker']},{e['side']},"
                  f"{dt.datetime.fromtimestamp(e['ts'], tz):%H:%M},{e['ask']:.0f},"
                  f"{dt.datetime.fromtimestamp(e['t_dec'], tz):%H:%M},{e['m']:.0f},"
                  f"{e['strike']:.0f},{e['result']},{e['pnl']:+.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
