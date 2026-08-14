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


# Kalshi's published maker coefficient — must equal kalshi_bot.paper.engine.MAKER_COEFF
# (pinned by tests/test_mmsell_fill_model.py; this script stays stdlib-only so it can run on a
# bare ops runner, which is why the constant is restated rather than imported).
MAKER_COEFF = 0.0175

# Normalize every P&L to ONE fee model, in dollars.
#
# Why this is not optional: `paper_trades.pnl` has whatever fee the engine charged AT THE TIME
# subtracted from it, and the engine changed on 2026-08-11 (it had been billing resting maker
# entries Kalshi's TAKER rate, ceiled to a whole cent -- 1.000c/contract against the 0.003c
# Kalshi actually charges a maker). Averaging raw `pnl` across that boundary silently blends two
# fee models and drifts as the post-fix sample grows, which would make every MIRAGE/EDGE label
# here a function of WHEN a book traded rather than HOW it traded.
#
# So: add back the fee actually charged (stored per row) and re-apply the correct maker fee.
# The result is fee-model-independent and directly comparable across the boundary.
_PNL_NORM = (
    "(p.pnl + coalesce(p.fees,0)"
    f" - {MAKER_COEFF} * coalesce(p.quantity,1)"
    " * (p.assumed_price/100.0) * (1 - p.assumed_price/100.0))"
)


# --- COHORT BOUNDARIES: books whose UNIVERSE changed underneath them --------------------
#
# A book's trades are poolable only while the SET OF MARKETS it was offered stayed the same.
# When a deploy changes what a book can see, the trades either side are drawn from different
# populations, and their average describes neither.
#
# This is a different kind of boundary from the 2026-08-11 maker-fee correction that _PNL_NORM
# above handles, and it needs the opposite remedy. The fee change was a UNIFORM SHIFT: every
# maker trade moved by the same ~0.87c, so pre-boundary trades stay usable once the shift is
# added back — which is exactly what _PNL_NORM does. A universe change admits no such
# conversion: the pre-boundary trades are a BIASED SUBSAMPLE of the post-boundary population,
# and no offset turns one into the other. The only correct treatment is to DROP them.
#
# The tags deliberately did NOT change. Each book's selection RULE is byte-identical either
# side of its boundary, so forking the tag would assert a change that did not happen and would
# leave two tags testing one rule. The cohort is the DATE — and this table is what makes that
# enforceable instead of merely remembered. Every previous re-baseline in this repo lived only
# in prose (docs/BOOK_REGISTRY.md, "the boundary is a date, not a flag"), which meant the
# standing read still silently pooled across it.
#
#   tag: (first instant of the new cohort, the control to read the book against)
#
# 2026-08-13 18:09:40Z — THE TAXONOMY BOUNDARY (commit c75c6be, merged 18:04:24Z; this is the
# first live cycle observed entering a newly classified series, ~5 min later). The market-type
# taxonomy went from 50.5% to 99.6% of candidate flow: 46 series added, none reclassified.
# The four surviving type books select with `mtype=`/`mode=`, and an unclassified series is
# admitted by NO allowlist — so before this instant each was being offered roughly half its
# intended universe, and specifically the half somebody had already classified, i.e. the very
# series the census prior was fit on. Their entry rates jumped 5-9x across the boundary while
# the un-filtered control `mmsell10` moved 1.6x with the ambient universe.
#
# The upside that pooling would destroy: those 46 series were never in the census, so
# post-boundary flow is the first genuinely OUT-OF-SAMPLE test the type hypothesis has had.
# docs/MMSELL_TYPE_BOOKS.md states the boundary and its consequences; this applies it.
COHORT_START: dict[str, tuple[str, str]] = {
    "Tmmsell1": ("2026-08-13 18:09:40+00", "mmsell10"),
    "Tmmsell2": ("2026-08-13 18:09:40+00", "mmsell10"),
    "Tmmsell5": ("2026-08-13 18:09:40+00", "mmsell10"),
    "Tmmsell6": ("2026-08-13 18:09:40+00", "mmsell10"),
}


