"""RUNAWAY-CHASE probe — would chasing leapfrogged mmsell orders beat resting them?

Pre-registered in docs/MMSELL_RUNAWAY_CHASE_THESIS.md (2026-09-29). Frozen primary arm: K=2c,
persistence 15 s. Sensitivity rows are printed but never decide.

For every live NO buy of the tag since the WS-019 collector went live, walk its queue ticks
(`live_order_queue_ticks.features_json`, derived from the reconstructed WS book) BEFORE its
first fill and before its cancel. The trigger fires at the first tick where

    best_yes_ask < p   (a better NO bid exists: we have been passed)
    p - K <= best_yes_bid < p   (the NO ask, 100 - best_yes_bid, is within K of our limit)

has held on every consecutive usable tick for >= persistence seconds (p = our yes-convention
price = 100 - limit NO price). Counterfactual per triggered order, one contract:

    policy = settle_NO - C - taker_fee(C)     C = 100 - best_yes_bid at the trigger tick
    actual = (settle_NO - L) * filled_qty      includes any passive fill AFTER the trigger
    delta  = policy - actual

No lookahead: the trigger reads only ticks captured before the decision instant; the settlement
result (Kalshi public GET /markets/{ticker}) scores, it never prices.

Read-only: DB via DATABASE_URL_RO in a read-only transaction, plus public Kalshi REST. Places no
orders and writes no tables.

Usage: {"type":"script","name":"mmsell_chase_probe","args":["--tag","Fmmsell10"],"id":"chase-1"}
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import random
import sys
import time
from collections import defaultdict
from dataclasses import dataclass

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=300000 "
    "-c idle_in_transaction_session_timeout=300000"
)

# Pre-registered constants (docs/MMSELL_RUNAWAY_CHASE_THESIS.md). Changing any is a new probe.
PRIMARY_K = 2
PRIMARY_PERSIST_S = 15
SENSITIVITY = [(1, 15), (3, 15), (2, 0), (2, 60)]
C0_COVERAGE = 0.70
C1_FLOOR = 40
C2_MEAN_DELTA = 2.0
BOOT_N = 2000
BOOT_SEED = 20260929
REST_FILL_SHIFT_S = 300


def taker_fee_cents(price_cents: float, qty: int = 1) -> int:
    """Kalshi taker fee in cents: ceil(0.07 * qty * P * (1-P) * 100), P in dollars."""
    p = min(max(price_cents / 100.0, 0.0), 1.0)
    return math.ceil(round(0.07 * qty * p * (1.0 - p) * 100, 9))


@dataclass
class Tick:
    at: dt.datetime
    bid: int | None       # best_yes_bid
    ask: int | None       # best_yes_ask (= best NO bid, seen from the YES side)
    our: int | None       # our yes-convention price
    valid: bool
    usable: bool          # passes the pre-fill / non-terminal / remaining filters


def find_trigger(ticks: list[Tick], p: int, k: int, persist_s: float):
    """First (tick, chase_cost_NO) where the condition held on consecutive usable ticks for
    >= persist_s. Ticks must be time-ordered and already cut at the fill/cancel instant.
    An unusable tick (invalid book, missing prices) breaks the run."""
    run_start = None
    for t in ticks:
        ok = (t.usable and t.valid and t.bid is not None and t.ask is not None
              and t.ask < p and p - k <= t.bid < p)
        if not ok:
            run_start = None
            continue
        if run_start is None:
            run_start = t.at
        if (t.at - run_start).total_seconds() >= persist_s:
            return t, 100 - t.bid
    return None


def bootstrap_lb(values: list[float], n: int = BOOT_N, seed: int = BOOT_SEED,
                 q: float = 0.05) -> float | None:
    """q-quantile of the bootstrap distribution of the mean."""
    if not values:
        return None
    rng = random.Random(seed)
    m = len(values)
    means = sorted(sum(values[rng.randrange(m)] for _ in range(m)) / m for _ in range(n))
    return means[max(0, min(n - 1, int(q * n)))]


def verdict(coverage: float, n: int, mean_delta: float | None, lb: float | None,
            mean_policy: float | None) -> tuple[str, list[str]]:
    """The pre-registered decision rule, verbatim."""
    why = []
    if coverage < C0_COVERAGE:
        return "HOLD (instrument)", [f"C0 coverage {coverage:.1%} < {C0_COVERAGE:.0%}"]
    if n < C1_FLOOR:
        return "HOLD", [f"C1 triggered+settled n={n} < {C1_FLOOR}"]
    if mean_delta is not None and mean_delta <= 0:
        why.append(f"C2 KILL: mean delta {mean_delta:+.2f}c <= 0")
    if mean_policy is not None and mean_policy <= 0:
        why.append(f"C3 KILL: mean policy {mean_policy:+.2f}c <= 0")
    if why:
        return "KILL", why
    c2 = mean_delta is not None and mean_delta >= C2_MEAN_DELTA and lb is not None and lb > 0
    c3 = mean_policy is not None and mean_policy > 0
    why = [f"C2 mean delta {mean_delta:+.2f}c (bar +{C2_MEAN_DELTA}), boot 5th pct "
           f"{lb:+.2f}c (bar > 0) -> {'PASS' if c2 else 'fail'}",
           f"C3 mean policy {mean_policy:+.2f}c -> {'PASS' if c3 else 'fail'}"]
    return ("PROMOTE" if (c2 and c3) else "HOLD"), why


# ---------------------------------------------------------------------------------------------
# data


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


ORDERS_SQL = """
select o.id, o.kalshi_order_id, o.market_ticker, o.created_at, o.limit_price
  from live_orders o
 where o.strategy = %s and o.action = 'buy' and o.side = 'no'
   and o.kalshi_order_id is not null and o.limit_price is not null
   and o.created_at >= %s
 order by o.created_at
