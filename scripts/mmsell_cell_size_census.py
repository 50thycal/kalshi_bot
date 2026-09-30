"""CELL-SIZE census — is there a price cell on the live mmsell book where size x3 is worth it?

Pre-registered in docs/MMSELL_CELL_SIZE_CENSUS.md (2026-09-30). Per NO limit-price cell (93..97)
on the live tag since the epoch start: C1 the cell has >= 150 settled fills; C2 filled orders
earn >= +1.0c per contract with a bootstrap 95% lower bound > 0; C3 the same cell's ordered-but-
never-filled orders (scored by the live tag's own paper_trades row, the counterfactual the twin
already prices) do not beat the fills by more than 2.0c; C4 (reported) the median size of trades
printing at our YES price after we post is >= 3 contracts.

Outcome per filled order (real money): (settle_NO - limit) * min(filled, 1). Unfilled orders are
scored by paper_trades.pnl under the live tag, in cents per contract.

Read-only: DB via DATABASE_URL_RO in a read-only transaction, plus public Kalshi REST for
settlement. Places no orders and writes no tables.

Usage: {"type":"script","name":"mmsell_cell_size_census","id":"cellsize-1"}
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import sys
from collections import defaultdict

from mmsell_flow_veto_probe import (
    REST_FILLS_SQL,
    RO_OPTIONS,
    WS_FILLS_SQL,
    _aware,
    _to_libpq_url,
    fill_times,
    settle_no,
)
from mmsell_thin_market_probe import CONFIRM_TAG, ORDERS_SQL, PRIMARY_TAGS, SPLIT_AT

# Pre-registered constants (docs/MMSELL_CELL_SIZE_CENSUS.md). Changing any is a new census.
CELLS = (93, 94, 95, 96, 97)
C0_COVERAGE = 0.95
C1_FLOOR = 150
C2_MEAN = 1.0
C3_GAP = 2.0
C4_MEDIAN = 3.0
TIMEOUT_S = 4 * 3600
COLLECTOR_SINCE = "2026-09-16T00:00:00+00:00"
BOOT_N = 2000
BOOT_SEED = 20260930

PAPER_SQL = """
select market_ticker, pnl, quantity
  from paper_trades
 where strategy = %s and market_ticker = any(%s) and pnl is not null
"""

# Trades printing at a given YES price inside the order's resting window (WS-019 tape).
TRADES_SQL = """
select market_ticker, ts_ms, yes_price_cents, count_fp
  from execution_trade_events
 where market_ticker = any(%s) and ts_ms is not null
