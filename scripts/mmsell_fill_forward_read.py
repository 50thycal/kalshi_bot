"""MMSELL FILL FORWARD READ — P-SLOW and P-SPREAD on live fills since 2026-10-01.

Bars frozen 2026-10-03 (docs/MMSELL_REPLAY_PROBES_20261003.md, Pre-registration, verbatim); read
rules fixed 2026-10-29 before this read (docs/MMSELL_FILL_FORWARD_READ.md §2). Changing any
constant marked "pre-registered" is a new probe.

Sample: live buy-NO orders on the declared successor lineage of the Fmmsell10 book (Fmmsell10 ->
Hmmsell10 -> Jmmsell10 -> Kmmsell10) decided from 2026-10-01T00:00Z that filled (qty > 0) and
settled yes/no. Outcome per order: settle_NO - limit cents at one contract. Every other live tag is
printed as a diagnostic and never decides.

No lookahead: classification and spread are decision-time facts; fill and settlement only score.
Read-only: DATABASE_URL_RO in a read-only transaction plus public Kalshi REST. Places no orders,
writes no tables. Stdlib + psycopg (+ sibling scripts).

Usage: {"type":"script","name":"mmsell_fill_forward_read","args":[],"id":"rl-ffr-run1-<date>"}
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# --- pre-registered constants ----------------------------------------------------------------
FORWARD_AT = "2026-10-01T00:00:00+00:00"
LINEAGE = ("Fmmsell10", "Hmmsell10", "Jmmsell10", "Kmmsell10")
SLOW_CELL = frozenset({"event_stat", "mention", "exact_score", "outright", "game_prop"})
UNCLASSIFIED_TYPE = "unclassified"
SLOW_FLOOR, SLOW_MIN_MEAN = 100, 2.0
SPREAD_WIDE, SPREAD_NARROW_MAX = 3, 2
SPREAD_FLOOR, SPREAD_MIN_SEP = 80, 3.0
BOOT_N, BOOT_SEED = 5000, 20261003

ORDERS_SQL = """
SELECT o.id, o.kalshi_order_id, o.market_ticker, o.strategy, o.created_at, o.limit_price,
       c.decided_at, c.spread
  FROM live_orders o
  LEFT JOIN execution_order_context c ON c.live_order_id = o.id
 WHERE o.action = 'buy' AND o.side = 'no' AND o.strategy ILIKE %(pat)s
   AND o.kalshi_order_id IS NOT NULL AND o.limit_price IS NOT NULL
   AND coalesce(c.decided_at, o.created_at) >= %(since)s
 ORDER BY o.created_at
