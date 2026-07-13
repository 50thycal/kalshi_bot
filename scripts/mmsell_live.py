"""mmsell LIVE scorecard — the execution read the live test exists to produce.

Reports, per mmsell book (mmsell/mmsell1/mmsell2/mmsell3), the metrics paper cannot show:
fill rate & order outcomes, fill economics (price, real fees per contract), realized P&L +
win-rate on SETTLED live positions vs the paper shadow (the adverse-selection detector), and
the current open live footprint. Read-only, self-contained (stdlib + psycopg), so it runs
locally or via the ops channel:

    DATABASE_URL_RO=postgresql://... python scripts/mmsell_live.py
    # or:  {"type": "script", "name": "mmsell_live"}

Everything degrades gracefully to "(none yet)" until the live book has activity, so it's safe
to run before Stage 1 is funded. See docs/MMSELL_LIVE_PLAN.md for the gates it feeds.
"""

from __future__ import annotations

import os
import sys

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=60000 "
    "-c idle_in_transaction_session_timeout=60000"
)

# Kalshi trading fee ceil(0.07*C*P*(1-P)) rounded up to a cent — the paper model, so the live
# fee/contract can be compared against what paper assumed.
LIVE_ORDERS = "live_orders"


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def q(cur, sql: str, params: tuple = ()) -> list[tuple]:
    cur.execute(sql, params)
    return cur.fetchall()


def _pct(num: float, den: float) -> str:
    return f"{num / den * 100:.1f}%" if den else "n/a"


def report_outcomes(cur) -> None:
    print("=== Order outcomes & fill rate (live_orders, mmsell buys) ===")
    rows = q(cur,
             "SELECT strategy, status, count(*) FROM live_orders"
             " WHERE strategy LIKE 'mmsell%%' AND action='buy'"
             " GROUP BY strategy, status ORDER BY strategy, status")
    if not rows:
        print("  (no live mmsell orders yet)")
        return
    by_strat: dict[str, dict[str, int]] = {}
    for strat, status, n in rows:
        by_strat.setdefault(strat, {})[status or "?"] = int(n)
    print(f"  {'book':9s} {'placed':>6} {'filled':>6} {'resting':>7} {'canceled':>8}"
          f" {'rejected':>8} {'fill%':>6}")
    for strat in sorted(by_strat):
        st = by_strat[strat]
        filled = st.get("filled", 0)
        canceled = st.get("canceled", 0)
        rejected = st.get("rejected", 0)
        resting = st.get("resting", 0) + st.get("submitted", 0) + st.get("partial", 0)
        placed = sum(st.values())
        # Fill rate over TERMINAL entry outcomes (filled vs canceled/timed-out); resting still open.
        fillr = _pct(filled, filled + canceled)
        print(f"  {strat:9s} {placed:6d} {filled:6d} {resting:7d} {canceled:8d}"
              f" {rejected:8d} {fillr:>6}")
    print("  (fill% = filled / (filled + canceled); resting orders are still working)")


def report_fills(cur) -> None:
    print("\n=== Fill economics (fills joined to mmsell orders) ===")
    rows = q(cur,
             "SELECT lo.strategy, count(*), sum(f.quantity),"
             " round(avg(f.price)::numeric,1), round(sum(f.fee)::numeric,4),"
             " round((sum(f.fee)/nullif(sum(f.quantity),0))::numeric,4)"
             " FROM fills f JOIN live_orders lo ON lo.kalshi_order_id = f.kalshi_order_id"
             " WHERE lo.strategy LIKE 'mmsell%%'"
             " GROUP BY lo.strategy ORDER BY lo.strategy")
    if not rows:
        print("  (no fills yet)")
        return
    print(f"  {'book':9s} {'fills':>5} {'contracts':>9} {'avg_no_px':>9}"
          f" {'total_fee':>9} {'fee/ct':>7}")
    for strat, nf, contracts, avgpx, totfee, fect in rows:
        print(f"  {strat:9s} {int(nf):5d} {int(contracts or 0):9d} {avgpx or 0:>8}c"
              f" {float(totfee or 0):+8.2f}$ {float(fect or 0):>6.3f}$")
    print("  (paper assumed ~1.0c/contract fee at 1-contract clips — watch fee/ct vs clip size)")


def report_settled(cur) -> None:
    print("\n=== Realized P&L on SETTLED live positions vs paper shadow ===")
    live = q(cur,
             "WITH mm AS (SELECT DISTINCT market_ticker, strategy FROM live_orders"
             "  WHERE strategy LIKE 'mmsell%%' AND action='buy'),"
             " latest AS (SELECT DISTINCT ON (p.market_ticker) p.market_ticker, p.quantity,"
             "   p.realized_pnl FROM positions p JOIN mm ON mm.market_ticker=p.market_ticker"
             "   ORDER BY p.market_ticker, p.captured_at DESC)"
             " SELECT mm.strategy, count(*),"
             "  count(*) FILTER (WHERE l.realized_pnl>0),"
             "  round(sum(l.realized_pnl)::numeric,2), round(avg(l.realized_pnl)::numeric,4)"
             " FROM latest l JOIN mm ON mm.market_ticker=l.market_ticker"
             " WHERE l.quantity=0 AND l.realized_pnl IS NOT NULL"
             " GROUP BY mm.strategy ORDER BY mm.strategy")
    paper = {r[0]: r for r in q(cur,
             "SELECT strategy, count(*), round(avg(pnl)::numeric,4),"
             " round(100.0*avg((pnl>0)::int),1)"
             " FROM paper_trades WHERE strategy LIKE 'mmsell%%' AND status='settled' AND NOT legacy"
             " GROUP BY strategy")}
    if not live:
        print("  (no settled live positions yet — the win-rate/adverse-selection read starts here)")
    else:
        print(f"  {'book':9s} {'n':>4} {'live_win%':>9} {'live_$/ct':>9}"
              f" {'paper_win%':>10} {'paper_$/ct':>10}")
        for strat, n, wins, _tot, avg in live:
            n = int(n)
            p = paper.get(strat)
            p_avg = f"{float(p[2]):+.4f}" if p else "n/a"
            p_win = f"{float(p[3]):.1f}%" if p else "n/a"
            print(f"  {strat:9s} {n:4d} {_pct(int(wins or 0), n):>9} {float(avg or 0):>+8.4f}$"
                  f" {p_win:>10} {p_avg:>10}")
        print("  (live_win% materially below paper_win% is the adverse-selection signature)")


def report_open(cur) -> None:
    print("\n=== Current open live footprint ===")
    rows = q(cur,
             "WITH mm AS (SELECT DISTINCT market_ticker FROM live_orders"
             "  WHERE strategy LIKE 'mmsell%%' AND action='buy'),"
             " latest AS (SELECT DISTINCT ON (p.market_ticker) p.market_ticker, p.quantity_fp,"
             "   p.market_exposure FROM positions p JOIN mm ON mm.market_ticker=p.market_ticker"
             "   ORDER BY p.market_ticker, p.captured_at DESC)"
             " SELECT count(*) FILTER (WHERE abs(coalesce(quantity_fp,0))>0.01),"
             "  round(sum(market_exposure) FILTER (WHERE abs(coalesce(quantity_fp,0))>0.01)::numeric,2)"
             " FROM latest")
    n, exp = (rows[0] if rows else (0, None))
    print(f"  open positions: {int(n or 0)}   capital deployed: {float(exp or 0):.2f}$")


def main(argv: list[str] | None = None) -> int:
    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            report_outcomes(cur)
            report_fills(cur)
            report_settled(cur)
            report_open(cur)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
