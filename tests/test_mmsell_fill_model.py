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