def cohort_floor_sql() -> tuple[str, list]:
    """The per-book entry-time floor as a SQL predicate over `paper_trades p`, plus its params.

    A CASE rather than a join so it drops into any existing per-book aggregate unchanged: books
    with no boundary fall through to `-infinity` and keep their whole history. Returns ("", [])
    when no book has a boundary, so the caller needs no special case."""
    if not COHORT_START:
        return "", []
    whens: list[str] = []
    params: list[str] = []
    for tag, (start, _control) in sorted(COHORT_START.items()):
        whens.append("WHEN p.strategy = %s THEN %s::timestamptz")
        params += [tag, start]
    return (" AND p.created_at >= CASE " + " ".join(whens)
            + " ELSE '-infinity'::timestamptz END"), params


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
        f"        avg({_PNL_NORM}) FILTER (WHERE lf.filled) realizable_pnl"
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
    """Per book: yes_cent -> [n_trades, sum_paper_pnl_cents]. Cohort books are floored at their
    boundary (COHORT_START) — the number this table feeds is the one books get GATED on, so it
    must not span a universe change."""
    floor_sql, floor_params = cohort_floor_sql()
    cur.execute(
        f"SELECT p.strategy, (100 - p.assumed_price)::int yes_c, count(*), sum({_PNL_NORM})"
        " FROM paper_trades p"
        " WHERE p.strategy LIKE '%%mmsell%%' AND p.status='settled'"
        "   AND NOT coalesce(p.legacy,false)"
        "   AND p.assumed_price IS NOT NULL AND p.pnl IS NOT NULL"
        # Live/paper TWIN books are excluded: a twin already enters at the LIVE maker price, so
        # projecting it through the live fill calibration would double-count the correction, and a
        # twin must never be gated like a paper variant. Its read is scripts/live_paper_parity.py.
        "   AND p.strategy NOT IN (SELECT twin_tag FROM live_paper_twins)"
        + floor_sql
        + " GROUP BY 1, 2",
        floor_params or None,
    )
    hists: dict[str, dict[int, list]] = defaultdict(dict)
    for strat, yes_c, n, spnl in cur.fetchall():
        hists[strat][int(yes_c)] = [int(n), float(spnl) * 100]
    return hists


def _load_cohort_controls(cur) -> dict[str, dict[int, list]]:
    """Each cohort book's CONTROL, restricted to that book's own window — keyed by the COHORT
    BOOK's tag, so the pair reads as one row.

    The gate says "beats its control by >= +1.0c OVER THE SAME WINDOW", and until now nothing
    computed the right-hand side: the control's own row in the main table is its whole lifetime.
    That asymmetry is not cosmetic here — the cohort books' universe changed and `mmsell10`'s
    did not, so its lifetime number is a legitimate figure for a window the books no longer
    trade in, and differencing against it would silently compare two different regimes."""
    if not COHORT_START:
        return {}
    values, params = [], []
    for tag, (start, control) in sorted(COHORT_START.items()):
        values.append("(%s, %s, %s::timestamptz)")
        params += [tag, control, start]
    cur.execute(
        f"WITH win(tag, control, since) AS (VALUES {', '.join(values)})"
        f" SELECT win.tag, (100 - p.assumed_price)::int yes_c, count(*), sum({_PNL_NORM})"
        " FROM paper_trades p"
        " JOIN win ON win.control = p.strategy AND p.created_at >= win.since"
        " WHERE p.status='settled' AND NOT coalesce(p.legacy,false)"
        "   AND p.assumed_price IS NOT NULL AND p.pnl IS NOT NULL"
        " GROUP BY 1, 2",
        params,
    )
    hists: dict[str, dict[int, list]] = defaultdict(dict)
    for tag, yes_c, n, spnl in cur.fetchall():
        hists[tag][int(yes_c)] = [int(n), float(spnl) * 100]
    return hists


