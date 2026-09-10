"""What a LIVE book actually did with real money — by tag, from the sources that
enforce it.

WHY THIS EXISTS (XOS-000031)
----------------------------
The live path writes SIMULATED `paper_trades` rows under the LIVE tag, including
for orders that NEVER FILLED on the exchange. So the obvious query --

    select sum(pnl) from paper_trades where strategy = '<live tag>'

-- is not this book's real-money P&L. It counts trades real money never got, and
it can read POSITIVE while the book is losing. Measured on the mmsell
contest-cap canary on 2026-09-10: `paper_trades` said +$2.56 (n=194) while the
exchange's own realized P&L for the same tag and window was -$1.41 (settled=123).
The whole $3.97 difference was 70 tickers whose live order never filled.

That is the same class of error as reading a `paper_trades` row count as the
open-position count, which is enforced by `repository.count_live_book_open`.
Both have now produced a materially wrong operator report.

WHAT IT READS, AND WHY THOSE SOURCES
------------------------------------
Exactly the chains that the enforcing code uses, so the numbers here and the
numbers that bind are the same numbers:

  realized      `positions.realized_pnl` on the newest snapshot per ticker with
                quantity == 0 -- the exchange's own figure, net of the fees
                Kalshi actually charged, written by the reconcile loop. Same
                column `repository.live_realized_pnl_today` feeds to the
                `max_daily_loss` breaker, and same chain the live dashboard
                shows (`kalshi_bot/livedash/legs.py`).
  open count    `repository.count_live_book_open` semantics: distinct tickers
                with a committed live BUY, minus those whose newest position
                snapshot is flat. A resting order counts as open. This is the
                quantity `MMSELL_LIVE_MAX_OPEN_POSITIONS` bounds.
  attribution   there is no strategy column on `fills` or `positions`, so it
                runs through `live_orders.strategy` -> `fills` on
                `kalshi_order_id` -> newest `positions` snapshot per ticker.

The `paper_trades` figure IS printed -- split into the filled and never-filled
halves -- because the point is to make the trap visible rather than to hide the
number someone would otherwise reach for.

The never-filled half is measured over EVERY buy the book placed, whatever became
of the order. An mmsell order that fails to fill ordinarily rests and is then
CANCELLED, so restricting that set to committed statuses would drop the very
trades the phantom is counting. The open count is the one figure that uses the
narrower committed set, because that is what `count_live_book_open` bounds.

WHAT IT DOES NOT DO
-------------------
No unrealized P&L. `positions.unrealized_pnl` is never written by the live path,
and deriving a mark here would be a THIRD implementation of something the
dashboard already does off the shared tick. Open cost basis is printed instead:
what the book PAID for what it still holds, which is exact.

A `--twin` figure is the twin's own `paper_trades`, which is correct for a paper
tag. The gap between it and live realized is not "slippage": most of it is
usually selection at the fill, and the filled/never-filled split below is what
tells them apart.

Read-only (stdlib + psycopg):

    DATABASE_URL_RO=postgresql://... python scripts/live_book_truth.py \
        --tag Fmmsell10 --twin Fmmsell10_pt4
    # or:  {"type": "script", "name": "live_book_truth",
    #       "args": ["--tag", "Fmmsell10"]}
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=60000 "
    "-c idle_in_transaction_session_timeout=60000"
)

#: MIRRORS `repository.LIVE_NONTERMINAL_STATUSES + ("filled",)` -- the set
#: `count_live_book_open` treats as a committed live BUY. It is duplicated
#: rather than imported because ops scripts run on a runner that never installs
#: the `kalshi_bot` package (and `repository` pulls in SQLAlchemy). Drift is
#: caught by `tests/test_live_book_truth.py`, which imports both and compares.
COMMITTED_BUY_STATUSES = (
    "pending", "unknown", "submitted", "resting", "partial", "filled",
)

#: `count_live_book_open` treats |qty| <= this as settled/flat.
FLAT_EPSILON = 0.01


def _fmt(value: float | None, width: int = 9) -> str:
    return "     n/a" if value is None else f"{value:>{width}.4f}"


def _epoch_start(cur, tag: str):
    """The twin epoch's `started_at`, which scopes BOTH legs to the same window.

    Returns (started_at, twin_tag) or (None, None). Without it every figure here
    would silently pool a previous epoch's trades with this one's.
    """
    cur.execute(
        """
        select started_at, twin_tag
          from live_paper_twins
         where live_tag = %s and ended_at is null
         order by started_at desc
         limit 1
        """,
        (tag,),
    )
    row = cur.fetchone()
    return (row[0], row[1]) if row else (None, None)


def _attempted_tickers(cur, tag: str, since):
    """EVERY ticker this book placed a live buy on, whatever became of the order.

    Deliberately unfiltered by status, and that is the whole point of the
    never-filled half: the ordinary way an mmsell order fails to fill is that it
    rests and is then CANCELLED, so filtering to `COMMITTED_BUY_STATUSES` here
    would drop exactly the trades the phantom measures and report n=0 — which the
    first production run did, on a book with 65 cancelled orders.
    """
    cur.execute(
        """
        select distinct market_ticker
          from live_orders
         where strategy = %s
           and action = 'buy'
           and (%s::timestamptz is null or created_at >= %s::timestamptz)
        """,
        (tag, since, since),
    )
    return {r[0] for r in cur.fetchall()}


def _committed_tickers(cur, tag: str, since):
    """The narrower set `count_live_book_open` counts: a committed live buy.

    `COMMITTED_BUY_STATUSES` excludes `canceled` and `rejected` on purpose — a
    cancelled order holds nothing, so it cannot occupy a slot under the open cap.
    """
    cur.execute(
        """
        select distinct market_ticker
          from live_orders
         where strategy = %s
           and action = 'buy'
           and status = any(%s)
           and (%s::timestamptz is null or created_at >= %s::timestamptz)
        """,
        (tag, list(COMMITTED_BUY_STATUSES), since, since),
    )
    return {r[0] for r in cur.fetchall()}


def _filled(cur, tag: str, since):
    """ticker -> (contracts, entry fees) for orders that ACTUALLY filled.

    Joined through `kalshi_order_id` because that is the only link between a
    fill and the strategy that placed it.
    """
    cur.execute(
        """
        select f.market_ticker, sum(f.quantity), sum(coalesce(f.fee, 0))
          from fills f
          join live_orders o on o.kalshi_order_id = f.kalshi_order_id
         where o.strategy = %s
           and o.action = 'buy'
           and (%s::timestamptz is null or o.created_at >= %s::timestamptz)
         group by f.market_ticker
        """,
        (tag, since, since),
    )
    return {t: (float(q or 0), float(fee or 0)) for t, q, fee in cur.fetchall()}


def _snapshots(cur, tickers: set[str]):
    """Newest `positions` row per ticker: (qty, realized_pnl, cost basis).

    Snapshots accumulate every reconcile cycle, so only the newest is the
    current truth -- summing them would count one market many times.
    """
    if not tickers:
        return {}
    cur.execute(
        """
        select distinct on (market_ticker)
               market_ticker,
               coalesce(quantity_fp, quantity::numeric),
               realized_pnl,
               market_exposure
          from positions
         where market_ticker = any(%s)
         order by market_ticker, captured_at desc
        """,
        (list(tickers),),
    )
    return {
        t: (None if q is None else float(q),
            None if r is None else float(r),
            None if e is None else abs(float(e)))
        for t, q, r, e in cur.fetchall()
    }


def _paper_pnl(cur, tag: str, since, tickers: set[str] | None = None):
    """(sum(pnl), n) over a PAPER tag's closed trades, optionally on a ticker set."""
    sql = """
        select coalesce(sum(pnl), 0), count(*)
          from paper_trades
         where strategy = %s
           and pnl is not null
           and (%s::timestamptz is null or closed_at >= %s::timestamptz)
    """
    params: list = [tag, since, since]
    if tickers is not None:
        if not tickers:
            return 0.0, 0
        sql += " and market_ticker = any(%s)"
        params.append(list(tickers))
    cur.execute(sql, params)
    total, n = cur.fetchone()
    return float(total or 0), int(n or 0)


