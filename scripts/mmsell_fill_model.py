"""mmsell MAKER-FILL model — align the paper number with the number we can actually expect live.

WHY THIS EXISTS
---------------
The mmsell paper books assume a resting sell-YES (== buy-NO at the no-bid) ALWAYS fills at that
price. Live, the same resting maker order fills only ~70% of the time, and the live test proved the
un-filled ~30% are the *winners*: on the exact mmsell3 tickers, paper's own P&L was -0.67c/trade on
the ones live filled vs +3.77c/trade on the ones live tried but could never fill (identical entry
price ~91c). A passive bid only gets hit when someone actively takes the other side — the quiet
cheap longshots that settle worthless never trade against you, so you never book them. Paper counts
those free winners; live cannot. That selection gap IS the entire paper->live divergence.

WHY THIS IS A CALIBRATION MODEL, NOT A PER-TICKER REPLAY
-------------------------------------------------------
A literal replay ("would this resting order have been lifted?") needs the post-entry price PATH of
each mmsell ticker. We don't have it: the mmsell tracker fetches orderbooks live to decide entry but
never persists market_snapshots/orderbook_snapshots for the sports markets it trades (those tables
are the main scanner's different universe — 0 rows for mmsell tickers). So the only real fill data
we have is the live mmsell3 ground truth. This model calibrates the empirical relationship

    yes entry price  ->  (P[fill],  realizable P&L | fill)

from live mmsell3, then projects each paper book's own entry-price distribution through it. The
central live finding — fillability is driven by the price cell, not the sport/variant (non-WC live
win% matched paper exactly) — is exactly what makes that projection valid across variants. The
number it produces per book is the REALIZABLE per-trade edge: paper corrected for which trades a
maker actually gets. `scripts/mmsell_snapshot_note` / docs/MMSELL_FILL_MODEL.md track the collection
fix that would later allow a true per-ticker replay to refine this.

Read-only, self-contained (stdlib + psycopg); runs locally or via the ops channel:

    DATABASE_URL_RO=postgresql://... python scripts/mmsell_fill_model.py
    # or:  {"type": "script", "name": "mmsell_fill_model"}

The projection math lives in project_realizable() as a pure function so it is unit-tested
(tests/test_mmsell_fill_model.py) independently of the DB plumbing.
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)

# A live yes-cent cell needs at least this many FILLED observations before we trust its realizable
# P&L / fill rate enough to project other books through it.
MIN_CELL_FILLS = 8


def project_realizable(price_hist, calib, min_cell_fills: int = MIN_CELL_FILLS):
    """Project one book's entry-price distribution through the live calibration.

    price_hist: dict yes_cent(int) -> (n_trades, sum_paper_pnl_cents) for the book.
    calib:      dict yes_cent(int) -> (n_fills, fill_rate, realizable_pnl_cents) from live.

    Returns dict with:
      total_n, covered_n            — trades total vs those in a trusted calibration cell
      est_fill_rate                 — trade-weighted P[fill] over covered trades
      est_realizable_cents          — trade-weighted realizable P&L/contract over covered trades
      opt_cents                     — the current optimistic (fill-everything) P&L/contract, all trades
    A cell is "covered" only if the live calibration has >= min_cell_fills fills there; trades at
    uncovered prices are excluded from the estimate (and surfaced as coverage < 100%), never guessed.
    """
    total_n = sum(n for n, _ in price_hist.values())
    opt_sum = sum(s for _, s in price_hist.values())
    covered_n = 0
    fill_w = 0.0
    real_w = 0.0
    for yes_c, (n, _s) in price_hist.items():
        cell = calib.get(yes_c)
        if not cell or cell[0] < min_cell_fills:
            continue
        _nf, fr, rp = cell
        covered_n += n
        fill_w += fr * n
        real_w += rp * n
    return {
        "total_n": total_n,
        "covered_n": covered_n,
        "est_fill_rate": (fill_w / covered_n) if covered_n else None,
        "est_realizable_cents": (real_w / covered_n) if covered_n else None,
        "opt_cents": (opt_sum / total_n) if total_n else 0.0,
    }


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def _fmt_pct(x) -> str:
    return f"{x*100:.1f}%" if x is not None else "n/a"


def _fmt_c(x) -> str:
    return f"{x:+.2f}c" if x is not None else "n/a"


def _load_calibration(cur) -> dict[int, tuple]:
    """Live mmsell3 ground truth, per YES entry cent: (n_fills, fill_rate, realizable_pnl_cents).
    realizable = avg paper pnl (cents) over the tickers live actually FILLED at that price — i.e.
    what a maker really earns there. Fill/no-fill from the real live orders."""
    cur.execute(
        "WITH lf AS ("
        "  SELECT market_ticker, bool_or(status='filled') filled"
        "  FROM live_orders WHERE strategy='mmsell3' AND action='buy' GROUP BY market_ticker)"
        " SELECT (100 - p.assumed_price)::int yes_c,"
        "        count(*) FILTER (WHERE lf.filled) n_fill,"
        "        count(*) n_attempt,"
        "        avg(p.pnl) FILTER (WHERE lf.filled) realizable_pnl"
        " FROM paper_trades p JOIN lf ON lf.market_ticker = p.market_ticker"
        " WHERE p.strategy='mmsell3' AND p.status='settled' AND NOT coalesce(p.legacy,false)"
        "   AND p.assumed_price IS NOT NULL AND p.pnl IS NOT NULL"
        " GROUP BY 1",
    )
    calib: dict[int, tuple] = {}
    for yes_c, n_fill, n_attempt, real_pnl in cur.fetchall():
        fr = (n_fill / n_attempt) if n_attempt else 0.0
        rp = float(real_pnl) * 100 if real_pnl is not None else 0.0
        calib[int(yes_c)] = (int(n_fill), fr, rp)
    return calib


def _load_price_hists(cur) -> dict[str, dict[int, list]]:
    """Per book: yes_cent -> [n_trades, sum_paper_pnl_cents]."""
    cur.execute(
        "SELECT strategy, (100 - assumed_price)::int yes_c, count(*), sum(pnl)"
        " FROM paper_trades"
        " WHERE strategy LIKE '%%mmsell%%' AND status='settled' AND NOT coalesce(legacy,false)"
        "   AND assumed_price IS NOT NULL AND pnl IS NOT NULL"
        # Live/paper TWIN books are excluded: a twin already enters at the LIVE maker price, so
        # projecting it through the live fill calibration would double-count the correction, and a
        # twin must never be gated like a paper variant. Its read is scripts/live_paper_parity.py.
        "   AND strategy NOT IN (SELECT twin_tag FROM live_paper_twins)"
        " GROUP BY 1, 2",
    )
    hists: dict[str, dict[int, list]] = defaultdict(dict)
    for strat, yes_c, n, spnl in cur.fetchall():
        hists[strat][int(yes_c)] = [int(n), float(spnl) * 100]
    return hists


def report(cur) -> None:
    calib = _load_calibration(cur)
    hists = _load_price_hists(cur)
    if not hists:
        print("(no settled mmsell paper trades)")
        return

    _print_calibration(calib)
    _print_books(hists, calib)


def _print_calibration(calib: dict[int, tuple]) -> None:
    print("=== Live calibration (mmsell3 real orders): fill & realizable P&L by YES entry price ===")
    print(f"  {'yes_c':>5} {'n_fill':>6} {'fill_rate':>9} {'realizable_$/ct':>15}  trusted")
    for yes_c in sorted(calib):
        nf, fr, rp = calib[yes_c]
        trusted = "yes" if nf >= MIN_CELL_FILLS else "no (thin)"
        print(f"  {yes_c:>5} {nf:>6} {_fmt_pct(fr):>9} {_fmt_c(rp):>15}  {trusted}")
    print("  (realizable = what a maker actually earns per contract at that price — the fills you get."
          "\n   cheap cells stay ~paper; 8-10c go negative: that IS the adverse selection, priced.)")


def _print_books(hists: dict, calib: dict) -> None:
    print("\n=== Optimistic (fill-everything) vs REALIZABLE (live-calibrated) per book ===")
    print(f"  {'book':9s} {'n':>5} {'cover':>6} {'est_fill':>8} {'opt_$/ct':>9}"
          f" {'real_$/ct':>10}  read")
    for strat in sorted(hists):
        r = project_realizable(hists[strat], calib)
        cover = (r["covered_n"] / r["total_n"]) if r["total_n"] else 0.0
        opt = r["opt_cents"]
        real = r["est_realizable_cents"]
        # Promotability read from the REALIZABLE number + how much of the book it speaks for.
        # Low coverage => the estimate only covers the cheap slice; the rest is un-modeled.
        if real is None or cover < 0.50:
            read = "low coverage"
        elif real >= 1.0:
            read = "REALIZABLE EDGE"
        elif real > 0:
            read = "thin +"
        elif opt > 0:
            read = "MIRAGE (paper+ -> neg)"
        else:
            read = "dead (paper- too)"
        print(f"  {strat:9s} {r['total_n']:5d} {_fmt_pct(cover):>6} {_fmt_pct(r['est_fill_rate']):>8}"
              f" {opt:>+8.2f}c {_fmt_c(real):>10}  {read}")
    print("  (real_$/ct is the number to GATE on. 'cover' = share of the book's trades priced in a"
          "\n   trusted live cell; low cover = estimate speaks for only part of the book. A book whose"
          "\n   optimistic edge survives at high coverage is the real live candidate.)")


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
