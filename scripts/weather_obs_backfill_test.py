"""Validate policy B (observation-confirmed entry) at SCALE on the backfill archive.

weather_entry_timing_backfill could only test PRICE policies because the backfill is
market-only. This script supplies the missing ingredient: it fetches the historical hourly
station observations from the IEM ASOS archive (the same METAR temps the live bot's NWS
client reads, but archived — api.weather.gov only keeps ~7 days) and reconstructs, at every
candle timestamp, the running observed extreme so far that local day. That lets us replay
the obs-confirmed entry (B) over all ~500 settled backfill events and compare it head-to-head
with the fixed windows and the conviction rule.

Because settlement winners are already known (backfill_weather_markets.result), the obs only
drive ENTRY TIMING here — small archive/QC differences cannot corrupt the win/loss labels.

Policies (all BUY THE FAVORITE, HOLD to settlement; only timing differs):
  fixed  — buy the favorite at the candle nearest each window (h20/h14/h8).
  A:conv — first hour the favorite's price clears a conviction bar.
  B:obs  — first hour the running observed max/min confirms the favorite bucket (+/-tol).

Network fetch (IEM) runs on the Actions runner. Self-contained (stdlib + psycopg). Usage:
    {"type":"script","name":"weather_obs_backfill_test","args":["--kind","high","--by-city"]}
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

RO_OPTIONS = (
    "-c default_transaction_read_only=on -c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)
LOOKBACK_HOURS = 26
IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"

# city code -> (IEM ASOS station id, IANA tz). Mirrors kalshi_bot/weather/cities.py
# (KMDW->MDW, KNYC->NYC, KPHL->PHL, ...): the NWS settlement stations, K-prefix dropped.
CITY_META = {
    "NYC": ("NYC", "America/New_York"),
    "CHI": ("MDW", "America/Chicago"),
    "MIA": ("MIA", "America/New_York"),
    "AUS": ("AUS", "America/Chicago"),
    "LAX": ("LAX", "America/Los_Angeles"),
    "PHIL": ("PHL", "America/New_York"),
    "DEN": ("DEN", "America/Denver"),
}


# --- pricing / buckets -------------------------------------------------------------


def fee_cents(entry: float) -> int:
    p = max(0.0, min(1.0, entry / 100.0))
    return math.ceil(7.0 * p * (1.0 - p))


def trade_pnl(win: bool, entry: float) -> float:
    return (100.0 if win else 0.0) - entry - fee_cents(entry)


def bucket_for_temp(temp, buckets: list[dict]) -> int | None:
    if temp is None:
        return None
    for i, b in enumerate(buckets):
        lo, hi = b.get("low"), b.get("high")
        if (lo is None or temp >= lo) and (hi is None or temp <= hi):
            return i
    best, best_d = None, None
    for i, b in enumerate(buckets):
        edges = [e for e in (b.get("low"), b.get("high")) if e is not None]
        d = min(abs(temp - e) for e in edges) if edges else 0.0
        if best_d is None or d < best_d:
            best, best_d = i, d
    return best


# --- IEM observation archive -------------------------------------------------------


def fetch_iem(station: str, d1: date, d2: date) -> list[tuple[datetime, float]]:
    """Hourly+special tmpf observations (UTC) for an ASOS station over [d1, d2]."""
    url = (f"{IEM_URL}?station={station}&data=tmpf"
           f"&year1={d1.year}&month1={d1.month}&day1={d1.day}"
           f"&year2={d2.year}&month2={d2.month}&day2={d2.day}"
           "&tz=Etc%2FUTC&format=onlycomma&latlon=no&missing=M&trace=T"
           "&report_type=3&report_type=4")
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (kalshi-research)"})
    text = None
    for attempt in range(5):  # IEM rate-limits bursts (429); back off and retry
        try:
            text = urlopen(req, timeout=180).read().decode("utf-8", "replace")  # noqa: S310
            break
        except HTTPError as exc:
            if exc.code == 429 and attempt < 4:
                time.sleep(6 * (attempt + 1))
                continue
            raise
    out: list[tuple[datetime, float]] = []
    for line in text.splitlines():
        if not line or line.startswith("station"):
            continue
        parts = line.split(",")
        if len(parts) < 3:
            continue
        valid, tmpf = parts[1].strip(), parts[2].strip()
        if tmpf in ("M", "T", ""):
            continue
        try:
            dt = datetime.strptime(valid, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            out.append((dt, float(tmpf)))
        except ValueError:
            continue
    return out


class ObsIndex:
    """Per-local-day running extreme lookup for one station."""

    def __init__(self, rows: list[tuple[datetime, float]], tz_name: str, kind: str):
        self.tz = ZoneInfo(tz_name)
        self.kind = kind
        days: dict[date, list] = defaultdict(list)
        for dt, t in rows:
            days[dt.astimezone(self.tz).date()].append((dt, t))
        self.days: dict[date, tuple[list, list]] = {}
        for d, lst in days.items():
            lst.sort()
            ext, cur = [], None
            for _, t in lst:
                cur = t if cur is None else (max(cur, t) if kind == "high" else min(cur, t))
                ext.append(cur)
            self.days[d] = ([x[0] for x in lst], ext)

    def running_extreme(self, target_date: date, ts: datetime):
        entry = self.days.get(target_date)
        if not entry:
            return None
        tslist, ext = entry
        i = bisect_right(tslist, ts) - 1
        return ext[i] if i >= 0 else None


# --- entry policies ----------------------------------------------------------------


def _band(cycles, a):
    return [c for c in cycles if a.htc_min <= c["htc"] <= a.htc_max]


def entry_A(cycles, a):
    for c in _band(cycles, a):
        if c["price"] is not None and c["price"] >= a.conv_threshold:
            return c
    return None


def entry_B(cycles, a, obs: ObsIndex, target_date: date, ladder: list[dict]):
    if obs is None or target_date is None:
        return None
    for c in _band(cycles, a):
        rm = obs.running_extreme(target_date, c["ts"])
        if rm is None:
            continue
        oidx = bucket_for_temp(rm, ladder)
        if oidx is not None and abs(oidx - c["fav_idx"]) <= a.tol:
            return c
    return None


def entry_fixed(cycles, windows):
    out = []
    if not cycles:
        return out
    for w in windows:
        c = min(cycles, key=lambda c: abs(c["htc"] - w))
        if abs(c["htc"] - w) <= 3.0:
            out.append(c)
    return out


# --- aggregation -------------------------------------------------------------------


class Cell:
    __slots__ = ("n", "wins", "pnl", "buy", "htc", "events")

    def __init__(self):
        self.n = self.wins = 0
        self.pnl = self.buy = self.htc = 0.0
        self.events = set()

    def add(self, c: dict):
        self.n += 1
        self.wins += 1 if c["win"] else 0
        self.pnl += trade_pnl(c["win"], c["ask"])
        self.buy += c["ask"]
        self.htc += c["htc"]
        self.events.add(c["event"])

    def row(self, total_events: int) -> str:
        if not self.n:
            return f"{0:6d} {'--':>5} {'--':>5} {'--':>6} {'--':>8} {'--':>7} {'--':>9}"
        cov = len(self.events) / total_events * 100 if total_events else 0
        return (f"{self.n:6d} {len(self.events):5d} {cov:4.0f}% {self.wins / self.n * 100:4.0f}%"
                f" {self.buy / self.n:5.1f}c {self.pnl / self.n:+7.1f}c {self.htc / self.n:6.1f}h"
                f" {self.pnl / 100:+8.2f}$")


def run_report(events, ladders, ev_meta, obs_by_city, windows, a, label: str) -> None:
    total = len(events)
    cells = {"fixed": Cell(), "A:conv": Cell(), "B:obs": Cell()}
    b_eligible = 0
    for ev, cycles in events.items():
        city, tdate = ev_meta.get(ev, (None, None))
        for c in entry_fixed(cycles, windows):
            cells["fixed"].add(c)
        ca = entry_A(cycles, a)
        if ca is not None:
            cells["A:conv"].add(ca)
        obs = obs_by_city.get(city)
        if obs is not None and tdate is not None:
            b_eligible += 1
            cb = entry_B(cycles, a, obs, tdate, ladders[ev])
            if cb is not None:
                cells["B:obs"].add(cb)
    wtxt = "/".join(f"h{int(w)}" for w in windows)
    print(f"\n=== Obs-backfill B test — {label} ({total} events, {b_eligible} with obs) ===")
    print("  policy        trades   ev cov%  win%  avg_buy  net/trd  htc@in  total$")
    for name in ("fixed", "A:conv", "B:obs"):
        tag = name if name != "fixed" else f"fixed({wtxt})"
        print(f"  {tag:<13} {cells[name].row(total)}")
    print(f"  A:conv = fav price >= {a.conv_threshold:g}c | B:obs = running observed extreme"
          f" confirms fav bucket (+/-{a.tol})")
    print("  all BUY THE FAVORITE and HOLD to settlement; band ="
          f" {a.htc_min:g}-{a.htc_max:g}h to close. net/trade is the deciding number.")


# --- data load ---------------------------------------------------------------------


def build_events(markets: dict, candles: list):
    ladders: dict = defaultdict(list)
    for mt, m in markets.items():
        ladders[m["event"]].append((mt, m))
    idx_of, ladder_buckets, winner_idx = {}, {}, {}
    for ev, items in ladders.items():
        items.sort(key=lambda x: (x[1]["low"] if x[1]["low"] is not None else -999.0))
        lb = []
        for i, (mt, m) in enumerate(items):
            idx_of[(ev, mt)] = i
            lb.append({"sub": m["sub"], "low": m["low"], "high": m["high"]})
            if m["result"] == "yes":
                winner_idx[ev] = i
        ladder_buckets[ev] = lb
    snaps: dict = defaultdict(dict)
    for mt, ts, ask, bid, close in candles:
        m = markets.get(mt)
        if m is None or m["close"] is None:
            continue
        price = close if close is not None else bid
        if price is None:
            continue
        ask_c = ask if ask is not None else (close if close is not None else bid)
        snaps[(m["event"], ts)][mt] = (float(ask_c), float(price))
    events: dict = defaultdict(list)
    for (ev, ts), legs in snaps.items():
        fav_mt = max(legs, key=lambda mt: legs[mt][1])
        m = markets[fav_mt]
        htc = (m["close"] - ts).total_seconds() / 3600.0
        if htc <= 0:
            continue
        ask, price = legs[fav_mt]
        events[ev].append({
            "event": ev, "ts": ts, "htc": htc, "price": price,
            "ask": max(1.0, min(99.0, ask)), "fav_idx": idx_of[(ev, fav_mt)],
            "win": idx_of[(ev, fav_mt)] == winner_idx.get(ev), "sub": m["sub"],
        })
    for cyc in events.values():
        cyc.sort(key=lambda c: -c["htc"])
    return events, ladder_buckets


def _parse_date(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--windows", default="20,14,8")
    ap.add_argument("--kind", choices=("high", "low"), default="high")
    ap.add_argument("--city", default="ALL")
    ap.add_argument("--tol", type=int, default=1)
    ap.add_argument("--conv-threshold", type=float, default=60.0, dest="conv_threshold")
    ap.add_argument("--htc-min", type=float, default=1.0, dest="htc_min")
    ap.add_argument("--htc-max", type=float, default=24.0, dest="htc_max")
    ap.add_argument("--by-city", action="store_true")
    args = ap.parse_args(argv)
    windows = [float(x) for x in args.windows.split(",") if x.strip()]

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    where, params = ["close_time IS NOT NULL", "event_ticker IS NOT NULL", "kind = %s"], [args.kind]
    if args.city != "ALL":
        where.append("city = %s")
        params.append(args.city)
    clause = " AND ".join(where)

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT market_ticker, event_ticker, city, subtitle, low_f, high_f, result,"
                f" close_time, target_date FROM backfill_weather_markets WHERE {clause}", params)
            markets, ev_meta = {}, {}
            for mt, ev, city, sub, low, high, result, close, tdate in cur.fetchall():
                markets[mt] = {"event": ev, "city": city, "sub": sub, "low": low,
                               "high": high, "result": result, "close": close}
                ev_meta[ev] = (city, _parse_date(tdate))
            cur.execute(
                "SELECT c.market_ticker, c.end_period_ts, c.yes_ask_close, c.yes_bid_close,"
                " c.price_close FROM backfill_weather_candles c"
                f" JOIN backfill_weather_markets m ON m.market_ticker = c.market_ticker WHERE {clause}"
                f" AND c.end_period_ts >= m.close_time - interval '{LOOKBACK_HOURS} hours'", params)
            candles = cur.fetchall()

    if not markets:
        print("No backfill markets matched.")
        return 0
    events, ladders = build_events(markets, candles)

    # date span + per-city obs fetch from IEM
    tdates = [d for _, d in ev_meta.values() if d is not None]
    d1, d2 = min(tdates), max(tdates)
    cities = sorted({m["city"] for m in markets.values() if m["city"]})
    obs_by_city: dict[str, ObsIndex] = {}
    print(f"=== Obs-backfill B test — kind={args.kind} city={args.city} ===")
    print(f"  {len(markets)} markets, {len(candles)} candles, {len(events)} events;"
          f" fetching IEM obs {d1}..{d2} for {len(cities)} stations")
    for i, city in enumerate(cities):
        meta = CITY_META.get(city)
        if not meta:
            print(f"  ! no station mapping for {city}; B skipped there", file=sys.stderr)
            continue
        if i:
            time.sleep(3)  # space requests so IEM doesn't 429 the burst
        station, tz = meta
        try:
            rows = fetch_iem(station, d1, d2)
            obs_by_city[city] = ObsIndex(rows, tz, args.kind)
            print(f"  {city:>4} ({station}/{tz}): {len(rows)} obs")
        except Exception as exc:  # noqa: BLE001 — network/source hiccup, keep going
            print(f"  ! IEM fetch failed for {city} ({station}): {exc}", file=sys.stderr)

    run_report(events, ladders, ev_meta, obs_by_city, windows, args,
               f"kind={args.kind} city={args.city}")
    if args.by_city:
        for city in cities:
            sub = {ev: cyc for ev, cyc in events.items() if ev_meta.get(ev, (None,))[0] == city}
            if sub:
                run_report(sub, ladders, ev_meta, obs_by_city, windows, args, f"city={city}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
