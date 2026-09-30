"""CELL-SIZE census: the pure per-cell rule on inputs whose answers are known by construction
(docs/MMSELL_CELL_SIZE_CENSUS.md)."""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import mmsell_cell_size_census as cs  # noqa: E402
import ops_runner  # noqa: E402


def test_allowlisted():
    assert "mmsell_cell_size_census" in ops_runner.ALLOWED_SCRIPTS


def test_median_and_bootstrap():
    assert cs.median([]) is None
    assert cs.median([3.0]) == 3.0
    assert cs.median([1.0, 4.0, 2.0, 3.0]) == 2.5
    lb = cs.bootstrap_mean_lb([1.0] * 200)
    assert lb == 1.0
    assert cs.bootstrap_mean_lb([]) is None


def test_cell_rule():
    assert cs.cell_verdict(149, 5.0, 4.0, 5.0, 5.0)[0] == "unreadable"
    assert cs.cell_verdict(150, 0.9, 0.5, 1.0, 5.0)[0] == "fails"          # C2 mean
    assert cs.cell_verdict(150, 1.5, 0.0, 1.0, 5.0)[0] == "fails"          # C2 lower bound
    assert cs.cell_verdict(150, 1.5, 0.2, 3.6, 5.0)[0] == "fails"          # C3 gap 2.1
    status, size, _ = cs.cell_verdict(150, 1.5, 0.2, 3.5, 5.0)             # C3 gap 2.0
    assert (status, size) == ("PASS", 3)
    assert cs.cell_verdict(150, 1.5, 0.2, 3.5, 2.4)[:2] == ("PASS", 2)     # C4 caps the size
    assert cs.cell_verdict(150, 1.5, 0.2, 3.5, None)[:2] == ("PASS", 3)    # no tape: unverified
    assert cs.cell_verdict(150, 1.5, 0.2, None, 5.0)[0] == "fails"         # no counterfactual
