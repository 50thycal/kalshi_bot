"""LIMM PAPER STRATEGIES READ — S1-S4's seven paper arms, read once against pre-registered bars.

Pre-registered in docs/LIMM_PAPER_STRATEGIES.md (DEC-043/044). Every constant marked
pre-registered is frozen there; changing one after recording starts is a new version.

What it reads (all written by the shadow collector, `liquidity_incentive/paper_arms.py`):
  incentive_shadow_quotes   (policy = arm; one row is about one bid-hour)
  incentive_shadow_outcomes (one row per quote per fill model: reward estimate, fills, settlement)
  incentive_shadow_marks    (the 24 h markout of a fill)
  incentive_shadow_events   (kind 'arm_hour': share, qualification and thin-rule seconds)

Per arm and fill model:
  * calibrated reward = estimate x CALIBRATION, with Kalshi's $1 minimum applied per market per
    programme (a programme where the arm earned under $1 counts $0);
  * fill rate per bid-hour, and fill cost at 24 h (the 24 h mark; else the settlement; else half
    the price, study 5's rule) and at settlement (settled fills only);
  * net = calibrated reward - fill cost at 24 h, per $10 bid-day and per $ of capital-day,
    with an event-clustered SE (study 5's ratio_cluster);
  * the cycle-8 yardstick: paid c/h against fill c/h.
Pairwise, on the matched markets and hours of each pair: S2-best - S2-behind, S3-aside -
S3-control, S4-burst - S4-flat (and for S4 the share of programmes that clear $1).

NO PEEKING: the full read refuses to run before READ_NOT_BEFORE. `--counts` (rows, markets,
bid-hours per arm, nothing else) is allowed any time.

Read-only: DATABASE_URL_RO (read-only transaction). Stdlib + psycopg.

Usage: {"type":"script","name":"limm_paper_strategies_read","args":["--counts"],"id":"rl-ps-counts-<date>"}
       {"type":"script","name":"limm_paper_strategies_read","args":[],"id":"rl-ps-read-<date>"}
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from limm_fill_hazard import ratio_cluster  # noqa: E402  (study 5's event-clustered ratio)

# --- pre-registered constants (docs/LIMM_PAPER_STRATEGIES.md) --------------------------------
RECORDING_START = "2026-11-09T00:00:00+00:00"
READ_NOT_BEFORE = "2026-11-24T00:00:00+00:00"
ARMS = ("S1", "S2-best", "S2-behind", "S3-aside", "S3-control", "S4-burst", "S4-flat")
PAIRS = (("S2-best", "S2-behind"), ("S3-aside", "S3-control"), ("S4-burst", "S4-flat"))
MODELS = ("conservative", "queue_aware", "optimistic", "whole_order")
PRIMARY_MODEL = "conservative"
STRESS_MODEL = "whole_order"
CALIBRATION = 1.34                   # global median paid/est, fixed in advance
KALSHI_MINIMUM_USD = 1.00
UNMARKED_FRACTION = 0.5              # study 5: an unmarked, unsettled fill is charged half its price
PROMISING_USD = 0.15                 # net per $10 bid-day
MIN_BID_DAYS, MIN_EVENTS = 100.0, 10
Z = 1.96
BID_UNIT_USD = 10.0

QUOTES_SQL = """
SELECT id, policy, market_ticker, placed_at, coalesce(rest_seconds, 0), capital_tier_usd,
       yes_bid, no_bid, qty_per_side, capital_required_usd, book_json -> 'arm'
  FROM incentive_shadow_quotes
 WHERE policy = ANY(%(arms)s) AND placed_at >= %(since)s AND placed_at < %(until)s
"""
OUTCOMES_SQL = """
SELECT quote_id, fill_model, est_reward_usd, single_leg_side, single_leg_qty, settlement_pnl_usd
  FROM incentive_shadow_outcomes
 WHERE policy = ANY(%(arms)s) AND placed_at >= %(since)s AND placed_at < %(until)s
"""
MARKS_SQL = """
SELECT m.quote_id, m.fill_model, m.fill_price_cents, m.mark_bid_cents
  FROM incentive_shadow_marks m JOIN incentive_shadow_quotes q ON q.id = m.quote_id
 WHERE m.horizon_seconds = 86400 AND q.policy = ANY(%(arms)s)
   AND q.placed_at >= %(since)s AND q.placed_at < %(until)s
"""
HOURS_SQL = """
SELECT e.quote_id, e.detail_json
  FROM incentive_shadow_events e JOIN incentive_shadow_quotes q ON q.id = e.quote_id
 WHERE e.kind = 'arm_hour' AND q.policy = ANY(%(arms)s)
   AND q.placed_at >= %(since)s AND q.placed_at < %(until)s
