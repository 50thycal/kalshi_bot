"""LEVELHOLD probe: does the hourly BTC ladder misprice strikes that sit on a technical level?

Pre-registration: docs/LEVELHOLD_PREREG.md (P0-P3, KILL). Constants below are copied from it;
tests pin them. Source idea: tube_bot video z97PnFIPoFg (real levels / flip zones hold more often
than random lines on NQ/ES) - the reason to ask, not evidence for the answer.

Read-only. DATABASE_URL_RO (crypto_ladder_snapshots + crypto_spot_candles, one day per query) +
Kalshi PUBLIC REST for each market's official settlement result (live tier, then historical tier).
No orders, no writes. Reuses MOMO-H's tested helpers (exact tickers, fee, bootstrap, labels).

Usage (ops channel):
    {"type":"script","name":"levelhold_probe","args":["--start","2026-08-06","--end","2026-11-08"]}
"""

from __future__ import annotations

import argparse
import bisect
import datetime as dt
import json
import math
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kalshi_settled as ks  # noqa: E402
import momo_hourly_probe as mh  # noqa: E402

# --- pre-registered constants (docs/LEVELHOLD_PREREG.md) ----------------------------------
SERIES = "KXBTCD"
SPOT_PRODUCT = "BTC-USD"
MTC_LO, MTC_HI, MTC_TARGET = 20.0, 40.0, 30.0
MID_LO, MID_HI = 15.0, 85.0
BAR_SECONDS = 4 * 3600
BAR_MIN_CANDLES = 120              # a 4h bar counts only if it holds >= half of its 240 minutes
LOOKBACK_BARS = 30                 # five days of completed 4h bars
ATR_N = 14
PIVOT_N = 2                        # fractal: 2 bars each side
LEVEL_BAND_ATR = 0.25
ROUND_GRID = 1000
P0_MIN_DAYS, P0_MIN_SWING, P0_MAX_EXCLUSION, P0_MIN_LOOKBACK_SHARE = 40, 600, 0.10, 0.95
P1_SIGNAL_CENTS, KILL_CENTS = 3.0, 1.5
P1_CI_PRIMARY, P1_CI_SECONDARY = 0.90, 0.95
P2_NET_EV_CENTS = 1.5
FEE_QTY = 10
BOOT_N, BOOT_SEED = 2000, 20261109
ARMS = ("swing", "flip", "round")
PRIMARY = "swing"

DAY_SQL = """
with s as materialized (
  select event_ticker ev, market_ticker mk, captured_at t, minutes_to_close mtc, mid_cents mid,
         yes_ask_cents ask, yes_bid_cents bid, spot, floor_strike k
  from crypto_ladder_snapshots
  where event_ticker = any(%(evs)s) and strike_type = 'greater')
select distinct on (ev, mk) ev, mk, t, mid, ask, bid, spot, k from s
where mtc between %(mtc_lo)s and %(mtc_hi)s and mid between %(mid_lo)s and %(mid_hi)s
order by ev, mk, abs(mtc - %(mtc_t)s), t
"""

CANDLE_SQL = """
select minute_ts, close from crypto_spot_candles
where product = %(product)s and minute_ts >= %(lo)s and minute_ts < %(hi)s
order by minute_ts
"""


# --- pure functions (tested) ------------------------------------------------------------------


def _epoch(t: dt.datetime) -> float:
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.timestamp()


