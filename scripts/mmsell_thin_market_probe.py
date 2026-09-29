"""THIN-MARKET probe — are mmsell fills in quiet markets less adversely selected?

Pre-registered in docs/MMSELL_THIN_MARKET_THESIS.md (2026-09-29). Frozen feature: V24 =
contracts traded in the market over the 24 COMPLETE hourly candles at or before the decision
instant. Frozen split: tercile cut points of V24 over all PRIMARY orders (filled or not);
"thin" = bottom tercile. Primary sample = the pre-Fmmsell10 live mmsell10-family books;
confirmation = Fmmsell10.

Outcome per filled order (real money): (settle_NO - limit) * min(filled, 1).

No lookahead: only candles whose end_period_ts <= decision are summed; settlement
(GET /markets/{ticker}) only scores; cut points never see an outcome.

Read-only: DB via DATABASE_URL_RO in a read-only transaction, plus public Kalshi REST. Places no
orders and writes no tables.

Usage: {"type":"script","name":"mmsell_thin_market_probe","id":"thin-1"}
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict

from mmsell_flow_veto_probe import (
    KALSHI,
    REST_FILLS_SQL,
    RO_OPTIONS,
    WS_FILLS_SQL,
    _aware,
    _to_libpq_url,
    bootstrap_diff_lb,
    fill_times,
    settle_no,
)

# Pre-registered constants (docs/MMSELL_THIN_MARKET_THESIS.md). Changing any is a new probe.
PRIMARY_TAGS = ("mmsell10", "mmsell10a", "mmsell10b", "Lmmsell10", "Cmmsell10", "Dmmsell10",
                "Emmsell10")
CONFIRM_TAG = "Fmmsell10"
SPLIT_AT = "2026-09-07T02:03:36+00:00"
WINDOW_H = 24
T0_COVERAGE = 0.85
T1_FLOOR = 100
T2_SEPARATION = 2.0
T2_THIN_MEAN = 1.0
T3_FLOOR = 40

ORDERS_SQL = """
select o.id, o.kalshi_order_id, o.market_ticker, o.created_at, o.limit_price, c.decided_at,
       o.strategy
  from live_orders o
  left join execution_order_context c on c.live_order_id = o.id
 where o.strategy = any(%s) and o.action = 'buy' and o.side = 'no'
   and o.kalshi_order_id is not null and o.limit_price is not null
   and o.created_at >= %s and o.created_at < %s
 order by o.created_at
