"""The realizable-P&L projection (scripts/mmsell_fill_model.project_realizable) — how a book's
optimistic paper number is corrected into the number a maker can actually expect, by projecting the
book's entry-price distribution through the live-calibrated (price -> fill, realizable P&L)
relationship. Pinned here independently of the DB plumbing.

Key invariants: only price cells with enough live fills count ("coverage"); realizable P&L and fill
rate are trade-weighted over covered trades; uncovered trades are excluded, never guessed.
"""

from __future__ import annotations

import importlib.util
import pathlib

# scripts/ isn't a package; load the module by path.
_SPEC = importlib.util.spec_from_file_location(
    "mmsell_fill_model",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "mmsell_fill_model.py",
)
fm = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fm)  # type: ignore[union-attr]


def test_pnl_is_normalized_to_one_fee_model_and_that_model_matches_the_engine():
    """The 2026-08-11 re-baseline. `paper_trades.pnl` carries whatever fee the engine charged at
    the time, and the engine changed that day (resting maker entries had been billed the TAKER
    rate, ~1c/contract against Kalshi's real 0.003c). Averaging raw pnl across the boundary blends
    two fee models and makes each book's EDGE/MIRAGE label drift with WHEN it traded rather than
    HOW -- so every P&L this script reads adds the stored fee back and re-applies the maker fee.

    The coefficient is restated here rather than imported (the script must stay stdlib-only to run
    on a bare ops runner), which is exactly the kind of duplicate that rots silently."""
    from kalshi_bot.paper import engine

    assert fm.MAKER_COEFF == engine.MAKER_COEFF
    # the normalization must undo the stored fee, not stack on top of it
    assert "p.pnl + coalesce(p.fees,0)" in fm._PNL_NORM
    assert str(fm.MAKER_COEFF) in fm._PNL_NORM
    # and it must scale with the clip, or multi-contract books are mis-normalized
    assert "coalesce(p.quantity,1)" in fm._PNL_NORM


def test_no_calibration_means_no_coverage():
    # A book at prices the live data never covered -> no estimate, but optimistic still computed.
    hist = {7: [10, 12.0]}  # 10 trades, +1.2c avg
    r = fm.project_realizable(hist, calib={}, min_cell_fills=8)
    assert r["total_n"] == 10 and r["covered_n"] == 0
    assert r["est_realizable_cents"] is None and r["est_fill_rate"] is None
    assert abs(r["opt_cents"] - 1.2) < 1e-9


def test_thin_cells_are_not_trusted():
    hist = {7: [10, 12.0]}
    calib = {7: (3, 0.7, -0.5)}  # only 3 fills -> below threshold
    r = fm.project_realizable(hist, calib, min_cell_fills=8)
    assert r["covered_n"] == 0 and r["est_realizable_cents"] is None


def test_single_covered_cell_passes_realizable_through():
    hist = {7: [10, 12.0]}          # book earns +1.2c optimistically at 7c
    calib = {7: (50, 0.8, -0.6)}    # live: 80% fill, realizable -0.6c at 7c
    r = fm.project_realizable(hist, calib, min_cell_fills=8)
    assert r["covered_n"] == 10
    assert abs(r["est_fill_rate"] - 0.8) < 1e-9
    assert abs(r["est_realizable_cents"] - (-0.6)) < 1e-9
    assert abs(r["opt_cents"] - 1.2) < 1e-9  # optimistic unchanged


def test_trade_weighted_across_cells():
    # 30 trades at 6c (realizable +2c) and 10 at 9c (realizable -6c): weighted = (30*2 + 10*-6)/40 = 0.
    hist = {6: [30, 60.0], 9: [10, -60.0]}
    calib = {6: (40, 0.65, 2.0), 9: (40, 0.69, -6.0)}
    r = fm.project_realizable(hist, calib, min_cell_fills=8)
    assert r["covered_n"] == 40
    assert abs(r["est_realizable_cents"] - 0.0) < 1e-9
    assert abs(r["est_fill_rate"] - (0.65 * 30 + 0.69 * 10) / 40) < 1e-9


def test_partial_coverage_reported():
    # 6c covered, 20c not in calibration -> covered_n < total_n.
    hist = {6: [30, 60.0], 20: [10, 50.0]}
    calib = {6: (40, 0.65, 2.0)}
    r = fm.project_realizable(hist, calib, min_cell_fills=8)
    assert r["total_n"] == 40 and r["covered_n"] == 30
    assert abs(r["est_realizable_cents"] - 2.0) < 1e-9  # only the covered cell


def test_price_ceiling_book_keeps_its_edge():
    # A maxyes-style book entirely in the cheap fillable band keeps a positive realizable number.
    hist = {5: [20, 80.0], 6: [40, 80.0], 7: [40, 40.0]}
    calib = {5: (20, 0.7, 3.0), 6: (60, 0.68, 1.8), 7: (55, 0.81, 0.9)}
    r = fm.project_realizable(hist, calib, min_cell_fills=8)
    assert r["covered_n"] == 100
    assert r["est_realizable_cents"] > 0  # edge survives the fill model