def _breaker_today(cur):
    """What `repository.live_realized_pnl_today` computes: newest snapshot per
    ticker among rows captured since UTC midnight, summed. PORTFOLIO-wide, not
    per book -- `max_daily_loss` is a portfolio breaker and reading it as this
    book's day would understate what is left before it trips.
    """
    cur.execute(
        """
        with latest as (
            select distinct on (market_ticker) market_ticker, realized_pnl
              from positions
             where captured_at >= date_trunc('day', now() at time zone 'utc')
               and realized_pnl is not null
             order by market_ticker, captured_at desc
        )
        select coalesce(sum(realized_pnl), 0), count(*) from latest
        """
    )
    total, n = cur.fetchone()
    return float(total or 0), int(n or 0)


def report(cur, tag: str, twin: str | None, since) -> int:
    epoch_since, epoch_twin = _epoch_start(cur, tag)
    if since is None:
        since = epoch_since
    twin = twin or epoch_twin

    attempted = _attempted_tickers(cur, tag, since)
    committed = _committed_tickers(cur, tag, since)
    filled = _filled(cur, tag, since)
    snaps = _snapshots(cur, attempted)

    realized = 0.0
    settled = 0
    open_cost = 0.0
    for ticker in filled:
        qty, pnl, exposure = snaps.get(ticker, (None, None, None))
        if qty is not None and abs(qty) <= FLAT_EPSILON and pnl is not None:
            realized += pnl
            settled += 1
        elif exposure is not None:
            open_cost += exposure

    # count_live_book_open: committed, minus those whose newest snapshot is flat.
    open_count = sum(
        1 for t in committed
        if not (snaps.get(t, (None, None, None))[0] is not None
                and abs(snaps[t][0]) <= FLAT_EPSILON)
    )

    fees = sum(fee for _q, fee in filled.values())
    contracts = sum(q for q, _fee in filled.values())
    unfilled = attempted - set(filled)

    pt_all = _paper_pnl(cur, tag, since)
    pt_filled = _paper_pnl(cur, tag, since, set(filled))
    pt_unfilled = _paper_pnl(cur, tag, since, unfilled)
    day_total, day_n = _breaker_today(cur)

    since_s = since.isoformat() if isinstance(since, dt.datetime) else str(since)
    print(f"\n=== live book truth · {tag} ===")
    print(f"epoch since : {since_s}"
          f"{'  (live_paper_twins)' if epoch_since and since == epoch_since else ''}")
    print(f"twin        : {twin or '(none linked)'}")

    print("\nREAL MONEY (exchange truth, positions.realized_pnl)")
    print(f"  realized              : {_fmt(realized)}   settled={settled}")
    print(f"  entry fees paid       : {_fmt(fees)}")
    print(f"  open cost basis       : {_fmt(open_cost)}   (what it PAID for what it still holds)")
    print(f"  open by the cap rule  : {open_count:>9}   (count_live_book_open semantics)")
    print(f"  contracts filled      : {contracts:>9.0f}")

    rate = (len(filled) / len(attempted) * 100) if attempted else 0.0
    print("\nEXECUTION")
    print(f"  tickers ordered       : {len(attempted):>9}   (any status — a cancelled rest counts)")
    print(f"  tickers filled        : {len(filled):>9}   ({rate:.1f}%)")
    print(f"  NEVER filled          : {len(unfilled):>9}")

    print("\npaper_trades UNDER THE LIVE TAG — simulated, NOT real money")
    print(f"  all rows              : {_fmt(pt_all[0])}   n={pt_all[1]}")
    print(f"  on tickers that filled: {_fmt(pt_filled[0])}   n={pt_filled[1]}")
    print(f"  on NEVER-filled       : {_fmt(pt_unfilled[0])}   n={pt_unfilled[1]}   <- phantom")
    gap = pt_all[0] - realized
    print(f"  overstates real money by {gap:+.4f}")
    stray = pt_all[1] - pt_filled[1] - pt_unfilled[1]
    if stray:
        # The two halves must partition every simulated row, or the phantom is
        # understated and the split is lying by omission. That is exactly how the
        # first production run reported n=0 while 76 rows sat outside both sets.
        print(f"  !! {stray} simulated row(s) in NEITHER half — the split is incomplete;"
              " treat the phantom figure as a floor and report this")

    if twin:
        tw_total, tw_n = _paper_pnl(cur, twin, since)
        print(f"\nTWIN {twin} (paper_trades IS the right source for a paper tag)")
        print(f"  realized              : {_fmt(tw_total)}   n={tw_n}")
        print(f"  twin - live           : {tw_total - realized:+.4f}")
        print("  NOTE: most of a gap this shape is SELECTION at the fill, not slippage —"
              " compare the two halves of the split above before calling it execution cost.")

    print("\nmax_daily_loss BREAKER INPUT (live_realized_pnl_today, PORTFOLIO-wide)")
    print(f"  realized today        : {_fmt(day_total)}   markets={day_n}")
    print("  (compare against MAX_DAILY_LOSS; this is every live book, not just this one)")

    print("\n--- read this before quoting a number ---")
    print(f"REAL MONEY for {tag} is {realized:+.4f} (settled={settled}).")
    print(f"paper_trades under the same tag says {pt_all[0]:+.4f} (n={pt_all[1]}); "
          f"{pt_unfilled[1]} of those trades never filled live. See XOS-000031.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tag", required=True, help="the LIVE strategy tag")
    ap.add_argument("--twin", default=None,
                    help="paper twin tag (default: the open live_paper_twins row)")
    ap.add_argument("--since", default=None,
                    help="ISO timestamp; default is the twin epoch's started_at")
    args = ap.parse_args(argv)

    url = os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL_RO is required", file=sys.stderr)
        return 2

    since = None
    if args.since:
        since = dt.datetime.fromisoformat(args.since)
        if since.tzinfo is None:
            since = since.replace(tzinfo=dt.timezone.utc)

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS) as conn, conn.cursor() as cur:
        return report(cur, args.tag, args.twin, since)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