"""


def volume_before(candles: list[tuple[float, float]], t_dec: float,
                  hours: int | None) -> float:
    """Sum of candle volume over complete hours ending at or before t_dec. `hours=None` sums
    every such candle (cumulative); otherwise only those ending within `hours` of t_dec."""
    tot = 0.0
    for end_ts, vol in candles:
        if end_ts > t_dec:
            continue
        if hours is not None and end_ts <= t_dec - hours * 3600:
            continue
        tot += vol
    return tot


def terciles(values: list[float]) -> tuple[float, float]:
    """(p33, p67) cut points; thin = v <= p33."""
    s = sorted(values)
    n = len(s)
    return s[max(0, (n - 1) // 3)], s[max(0, (2 * (n - 1)) // 3)]


def _f(x) -> str:
    return "n/a" if x is None else f"{x:+.2f}c"


def verdict(cov: float, n_thin: int, sep: float | None, lb: float | None,
            thin_mean: float | None, c_n_thin: int, c_sep: float | None):
    """The pre-registered decision rule, verbatim."""
    if cov < T0_COVERAGE:
        return "HOLD (instrument)", [f"T0 coverage {cov:.1%} < {T0_COVERAGE:.0%}"]
    if n_thin < T1_FLOOR:
        return "HOLD", [f"T1 thin fills n={n_thin} < {T1_FLOOR}"]
    kills = []
    if sep is not None and sep <= 0:
        kills.append(f"T2 KILL: separation {_f(sep)} <= 0")
    if c_n_thin >= T3_FLOOR and c_sep is not None and c_sep <= 0:
        kills.append(f"T3 KILL: confirmation separation {_f(c_sep)} <= 0 (n_thin={c_n_thin})")
    if kills:
        return "KILL", kills
    t2 = (sep is not None and sep >= T2_SEPARATION and lb is not None and lb > 0
          and thin_mean is not None and thin_mean >= T2_THIN_MEAN)
    why = [f"T2 separation {_f(sep)} (bar +{T2_SEPARATION}), boot 5th pct {_f(lb)}, thin "
           f"mean {_f(thin_mean)} (bar +{T2_THIN_MEAN}) -> {'PASS' if t2 else 'fail'}"]
    if c_n_thin < T3_FLOOR:
        why.append(f"T3 confirmation thin fills n={c_n_thin} < {T3_FLOOR} -> HOLD")
        return "HOLD", why
    why.append(f"T3 confirmation separation {_f(c_sep)} -> PASS")
    return ("PROMOTE" if t2 else "HOLD"), why


def fetch_candles(ticker: str, t0: int, t1: int):
    """[(end_period_ts, volume)] hourly, or None if the API failed."""
    import xvenue_leadlag as xl  # browser UA + retries; deferred so tests need no network
    series = ticker.split("-")[0]
    data = xl._get(f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks"
                   f"?start_ts={t0}&end_ts={t1}&period_interval=60")
    if data is None:
        return None
    out = []
    for c in data.get("candlesticks") or []:
        ts = xl._num(c.get("end_period_ts"))
        v = c.get("volume_fp")
        if v is None:
            v = c.get("volume")
        if ts:
            out.append((ts, xl._num(v)))
    time.sleep(0.05)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--primary-since", default="2026-07-26T00:00:00+00:00")
    ap.add_argument("--confirm-until", default="2100-01-01T00:00:00+00:00")
    ap.add_argument("--lookback-days", type=int, default=45,
                    help="cumulative-volume lookback (reported only)")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1
    split = _aware(SPLIT_AT)

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn, \
            conn.cursor() as cur:
        cur.execute(ORDERS_SQL, (list(PRIMARY_TAGS), _aware(args.primary_since), split))
        primary = cur.fetchall()
        cur.execute(ORDERS_SQL, ([CONFIRM_TAG], split, _aware(args.confirm_until)))
        confirm = cur.fetchall()
        koids = [r[1] for r in primary + confirm]
        cur.execute(WS_FILLS_SQL, (koids,))
        ws = cur.fetchall()
        cur.execute(REST_FILLS_SQL, (koids,))
        rest = cur.fetchall()
    fills = fill_times(ws, rest)

    print("THIN-MARKET probe — read-only; pre-registered in docs/MMSELL_THIN_MARKET_THESIS.md")
    print(f"primary tags {','.join(PRIMARY_TAGS)} before {SPLIT_AT}: {len(primary)} orders")
    print(f"confirmation {CONFIRM_TAG} from {SPLIT_AT}: {len(confirm)} orders\n")

    cache: dict = {}

    def build(rows):
        out, missing = [], 0
        for _oid, koid, ticker, created, limit, decided, tag in rows:
            t = (_aware(decided) or _aware(created)).timestamp()
            candles = fetch_candles(ticker, int(t) - args.lookback_days * 86400, int(t))
            if candles is None:
                missing += 1
                continue
            fill = fills.get(koid)
            filled = bool(fill and fill[1] > 0)
            realized = None
            if filled:
                s = settle_no(ticker, cache)
                if s is not None:
                    realized = (s - int(limit)) * min(fill[1], 1.0)
            out.append({"tag": tag, "v24": volume_before(candles, t, WINDOW_H),
                        "vcum": volume_before(candles, t, None), "filled": filled,
                        "realized": realized})
        return out, missing

    prim, p_missing = build(primary)
    conf, _c_missing = build(confirm)
    cov = len(prim) / len(primary) if primary else 0.0
    print("== T0 instrument ==")
    print(f"  primary orders with V24: {len(prim)} of {len(primary)} ({cov:.1%}); "
          f"API misses {p_missing}")
    if not prim:
        print("\n== verdict ==\n  HOLD (instrument)")
        return 0
    p33, p67 = terciles([r["v24"] for r in prim])
    c33, c67 = terciles([r["vcum"] for r in prim])
    print(f"  V24 tercile cut points (frozen from primary): thin <= {p33:.0f}  "
          f"mid <= {p67:.0f} contracts")

    def split_stats(rows, key, lo, hi, label):
        thin = [r for r in rows if r[key] <= lo]
        mid = [r for r in rows if lo < r[key] <= hi]
        thick = [r for r in rows if r[key] > hi]
        other = mid + thick

        def fl(grp):
            return [r["realized"] for r in grp if r["realized"] is not None]

        tf, of = fl(thin), fl(other)
        tm = sum(tf) / len(tf) if tf else None
        om = sum(of) / len(of) if of else None
        sep = (tm - om) if (tm is not None and om is not None) else None
        lb = bootstrap_diff_lb(tf, of)
        print(f"\n== {label} ==")
        for name, grp in (("thin", thin), ("mid", mid), ("thick", thick)):
            f = fl(grp)
            rate = sum(r["filled"] for r in grp) / len(grp) if grp else 0.0
            mean = f"{sum(f) / len(f):+.2f}c" if f else "  n/a"
            print(f"  {name:6s} orders {len(grp):5d}  fill rate {rate:5.1%}  settled fills "
                  f"{len(f):5d}  realized/fill {mean}  total {sum(f) / 100:+.2f}$")
        if sep is not None:
            print(f"  separation thin - (mid+thick) {_f(sep)}   boot 5th pct {_f(lb)}")
        return len(tf), tm, sep, lb

    n_thin, thin_mean, sep, lb = split_stats(prim, "v24", p33, p67, "PRIMARY — V24 terciles")
    c_n_thin, _cm, c_sep, _clb = split_stats(conf, "v24", p33, p67,
                                             "T3 CONFIRMATION — Fmmsell10, same cut points")

    print("\n-- reported, never decides --")
    split_stats(prim, "vcum", c33, c67, "primary — cumulative-volume terciles")
    by_tag = defaultdict(list)
    for r in prim:
        by_tag[r["tag"]].append(r)
    for tag, rows in sorted(by_tag.items()):
        if len(rows) >= 60:
            split_stats(rows, "v24", p33, p67, f"primary book {tag}")

    v, why = verdict(cov, n_thin, sep, lb, thin_mean, c_n_thin, c_sep)
    print("\n== verdict ==")
    print(f"  {v}")
    for w in why:
        print(f"   - {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
