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
  unrealized    `abs(quantity) x (no_bid - avg_price) / 100`, marked off the
                newest `mmsell_position_ticks` row -- the same tape and the same
                arithmetic the dashboard uses, so the two agree. `positions.
                unrealized_pnl` is never written by the live path, which is why
                it is derived rather than read.
  attribution   there is no strategy column on `fills` or `positions`, so it
                runs through `live_orders.strategy` -> `fills` on
                `kalshi_order_id` -> newest `positions` snapshot per ticker.

The `paper_trades` figure IS printed -- split into the filled and never-filled
halves -- because the point is to make the trap visible rather than to hide the
number someone would otherwise reach for.

THREE buckets, not two. A simulated row under the live tag falls in exactly one:
the live book FILLED that market, it ORDERED and never filled (lost at the fill,
which is adverse selection -- a resting no-side offer is hit mainly when the
market moves against it), or it NEVER ORDERED the market at all, because a live
gate refused it first: the contest cap, the open-position cap, the price ceiling,
an exposure bound. The last two together are the phantom, and separating them is
the point -- one is what execution costs, the other is what the caps cost, and
they are different questions with different answers.

The ordered/never-filled split is measured over EVERY buy the book placed, whatever
became of the order. An mmsell order that fails to fill ordinarily rests and is then
CANCELLED, so restricting that set to committed statuses would drop the very
trades the phantom is counting. The open count is the one figure that uses the
narrower committed set, because that is what `count_live_book_open` bounds.

WHY TOTAL, NOT JUST REALIZED
----------------------------
The first three versions of this script printed realized P&L only, and every
report built on it disagreed with the dashboard -- which shows realized PLUS
unrealized, and is the number an operator actually reads. On 2026-09-10 that was
the difference between "the book is flat at -$0.02" and the truth, "the book is
down -$2.52 with a $2.50 markdown sitting in ten open positions that will mostly
settle as losses". A figure that cannot be reconciled against the screen is worse
than no figure, so TOTAL is the headline here and the two parts are shown beside
it. Open cost basis is still printed -- what the book PAID is exact, where a mark
is an estimate.

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

#: A missing `positions` snapshot, shaped like one so unpacking never raises.
_NO_SNAP = (None, None, None, None)


def _fmt(value: float | None, width: int = 9) -> str:
    return "n/a".rjust(width) if value is None else f"{value:>{width}.4f}"


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
    """Newest `positions` row per ticker: (qty, realized_pnl, cost basis, entry).

    Snapshots accumulate every reconcile cycle, so only the newest is the
    current truth -- summing them would count one market many times. `avg_price`
    is the cost basis IN CENTS on the held side, and is the entry the dashboard
    marks against; the fill VWAP is a different number and mixing the two makes
    the unrealized figure disagree with the dashboard for no good reason.
    """
    if not tickers:
        return {}
    cur.execute(
        """
        select distinct on (market_ticker)
               market_ticker,
               coalesce(quantity_fp, quantity::numeric),
               realized_pnl,
               market_exposure,
               avg_price
          from positions
         where market_ticker = any(%s)
         order by market_ticker, captured_at desc
        """,
        (list(tickers),),
    )
    return {
        t: (None if q is None else float(q),
            None if r is None else float(r),
            None if e is None else abs(float(e)),
            None if a is None else float(a))
        for t, q, r, e, a in cur.fetchall()
    }


def _marks(cur, tickers: set[str]):
    """ticker -> (no_bid cents, captured_at) from the newest position tick.

    The same tape the dashboard marks off (`mmsell_position_ticks.no_bid`), so
    the two agree by construction. A ticker is taped only while some mmsell book
    holds it, so a mark can legitimately be missing -- which is reported, never
    silently treated as zero or as cost.
    """
    if not tickers:
        return {}
    cur.execute(
        """
        select distinct on (market_ticker) market_ticker, no_bid, captured_at
          from mmsell_position_ticks
         where market_ticker = any(%s) and no_bid is not null
         order by market_ticker, captured_at desc
        """,
        (list(tickers),),
    )
    return {t: (float(b), at) for t, b, at in cur.fetchall()}