"""


def bootstrap_mean_lb(a: list[float], n: int = BOOT_N, seed: int = BOOT_SEED,
                      q: float = 0.05) -> float | None:
    """q-quantile of the bootstrap distribution of mean(a)."""
    if not a:
        return None
    rng = random.Random(seed)
    la = len(a)
    means = sorted(sum(a[rng.randrange(la)] for _ in range(la)) / la for _ in range(n))
    return means[max(0, min(n - 1, int(q * n)))]


def median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def _f(x) -> str:
    return "n/a" if x is None else f"{x:+.2f}c"


def cell_verdict(n_fills: int, mean: float | None, lb: float | None,
                 unfilled_mean: float | None, med_print: float | None):
    """Per-cell pre-registered rule: (status, size_candidate, reasons)."""
    why = []
    if n_fills < C1_FLOOR:
        return "unreadable", 0, [f"C1 settled fills {n_fills} < {C1_FLOOR}"]
    c2 = mean is not None and mean >= C2_MEAN and lb is not None and lb > 0
    why.append(f"C2 realized/fill {_f(mean)} (bar +{C2_MEAN}), boot 95% LB {_f(lb)} -> "
               f"{'pass' if c2 else 'fail'}")
    gap = None if (unfilled_mean is None or mean is None) else unfilled_mean - mean
    c3 = gap is not None and gap <= C3_GAP
    why.append(f"C3 unfilled paper - filled realized {_f(gap)} (bar <= {C3_GAP}) -> "
               f"{'pass' if c3 else 'fail'}")
    if not (c2 and c3):
        return "fails", 0, why
    size = 3
    if med_print is None:
        why.append("C4 median print at our price: n/a (no tape) -> size stays 3, unverified")
    elif med_print < C4_MEDIAN:
        size = max(1, int(med_print))
        why.append(f"C4 median print {med_print:.1f} < {C4_MEDIAN:.0f} -> size capped at {size}")
    else:
        why.append(f"C4 median print {med_print:.1f} >= {C4_MEDIAN:.0f} -> size 3")
    return "PASS", size, why


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tag", default=CONFIRM_TAG)
    ap.add_argument("--since", default=SPLIT_AT)
    ap.add_argument("--until", default="2100-01-01T00:00:00+00:00")
    ap.add_argument("--no-context", action="store_true",
                    help="skip the pre-9/7 books context table (reported only)")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1
    since, until = _aware(args.since), _aware(args.until)

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn, \
            conn.cursor() as cur:
        cur.execute(ORDERS_SQL, ([args.tag], since, until))
        orders = [r for r in cur.fetchall() if int(r[4]) in CELLS]
        context = []
        if not args.no_context:
            cur.execute(ORDERS_SQL, (list(PRIMARY_TAGS), _aware("2026-07-26T00:00:00+00:00"),
                                     _aware(SPLIT_AT)))
            context = [r for r in cur.fetchall() if int(r[4]) in CELLS]
        koids = [r[1] for r in orders + context]
        cur.execute(WS_FILLS_SQL, (koids,))
        ws = cur.fetchall()
        cur.execute(REST_FILLS_SQL, (koids,))
        rest = cur.fetchall()
        fills = fill_times(ws, rest)
        unfilled_tickers = [r[2] for r in orders if not (fills.get(r[1]) and fills[r[1]][1] > 0)]
        cur.execute(PAPER_SQL, (args.tag, unfilled_tickers))
        paper = defaultdict(list)
        for ticker, pnl, qty in cur.fetchall():
            q = float(qty or 1) or 1.0
            paper[ticker].append(float(pnl) * 100.0 / q)
        tape_tickers = [r[2] for r in orders if _aware(r[3]) >= _aware(COLLECTOR_SINCE)]
        cur.execute(TRADES_SQL, (tape_tickers,))
        trades = defaultdict(list)
        for ticker, ts_ms, yes_px, cnt in cur.fetchall():
            trades[ticker].append((int(ts_ms) / 1000.0, yes_px, float(cnt or 0)))

    print("CELL-SIZE census — read-only; pre-registered in docs/MMSELL_CELL_SIZE_CENSUS.md")
    print(f"tag {args.tag} {args.since} -> {args.until}: {len(orders)} NO-buy orders in cells "
          f"{CELLS}; tape-covered orders {len(tape_tickers)} (collector since {COLLECTOR_SINCE})\n")

    cache: dict = {}

    def build(rows):
        out, unmapped = [], 0
        for _oid, koid, ticker, created, limit, decided, _tag in rows:
            fill = fills.get(koid)
            filled = bool(fill and fill[1] > 0)
            realized = None
            if filled:
                s = settle_no(ticker, cache)
                if s is None:
                    unmapped += 1
                    continue
                realized = (s - int(limit)) * min(fill[1], 1.0)
            t0 = (_aware(decided) or _aware(created)).timestamp()
            prints = [c for ts, px, c in trades.get(ticker, ())
                      if px == 100 - int(limit) and t0 <= ts <= t0 + TIMEOUT_S]
            out.append({"cell": int(limit), "ticker": ticker, "filled": filled,
                        "realized": realized, "paper": paper.get(ticker),
                        "prints": prints, "created": _aware(created)})
        return out, unmapped

    rows, unmapped = build(orders)
    n_filled = sum(1 for r in rows if r["filled"]) + unmapped
    cov = (n_filled - unmapped) / n_filled if n_filled else 0.0
    print("== C0 instrument ==")
    print(f"  filled orders settle-mapped: {n_filled - unmapped} of {n_filled} ({cov:.1%})")
    if cov < C0_COVERAGE:
        print("\n== verdict ==\n  HOLD (instrument)")
        return 0

    days = max(1e-9, ((min(until, _aware(dt.datetime.now(dt.timezone.utc).isoformat()))
                       - since).total_seconds() / 86400.0))
    passing = []
    print(f"\n== cells ({days:.1f} days) ==")
    hdr = (f"  {'cell':>4s} {'orders':>6s} {'fill%':>6s} {'fills':>5s} {'win':>6s} "
           f"{'real/fill':>9s} {'95%LB':>7s} {'unfilled':>8s} {'paper/ct':>8s} {'gap':>7s} "
           f"{'medprint':>8s} {'$/mo@1':>7s} {'$/mo@3':>7s}  status")
    print(hdr)
    for cell in CELLS:
        grp = [r for r in rows if r["cell"] == cell]
        if not grp:
            continue
        f = [r["realized"] for r in grp if r["realized"] is not None]
        mean = sum(f) / len(f) if f else None
        lb = bootstrap_mean_lb(f)
        wins = sum(1 for x in f if x > 0)
        uf = [x for r in grp if not r["filled"] and r["paper"] for x in r["paper"]]
        umean = sum(uf) / len(uf) if uf else None
        prints = [c for r in grp for c in r["prints"]]
        med = median(prints)
        status, size, why = cell_verdict(len(f), mean, lb, umean, med)
        rate = sum(r["filled"] for r in grp) / len(grp)
        fpd = len(f) / days
        gap = None if (umean is None or mean is None) else umean - mean
        dpm1 = None if mean is None else fpd * mean * 30 / 100
        dpm3 = None if mean is None else fpd * mean * size * 30 / 100 if size else None
        print(f"  {cell:4d} {len(grp):6d} {rate:6.1%} {len(f):5d} "
              f"{(wins / len(f)) if f else 0:6.1%} {_f(mean):>9s} {_f(lb):>7s} {len(uf):8d} "
              f"{_f(umean):>8s} {_f(gap):>7s} "
              f"{'n/a' if med is None else f'{med:.1f}':>8s} "
              f"{'n/a' if dpm1 is None else f'{dpm1:+.2f}':>7s} "
              f"{'n/a' if dpm3 is None else f'{dpm3:+.2f}':>7s}  {status}")
        for w in why:
            print(f"        - {w}")
        if status == "PASS":
            passing.append((cell, size))

    allf = [r["realized"] for r in rows if r["realized"] is not None]
    if allf:
        print(f"\n  all cells pooled (reference only): {len(allf)} fills, "
              f"{sum(allf) / len(allf):+.2f}c/fill, {sum(allf) / 100:+.2f}$")

    if context:
        ctx, _ = build(context)
        print("\n== context: pre-9/7 live books, same cells (a different season; never pooled) ==")
        for cell in CELLS:
            grp = [r for r in ctx if r["cell"] == cell]
            f = [r["realized"] for r in grp if r["realized"] is not None]
            if not grp:
                continue
            mean = f"{sum(f) / len(f):+.2f}c" if f else "n/a"
            rate = sum(r["filled"] for r in grp) / len(grp)
            print(f"  {cell:4d} orders {len(grp):5d}  fill rate {rate:5.1%}  settled fills "
                  f"{len(f):5d}  realized/fill {mean}")

    print("\n== verdict ==")
    if passing:
        print("  SIZE CANDIDATE: " + ", ".join(f"cell {c} at size {s}" for c, s in passing))
        print("   - a passing cell makes size-in-cell a candidate successor treatment; arming "
              "is an operator hard stop and the envelope must be restated for the clip")
    else:
        print("  NO CELL PASSES -> sizing on this book is closed until a per-fill edge exists")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
