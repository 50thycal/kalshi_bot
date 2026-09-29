"""FLOW-VETO probe — does pre-post YES-taker flow predict toxic mmsell fills?

Pre-registered in docs/MMSELL_FLOW_VETO_THESIS.md (2026-09-29). Frozen primary: W = 10 min.
Sensitivity windows are printed but never decide.

For every live NO buy of the tag since its twin epoch, read the market's PUBLIC trade tape in
[decision - W, decision) (Kalshi GET /markets/trades). Veto when YES-taker contracts exceed
NO-taker contracts and YES-taker contracts >= 1 — someone is buying YES, i.e. hitting the NO
bids we would join. Score each order on real money:

    realized = (settle_NO - L) * filled_qty     (0 when never filled)

and compare vetoed fills against kept fills.

No lookahead: the tape window ends strictly before the decision instant; fill timing and the
settlement result (GET /markets/{ticker}) only score. Provenance: our orders/fills come from the
bot DB, the tape and settlement from Kalshi public REST; the sources are never mixed in a series.

Read-only: DB via DATABASE_URL_RO in a read-only transaction, plus public Kalshi REST. Places no
orders and writes no tables.

Usage: {"type":"script","name":"mmsell_flow_veto_probe","args":["--tag","Fmmsell10"],"id":"veto-1"}
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import sys
import time
from collections import defaultdict

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=300000 "
    "-c idle_in_transaction_session_timeout=300000"
)

# Pre-registered constants (docs/MMSELL_FLOW_VETO_THESIS.md). Changing any is a new probe.
PRIMARY_W_MIN = 10
SENSITIVITY_W_MIN = (5, 30)
FAST_FILL_MIN = 10
V0_TAPE_COVERAGE = 0.90
V1_FLOOR = 40
V2_VETOED_MEAN = -1.0
V3_SEPARATION = 2.0
V4_MAX_VETO_RATE = 0.50
BOOT_N = 2000
BOOT_SEED = 20260929
REST_FILL_SHIFT_S = 300


def flow(trades: list[tuple[float, str, float]], t_end: float, window_s: float):
    """(yes_taker_contracts, no_taker_contracts) in [t_end - window_s, t_end)."""
    yes = no = 0.0
    for ts, side, count in trades:
        if t_end - window_s <= ts < t_end:
            if side == "yes":
                yes += count
            elif side == "no":
                no += count
    return yes, no


def veto(yes: float, no: float) -> bool:
    """The frozen rule: net YES-taker pressure, with at least one contract of it."""
    return yes >= 1 and yes > no


def bootstrap_diff_lb(a: list[float], b: list[float], n: int = BOOT_N, seed: int = BOOT_SEED,
                      q: float = 0.05) -> float | None:
    """q-quantile of the bootstrap distribution of mean(a) - mean(b)."""
    if not a or not b:
        return None
    rng = random.Random(seed)
    la, lb = len(a), len(b)
    diffs = sorted(sum(a[rng.randrange(la)] for _ in range(la)) / la
                   - sum(b[rng.randrange(lb)] for _ in range(lb)) / lb for _ in range(n))
    return diffs[max(0, min(n - 1, int(q * n)))]


def verdict(tape_cov: float, n_vf: int, vetoed_mean: float | None, sep: float | None,
            lb: float | None, veto_rate: float) -> tuple[str, list[str]]:
    """The pre-registered decision rule, verbatim."""
    if tape_cov < V0_TAPE_COVERAGE:
        return "HOLD (instrument)", [f"V0 tape coverage {tape_cov:.1%} < {V0_TAPE_COVERAGE:.0%}"]
    if n_vf < V1_FLOOR:
        return "HOLD", [f"V1 vetoed filled n={n_vf} < {V1_FLOOR}"]
    kills = []
    if sep is not None and sep <= 0:
        kills.append(f"V3 KILL: kept - vetoed {sep:+.2f}c <= 0")
    if veto_rate > V4_MAX_VETO_RATE and (sep is None or sep <= V3_SEPARATION):
        kills.append(f"V4 KILL: veto rate {veto_rate:.1%} > {V4_MAX_VETO_RATE:.0%} with "
                     f"separation {sep:+.2f}c <= +{V3_SEPARATION}")
    if kills:
        return "KILL", kills
    v2 = vetoed_mean is not None and vetoed_mean <= V2_VETOED_MEAN
    v3 = sep is not None and sep >= V3_SEPARATION and lb is not None and lb > 0
    v4 = veto_rate <= V4_MAX_VETO_RATE
    why = [f"V2 vetoed-fill mean {vetoed_mean:+.2f}c (bar <= {V2_VETOED_MEAN}) -> "
           f"{'PASS' if v2 else 'fail'}",
           f"V3 separation {sep:+.2f}c (bar +{V3_SEPARATION}), boot 5th pct {lb:+.2f}c "
           f"(bar > 0) -> {'PASS' if v3 else 'fail'}",
           f"V4 veto rate {veto_rate:.1%} (bar <= {V4_MAX_VETO_RATE:.0%}) -> "
           f"{'PASS' if v4 else 'fail'}"]
    return ("PROMOTE" if (v2 and v3 and v4) else "HOLD"), why


# ---------------------------------------------------------------------------------------------
# data


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def _aware(x):
    if x is None:
        return None
    if isinstance(x, str):
        try:
            x = dt.datetime.fromisoformat(x.replace("Z", "+00:00"))
        except ValueError:
            return None
    return x if x.tzinfo else x.replace(tzinfo=dt.timezone.utc)


ORDERS_SQL = """
select o.id, o.kalshi_order_id, o.market_ticker, o.created_at, o.limit_price, c.decided_at
  from live_orders o
  left join execution_order_context c on c.live_order_id = o.id
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