def _paper_pnl(cur, tag: str, since, tickers: set[str] | None = None, *, exclude=False):
    """(sum(pnl), n) over a PAPER tag's closed trades, optionally on a ticker set.

    `exclude=True` inverts the set: rows on tickers OUTSIDE it. That is how the
    third bucket -- trades the live executor never ordered at all -- is measured,
    since there is no positive list of "markets a gate refused".
    """
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
            if not exclude:
                return 0.0, 0
        else:
            sql += (" and not (market_ticker = any(%s))" if exclude
                    else " and market_ticker = any(%s)")
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

    still_open = {
        t for t in filled
        if (snaps.get(t, _NO_SNAP)[0] is None
            or abs(snaps[t][0]) > FLAT_EPSILON)
    }
    marks = _marks(cur, still_open)

    realized = 0.0
    settled = 0
    open_cost = 0.0
    unrealized = 0.0
    unmarked = 0
    marked_positions: list[tuple[float, str, float, float]] = []
    for ticker in filled:
        qty, pnl, exposure, entry = snaps.get(ticker, _NO_SNAP)
        if qty is not None and abs(qty) <= FLAT_EPSILON and pnl is not None:
            realized += pnl
            settled += 1
            continue
        if exposure is not None:
            open_cost += exposure
        mark = marks.get(ticker)
        if mark is None or entry is None or qty is None:
            unmarked += 1
            continue
        # abs(qty): `positions.quantity` is SIGNED and a NO position is negative,
        # so using it raw flips the sign of every open position and turns a
        # marked-down book into a marked-up one. The dashboard takes abs() here
        # (livedash/legs.py) and so does this.
        pnl_open = abs(qty) * (mark[0] - entry) / 100.0
        unrealized += pnl_open
        marked_positions.append((pnl_open, ticker, entry, mark[0]))

    # count_live_book_open: committed, minus those whose newest snapshot is flat.
    open_count = sum(
        1 for t in committed
        if not (snaps.get(t, _NO_SNAP)[0] is not None
                and abs(snaps[t][0]) <= FLAT_EPSILON)
    )

    fees = sum(fee for _q, fee in filled.values())
    contracts = sum(q for q, _fee in filled.values())
    unfilled = attempted - set(filled)

    pt_all = _paper_pnl(cur, tag, since)
    pt_filled = _paper_pnl(cur, tag, since, set(filled))
    pt_unfilled = _paper_pnl(cur, tag, since, unfilled)
    pt_unordered = _paper_pnl(cur, tag, since, attempted, exclude=True)
    day_total, day_n = _breaker_today(cur)

    since_s = since.isoformat() if isinstance(since, dt.datetime) else str(since)
    print(f"\n=== live book truth · {tag} ===")
    print(f"epoch since : {since_s}"
          f"{'  (live_paper_twins)' if epoch_since and since == epoch_since else ''}")
    print(f"twin        : {twin or '(none linked)'}")

    total = realized + unrealized
    print("\nREAL MONEY (exchange truth, positions.realized_pnl)")
    print(f"  realized              : {_fmt(realized)}   settled={settled}")
    print(f"  unrealized            : {_fmt(unrealized)}   marked={len(marked_positions)}"
          + (f"  UNMARKED={unmarked}" if unmarked else ""))
    print(f"  TOTAL                 : {_fmt(total)}   <- the figure the dashboard shows")
    if unmarked:
        print(f"  !! {unmarked} open position(s) have no mark, so TOTAL is INCOMPLETE —"
              " a ticker is taped only while an mmsell book holds it")
    print(f"  entry fees paid       : {_fmt(fees)}")
    print(f"  open cost basis       : {_fmt(open_cost)}   (what it PAID for what it still holds)")
    print(f"  open by the cap rule  : {open_count:>9}   (count_live_book_open semantics)")
    print(f"  contracts filled      : {contracts:>9.0f}")
    if marked_positions:
        print("  open positions, worst first (entry -> no_bid):")
        for pnl_open, ticker, entry, mark_c in sorted(marked_positions)[:8]:
            print(f"    {_fmt(pnl_open, 8)}  {ticker[:44]:<44} {entry:.0f}c -> {mark_c:.0f}c")

    rate = (len(filled) / len(attempted) * 100) if attempted else 0.0
    print("\nEXECUTION")
    print(f"  tickers ordered       : {len(attempted):>9}   (any status — a cancelled rest counts)")
    print(f"  tickers filled        : {len(filled):>9}   ({rate:.1f}%)")
    print(f"  NEVER filled          : {len(unfilled):>9}")

    phantom = pt_unfilled[0] + pt_unordered[0]
    print("\npaper_trades UNDER THE LIVE TAG — simulated, NOT real money")
    print(f"  all rows              : {_fmt(pt_all[0])}   n={pt_all[1]}")
    print(f"  live FILLED it        : {_fmt(pt_filled[0])}   n={pt_filled[1]}")
    print(f"  ordered, NEVER filled : {_fmt(pt_unfilled[0])}   n={pt_unfilled[1]}"
          "   <- lost AT THE FILL")
    print(f"  NEVER ordered         : {_fmt(pt_unordered[0])}   n={pt_unordered[1]}"
          "   <- a live GATE refused it")
    print(f"  phantom total         : {_fmt(phantom)}   (the two the book never had)")
    # Against TOTAL, not realized: the headline above is TOTAL, and an
    # overstatement measured against a different denominator is unreadable.
    gap = pt_all[0] - total
    print(f"  overstates real money by {gap:+.4f}   (vs TOTAL)")
    stray = pt_all[1] - pt_filled[1] - pt_unfilled[1] - pt_unordered[1]
    if stray:
        # The three buckets must partition every simulated row, or the phantom is
        # understated and the split is lying by omission. That is how the first
        # production run reported n=0 while 76 rows sat outside every set, and how
        # the second surfaced the never-ordered bucket this line now measures.
        print(f"  !! {stray} simulated row(s) in NO bucket — the split is incomplete;"
              " treat the phantom figure as a floor and report this")

    if twin:
        tw_total, tw_n = _paper_pnl(cur, twin, since)
        print(f"\nTWIN {twin} (paper_trades IS the right source for a paper tag)")
        print(f"  realized              : {_fmt(tw_total)}   n={tw_n}")
        print(f"  twin - live           : {tw_total - realized:+.4f}"
              "   (realized vs realized — the twin's open book is not marked here)")
        print("  NOTE: a gap this shape is not slippage. Read the three buckets above:"
              " what live ORDERED and lost is execution; what it NEVER ORDERED is what"
              " the caps and gates cost. They are different questions.")

    print("\nmax_daily_loss BREAKER INPUT (live_realized_pnl_today, PORTFOLIO-wide)")
    print(f"  realized today        : {_fmt(day_total)}   markets={day_n}")
    print("  (compare against MAX_DAILY_LOSS; this is every live book, not just this one)")

    print("\n--- read this before quoting a number ---")
    print(f"REAL MONEY for {tag} is {total:+.4f} TOTAL "
          f"({realized:+.4f} realized over {settled} settled, {unrealized:+.4f} unrealized "
          f"on {len(marked_positions)} still open).")
    print(f"paper_trades under the same tag says {pt_all[0]:+.4f} (n={pt_all[1]}); "
          f"{pt_unfilled[1] + pt_unordered[1]} of those trades real money never had "
          f"({pt_unfilled[1]} never filled, {pt_unordered[1]} never ordered). "
          "See XOS-000031.")
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
