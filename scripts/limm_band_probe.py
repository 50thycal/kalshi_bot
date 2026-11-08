"""LIMM BAND PROBE — do incentive bids above 10c pay, one-sided or two-sided? (study 9)

Pre-registered in docs/LIMM_BAND_PROBE.md (2026-11-08). Every constant marked "pre-registered" is
frozen there; changing one is a new probe.

Study 3's hourly construction (qualifying hours, reward estimator, thin rule) and study 5's fill
replay (`ShadowLeg` against the next clock hour's prints) are imported unchanged. New here: only
hours whose CHEAPER side bids 11-50c (so no side is in study 5/6/8's 1-10c cheap band), a $10 leg
at each side's best bid, three policies (CHEAP, DEAR, BOTH) and a 24 h markout.

Read-only: DATABASE_URL_RO (read-only transaction). Stdlib + psycopg.

Usage: {"type":"script","name":"limm_band_probe","args":[],"id":"rl-s9-run1-<date>"}
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from limm_fill_hazard import (  # noqa: E402  (study 5 replay, unchanged)
    MODELS,
    ratio_cluster,
    simulate_hour,
    trade_time,
)
from limm_programme_outcomes import (  # noqa: E402  (study 3 construction, unchanged)
    THIN_SHARE,
    _aware,
    hourly_reward,
    qualifies,
)

from kalshi_bot.liquidity_incentive.fills import MODEL_CONSERVATIVE  # noqa: E402

# --- pre-registered constants ----------------------------------------------------------------
SINCE = "2026-11-09T00:00:00+00:00"            # Part B window
UNTIL = "2026-12-03T15:00:00+00:00"
BAND_LO, BAND_HI = 11, 50                      # the cheaper side's best bid
LEG_USD_CENTS = 1000                           # $10 per leg
MARK_HOURS = 24                                # deciding markout horizon
MARK_HOURS_DESC = 6                            # descriptive
MARK_SEARCH = (-1, 1, -2, 2)                   # nearest hour within +-2 h
UNMARKED_FRACTION = 0.5                        # no mark: charged half the price (study 5)
MIN_LEG_DAYS, MIN_EVENTS = 100, 10
BAR_USD = 0.15                                 # per $10 leg-day
Z_DECIDE = 2.39                                # Bonferroni, three policies
DECIDING = MODEL_CONSERVATIVE
POLICIES = ("CHEAP", "DEAR", "BOTH")
BANDS = ((11, 25), (26, 40), (41, 50))

SNAP_SQL = """
WITH t AS (
  SELECT DISTINCT market_ticker FROM incentive_market_snapshots
   WHERE at >= %(since)s AND at < %(until)s AND book_valid
     AND LEAST(best_yes_bid, best_no_bid) BETWEEN %(lo)s AND %(hi)s
), s AS (
  SELECT DISTINCT ON (m.market_ticker, date_trunc('hour', m.at))
         m.market_ticker, date_trunc('hour', m.at) AS hr, m.program_row_id, m.best_yes_bid,
         m.best_no_bid, m.yes_depth_at_best, m.no_depth_at_best, m.est_yes_score_total,
         m.est_no_score_total, m.est_yes_meets_target, m.est_no_meets_target
    FROM incentive_market_snapshots m JOIN t USING (market_ticker)
   WHERE m.at >= %(since)s AND m.at < %(mark_until)s AND m.book_valid
   ORDER BY m.market_ticker, date_trunc('hour', m.at), m.at DESC
)
SELECT s.market_ticker, s.hr, s.best_yes_bid, s.best_no_bid, s.yes_depth_at_best,
       s.no_depth_at_best, s.est_yes_score_total, s.est_no_score_total, s.est_yes_meets_target,
       s.est_no_meets_target, p.series_ticker, p.event_ticker, p.incentive_type,
       p.period_reward_usd, p.start_date, p.end_date, p.close_time
  FROM s LEFT JOIN incentive_programs p ON p.id = s.program_row_id
"""

TRADE_SQL = """
SELECT e.market_ticker, e.ts_ms, e.received_at, e.yes_price_cents, e.count_fp,
       e.taker_outcome_side
  FROM incentive_trade_events e
 WHERE e.received_at >= %(since)s AND e.received_at < %(trade_until)s
   AND e.market_ticker = ANY(%(tickers)s)
