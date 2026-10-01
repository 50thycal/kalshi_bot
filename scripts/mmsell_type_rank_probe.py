"""TYPE-RANK probe — do the market types that earned most on real fills keep earning most?

Pre-registered in docs/MMSELL_TYPE_RANK_THESIS.md (2026-10-01). Cells = market_type from
`mmsell_market_types.classify(series)`; `unclassified` excluded. A cell is READABLE in a ranking
window at >= 100 settled live fills. TOP = the two readable cells with the highest realized
c/fill in the ranking window (ties: more fills first); REST = every other readable cell.

Leg R (retrospective, can only KILL): rank on window A (pre-Fmmsell10 live books, 07-26 ->
09-07 epoch start), score on window B (Fmmsell10, epoch start -> 2026-10-01T00:00Z).
Leg F (forward, decides promotion): rank on A u B, score on Fmmsell10 orders decided from
2026-10-01T00:00Z.

Outcome per filled order (real money): (settle_NO - limit) * min(filled, 1).

T0 is read as two separate rates, each >= 95% of the window's filled orders: share that classify
to a known type, and share whose market has a settled result. (Interpretation written before the
first run.)

Bootstrap: settlement-date blocks (the market's close date), 10,000 resamples, seed 20261001.

Read-only: DB via DATABASE_URL_RO in a read-only transaction, plus public Kalshi REST for
settlement. Places no orders and writes no tables.

Usage: {"type":"script","name":"mmsell_type_rank_probe","id":"typerank-1"}
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import sys
import time
from collections import Counter, defaultdict

from mmsell_flow_veto_probe import (
    KALSHI,
    REST_FILLS_SQL,
    RO_OPTIONS,
    WS_FILLS_SQL,
    _aware,
    _to_libpq_url,
    fill_times,
)
from mmsell_market_types import UNCLASSIFIED, classify
from mmsell_thin_market_probe import CONFIRM_TAG, ORDERS_SQL, PRIMARY_TAGS, SPLIT_AT

assert UNCLASSIFIED[0] == "unclassified"  # rank_cells and coverage key on this literal

# Pre-registered constants (docs/MMSELL_TYPE_RANK_THESIS.md). Changing any is a new probe.
A_SINCE = "2026-07-26T00:00:00+00:00"
FORWARD_AT = "2026-10-01T00:00:00+00:00"
READABLE = 100
TOP_N = 2
T0_COVERAGE = 0.95
T1_CELLS = 4
TOP_FLOOR = 150
REST_FLOOR = 100
T4_SEPARATION = 1.5
T4_TOP_MEAN = 1.0
BOOT_N = 10_000
BOOT_SEED = 20261001


def series_of(ticker: str) -> str:
    return ticker.split("-")[0]


def type_of(ticker: str) -> str:
    return classify(series_of(ticker))[0]


def rank_cells(fills: list[dict]) -> list[tuple[str, int, float]]:
    """[(type, n, mean c/fill)] over READABLE cells, best first; ties -> more fills first."""
    by = defaultdict(list)
    for f in fills:
        if f["type"] != "unclassified" and f["realized"] is not None:
            by[f["type"]].append(f["realized"])
    cells = [(t, len(v), sum(v) / len(v)) for t, v in by.items() if len(v) >= READABLE]
    return sorted(cells, key=lambda c: (-c[2], -c[1]))


def split_top_rest(ranked: list[tuple[str, int, float]]) -> tuple[set[str], set[str]]:
    top = {t for t, _, _ in ranked[:TOP_N]}
    rest = {t for t, _, _ in ranked[TOP_N:]}
    return top, rest


def score(fills: list[dict], top: set[str], rest: set[str]):
    """(n_top, mean_top, n_rest, mean_rest, separation) over settled fills."""
    t = [f["realized"] for f in fills if f["realized"] is not None and f["type"] in top]
    r = [f["realized"] for f in fills if f["realized"] is not None and f["type"] in rest]
    mt = sum(t) / len(t) if t else None
    mr = sum(r) / len(r) if r else None
    sep = (mt - mr) if (mt is not None and mr is not None) else None
    return len(t), mt, len(r), mr, sep


def date_block_lb(fills: list[dict], top: set[str], rest: set[str], n: int = BOOT_N,
                  seed: int = BOOT_SEED, q: float = 0.05) -> float | None:
    """q-quantile of mean(TOP) - mean(REST) under a bootstrap that resamples whole settle dates."""
    blocks = defaultdict(lambda: [0.0, 0, 0.0, 0])
    for f in fills:
        if f["realized"] is None or f["type"] not in top | rest:
            continue
        b = blocks[f["day"]]
        if f["type"] in top:
            b[0] += f["realized"]
            b[1] += 1
        else:
            b[2] += f["realized"]
            b[3] += 1
    days = list(blocks.values())
    if not days:
        return None
    rng = random.Random(seed)
    nd = len(days)
    diffs = []
    for _ in range(n):
        st = nt = sr = nr = 0.0
        for _ in range(nd):
            b = days[rng.randrange(nd)]
            st += b[0]
            nt += b[1]
            sr += b[2]
            nr += b[3]
        if nt and nr:
            diffs.append(st / nt - sr / nr)
    if not diffs:
        return None
    diffs.sort()
    return diffs[max(0, min(len(diffs) - 1, int(q * len(diffs))))]


def _f(x) -> str:
    return "n/a" if x is None else f"{x:+.2f}c"


def verdict(cov: dict, cells_a: int, cells_ab: int, r: tuple, f: tuple, f_lb: float | None):
    """The pre-registered decision rule, verbatim. r/f = score() tuples. Returns (verdict, why)."""
    for name, (c_type, c_settle) in cov.items():
        if c_type < T0_COVERAGE or c_settle < T0_COVERAGE:
            return "HOLD (instrument)", [f"T0 {name}: classify {c_type:.1%}, settle-map "
                                         f"{c_settle:.1%} (bar {T0_COVERAGE:.0%} each)"]
    if cells_a < T1_CELLS or cells_ab < T1_CELLS:
        return "HOLD (instrument)", [f"T1 readable cells A={cells_a}, A+B={cells_ab} "
                                     f"(bar {T1_CELLS})"]
    why = []
    rn_t, _rmt, rn_r, _rmr, r_sep = r
    r_floor = rn_t >= TOP_FLOOR and rn_r >= REST_FLOOR
    if r_floor and r_sep is not None and r_sep <= 0:
        return "KILL", [f"T2 KILL: retrospective separation {_f(r_sep)} <= 0 "
                        f"(TOP n={rn_t}, REST n={rn_r})"]
    if r_floor:
        why.append(f"T2 retrospective separation {_f(r_sep)} > 0 -> not killed")
    else:
        why.append(f"T2 retrospective floors not met (TOP n={rn_t} / {TOP_FLOOR}, REST n={rn_r} "
                   f"/ {REST_FLOOR}) -> leg R not decisive")
    fn_t, f_mt, fn_r, _fmr, f_sep = f
    if fn_t < TOP_FLOOR or fn_r < REST_FLOOR:
        why.append(f"T3 forward floors TOP n={fn_t} / {TOP_FLOOR}, REST n={fn_r} / {REST_FLOOR} "
                   f"-> HOLD (accrual); re-run weekly")
        return "HOLD (accrual)", why
    if f_sep is not None and f_sep <= 0:
        return "KILL", why + [f"T4 KILL: forward separation {_f(f_sep)} <= 0"]
    t4 = (f_sep is not None and f_sep >= T4_SEPARATION and f_lb is not None and f_lb > 0
          and f_mt is not None and f_mt >= T4_TOP_MEAN)
    why.append(f"T4 forward separation {_f(f_sep)} (bar +{T4_SEPARATION}), date-block p5 "
               f"{_f(f_lb)}, TOP {_f(f_mt)} (bar +{T4_TOP_MEAN}) -> "
               f"{'PASS' if t4 else 'HOLD (underpowered)'}")
    return ("PROMOTE" if t4 else "HOLD (underpowered)"), why


def settle_info(ticker: str, cache: dict):
    """(settle_NO in {0,100} or None, close date 'YYYY-MM-DD' or None)."""
    if ticker in cache:
        return cache[ticker]
    import xvenue_leadlag as xl  # browser UA + retries; deferred so tests need no network
    m = ((xl._get(f"{KALSHI}/markets/{ticker}") or {}).get("market") or {})
    res = m.get("result")
    s = 100 if res == "no" else 0 if res == "yes" else None
    day = None
    for k in ("settlement_ts", "close_time", "expiration_time"):
        d = _aware(m.get(k)) if isinstance(m.get(k), str) else None
        if d is not None:
            day = d.date().isoformat()
            break
    cache[ticker] = (s, day)
    time.sleep(0.05)
    return cache[ticker]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--confirm-tag", default=CONFIRM_TAG)
    ap.add_argument("--until", default="2100-01-01T00:00:00+00:00")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1
    split, fwd, until = _aware(SPLIT_AT), _aware(FORWARD_AT), _aware(args.until)

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn, \
            conn.cursor() as cur:
        cur.execute(ORDERS_SQL, (list(PRIMARY_TAGS), _aware(A_SINCE), split))
        rows_a = cur.fetchall()
        cur.execute(ORDERS_SQL, ([args.confirm_tag], split, fwd))
        rows_b = cur.fetchall()
        cur.execute(ORDERS_SQL, ([args.confirm_tag], fwd, until))
        rows_f = cur.fetchall()
        koids = [r[1] for r in rows_a + rows_b + rows_f]
        cur.execute(WS_FILLS_SQL, (koids,))
        ws = cur.fetchall()
        cur.execute(REST_FILLS_SQL, (koids,))
        rest = cur.fetchall()
    fills_by = fill_times(ws, rest)

    print("TYPE-RANK probe — read-only; pre-registered in docs/MMSELL_TYPE_RANK_THESIS.md")
    print(f"window A {','.join(PRIMARY_TAGS)} {A_SINCE} -> {SPLIT_AT}: {len(rows_a)} orders")
    print(f"window B {args.confirm_tag} {SPLIT_AT} -> {FORWARD_AT}: {len(rows_b)} orders")
    print(f"forward  {args.confirm_tag} from {FORWARD_AT}: {len(rows_f)} orders\n")

    cache: dict = {}

    def build(rows):
        out = []
        for _oid, koid, ticker, created, limit, _decided, _tag in rows:
            fill = fills_by.get(koid)
            if not (fill and fill[1] > 0):
                continue
            s, day = settle_info(ticker, cache)
            realized = None if s is None else (s - int(limit)) * min(fill[1], 1.0)
            out.append({"ticker": ticker, "series": series_of(ticker), "type": type_of(ticker),
                        "realized": realized,
                        "day": day or (_aware(created).date().isoformat())})
        return out

    fa, fb, ff = build(rows_a), build(rows_b), build(rows_f)

    def coverage(fills):
        if not fills:
            return (1.0, 1.0)
        return (sum(f["type"] != "unclassified" for f in fills) / len(fills),
                sum(f["realized"] is not None for f in fills) / len(fills))

    cov = {"A": coverage(fa), "B": coverage(fb)}
    print("== T0 instrument ==")
    for name, fills in (("A", fa), ("B", fb), ("forward", ff)):
        c = coverage(fills)
        print(f"  {name:8s} filled orders {len(fills):5d}  classify {c[0]:6.1%}  "
              f"settle-map {c[1]:6.1%}")

    def table(fills, label):
        by = defaultdict(list)
        mix = defaultdict(Counter)
        for f in fills:
            if f["realized"] is not None:
                by[f["type"]].append(f["realized"])
                mix[f["type"]][f["series"]] += 1
        print(f"\n== {label} — per type (settled fills) ==")
        for t, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
            wins = sum(1 for x in v if x > 0)
            top3 = ", ".join(f"{s} {n}" for s, n in mix[t].most_common(3))
            print(f"  {t:14s} n {len(v):5d}  win {wins / len(v):6.1%}  "
                  f"c/fill {sum(v) / len(v):+7.2f}  total {sum(v) / 100:+7.2f}$   [{top3}]")

    table(fa, "window A")
    table(fb, "window B")
    if ff:
        table(ff, "forward (from 10-01)")

    ranked_a = rank_cells(fa)
    ranked_ab = rank_cells(fa + fb)
    print("\n== T1 rankings (readable cells, best first) ==")
    print("  A:   " + "  ".join(f"{t} {m:+.2f}c (n={n})" for t, n, m in ranked_a))
    print("  A+B: " + "  ".join(f"{t} {m:+.2f}c (n={n})" for t, n, m in ranked_ab))

    top_a, rest_a = split_top_rest(ranked_a)
    r = score(fb, top_a, rest_a)
    r_lb = date_block_lb(fb, top_a, rest_a)
    print(f"\n== LEG R — rank on A, score on B ==\n  TOP {sorted(top_a)}  REST {sorted(rest_a)}")
    print(f"  TOP n={r[0]} {_f(r[1])}   REST n={r[2]} {_f(r[3])}   separation {_f(r[4])}   "
          f"date-block p5 {_f(r_lb)}")

    top_ab, rest_ab = split_top_rest(ranked_ab)
    f = score(ff, top_ab, rest_ab)
    f_lb = date_block_lb(ff, top_ab, rest_ab) if ff else None
    print(f"\n== LEG F — rank on A+B, score from 10-01 ==\n  TOP {sorted(top_ab)}  "
          f"REST {sorted(rest_ab)}")
    print(f"  TOP n={f[0]} {_f(f[1])}   REST n={f[2]} {_f(f[3])}   separation {_f(f[4])}   "
          f"date-block p5 {_f(f_lb)}")

    print("\n-- reported, never decides --")
    days_b = max(1e-9, (fwd - split).total_seconds() / 86400.0)
    if r[1] is not None:
        per_day = r[0] / days_b
        print(f"  window-B TOP fills/day {per_day:.1f}; $/month at x1 "
              f"{per_day * r[1] * 30 / 100:+.2f}$, extra at x3 {per_day * r[1] * 2 * 30 / 100:+.2f}$")
    now = dt.datetime.now(dt.timezone.utc)
    print(f"  forward window age: {(min(now, until) - fwd).total_seconds() / 86400.0:.1f} days")

    v, why = verdict(cov, len(ranked_a), len(ranked_ab), r, f, f_lb)
    print("\n== verdict ==")
    print(f"  {v}")
    for w in why:
        print(f"   - {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