def fill_times(ws_rows, rest_rows):
    """koid -> (first fill instant, filled qty); WS exchange ms, else REST created_time, else
    the reconcile stamp shifted early."""
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


def parse_trade(t: dict):
    """(unix ts, taker_side, contracts) from a public trade, tolerant of field variants."""
    ts = _aware(t.get("created_time"))
    side = (t.get("taker_side") or "").lower()
    cnt = t.get("count_fp")
    if cnt is None:
        cnt = t.get("count")
    try:
        cnt = float(cnt)
    except (TypeError, ValueError):
        cnt = 0.0
    if ts is None or side not in ("yes", "no"):
        return None
    return ts.timestamp(), side, cnt


def fetch_tape(ticker: str, t0: int, t1: int):
    """Public trades in [t0, t1] (unix s), paginated. None when the API failed."""
    import xvenue_leadlag as xl  # browser UA + retries; deferred so tests need no network
    out, cursor = [], ""
    for _ in range(20):
        page = xl._get(f"{KALSHI}/markets/trades?ticker={ticker}&min_ts={t0}&max_ts={t1}"
                       f"&limit=1000&cursor={cursor}")
        if page is None:
            return None
        for t in page.get("trades") or []:
            p = parse_trade(t)
            if p:
                out.append(p)
        cursor = page.get("cursor") or ""
        if not cursor or not page.get("trades"):
            break
        time.sleep(0.05)
    return out


def settle_no(ticker: str, cache: dict):
    if ticker in cache:
        return cache[ticker]
    import xvenue_leadlag as xl
    data = xl._get(f"{KALSHI}/markets/{ticker}")
    res = ((data or {}).get("market") or {}).get("result")
    cache[ticker] = 100 if res == "no" else 0 if res == "yes" else None
    time.sleep(0.05)
    return cache[ticker]