"""


# ---------------------------------------------------------------------------------------------
# pure helpers (unit-tested in tests/test_mmsell_fill_forward_read.py)


def outcome_cents(result: str, limit: int) -> float | None:
    """settle_NO - limit at one contract: NO settles 100, YES settles 0."""
    r = (result or "").strip().lower()
    if r not in ("yes", "no") or limit is None:
        return None
    return (100 if r == "no" else 0) - limit


def in_slow_cell(mtype: str) -> bool | None:
    """True = cell, False = REST (every other CLASSIFIED type), None = unclassified (excluded)."""
    if mtype == UNCLASSIFIED_TYPE:
        return None
    return mtype in SLOW_CELL


def spread_side(spread) -> str | None:
    if spread is None:
        return None
    if spread >= SPREAD_WIDE:
        return "wide"
    if spread <= SPREAD_NARROW_MAX:
        return "narrow"
    return None


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def block_bootstrap_sep(a: list[tuple[str, float]], b: list[tuple[str, float]],
                        n: int = BOOT_N, seed: int = BOOT_SEED) -> tuple[float | None, float | None]:
    """(separation mean(a) - mean(b), its 5th percentile) resampling settlement-date blocks."""
    ma, mb = _mean([v for _, v in a]), _mean([v for _, v in b])
    if ma is None or mb is None:
        return None, None
    blocks: dict[str, tuple[list[float], list[float]]] = defaultdict(lambda: ([], []))
    for d, v in a:
        blocks[d][0].append(v)
    for d, v in b:
        blocks[d][1].append(v)
    keys = sorted(blocks)
    rng = random.Random(seed)
    seps = []
    for _ in range(n):
        xa: list[float] = []
        xb: list[float] = []
        for _k in keys:
            ka, kb = blocks[keys[rng.randrange(len(keys))]]
            xa.extend(ka)
            xb.extend(kb)
        if xa and xb:
            seps.append(sum(xa) / len(xa) - sum(xb) / len(xb))
    if not seps:
        return ma - mb, None
    seps.sort()
    return ma - mb, seps[int(0.05 * (len(seps) - 1))]


def p_slow_verdict(n_cell: int, cell_mean, sep, p5) -> str:
    if n_cell < SLOW_FLOOR:
        return f"HOLD (accrual: {n_cell}/{SLOW_FLOOR} cell fills)"
    if cell_mean is not None and cell_mean >= SLOW_MIN_MEAN and sep is not None and sep > 0 \
            and p5 is not None and p5 > 0:
        return "PASS"
    return "NO PASS (at floor; no forward kill clause was pre-registered)"


def p_spread_verdict(n_wide: int, sep, p5) -> str:
    if n_wide < SPREAD_FLOOR:
        return f"HOLD (accrual: {n_wide}/{SPREAD_FLOOR} wide-spread fills)"
    if sep is not None and sep <= 0:
        return "KILL"
    if sep is not None and sep >= SPREAD_MIN_SEP and p5 is not None and p5 > 0:
        return "PASS"
    return "HOLD"


# ---------------------------------------------------------------------------------------------


def _fmt(x, spec="{:+.2f}") -> str:
    return "n/a" if x is None else spec.format(x)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default=FORWARD_AT)
    args = ap.parse_args(argv)

    import mmsell_market_types as mt
    import psycopg
    from mmsell_flow_veto_probe import (
        REST_FILLS_SQL,
        RO_OPTIONS,
        WS_FILLS_SQL,
        _aware,
        _to_libpq_url,
        fill_times,
    )
    from mmsell_selection_audit import _result, fetch_markets

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO not set", file=sys.stderr)
        return 1
    print("MMSELL fill forward read — P-SLOW / P-SPREAD (bars frozen 2026-10-03; read rules "
          "docs/MMSELL_FILL_FORWARD_READ.md)\n")
    with psycopg.connect(url, options=RO_OPTIONS) as conn, conn.cursor() as cur:
        cur.execute(ORDERS_SQL, {"pat": "%mmsell%", "since": args.since})
        orders = cur.fetchall()
        koids = [o[1] for o in orders]
        cur.execute(WS_FILLS_SQL, (koids,))
        ws = cur.fetchall()
        cur.execute(REST_FILLS_SQL, (koids,))
        rest = cur.fetchall()
    fills = fill_times(ws, rest)
    markets = fetch_markets(sorted({o[2] for o in orders}))

    rows, excl = [], defaultdict(int)
    for _id, koid, ticker, tag, created, limit, decided, spread in orders:
        f = fills.get(koid)
        filled = bool(f and f[1] > 0)
        m = markets.get(ticker) or {}
        out = outcome_cents(_result(m), limit)
        series = ticker.split("-", 1)[0].upper()
        mtype = mt.classify(series)[0]
        settle_day = (m.get("settlement_ts") or m.get("close_time") or m.get("expiration_time")
                      or "")[:10]
        if out is None:
            excl["unsettled/void"] += 1
        rows.append({"tag": tag, "ticker": ticker, "filled": filled, "out": out, "mtype": mtype,
                     "cell": in_slow_cell(mtype), "spread": spread, "side": spread_side(spread),
                     "day": settle_day or "?", "week": (_aware(decided or created)
                                                        .strftime("%G-W%V"))})
    lin = [r for r in rows if r["tag"] in LINEAGE]
    scored = [r for r in lin if r["filled"] and r["out"] is not None]
    print(f"orders since {args.since}: {len(orders)} (lineage {len(lin)}); lineage filled+settled:"
          f" {len(scored)}; excluded {dict(excl)}")
    by_tag = defaultdict(lambda: [0, 0, []])
    for r in rows:
        t = by_tag[r["tag"]]
        t[0] += 1
        t[1] += r["filled"]
        if r["filled"] and r["out"] is not None:
            t[2].append(r["out"])
    print("\n== by tag (orders, fill rate, settled fills, realized c/fill)"
          "  [* = decides]")
    for tag, (n, nf, outs) in sorted(by_tag.items()):
        print(f"  {'*' if tag in LINEAGE else ' '} {tag:14s} {n:5d} {100 * nf / n:5.1f}%"
              f" {len(outs):5d} {_fmt(_mean(outs)):>7}")

    # --- P-SLOW -----------------------------------------------------------------------------
    cell = [(r["day"], r["out"]) for r in scored if r["cell"] is True]
    restc = [(r["day"], r["out"]) for r in scored if r["cell"] is False]
    unc = sum(1 for r in scored if r["cell"] is None)
    sep, p5 = block_bootstrap_sep(cell, restc)
    cm = _mean([v for _, v in cell])
    vs = p_slow_verdict(len(cell), cm, sep, p5)
    print("\n== P-SLOW (forward leg)")
    print(f"  cell {len(cell)} fills, {_fmt(cm)}c | REST {len(restc)} fills,"
          f" {_fmt(_mean([v for _, v in restc]))}c | unclassified excluded {unc}")
    print(f"  separation {_fmt(sep)}c, bootstrap 5th pct {_fmt(p5)} -> {vs}")

    # --- P-SPREAD ---------------------------------------------------------------------------
    wide = [(r["day"], r["out"]) for r in scored if r["side"] == "wide"]
    narrow = [(r["day"], r["out"]) for r in scored if r["side"] == "narrow"]
    no_ctx = sum(1 for r in scored if r["spread"] is None)
    ssep, sp5 = block_bootstrap_sep(wide, narrow)
    vsp = p_spread_verdict(len(wide), ssep, sp5)
    print("\n== P-SPREAD")
    print(f"  spread>=3: {len(wide)} fills, {_fmt(_mean([v for _, v in wide]))}c |"
          f" spread<=2: {len(narrow)} fills, {_fmt(_mean([v for _, v in narrow]))}c |"
          f" no context {no_ctx}")
    print(f"  separation {_fmt(ssep)}c, bootstrap 5th pct {_fmt(sp5)} -> {vsp}")

    # --- descriptive only -------------------------------------------------------------------
    print("\n== descriptive (never decides): lineage, by market type")
    print(f"  {'type':14s} {'orders':>6} {'fill%':>6} {'fills':>6} {'c/fill':>7} {'loss%':>6}")
    by_type = defaultdict(list)
    for r in lin:
        by_type[r["mtype"]].append(r)
    for k, rs in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
        sc = [r["out"] for r in rs if r["filled"] and r["out"] is not None]
        loss = sum(1 for v in sc if v < 0)
        print(f"  {k:14s} {len(rs):6d} {100 * sum(r['filled'] for r in rs) / len(rs):6.1f}"
              f" {len(sc):6d} {_fmt(_mean(sc)):>7} {_fmt(100 * loss / len(sc) if sc else None, '{:.1f}'):>6}")
    print("\n== descriptive: lineage by decision week (fills, c/fill, cell fills, cell c/fill)")
    by_week = defaultdict(list)
    for r in scored:
        by_week[r["week"]].append(r)
    for w, rs in sorted(by_week.items()):
        c = [r["out"] for r in rs if r["cell"] is True]
        print(f"  {w}  {len(rs):4d} {_fmt(_mean([r['out'] for r in rs])):>7}"
              f"  {len(c):4d} {_fmt(_mean(c)):>7}")
    print(f"\n== VERDICT\n  P-SLOW: {vs}\n  P-SPREAD: {vsp}\n  This read authorizes nothing;"
          " any size or filter change is an owner decision through Experiment OS.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