"""


# ---------------------------------------------------------------------------------------------
# pure helpers (unit-tested in tests/test_limm_band_probe.py)


def in_band(best_yes_bid, best_no_bid) -> bool:
    """Both sides bid, and the cheaper side's best bid is 11-50c."""
    if best_yes_bid is None or best_no_bid is None:
        return False
    return BAND_LO <= min(int(best_yes_bid), int(best_no_bid)) <= BAND_HI


def band_of(best_yes_bid, best_no_bid) -> str | None:
    if not in_band(best_yes_bid, best_no_bid):
        return None
    lo = min(int(best_yes_bid), int(best_no_bid))
    for a, b in BANDS:
        if a <= lo <= b:
            return f"{a}-{b}"
    return None


def legs_for(policy: str, best_yes_bid: int, best_no_bid: int) -> list[tuple[str, int]]:
    """[(side, price)] the policy quotes. Ties: yes is the cheaper side."""
    cheap = "yes" if best_yes_bid <= best_no_bid else "no"
    dear = "no" if cheap == "yes" else "yes"
    price = {"yes": int(best_yes_bid), "no": int(best_no_bid)}
    if policy == "CHEAP":
        return [(cheap, price[cheap])]
    if policy == "DEAR":
        return [(dear, price[dear])]
    if policy == "BOTH":
        return [(cheap, price[cheap]), (dear, price[dear])]
    raise ValueError(policy)