"""


# ---------------------------------------------------------------------------------------------
# pure helpers (unit-tested in tests/test_limm_paper_strategies_read.py)


def _aware(x):
    if x is None:
        return None
    if isinstance(x, str):
        x = dt.datetime.fromisoformat(x)
    return x if x.tzinfo else x.replace(tzinfo=dt.timezone.utc)


def _f(x):
    return None if x is None else float(x)


def event_of(ticker: str, arm_meta: dict | None) -> str:
    ev = (arm_meta or {}).get("event")
    if ev:
        return ev
    return ticker.rsplit("-", 1)[0] if ticker.count("-") >= 2 else ticker


def fill_cost(side_price: int, qty: float, mark_bid: int | None, settlement_pnl: float | None) -> tuple[float, str]:
    """Cost (USD, positive = loss) of an unmatched filled leg at 24 h, and which source priced it."""
    if qty <= 0:
        return 0.0, "none"
    if mark_bid is not None:
        return (side_price - mark_bid) * qty / 100.0, "mark24h"
    if settlement_pnl is not None:
        return -float(settlement_pnl), "settlement"
    return side_price * UNMARKED_FRACTION * qty / 100.0, "unmarked"


def apply_minimum(by_programme: dict) -> tuple[dict, int, int]:
    """{programme: calibrated USD} -> the same with programmes under Kalshi's $1 set to 0;
    plus (programmes, programmes that clear $1)."""
    out = {k: (v if v >= KALSHI_MINIMUM_USD else 0.0) for k, v in by_programme.items()}
    return out, len(out), sum(1 for v in by_programme.values() if v >= KALSHI_MINIMUM_USD)


def label(r: dict | None, bid_days: float, events: int, stress_net: float | None) -> str:
    if r is None or bid_days < MIN_BID_DAYS or events < MIN_EVENTS:
        return "HOLD"
    lcb = r["R"] - Z * r["se"] if not math.isnan(r["se"]) else None
    if r["R"] >= PROMISING_USD and lcb is not None and lcb > 0:
        if stress_net is not None and stress_net < 0:
            return "FRAGILE"          # PROMISING under the primary model, a loss under whole-order
        return "PROMISING"
    return "NOT-PROMISING"


def decides(d: dict | None) -> str:
    if d is None or math.isnan(d["se"]):
        return "UNDECIDED"
    lo, hi = d["R"] - Z * d["se"], d["R"] + Z * d["se"]
    if lo > 0:
        return "A-BETTER"
    if hi < 0:
        return "B-BETTER"
    return "UNDECIDED"


def counts(quotes) -> dict:
    out = {a: {"quotes": 0, "markets": set(), "bid_hours": 0.0} for a in ARMS}
    for q in quotes:
        a = out.get(q[1])
        if a is None:
            continue
        a["quotes"] += 1
        a["markets"].add(q[2])
        a["bid_hours"] += float(q[4] or 0.0) / 3600.0
    return {k: {"quotes": v["quotes"], "markets": len(v["markets"]),
                "bid_hours": round(v["bid_hours"], 1)} for k, v in out.items()}


def analyse(quotes, outcomes, marks, hours) -> dict:
    """Everything the read reports, from raw row tuples (see the SQL above for column order)."""
    q_by_id = {}
    for (qid, arm, tk, _placed, rest_s, size, yb, nb, _qty, cap, meta) in quotes:
        meta = meta if isinstance(meta, dict) else (json.loads(meta) if meta else {})
        side = (meta or {}).get("side") or ("yes" if (yb or 0) > 0 else "no")
        price = int(yb if side == "yes" else nb)
        q_by_id[qid] = {"arm": arm, "tk": tk, "event": event_of(tk, meta),
                        "prog": (tk, (meta or {}).get("programme_start") or ""),
                        "rest_h": float(rest_s or 0) / 3600.0, "size": float(size), "price": price,
                        "cap": float(cap or 0.0)}
    mark = {(qid, mo): (int(fp), None if mb is None else int(mb)) for qid, mo, fp, mb in marks}
    thin = defaultdict(lambda: [0.0, 0.0, 0.0])          # arm -> [scored, qualifying, thin] seconds
    for qid, detail in hours:
        q = q_by_id.get(qid)
        d = detail if isinstance(detail, dict) else (json.loads(detail) if detail else {})
        if q is None or not d:
            continue
        t = thin[q["arm"]]
        t[0] += float(d.get("seconds_scored") or 0)
        t[1] += float(d.get("seconds_qualifying") or 0)
        t[2] += float(d.get("seconds_thin") or 0)

    # per (arm, model): rows of (quote, reward, cost24, cost_settle, filled?)
    acc: dict[tuple, list] = defaultdict(list)
    for (qid, model, est, sl_side, sl_qty, settle) in outcomes:
        q = q_by_id.get(qid)
        if q is None or model not in MODELS:
            continue
        qty = float(sl_qty or 0.0) if sl_side else 0.0
        mk = mark.get((qid, model))
        cost24, src = fill_cost(q["price"], qty, None if mk is None else mk[1], _f(settle))
        cost_settle = (-float(settle)) if (qty > 0 and settle is not None) else None
        acc[(q["arm"], model)].append({"q": q, "reward": float(est or 0.0) * CALIBRATION,
                                       "cost24": cost24, "src": src, "cost_settle": cost_settle,
                                       "filled": qty > 0})
    arms_out: dict[str, dict] = {}
    per_event_net: dict[tuple, dict] = {}
    for arm in ARMS:
        arm_rows = {}
        for model in MODELS:
            rows = acc.get((arm, model), [])
            by_prog: dict[tuple, float] = defaultdict(float)
            prog_event: dict[tuple, str] = {}
            for r in rows:
                by_prog[r["q"]["prog"]] += r["reward"]
                prog_event[r["q"]["prog"]] = r["q"]["event"]
            paid, n_prog, n_clear = apply_minimum(by_prog)
            ev: dict[str, list] = defaultdict(lambda: [0.0, 0.0, 0.0])     # net, $10 bid-days, capital-days
            for prog, usd in paid.items():
                ev[prog_event[prog]][0] += usd
            bid_hours = fills = 0.0
            cost24 = 0.0
            settled = [0, 0.0]
            srcs: dict[str, int] = defaultdict(int)
            for r in rows:
                q = r["q"]
                e = ev[q["event"]]
                e[0] -= r["cost24"]
                e[1] += (q["size"] / BID_UNIT_USD) * q["rest_h"] / 24.0
                e[2] += q["cap"] * q["rest_h"] / 24.0
                bid_hours += q["rest_h"]
                fills += int(r["filled"])
                cost24 += r["cost24"]
                if r["filled"]:
                    srcs[r["src"]] += 1
                if r["cost_settle"] is not None:
                    settled[0] += 1
                    settled[1] += r["cost_settle"]
            per_event_net[(arm, model)] = {k: (v[0], v[1]) for k, v in ev.items()}
            r10 = ratio_cluster({k: (v[0], v[1]) for k, v in ev.items()})
            rcap = ratio_cluster({k: (v[0], v[2]) for k, v in ev.items()})
            days = sum(v[1] for v in ev.values())
            reward = sum(paid.values())
            arm_rows[model] = {
                "bid_days_10usd": round(days, 2), "events": len(ev), "programmes": n_prog,
                "programmes_clearing_1usd": n_clear, "reward_calibrated_after_min": round(reward, 4),
                "fills": int(fills), "fills_per_100_bid_hours": (100 * fills / bid_hours) if bid_hours else None,
                "fill_cost_24h": round(cost24, 4), "fill_cost_sources": dict(srcs),
                "settled_fills": settled[0], "fill_cost_at_settlement": round(settled[1], 4),
                "net_per_10usd_bid_day": r10, "net_per_capital_usd_day": rcap,
                "paid_c_per_bid_hour": (100 * reward / bid_hours) if bid_hours else None,
                "fill_c_per_bid_hour": (100 * cost24 / bid_hours) if bid_hours else None,
            }
        prim, stress = arm_rows[PRIMARY_MODEL], arm_rows[STRESS_MODEL]
        t = thin.get(arm, [0.0, 0.0, 0.0])
        arms_out[arm] = {
            "models": arm_rows,
            "label": label(prim["net_per_10usd_bid_day"], prim["bid_days_10usd"], prim["events"],
                           None if stress["net_per_10usd_bid_day"] is None else stress["net_per_10usd_bid_day"]["R"]),
            "thin_share_of_scored_time": (t[2] / t[0]) if t[0] else None,
            "qualifying_share_of_scored_time": (t[1] / t[0]) if t[0] else None,
        }
    pairs_out = {}
    for a, b in PAIRS:
        for model in (PRIMARY_MODEL, STRESS_MODEL):
            ea, eb = per_event_net.get((a, model), {}), per_event_net.get((b, model), {})
            matched = sorted(set(ea) & set(eb))
            # Difference per event over the CONTROL's bid-days (the pull arms rest fewer hours).
            d = ratio_cluster({e: (ea[e][0] - eb[e][0], eb[e][1]) for e in matched})
            pairs_out[f"{a} - {b} [{model}]"] = {"events": len(matched), "diff_per_10usd_bid_day": d,
                                                "decision": decides(d)}
    s4 = {arm: {"programmes": arms_out[arm]["models"][PRIMARY_MODEL]["programmes"],
                "clearing_1usd": arms_out[arm]["models"][PRIMARY_MODEL]["programmes_clearing_1usd"]}
          for arm in ("S4-burst", "S4-flat")}
    return {"arms": arms_out, "pairs": pairs_out, "s4_clear_1usd": s4}


def _fmt(r):
    if r is None:
        return "n/a"
    se = "nan" if math.isnan(r["se"]) else f"{r['se']:.3f}"
    return f"{r['R']:+.3f} (se {se}, k={r['events']})"


def render(res: dict) -> str:
    out = ["arm         label          bid-days  events  progs  >=$1  net/$10-day [conservative]       "
           "whole-order net   paid c/h  fill c/h  thin%"]
    for arm, a in res["arms"].items():
        p, s = a["models"][PRIMARY_MODEL], a["models"][STRESS_MODEL]
        thin = "n/a" if a["thin_share_of_scored_time"] is None else f"{100 * a['thin_share_of_scored_time']:.0f}"
        pc = "n/a" if p["paid_c_per_bid_hour"] is None else f"{p['paid_c_per_bid_hour']:.2f}"
        fc = "n/a" if p["fill_c_per_bid_hour"] is None else f"{p['fill_c_per_bid_hour']:.2f}"
        out.append(f"{arm:11} {a['label']:14} {p['bid_days_10usd']:8.1f} {p['events']:7} {p['programmes']:6} "
                   f"{p['programmes_clearing_1usd']:5}  {_fmt(p['net_per_10usd_bid_day']):30} "
                   f"{_fmt(s['net_per_10usd_bid_day']):17} {pc:>9} {fc:>9} {thin:>6}")
    out += ["", "fills and fill cost by model (24 h; settled-only at settlement):"]
    for arm, a in res["arms"].items():
        cells = []
        for model in MODELS:
            m = a["models"][model]
            cells.append(f"{model}: {m['fills']} fills ${m['fill_cost_24h']:.2f}/24h "
                         f"(settled {m['settled_fills']}: ${m['fill_cost_at_settlement']:.2f})")
        out.append(f"  {arm:11} " + " | ".join(cells))
    out += ["", "pairwise (per event, over the control's $10 bid-days):"]
    for k, v in res["pairs"].items():
        out.append(f"  {k:40} events {v['events']:3}  {_fmt(v['diff_per_10usd_bid_day']):30} {v['decision']}")
    out += ["", f"S4 programmes clearing Kalshi's $1 minimum: {res['s4_clear_1usd']}"]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--counts", action="store_true", help="rows, markets and bid-hours only")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    now = dt.datetime.now(dt.timezone.utc)
    since = _aware(RECORDING_START)
    if not args.counts and now < _aware(READ_NOT_BEFORE):
        print(f"REFUSED: the read is pre-registered for {READ_NOT_BEFORE} or later "
              f"(docs/LIMM_PAPER_STRATEGIES.md). Use --counts before then.")
        return 2

    import psycopg
    from mmsell_flow_veto_probe import RO_OPTIONS, _to_libpq_url

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO not set", file=sys.stderr)
        return 1
    p = {"arms": list(ARMS), "since": since, "until": now}
    with psycopg.connect(url, options=RO_OPTIONS) as conn, conn.cursor() as cur:
        cur.execute(QUOTES_SQL, p)
        quotes = cur.fetchall()
        if args.counts:
            c = counts(quotes)
            print(f"LIMM paper strategies — COUNTS ONLY since {RECORDING_START[:16]} (as of {now.isoformat()[:16]})")
            for arm, v in c.items():
                print(f"  {arm:11} quotes {v['quotes']:6}  markets {v['markets']:4}  bid-hours {v['bid_hours']:8.1f}")
            if args.json:
                print(json.dumps(c))
            return 0
        cur.execute(OUTCOMES_SQL, p)
        outcomes = cur.fetchall()
        cur.execute(MARKS_SQL, p)
        marks = cur.fetchall()
        cur.execute(HOURS_SQL, p)
        hours = cur.fetchall()
    res = analyse(quotes, outcomes, marks, hours)
    print("LIMM paper strategies — the single pre-registered read (docs/LIMM_PAPER_STRATEGIES.md)\n")
    print(f"window {RECORDING_START[:16]} -> {now.isoformat()[:16]}; quotes {len(quotes)}; "
          f"outcome rows {len(outcomes)}; 24 h marks {len(marks)}\n")
    print(render(res))
    if args.json:
        print(json.dumps(res, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
