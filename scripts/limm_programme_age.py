"""LIMM PROGRAMME AGE — does an incentive programme pay more per contract in its first hours?

Pre-registered in docs/LIMM_PROGRAMME_AGE.md (2026-11-08, DEC-044.2). Run ONCE; its verdict
sets S4-burst's timing in docs/LIMM_PAPER_STRATEGIES.md. Every constant marked pre-registered is
frozen there.

Reward per contract-hour of a $10 cheap-side bid (study 3's estimator and qualifying rules,
imported unchanged) is bucketed by programme age; the primary statistic compares the first 24 h
with 3 d+ WITHIN programmes observed in both. Fill rates per resting market-hour come from
study 5's replay (`simulate_hour`, conservative model) on hours BEFORE study 5's window only.

`--census` (handoff §5 step 0): counts of programmes in the $1-minimum band, no outcomes.

Read-only: DATABASE_URL_RO (read-only transaction). Stdlib + psycopg.

Usage: {"type":"script","name":"limm_programme_age","args":[],"id":"rl-age-run1-<date>"}
       {"type":"script","name":"limm_programme_age","args":["--census"],"id":"rl-age-census-<date>"}
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from limm_fill_hazard import simulate_hour, trade_time  # noqa: E402  (study 5's replay)
from limm_programme_outcomes import (  # noqa: E402  (study 3's construction, unchanged)
    THIN_SHARE,
    _aware,
    cheap_side,
    hourly_reward,
    qualifies,
)

# --- pre-registered constants (docs/LIMM_PROGRAMME_AGE.md) -----------------------------------
SINCE = "2026-09-08T00:00:00+00:00"
#: Study 5's window opens here; no simulated fill at or after it is read (its own read is 11-13).
FILL_UNTIL = "2026-10-30T16:00:00+00:00"
BUCKETS = ((0.0, 6.0, "0-6h"), (6.0, 24.0, "6-24h"), (24.0, 72.0, "1-3d"), (72.0, math.inf, "3d+"))
EARLY_MAX_H = 24.0
LATE_MIN_H = 72.0
BID_CENTS = 1000                     # $10
MAX_QTY = 1000
RATIO_BAR = 2.0
MIN_PAIRED_PROGRAMMES, MIN_PAIRED_EVENTS = 10, 5
BOOT_N, BOOT_SEED = 2000, 20261108
CALIBRATION = 1.34
LATE_PAYS_BAR_USD = 0.15             # per $10 bid-day, calibrated
EARLY_WINDOW_HOURS = 24.0            # S4's burst window when EARLY-PAYS
ONE_SIDED_SHARE = 0.8                # paid/est split: an event is "early" or "late" at >= 80%
# census (handoff §5 step 0)
CENSUS_DAYS = 28
BAND_LOW, BAND_HIGH = 0.30, 1.00
STEP_ASIDE_HOURS = 48.0
SHORT_HOURS = 72.0
#: S4's watch funnel (docs/LIMM_PAPER_STRATEGIES.md): thin programmes, by total pool per market,
#: from which the collector pins candidates so the band test has book data to work on.
CANDIDATE_POOL_LOW, CANDIDATE_POOL_HIGH = 2.0, 50.0

SNAP_SQL = """
WITH s AS (
  SELECT DISTINCT ON (market_ticker, date_trunc('hour', at))
         market_ticker, date_trunc('hour', at) AS hr, program_row_id, best_yes_bid, best_no_bid,
         yes_depth_at_best, no_depth_at_best, est_yes_score_total, est_no_score_total,
         est_yes_meets_target, est_no_meets_target
    FROM incentive_market_snapshots
   WHERE at >= %(since)s AND book_valid
   ORDER BY market_ticker, date_trunc('hour', at), at DESC
)
SELECT s.market_ticker, s.hr, s.best_yes_bid, s.best_no_bid, s.yes_depth_at_best,
       s.no_depth_at_best, s.est_yes_score_total, s.est_no_score_total, s.est_yes_meets_target,
       s.est_no_meets_target, p.series_ticker, p.event_ticker, p.incentive_type,
       p.period_reward_usd, p.start_date, p.end_date, p.close_time
  FROM s LEFT JOIN incentive_programs p ON p.id = s.program_row_id
"""

TRADE_SQL = """
SELECT market_ticker, ts_ms, received_at, yes_price_cents, count_fp, taker_outcome_side
  FROM incentive_trade_events
 WHERE received_at >= %(since)s AND received_at < %(until)s
