#!/usr/bin/env python3
"""Liquidity-incentive calibration: the bot's own reward estimate against what Kalshi paid.

Operator decision r4-calibrate (2026-11-08): before the per-hour entry/exit rule is built on the
bot's reward estimate, check that estimate against real payouts. For every entry bid the live
book rested, this re-scores each of the book's own 10-minute order-book snapshots
(`incentive_market_snapshots`, written by `runner._snapshot_own_books`) with the SAME scoring
function the runner uses (`liquidity_incentive/scoring.estimate`, loaded from its file: the
ops runner installs psycopg and nothing else), at the bid's real price and size, and
integrates that rate over the time the bid rested. Summed per event, the estimate is set
beside the confirmed payouts (`CONFIRMED_REWARD_PAYOUTS`).

What it reports:
  * per event: estimated $, paid $, paid/estimated, snapshot coverage of the resting time;
  * overall: paid/estimated across events with a confirmed payout (the calibration factor);
  * per market: how many markets the estimate puts under Kalshi's $1 programme minimum.

Approximations, stated rather than hidden: a snapshot holds the top 10 levels per side, so a
side whose qualifying depth sits deeper reads short of Target Size (estimate 0 for that
snapshot); a gap between snapshots is filled with the earlier snapshot's rate, capped at
MAX_GAP_SECONDS (the rest of a longer gap is counted as uncovered, not guessed).

Read-only, stdlib + psycopg. Decides nothing, writes nothing.

    {"type": "script", "name": "limm_estimate_calibration", "args": ["--json"]}
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import importlib.util
import json
import os
import sys

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=300000 "
    "-c idle_in_transaction_session_timeout=300000"
)
LIVE_TAG = "Alimm1"
CLOSING_PREFIXES = ("limmexit", "limmsell:")
#: Two missed snapshots' worth: a longer gap is uncovered time, not extrapolated.
MAX_GAP_SECONDS = 1800.0
HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, os.pardir, "kalshi_bot", "liquidity_incentive")
LEDGER_PATH = os.path.join(PKG, "reward_ledger.py")
SCORING_PATH = os.path.join(PKG, "scoring.py")


def load_scoring():
    spec = importlib.util.spec_from_file_location("limm_scoring", SCORING_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["limm_scoring"] = mod   # dataclasses resolve the module by name
    spec.loader.exec_module(mod)
    return mod


def load_payouts(path: str = LEDGER_PATH) -> list[tuple[str, int, str]]:
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
        if target == "CONFIRMED_REWARD_PAYOUTS" and node.value is not None:
            return [tuple(x) for x in ast.literal_eval(node.value)]
    raise ValueError(f"CONFIRMED_REWARD_PAYOUTS not found in {path}")


def event_of(ticker: str) -> str:
    return ticker.rsplit("-", 1)[0] if ticker.count("-") >= 2 else ticker


def _utc(t):
    if t is None:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _levels(raw) -> dict[int, float]:
    out: dict[int, float] = {}
    for item in raw or []:
        try:
            p, q = item[0], item[1]
            out[int(p)] = out.get(int(p), 0.0) + float(q)
        except (TypeError, ValueError, IndexError):
            continue
    return out


def _without(levels: dict, price: int | None, qty: float) -> dict:
    """The book less our own resting order (the snapshot was taken with it in)."""
    out = dict(levels)
    if price is not None and price in out:
        left = out[price] - qty
        if left > 1e-9:
            out[price] = left
        else:
            out.pop(price)
    return out


def rate_for(scoring, snap, program, side: str, price: int, qty: float) -> float:
    """Reward $/h for our bid on this snapshot — mirrors `runner.est_reward_per_hour` with
    the bid passed as `own_*` (removed from the levels, then scored as ours)."""
    reward, start, end, target, disc_bps = program
    if reward is None or start is None or end is None:
        return 0.0
    period = (end - start).total_seconds()
    if period <= 0:
        return 0.0
    yes = _levels(snap["yes"])
    no = _levels(snap["no"])
    if side == "yes":
        yes = _without(yes, price, qty)
    else:
        no = _without(no, price, qty)
    est = scoring.estimate(
        yes_levels=yes, no_levels=no, target_size=target, discount_factor_bps=disc_bps,
        our_yes_price=price if side == "yes" else None,
        our_yes_size=qty if side == "yes" else 0.0,
        our_no_price=price if side == "no" else None,
        our_no_size=qty if side == "no" else 0.0,
        period_reward_usd_value=reward, period_seconds=period)
    return float(est.reward_per_hour_usd or 0.0)


def fetch(cur, tag: str):
    cur.execute(
        """SELECT o.id, o.market_ticker, o.created_at, o.status, o.side, o.limit_price,
                  o.quantity, o.client_order_id,
                  (SELECT max(e.received_at) FROM execution_order_events e
                    WHERE e.kalshi_order_id = o.kalshi_order_id
                      AND e.status IN ('canceled', 'executed')),
                  (SELECT max(q.captured_at) FROM live_order_queue_ticks q
                    WHERE q.live_order_id = o.id)
           FROM live_orders o
           WHERE o.strategy = %s AND o.action = 'buy'
             AND o.status NOT IN ('rejected', 'not_landed', 'error', 'pending')""", (tag,))
    orders = [r for r in cur.fetchall() if not (r[7] or "").startswith(CLOSING_PREFIXES)]
    tickers = sorted({r[1] for r in orders})
    cur.execute(
        """SELECT market_ticker, first_seen_at, period_reward_usd, start_date, end_date,
                  target_size, discount_factor_bps
           FROM incentive_programs WHERE market_ticker = ANY(%s)
           ORDER BY market_ticker, first_seen_at""", (tickers,))
    programs: dict[str, list] = {}
    for t, seen, reward, start, end, target, disc in cur.fetchall():
        programs.setdefault(t, []).append(
            (_utc(seen), (None if reward is None else float(reward)), _utc(start), _utc(end),
             None if target is None else float(target), disc))
    cur.execute(
        """SELECT market_ticker, at, yes_levels_json, no_levels_json
           FROM incentive_market_snapshots
           WHERE market_ticker = ANY(%s) ORDER BY market_ticker, at""", (tickers,))
    snaps: dict[str, list] = {}
    for t, at, y, n in cur.fetchall():
        snaps.setdefault(t, []).append({"at": _utc(at), "yes": y, "no": n})
    return orders, programs, snaps


def program_at(rows: list, at: dt.datetime):
    """The programme terms in force at `at`: the newest row first seen at or before it whose
    period covers it, else the newest row whose period covers it."""
    cover = [r for r in rows if r[2] and r[3] and r[2] <= at <= r[3]]
    seen = [r for r in cover if r[0] and r[0] <= at]
    pick = (seen or cover or [None])[-1]
    return None if pick is None else pick[1:]


def summarise(orders, programs, snaps, payouts, scoring, now: dt.datetime) -> dict:
    est_by_market: dict[str, float] = {}
    rest_s: dict[str, float] = {}
    covered_s: dict[str, float] = {}
    for _id, t, created, status, side, price, qty, _coid, ended, last_tick in orders:
        a = _utc(created)
        b = _utc(ended) or _utc(last_tick) or now
        if status == "resting":
            b = now
        if b <= a or price is None or not qty:
            continue
        rest_s[t] = rest_s.get(t, 0.0) + (b - a).total_seconds()
        ss = [s for s in snaps.get(t, []) if a - dt.timedelta(seconds=MAX_GAP_SECONDS) <= s["at"] <= b]
        for i, s in enumerate(ss):
            seg_a = max(a, s["at"])
            nxt = ss[i + 1]["at"] if i + 1 < len(ss) else b
            seg_b = min(b, nxt, s["at"] + dt.timedelta(seconds=MAX_GAP_SECONDS))
            secs = (seg_b - seg_a).total_seconds()
            if secs <= 0:
                continue
            prog = program_at(programs.get(t, []), seg_a)
            if prog is None:
                continue
            r = rate_for(scoring, s, prog, side, int(price), float(qty))
            est_by_market[t] = est_by_market.get(t, 0.0) + r * secs / 3600.0
            covered_s[t] = covered_s.get(t, 0.0) + secs
    paid: dict[str, float] = {}
    for _d, cents, ev in payouts:
        paid[ev] = paid.get(ev, 0.0) + cents / 100.0
    events: dict[str, dict] = {}
    for t, secs in rest_s.items():
        e = events.setdefault(event_of(t), {"event": event_of(t), "markets": 0, "rest_h": 0.0,
                                            "covered_h": 0.0, "est": 0.0,
                                            "markets_est_under_1": 0})
        e["markets"] += 1
        e["rest_h"] += secs / 3600.0
        e["covered_h"] += covered_s.get(t, 0.0) / 3600.0
        e["est"] += est_by_market.get(t, 0.0)
        e["markets_est_under_1"] += int(est_by_market.get(t, 0.0) < 1.0)
    rows = []
    for e in events.values():
        e["paid"] = round(paid.get(e["event"], 0.0), 2)
        e["paid_recorded"] = e["event"] in paid
        e["ratio"] = round(e["paid"] / e["est"], 2) if e["est"] > 0.005 else None
        for k in ("rest_h", "covered_h", "est"):
            e[k] = round(e[k], 2)
        rows.append(e)
    rows.sort(key=lambda r: r["event"])

    def total(sel):
        est = sum(r["est"] for r in sel)
        pd = sum(r["paid"] for r in sel)
        cov = sum(r["covered_h"] for r in sel)
        rest = sum(r["rest_h"] for r in sel)
        return {"events": len(sel), "est": round(est, 2), "paid": round(pd, 2),
                "paid_over_est": round(pd / est, 2) if est > 0 else None,
                "coverage": round(cov / rest, 2) if rest else None}
    well = [r for r in rows if r["paid_recorded"] and r["rest_h"] and r["covered_h"] / r["rest_h"] >= 0.7]
    ratios = sorted(r["ratio"] for r in well if r["ratio"] is not None)
    return {
        "as_of": now.isoformat(timespec="minutes"),
        "paid_events": total([r for r in rows if r["paid_recorded"]]),
        "paid_events_well_covered": total(well),
        "median_ratio_well_covered": ratios[len(ratios) // 2] if ratios else None,
        "unpaid_events": total([r for r in rows if not r["paid_recorded"]]),
        "events": rows,
    }


def render(s: dict) -> str:
    out = [f"# LIMM estimate calibration (as of {s['as_of']})", "",
           f"{'event':34} {'mkts':>4} {'rest_h':>7} {'cov':>5} {'est$':>7} {'paid$':>7} {'paid/est':>8}"]
    for r in s["events"]:
        cov = r["covered_h"] / r["rest_h"] if r["rest_h"] else 0
        ratio = "-" if r["ratio"] is None else f"{r['ratio']:.2f}"
        paid = f"{r['paid']:.2f}" if r["paid_recorded"] else "-"
        out.append(f"{r['event'][:34]:34} {r['markets']:>4} {r['rest_h']:7.1f} {cov:5.0%} "
                   f"{r['est']:7.2f} {paid:>7} {ratio:>8}")
    out += ["", f"paid events:               {s['paid_events']}",
            f"paid, >=70% covered:       {s['paid_events_well_covered']}",
            f"median paid/est (covered): {s['median_ratio_well_covered']}",
            f"unpaid events:             {s['unpaid_events']}"]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="LIMM reward-estimate calibration.")
    ap.add_argument("--tag", default=LIVE_TAG)
    ap.add_argument("--json", action="store_true", help="also print the data as one JSON line")
    args = ap.parse_args(argv)
    url = (os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1
    import psycopg

    scoring = load_scoring()
    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            orders, programs, snaps = fetch(cur, args.tag)
    s = summarise(orders, programs, snaps, load_payouts(), scoring,
                  dt.datetime.now(dt.timezone.utc))
    print(render(s))
    if args.json:
        print(json.dumps(s, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
