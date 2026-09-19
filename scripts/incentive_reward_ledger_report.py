#!/usr/bin/env python3
"""Read the reward ledger: has anything credited us that no trade explains?

This is the report for the one number the liquidity-incentive thesis turns on and cannot read
directly. Kalshi publishes a programme's terms — pool, target size, discount factor, whether it
has paid out — and never our share of them, so the reward is recovered as the part of a balance
change that no fill and no settlement accounts for (`liquidity_incentive/reward_ledger.py`).

Read-only, stdlib + psycopg, exactly like every other allowlisted ops analysis. The ops runner
installs `psycopg[binary]` and NOTHING ELSE for a `script` request — no SQLAlchemy, no
`kalshi_bot` package — so this file must not import either. (The first version of this script
did, and failed in the runner with `ModuleNotFoundError: No module named 'sqlalchemy'`; the
convention was there to copy and was not.)

Nothing here writes a row, decides a gate, or concludes that a residual IS a reward — that
judgement belongs against the payout dates of programmes we actually quoted, and it is made by
a person.
"""

from __future__ import annotations

import argparse
import os
import sys

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)

#: Mirrors `reward_ledger.MATERIAL_RESIDUAL_CENTS`. One cent, because that is the smallest unit
#: a balance moves in — the first reward we could ever SEE, whatever Kalshi accrued internally.
MATERIAL_RESIDUAL_CENTS = 1


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def _c(cents) -> str:
    """Cents as dollars, always signed, because the sign is the finding."""
    if cents is None:
        return "-"
    return f"{int(cents) / 100.0:+.2f}"


def _table_exists(cur) -> bool:
    cur.execute("SELECT to_regclass('public.incentive_balance_observations') IS NOT NULL")
    return bool(cur.fetchone()[0])


def report(cur, days: int, limit: int) -> None:
    cur.execute(
        """
        SELECT at, balance_cents, delta_cents, buy_cost_cents, sell_proceeds_cents,
               fees_cents, settlement_cents, residual_cents, presumed_transfer, notes_json
          FROM incentive_balance_observations
         WHERE at >= now() - make_interval(days => %s)
         ORDER BY at DESC, id DESC
         LIMIT %s
        """,
        (days, limit),
    )
    rows = cur.fetchall()

    print("=" * 96)
    print(f"REWARD LEDGER — balance residuals, last {days} days")
    print("residual = Dbalance - settlements - sell proceeds + buy cost + fees")
    print("A residual is a CANDIDATE, not a reward: deposits, withdrawals and unreported fees")
    print("land here too. `flag` says which.")
    print("=" * 96)

    if not rows:
        print("\nNo observations yet. The collector writes one every")
        print("`liquidity_incentive_balance_seconds` (default 900s) once the worker has booted")
        print("on the deployed code. Empty right after a deploy is expected, not a failure.")
        return

    print(f"\n{'at':<21} {'balance$':>10} {'delta$':>9} {'buys$':>8} {'sells$':>8} "
          f"{'fees$':>7} {'settle$':>9} {'RESIDUAL$':>10}  flag")
    print("-" * 96)
    material = []
    for (at, bal, delta, buys, sells, fees, settle, resid, transfer, notes) in rows:
        notes = notes or {}
        if resid is None:
            flag = "anchor (no previous reading)"
        elif transfer:
            flag = "presumed deposit/withdrawal"
        elif notes.get("residual_untrustworthy"):
            keys = ",".join(k for k in notes if k != "residual_untrustworthy")
            flag = f"UNTRUSTWORTHY: {keys}"
        elif abs(int(resid)) >= MATERIAL_RESIDUAL_CENTS:
            flag = "** UNEXPLAINED CASH **"
            material.append(int(resid))
        else:
            flag = "explained"
        print(f"{at:%Y-%m-%d %H:%M:%S}   {int(bal) / 100.0:>10.2f} "
              f"{_c(delta):>9} {_c(buys):>8} {_c(sells):>8} "
              f"{_c(fees):>7} {_c(settle):>9} {_c(resid):>10}  {flag}")

    print("\n" + "=" * 96)
    print("VERDICT")
    print("=" * 96)
    if not material:
        print("\nNo unexplained cash in this window. Every balance change is accounted for by a")
        print("fill or a settlement, so NO liquidity reward has been credited that we can see.")
        print("\nThat is consistent with the thesis rather than a refutation of it: a 1-contract")
        print("bid in a book of tens of thousands is a fraction of a percent of a per-period")
        print("slice, which rounds to $0.00. Evidence the test is too small to earn, not")
        print("evidence the mechanism is absent.")
    else:
        print(f"\n{len(material)} observation(s) carry unexplained cash, "
              f"totalling {_c(sum(material))}.")
        print("\nBefore reading any of it as a liquidity reward, check each window against the")
        print("END DATES of programmes we actually had a resting order in — Kalshi credits only")
        print("after a programme ends. A residual in a window with no programme ending in it is")
        print("something else, and the something else is usually an unreported fee.")
        neg = [r for r in material if r < 0]
        if neg and len(neg) == len(material):
            print("\nNOTE: every material residual here is NEGATIVE. That is the signature of")
            print("fees Kalshi charged but did not report on its fills payload — an accounting")
            print("gap, not income, and the safe failure direction.")
    print()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Reward ledger — balance residuals.")
    ap.add_argument("--days", type=int, default=14, help="window in days (default 14)")
    ap.add_argument("--limit", type=int, default=40, help="max rows (default 40)")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            if not _table_exists(cur):
                # Said plainly rather than raised: right after a merge the worker may not have
                # run the migration yet, and "not deployed yet" must not read as "broken".
                print("`incentive_balance_observations` does not exist yet.")
                print("The migration (b3d7f1a409ce) runs when the worker boots on the deployed")
                print("code. If this persists well past a deploy, THAT is a real failure.")
                return 0
            report(cur, args.days, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
