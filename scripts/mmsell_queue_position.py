"""QUEUE POSITION — what does a cent of price actually buy on the book?

WHY THIS EXISTS
---------------
`docs/MMSELL_FILL_MODEL.md` says maker adverse selection IS the paper->live gap, worth about
2c/contract, and that we cannot replay it because the books throw away the data a fill model
needs. `docs/MMSELL_OFFSET_AB.md` then spends real money trying to recover one piece of it:
`mmsell10a` rests AT the no-bid, `mmsell10b` rests 1c better, and the two are compared on realized
c/contract.

That comparison cannot finish. Its own gate computes the arithmetic:

    mmsell10b vs mmsell10a: -2.77c/contract  SE 2.20c  95% CI [-7.09, +1.54]
    Detecting +0.5c needs ~47,106 contracts/arm

against roughly 270 contracts/arm actually accrued. It is not a slow experiment, it is an
unanswerable one at our size — and it is being paid for in live capital.

Queue position replaces the inference with an observation. Instead of "did the extra cent produce
more P&L", ask the mechanical question the cent was bought for: **did it move us up the queue, and
past how many contracts?** That is visible on every resting order, every cycle, immediately, and
it accrues whether or not any A/B is armed.

READ IT IN THIS ORDER
---------------------
1. **COVERAGE** first, and treat it as a gate on everything below. `null_rank%` is the share of
   samples where the API answered but we could not read a figure. A high or rising number means
   the payload shape moved under us -- which has now happened TWICE (`orderbook_fp`, then
   `queue_position_fp`), so treat it as likely rather than exotic. Every table after it then
   describes a shrinking, non-random subset. Nothing here is trustworthy until this is near zero.
2. **QUEUE DEPTH BY BOOK** — the direct arm comparison. `p50 ahead` is the answer to what the
   cent bought.
3. **CONTRACTS AHEAD vs FILL** — the payoff. Each order's FIRST observed depth against whether it
   ever filled. This is the P(fill | queue depth) relationship the fill model currently has to
   approximate from price alone.

WHAT THE NUMBER IS
------------------
Kalshi sends ONE fixed-point figure, `queue_position_fp`, and a value like `"2028.55"` settles
what it means: an ordinal rank cannot be fractional, so it is the QUANTITY OF CONTRACTS resting
ahead of us at our price. That is the more useful measure anyway -- rank 3 behind three 1-lots is
a completely different queue from rank 3 behind three 500-lots -- and it is what the tables lead
on. `0.00` is the front of the queue.

WHAT THIS CANNOT TELL YOU
-------------------------
Whether the cent was WORTH it. A better queue position that fills more often is not automatically
good -- the whole adverse-selection finding is that the fills a maker wins are the losers. This
script measures the mechanism (did the cent buy priority); `mmsell_live` and `mmsell_fill_model`
measure whether the resulting fills made money. A promotion needs both.

Read-only, stdlib + psycopg:

    {"type": "script", "name": "mmsell_queue_position"}
    {"type": "script", "name": "mmsell_queue_position", "args": ["--hours", "48"]}
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

# Below this many usable samples a book's percentiles are noise; printed but flagged.
MIN_SAMPLES = 30


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def _pct(x) -> str:
    return f"{x * 100:.1f}%" if x is not None else "n/a"


def _num(x, nd=1) -> str:
    return f"{float(x):.{nd}f}" if x is not None else "n/a"


def _coverage(cur, hours: int) -> bool:
    """Returns True if there is anything worth reporting below."""
    cur.execute(
        "SELECT count(*) AS samples,"
        "       count(*) FILTER (WHERE queue_position IS NOT NULL) AS with_rank,"
        "       count(*) FILTER (WHERE contracts_ahead IS NOT NULL) AS with_ahead,"
        "       count(DISTINCT kalshi_order_id) AS orders,"
        "       min(captured_at)::text, max(captured_at)::text"
        " FROM live_order_queue_ticks"
        f" WHERE captured_at >= now() - interval '{int(hours)} hours'",
    )
    samples, with_rank, with_ahead, orders, first, last = cur.fetchone()
    print(f"=== COVERAGE (last {hours}h) ===")
    if not samples:
        print("  no queue samples yet.")
        print("  Expected while: LIVE_QUEUE_POSITION_SAMPLING=false, no book is live, or nothing")
        print("  is resting. If a live maker book IS running with orders resting, this is a")
        print("  FAULT — check the worker log for 'queue-position'.")
        return False
    null_rank = 1 - (with_rank / samples)
    print(f"  samples {samples}   orders {orders}   window {first} -> {last}")
    print(f"  usable rank {with_rank} ({_pct(with_rank / samples)})"
          f"   contracts_ahead {with_ahead} ({_pct(with_ahead / samples)})")
    if null_rank > 0.10:
        print(f"  !! {_pct(null_rank)} of samples have NO readable rank. Kalshi's payload shape")
        print("     may have changed (it has before — see live/queue_position.py). Every table")
        print("     below is then a non-random subset. Inspect live_order_queue_ticks.raw_json")
        print("     on a null row before believing anything here.")
    elif null_rank > 0:
        print(f"  ({_pct(null_rank)} unreadable — normal if orders fill or cancel mid-sample)")
    return with_rank > 0


def _by_book(cur, hours: int) -> None:
    print("\n=== QUEUE DEPTH BY BOOK — what the offset actually buys ===")
    cur.execute(
        # Percentiles over `contracts_ahead` — the semantic column. `queue_position` carries the
        # same figure and is used only as the "was this sample readable at all" filter, so the
        # two can never disagree about which rows counted.
        "SELECT strategy, count(*) AS n, count(DISTINCT kalshi_order_id) AS orders,"
        "       round(avg(limit_price)::numeric,1) AS avg_px,"
        "       percentile_disc(0.5) WITHIN GROUP (ORDER BY contracts_ahead) AS p50_ahead,"
        "       percentile_disc(0.9) WITHIN GROUP (ORDER BY contracts_ahead) AS p90_ahead,"
        "       count(*) FILTER (WHERE contracts_ahead = 0) AS at_front"
        " FROM live_order_queue_ticks"
        f" WHERE captured_at >= now() - interval '{int(hours)} hours'"
        "   AND queue_position IS NOT NULL AND contracts_ahead IS NOT NULL"
        " GROUP BY 1 ORDER BY 1",
    )
    rows = cur.fetchall()
    if not rows:
        print("  (no samples carry a readable contracts-ahead figure)")
        return
    print(f"  {'book':11s} {'n':>6} {'orders':>7} {'avg_px':>7} {'p50 ahead':>10}"
          f" {'p90 ahead':>10} {'at front':>9}")
    for strat, n, orders, avg_px, p50, p90, front in rows:
        flag = "" if n >= MIN_SAMPLES else "  (thin)"
        print(f"  {str(strat)[:11]:11s} {n:>6} {orders:>7} {_num(avg_px):>7}"
              f" {_num(p50, 0):>10} {_num(p90, 0):>10} {_pct(front / n):>9}{flag}")
    print("  ('ahead' is CONTRACTS that must trade before you do, not an ordinal rank.")
    print("   Fewer contracts ahead, bought by an improving offset, is the mechanism the offset")
    print("   A/B is trying to price; this measures it directly instead of through P&L.")
    print("   'at front' is the share of samples with nothing ahead at all — first in line.)")


def _rank_vs_fill(cur, hours: int) -> None:
    """P(fill | first observed rank). The relationship mmsell_fill_model currently approximates
    from entry price alone, because this data did not exist."""
    print("\n=== CONTRACTS AHEAD vs FILL — P(fill | queue depth when first seen resting) ===")
    cur.execute(
        "WITH first_seen AS ("
        "  SELECT DISTINCT ON (t.kalshi_order_id) t.kalshi_order_id, t.strategy,"
        "         t.queue_position, t.contracts_ahead"
        "  FROM live_order_queue_ticks t"
        f"  WHERE t.captured_at >= now() - interval '{int(hours)} hours'"
        "    AND t.queue_position IS NOT NULL"
        "  ORDER BY t.kalshi_order_id, t.captured_at"
        # Bands are CONTRACT COUNTS, not ordinal ranks. Kalshi sends one fixed-point figure
        # (`queue_position_fp`) and a value of 2028.55 settles what it is: the quantity resting
        # ahead of us. Ordinal-shaped bands (1-2, 3-5, 6-10) would dump almost every real order
        # into a single "11+" bucket and show a flat, meaningless gradient.
        ") SELECT f.strategy,"
        "         CASE WHEN f.contracts_ahead = 0 THEN '0 (front)'"
        "              WHEN f.contracts_ahead <= 10 THEN '1-10'"
        "              WHEN f.contracts_ahead <= 100 THEN '11-100'"
        "              WHEN f.contracts_ahead <= 1000 THEN '101-1k'"
        "              ELSE '1k+' END AS ahead_band,"
        "         count(*) AS orders,"
        "         count(*) FILTER (WHERE o.status = 'filled') AS filled"
        "  FROM first_seen f JOIN live_orders o ON o.kalshi_order_id = f.kalshi_order_id"
        "  WHERE o.status IN ('filled','canceled')"
        "  GROUP BY 1, 2 ORDER BY 1, 2",
    )
    rows = cur.fetchall()
    if not rows:
        print("  (no orders have both a rank sample and a terminal status yet — this fills in")
        print("   as resting orders settle out; it is a data-maturity wait, not a fault)")
        return
    print(f"  {'book':11s} {'ahead band':>11} {'orders':>7} {'filled':>7} {'fill%':>7}")
    for strat, band, orders, filled in rows:
        print(f"  {str(strat)[:11]:11s} {band:>11} {orders:>7} {filled:>7}"
              f" {_pct(filled / orders) if orders else 'n/a':>7}")
    print("  (A steep fill% gradient across the bands says queue depth drives fills, which")
    print("   makes the offset a real lever. A flat one says it does not — and that we should")
    print("   stop paying a cent for it, which is the decision this whole line of work is for.")
    print("   Either way it does NOT say the fills are profitable: see mmsell_fill_model.)")


def report(cur, hours: int) -> None:
    if not _coverage(cur, hours):
        return
    _by_book(cur, hours)
    _rank_vs_fill(cur, hours)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=168, help="lookback window (default 7 days)")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            report(cur, args.hours)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
