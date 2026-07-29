"""The realizable-P&L projection (scripts/theta_fill_model.project_realizable) — identical contract
to mmsell_fill_model's version (theta uses the same maker-sell convention), pinned here
independently of the DB plumbing. See scripts/theta_fill_model.py's module docstring for why theta
needs its own copy: it falls back to a BORROWED (cross-strategy) calibration until theta has its
own live fill history.

Key invariants: only price cells with enough live fills count ("coverage"); realizable P&L and fill
rate are trade-weighted over covered trades; uncovered trades are excluded, never guessed.
"""

from __future__ import annotations

import importlib.util
import pathlib

_SPEC = importlib.util.spec_from_file_location(
    "theta_fill_model",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "theta_fill_model.py",
)
fm = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fm)  # type: ignore[union-attr]


def test_no_calibration_means_no_coverage():
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
    hist = {7: [10, 12.0]}
    calib = {7: (50, 0.8, -0.6)}
    r = fm.project_realizable(hist, calib, min_cell_fills=8)
    assert r["covered_n"] == 10
    assert abs(r["est_fill_rate"] - 0.8) < 1e-9
    assert abs(r["est_realizable_cents"] - (-0.6)) < 1e-9
    assert abs(r["opt_cents"] - 1.2) < 1e-9


def test_trade_weighted_across_cells():
    hist = {6: [30, 60.0], 9: [10, -60.0]}
    calib = {6: (40, 0.65, 2.0), 9: (40, 0.69, -6.0)}
    r = fm.project_realizable(hist, calib, min_cell_fills=8)
    assert r["covered_n"] == 40
    assert abs(r["est_realizable_cents"] - 0.0) < 1e-9
    assert abs(r["est_fill_rate"] - (0.65 * 30 + 0.69 * 10) / 40) < 1e-9


def test_partial_coverage_reported():
    hist = {6: [30, 60.0], 20: [10, 50.0]}
    calib = {6: (40, 0.65, 2.0)}
    r = fm.project_realizable(hist, calib, min_cell_fills=8)
    assert r["total_n"] == 40 and r["covered_n"] == 30
    assert abs(r["est_realizable_cents"] - 2.0) < 1e-9


def test_rows_to_calib_converts_pnl_to_cents():
    rows = [(7, 4, 10, 0.05), (9, 2, 5, None)]
    calib = fm._rows_to_calib(rows)
    assert calib[7] == (4, 0.4, 5.0)
    nf, fr, rp = calib[9]
    assert nf == 2 and abs(fr - 0.4) < 1e-9 and rp == 0.0


def test_realistic_theta4_projection_matches_low_coverage_caution():
    # Loosely mirrors the manual check run against real data: theta4's price mix sits mostly
    # above the mmsell-calibrated cells (6-12c), so only a minority of its trades are covered
    # and the realizable estimate for that covered slice reads far below the optimistic paper
    # number -- exactly the caution the borrowed-calibration path exists to surface.
    hist = {
        6: [2, 56.0], 7: [3, 96.0], 8: [4, 118.0], 9: [5, 210.0], 10: [5, 230.0],
        11: [7, 316.0], 12: [5, 235.0],
        14: [10, 650.0], 15: [11, 270.0], 18: [17, 760.0],
    }
    calib = {
        6: (62, 62 / 98, 1.77), 7: (59, 59 / 74, 0.92), 8: (64, 64 / 92, -0.81),
        9: (58, 58 / 85, -5.79), 10: (75, 75 / 93, -1.67), 11: (28, 28 / 46, -0.71),
        12: (10, 10 / 12, 11.00),
    }
    r = fm.project_realizable(hist, calib, min_cell_fills=8)
    covered_expected = 2 + 3 + 4 + 5 + 5 + 7 + 5
    assert r["covered_n"] == covered_expected
    assert r["total_n"] > r["covered_n"]  # cells 14/15/18 uncovered -> partial coverage
    assert r["opt_cents"] > 20  # optimistic paper number is large and positive
    assert r["est_realizable_cents"] < 5  # realizable collapses well below optimistic
