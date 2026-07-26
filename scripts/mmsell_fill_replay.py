"""mmsell FILL REPLAY — a direct per-ticker measurement of maker-fill realism from captured
candidate price paths, complementing the live-calibrated estimate in mmsell_fill_model.py.

WHY THIS EXISTS
---------------
mmsell_fill_model.py corrects paper's "a resting order always fills" assumption with a live-
CALIBRATED estimate: it can only learn the price -> fill-probability relationship from the one
359-trade live mmsell3 window, and it can't see price cells that window never visited. This script
is the promised follow-on (docs/MMSELL_FILL_MODEL.md Sec 5 #2). Now that mmsell_candidate_ticks
tapes the pre-entry price PATH of every in-band candidate each cycle — not just markets we held —
it replays each SETTLED candidate's actual path to ask, per ticker:

    would a resting buy-NO at the no-bid (== sell-YES at the yes-ask) have been LIFTED before close?

FILL PROXY — read this before trusting the numbers
---------------------------------------------------
We only have periodic top-of-book SNAPSHOTS (every mmsell_interval_minutes), not trade prints, so
"lifted" can't be observed directly. The proxy used here: a resting buy-NO at price P is treated as
FILLED if any LATER snapshot (before the market's close) shows no_ask <= P — i.e. the book's touch
crossed through (or past) our resting level. Equivalently (no_ask == 100 - yes_bid): the yes bid
rose to at least our entry's implied yes-ask. A matching engine cannot show an ask at/below a
standing bid without a trade having cleared through it, so this is a legitimate, standard quote-
based fill proxy — but it is still an approximation: (a) it can't see a cross-and-revert that
happened entirely between two samples (under-counts fills there), and (b) it says nothing about
whether OUR specific order would have had queue priority over others resting at the same price —
live orders proved queue position matters (docs/MMSELL_FILL_MODEL.md Sec 1). Treat this as a
second, independent read alongside the live calibration, not a replacement for it, until the two
have been cross-validated over many more settlements.

A market only enters this replay once it has SETTLED with a captured path (created_at..closed_at) —
coverage grows over days as the candidate cohort turns over (docs/RESEARCH_JOURNAL.md 2026-07-23).

Read-only, self-contained (stdlib + psycopg); runs locally or via the ops channel:

    DATABASE_URL_RO=postgresql://... python scripts/mmsell_fill_replay.py
    # or:  {"type": "script", "name": "mmsell_fill_replay"}

The replay math lives in replay_fill() as a pure function, unit-tested in
tests/test_mmsell_fill_replay.py, independent of the DB.
"""

from __future__ import annotations

import math
import os
import sys
from collections import defaultdict

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)


def fee_c(price_c: float) -> float:
    """Kalshi fee in CENTS/contract at a price — matches paper's kalshi_fee (ceil(7*p*(1-p)))."""
    p = price_c / 100.0
    return math.ceil(7.0 * p * (1.0 - p))


def replay_fill(entry_no: float, settle_no: float, path):
    """One settled candidate's counterfactual maker fill + P&L.

    entry_no  : NO cost basis in cents (== the no-bid we'd have rested at; paper's assumed_price).
    settle_no : NO settlement value, 100 (longshot missed) or 0 (longshot hit).
    path      : time-ordered no_ask (cents) observations STRICTLY AFTER entry, up to close.

    Returns (filled: bool, pnl_c: float). If never filled, pnl is 0.0 — a maker who never gets
    taken has no position, so no gain or loss, NOT the optimistic settle P&L paper assumes.
    """
    filled = any(no_ask is not None and no_ask <= entry_no for no_ask in path)
    if not filled:
        return False, 0.0
    return True, (settle_no - entry_no - fee_c(entry_no))


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def _cell(yes_entry_c: int) -> str:
    if yes_entry_c <= 7:
        return "<=7c"
    if yes_entry_c <= 11:
        return "8-11c"
    return ">=12c"


def _load(cur):
    """Settled control-book (strategy='mmsell') trades + their captured candidate price path."""
    cur.execute(
        "SELECT market_ticker, assumed_price, resolved_value, created_at, closed_at"
        " FROM paper_trades WHERE strategy = 'mmsell' AND status = 'settled'"
        "   AND NOT coalesce(legacy, false) AND assumed_price IS NOT NULL"
        "   AND resolved_value IN (0, 100) AND created_at IS NOT NULL AND closed_at IS NOT NULL",
    )
    trades = cur.fetchall()
    tickers = sorted({t[0] for t in trades})
    ticks_by_ticker = defaultdict(list)
    if tickers:
        cur.execute(
            "SELECT market_ticker, captured_at, no_ask FROM mmsell_candidate_ticks"
            " WHERE market_ticker = ANY(%s) ORDER BY market_ticker, captured_at",
            (tickers,),
        )
        for tk, ts, no_ask in cur.fetchall():
            ticks_by_ticker[tk].append((ts, no_ask))
    return trades, ticks_by_ticker