def leg_qty(price: int) -> int:
    return int(LEG_USD_CENTS // int(price))


def mark_bid(marks: dict[dt.datetime, tuple], side: str, hr: dt.datetime,
             hours: int) -> int | None:
    """The side's best bid `hours` after the resting hour (h+1), nearest within the search."""
    base = hr + dt.timedelta(hours=1 + hours)
    for off in (0,) + MARK_SEARCH:
        snap = marks.get(base + dt.timedelta(hours=off))
        if snap is not None:
            b = snap[0] if side == "yes" else snap[1]
            return 0 if b is None else int(b)
    return None


def markout_usd(filled: float, price: int, bid_later: int | None) -> float:
    """Fill cost in USD (positive = loss). Unmarked fills are charged half their price."""
    if filled <= 0:
        return 0.0
    per = price * UNMARKED_FRACTION if bid_later is None else price - bid_later
    return filled * per / 100.0


def run_leg(hours: list[dict], trades_by_hour: dict, marks: dict, model: str) -> dict:
    """Walk one leg (one market, one side) through its qualifying hours in time order.
    Each hour dict: hr, price, qty, depth, reward (USD or None)."""
    out = {"reward": 0.0, "loss": 0.0, "loss6": 0.0, "fills": 0, "unmarked": 0,
           "filled_at": None, "hours": 0}
    side = None
    for h in sorted(hours, key=lambda x: x["hr"]):
        side = h["side"]
        out["hours"] += 1
        if h["reward"] is not None:
            out["reward"] += h["reward"]
        nxt = h["hr"] + dt.timedelta(hours=1)
        got = simulate_hour(side, h["price"], h["qty"], h["depth"],
                            trades_by_hour.get(nxt, []))[model]
        if got > 0:
            bid = mark_bid(marks, side, h["hr"], MARK_HOURS)
            bid6 = mark_bid(marks, side, h["hr"], MARK_HOURS_DESC)
            out["loss"] += markout_usd(got, h["price"], bid)
            out["loss6"] += markout_usd(got, h["price"], bid6)
            out["fills"] = 1
            out["unmarked"] = int(bid is None)
            out["filled_at"] = nxt
            break
    return out


def verdict(leg_days: int, events: int, reward_ld: float | None, est: dict | None) -> str:
    if leg_days < MIN_LEG_DAYS or events < MIN_EVENTS or est is None:
        return "HOLD (accrual)"
    r, se = est["R"], est["se"]
    if math.isnan(se):
        return "HOLD"
    if r >= BAR_USD and r - Z_DECIDE * se > 0:
        return "PROMISING"
    if reward_ld is not None and reward_ld >= BAR_USD and r <= 0:
        return "FILL-TRAP"
    if r + Z_DECIDE * se < BAR_USD:
        return "NO-LEAD"
    return "HOLD"


# ---------------------------------------------------------------------------------------------


def _f(x, spec="{:+.2f}"):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else spec.format(x)


def aggregate(legs: list[dict]) -> dict:
    """legs: [{event, days:set, reward, loss, loss6, fills, unmarked}] -> summary."""
    ev: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    ev6: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    days = 0
    reward = loss = loss6 = 0.0
    fills = unmarked = 0
    for lg in legs:
        d = len(lg["days"])
        days += d
        reward += lg["reward"]
        loss += lg["loss"]
        loss6 += lg["loss6"]
        fills += lg["fills"]
        unmarked += lg["unmarked"]
        ev[lg["event"]][0] += lg["reward"] - lg["loss"]
        ev[lg["event"]][1] += d
        ev6[lg["event"]][0] += lg["reward"] - lg["loss6"]
        ev6[lg["event"]][1] += d
    est = ratio_cluster({e: (v[0], v[1]) for e, v in ev.items()})
    est6 = ratio_cluster({e: (v[0], v[1]) for e, v in ev6.items()})
    return {"leg_days": days, "events": len(ev), "reward": reward, "loss": loss,
            "fills": fills, "unmarked": unmarked, "est": est, "est6": est6,
            "reward_ld": reward / days if days else None,
            "loss_ld": loss / days if days else None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default=SINCE)
    ap.add_argument("--until", default=UNTIL)
    args = ap.parse_args(argv)
    since, until = _aware(args.since), _aware(args.until)
    mark_until = until + dt.timedelta(hours=2 + MARK_HOURS + 2)
    trade_until = until + dt.timedelta(hours=2)

    import psycopg
    from mmsell_flow_veto_probe import RO_OPTIONS, _to_libpq_url

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO not set", file=sys.stderr)
        return 1
    print("LIMM band probe — $10 incentive bids where both sides bid 11-50c "
          "(pre-registered docs/LIMM_BAND_PROBE.md)\n")
    opts = RO_OPTIONS.replace("statement_timeout=300000", "statement_timeout=1500000")
    with psycopg.connect(url, options=opts) as conn, conn.cursor() as cur:
        cur.execute(SNAP_SQL, {"since": since, "until": until, "mark_until": mark_until,
                               "lo": BAND_LO, "hi": BAND_HI})
        snaps = cur.fetchall()
        tickers = sorted({r[0] for r in snaps})
        cur.execute(TRADE_SQL, {"since": since, "trade_until": trade_until, "tickers": tickers})
        trades = cur.fetchall()
    print(f"window {since.isoformat()[:16]} -> {until.isoformat()[:16]}; hourly snapshots "
          f"(band markets, incl. mark tail) {len(snaps)}; band markets {len(tickers)}; "
          f"prints {len(trades)}")

    marks: dict[str, dict[dt.datetime, tuple]] = defaultdict(dict)
    # (ticker, policy, side) -> hours
    leg_hours: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    meta: dict[str, tuple[str, str]] = {}
    band_hours: dict[str, int] = defaultdict(int)
    week_hours: dict[str, int] = defaultdict(int)
    n_q = thin = 0
    for (tk, hr, yb, nb, yd, nd, ys, ns, ym, nm, series, event, itype, pool, start, end,
         close) in snaps:
        hr = _aware(hr)
        marks[tk][hr] = (yb, nb)
        if not (since <= hr < until) or not in_band(yb, nb):
            continue
        row = {"hr": hr, "start": start, "end": end, "close": close, "yes_meets": ym,
               "no_meets": nm, "itype": itype}
        if not qualifies(row):
            continue
        n_q += 1
        band_hours[band_of(yb, nb)] += 1
        week_hours[(hr - dt.timedelta(days=hr.weekday())).date().isoformat()] += 1
        meta[tk] = ((series or tk.split("-", 1)[0]).upper(), event or tk.rsplit("-", 1)[0])
        for pol in POLICIES:
            for side, price in legs_for(pol, int(yb), int(nb)):
                qty = leg_qty(price)
                hrw = hourly_reward(pool, start, end, qty, ys if side == "yes" else ns)
                reward = None
                if hrw is not None:
                    if hrw[1] > THIN_SHARE:
                        thin += 1
                    else:
                        reward = hrw[0]
                leg_hours[(tk, pol, side)].append({
                    "hr": hr, "side": side, "price": price, "qty": qty,
                    "depth": yd if side == "yes" else nd, "reward": reward,
                    "band": band_of(yb, nb)})
    tbh: dict[str, dict[dt.datetime, list]] = defaultdict(lambda: defaultdict(list))
    for tk, ts_ms, rcv, yp, cnt, taker in trades:
        if tk not in meta:
            continue
        at = trade_time(ts_ms, rcv)
        if at is None:
            continue
        tbh[tk][at.replace(minute=0, second=0, microsecond=0)].append((at, yp, cnt, taker))
    print(f"qualifying band hours {n_q}; markets {len(meta)}; series "
          f"{len({m[0] for m in meta.values()})}; events {len({m[1] for m in meta.values()})}; "
          f"thin leg-hours (no reward) {thin}")
    print("  by band: " + ", ".join(f"{b} {band_hours.get(f'{b[0]}-{b[1]}', 0)}h"
                                    for b in BANDS))
    print("  by week: " + ", ".join(f"{w} {n}h" for w, n in sorted(week_hours.items())))

    for model in MODELS:
        tag = "DECIDES" if model == DECIDING else "sensitivity"
        print(f"\n== model `{model}` ({tag}); 24 h markout decides, 6 h descriptive")
        print(f"  {'policy':6s} {'leg-d':>6} {'evts':>4} {'rew/ld':>7} {'loss/ld':>7} "
              f"{'R/ld':>7} {'±se':>5} {'R6/ld':>7} {'fills':>5} {'/100ld':>6} {'unmk':>4}  verdict")
        per_policy: dict[str, list[dict]] = defaultdict(list)
        by_band: dict[tuple[str, str], list[dict]] = defaultdict(list)
        by_series: dict[tuple[str, str], list[dict]] = defaultdict(list)
        pair_fills = 0
        for (tk, pol, side), hs in leg_hours.items():
            series, event = meta[tk]
            r = run_leg(hs, tbh.get(tk, {}), marks.get(tk, {}), model)
            lg = {"event": event, "days": {(tk, side, h["hr"].date()) for h in hs},
                  "reward": r["reward"], "loss": r["loss"], "loss6": r["loss6"],
                  "fills": r["fills"], "unmarked": r["unmarked"], "tk": tk,
                  "filled": r["filled_at"] is not None}
            per_policy[pol].append(lg)
            by_band[(pol, hs[0]["band"])].append(lg)
            by_series[(pol, series)].append(lg)
        both = defaultdict(int)
        for lg in per_policy["BOTH"]:
            both[lg["tk"]] += int(lg["filled"])
        pair_fills = sum(1 for v in both.values() if v == 2)
        verdicts = {}
        for pol in POLICIES:
            a = aggregate(per_policy[pol])
            est, est6 = a["est"] or {}, a["est6"] or {}
            v = verdict(a["leg_days"], a["events"], a["reward_ld"], a["est"])
            verdicts[pol] = v
            print(f"  {pol:6s} {a['leg_days']:6d} {a['events']:4d} {_f(a['reward_ld']):>7} "
                  f"{_f(a['loss_ld']):>7} {_f(est.get('R')):>7} "
                  f"{_f(est.get('se'), '{:.2f}'):>5} {_f(est6.get('R')):>7} {a['fills']:5d} "
                  f"{_f(100 * a['fills'] / a['leg_days'] if a['leg_days'] else None, '{:.1f}'):>6} "
                  f"{a['unmarked']:4d}  {v}")
        print(f"  BOTH markets where both legs filled: {pair_fills} of {len(both)}")
        print("  by band (cheaper side's bid; descriptive):")
        for pol in POLICIES:
            parts = []
            for b in BANDS:
                a = aggregate(by_band.get((pol, f"{b[0]}-{b[1]}"), []))
                parts.append(f"{b[0]}-{b[1]}: {a['leg_days']}ld R {_f((a['est'] or {}).get('R'))}"
                             f" fills {a['fills']}")
            print(f"    {pol:6s} " + " | ".join(parts))
        if model == DECIDING:
            print("  by series, policy BOTH (descriptive), top 15 by leg-days:")
            rows = []
            for (pol, s), lgs in by_series.items():
                if pol != "BOTH":
                    continue
                a = aggregate(lgs)
                rows.append((a["leg_days"], s, a))
            for ld, s, a in sorted(rows, reverse=True)[:15]:
                print(f"    {s[:24]:24s} {ld:5d}ld {a['events']:3d}ev rew {_f(a['reward_ld'])} "
                      f"loss {_f(a['loss_ld'])} R {_f((a['est'] or {}).get('R'))} "
                      f"fills {a['fills']}")
            print("\n== VERDICT (decides: conservative, 24 h). PROMISING is an OWNER decision for "
                  "a live trial, never automatic")
            for pol in POLICIES:
                print(f"  {pol:6s} {verdicts[pol]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
