"""LIMM FILL WARNING — do snapshot signals in the hour before predict a resting bid's fill?

Pre-registered in docs/LIMM_FILL_WARNING.md (2026-11-04, study 8). Every constant marked
"pre-registered" is frozen there; changing one is a new probe.

Study 5's construction (scripts/limm_fill_hazard.py) is imported unchanged; each qualifying hour
(>= 48h to close) is scored on its own: the warning is read from hour h's (and h-1's) snapshot,
the fill from hour h+1's prints. Policy: pull the bid in warned hours.

Read-only: DATABASE_URL_RO (read-only transaction). Stdlib + psycopg.

Usage: {"type":"script","name":"limm_fill_warning","args":[],"id":"rl-s8-run1-<date>"}
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

from limm_fill_hazard import (  # noqa: E402  (study 5 construction, unchanged)
    DECIDING,
    MARK_HOURS,
    MARK_SEARCH,
    TRADE_SQL,
    mark_bid,
    markout_usd,
    simulate_hour,
    trade_time,
)
from limm_programme_outcomes import (  # noqa: E402
    THIN_SHARE,
    Z,
    _aware,
    cheap_side,
    hourly_reward,
    in_live_universe,
    live_qty,
    qualifies,
)

# --- pre-registered constants ----------------------------------------------------------------
SINCE = "2026-11-27T15:00:00+00:00"
UNTIL = "2026-12-11T15:00:00+00:00"
TRADES_5M_WARN = 1
DEPTH_DROP_WARN = 0.5
MIN_FILLS, MIN_FILL_EVENTS = 30, 10
MIN_WARNED_FILLS = 20
LIFT_USEFUL, LIFT_NO_SIGNAL = 2.0, 1.5

SNAP_SQL = """
WITH s AS (
  SELECT DISTINCT ON (market_ticker, date_trunc('hour', at))
         market_ticker, date_trunc('hour', at) AS hr, program_row_id, best_yes_bid, best_no_bid,
         yes_depth_at_best, no_depth_at_best, est_yes_score_total, est_no_score_total,
         est_yes_meets_target, est_no_meets_target, trades_last_5m, price_range_5m_cents
    FROM incentive_market_snapshots
   WHERE at >= %(since)s AND at < %(mark_until)s AND book_valid
   ORDER BY market_ticker, date_trunc('hour', at), at DESC
)
SELECT s.market_ticker, s.hr, s.best_yes_bid, s.best_no_bid, s.yes_depth_at_best,
       s.no_depth_at_best, s.est_yes_score_total, s.est_no_score_total, s.est_yes_meets_target,
       s.est_no_meets_target, s.trades_last_5m, s.price_range_5m_cents, p.series_ticker,
       p.event_ticker, p.incentive_type, p.period_reward_usd, p.start_date, p.end_date,
       p.close_time
  FROM s LEFT JOIN incentive_programs p ON p.id = s.program_row_id