def report(cur) -> None:
    calib = _load_calibration(cur)
    hists = _load_price_hists(cur)
    if not hists:
        print("(no settled mmsell paper trades)")
        return

    _print_calibration(calib)
    _print_books(hists, calib)
    _print_cohort_gate(hists, _load_cohort_controls(cur), calib)


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
    print(f"  {'book':9s} {'since':>6} {'n':>5} {'cover':>6} {'est_fill':>8} {'opt_$/ct':>9}"
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
        since = _since_label(strat)
        print(f"  {strat:9s} {since:>6} {r['total_n']:5d} {_fmt_pct(cover):>6}"
              f" {_fmt_pct(r['est_fill_rate']):>8}"
              f" {opt:>+8.2f}c {_fmt_c(real):>10}  {read}")
    print("  (real_$/ct is the number to GATE on. 'cover' = share of the book's trades priced in a"
          "\n   trusted live cell; low cover = estimate speaks for only part of the book. A book whose"
          "\n   optimistic edge survives at high coverage is the real live candidate.)")
    if COHORT_START:
        print("  ('since' = this book's universe changed on that date and its earlier trades are"
              "\n   EXCLUDED here — see COHORT GATE READ below. A blank means the whole history.)")


def _since_label(strat: str) -> str:
    """`MM-DD` for a cohort book, blank otherwise. The marker exists because the alternative is a
    row whose `n` quietly dropped between two runs of this script with no visible reason."""
    entry = COHORT_START.get(strat)
    return entry[0][5:10] if entry else ""


def _print_cohort_gate(hists: dict, controls: dict, calib: dict) -> None:
    """The gate's own arithmetic, for books whose universe changed: book vs its control over the
    SAME window. Printed as its own section rather than folded into the table above because the
    number that decides these books is the DIFFERENCE, and a table of absolutes invites reading
    them against the control's lifetime row — the exact error the boundary creates."""
    if not COHORT_START:
        return
    print("\n=== COHORT GATE READ — universe-changed books vs their control over the SAME window ===")
    print(f"  {'book':9s} {'since':>11} {'n':>5} {'opt_$/ct':>9} {'real_$/ct':>10}"
          f" {'ctl':>9} {'ctl_n':>6} {'ctl_opt':>9} {'D opt':>8}  gate")
    for tag in sorted(COHORT_START):
        start, control = COHORT_START[tag]
        book = project_realizable(hists.get(tag, {}), calib)
        ctl = project_realizable(controls.get(tag, {}), calib)
        n, ctl_n = book["total_n"], ctl["total_n"]
        opt, ctl_opt = book["opt_cents"], ctl["opt_cents"]
        real = book["est_realizable_cents"]
        delta = (opt - ctl_opt) if (n and ctl_n) else None
        print(f"  {tag:9s} {start[5:10]:>11} {n:5d} {opt:>+8.2f}c {_fmt_c(real):>10}"
              f" {control:>9} {ctl_n:>6} {ctl_opt:>+8.2f}c"
              f" {(f'{delta:+.2f}c' if delta is not None else 'n/a'):>8}"
              f"  {_cohort_verdict(n, opt, delta)}")
    print("  (KEEP needs all three, per docs/MMSELL_TYPE_BOOKS.md: own opt > 0 absolute, D opt >="
          "\n   +1.0c vs the control, realizable > 0. Condition 3 does NOT discriminate inside the"
          "\n   tight band — the projection sees entry price only, so the control lands there too;"
          "\n   it is printed to be checked for a sign flip, never to promote a book on.)")


# The gate's n floor for the type books (docs/MMSELL_TYPE_BOOKS.md).
COHORT_GATE_N = 100
COHORT_GATE_EDGE_CENTS = 1.0


def _cohort_verdict(n: int, opt: float, delta: float | None) -> str:
    """Deliberately silent until the pre-registered n. Reporting a lead at n=12 is how a noise
    draw becomes a decision; the honest output there is how far the book still has to go."""
    if n < COHORT_GATE_N or delta is None:
        return f"n {n}/{COHORT_GATE_N} — no verdict yet"
    if opt <= 0:
        return "KILL — loses money in absolute terms"
    if delta < COHORT_GATE_EDGE_CENTS:
        return f"KILL — beats control by only {delta:+.2f}c (needs >= +{COHORT_GATE_EDGE_CENTS:.1f}c)"
    return "PASSES 1+2 — confirm realizable before promoting"


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