def build_bars(candles: list[tuple[float, float]]) -> list[dict]:
    """1-minute (epoch, close) -> 4h bars aligned to UTC 00/04/../20, ascending. high/low are the
    max/min CLOSE (the table carries closes only); a bar with < BAR_MIN_CANDLES minutes is kept but
    flagged invalid so a lookback that contains it is not 'full'."""
    by_bar: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for ts, close in candles:
        by_bar[int(ts // BAR_SECONDS) * BAR_SECONDS].append((ts, close))
    bars = []
    for start in sorted(by_bar):
        pts = sorted(by_bar[start])
        closes = [c for _, c in pts]
        bars.append({"start": start, "end": start + BAR_SECONDS, "high": max(closes),
                     "low": min(closes), "close": closes[-1], "n": len(closes),
                     "valid": len(closes) >= BAR_MIN_CANDLES})
    return bars


def completed_lookback(bars: list[dict], bar_ends: list[int], t_epoch: float) -> list[dict] | None:
    """The last LOOKBACK_BARS bars that ENDED at or before t, or None unless they are all valid
    and contiguous (no missing 4h slot). No lookahead: a bar still open at t is never used."""
    i = bisect.bisect_right(bar_ends, t_epoch)
    if i < LOOKBACK_BARS:
        return None
    win = bars[i - LOOKBACK_BARS:i]
    if not all(b["valid"] for b in win):
        return None
    if any(win[j + 1]["start"] - win[j]["start"] != BAR_SECONDS for j in range(len(win) - 1)):
        return None
    return win


def wilder_atr(bars: list[dict], n: int = ATR_N) -> float:
    trs = []
    for j in range(1, len(bars)):
        pc = bars[j - 1]["close"]
        trs.append(max(bars[j]["high"] - bars[j]["low"], abs(bars[j]["high"] - pc), abs(bars[j]["low"] - pc)))
    if len(trs) < n:
        return float("nan")
    atr = sum(trs[:n]) / n
    for tr in trs[n:]:
        atr = (atr * (n - 1) + tr) / n
    return atr


def swing_levels(bars: list[dict], n: int = PIVOT_N) -> list[dict]:
    """Fractal pivots with n confirming bars on each side (all completed, by construction).
    flipped = a later completed 4h close crossed the level (a 'flip zone' candidate)."""
    out = []
    for i in range(n, len(bars) - n):
        h, lo = bars[i]["high"], bars[i]["low"]
        side = [bars[j] for j in range(i - n, i + n + 1) if j != i]
        later = bars[i + 1:]
        if all(h > b["high"] for b in side):
            out.append({"level": h, "kind": "high", "flipped": any(b["close"] > h for b in later)})
        if all(lo < b["low"] for b in side):
            out.append({"level": lo, "kind": "low", "flipped": any(b["close"] < lo for b in later)})
    return out


def arm_flags(strike: float, levels: list[dict], atr: float) -> dict[str, bool]:
    band = LEVEL_BAND_ATR * atr
    near = [lv for lv in levels if abs(strike - lv["level"]) <= band]
    return {"swing": bool(near), "flip": any(lv["flipped"] for lv in near),
            "round": round(strike) % ROUND_GRID == 0}


def held_view(row: dict) -> dict | None:
    """Re-express the decision on the side of K where spot sits at t. None when spot == K."""
    if row["spot"] > row["k"]:
        return {"held_yes": True, "mid_held": row["mid"]}
    if row["spot"] < row["k"]:
        return {"held_yes": False, "mid_held": 100.0 - row["mid"]}
    return None


def resid(r: dict) -> float:
    return 100.0 * r["y_held"] - r["mid_held"]


def decile_edges(values: list[float]) -> list[float]:
    v = sorted(values)
    n = len(v)
    return [v[min(n - 1, int(n * k / 10))] for k in range(1, 10)]


def decile(x: float, edges: list[float]) -> int:
    return bisect.bisect_right(edges, x)


def stratified_l(rows: list[dict], arm: str) -> float:
    """L = sum_d w_d (mean resid arm in d - mean resid control in d); w_d = arm's share in d.
    Deciles with arm rows but no control rows are dropped (their weight renormalised away)."""
    arm_by: dict[int, list[float]] = defaultdict(list)
    ctl_by: dict[int, list[float]] = defaultdict(list)
    for r in rows:
        if r[arm]:
            arm_by[r["dec"]].append(resid(r))
        elif r["control"]:
            ctl_by[r["dec"]].append(resid(r))
    num, den = 0.0, 0
    for d, xs in arm_by.items():
        if ctl_by.get(d):
            num += len(xs) * (mh._mean(xs) - mh._mean(ctl_by[d]))
            den += len(xs)
    return num / den if den else float("nan")


def held_leg_ev(r: dict, buy_held: bool) -> float:
    """Net EV (cents/contract) of a taker buy held to settlement, on the held or the break side."""
    buy_yes = r["held_yes"] if buy_held else not r["held_yes"]
    px = r["ask"] if buy_yes else 100.0 - r["bid"]
    win = r["y"] if buy_yes else 1 - r["y"]
    return 100.0 * win - px - mh.fee_cents(px, FEE_QTY)


def arm_trade_ev(rows: list[dict], arm: str, buy_held: bool) -> float:
    evs = [held_leg_ev(r, buy_held) for r in rows if r[arm]]
    return mh._mean(evs) if evs else float("nan")


def arm_verdict(arm: str, n_arm: int, l_val: float, ci: tuple[float, float], ev: float, ev_p5: float) -> str:
    excludes = ci[0] > 0 or ci[1] < 0
    if math.isnan(l_val) or n_arm == 0:
        return "NO DATA"
    if abs(l_val) < KILL_CENTS or not excludes:
        return "NO SIGNAL"
    if abs(l_val) < P1_SIGNAL_CENTS:
        return "WEAK (HOLD)" if arm == PRIMARY else "NO SIGNAL (secondary needs |L| >= 3c)"
    if ev >= P2_NET_EV_CENTS and ev_p5 > 0:
        return "P1+P2"
    return "P1 SIGNAL / P2 FAIL"


def overall_verdict(p0_ok: bool, p0_reason: str, arm_calls: dict[str, str]) -> str:
    if not p0_ok:
        return f"HOLD (instrument: {p0_reason})"
    passing = [a for a, c in arm_calls.items() if c == "P1+P2"]
    if passing:
        return ("PASS (P1+P2 on " + ",".join(passing) + ") - authorizes a FORWARD CONFIRMATION only (P3), "
                "not a paper book")
    if arm_calls.get(PRIMARY) == "WEAK (HOLD)":
        return "HOLD (weak primary signal: re-read on the forward window at >= 600 new SWING decisions)"
    signals = [a for a, c in arm_calls.items() if c.startswith("P1 SIGNAL")]
    if signals:
        return "P1 SIGNAL / P2 FAIL on " + ",".join(signals) + " (real but not tradeable after cost)"
    return "KILL (technical levels are priced; no ATR-band/pivot/grid variants)"


def ci_from(boot: list[float], level: float) -> tuple[float, float]:
    a = (1.0 - level) / 2.0
    return (mh.pct(boot, a), mh.pct(boot, 1.0 - a))


def analyse(rows: list[dict], n_raw: int, n_no_label: int, n_no_lookback: int) -> dict:
    """rows: usable decisions with held_yes, mid_held, y, y_held, ask, bid, day, swing, flip, round."""
    edges = decile_edges([r["mid_held"] for r in rows])
    for r in rows:
        r["dec"] = decile(r["mid_held"], edges)
        r["control"] = not r["swing"] and not r["round"]
    days = {r["day"] for r in rows}
    n_lb_base = len(rows) + n_no_lookback
    exclusion = n_no_label / n_raw if n_raw else 1.0
    lookback_share = len(rows) / n_lb_base if n_lb_base else 0.0
    counts = {a: sum(1 for r in rows if r[a]) for a in ARMS}
    counts["control"] = sum(1 for r in rows if r["control"])
    p0_reason = ""
    if len(days) < P0_MIN_DAYS:
        p0_reason = f"{len(days)} days < {P0_MIN_DAYS}"
    elif counts[PRIMARY] < P0_MIN_SWING:
        p0_reason = f"{counts[PRIMARY]} SWING decisions < {P0_MIN_SWING}"
    elif exclusion > P0_MAX_EXCLUSION:
        p0_reason = f"label exclusion {exclusion:.3f} > {P0_MAX_EXCLUSION}"
    elif lookback_share < P0_MIN_LOOKBACK_SHARE:
        p0_reason = f"full-lookback share {lookback_share:.3f} < {P0_MIN_LOOKBACK_SHARE}"
    arms_out, calls = {}, {}
    for arm in ARMS:
        l_val = stratified_l(rows, arm)
        boot = mh.day_bootstrap(rows, lambda rs, a=arm: stratified_l(rs, a), n=BOOT_N, seed=BOOT_SEED)
        ci = ci_from(boot, P1_CI_PRIMARY if arm == PRIMARY else P1_CI_SECONDARY)
        buy_held = not (l_val < 0)
        ev = arm_trade_ev(rows, arm, buy_held)
        ev_boot = mh.day_bootstrap(rows, lambda rs, a=arm, b=buy_held: arm_trade_ev(rs, a, b),
                                   n=BOOT_N, seed=BOOT_SEED)
        ev_p5 = mh.pct(ev_boot, 0.05)
        arm_rows = [r for r in rows if r[arm]]
        calls[arm] = arm_verdict(arm, counts[arm], l_val, ci, ev, ev_p5)
        arms_out[arm] = {
            "n": counts[arm], "L": round(l_val, 3), "ci": [round(ci[0], 3), round(ci[1], 3)],
            "ci_level": P1_CI_PRIMARY if arm == PRIMARY else P1_CI_SECONDARY,
            "raw_resid": round(mh._mean([resid(r) for r in arm_rows]), 3) if arm_rows else None,
            "trade": "buy held side" if buy_held else "buy break side",
            "trade_ev": round(ev, 3), "trade_ev_p5": round(ev_p5, 3), "call": calls[arm],
        }
    ctl = [r for r in rows if r["control"]]
    per_dec = []
    for d in range(10):
        ds = [r for r in rows if r["dec"] == d]
        per_dec.append({"dec": d + 1, "n": len(ds),
                        "mid_held": round(mh._mean([r["mid_held"] for r in ds]), 1) if ds else None,
                        "swing_resid": _r(mh._mean([resid(r) for r in ds if r["swing"]])),
                        "round_resid": _r(mh._mean([resid(r) for r in ds if r["round"]])),
                        "ctl_resid": _r(mh._mean([resid(r) for r in ds if r["control"]]))})
    return {
        "n_days": len(days), "n_raw": n_raw, "n_usable": len(rows), "counts": counts,
        "label_exclusion": round(exclusion, 4), "full_lookback_share": round(lookback_share, 4),
        "control_raw_resid": round(mh._mean([resid(r) for r in ctl]), 3) if ctl else None,
        "arms": arms_out, "per_decile": per_dec,
        "verdict": overall_verdict(not p0_reason, p0_reason, calls),
    }


def _r(x: float):
    return None if (x is None or math.isnan(x)) else round(x, 2)


def featurize(raw: list[dict], bars: list[dict]) -> tuple[list[dict], int]:
    """Attach held-side and arm flags. Returns (featurized rows, rows dropped for no full lookback)."""
    ends = [b["end"] for b in bars]
    out, no_lb = [], 0
    for r in raw:
        hv = held_view(r)
        if hv is None:
            continue
        win = completed_lookback(bars, ends, _epoch(r["t"]))
        if win is None:
            no_lb += 1
            continue
        atr = wilder_atr(win)
        if math.isnan(atr) or atr <= 0:
            no_lb += 1
            continue
        r.update(hv)
        r.update(arm_flags(r["k"], swing_levels(win), atr))
        r["atr"] = atr
        out.append(r)
    return out, no_lb


# --- IO -------------------------------------------------------------------------------------


def load_day(cur, day: dt.date) -> list[dict]:
    cur.execute(DAY_SQL, {"evs": mh.day_event_tickers(SERIES, day), "mtc_lo": MTC_LO, "mtc_hi": MTC_HI,
                          "mid_lo": MID_LO, "mid_hi": MID_HI, "mtc_t": MTC_TARGET})
    rows = []
    for ev, mk, t, mid, ask, bid, spot, k in cur.fetchall():
        if spot is None or k is None or ask is None or bid is None:
            continue
        rows.append({"ev": ev, "mk": mk, "t": t, "day": day.isoformat(), "mid": float(mid),
                     "ask": float(ask), "bid": float(bid), "spot": float(spot), "k": float(round(k))})
    return rows


def load_candles(cur, lo: dt.datetime, hi: dt.datetime) -> list[tuple[float, float]]:
    out = []
    t = lo
    while t < hi:
        nxt = min(t + dt.timedelta(days=1), hi)
        cur.execute(CANDLE_SQL, {"product": SPOT_PRODUCT, "lo": t, "hi": nxt})
        out.extend((_epoch(ts), float(c)) for ts, c in cur.fetchall())
        t = nxt
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2026-08-06")
    ap.add_argument("--end", default="", help="inclusive ET date; default yesterday (UTC)")
    ap.add_argument("--max-days", type=int, default=120)
    args = ap.parse_args(argv)
    start = dt.date.fromisoformat(args.start)
    end = (dt.date.fromisoformat(args.end) if args.end
           else dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1))
    import psycopg

    raw: list[dict] = []
    opts = "-c default_transaction_read_only=on -c statement_timeout=120000"
    with psycopg.connect(mh._db_url(), options=opts, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            day, n = start, 0
            while day <= end and n < args.max_days:
                got = load_day(cur, day)
                raw.extend(got)
                print(f"day {day}: {len(got)} decision rows", file=sys.stderr)
                day += dt.timedelta(days=1)
                n += 1
            lo = dt.datetime.combine(start, dt.time(), dt.timezone.utc) - dt.timedelta(days=7)
            hi = dt.datetime.combine(end, dt.time(), dt.timezone.utc) + dt.timedelta(days=2)
            candles = load_candles(cur, lo, hi)
    bars = build_bars(candles)
    print(f"candles {len(candles)}, 4h bars {len(bars)} ({sum(b['valid'] for b in bars)} valid)", file=sys.stderr)

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
    labelled = [r for r in raw if r["mk"] in results]
    n_no_label = len(raw) - len(labelled)
    for r in labelled:
        r["y"] = results[r["mk"]]
    rows, no_lb = featurize(labelled, bars)
    for r in rows:
        r["y_held"] = r["y"] if r["held_yes"] else 1 - r["y"]

    print(f"# LEVELHOLD probe - series {SERIES}, {start}..{end} (docs/LEVELHOLD_PREREG.md)")
    print(f"raw decisions {len(raw)}, no official label {n_no_label}, no full lookback {no_lb}, "
          f"usable {len(rows)}")
    ks.report(tally)
    print(f"events {len(by_ev)}, result fetch errors {fetch_errors}")
    if len(rows) < 50:
        print("VERDICT: HOLD (instrument: P0 data floor) - too few usable rows to analyse")
        return 0
    out = analyse(rows, len(raw), n_no_label, no_lb)
    print(json.dumps({k: v for k, v in out.items() if k != "per_decile"}, indent=1))
    print("decile  n      mid_held  swing_resid  round_resid  ctl_resid")
    for d in out["per_decile"]:
        print(f"D{d['dec']:<6} {d['n']:<6} {d['mid_held']!s:>8} {d['swing_resid']!s:>12} "
              f"{d['round_resid']!s:>12} {d['ctl_resid']!s:>10}")
    print(f"VERDICT: {out['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
