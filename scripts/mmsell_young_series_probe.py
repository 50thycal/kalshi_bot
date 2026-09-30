"""YOUNG-SERIES probe — are mmsell fills in series young to the board less adversely selected?

Pre-registered in docs/MMSELL_YOUNG_SERIES_THESIS.md (2026-09-30). Frozen feature: AGE = days
between the order's decision instant and the series' first appearance anywhere in the bot's own
history (earliest `mmsell_candidate_ticks.captured_at` for the series, or the earliest
`paper_trades.created_at` on any tag whose ticker carries the series prefix, whichever is
earlier). Frozen split: YOUNG = AGE <= 30 days, MATURE = AGE > 30. Bands are reported only.

Samples: PRIMARY = the pre-Fmmsell10 live mmsell10-family books (out-of-sample of the 09-29
observation); CONFIRMATION A = Fmmsell10 up to 2026-09-30 (in-sample, reported, never decides);
CONFIRMATION B = Fmmsell10 (and any successor tag passed with --confirm-tags) from 2026-09-30
forward (the decisive out-of-sample read).

Outcome per filled order (real money): (settle_NO - limit) * min(filled, 1).

No lookahead: only history rows strictly before the decision instant define AGE; settlement
(GET /markets/{ticker}) only scores; the split is an absolute, outcome-blind cut.

Read-only: DB via DATABASE_URL_RO in a read-only transaction, plus public Kalshi REST for
settlement. Places no orders and writes no tables.

Usage: {"type":"script","name":"mmsell_young_series_probe","id":"young-1"}
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
from mmsell_thin_market_probe import CONFIRM_TAG, ORDERS_SQL, PRIMARY_TAGS, SPLIT_AT

# Pre-registered constants (docs/MMSELL_YOUNG_SERIES_THESIS.md). Changing any is a new probe.
PRIMARY_SINCE = "2026-07-26T00:00:00+00:00"
FORWARD_AT = "2026-09-30T00:00:00+00:00"
YOUNG_DAYS = 30.0
BANDS = ((0.0, 7.0, "<=7d"), (7.0, 30.0, "8-30d"), (30.0, 60.0, "31-60d"), (60.0, 1e9, ">60d"))
Y0_COVERAGE = 0.95
Y1_FLOOR = 150
Y2_SEPARATION = 1.0
Y3_FLOOR = 100
Y4_FILL_RATIO = 0.50
Y4_FILLS_PER_DAY = 5.0

# Earliest instant each series was ever seen by the bot, from the two histories the thesis names.
# `strictly before the decision` is enforced in Python (a first-seen after the decision is
# impossible for a live order, which itself writes a paper_trades row, but it is checked anyway).
FIRST_SEEN_SQL = """
select series, min(first_at)
  from (
        select series, min(captured_at) as first_at
          from mmsell_candidate_ticks
         where series is not null
         group by series
        union all
        select split_part(market_ticker, '-', 1) as series, min(created_at) as first_at
          from paper_trades
         group by 1
       ) u
 group by series
"""


def series_of(ticker: str) -> str:
    return ticker.split("-")[0]


def age_days(decision, first_seen) -> float | None:
    """Days from the series' first appearance to the decision; None if unknown or after it."""
    if decision is None or first_seen is None:
        return None
    d = (decision - first_seen).total_seconds() / 86400.0
    return d if d >= 0 else None


def is_young(age: float | None) -> bool | None:
    if age is None:
        return None
    return age <= YOUNG_DAYS


def band_of(age: float | None) -> str:
    if age is None:
        return "n/a"
    for lo, hi, name in BANDS:
        if (age <= hi and age > lo) or (age == 0.0 and lo == 0.0):
            return name
    return ">60d"


def _f(x) -> str:
    return "n/a" if x is None else f"{x:+.2f}c"


