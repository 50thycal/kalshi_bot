"""LIMM EXIT VS HOLD — do the live incentive book's exits beat holding its fills to settlement?

Pre-registered in docs/LIMM_PROGRAMME_OUTCOMES.md §9 (2026-10-30). The §7 surprise was that
mention long shots settled above their price; if that holds on the book's own fills, the stop at
half price sells contracts that would have won.

Per settled `Alimm1` market with at least one filled entry (fills of a `buy` order):
  actual = Kalshi's realized_pnl for the market (positions row carrying settled_time)
  hold   = sum over entry fills of qty * (100*[fill side won] - price) / 100 - entry fees
  exit value = actual - hold, dollars per market, event-clustered.
Secondary (instrument check, never decides): the same exit value computed from the exit fills
themselves, so a basis difference in realized_pnl (fees) is visible.

No lookahead: fills are what happened; the settlement result only scores.
Read-only: DATABASE_URL_RO (read-only transaction) plus public Kalshi REST. Stdlib + psycopg.

Usage: {"type":"script","name":"limm_exit_vs_hold","args":[],"id":"rl-lexit-run1-<date>"}
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# --- pre-registered constants ----------------------------------------------------------------
LIVE_TAG = "Alimm1"
MIN_MARKETS, MIN_EVENTS = 30, 15
Z = 1.96

FILLS_SQL = """
SELECT DISTINCT ON (f.id) f.market_ticker, o.action, f.action, f.side, f.price, f.quantity,
       COALESCE(f.fee, 0)
  FROM fills f JOIN live_orders o ON o.kalshi_order_id = f.kalshi_order_id
 WHERE o.strategy = %(tag)s
"""

SETTLED_SQL = """
SELECT DISTINCT ON (market_ticker) market_ticker, realized_pnl
  FROM positions
 WHERE market_ticker = ANY(%(tickers)s) AND raw_json ? 'settled_time'
 ORDER BY market_ticker, captured_at DESC