# ---------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tag", default="Fmmsell10")
    ap.add_argument("--since", default="2026-09-07T02:03:36+00:00",
                    help="default: the Fmmsell10 twin epoch start")
    ap.add_argument("--max-orders", type=int, default=3000, help="bounds API cost")
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
        orders = cur.fetchall()[: args.max_orders]
        koids = [r[1] for r in orders]
        cur.execute(WS_FILLS_SQL, (koids,))
        ws = cur.fetchall()
        cur.execute(REST_FILLS_SQL, (koids,))
        rest = cur.fetchall()
    fills = fill_times(ws, rest)

    print(f"FLOW-VETO probe — tag {args.tag}, orders since {since.isoformat()}")
    print("(read-only; pre-registered in docs/MMSELL_FLOW_VETO_THESIS.md)\n")

    max_w = max((PRIMARY_W_MIN,) + SENSITIVITY_W_MIN) * 60
    cache: dict = {}
    rows = []
    tape_ok = unsettled = 0
    for _oid, koid, ticker, created, limit, decided in orders:
        t = _aware(decided) or _aware(created)
        tape = fetch_tape(ticker, int(t.timestamp()) - max_w, int(t.timestamp()))
        if tape is None:
            continue
        tape_ok += 1
        s = settle_no(ticker, cache)
        if s is None:
            unsettled += 1
            continue
        fill = fills.get(koid)
        filled = bool(fill and fill[1] > 0)
        realized = (s - int(limit)) * min(fill[1], 1.0) if filled else 0.0
        age = (fill[0] - t).total_seconds() / 60 if filled else None
        rows.append({"ticker": ticker, "series": ticker.split("-")[0], "filled": filled,
                     "realized": realized, "fast": filled and age is not None
                     and age < FAST_FILL_MIN, "flows": {
                         w: flow(tape, t.timestamp(), w * 60)
                         for w in (PRIMARY_W_MIN,) + SENSITIVITY_W_MIN}})
        time.sleep(0.05)

    tape_cov = tape_ok / len(orders) if orders else 0.0
    print("== V0 instrument ==")
    print(f"  orders {len(orders)}  tape fetched {tape_ok} ({tape_cov:.1%})  "
          f"settled+scored {len(rows)}  unsettled {unsettled}")
    print(f"  filled {sum(r['filled'] for r in rows)}  live realized "
          f"{sum(r['realized'] for r in rows) / 100:+.2f}$")

    def evaluate(w: int, label: str):
        vet = [r for r in rows if veto(*r["flows"][w])]
        kept = [r for r in rows if not veto(*r["flows"][w])]
        vf = [r["realized"] for r in vet if r["filled"]]
        kf = [r["realized"] for r in kept if r["filled"]]
        rate = len(vet) / len(rows) if rows else 0.0
        vm = sum(vf) / len(vf) if vf else None
        km = sum(kf) / len(kf) if kf else None
        sep = (km - vm) if (vm is not None and km is not None) else None
        lb = bootstrap_diff_lb(kf, vf)
        fast_v = (sum(1 for r in vet if r["fast"]) / len(vf)) if vf else None
        fast_k = (sum(1 for r in kept if r["fast"]) / len(kf)) if kf else None
        print(f"\n== {label} (W={w} min) ==")
        print(f"  vetoed {len(vet)} of {len(rows)} orders ({rate:.1%}); vetoed fills {len(vf)}, "
              f"kept fills {len(kf)}")
        if vm is not None and km is not None:
            print(f"  mean realized per fill: vetoed {vm:+.2f}c  kept {km:+.2f}c  "
                  f"separation {sep:+.2f}c  boot 5th pct {lb:+.2f}c")
            print(f"  veto's net effect on real money: {-sum(vf) / 100:+.2f}$")
            print(f"  fast-fill (<{FAST_FILL_MIN}m) share of fills: vetoed {fast_v:.1%}  "
                  f"kept {fast_k:.1%}   (mechanism check: vetoed should be higher)")
        return len(vf), vm, sep, lb, rate, vet

    n_vf, vm, sep, lb, rate, vet = evaluate(PRIMARY_W_MIN, "PRIMARY")
    ser = defaultdict(list)
    for r in vet:
        if r["filled"]:
            ser[r["series"]].append(r["realized"])
    print("  vetoed fills by series (n>=5):")
    for k, v in sorted(ser.items(), key=lambda kv: -len(kv[1])):
        if len(v) >= 5:
            print(f"    {k:24s} n={len(v):4d}  mean {sum(v) / len(v):+.2f}c")

    print("\n-- sensitivity (reported, never decides) --")
    for w in SENSITIVITY_W_MIN:
        evaluate(w, "sensitivity")

    v, why = verdict(tape_cov, n_vf, vm, sep, lb, rate)
    print("\n== verdict ==")
    print(f"  {v}")
    for w_ in why:
        print(f"   - {w_}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
