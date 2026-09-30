"""QUEUE-DEPTH probe — are mmsell fills that land behind a deep queue less adversely selected?

Pre-registered in docs/MMSELL_QUEUE_DEPTH_THESIS.md (2026-09-30). Frozen feature: AHEAD =
`contracts_ahead` on the order's FIRST live_order_queue_ticks row (non-null reading) captured
within 600 s of the order's creation. Frozen split: DEEP = AHEAD >= 500, NOT-DEEP otherwise.
A filled order with no reading inside 600 s is CENSORED-FAST (it filled before it could be read)
and is carried separately, never dropped silently.

Samples: PRIMARY = the pre-Fmmsell10 live books from 2026-08-14 (queue sampling began) to the
Fmmsell10 epoch start; CONFIRMATION = Fmmsell10 from its epoch start.

Outcome per filled order (real money): (settle_NO - limit) * min(filled, 1).

The scan-time `depth_at_best_ask` column is deliberately NOT used (docs/MMSELL_DEPTH_FILL_MODEL.md:
it is not queue position). Read-only: DB via DATABASE_URL_RO in a read-only transaction plus
public Kalshi REST for settlement. Places no orders and writes no tables.

Usage: {"type":"script","name":"mmsell_queue_depth_probe","id":"qdepth-1"}
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

from mmsell_flow_veto_probe import (
    REST_FILLS_SQL,
    RO_OPTIONS,
    WS_FILLS_SQL,
    _aware,
    _to_libpq_url,
    bootstrap_diff_lb,
    fill_times,
    settle_no,
)
from mmsell_thin_market_probe import CONFIRM_TAG, ORDERS_SQL, SPLIT_AT

# Pre-registered constants (docs/MMSELL_QUEUE_DEPTH_THESIS.md). Changing any is a new probe.
PRIMARY_TAGS = ("mmsell10a", "mmsell10b", "Lmmsell10", "Cmmsell10", "Dmmsell10", "Emmsell10")
PRIMARY_SINCE = "2026-08-14T00:00:00+00:00"
READ_WINDOW_S = 600
ROBUST_WINDOW_S = 90
DEEP = 500
BANDS = ((0, 50, "<50"), (50, 200, "50-199"), (200, 500, "200-499"), (500, 2000, "500-1999"),
         (2000, 10**12, ">=2000"))
Q0_COVERAGE = 0.80
Q1_FLOOR = 100
Q2_SEPARATION = 1.0
Q3_FLOOR = 100
SHAPE_D = (100, 200, 500, 1000, 2000)

# First reading per order with a non-null contracts_ahead; the 600 s window is applied in Python
# against the order's own created_at so the SQL stays one pass.
FIRST_TICK_SQL = """
select distinct on (t.kalshi_order_id) t.kalshi_order_id, t.captured_at, t.contracts_ahead,
       t.trigger
  from live_order_queue_ticks t
 where t.kalshi_order_id = any(%s) and t.contracts_ahead is not null
 order by t.kalshi_order_id, t.captured_at