def report(cur) -> None:
    trades, ticks_by_ticker = _load(cur)
    if not trades:
        print("(no settled mmsell control-book trades)")
        return

    per_cell = defaultdict(lambda: {"n": 0, "with_path": 0, "filled": 0,
                                     "opt_sum": 0.0, "opt_path_sum": 0.0, "real_sum": 0.0})
    n_total = len(trades)
    n_path = 0
    for ticker, entry_no, settle_no, created_at, closed_at in trades:
        entry_no = float(entry_no)
        settle_no = float(settle_no)
        cell = _cell(int(round(100 - entry_no)))
        pc = per_cell[cell]
        pc["n"] += 1
        opt_pnl = settle_no - entry_no - fee_c(entry_no)
        pc["opt_sum"] += opt_pnl

        path = [no_ask for ts, no_ask in ticks_by_ticker.get(ticker, ())
                if created_at < ts <= closed_at and no_ask is not None]
        if not path:
            continue
        n_path += 1
        pc["with_path"] += 1
        # Same trades' optimistic P&L, restricted to the replayable subset -- the fair comparator
        # for replay_$/ct. opt_sum/n (the all-time, all-n figure) is a different, much larger
        # population and must never be read side-by-side with replay_$/ct as if it were.
        pc["opt_path_sum"] += opt_pnl
        filled, real_pnl = replay_fill(entry_no, settle_no, path)
        if filled:
            pc["filled"] += 1
        pc["real_sum"] += real_pnl

    print("=== mmsell fill REPLAY — direct per-ticker measurement from captured candidate paths ===")
    print(f"  {n_path}/{n_total} settled control-book trades have a captured price path"
          f" (coverage {100*n_path/n_total:.0f}%).")
    if not n_path:
        print("\n  No settled trade yet has a captured candidate path — mmsell_candidate_ticks starts"
              "\n  filling on deploy; a candidate must be BORN, TAPED and SETTLE inside the capture"
              "\n  window to be replayable. Data-maturity wait, not an error — re-run in a few days.")
        return

    print(f"\n  {'cell':7s} {'n':>5} {'w/path':>7} {'fill%':>7}"
          f" {'opt_all':>8} {'opt_path':>9} {'replay':>8}")
    all_n = all_path = all_filled = 0
    all_opt = all_opt_path = all_real = 0.0
    for cell in ("<=7c", "8-11c", ">=12c"):
        pc = per_cell.get(cell)
        if not pc:
            continue
        all_n += pc["n"]
        all_opt += pc["opt_sum"]
        if not pc["with_path"]:
            print(f"  {cell:7s} {pc['n']:>5}  (no replayable settlements yet)")
            continue
        all_path += pc["with_path"]
        all_filled += pc["filled"]
        all_opt_path += pc["opt_path_sum"]
        all_real += pc["real_sum"]
        fill_pct = 100.0 * pc["filled"] / pc["with_path"]
        opt_all = pc["opt_sum"] / pc["n"]
        opt_path = pc["opt_path_sum"] / pc["with_path"]
        real = pc["real_sum"] / pc["with_path"]
        print(f"  {cell:7s} {pc['n']:>5} {pc['with_path']:>7} {fill_pct:>6.0f}%"
              f" {opt_all:>+7.2f}c {opt_path:>+8.2f}c {real:>+7.2f}c")
    if all_path:
        print(f"  {'ALL':7s} {all_n:>5} {all_path:>7} {100.0*all_filled/all_path:>6.0f}%"
              f" {all_opt/all_n:>+7.2f}c {all_opt_path/all_path:>+8.2f}c {all_real/all_path:>+7.2f}c")
    print("\n  opt_all = paper's fill-everything assumption over ALL settled trades ever in the cell"
          "\n  (reference only -- a different, much larger population than the other two columns)."
          "\n  opt_path = that SAME fill-everything assumption restricted to just the trades with a"
          "\n  captured path -- the fair baseline for replay. replay = this replay's fill-proxy-gated"
          "\n  P&L over that identical subset (0 booked where the path shows the book never crossed"
          "\n  our level -- see the fill-proxy caveat in the module docstring). Judge the fill proxy by"
          "\n  (replay - opt_path), not (replay - opt_all). Cross-check against mmsell_fill_model's"
          "\n  live-calibrated realizable number for the same cell before trusting either alone; at"
          "\n  small w/path this is a TREND check, not a promote/kill signal.")


def main(argv: list[str] | None = None) -> int:
    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            report(cur)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
