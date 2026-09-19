#!/usr/bin/env python3
"""Read the reward ledger: has anything credited us that no trade explains?

This is the report for the one number the liquidity-incentive thesis turns on and cannot read
directly. Kalshi publishes a programme's terms — pool, target size, discount factor, whether it
has paid out — and never our share of them, so the reward is recovered as the part of a balance
change that no fill and no settlement accounts for (`liquidity_incentive/reward_ledger.py`).

Read-only, like every ops analysis: it selects and prints. Nothing here writes a row, decides a
gate or concludes that a residual IS a reward — that judgement belongs against the payout dates
of programmes we actually quoted, and it is made by a person, not by this script.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from kalshi_bot import db  # noqa: E402
from kalshi_bot import models as m  # noqa: E402
from kalshi_bot.liquidity_incentive import reward_ledger as rl  # noqa: E402

DAYS = int(os.environ.get("LEDGER_DAYS", "14"))
LIMIT = int(os.environ.get("LEDGER_LIMIT", "40"))


def _c(cents) -> str:
    """Cents as dollars, always signed, because the sign is the finding."""
    if cents is None:
        return "-"
    return f"{int(cents) / 100.0:+.2f}"


def main() -> int:
    since = datetime.now(timezone.utc) - timedelta(days=DAYS)
    with db.session_scope() as session:
        rows = session.execute(
            select(m.IncentiveBalanceObservation)
            .where(m.IncentiveBalanceObservation.at >= since)
            .order_by(m.IncentiveBalanceObservation.at.desc(),
                      m.IncentiveBalanceObservation.id.desc())
            .limit(LIMIT)
        ).scalars().all()

    print("=" * 96)
    print(f"REWARD LEDGER — balance residuals, last {DAYS} days")
    print("residual = Dbalance - settlements - sell proceeds + buy cost + fees")
    print("A residual is a CANDIDATE, not a reward: deposits, withdrawals and unreported fees")
    print("land here too. `flag` says which.")
    print("=" * 96)

    if not rows:
        print("\nNo observations yet. The collector writes one every "
              "`liquidity_incentive_balance_seconds` (default 900s) once deployed.")
        return 0

    print(f"\n{'at':<21} {'balance$':>10} {'delta$':>9} {'buys$':>8} {'sells$':>8} "
          f"{'fees$':>7} {'settle$':>9} {'RESIDUAL$':>10}  flag")
    print("-" * 96)
    material = []
    for r in rows:
        if r.residual_cents is None:
            flag = "anchor (no previous reading)"
        elif r.presumed_transfer:
            flag = "presumed deposit/withdrawal"
        elif (r.notes_json or {}).get("residual_untrustworthy"):
            flag = "UNTRUSTWORTHY: " + ",".join(
                k for k in (r.notes_json or {}) if k != "residual_untrustworthy")
        elif abs(r.residual_cents) >= rl.MATERIAL_RESIDUAL_CENTS:
            flag = "** UNEXPLAINED CASH **"
            material.append(r)
        else:
            flag = "explained"
        print(f"{r.at:%Y-%m-%d %H:%M:%S}   {int(r.balance_cents) / 100.0:>10.2f} "
              f"{_c(r.delta_cents):>9} {_c(r.buy_cost_cents):>8} {_c(r.sell_proceeds_cents):>8} "
              f"{_c(r.fees_cents):>7} {_c(r.settlement_cents):>9} "
              f"{_c(r.residual_cents):>10}  {flag}")

    print("\n" + "=" * 96)
    print("VERDICT")
    print("=" * 96)
    if not material:
        print("\nNo unexplained cash in this window. Every balance change is accounted for by a")
        print("fill or a settlement, so NO liquidity reward has been credited that we can see.")
        print("\nThat is consistent with the thesis rather than a refutation of it: a 1-contract")
        print("bid in a book of tens of thousands is a fraction of a percent of a per-period")
        print("slice, which rounds to $0.00. It is evidence the test is too small to earn, not")
        print("evidence the mechanism is absent.")
    else:
        total = sum(int(r.residual_cents) for r in material)
        print(f"\n{len(material)} observation(s) carry unexplained cash, totalling {_c(total)}.")
        print("\nBefore reading any of it as a liquidity reward, check each window against the")
        print("END DATES of programmes we actually had a resting order in — Kalshi credits only")
        print("after a programme ends. A residual in a window with no programme ending in it is")
        print("something else, and the something else is usually an unreported fee.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