"""

PROGRAMS_SQL = """
SELECT DISTINCT market_ticker, split_part(market_ticker, '-', 1) AS series, start_date, end_date,
       close_time, period_reward_usd
  FROM incentive_programs
 WHERE coalesce(incentive_type, '') ILIKE '%%liquid%%' AND start_date >= %(since)s
   AND end_date > start_date
"""


# ---------------------------------------------------------------------------------------------
# pure helpers (unit-tested in tests/test_limm_programme_age.py)


def bucket(age_h: float) -> str | None:
    for lo, hi, name in BUCKETS:
        if lo <= age_h < hi:
            return name
    return None


def qty10(price: int) -> int:
    return min(MAX_QTY, int(BID_CENTS // int(price)))


def per_contract_hour(pool, start, end, price: int, field) -> tuple[float, bool] | None:
    """(reward USD per contract-hour for a $10 bid, thin?) — thin hours earn 0 (study 3)."""
    q = qty10(price)
    hr = hourly_reward(pool, start, end, q, field)
    if hr is None:
        return None
    reward, share = hr
    if share > THIN_SHARE:
        return 0.0, True
    return reward / q, False


def paired_ratio(progs: dict) -> float | None:
    """`progs` = {programme: (event, early_mean, late_mean)}; R = Σ early ÷ Σ late."""
    late = sum(v[2] for v in progs.values())
    if late <= 0:
        return None
    return sum(v[1] for v in progs.values()) / late


def bootstrap_ci(progs: dict, n: int = BOOT_N, seed: int = BOOT_SEED) -> tuple[float, float] | None:
    """Event-clustered percentile bootstrap of `paired_ratio`."""
    by_event: dict[str, list] = defaultdict(list)
    for v in progs.values():
        by_event[v[0]].append(v)
    events = sorted(by_event)
    if len(events) < 2:
        return None
    rng = random.Random(seed)
    stats = []
    for _ in range(n):
        e_sum = l_sum = 0.0
        for _k in range(len(events)):
            for v in by_event[events[rng.randrange(len(events))]]:
                e_sum += v[1]
                l_sum += v[2]
        if l_sum > 0:
            stats.append(e_sum / l_sum)
    if not stats:
        return None
    stats.sort()
    lo = stats[int(0.025 * (len(stats) - 1))]
    hi = stats[int(math.ceil(0.975 * (len(stats) - 1)))]
    return lo, hi


def verdict(progs: dict, ci, early_fill: float | None, late_fill: float | None) -> str:
    events = {v[0] for v in progs.values()}
    if len(progs) < MIN_PAIRED_PROGRAMMES or len(events) < MIN_PAIRED_EVENTS:
        return "INSUFFICIENT"
    r = paired_ratio(progs)
    fills_ok = early_fill is None or late_fill is None or early_fill <= late_fill
    if r is not None and r >= RATIO_BAR and ci is not None and ci[0] > 1.0 and fills_ok:
        return "EARLY-PAYS"
    return "NOT-EARLY"


def s4_rule(v: str, late_bid_day_usd: float | None) -> dict:
    """What the verdict sets for S4 (docs/LIMM_PROGRAMME_AGE.md §5)."""
    after = "flat" if (late_bid_day_usd is not None and late_bid_day_usd >= LATE_PAYS_BAR_USD) else "off"
    return {"s4_burst_window_hours": EARLY_WINDOW_HOURS if v == "EARLY-PAYS" else None,
            "s4_after_burst": after}


def pay_hours(start, end, close) -> float:
    start, end, close = _aware(start), _aware(end), _aware(close)
    if start is None or end is None:
        return 0.0
    stop = end if close is None else min(end, close - dt.timedelta(hours=STEP_ASIDE_HOURS))
    return max(0.0, (stop - start).total_seconds() / 3600.0)


def in_band(proj: float) -> bool:
    return BAND_LOW <= proj < BAND_HIGH


# ---------------------------------------------------------------------------------------------


def build_hours(snaps) -> list[dict]:
    """Qualifying cheap-side programme-hours with their age and reward per contract-hour."""
    out = []
    for (tk, hr, yb, nb, yd, nd, ys, ns, ym, nm, series, event, itype, pool, start, end,
         close) in snaps:
        hr = _aware(hr)
        row = {"hr": hr, "start": start, "end": end, "close": close, "yes_meets": ym,
               "no_meets": nm, "itype": itype}
        if not qualifies(row):
            continue
        cs = cheap_side(yb, nb)
        if cs is None:
            continue
        side, price = cs
        pc = per_contract_hour(pool, start, end, price, ys if side == "yes" else ns)
        if pc is None:
            continue
        start = _aware(start)
        age = (hr - start).total_seconds() / 3600.0
        b = bucket(age)
        if b is None:
            continue
        out.append({"tk": tk, "hr": hr, "prog": (tk, start.isoformat()),
                    "series": (series or tk.split("-", 1)[0]).upper(),
                    "event": event or tk.rsplit("-", 1)[0], "age": age, "bucket": b,
                    "side": side, "price": price, "depth": yd if side == "yes" else nd,
                    "r": pc[0], "thin": pc[1], "pool": pool, "start": start,
                    "end": _aware(end), "close": _aware(close)})
    return out


def summarise(hours: list[dict], trades_by_hour: dict, fill_until: dt.datetime) -> dict:
    by_b: dict[str, dict] = {name: {"hours": 0, "thin": 0, "r_sum": 0.0, "progs": set(),
                                    "fill_hours": 0, "fills": 0} for _lo, _hi, name in BUCKETS}
    per_prog: dict[tuple, dict] = defaultdict(lambda: {"event": None, "early": [], "late": []})
    late_bid_day = []
    for h in hours:
        b = by_b[h["bucket"]]
        b["hours"] += 1
        b["thin"] += int(h["thin"])
        b["r_sum"] += h["r"]
        b["progs"].add(h["prog"])
        p = per_prog[h["prog"]]
        p["event"] = h["event"]
        if h["age"] < EARLY_MAX_H:
            p["early"].append(h["r"])
        elif h["age"] >= LATE_MIN_H:
            p["late"].append(h["r"])
            late_bid_day.append(h["r"] * qty10(h["price"]) * 24 * CALIBRATION)
        if h["hr"] + dt.timedelta(hours=2) <= fill_until:
            nxt = h["hr"] + dt.timedelta(hours=1)
            got = simulate_hour(h["side"], h["price"], qty10(h["price"]), h["depth"],
                                trades_by_hour.get(h["tk"], {}).get(nxt, []))["conservative"]
            b["fill_hours"] += 1
            b["fills"] += int(got > 0)
    paired = {k: (v["event"], sum(v["early"]) / len(v["early"]), sum(v["late"]) / len(v["late"]))
              for k, v in per_prog.items() if v["early"] and v["late"]}

    def fill_rate(names):
        fh = sum(by_b[n]["fill_hours"] for n in names)
        return (sum(by_b[n]["fills"] for n in names) / fh) if fh else None

    early_fill = fill_rate(("0-6h", "6-24h"))
    late_fill = fill_rate(("3d+",))
    ci = bootstrap_ci(paired)
    v = verdict(paired, ci, early_fill, late_fill)
    late_mean = (sum(late_bid_day) / len(late_bid_day)) if late_bid_day else None
    table = []
    for _lo, _hi, name in BUCKETS:
        b = by_b[name]
        table.append({"bucket": name, "hours": b["hours"], "programmes": len(b["progs"]),
                      "thin_hours": b["thin"],
                      "reward_per_1000_contract_hours": (1000 * b["r_sum"] / b["hours"]) if b["hours"] else None,
                      "fill_hours": b["fill_hours"], "fills": b["fills"],
                      "fills_per_100_hours": (100 * b["fills"] / b["fill_hours"]) if b["fill_hours"] else None})
    return {"buckets": table, "paired_programmes": len(paired),
            "paired_events": len({x[0] for x in paired.values()}),
            "ratio_early_over_late": paired_ratio(paired), "ci95": ci,
            "early_fill_rate": early_fill, "late_fill_rate": late_fill,
            "late_calibrated_per_10usd_bid_day": late_mean, "verdict": v,
            "s4": s4_rule(v, late_mean)}


def census(hours: list[dict], programmes, now: dt.datetime) -> dict:
    since = now - dt.timedelta(days=CENSUS_DAYS)
    seen: dict[tuple, dict] = {}
    for h in hours:
        if h["start"] < since:
            continue
        p = seen.setdefault(h["prog"], {"series": h["series"], "start": h["start"],
                                        "len_h": ((h["end"] - h["start"]).total_seconds() / 3600.0),
                                        "pay_h": pay_hours(h["start"], h["end"], h["close"]),
                                        "rs": []})
        p["rs"].append(h["r"] * qty10(h["price"]))
    weeks: dict[str, dict] = defaultdict(lambda: {"observed": 0, "band": 0, "below": 0, "above": 0,
                                                  "band_series": set(), "band_len_h": [],
                                                  "all": 0, "all_short": 0, "all_thin_pool": 0,
                                                  "thin_pool_series": defaultdict(int)})
    for p in seen.values():
        proj = (sum(p["rs"]) / len(p["rs"])) * p["pay_h"] * CALIBRATION
        w = weeks[p["start"].strftime("%G-W%V")]
        w["observed"] += 1
        if in_band(proj):
            w["band"] += 1
            w["band_series"].add(p["series"])
            w["band_len_h"].append(p["len_h"])
        elif proj < BAND_LOW:
            w["below"] += 1
        else:
            w["above"] += 1
    for _tk, series, start, end, _close, pool in programmes:
        start, end = _aware(start), _aware(end)
        if start is None or start < since:
            continue
        w = weeks[start.strftime("%G-W%V")]
        w["all"] += 1
        w["all_short"] += int((end - start).total_seconds() / 3600.0 < SHORT_HOURS)
        if pool is not None and CANDIDATE_POOL_LOW <= float(pool) <= CANDIDATE_POOL_HIGH:
            w["all_thin_pool"] += 1
            w["thin_pool_series"][series] += 1
    rows = []
    for wk in sorted(weeks):
        w = weeks[wk]
        lens = sorted(w["band_len_h"])
        top = sorted(w["thin_pool_series"].items(), key=lambda kv: -kv[1])[:8]
        rows.append({"week": wk, "all_programmes": w["all"], "all_short_lt_72h": w["all_short"],
                     "all_thin_pool_2_50": w["all_thin_pool"],
                     "thin_pool_series": [f"{k}:{v}" for k, v in top],
                     "observed": w["observed"], "band": w["band"], "below": w["below"],
                     "above": w["above"],
                     "band_median_len_h": lens[len(lens) // 2] if lens else None,
                     "band_series": sorted(w["band_series"])})
    return {"weeks": rows}


def _f(x, spec="{:.3f}"):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else spec.format(x)


def render(s: dict) -> str:
    out = ["bucket   hours  progs  thin  $/1000 c-h  fill-hours  fills  fills/100h"]
    for b in s["buckets"]:
        out.append(f"{b['bucket']:7} {b['hours']:6} {b['programmes']:6} {b['thin_hours']:5} "
                   f"{_f(b['reward_per_1000_contract_hours'], '{:11.4f}')} {b['fill_hours']:11} "
                   f"{b['fills']:6} {_f(b['fills_per_100_hours'], '{:11.2f}')}")
    ci = s["ci95"]
    out += ["", f"paired programmes {s['paired_programmes']} in {s['paired_events']} events",
            f"R = early (0-24h) / late (3d+) reward per contract-hour: {_f(s['ratio_early_over_late'])}"
            f"  95% CI {('n/a' if ci is None else f'{ci[0]:.3f} .. {ci[1]:.3f}')}",
            f"fill rate per resting hour (pre-{FILL_UNTIL[:10]}): early {_f(s['early_fill_rate'], '{:.4f}')}"
            f"  late {_f(s['late_fill_rate'], '{:.4f}')}",
            f"late calibrated $ per $10 bid-day: {_f(s['late_calibrated_per_10usd_bid_day'])}",
            f"VERDICT: {s['verdict']}   S4 rule: {s['s4']}"]
    return "\n".join(out)


def render_census(c: dict) -> str:
    out = ["week      all  short<72h  pool$2-50  observed  band  below  above  band-med-len-h  band series"]
    for r in c["weeks"]:
        out.append(f"{r['week']:8} {r['all_programmes']:5} {r['all_short_lt_72h']:10} "
                   f"{r['all_thin_pool_2_50']:10} {r['observed']:9} "
                   f"{r['band']:5} {r['below']:6} {r['above']:6} {_f(r['band_median_len_h'], '{:15.0f}')}  "
                   f"{' '.join(r['band_series'][:12])}")
    out.append("")
    out.append("thin-pool ($2-50 per market) programmes by series, per week (S4's watch funnel):")
    for r in c["weeks"]:
        out.append(f"  {r['week']}: {', '.join(r['thin_pool_series'])}")
    return "\n".join(out)


def paid_vs_est_by_age(cur, now: dt.datetime) -> list[dict]:
    """Descriptive: the live book's confirmed-payout events split by the age of each estimated
    segment (the calibration script's integration, segment by segment)."""
    import limm_estimate_calibration as cal

    scoring = cal.load_scoring()
    orders, programs, snaps = cal.fetch(cur, cal.LIVE_TAG)
    paid: dict[str, float] = defaultdict(float)
    for _d, cents, ev in cal.load_payouts():
        paid[ev] += cents / 100.0
    est: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])      # event -> [early, late]
    for _id, t, created, status, side, price, qty, _coid, ended, last_tick in orders:
        a = cal._utc(created)
        b = cal._utc(ended) or cal._utc(last_tick) or now
        if status == "resting":
            b = now
        if b <= a or price is None or not qty:
            continue
        ss = [x for x in snaps.get(t, []) if a - dt.timedelta(seconds=cal.MAX_GAP_SECONDS) <= x["at"] <= b]
        for i, x in enumerate(ss):
            seg_a = max(a, x["at"])
            nxt = ss[i + 1]["at"] if i + 1 < len(ss) else b
            seg_b = min(b, nxt, x["at"] + dt.timedelta(seconds=cal.MAX_GAP_SECONDS))
            secs = (seg_b - seg_a).total_seconds()
            prog = cal.program_at(programs.get(t, []), seg_a)
            if secs <= 0 or prog is None:
                continue
            usd = cal.rate_for(scoring, x, prog, side, int(price), float(qty)) * secs / 3600.0
            age_h = (seg_a - prog[1]).total_seconds() / 3600.0
            est[cal.event_of(t)][0 if age_h < EARLY_MAX_H else 1] += usd
    rows = []
    for ev, (e, lt) in sorted(est.items()):
        if ev not in paid or e + lt <= 0:
            continue
        share = e / (e + lt)
        kind = "early" if share >= ONE_SIDED_SHARE else ("late" if share <= 1 - ONE_SIDED_SHARE else "mixed")
        rows.append({"event": ev, "est": round(e + lt, 2), "early_share": round(share, 2),
                     "paid": round(paid[ev], 2), "paid_over_est": round(paid[ev] / (e + lt), 2),
                     "kind": kind})
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--census", action="store_true", help="counts of the $1-minimum band only")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    now = dt.datetime.now(dt.timezone.utc)
    since, fill_until = _aware(SINCE), _aware(FILL_UNTIL)

    import psycopg
    from mmsell_flow_veto_probe import RO_OPTIONS, _to_libpq_url

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO not set", file=sys.stderr)
        return 1
    opts = RO_OPTIONS.replace("statement_timeout=300000", "statement_timeout=1500000")
    with psycopg.connect(url, options=opts) as conn, conn.cursor() as cur:
        snap_since = (now - dt.timedelta(days=CENSUS_DAYS + 14)) if args.census else since
        cur.execute(SNAP_SQL, {"since": snap_since})
        snaps = cur.fetchall()
        hours = build_hours(snaps)
        if args.census:
            cur.execute(PROGRAMS_SQL, {"since": now - dt.timedelta(days=CENSUS_DAYS)})
            c = census(hours, cur.fetchall(), now)
            print(f"LIMM $1-minimum band census (counts only; docs/LIMM_PROGRAMME_AGE.md §6) — "
                  f"as of {now.isoformat()[:16]}, last {CENSUS_DAYS} days, band "
                  f"${BAND_LOW:.2f}-{BAND_HIGH:.2f} projected calibrated reward of a $10 bid\n")
            print(render_census(c))
            if args.json:
                print(json.dumps(c, default=str))
            return 0
        cur.execute(TRADE_SQL, {"since": since, "until": fill_until + dt.timedelta(hours=1)})
        trades = cur.fetchall()
        try:
            pve = paid_vs_est_by_age(cur, now)
        except Exception as exc:  # noqa: BLE001 — descriptive only; never blocks the verdict
            pve = [{"error": f"{type(exc).__name__}: {exc}"}]
    tbh: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for tk, ts_ms, rcv, yp, cnt, taker in trades:
        at = trade_time(ts_ms, rcv)
        if at is not None:
            tbh[tk][at.replace(minute=0, second=0, microsecond=0)].append((at, yp, cnt, taker))
    s = summarise(hours, tbh, fill_until)
    print("LIMM programme age — reward per contract-hour by programme age "
          "(pre-registered docs/LIMM_PROGRAMME_AGE.md)\n")
    print(f"window {SINCE[:16]} -> {now.isoformat()[:16]}; hourly snapshots {len(snaps)}; "
          f"qualifying cheap-side hours {len(hours)}; prints (fill window) {len(trades)}\n")
    print(render(s))
    print("\npaid / estimated by age (descriptive, the live book's paid events):")
    for r in pve:
        print("  " + json.dumps(r))
    if args.json:
        print(json.dumps({**s, "paid_vs_est": pve}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