"""


# ---------------------------------------------------------------------------------------------
# pure helpers (unit-tested in tests/test_limm_exit_vs_hold.py)


def fill_settle_pnl(fill_action: str, side: str, price: int, qty: float, fee: float,
                    result: str) -> float:
    """Dollars one fill contributes when held to settlement. A `sell` fill disposes of `side`
    at `price`; anything else acquires it (Kalshi books a YES sell as a NO acquisition)."""
    won = 100.0 if (side or "").lower() == result else 0.0
    if (fill_action or "").lower() == "sell":
        return qty * (price - won) / 100.0 - fee
    return qty * (won - price) / 100.0 - fee


def market_view(fills: list[tuple], result: str, realized: float | None) -> dict | None:
    """Per-market hold, actual and exit value. `fills` rows:
    (order_action, fill_action, side, price, qty, fee)."""
    entries = [f for f in fills if (f[0] or "").lower() == "buy"]
    exits = [f for f in fills if (f[0] or "").lower() != "buy"]
    if not entries or result not in ("yes", "no"):
        return None
    hold = sum(fill_settle_pnl(fa, s, int(p), float(q), float(fee), result)
               for _oa, fa, s, p, q, fee in entries)
    exit_fills = sum(fill_settle_pnl(fa, s, int(p), float(q), float(fee), result)
                     for _oa, fa, s, p, q, fee in exits)
    entry_px = [int(p) for _oa, _fa, _s, p, _q, _f in entries]
    kind = "none"
    if exits:
        # An exit books as the opposite side at (100 - sale price) unless the row says `sell`;
        # normalize to the sale price of the held side before comparing with the entry.
        sale = [int(p) if (fa or "").lower() == "sell" else 100 - int(p)
                for _oa, fa, _s, p, _q, _f in exits]
        kind = "stop" if (sum(sale) / len(sale)) < (sum(entry_px) / len(entry_px)) else "take"
    actual = realized if realized is not None else None
    return {"hold": hold, "actual": actual,
            "exit_value": None if actual is None else actual - hold,
            "exit_value_fills": exit_fills, "kind": kind}


def cluster_mean(vals: list[tuple[str, float]]) -> dict | None:
    n = len(vals)
    if not n:
        return None
    mean = sum(v for _, v in vals) / n
    res: dict[str, float] = defaultdict(float)
    for g, v in vals:
        res[g] += v - mean
    k = len(res)
    se = math.sqrt(k / (k - 1) * sum(x * x for x in res.values())) / n if k > 1 else float("nan")
    return {"n": n, "events": k, "mean": mean, "se": se, "total": sum(v for _, v in vals)}


def verdict(s: dict | None) -> str:
    if s is None or s["n"] < MIN_MARKETS or s["events"] < MIN_EVENTS or math.isnan(s["se"]):
        return "HOLD (accrual)"
    if s["mean"] - Z * s["se"] > 0:
        return "PASS — exits add value"
    if s["mean"] + Z * s["se"] < 0:
        return "FAIL — exits cost money; holding / a looser stop is nominated for an owner decision"
    return "HOLD"


def _f(x, spec="{:+.2f}"):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else spec.format(x)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tag", default=LIVE_TAG)
    args = ap.parse_args(argv)

    import psycopg
    from mmsell_flow_veto_probe import RO_OPTIONS, _to_libpq_url
    from mmsell_selection_audit import _result, fetch_markets

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO not set", file=sys.stderr)
        return 1
    print(f"LIMM exits vs holding to settlement — {args.tag} (pre-registered "
          "docs/LIMM_PROGRAMME_OUTCOMES.md §9)\n")
    by_mkt: dict[str, list[tuple]] = defaultdict(list)
    with psycopg.connect(url, options=RO_OPTIONS) as conn, conn.cursor() as cur:
        cur.execute(FILLS_SQL, {"tag": args.tag})
        for tk, oa, fa, side, price, qty, fee in cur.fetchall():
            by_mkt[tk].append((oa, fa, side, price or 0, qty or 0, fee or 0))
        cur.execute(SETTLED_SQL, {"tickers": list(by_mkt)})
        realized = {t: float(p) for t, p in cur.fetchall() if p is not None}
    settled_tk = sorted(t for t in by_mkt if t in realized)
    markets = fetch_markets(settled_tk)
    rows = []
    for tk in settled_tk:
        v = market_view(by_mkt[tk], _result(markets.get(tk) or {}), realized.get(tk))
        if v is not None:
            v["ticker"], v["event"] = tk, tk.rsplit("-", 1)[0]
            rows.append(v)
    print(f"markets with fills: {len(by_mkt)}; settled with a position row: {len(settled_tk)}; "
          f"scored: {len(rows)}")

    prim = cluster_mean([(r["event"], r["exit_value"]) for r in rows if r["exit_value"] is not None])
    sec = cluster_mean([(r["event"], r["exit_value_fills"]) for r in rows])
    tot_actual = sum(r["actual"] for r in rows if r["actual"] is not None)
    tot_hold = sum(r["hold"] for r in rows)
    print(f"\n  actual (Kalshi realized): ${tot_actual:+.2f}   held to settlement: ${tot_hold:+.2f}")
    print(f"  exit value per market: {_f(prim and prim['mean'])} ± {_f(prim and prim['se'])} "
          f"({(prim or {}).get('n', 0)} markets, {(prim or {}).get('events', 0)} events), "
          f"total {_f(prim and prim['total'])}")
    print(f"  instrument check (from exit fills): {_f(sec and sec['mean'])} ± "
          f"{_f(sec and sec['se'])}, total {_f(sec and sec['total'])}")
    print("\n== descriptive: by exit kind")
    for kind in ("stop", "take", "none"):
        rs = [r for r in rows if r["kind"] == kind and r["exit_value"] is not None]
        s = cluster_mean([(r["event"], r["exit_value"]) for r in rs])
        print(f"  {kind:5s} {len(rs):4d} markets  exit value {_f(s and s['mean'])} ± "
              f"{_f(s and s['se'])}  total {_f(s and s['total'])}")
    if rows:
        wa = min((r for r in rows if r["actual"] is not None), key=lambda r: r["actual"],
                 default=None)
        wh = min(rows, key=lambda r: r["hold"])
        print(f"\n  worst market, actual: {wa and wa['ticker']} {_f(wa and wa['actual'])}; "
              f"worst market, held: {wh['ticker']} {_f(wh['hold'])}")
    print(f"\n== VERDICT\n  H1: {verdict(prim)}\n  Authorizes nothing; a live risk-rule change "
          "is an owner decision recorded as an epoch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