def verdict(cov: float, n_young: int, sep: float | None, lb: float | None,
            b_n_young: int, b_sep: float | None,
            fill_ratio: float | None, young_per_day: float | None):
    """The pre-registered decision rule, verbatim. Returns (verdict, reasons)."""
    if cov < Y0_COVERAGE:
        return "HOLD (instrument)", [f"Y0 AGE coverage {cov:.1%} < {Y0_COVERAGE:.0%}"]
    why = []
    kills = []
    # Y3 is evaluated whenever its floor is met, independent of Y1 (the forward read is decisive).
    if b_n_young >= Y3_FLOOR and b_sep is not None and b_sep <= 0:
        kills.append(f"Y3 KILL: forward separation {_f(b_sep)} <= 0 (n_young={b_n_young})")
    if n_young < Y1_FLOOR:
        if kills:
            return "KILL", kills
        return "HOLD (accrual)", [f"Y1 young fills n={n_young} < {Y1_FLOOR}; trigger = "
                                  f"Confirmation B reaching {Y1_FLOOR} young fills"] + why
    if sep is not None and sep <= 0:
        kills.append(f"Y2 KILL: separation {_f(sep)} <= 0 at the Y1 floor")
    if kills:
        return "KILL", kills
    y2 = sep is not None and sep >= Y2_SEPARATION and lb is not None and lb > 0
    why.append(f"Y2 separation {_f(sep)} (bar +{Y2_SEPARATION}), boot 5th pct {_f(lb)} -> "
               f"{'PASS' if y2 else 'HOLD (underpowered)'}")
    if not y2:
        return "HOLD (underpowered)", why
    if b_n_young < Y3_FLOOR:
        why.append(f"Y3 forward young fills n={b_n_young} < {Y3_FLOOR} -> HOLD (accrual)")
        return "HOLD (accrual)", why
    why.append(f"Y3 forward separation {_f(b_sep)} -> PASS")
    y4 = (fill_ratio is not None and fill_ratio >= Y4_FILL_RATIO
          and young_per_day is not None and young_per_day >= Y4_FILLS_PER_DAY)
    why.append(f"Y4 young/mature fill-rate ratio {fill_ratio if fill_ratio is None else round(fill_ratio, 2)} "
               f"(bar {Y4_FILL_RATIO}), young fills/day "
               f"{young_per_day if young_per_day is None else round(young_per_day, 1)} "
               f"(bar {Y4_FILLS_PER_DAY}) -> {'PASS' if y4 else 'HOLD (capacity)'}")
    return ("PROMOTE" if y4 else "HOLD (capacity)"), why


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--confirm-tags", default=CONFIRM_TAG,
                    help="comma-separated live tags for the confirmation samples")
    ap.add_argument("--until", default="2100-01-01T00:00:00+00:00")
    ap.add_argument("--min-series-fills", type=int, default=10,
                    help="per-series report rows need this many settled fills (reported only)")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1
    split, forward, until = _aware(SPLIT_AT), _aware(FORWARD_AT), _aware(args.until)
    confirm_tags = [t.strip() for t in args.confirm_tags.split(",") if t.strip()]

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn, \
            conn.cursor() as cur:
        cur.execute(ORDERS_SQL, (list(PRIMARY_TAGS), _aware(PRIMARY_SINCE), split))
        primary = cur.fetchall()
        cur.execute(ORDERS_SQL, (confirm_tags, split, forward))
        conf_a = cur.fetchall()
        cur.execute(ORDERS_SQL, (confirm_tags, forward, until))
        conf_b = cur.fetchall()
        koids = [r[1] for r in primary + conf_a + conf_b]
        cur.execute(WS_FILLS_SQL, (koids,))
        ws = cur.fetchall()
        cur.execute(REST_FILLS_SQL, (koids,))
        rest = cur.fetchall()
        cur.execute(FIRST_SEEN_SQL)
        first_seen = {s: _aware(t) for s, t in cur.fetchall()}
    fills = fill_times(ws, rest)

    print("YOUNG-SERIES probe — read-only; pre-registered in docs/MMSELL_YOUNG_SERIES_THESIS.md")
    print(f"primary tags {','.join(PRIMARY_TAGS)} {PRIMARY_SINCE} -> {SPLIT_AT}: "
          f"{len(primary)} orders")
    print(f"confirmation A {','.join(confirm_tags)} {SPLIT_AT} -> {FORWARD_AT}: "
          f"{len(conf_a)} orders (in-sample of the 09-29 observation; reported only)")
    print(f"confirmation B {','.join(confirm_tags)} from {FORWARD_AT}: {len(conf_b)} orders "
          f"(decisive forward read)")
    print(f"series with a first-seen instant: {len(first_seen)}\n")

    cache: dict = {}

    def build(rows):
        out, missing = [], 0
        for _oid, koid, ticker, created, limit, decided, tag in rows:
            decision = _aware(decided) or _aware(created)
            age = age_days(decision, first_seen.get(series_of(ticker)))
            if age is None:
                missing += 1
                continue
            fill = fills.get(koid)
            filled = bool(fill and fill[1] > 0)
            realized = None
            if filled:
                s = settle_no(ticker, cache)
                if s is not None:
                    realized = (s - int(limit)) * min(fill[1], 1.0)
            out.append({"tag": tag, "series": series_of(ticker), "age": age,
                        "young": is_young(age), "band": band_of(age), "filled": filled,
                        "realized": realized, "decision": decision})
        return out, missing

    prim, p_missing = build(primary)
    ca, _ = build(conf_a)
    cb, _ = build(conf_b)
    cov = len(prim) / len(primary) if primary else 0.0
    print("== Y0 instrument ==")
    print(f"  primary orders with AGE: {len(prim)} of {len(primary)} ({cov:.1%}); "
          f"unresolvable {p_missing}")
    if not prim:
        print("\n== verdict ==\n  HOLD (instrument)")
        return 0

    def fl(grp):
        return [r["realized"] for r in grp if r["realized"] is not None]

    def split_stats(rows, label):
        young = [r for r in rows if r["young"]]
        mature = [r for r in rows if r["young"] is False]
        yf, mf = fl(young), fl(mature)
        ym = sum(yf) / len(yf) if yf else None
        mm = sum(mf) / len(mf) if mf else None
        sep = (ym - mm) if (ym is not None and mm is not None) else None
        lb = bootstrap_diff_lb(yf, mf)
        print(f"\n== {label} ==")
        rates = {}
        for name, grp, f in (("young", young, yf), ("mature", mature, mf)):
            rate = sum(r["filled"] for r in grp) / len(grp) if grp else 0.0
            rates[name] = rate if grp else None
            mean = f"{sum(f) / len(f):+.2f}c" if f else "  n/a"
            wins = sum(1 for x in f if x > 0)
            win = f"{wins / len(f):5.1%}" if f else "  n/a"
            print(f"  {name:6s} orders {len(grp):5d}  fill rate {rate:5.1%}  settled fills "
                  f"{len(f):5d}  win {win}  realized/fill {mean}  total {sum(f) / 100:+.2f}$")
        if sep is not None:
            print(f"  separation young - mature {_f(sep)}   boot 5th pct {_f(lb)}")
        for _lo, _hi, name in BANDS:
            grp = [r for r in rows if r["band"] == name]
            f = fl(grp)
            if grp:
                rate = sum(r["filled"] for r in grp) / len(grp)
                mean = f"{sum(f) / len(f):+.2f}c" if f else "  n/a"
                print(f"    band {name:6s} orders {len(grp):5d}  fill rate {rate:5.1%}  "
                      f"settled fills {len(f):5d}  realized/fill {mean}")
        ratio = None
        if rates.get("young") is not None and rates.get("mature"):
            ratio = rates["young"] / rates["mature"]
        return len(yf), ym, sep, lb, ratio, len(young)

    n_young, _ym, sep, lb, ratio, young_orders = split_stats(prim, "PRIMARY — YOUNG (<=30d) vs MATURE")
    days = max(1e-9, (split - _aware(PRIMARY_SINCE)).total_seconds() / 86400.0)
    young_fills_per_day = sum(1 for r in prim if r["young"] and r["filled"]) / days
    print(f"  young fills/day over the primary window ({days:.1f} d): {young_fills_per_day:.1f}")

    b_n_young, _bm, b_sep, _blb, _r, _yo = split_stats(cb, "Y3 CONFIRMATION B — forward, decisive")
    split_stats(ca, "CONFIRMATION A — Fmmsell10 to 09-30 (in-sample; reported only)")

    print("\n-- reported, never decides --")
    by_tag = defaultdict(list)
    for r in prim:
        by_tag[r["tag"]].append(r)
    for tag, rows in sorted(by_tag.items()):
        if len(rows) >= 60:
            split_stats(rows, f"primary book {tag}")

    by_series = defaultdict(list)
    for r in ca + cb:
        by_series[r["series"]].append(r)
    print("\n== per-series, Fmmsell10 whole epoch (reported only) ==")
    print(f"  {'series':22s} {'age@1st':>8s} {'orders':>6s} {'fills':>6s} {'win':>6s} {'c/fill':>8s}")
    for s, rows in sorted(by_series.items(), key=lambda kv: -len(fl(kv[1]))):
        f = fl(rows)
        if len(f) < args.min_series_fills:
            continue
        first_age = min(r["age"] for r in rows)
        wins = sum(1 for x in f if x > 0)
        print(f"  {s:22s} {first_age:8.1f} {len(rows):6d} {len(f):6d} {wins / len(f):6.1%} "
              f"{sum(f) / len(f):+8.2f}")

    if sep is not None:
        for size in (1, 3):
            print(f"  implied $/month at size {size} from young fills alone: "
                  f"{young_fills_per_day * sep * size * 30 / 100:+.2f}$ "
                  f"(young fills/day x separation x size x 30)")

    v, why = verdict(cov, n_young, sep, lb, b_n_young, b_sep, ratio, young_fills_per_day)
    print("\n== verdict ==")
    print(f"  {v}")
    for w in why:
        print(f"   - {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
