"""VOLCLOCK probe: does the hourly BTC ladder under-price moves in scheduled US volatility windows?

Pre-registration: docs/VOLCLOCK_PREREG.md (P0-P3, decision rule). Constants below are copied
from it; tests pin them.

Read-only. DATABASE_URL_RO (crypto_ladder_snapshots, one ET day per query, exact event tickers)
+ Kalshi PUBLIC REST for each market's official settlement result, live tier then historical tier
(scripts/kalshi_settled.py). No orders, no writes.

No lookahead: the decision row is the T-40 quote; the official result and the close spot only
SCORE it. Hour classes are a calendar fact known days ahead.

Usage (ops channel):
    {"type":"script","name":"volclock_probe","args":["--start","2026-09-01","--end","2026-11-05"]}
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import random
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kalshi_settled as ks  # noqa: E402

# --- pre-registered constants (docs/VOLCLOCK_PREREG.md) ------------------------------------
SERIES = "KXBTCD"
WINDOW_HOURS = (9, 10, 16)       # ET close hour: 08:30 slot, 09:30 open, into the 16:00 close
CONTROL_HOURS = (12, 13, 14)     # midday, no standard scheduled release
MTC_LO, MTC_HI, MTC_TARGET = 35.0, 50.0, 40.0
MID_LO, MID_HI = 10.0, 90.0
CLOSE_SPOT_TOL_MIN = 3
NYSE_HOLIDAYS = frozenset({dt.date(2026, 9, 7), dt.date(2026, 11, 26), dt.date(2026, 12, 25),
                           dt.date(2027, 1, 1)})
P0_MIN_TRADING_DAYS, P0_MIN_ROWS_PER_CLASS = 35, 300
P0_MAX_EXCLUSION, P0_MIN_CLOSE_COVERAGE, P0_MIN_HOUR_MATCH = 0.05, 0.90, 0.99
P1_MIN_RATIO = 1.15
KILL_CENTS, P2_SIGNAL_CENTS, P3_NET_EV_CENTS = 1.5, 3.0, 1.5
FEE_QTY = 10
BOOT_N, BOOT_SEED = 2000, 20261106

_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

# All 24 events feed `caps` (the close spot of a 16:00 event is read from the 17:00 event's
# captures); decision rows come only from the six classed hours.
DAY_SQL = """
with s as materialized (
  select event_ticker ev, market_ticker mk, captured_at t, minutes_to_close mtc, mid_cents mid,
         yes_ask_cents ask, yes_bid_cents bid, spot, floor_strike k
  from crypto_ladder_snapshots
  where event_ticker = any(%(evs)s) and strike_type = 'greater'),
caps as (select t, max(spot) spot from s where spot is not null group by t),
dec as (select distinct on (ev, mk) ev, mk, t, mtc, mid, ask, bid, spot, k from s
        where ev = any(%(dec_evs)s)
          and mtc between %(mtc_lo)s and %(mtc_hi)s and mid between %(mid_lo)s and %(mid_hi)s
          and ask is not null and bid is not null
        order by ev, mk, abs(mtc - %(mtc_t)s), t)
select d.ev, d.mk, d.t, d.mtc, d.mid, d.ask, d.bid, d.spot, d.k,
       (select c.spot from caps c
         where c.t between d.t + make_interval(secs => d.mtc * 60) - make_interval(mins => %(tol)s)
                       and d.t + make_interval(secs => d.mtc * 60) + make_interval(mins => %(tol)s)
         order by abs(extract(epoch from (c.t - d.t)) - d.mtc * 60) limit 1) as s_close
