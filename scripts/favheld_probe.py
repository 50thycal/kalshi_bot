"""FAVHELD probe: is the side spot already sits on underpriced on the hourly BTC ladder?

Pre-registration: docs/FAVHELD_PREREG.md (P0-P2, two looks, KILL). Constants below are copied from it;
tests pin them. Forward-only: every graded decision is on an ET date >= 2026-11-10.

Read-only. Reuses LEVELHOLD's decision-row loader and held-side helpers and MOMO-H's labels and
day-block bootstrap. DATABASE_URL_RO + Kalshi PUBLIC REST. No orders, no writes.

Usage (ops channel):
    {"type":"script","name":"favheld_probe","args":["--start","2026-11-10","--end","2027-01-08","--look","1"]}
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kalshi_settled as ks  # noqa: E402
import levelhold_probe as lp  # noqa: E402
import momo_hourly_probe as mh  # noqa: E402

# --- pre-registered constants (docs/FAVHELD_PREREG.md) ------------------------------------
SERIES = "KXBTCD"
FORWARD_START = dt.date(2026, 11, 10)
LOOK_MIN_DAYS = {1: 60, 2: 120}
P0_MIN_DECISIONS, P0_MAX_EXCLUSION = 4000, 0.10
P1_RESID_CENTS, P1_CI = 2.0, 0.90
P2_NET_EV_CENTS, P2_LOWER_PCT = 1.0, 0.025
FEE_QTY = 10
BOOT_N, BOOT_SEED = 2000, 20261110
WINDOW_HOURS = (9, 10, 16)          # VOLCLOCK's definition (ET close hour, weekday non-holiday)
HOLIDAYS = frozenset({dt.date(2026, 11, 26), dt.date(2026, 12, 25), dt.date(2027, 1, 1),
                      dt.date(2027, 1, 18), dt.date(2027, 2, 15)})
BANDS = ((0.0, 65.0, "<65"), (65.0, 75.0, "65-75"), (75.0, 101.0, ">=75"))


# --- pure functions (tested) ------------------------------------------------------------------


def hour_class(day: dt.date, event_ticker: str) -> str:
    """WINDOW = weekday non-holiday close hour 09/10/16 ET (from the event ticker's hour); else QUIET."""
    hour = int(event_ticker[-2:])
    if day.weekday() < 5 and day not in HOLIDAYS and hour in WINDOW_HOURS:
        return "WINDOW"
    return "QUIET"


def held_ev(r: dict) -> float:
    return lp.held_leg_ev(r, buy_held=True)


def mean_resid(rows: list[dict]) -> float:
    return mh._mean([lp.resid(r) for r in rows]) if rows else float("nan")


def mean_ev(rows: list[dict]) -> float:
    return mh._mean([held_ev(r) for r in rows]) if rows else float("nan")


def event_mean_ev(rows: list[dict]) -> float:
    by_ev: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by_ev[r["ev"]].append(held_ev(r))
    return mh._mean([mh._mean(v) for v in by_ev.values()]) if by_ev else float("nan")


def verdict(look: int, n_days: int, n_dec: int, exclusion: float, r: float, r_ci: tuple[float, float],
            e: float, e_lo: float) -> str:
    """The pre-registered two-look rule. Look 2 is final: anything but PASS is KILL."""
    fails = []
    if n_days < LOOK_MIN_DAYS[look]:
        fails.append(f"{n_days} days < {LOOK_MIN_DAYS[look]}")
    if n_dec < P0_MIN_DECISIONS:
        fails.append(f"{n_dec} decisions < {P0_MIN_DECISIONS}")
    if exclusion > P0_MAX_EXCLUSION:
        fails.append(f"label exclusion {exclusion:.3f} > {P0_MAX_EXCLUSION}")
    if fails:
        return "HOLD (instrument: " + "; ".join(fails) + ") - re-run this same look when the floor clears"
    p1 = r >= P1_RESID_CENTS and r_ci[0] > 0
    p2 = e >= P2_NET_EV_CENTS and e_lo > 0
    if p1 and p2:
        return "PASS (P1+P2) - authorizes a PAPER-PACKAGE DRAFT only; real money stays an operator hard stop"
    if look == 1 and p1 and e > 0:
        return "HOLD (P1 holds, P2 not yet proven) - run look 2 on 2026-11-10..2027-03-09, the final look"
    why = "P1 fail: mechanism absent forward" if not p1 else "P2 fail: not tradeable after cost"
    if look == 2 and p1:
        why += " (unproven at 120 days)"
    return f"KILL ({why}) - held-side favourite-buy on KXBTCD closed for a taker; no variants"


def analyse(rows: list[dict], n_raw: int, n_no_label: int, look: int) -> dict:
    days = {r["day"] for r in rows}
    exclusion = n_no_label / n_raw if n_raw else 1.0
    r = mean_resid(rows)
    e = mean_ev(rows)
    boot_r = mh.day_bootstrap(rows, mean_resid, n=BOOT_N, seed=BOOT_SEED)
    boot_e = mh.day_bootstrap(rows, mean_ev, n=BOOT_N, seed=BOOT_SEED)
    a = (1.0 - P1_CI) / 2.0
    r_ci = (mh.pct(boot_r, a), mh.pct(boot_r, 1.0 - a))
    e_lo = mh.pct(boot_e, P2_LOWER_PCT)

    def block(sub: list[dict]) -> dict:
        return {"n": len(sub), "R": _r(mean_resid(sub)), "E": _r(mean_ev(sub)),
                "mid_held": _r(mh._mean([x["mid_held"] for x in sub]) if sub else float("nan"))}

    by_hour = {c: block([x for x in rows if x["cls"] == c]) for c in ("QUIET", "WINDOW")}
    by_band = {lab: block([x for x in rows if lo <= x["mid_held"] < hi]) for lo, hi, lab in BANDS}
    return {
        "look": look, "n_days": len(days), "n_raw": n_raw, "n_decisions": len(rows),
        "n_events": len({x["ev"] for x in rows}), "label_exclusion": round(exclusion, 4),
        "R": _r(r), "R_ci90": [_r(r_ci[0]), _r(r_ci[1])],
        "E": _r(e), "E_p2_5": _r(e_lo), "E_p5": _r(mh.pct(boot_e, 0.05)),
        "E_event_mean": _r(event_mean_ev(rows)),
        "descriptive_by_hour": by_hour, "descriptive_by_band": by_band,
        "verdict": verdict(look, len(days), len(rows), exclusion, r, r_ci, e, e_lo),
    }


def _r(x: float):
    return None if (x is None or (isinstance(x, float) and math.isnan(x))) else round(x, 3)


def prepare(raw: list[dict], results: dict[str, int]) -> tuple[list[dict], int]:
    """Attach label, held side and hour class. Returns (rows, n_no_label)."""
    rows, no_label = [], 0
    for r in raw:
        if r["mk"] not in results:
            no_label += 1
            continue
        hv = lp.held_view(r)
        if hv is None:
            continue
        r.update(hv)
        r["y"] = results[r["mk"]]
        r["y_held"] = r["y"] if r["held_yes"] else 1 - r["y"]
        r["cls"] = hour_class(dt.date.fromisoformat(r["day"]), r["ev"])
        rows.append(r)
    return rows, no_label


# --- IO -------------------------------------------------------------------------------------


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default=FORWARD_START.isoformat())
    ap.add_argument("--end", required=True, help="inclusive ET date")
    ap.add_argument("--look", type=int, choices=(1, 2), required=True)
    args = ap.parse_args(argv)
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    if start < FORWARD_START:
        raise SystemExit(f"--start {start} is before the pre-registered forward start {FORWARD_START}: refused")
    import psycopg

    raw: list[dict] = []
    opts = "-c default_transaction_read_only=on -c statement_timeout=120000"
    with psycopg.connect(mh._db_url(), options=opts, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            day = start
            while day <= end:
                got = lp.load_day(cur, day)
                raw.extend(got)
                print(f"day {day}: {len(got)} decision rows", file=sys.stderr)
                day += dt.timedelta(days=1)

    results: dict[str, int] = {}
    ev_source: dict[str, str] = {}
    fetch_errors = 0
    by_ev: dict[str, set[str]] = defaultdict(set)
    for r in raw:
        by_ev[r["ev"]].add(r["mk"])
    for ev in sorted(by_ev):
        try:
            got, ev_source[ev] = mh.event_results(ev, by_ev[ev])
            results.update(got)
        except Exception as exc:  # noqa: BLE001 - an unreadable event is counted as excluded
            fetch_errors += 1
            ev_source[ev] = ks.NOT_FOUND
            print(f"result fetch failed {ev}: {exc}", file=sys.stderr)
        time.sleep(0.06)
    tally = ks.Tally()
    for r in raw:
        tally.add(ev_source.get(r["ev"], ks.NOT_FOUND) if r["mk"] in results else ks.NOT_FOUND)
    rows, no_label = prepare(raw, results)

    print(f"# FAVHELD probe look {args.look} - series {SERIES}, {start}..{end} (docs/FAVHELD_PREREG.md)")
    print(f"raw decisions {len(raw)}, no official label {no_label}, usable {len(rows)}")
    ks.report(tally)
    print(f"events {len(by_ev)}, result fetch errors {fetch_errors}")
    if len(rows) < 50:
        print("VERDICT: HOLD (instrument: P0 data floor) - too few usable rows to analyse")
        return 0
    out = analyse(rows, len(raw), no_label, args.look)
    print(json.dumps(out, indent=1))
    print(f"VERDICT: {out['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