"""

WS_FILLS_SQL = """
select kalshi_order_id, min(ts_ms), sum(coalesce(count_fp, 0))
  from execution_fill_events
 where kalshi_order_id = any(%s)
 group by kalshi_order_id
"""

REST_FILLS_SQL = """
select kalshi_order_id, min(raw_fill_json->>'created_time'), min(filled_at),
       sum(coalesce(quantity, 0))
  from fills
 where kalshi_order_id = any(%s)
 group by kalshi_order_id
"""

CANCELS_SQL = """
select kalshi_order_id, min(received_at)
  from execution_order_events
 where kalshi_order_id = any(%s) and status in ('canceled', 'cancelled')
 group by kalshi_order_id
"""

TICKS_SQL = """
select live_order_id, captured_at, trigger, remaining_count,
       features_json->>'book_valid', features_json->>'best_yes_bid',
       features_json->>'best_yes_ask', features_json->>'our_price'
  from live_order_queue_ticks
 where live_order_id = any(%s) and features_json is not null
 order by live_order_id, captured_at
"""


def _aware(x):
    if x is None:
        return None
    if isinstance(x, str):
        try:
            x = dt.datetime.fromisoformat(x.replace("Z", "+00:00"))
        except ValueError:
            return None
    return x if x.tzinfo else x.replace(tzinfo=dt.timezone.utc)


def _int(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def fill_times(ws_rows, rest_rows):
    """koid -> (first fill instant, filled qty). WS exchange ms first; else REST created_time;
    else the reconcile stamp shifted early (a late fill time would let a trigger fire after we
    had already filled)."""
    out = {}
    for koid, ts_ms, qty in ws_rows:
        if ts_ms:
            out[koid] = (dt.datetime.fromtimestamp(int(ts_ms) / 1000, dt.timezone.utc),
                         float(qty or 0))
    for koid, created, filled_at, qty in rest_rows:
        if koid in out:
            continue
        at = _aware(created)
        if at is None and filled_at is not None:
            at = _aware(filled_at) - dt.timedelta(seconds=REST_FILL_SHIFT_S)
        if at is not None:
            out[koid] = (at, float(qty or 0))
    return out


def settle_no(ticker: str, cache: dict):
    """100 if the market settled NO, 0 if YES, None if unsettled/unreadable."""
    if ticker in cache:
        return cache[ticker]
    import xvenue_leadlag as xl  # browser UA + retries; deferred so tests need no network
    data = xl._get(f"{KALSHI}/markets/{ticker}")
    res = ((data or {}).get("market") or {}).get("result")
    cache[ticker] = 100 if res == "no" else 0 if res == "yes" else None
    time.sleep(0.05)
    return cache[ticker]


# ---------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tag", default="Fmmsell10")
    ap.add_argument("--since", default="2026-09-16T00:00:00+00:00",
                    help="WS-019 collector start; ticks before it carry no features")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1
    since = _aware(args.since)

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn, \
            conn.cursor() as cur:
        cur.execute(ORDERS_SQL, (args.tag, since))
        orders = cur.fetchall()
        koids = [r[1] for r in orders]
        ids = [r[0] for r in orders]
        cur.execute(WS_FILLS_SQL, (koids,))
        ws = cur.fetchall()
        cur.execute(REST_FILLS_SQL, (koids,))
        rest = cur.fetchall()
        cur.execute(CANCELS_SQL, (koids,))
        cancels = {k: _aware(t) for k, t in cur.fetchall()}
        cur.execute(TICKS_SQL, (ids,))
        raw_ticks = cur.fetchall()

    fills = fill_times(ws, rest)
    by_order = defaultdict(list)
    for oid, at, trig, remaining, valid, bid, ask, our in raw_ticks:
        by_order[oid].append((_aware(at), trig, remaining, valid, _int(bid), _int(ask), _int(our)))

    print(f"RUNAWAY-CHASE probe — tag {args.tag}, orders since {since.isoformat()}")
    print("(read-only; pre-registered in docs/MMSELL_RUNAWAY_CHASE_THESIS.md)\n")

    cache: dict = {}
    covered = 0
    per_order = []   # (order row, cut ticks)
    for oid, koid, ticker, created, limit in orders:
        fill = fills.get(koid)
        cut = min([x for x in (fill[0] if fill and fill[1] > 0 else None, cancels.get(koid))
                   if x is not None], default=None)
        ticks = []
        for at, trig, remaining, valid, bid, ask, our in by_order.get(oid, []):
            usable = (trig != "terminal" and (cut is None or at < cut)
                      and (remaining is None or float(remaining) > 0))
            ticks.append(Tick(at, bid, ask, our, str(valid).lower() == "true", usable))
        if any(t.usable and t.valid and t.bid is not None and t.ask is not None for t in ticks):
            covered += 1
        per_order.append(((oid, koid, ticker, _aware(created), int(limit)), ticks, fill))

    coverage = covered / len(orders) if orders else 0.0
    print("== C0 instrument ==")
    print(f"  orders {len(orders)}  with >=1 usable valid-book tick {covered} ({coverage:.1%})"
          f"  ticks read {len(raw_ticks)}")
    filled_n = sum(1 for _o, _t, f in per_order if f and f[1] > 0)
    print(f"  filled {filled_n}  unfilled {len(orders) - filled_n}")

    def run(k: int, persist: float):
        rows = []
        unsettled = 0
        for (_oid, _koid, ticker, created, limit), ticks, fill in per_order:
            p = next((t.our for t in ticks if t.our is not None), None) or (100 - limit)
            hit = find_trigger(ticks, p, k, persist)
            if hit is None:
                continue
            tick, cost = hit
            s = settle_no(ticker, cache)
            if s is None:
                unsettled += 1
                continue
            qty = min(fill[1], 1.0) if fill and fill[1] > 0 else 0.0
            later_fill = bool(fill and fill[1] > 0 and fill[0] > tick.at)
            actual = (s - limit) * qty
            policy = s - cost - taker_fee_cents(cost)
            rows.append({"ticker": ticker, "series": ticker.split("-")[0], "L": limit,
                         "C": cost, "settle": s, "policy": policy, "actual": actual,
                         "delta": policy - actual, "later_fill": later_fill,
                         "age_min": (tick.at - created).total_seconds() / 60})
        return rows, unsettled

    def summarize(rows):
        n = len(rows)
        if not n:
            return None
        md = sum(r["delta"] for r in rows) / n
        mp = sum(r["policy"] for r in rows) / n
        return {"n": n, "mean_delta": md, "mean_policy": mp,
                "lb": bootstrap_lb([r["delta"] for r in rows]),
                "sum_delta_usd": sum(r["delta"] for r in rows) / 100,
                "win": sum(1 for r in rows if r["settle"] == 100) / n,
                "cost_gap": sum(r["C"] - r["L"] for r in rows) / n,
                "later_fill": sum(1 for r in rows if r["later_fill"]) / n}

    primary, unsettled = run(PRIMARY_K, PRIMARY_PERSIST_S)
    s = summarize(primary)
    print(f"\n== PRIMARY (K={PRIMARY_K}c, persistence {PRIMARY_PERSIST_S}s) ==")
    print(f"  triggered + settled n={len(primary)}   triggered but unsettled {unsettled}")
    if s:
        print(f"  mean delta {s['mean_delta']:+.2f}c  boot 5th pct {s['lb']:+.2f}c  "
              f"total {s['sum_delta_usd']:+.2f}$")
        print(f"  mean policy {s['mean_policy']:+.2f}c  chase-set win {s['win']:.1%}  "
              f"mean (C - L) {s['cost_gap']:+.2f}c")
        print(f"  share that later filled passively anyway {s['later_fill']:.1%}")
        for label, sub in (("would have filled anyway", [r for r in primary if r["later_fill"]]),
                           ("never filled (the target)",
                            [r for r in primary if not r["later_fill"]])):
            if sub:
                print(f"    {label:28s} n={len(sub):4d}  mean delta "
                      f"{sum(r['delta'] for r in sub) / len(sub):+.2f}c  win "
                      f"{sum(1 for r in sub if r['settle'] == 100) / len(sub):.1%}")
        ser = defaultdict(list)
        for r in primary:
            ser[r["series"]].append(r["delta"])
        print("  by series (n>=5):")
        for k_, v in sorted(ser.items(), key=lambda kv: -len(kv[1])):
            if len(v) >= 5:
                print(f"    {k_:24s} n={len(v):4d}  mean delta {sum(v) / len(v):+.2f}c")

    print("\n== sensitivity (reported, never decides) ==")
    for k, persist in SENSITIVITY:
        rows, _u = run(k, persist)
        ss = summarize(rows)
        if ss:
            print(f"  K={k} persist={persist:>2}s  n={ss['n']:4d}  mean delta "
                  f"{ss['mean_delta']:+.2f}c  lb {ss['lb']:+.2f}c  policy "
                  f"{ss['mean_policy']:+.2f}c  later-fill {ss['later_fill']:.0%}")
        else:
            print(f"  K={k} persist={persist:>2}s  n=0")

    v, why = verdict(coverage, len(primary), s["mean_delta"] if s else None,
                     s["lb"] if s else None, s["mean_policy"] if s else None)
    print("\n== verdict ==")
    print(f"  {v}")
    for w in why:
        print(f"   - {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