"""


def band_of(ahead: float | None) -> str:
    if ahead is None:
        return "censored"
    for lo, hi, name in BANDS:
        if lo <= ahead < hi:
            return name
    return ">=2000"


def is_deep(ahead: float | None, d: float = DEEP) -> bool | None:
    return None if ahead is None else ahead >= d


def _f(x) -> str:
    return "n/a" if x is None else f"{x:+.2f}c"


def verdict(cov_p: float, cov_c: float, n_deep: int, n_not: int, sep: float | None,
            lb: float | None, c_n_deep: int, c_n_not: int, c_sep: float | None,
            policy_usd: float | None, all_usd: float | None):
    """The pre-registered decision rule, verbatim. Returns (verdict, reasons)."""
    if cov_p < Q0_COVERAGE or cov_c < Q0_COVERAGE:
        return "HOLD (instrument)", [f"Q0 coverage primary {cov_p:.1%} / confirmation "
                                     f"{cov_c:.1%} < {Q0_COVERAGE:.0%}"]
    kills, why = [], []
    c_ready = c_n_deep >= Q3_FLOOR and c_n_not >= Q3_FLOOR
    if c_ready and c_sep is not None and c_sep <= 0:
        kills.append(f"Q3 KILL: confirmation separation {_f(c_sep)} <= 0 "
                     f"(deep n={c_n_deep}, not-deep n={c_n_not})")
    if n_deep < Q1_FLOOR or n_not < Q1_FLOOR:
        if kills:
            return "KILL", kills
        return "HOLD (accrual)", [f"Q1 primary deep n={n_deep} / not-deep n={n_not} < {Q1_FLOOR}"]
    if sep is not None and sep <= 0:
        kills.append(f"Q2 KILL: primary separation {_f(sep)} <= 0 at the floor")
    if kills:
        return "KILL", kills
    q2 = sep is not None and sep >= Q2_SEPARATION and lb is not None and lb > 0
    why.append(f"Q2 separation {_f(sep)} (bar +{Q2_SEPARATION}), boot 5th pct {_f(lb)} -> "
               f"{'PASS' if q2 else 'HOLD (underpowered)'}")
    if not q2:
        return "HOLD (underpowered)", why
    if not c_ready:
        why.append(f"Q3 confirmation deep n={c_n_deep} / not-deep n={c_n_not} < {Q3_FLOOR} -> "
                   f"HOLD (accrual)")
        return "HOLD (accrual)", why
    why.append(f"Q3 confirmation separation {_f(c_sep)} -> PASS")
    q4 = policy_usd is not None and all_usd is not None and policy_usd >= all_usd
    why.append(f"Q4 policy dollars {policy_usd if policy_usd is None else round(policy_usd, 2)} "
               f"vs all fills {all_usd if all_usd is None else round(all_usd, 2)} -> "
               f"{'PASS' if q4 else 'HOLD (policy)'}")
    return ("PROMOTE" if q4 else "HOLD (policy)"), why


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--confirm-tag", default=CONFIRM_TAG)
    ap.add_argument("--until", default="2100-01-01T00:00:00+00:00")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1
    split, until = _aware(SPLIT_AT), _aware(args.until)

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn, \
            conn.cursor() as cur:
        cur.execute(ORDERS_SQL, (list(PRIMARY_TAGS), _aware(PRIMARY_SINCE), split))
        primary = cur.fetchall()
        cur.execute(ORDERS_SQL, ([args.confirm_tag], split, until))
        confirm = cur.fetchall()
        koids = [r[1] for r in primary + confirm]
        cur.execute(WS_FILLS_SQL, (koids,))
        ws = cur.fetchall()
        cur.execute(REST_FILLS_SQL, (koids,))
        rest = cur.fetchall()
        cur.execute(FIRST_TICK_SQL, (koids,))
        first = {k: (_aware(at), float(ahead), trig) for k, at, ahead, trig in cur.fetchall()}
    fills = fill_times(ws, rest)

    print("QUEUE-DEPTH probe — read-only; pre-registered in docs/MMSELL_QUEUE_DEPTH_THESIS.md")
    print(f"primary tags {','.join(PRIMARY_TAGS)} {PRIMARY_SINCE} -> {SPLIT_AT}: "
          f"{len(primary)} orders")
    print(f"confirmation {args.confirm_tag} from {SPLIT_AT}: {len(confirm)} orders")
    print(f"orders with any queue reading: {len(first)}\n")

    cache: dict = {}

    def build(rows):
        out = []
        for _oid, koid, ticker, created, limit, _decided, tag in rows:
            created = _aware(created)
            fill = fills.get(koid)
            filled = bool(fill and fill[1] > 0)
            ahead, delay, trig = None, None, None
            ft = first.get(koid)
            if ft is not None and ft[0] is not None:
                delay = (ft[0] - created).total_seconds()
                if 0 <= delay <= READ_WINDOW_S:
                    ahead, trig = ft[1], ft[2]
            realized = None
            if filled:
                s = settle_no(ticker, cache)
                if s is not None:
                    realized = (s - int(limit)) * min(fill[1], 1.0)
            out.append({"tag": tag, "ahead": ahead, "deep": is_deep(ahead),
                        "band": band_of(ahead), "delay": delay, "trigger": trig,
                        "filled": filled, "realized": realized,
                        "censored": filled and ahead is None})
        return out

    prim, conf = build(primary), build(confirm)

    def coverage(rows):
        return sum(1 for r in rows if r["ahead"] is not None) / len(rows) if rows else 0.0

    cov_p, cov_c = coverage(prim), coverage(conf)
    print("== Q0 instrument ==")
    for name, rows, cov in (("primary", prim, cov_p), ("confirmation", conf, cov_c)):
        cens = sum(1 for r in rows if r["censored"])
        nf = sum(1 for r in rows if r["filled"])
        print(f"  {name:12s} orders {len(rows):5d}  with AHEAD inside {READ_WINDOW_S}s "
              f"{cov:6.1%}  censored-fast fills {cens} of {nf} filled")

    def fl(grp):
        return [r["realized"] for r in grp if r["realized"] is not None]

    def split_stats(rows, label):
        deep = [r for r in rows if r["deep"]]
        notd = [r for r in rows if r["deep"] is False]
        df, nf = fl(deep), fl(notd)
        dm = sum(df) / len(df) if df else None
        nm = sum(nf) / len(nf) if nf else None
        sep = (dm - nm) if (dm is not None and nm is not None) else None
        lb = bootstrap_diff_lb(df, nf)
        print(f"\n== {label} ==")
        for name, grp, f in (("deep", deep, df), ("not-deep", notd, nf)):
            rate = sum(r["filled"] for r in grp) / len(grp) if grp else 0.0
            mean = f"{sum(f) / len(f):+.2f}c" if f else "  n/a"
            wins = sum(1 for x in f if x > 0)
            win = f"{wins / len(f):5.1%}" if f else "  n/a"
            print(f"  {name:8s} orders {len(grp):5d}  fill rate {rate:5.1%}  settled fills "
                  f"{len(f):5d}  win {win}  realized/fill {mean}  total {sum(f) / 100:+.2f}$")
        if sep is not None:
            print(f"  separation deep - not-deep {_f(sep)}   boot 5th pct {_f(lb)}")
        for _lo, _hi, name in BANDS:
            grp = [r for r in rows if r["band"] == name]
            f = fl(grp)
            if grp:
                rate = sum(r["filled"] for r in grp) / len(grp)
                mean = f"{sum(f) / len(f):+.2f}c" if f else "  n/a"
                print(f"    band {name:8s} orders {len(grp):5d}  fill rate {rate:5.1%}  "
                      f"settled fills {len(f):5d}  realized/fill {mean}")
        cens = fl([r for r in rows if r["censored"]])
        if cens:
            print(f"    censored-fast fills {len(cens):5d}  realized/fill "
                  f"{sum(cens) / len(cens):+.2f}c  (no reading inside {READ_WINDOW_S}s)")
        return len(df), len(nf), sep, lb

    n_deep, n_not, sep, lb = split_stats(prim, "PRIMARY — DEEP (>=500 ahead) vs NOT-DEEP")
    c_n_deep, c_n_not, c_sep, _clb = split_stats(conf, "Q3 CONFIRMATION — Fmmsell10")

    # Q4 policy value on the confirmation sample: keep DEEP fills and the censored-fast fills the
    # policy could not have cancelled; compare to all fills.
    all_f = fl(conf)
    all_usd = sum(all_f) / 100 if all_f else None
    policy_usd = None
    print("\n== Q4 policy value (confirmation): keep fills with AHEAD >= D plus censored-fast ==")
    for d in SHAPE_D:
        kept = [r["realized"] for r in conf
                if r["realized"] is not None and (r["censored"] or (r["ahead"] is not None
                                                                     and r["ahead"] >= d))]
        usd = sum(kept) / 100 if kept else None
        mark = " <- decides" if d == DEEP else ""
        print(f"  D={d:5d}: kept fills {len(kept):4d} of {len(all_f):4d}  policy ${usd if usd is None else round(usd, 2)}"
              f"  vs all ${all_usd if all_usd is None else round(all_usd, 2)}{mark}")
        if d == DEEP:
            policy_usd = usd

    print("\n-- reported, never decides --")
    rob = [r for r in conf if r["trigger"] == "at_rest" and r["delay"] is not None
           and r["delay"] <= ROBUST_WINDOW_S]
    if rob:
        split_stats(rob, f"robustness — at_rest reading within {ROBUST_WINDOW_S}s (WS-019)")
    by_tag = defaultdict(list)
    for r in prim:
        by_tag[r["tag"]].append(r)
    for tag, rows in sorted(by_tag.items()):
        if len(rows) >= 60:
            split_stats(rows, f"primary book {tag}")

    kept_deep = [r for r in conf if r["realized"] is not None and r["deep"]]
    if kept_deep and c_sep is not None:
        km = sum(r["realized"] for r in kept_deep) / len(kept_deep)
        print(f"\n  deep fills on Fmmsell10: {len(kept_deep)} settled at {km:+.2f}c/fill")

    v, why = verdict(cov_p, cov_c, n_deep, n_not, sep, lb, c_n_deep, c_n_not, c_sep,
                     policy_usd, all_usd)
    print("\n== verdict ==")
    print(f"  {v}")
    for w in why:
        print(f"   - {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