"""


# ---------------------------------------------------------------------------------------------
# pure helpers (unit-tested in tests/test_limm_fill_warning.py)


def depth_drop(depth_now, depth_prev) -> float | None:
    """Fractional fall in depth at best since the previous hour (0.6 = fell by 60%)."""
    if depth_now is None or depth_prev is None:
        return None
    prev = float(depth_prev)
    if prev <= 0:
        return None
    return max(0.0, (prev - float(depth_now)) / prev)


def warn_trades(trades_5m) -> bool:
    return trades_5m is not None and int(trades_5m) >= TRADES_5M_WARN


def warn_depth(drop) -> bool:
    return drop is not None and drop >= DEPTH_DROP_WARN


def primary_warning(trades_5m, drop) -> bool:
    return warn_trades(trades_5m) or warn_depth(drop)


def lift(fills_w: int, hours_w: int, fills_u: int, hours_u: int) -> float | None:
    if hours_w == 0 or hours_u == 0:
        return None
    rate_u = fills_u / hours_u
    if rate_u == 0:
        return math.inf if fills_w else None
    return (fills_w / hours_w) / rate_u


def cluster_sum(per_event: dict[str, float]) -> dict | None:
    """Total gain and its event-clustered SE (sum of independent event totals)."""
    if not per_event:
        return None
    vals = list(per_event.values())
    k = len(vals)
    total = sum(vals)
    mean = total / k
    se = math.sqrt(k / (k - 1) * sum((v - mean) ** 2 for v in vals)) if k > 1 else float("nan")
    return {"gain": total, "se": se, "events": k}


def verdict(fills: int, fill_events: int, warned_fills: int, lft: float | None,
            gain: dict | None) -> str:
    if fills < MIN_FILLS or fill_events < MIN_FILL_EVENTS:
        return "HOLD (accrual)"
    if gain is None or math.isnan(gain["se"]):
        return "HOLD"
    lcb, ucb = gain["gain"] - Z * gain["se"], gain["gain"] + Z * gain["se"]
    if ucb < 0 or (lft is not None and lft < LIFT_NO_SIGNAL):
        return "NO-SIGNAL"
    if lcb > 0 and lft is not None and lft >= LIFT_USEFUL and warned_fills >= MIN_WARNED_FILLS:
        return "USEFUL"
    return "HOLD"


# ---------------------------------------------------------------------------------------------


def _f(x, spec="{:+.2f}"):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return "inf" if x == math.inf else spec.format(x)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default=SINCE)
    ap.add_argument("--until", default=UNTIL)
    args = ap.parse_args(argv)
    since, until = _aware(args.since), _aware(args.until)
    mark_until = until + dt.timedelta(hours=2 + MARK_HOURS + max(MARK_SEARCH))
    trade_until = until + dt.timedelta(hours=2)

    import psycopg
    from mmsell_flow_veto_probe import RO_OPTIONS, _to_libpq_url

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO not set", file=sys.stderr)
        return 1
    print("LIMM fill warning — snapshot signals one hour before a resting bid's fill "
          "(pre-registered docs/LIMM_FILL_WARNING.md)\n")
    opts = RO_OPTIONS.replace("statement_timeout=300000", "statement_timeout=1500000")
    with psycopg.connect(url, options=opts) as conn, conn.cursor() as cur:
        cur.execute(SNAP_SQL, {"since": since, "mark_until": mark_until})
        snaps = cur.fetchall()
        cur.execute(TRADE_SQL, {"since": since, "trade_until": trade_until})
        trades = cur.fetchall()
    print(f"window {since.isoformat()[:16]} -> {until.isoformat()[:16]}; hourly snapshots "
          f"(incl. mark tail) {len(snaps)}; prints {len(trades)}")

    marks: dict[str, dict[dt.datetime, tuple]] = defaultdict(dict)
    depth_by: dict[str, dict[dt.datetime, tuple]] = defaultdict(dict)
    rows = []
    for (tk, hr, yb, nb, yd, nd, ys, ns, ym, nm, t5, pr5, series, event, itype, pool, start,
         end, close) in snaps:
        hr = _aware(hr)
        marks[tk][hr] = (yb, nb)
        depth_by[tk][hr] = (yd, nd)
        if not (since <= hr < until):
            continue
        if not qualifies({"hr": hr, "start": start, "end": end, "close": close,
                          "yes_meets": ym, "no_meets": nm, "itype": itype}):
            continue
        cs = cheap_side(yb, nb)
        if cs is None:
            continue
        rows.append((tk, hr, cs, yd, nd, ys, ns, t5, pr5,
                     (series or tk.split("-", 1)[0]).upper(), event or tk.rsplit("-", 1)[0],
                     pool, start, end))
    tickers = {r[0] for r in rows}
    tbh: dict[str, dict[dt.datetime, list]] = defaultdict(lambda: defaultdict(list))
    for tk, ts_ms, rcv, yp, cnt, taker in trades:
        if tk not in tickers:
            continue
        at = trade_time(ts_ms, rcv)
        if at is not None:
            tbh[tk][at.replace(minute=0, second=0, microsecond=0)].append((at, yp, cnt, taker))

    pools = ("all", "live")
    agg = {p: {"hours_w": 0, "hours_u": 0, "fills_w": 0, "fills_u": 0, "fill_events": set(),
               "gain": defaultdict(float), "reward_w": 0.0, "loss_w": 0.0,
               "t5_null": 0, "depth_null": 0, "solo": defaultdict(lambda: [0, 0])}
           for p in pools}
    for (tk, hr, (side, price), yd, nd, ys, ns, t5, pr5, series, event, pool, start,
         end) in rows:
        qty = live_qty(price)
        hrw = hourly_reward(pool, start, end, qty, ys if side == "yes" else ns)
        reward = 0.0 if hrw is None or hrw[1] > THIN_SHARE else hrw[0]
        prev = depth_by[tk].get(hr - dt.timedelta(hours=1))
        drop = depth_drop(yd if side == "yes" else nd,
                          None if prev is None else (prev[0] if side == "yes" else prev[1]))
        warned = primary_warning(t5, drop)
        got = simulate_hour(side, price, qty, yd if side == "yes" else nd,
                            tbh.get(tk, {}).get(hr + dt.timedelta(hours=1), []))[DECIDING]
        loss = markout_usd(got, price, mark_bid(marks.get(tk, {}), side, hr)) if got > 0 else 0.0
        for p in pools:
            if p == "live" and not in_live_universe(series):
                continue
            a = agg[p]
            a["t5_null"] += t5 is None
            a["depth_null"] += drop is None
            filled = got > 0
            if filled:
                a["fill_events"].add(event)
            if warned:
                a["hours_w"] += 1
                a["fills_w"] += filled
                a["reward_w"] += reward
                a["loss_w"] += loss
                a["gain"][event] += loss - reward
            else:
                a["hours_u"] += 1
                a["fills_u"] += filled
            for name, w in (("trades_5m>=1", warn_trades(t5)), ("depth_drop>=50%", warn_depth(drop)),
                            ("range_5m>=2c", pr5 is not None and int(pr5) >= 2)):
                if w:
                    a["solo"][name][0] += 1
                    a["solo"][name][1] += filled

    print(f"qualifying hours {len(rows)}; markets {len(tickers)}; markets with prints "
          f"{sum(1 for t in tickers if t in tbh)}")
    for p in pools:
        a = agg[p]
        hours = a["hours_w"] + a["hours_u"]
        fills = a["fills_w"] + a["fills_u"]
        lf = lift(a["fills_w"], a["hours_w"], a["fills_u"], a["hours_u"])
        g = cluster_sum(dict(a["gain"]))
        print(f"\n== pool `{p}` ({DECIDING} fills)")
        print(f"  hours {hours} (warned {a['hours_w']}); fills {fills} (warned {a['fills_w']}) "
              f"in {len(a['fill_events'])} events; feature nulls: trades_5m {a['t5_null']}, "
              f"depth {a['depth_null']}")
        print(f"  fill rate warned {_f(a['fills_w'] / a['hours_w'] if a['hours_w'] else None, '{:.3f}')}"
              f" vs unwarned {_f(a['fills_u'] / a['hours_u'] if a['hours_u'] else None, '{:.3f}')}"
              f"; lift {_f(lf, '{:.2f}')}")
        print(f"  pull-on-warning: fill cost avoided ${a['loss_w']:.2f}, reward given up "
              f"${a['reward_w']:.2f}; gain {_f(g and g['gain'])} ± "
              f"{_f(g and g['se'], '{:.2f}')} (events {(g or {}).get('events', 0)})")
        print("  single features (descriptive): " + "; ".join(
            f"{k}: {v[1]}/{v[0]} filled" for k, v in sorted(a["solo"].items())))
        print(f"  VERDICT ({'decides' if p == 'all' else 'descriptive'}): "
              f"{verdict(fills, len(a['fill_events']), a['fills_w'], lf, g)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