from dec d
"""


# --- pure functions (tested) ------------------------------------------------------------------

def fee_cents(price_cents: float, qty: int = FEE_QTY) -> float:
    """Kalshi taker fee per contract: ceil(0.07*q*P*(1-P)*100)/q cents."""
    p = min(max(price_cents / 100.0, 0.0), 1.0)
    return math.ceil(round(0.07 * qty * p * (1 - p) * 100, 9)) / qty


def day_event_tickers(series: str, day: dt.date) -> list[str]:
    """The 24 exact hourly event tickers of one ET date (never a string range: prod collates
    en_US.utf8 - MOMO-H amendment A2)."""
    tag = f"{day:%y}{_MONTHS[day.month - 1]}{day:%d}"
    return [f"{series}-{tag}{h:02d}" for h in range(24)]


def ticker_hour(event_ticker: str) -> int:
    return int(event_ticker[-2:])


def is_trading_day(day: dt.date) -> bool:
    return day.weekday() < 5 and day not in NYSE_HOLIDAYS


def hour_class(day: dt.date, hour: int) -> str | None:
    """WINDOW / CONTROL on trading days; WKND_W / WKND_C (descriptive) on weekends; else None."""
    if hour not in WINDOW_HOURS and hour not in CONTROL_HOURS:
        return None
    w = hour in WINDOW_HOURS
    if is_trading_day(day):
        return "WINDOW" if w else "CONTROL"
    if day.weekday() >= 5:
        return "WKND_W" if w else "WKND_C"
    return None   # weekday holiday: excluded from everything


def _nth_sunday(year: int, month: int, n: int) -> dt.date:
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(6 - d.weekday()) % 7)
    return d + dt.timedelta(days=7 * (n - 1))


def utc_to_et(ts: dt.datetime) -> dt.datetime:
    """US Eastern from UTC by the statutory rule (DST from 2nd Sun Mar 07:00Z to 1st Sun Nov
    06:00Z). Stdlib-only so the probe does not depend on tzdata in the runner."""
    if ts.tzinfo is not None:
        ts = ts.astimezone(dt.timezone.utc).replace(tzinfo=None)
    y = ts.year
    start = dt.datetime.combine(_nth_sunday(y, 3, 2), dt.time(7))
    end = dt.datetime.combine(_nth_sunday(y, 11, 1), dt.time(6))
    return ts + dt.timedelta(hours=-4 if start <= ts < end else -5)


def close_hour_et(captured_at: dt.datetime, mtc: float) -> int:
    """ET hour of the close implied by the capture, rounded to the nearest hour."""
    et = utc_to_et(captured_at + dt.timedelta(minutes=mtc))
    return (et + dt.timedelta(minutes=30)).hour


def crossing(mid: float, ask: float, bid: float, spot: float, strike: float, y: int) -> dict | None:
    """The side that wins only if BTC crosses the strike. None if spot sits on the strike."""
    if spot < strike:
        return {"side": "yes", "px_mid": mid, "yx": y, "buy_cross": ask, "buy_other": 100.0 - bid}
    if spot > strike:
        return {"side": "no", "px_mid": 100.0 - mid, "yx": 1 - y,
                "buy_cross": 100.0 - bid, "buy_other": ask}
    return None


def _mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def resid(r: dict) -> float:
    return 100.0 * r["yx"] - r["px_mid"]


def leg_ev(r: dict, cross: bool) -> float:
    """Net EV (cents/contract) of a taker buy of the crossing (or non-crossing) side, held."""
    px = r["buy_cross"] if cross else r["buy_other"]
    win = r["yx"] if cross else 1 - r["yx"]
    return 100.0 * win - px - fee_cents(px)


def diff_d(rows: list[dict]) -> float:
    w = [resid(r) for r in rows if r["cls"] == "WINDOW"]
    c = [resid(r) for r in rows if r["cls"] == "CONTROL"]
    return _mean(w) - _mean(c) if w and c else float("nan")


def vol_ratio(events: list[dict]) -> float:
    w = [e["move"] for e in events if e["cls"] == "WINDOW" and e["move"] is not None]
    c = [e["move"] for e in events if e["cls"] == "CONTROL" and e["move"] is not None]
    if not w or not c or _mean(c) <= 0:
        return float("nan")
    return _mean(w) / _mean(c)


def trade_ev(rows: list[dict], sign: int) -> float:
    evs = [leg_ev(r, cross=sign > 0) for r in rows if r["cls"] == "WINDOW"]
    return _mean(evs) if evs else float("nan")


def day_bootstrap(items: list[dict], stat, n: int = BOOT_N, seed: int = BOOT_SEED) -> list[float]:
    by_day = defaultdict(list)
    for it in items:
        by_day[it["day"]].append(it)
    days = sorted(by_day)
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        sample = []
        for _d in days:
            sample.extend(by_day[days[rng.randrange(len(days))]])
        v = stat(sample)
        if not math.isnan(v):
            out.append(v)
    out.sort()
    return out


def pct(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return float("nan")
    return sorted_vals[min(len(sorted_vals) - 1, max(0, int(p * len(sorted_vals))))]


def p0_failures(n_tdays: int, n_win: int, n_ctl: int, exclusion: float, close_cov: float,
                hour_match: float) -> list[str]:
    f = []
    if n_tdays < P0_MIN_TRADING_DAYS:
        f.append(f"trading days {n_tdays} < {P0_MIN_TRADING_DAYS}")
    if n_win < P0_MIN_ROWS_PER_CLASS or n_ctl < P0_MIN_ROWS_PER_CLASS:
        f.append(f"rows WINDOW {n_win} / CONTROL {n_ctl} < {P0_MIN_ROWS_PER_CLASS}")
    if exclusion > P0_MAX_EXCLUSION:
        f.append(f"label exclusion {exclusion:.3f} > {P0_MAX_EXCLUSION}")
    if close_cov < P0_MIN_CLOSE_COVERAGE:
        f.append(f"close-spot coverage {close_cov:.3f} < {P0_MIN_CLOSE_COVERAGE}")
    if hour_match < P0_MIN_HOUR_MATCH:
        f.append(f"ET hour match {hour_match:.4f} < {P0_MIN_HOUR_MATCH}")
    return f


def verdict(p0: list[str], r: float, r_lo: float, d: float, d_lo: float, d_hi: float,
            ev: float, ev_p5: float) -> str:
    if p0:
        return "HOLD (instrument: P0 - " + "; ".join(p0) + ")"
    if math.isnan(r) or r < P1_MIN_RATIO or not r_lo > 1.0:
        return "KILL (P1: mechanism absent - BTC not measurably more volatile in the windows)"
    excludes_zero = d_lo > 0 or d_hi < 0
    if math.isnan(d) or abs(d) < KILL_CENTS:
        return "KILL (P2: the clock is priced)"
    if not excludes_zero:
        if abs(d) < P2_SIGNAL_CENTS:
            return "KILL (P2: the clock is priced - CI includes 0)"
        return "HOLD (P2 under-powered: re-read at ~2x trading days, ~2027-01-15)"
    if abs(d) < P2_SIGNAL_CENTS:
        return "HOLD (P2 weak signal: re-read at ~2x trading days, ~2027-01-15)"
    if ev >= P3_NET_EV_CENTS and ev_p5 > 0:
        return "PASS (P0-P3) - authorizes a paper-package draft only"
    return "P2 SIGNAL / P3 FAIL (real but not tradeable after cost)"


def build_rows(raw: list[dict], results: dict[str, int]) -> tuple[list[dict], dict]:
    """Attach labels + crossing side. Counts: exclusion is label failures only (as MOMO-H A1);
    rows with no spot/strike or spot on the strike are dropped and reported separately."""
    rows, no_label, dropped = [], 0, 0
    for r in raw:
        if r["mk"] not in results:
            no_label += 1
            continue
        if r["spot"] is None or r["k"] is None:
            dropped += 1
            continue
        x = crossing(r["mid"], r["ask"], r["bid"], r["spot"], r["k"], results[r["mk"]])
        if x is None:
            dropped += 1
            continue
        rows.append({**r, **x})
    return rows, {"no_label": no_label, "dropped": dropped}


def events_of(raw: list[dict]) -> list[dict]:
    """One realized-move record per event (decision spot and close spot are event-level)."""
    by_ev: dict[str, dict] = {}
    for r in raw:
        e = by_ev.setdefault(r["ev"], {"ev": r["ev"], "day": r["day"], "cls": r["cls"],
                                       "wd": r["wd"], "hour": r["hour"], "move": None})
        if e["move"] is None and r["spot"] and r["s_close"]:
            e["move"] = 10000.0 * abs(math.log(r["s_close"] / r["spot"]))
    return list(by_ev.values())


def _slice(rows: list[dict], events: list[dict], sign: int, label: str) -> dict:
    mv = [e["move"] for e in events if e["move"] is not None]
    return {"slice": label, "rows": len(rows), "events": len(events),
            "move_bp": round(_mean(mv), 2) if mv else None,
            "cross_px": round(_mean([r["px_mid"] for r in rows]), 2) if rows else None,
            "cross_won": round(100 * _mean([r["yx"] for r in rows]), 2) if rows else None,
            "resid": round(_mean([resid(r) for r in rows]), 2) if rows else None,
            "ev_buy_cross": round(_mean([leg_ev(r, True) for r in rows]), 2) if rows else None,
            "ev_buy_other": round(_mean([leg_ev(r, False) for r in rows]), 2) if rows else None}


def analyse(rows: list[dict], events: list[dict], counts: dict) -> dict:
    core = [r for r in rows if r["cls"] in ("WINDOW", "CONTROL")]
    core_ev = [e for e in events if e["cls"] in ("WINDOW", "CONTROL")]
    n_win = sum(1 for r in core if r["cls"] == "WINDOW")
    n_ctl = len(core) - n_win
    tdays = {r["day"] for r in core}
    r_ratio = vol_ratio(core_ev)
    boot_r = day_bootstrap(core_ev, vol_ratio)
    d = diff_d(core)
    boot_d = day_bootstrap(core, diff_d)
    sign = 1 if (math.isnan(d) or d >= 0) else -1
    ev = trade_ev(core, sign)
    boot_ev = day_bootstrap(core, lambda rs: trade_ev(rs, sign))
    close_cov = (sum(1 for e in core_ev if e["move"] is not None) / len(core_ev)) if core_ev else 0.0
    out = {
        "trading_days": len(tdays), "rows_window": n_win, "rows_control": n_ctl,
        "exclusion": counts["exclusion"], "dropped": counts["dropped"],
        "hour_match": counts["hour_match"], "close_spot_coverage": round(close_cov, 4),
        "R": round(r_ratio, 4), "R_ci90": [round(pct(boot_r, 0.05), 4), round(pct(boot_r, 0.95), 4)],
        "D": round(d, 3), "D_ci90": [round(pct(boot_d, 0.05), 3), round(pct(boot_d, 0.95), 3)],
        "trade": "buy crossing side" if sign > 0 else "buy non-crossing side",
        "trade_ev": round(ev, 3), "trade_ev_p5": round(pct(boot_ev, 0.05), 3),
    }
    p0 = p0_failures(len(tdays), n_win, n_ctl, counts["exclusion"], close_cov, counts["hour_match"])
    out["p0_failures"] = p0
    out["verdict"] = verdict(p0, r_ratio, out["R_ci90"][0], d, out["D_ci90"][0], out["D_ci90"][1],
                             ev, out["trade_ev_p5"])
    # --- descriptive, decides nothing ---
    sl = []
    for cls in ("WINDOW", "CONTROL", "WKND_W", "WKND_C"):
        sl.append(_slice([r for r in rows if r["cls"] == cls],
                         [e for e in events if e["cls"] == cls], sign, cls))
    for h in WINDOW_HOURS + CONTROL_HOURS:
        cls = "WINDOW" if h in WINDOW_HOURS else "CONTROL"
        sl.append(_slice([r for r in rows if r["cls"] == cls and r["hour"] == h],
                         [e for e in events if e["cls"] == cls and e["hour"] == h], sign,
                         f"{cls} {h:02d}:00"))
    sl.append(_slice([r for r in rows if r["cls"] == "WINDOW" and r["hour"] == 9 and r["wd"] == 3],
                     [e for e in events if e["cls"] == "WINDOW" and e["hour"] == 9 and e["wd"] == 3],
                     sign, "09:00 Thursday (claims)"))
    sl.append(_slice([r for r in rows if r["cls"] == "WINDOW" and r["hour"] == 9 and r["wd"] != 3],
                     [e for e in events if e["cls"] == "WINDOW" and e["hour"] == 9 and e["wd"] != 3],
                     sign, "09:00 other weekdays"))
    out["slices"] = sl
    wk = [r for r in rows if r["cls"] in ("WKND_W", "WKND_C")]
    w = [resid(r) for r in wk if r["cls"] == "WKND_W"]
    c = [resid(r) for r in wk if r["cls"] == "WKND_C"]
    out["D_weekend_same_clock"] = round(_mean(w) - _mean(c), 3) if w and c else None
    return out


# --- IO -------------------------------------------------------------------------------------

def event_results(event_ticker: str, tickers=()) -> tuple[dict[str, int], str]:
    """({market: 1|0}, source). Live first, then Kalshi's historical tier (XOS-000048): settled
    markets older than the cutoff are not served by live /markets, and losing them is silent."""
    markets, source = ks.event_markets(event_ticker, tickers)
    out: dict[str, int] = {}
    for m in markets:
        res = ks.result_of(m)
        if res is not None:
            out[m["ticker"]] = 1 if res == "yes" else 0
    return out, source


def label_tally(raw: list[dict], results: dict[str, int], ev_source: dict[str, str]) -> ks.Tally:
    """Decision rows by where their label came from; a row with no yes/no result is not_found."""
    tally = ks.Tally()
    for r in raw:
        tally.add(ev_source.get(r["ev"], ks.NOT_FOUND) if r["mk"] in results else ks.NOT_FOUND)
    return tally


def _db_url() -> str:
    url = (os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if not url:
        raise SystemExit("DATABASE_URL_RO (or DATABASE_URL) is not set.")
    return url


def _f(x):
    return float(x) if x is not None else None


def load_day(cur, day: dt.date) -> list[dict]:
    evs = day_event_tickers(SERIES, day)
    dec_evs = [e for e in evs if hour_class(day, ticker_hour(e)) is not None]
    if not dec_evs:
        return []
    cur.execute(DAY_SQL, {"evs": evs, "dec_evs": dec_evs, "mtc_lo": MTC_LO, "mtc_hi": MTC_HI,
                          "mid_lo": MID_LO, "mid_hi": MID_HI, "mtc_t": MTC_TARGET,
                          "tol": CLOSE_SPOT_TOL_MIN})
    rows = []
    for ev, mk, t, mtc, mid, ask, bid, spot, k, s_close in cur.fetchall():
        h = ticker_hour(ev)
        rows.append({"ev": ev, "mk": mk, "day": day.isoformat(), "wd": day.weekday(), "hour": h,
                     "cls": hour_class(day, h), "mid": float(mid), "ask": float(ask),
                     "bid": float(bid), "spot": _f(spot), "k": _f(k), "s_close": _f(s_close),
                     "hour_ok": close_hour_et(t, float(mtc)) == h})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2026-09-01")
    ap.add_argument("--end", default="2026-11-05", help="inclusive ET date")
    ap.add_argument("--max-days", type=int, default=150)
    args = ap.parse_args(argv)
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    import psycopg

    raw: list[dict] = []
    opts = "-c default_transaction_read_only=on -c statement_timeout=120000"
    with psycopg.connect(_db_url(), options=opts, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            day, n = start, 0
            while day <= end and n < args.max_days:
                got = load_day(cur, day)
                raw.extend(got)
                print(f"day {day}: {len(got)} decision rows", file=sys.stderr)
                day += dt.timedelta(days=1)
                n += 1
    results: dict[str, int] = {}
    ev_source: dict[str, str] = {}
    fetch_err = 0
    by_ev: dict[str, set[str]] = defaultdict(set)
    for r in raw:
        by_ev[r["ev"]].add(r["mk"])
    for ev in sorted(by_ev):
        try:
            got, ev_source[ev] = event_results(ev, by_ev[ev])
            results.update(got)
        except Exception as exc:  # noqa: BLE001 - an unreadable event is counted as excluded
            fetch_err += 1
            ev_source[ev] = ks.NOT_FOUND
            print(f"result fetch failed {ev}: {exc}", file=sys.stderr)
        time.sleep(0.06)
    tally = label_tally(raw, results, ev_source)
    hour_match = _mean([1.0 if r["hour_ok"] else 0.0 for r in raw]) if raw else 0.0
    rows, counts = build_rows(raw, results)
    counts["exclusion"] = round(counts["no_label"] / len(raw), 4) if raw else 1.0
    counts["hour_match"] = round(hour_match, 4)
    print(f"# VOLCLOCK probe - series {SERIES}, {start}..{end} (docs/VOLCLOCK_PREREG.md)")
    print(f"raw decisions {len(raw)}, labelled+classed {len(rows)}, no official label "
          f"{counts['no_label']} (P0 exclusion), dropped (no spot/strike or on strike) "
          f"{counts['dropped']}, result fetch errors {fetch_err}, ET hour match {hour_match:.4f}")
    ks.report(tally)
    if len(rows) < 50:
        print("VERDICT: HOLD (instrument: P0 data floor) - too few usable rows to analyse")
        return 0
    out = analyse(rows, events_of(raw), counts)
    print(json.dumps({k: v for k, v in out.items() if k != "slices"}, indent=1))
    print("descriptive slices (decide nothing):")
    print(f"{'slice':<26} {'rows':>5} {'evts':>5} {'|move|bp':>8} {'xpx':>6} {'xwon%':>6} "
          f"{'resid':>6} {'EVbuyX':>7} {'EVbuyO':>7}")
    for s in out["slices"]:
        print(f"{s['slice']:<26} {s['rows']:>5} {s['events']:>5} {s['move_bp']!s:>8} "
              f"{s['cross_px']!s:>6} {s['cross_won']!s:>6} {s['resid']!s:>6} "
              f"{s['ev_buy_cross']!s:>7} {s['ev_buy_other']!s:>7}")
    print(f"VERDICT: {out['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
