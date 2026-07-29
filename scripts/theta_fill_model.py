"""theta MAKER-FILL model — align paper P&L with the number we can actually expect live.

WHY THIS EXISTS
---------------
theta uses the exact same maker-sell convention as mmsell (`kalshi_bot/theta/tracker.py`: "entry is
the maker-sell convention the mmsell book uses: sell YES at the ask == buy NO at no-bid"). mmsell's
own live test proved that convention's paper P&L is a mirage in the mid-price cells: a resting order
only fills when someone actively takes the other side, so paper (which assumes every resting order
fills) books the quiet cheap winners a maker structurally can never capture (see
`docs/MMSELL_FILL_MODEL.md`). theta has never traded live — as of this script's introduction,
`live_orders` has zero rows for any `theta*` strategy — so there is no theta-specific ground truth to
calibrate against yet. This script is built now, so it activates the moment that changes:

  * If any `theta*` strategy has live order history, `_load_own_calibration()` finds it and the
    report calibrates from theta's OWN fills (exactly like `mmsell_fill_model.py` does for mmsell).
  * Until then, it FALLS BACK to mmsell3's live calibration, clearly labeled BORROWED. This is a
    weaker claim than mmsell's self-calibration: it assumes maker adverse selection is driven by
    price cell in a way that transfers across market series (mmsell's sports h2h/longshot markets
    vs theta's hourly crypto ladders), which is unproven for theta. Treat the borrowed read as a
    caution flag, not a verdict — it exists to stop a paper-optimistic gate pass (like theta4's
    n>=80 per-trade gate) from being mistaken for a live-ready result, the same mistake mmsell3 made.

Read-only, self-contained (stdlib + psycopg); runs locally or via the ops channel:

    DATABASE_URL_RO=postgresql://... python scripts/theta_fill_model.py
    # or:  {"type": "script", "name": "theta_fill_model"}

The projection math (`project_realizable`) is a pure function, unit-tested independently of the DB
in `tests/test_theta_fill_model.py`. It is the same shape as `mmsell_fill_model.project_realizable`
(duplicated here rather than imported — every script in this repo is self-contained, no sibling
imports across scripts/).
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

# A yes-cent cell needs at least this many FILLED observations before we trust its realizable
# P&L / fill rate enough to project other books through it.
MIN_CELL_FILLS = 8


def project_realizable(price_hist, calib, min_cell_fills: int = MIN_CELL_FILLS):
    """Project one book's entry-price distribution through a (price -> fill, realizable P&L)
    calibration. Identical contract to `mmsell_fill_model.project_realizable`:

    price_hist: dict yes_cent(int) -> (n_trades, sum_paper_pnl_cents) for the book.
    calib:      dict yes_cent(int) -> (n_fills, fill_rate, realizable_pnl_cents).

    Returns dict with total_n, covered_n, est_fill_rate, est_realizable_cents, opt_cents. A cell is
    "covered" only if the calibration has >= min_cell_fills fills there; uncovered trades are
    excluded from the estimate (surfaced as coverage < 100%), never guessed.
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


def _load_own_calibration(cur) -> dict[int, tuple]:
    """theta's OWN live ground truth, per YES entry cent, pooled across every theta* strategy that
    has ever traded live: (n_fills, fill_rate, realizable_pnl_cents). Empty dict if theta has no
    live order history yet (the current, expected state)."""
    cur.execute("SELECT DISTINCT strategy FROM live_orders WHERE strategy LIKE 'theta%%' AND action='buy'")
    live_strats = [r[0] for r in cur.fetchall() if r[0]]
    if not live_strats:
        return {}
    cur.execute(
        "WITH lf AS ("
        "  SELECT market_ticker, strategy, bool_or(status='filled') filled"
        "  FROM live_orders WHERE strategy = ANY(%s) AND action='buy' GROUP BY market_ticker, strategy)"
        " SELECT (100 - p.assumed_price)::int yes_c,"
        "        count(*) FILTER (WHERE lf.filled) n_fill,"
        "        count(*) n_attempt,"
        "        avg(p.pnl) FILTER (WHERE lf.filled) realizable_pnl"
        " FROM paper_trades p"
        " JOIN lf ON lf.market_ticker = p.market_ticker AND lf.strategy = p.strategy"
        " WHERE p.strategy = ANY(%s) AND p.status='settled' AND NOT coalesce(p.legacy,false)"
        "   AND p.assumed_price IS NOT NULL AND p.pnl IS NOT NULL"
        " GROUP BY 1",
        (live_strats, live_strats),
    )
    return _rows_to_calib(cur.fetchall())


