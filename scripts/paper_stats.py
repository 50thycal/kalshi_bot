"""Read-only paper-trading performance report.

Usage (from the repo root, or anywhere with DATABASE_URL set):

    DATABASE_URL=postgresql://... python scripts/paper_stats.py

On Railway, run it as a one-off command against the same DATABASE_URL the worker uses.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import case, func, select  # noqa: E402

from kalshi_bot import db  # noqa: E402
from kalshi_bot import models as m  # noqa: E402
from kalshi_bot.config import normalize_database_url  # noqa: E402

CLOSED = ("settled", "closed_timeout", "closed_tp", "closed_sl", "closed_void")


def _money(x) -> str:
    return f"${float(x or 0):,.2f}"


def main() -> int:
    url = normalize_database_url(os.environ.get("DATABASE_URL"))
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1
    db.init_engine(url)

    with db.session_scope() as s:
        print("=== Performance by strategy ===")
        strat_rows = s.execute(
            select(
                m.PaperTrade.strategy,
                func.count(),
                func.coalesce(func.sum(m.PaperTrade.pnl), 0),
                func.sum(case((m.PaperTrade.pnl > 0, 1), else_=0)),
            )
            .where(m.PaperTrade.status.in_(CLOSED))
            .group_by(m.PaperTrade.strategy)
            .order_by(func.coalesce(func.sum(m.PaperTrade.pnl), 0).desc())
        ).all()
        if not strat_rows:
            print("  (no closed trades yet)")
        for strat, closed, pnl, wins in strat_rows:
            wr = f"{(int(wins or 0) / closed * 100):.0f}%" if closed else "n/a"
            print(f"  {str(strat or 'unknown'):14s} closed={closed:4d}  pnl={_money(pnl)}  win={wr}")

        print("\n=== Paper trades by status ===")
        for status, count, pnl in s.execute(
            select(m.PaperTrade.status, func.count(), func.coalesce(func.sum(m.PaperTrade.pnl), 0))
            .group_by(m.PaperTrade.status)
            .order_by(m.PaperTrade.status)
        ).all():
            print(f"  {status or 'None':16s} {count:6d}  pnl={_money(pnl)}")

        closed = [
            float(p or 0)
            for p in s.scalars(
                select(m.PaperTrade.pnl).where(m.PaperTrade.status.in_(CLOSED))
            ).all()
        ]
        wins = sum(1 for p in closed if p > 0)
        print("\n=== Realized ===")
        print(f"  closed trades : {len(closed)}")
        print(f"  realized P&L  : {_money(sum(closed))}")
        print(f"  win rate      : {(wins / len(closed) * 100):.1f}%" if closed else "  win rate      : n/a")

        open_pos = s.scalars(
            select(m.PaperPosition).where(m.PaperPosition.status == "open")
        ).all()
        open_unreal = sum(float(p.unrealized_pnl or 0) for p in open_pos)
        print("\n=== Open positions ===")
        print(f"  open positions  : {len(open_pos)}")
        print(f"  open unrealized : {_money(open_unreal)}")

        filled = (
            s.scalar(select(func.count()).select_from(m.PaperTrade).where(m.PaperTrade.status != "no_fill"))
            or 0
        )
        no_fill = (
            s.scalar(select(func.count()).select_from(m.PaperTrade).where(m.PaperTrade.status == "no_fill"))
            or 0
        )
        denom = filled + no_fill
        print("\n=== Fillability ===")
        if denom:
            print(f"  filled={filled} no_fill={no_fill} rate={(filled / denom * 100):.1f}%")
        else:
            print("  no trades yet")

        print("\n=== Realized P&L by category (closed trades) ===")
        cat_rows = s.execute(
            select(m.Market.category, func.count(), func.coalesce(func.sum(m.PaperTrade.pnl), 0))
            .join(m.Market, m.Market.ticker == m.PaperTrade.market_ticker)
            .where(m.PaperTrade.status.in_(CLOSED))
            .group_by(m.Market.category)
            .order_by(func.coalesce(func.sum(m.PaperTrade.pnl), 0))
        ).all()
        if not cat_rows:
            print("  (no closed trades yet)")
        for cat, count, pnl in cat_rows:
            print(f"  {str(cat or 'uncategorized'):24s} {count:5d}  pnl={_money(pnl)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