# --------------------------------------------------------------- COHORT BOUNDARIES
#
# The 2026-08-13 taxonomy deploy widened the type books' universe underneath them (50.5% ->
# 99.6% of candidate flow classified, 46 series added, none reclassified). Their tags did not
# change, and should not have — the selection RULE is identical either side. So the boundary is
# a DATE, and these tests exist because a date is exactly the kind of boundary a standing read
# forgets: before this, `mmsell_fill_model` pooled each tag's entire lifetime, which would have
# blended the narrow pre-deploy sample into the wide post-deploy one forever, silently.


def test_every_cohort_book_carries_a_start_and_a_control():
    """A boundary with no control is unusable: the gate these books are read on is RELATIVE
    ("beats its control by >= +1.0c over the same window"), so a start date alone would floor the
    left-hand side of the comparison while leaving the right-hand side at lifetime."""
    assert fm.COHORT_START, "the taxonomy boundary must be recorded somewhere the READ applies"
    for tag, (start, control) in fm.COHORT_START.items():
        assert start.startswith("2026-"), f"{tag}: {start!r} is not an absolute instant"
        assert control and control != tag, f"{tag}: control must be a different book"
        assert control not in fm.COHORT_START, (
            f"{tag}: its control {control} is itself cohort-floored — the comparison would then"
            " silently drop the control's trades too")


def test_only_the_taxonomy_books_are_floored():
    """The exhaustive list, not a spot check. A book that quietly acquires a boundary has had
    part of its history deleted from the number it gets gated on, and nothing else would notice.
    Only books selecting via `mtype=`/`mode=`/`xmtype=` saw their universe change on 2026-08-13:
    every other mmsell book filters on a series substring or on price alone."""
    assert sorted(fm.COHORT_START) == ["Tmmsell1", "Tmmsell2", "Tmmsell5", "Tmmsell6"]


def test_the_floor_defaults_to_keeping_a_books_whole_history():
    """The predicate is spliced into a query covering EVERY mmsell book, so the failure that
    matters is not a missing floor — it is a floor that leaks onto books that never had one."""
    sql, params = fm.cohort_floor_sql()
    assert "-infinity" in sql, "books with no boundary must fall through to their full history"
    assert sql.lstrip().startswith("AND "), "the fragment must compose into an existing WHERE"
    # One (tag, start) pair per book, and the tags bound as PARAMETERS rather than interpolated.
    assert len(params) == 2 * len(fm.COHORT_START)
    assert set(params[::2]) == set(fm.COHORT_START)
    assert sql.count("%s") == len(params)


def test_an_empty_cohort_table_produces_no_predicate_at_all():
    """The caller passes `params or None`, which is what keeps psycopg from reinterpreting the
    `%%` in the LIKE pattern. An empty table must therefore yield an empty fragment, not a
    vacuous `AND TRUE` that still carries placeholders."""
    saved = fm.COHORT_START
    try:
        fm.COHORT_START = {}
        assert fm.cohort_floor_sql() == ("", [])
    finally:
        fm.COHORT_START = saved


def test_no_verdict_before_the_pre_registered_n():
    """Reporting a lead at n=12 is how a noise draw becomes a decision. These books' n restarted
    at the boundary, so they WILL sit under the floor for days — the read has to say so rather
    than rank them."""
    assert "no verdict" in fm._cohort_verdict(12, opt=+4.0, delta=+3.0)
    assert "no verdict" in fm._cohort_verdict(fm.COHORT_GATE_N - 1, opt=+4.0, delta=+3.0)
    # ...and a book with no control trades in the window cannot be verdicted either.
    assert "no verdict" in fm._cohort_verdict(500, opt=+4.0, delta=None)


def test_the_absolute_floor_outranks_the_relative_one():
    """The 2026-08-09 gate fix, restated here because this is now where it executes: five W books
    once cleared the relative bar while losing money, because their control lost more. Beating a
    losing control is not an edge."""
    v = fm._cohort_verdict(300, opt=-0.5, delta=+2.0)
    assert v.startswith("KILL") and "absolute" in v


def test_a_thin_relative_edge_is_a_kill_not_a_pass():
    assert fm._cohort_verdict(300, opt=+2.0, delta=+0.9).startswith("KILL")
    assert not fm._cohort_verdict(300, opt=+2.0, delta=+1.0).startswith("KILL")


def test_passing_the_first_two_conditions_is_not_a_promotion():
    """Condition 3 (realizable > 0) cannot be evaluated here as a discriminator — the projection
    sees entry price only, so every tight-band book INCLUDING the control lands at roughly the
    same realizable number. The verdict must therefore stop short of 'promote'."""
    v = fm._cohort_verdict(300, opt=+3.0, delta=+2.0)
    assert "PASSES" in v and "confirm" in v
    assert "PROMOTE" not in v.upper().replace("PASSES", "")