def _load_borrowed_calibration(cur) -> dict[int, tuple]:
    """Fallback ground truth: live mmsell3, the only maker-sell book with live fill history so far.
    Cross-strategy borrow — see module docstring for the caveat."""
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
    return _rows_to_calib(cur.fetchall())


def _rows_to_calib(rows) -> dict[int, tuple]:
    calib: dict[int, tuple] = {}
    for yes_c, n_fill, n_attempt, real_pnl in rows:
        fr = (n_fill / n_attempt) if n_attempt else 0.0
        rp = float(real_pnl) * 100 if real_pnl is not None else 0.0
        calib[int(yes_c)] = (int(n_fill), fr, rp)
    return calib


def _load_price_hists(cur) -> dict[str, dict[int, list]]:
    """Per theta book: yes_cent -> [n_trades, sum_paper_pnl_cents]."""
    cur.execute(
        "SELECT strategy, (100 - assumed_price)::int yes_c, count(*), sum(pnl)"
        " FROM paper_trades"
        " WHERE strategy LIKE 'theta%%' AND status='settled' AND NOT coalesce(legacy,false)"
        "   AND assumed_price IS NOT NULL AND pnl IS NOT NULL"
        " GROUP BY 1, 2",
    )
    hists: dict[str, dict[int, list]] = defaultdict(dict)
    for strat, yes_c, n, spnl in cur.fetchall():
        hists[strat][int(yes_c)] = [int(n), float(spnl) * 100]
    return hists


def report(cur) -> None:
    calib = _load_own_calibration(cur)
    source = "theta's OWN live orders"
    borrowed = False
    if not calib:
        calib = _load_borrowed_calibration(cur)
        source = "BORROWED from mmsell3 live orders — theta has no live history yet"
        borrowed = True

    hists = _load_price_hists(cur)
    if not hists:
        print("(no settled theta paper trades)")
        return

    _print_calibration(calib, source, borrowed)
    _print_books(hists, calib, borrowed)


def _print_calibration(calib: dict[int, tuple], source: str, borrowed: bool) -> None:
    print(f"=== Calibration source: {source} ===")
    if borrowed:
        print("  ! CAUTION: this projects theta trades through mmsell's maker-fill behavior, a")
        print("    different market series (sports h2h/longshot vs hourly crypto ladders). Treat the")
        print("    realizable numbers below as a caution flag, not a verdict, until theta has its own")
        print("    live fills to calibrate from (see module docstring).")
    print(f"  {'yes_c':>5} {'n_fill':>6} {'fill_rate':>9} {'realizable_$/ct':>15}  trusted")
    for yes_c in sorted(calib):
        nf, fr, rp = calib[yes_c]
        trusted = "yes" if nf >= MIN_CELL_FILLS else "no (thin)"
        print(f"  {yes_c:>5} {nf:>6} {_fmt_pct(fr):>9} {_fmt_c(rp):>15}  {trusted}")


def _print_books(hists: dict, calib: dict, borrowed: bool) -> None:
    print("\n=== Optimistic (fill-everything) vs REALIZABLE (calibrated) per theta book ===")
    print(f"  {'book':9s} {'n':>5} {'cover':>6} {'est_fill':>8} {'opt_$/ct':>9}"
          f" {'real_$/ct':>10}  read")
    for strat in sorted(hists):
        r = project_realizable(hists[strat], calib)
        cover = (r["covered_n"] / r["total_n"]) if r["total_n"] else 0.0
        opt = r["opt_cents"]
        real = r["est_realizable_cents"]
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
        if borrowed and read in ("REALIZABLE EDGE", "thin +"):
            read += " (borrowed calib — unconfirmed)"
        print(f"  {strat:9s} {r['total_n']:5d} {_fmt_pct(cover):>6} {_fmt_pct(r['est_fill_rate']):>8}"
              f" {opt:>+8.2f}c {_fmt_c(real):>10}  {read}")
    print("  (real_$/ct is the number to GATE on once trusted. 'cover' = share of the book's trades"
          "\n   priced in a trusted cell; low cover = estimate speaks for only part of the book.)")


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
