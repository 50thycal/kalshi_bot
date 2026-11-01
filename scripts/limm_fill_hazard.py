"""LIMM FILL HAZARD — which incentive series pay net of the fills a resting cheap-side bid takes.

Pre-registered in docs/LIMM_FILL_HAZARD.md (2026-11-01, study 5). Every constant marked
"pre-registered" is frozen there; changing one is a new probe.

Hours, cheap side, live-rule size and the reward estimator are study 3's (§3 of
docs/LIMM_PROGRAMME_OUTCOMES.md), imported unchanged. New here: the bid placed on hour h's
snapshot rests through clock hour h+1 and is replayed against that hour's public prints under the
`conservative` fill model (depth at best is ahead of us), with `optimistic` as a sensitivity. A
fill ends the market's window; its cost is the markout to the same side's best bid 6 h later.

Read-only: DATABASE_URL_RO (read-only transaction). Stdlib + psycopg.

Usage: {"type":"script","name":"limm_fill_hazard","args":[],"id":"rl-s5-run1-<date>"}
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

from limm_programme_outcomes import (  # noqa: E402  (study 3 construction, unchanged)
    THIN_SHARE,
    Z,
    _aware,
    cheap_side,
    hourly_reward,
    in_live_universe,
    live_qty,
    qualifies,
)

from kalshi_bot.liquidity_incentive.fills import (  # noqa: E402
    MODEL_CONSERVATIVE,
    MODEL_OPTIMISTIC,
    ShadowLeg,
)

# --- pre-registered constants ----------------------------------------------------------------
SINCE = "2026-10-30T16:00:00+00:00"
UNTIL = "2026-11-13T15:00:00+00:00"
MARK_HOURS = 6                      # markout horizon after the resting hour
MARK_SEARCH = (-2, -1, 1, 2)        # nearest-hour fallback, h+5 .. h+9
UNMARKED_FRACTION = 0.5             # no mark: charged half the price (the stop trigger)
MIN_MARKET_DAYS, MIN_EVENTS = 20, 5
NET_BAR_USD = 0.50
MIN_SERIES_AT_FLOOR = 3
MODELS = (MODEL_CONSERVATIVE, MODEL_OPTIMISTIC)
DECIDING = MODEL_CONSERVATIVE

SNAP_SQL = """
WITH s AS (
  SELECT DISTINCT ON (market_ticker, date_trunc('hour', at))
         market_ticker, date_trunc('hour', at) AS hr, program_row_id, best_yes_bid, best_no_bid,
         yes_depth_at_best, no_depth_at_best, est_yes_score_total, est_no_score_total,
         est_yes_meets_target, est_no_meets_target
    FROM incentive_market_snapshots
   WHERE at >= %(since)s AND at < %(mark_until)s AND book_valid
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
 WHERE received_at >= %(since)s AND received_at < %(trade_until)s
"""


# ---------------------------------------------------------------------------------------------
# pure helpers (unit-tested in tests/test_limm_fill_hazard.py)


def trade_time(ts_ms, received_at) -> dt.datetime | None:
    """Exchange time when present, else ingest time."""
    if ts_ms:
        return dt.datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=dt.timezone.utc)
    return _aware(received_at)


def yes_scale(side: str, price: int) -> int:
    return int(price) if side == "yes" else 100 - int(price)


def simulate_hour(side: str, price: int, qty: int, depth_ahead, trades) -> dict[str, float]:
    """Contracts filled per model for one resting hour. `trades` = [(at, yes_price, count,
    taker_side)] in that hour, any order (sorted here)."""
    leg = ShadowLeg(side=side, price_yes_scale=yes_scale(side, price), quantity=float(qty),
                    placed_at=dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc),
                    queue_ahead_at_placement=float(depth_ahead or 0.0))
    for at, yp, cnt, taker in sorted(trades, key=lambda t: t[0]):
        leg.on_trade(yes_price_cents=yp, count=float(cnt or 0), taker_outcome_side=taker, at=at)
    return {m: leg.filled.get(m, 0.0) for m in MODELS}


def mark_bid(marks: dict[dt.datetime, tuple], side: str, hr: dt.datetime) -> int | None:
    """The side's best bid MARK_HOURS after the resting hour (h+1), nearest within the search."""
    base = hr + dt.timedelta(hours=1 + MARK_HOURS)
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


def ratio_cluster(per_event: dict[str, tuple[float, float]]) -> dict | None:
    """Ratio estimate R = sum(net) / sum(md) with an event-clustered linearised SE.
    `per_event` = {event: (net_usd, market_days)}."""
    md = sum(m for _, m in per_event.values())
    if md <= 0:
        return None
    net = sum(n for n, _ in per_event.values())
    r = net / md
    k = len(per_event)
    if k > 1:
        ss = sum((n - r * m) ** 2 for n, m in per_event.values())
        se = math.sqrt(k / (k - 1) * ss) / md
    else:
        se = float("nan")
    return {"R": r, "se": se, "events": k, "md": md}


def label(md: int, events: int, reward_md: float | None, est: dict | None) -> str:
    if md < MIN_MARKET_DAYS or events < MIN_EVENTS or est is None or reward_md is None:
        return "THIN"
    lcb = est["R"] - Z * est["se"] if not math.isnan(est["se"]) else None
    if est["R"] >= NET_BAR_USD and lcb is not None and lcb > 0:
        return "NET-LEAD"
    if reward_md >= NET_BAR_USD and est["R"] <= 0:
        return "FILL-TRAP"
    return "NO-LEAD"


def run_market(hours: list[dict], trades_by_hour: dict, marks: dict, model: str) -> dict:
    """Walk one market's qualifying hours in time order under one fill model.
    Each hour dict: hr, side, price, qty, depth, reward (USD or None when thin/unreadable)."""
    out = {"reward": 0.0, "loss": 0.0, "fills": 0, "unmarked": 0, "filled_at": None}
    for h in sorted(hours, key=lambda x: x["hr"]):
        if h["reward"] is not None:
            out["reward"] += h["reward"]
        nxt = h["hr"] + dt.timedelta(hours=1)
        got = simulate_hour(h["side"], h["price"], h["qty"], h["depth"],
                            trades_by_hour.get(nxt, []))[model]
        if got > 0:
            bid = mark_bid(marks, h["side"], h["hr"])
            out["loss"] += markout_usd(got, h["price"], bid)
            out["fills"] = 1
            out["unmarked"] = int(bid is None)
            out["filled_at"] = nxt
            break
    return out


# ---------------------------------------------------------------------------------------------


def _f(x, spec="{:+.2f}"):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else spec.format(x)


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
    print("LIMM fill hazard — reward minus fill markout per market-day "
          "(pre-registered docs/LIMM_FILL_HAZARD.md)\n")
    opts = RO_OPTIONS.replace("statement_timeout=300000", "statement_timeout=1500000")
    with psycopg.connect(url, options=opts) as conn, conn.cursor() as cur:
        cur.execute(SNAP_SQL, {"since": since, "mark_until": mark_until})
        snaps = cur.fetchall()
        cur.execute(TRADE_SQL, {"since": since, "trade_until": trade_until})
        trades = cur.fetchall()
    print(f"window {since.isoformat()[:16]} -> {until.isoformat()[:16]}; hourly snapshots "
          f"(incl. mark tail) {len(snaps)}; prints {len(trades)}")

    marks: dict[str, dict[dt.datetime, tuple]] = defaultdict(dict)
    hours: dict[str, list[dict]] = defaultdict(list)
    meta: dict[str, tuple[str, str]] = {}
    thin_h = n_q = 0
    for (tk, hr, yb, nb, yd, nd, ys, ns, ym, nm, series, event, itype, pool, start, end,
         close) in snaps:
        hr = _aware(hr)
        marks[tk][hr] = (yb, nb)
        if not (since <= hr < until):
            continue
        row = {"hr": hr, "start": start, "end": end, "close": close, "yes_meets": ym,
               "no_meets": nm, "itype": itype}
        if not qualifies(row):
            continue
        cs = cheap_side(yb, nb)
        if cs is None:
            continue
        side, price = cs
        qty = live_qty(price)
        hrw = hourly_reward(pool, start, end, qty, ys if side == "yes" else ns)
        reward = None
        if hrw is not None:
            if hrw[1] > THIN_SHARE:
                thin_h += 1
            else:
                reward = hrw[0]
        n_q += 1
        meta[tk] = ((series or tk.split("-", 1)[0]).upper(), event or tk.rsplit("-", 1)[0])
        hours[tk].append({"hr": hr, "side": side, "price": price, "qty": qty,
                          "depth": yd if side == "yes" else nd, "reward": reward})
    tbh: dict[str, dict[dt.datetime, list]] = defaultdict(lambda: defaultdict(list))
    for tk, ts_ms, rcv, yp, cnt, taker in trades:
        if tk not in hours:
            continue
        at = trade_time(ts_ms, rcv)
        if at is None:
            continue
        tbh[tk][at.replace(minute=0, second=0, microsecond=0)].append((at, yp, cnt, taker))
    print(f"qualifying cheap-side hours {n_q} (thin, no reward: {thin_h}); markets {len(hours)}; "
          f"markets with prints {sum(1 for t in hours if t in tbh)}")

    results: dict[str, dict] = {}
    for model in MODELS:
        agg = defaultdict(lambda: {"md": set(), "reward": 0.0, "loss": 0.0, "fills": 0,
                                   "unmarked": 0, "events": defaultdict(lambda: [0.0, 0])})
        for tk, hs in hours.items():
            series, event = meta[tk]
            r = run_market(hs, tbh.get(tk, {}), marks.get(tk, {}), model)
            days = {(tk, h["hr"].date()) for h in hs}
            a = agg[series]
            a["md"] |= days
            a["reward"] += r["reward"]
            a["loss"] += r["loss"]
            a["fills"] += r["fills"]
            a["unmarked"] += r["unmarked"]
            ev = a["events"][event]
            ev[0] += r["reward"] - r["loss"]
            ev[1] += len(days)
        out = {}
        for s, a in agg.items():
            md = len(a["md"])
            est = ratio_cluster({e: (v[0], v[1]) for e, v in a["events"].items()})
            rmd = a["reward"] / md if md else None
            out[s] = {"md": md, "events": len(a["events"]), "reward_md": rmd,
                      "loss_md": a["loss"] / md if md else None, "fills": a["fills"],
                      "unmarked": a["unmarked"], "est": est,
                      "label": label(md, len(a["events"]), rmd, est),
                      "per_event": {e: (v[0], v[1]) for e, v in a["events"].items()}}
        results[model] = out

    dec, opt = results[DECIDING], results[MODEL_OPTIMISTIC]
    rows = sorted(dec.items(), key=lambda kv: -((kv[1]["est"] or {}).get("R", -1e9)))
    print(f"\n== series (deciding model: {DECIDING}; * = label differs under optimistic)")
    print(f"  {'series':26s} {'live':>4} {'mkt-d':>5} {'evts':>4} {'rew/md':>7} {'loss/md':>7} "
          f"{'R net/md':>8} {'±se':>5} {'fills':>5} {'/100md':>6} {'R opt':>7}  label")
    for s, v in rows:
        est, o = v["est"] or {}, opt.get(s, {})
        star = "*" if o.get("label") != v["label"] else ""
        print(f"  {s[:26]:26s} {'yes' if in_live_universe(s) else '':>4} {v['md']:5d} "
              f"{v['events']:4d} {_f(v['reward_md']):>7} {_f(v['loss_md']):>7} "
              f"{_f(est.get('R')):>8} {_f(est.get('se'), '{:.2f}'):>5} {v['fills']:5d} "
              f"{_f(100 * v['fills'] / v['md'] if v['md'] else None, '{:.1f}'):>6} "
              f"{_f((o.get('est') or {}).get('R')):>7}  {v['label']}{star}")
    print(f"  unmarked fills (charged half price): "
          f"{sum(v['unmarked'] for v in dec.values())}")

    live_ev: dict[str, tuple[float, float]] = {}
    for s, v in dec.items():
        if in_live_universe(s):
            live_ev.update({f"{s}|{e}": t for e, t in v["per_event"].items()})
    live = ratio_cluster(live_ev)
    at_floor = [s for s, v in dec.items() if v["label"] != "THIN"]
    print("\n== live universe pooled")
    print(f"  R {_f(live and live['R'])} ± {_f(live and live['se'], '{:.2f}')} per market-day "
          f"over {(live or {}).get('md', 0):.0f} market-days, {(live or {}).get('events', 0)} events")

    def _names(pred):
        return [s + ("*" if opt.get(s, {}).get("label") != dec[s]["label"] else "")
                for s, v in rows if pred(s, v)]

    print("\n== VERDICT (descriptive; nominations are OWNER decisions, never automatic)")
    if len(at_floor) < MIN_SERIES_AT_FLOOR:
        print(f"  V0 instrument: HOLD (accrual) — {len(at_floor)} series at the floor "
              f"(need {MIN_SERIES_AT_FLOOR}); re-read once 2026-11-27, same bars")
        return 0
    print(f"  V0 instrument: OK — {len(at_floor)} series at the floor")
    v1 = _names(lambda s, v: not in_live_universe(s) and v["label"] == "NET-LEAD"
                and live is not None and v["est"]["R"] > live["R"])
    v2 = _names(lambda s, v: in_live_universe(s) and v["label"] == "FILL-TRAP")
    print(f"  V1 better markets: {'NOMINATE ' + ', '.join(v1) if v1 else 'none'}")
    print(f"  V2 markets to avoid: {'NOMINATE removal ' + ', '.join(v2) if v2 else 'none'}")
    for lab in ("NET-LEAD", "FILL-TRAP", "NO-LEAD"):
        names = _names(lambda s, v, lab=lab: v["label"] == lab)
        print(f"  {lab:10s} {len(names):3d}  {', '.join(names)}")
    print("  (* = model-sensitive: label differs under optimistic)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
