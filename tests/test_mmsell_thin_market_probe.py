"""THIN-MARKET probe: the pure logic on inputs whose answers are known by construction
(docs/MMSELL_THIN_MARKET_THESIS.md). Pinned: no candle ending after the decision is counted,
the tercile cut is outcome-blind, and the verdict matches the pre-registered bars."""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import mmsell_thin_market_probe as tm  # noqa: E402
import ops_runner  # noqa: E402

T = 1_790_000_000.0


def test_allowlisted():
    assert "mmsell_thin_market_probe" in ops_runner.ALLOWED_SCRIPTS


def test_volume_window_excludes_the_open_hour_and_old_hours():
    candles = [(T + 1800, 500.0),          # hour still open at decision: must not count
               (T, 10.0),                  # ends exactly at decision: counts
               (T - 23 * 3600, 5.0),       # inside 24 h
               (T - 24 * 3600, 99.0),      # ends exactly 24 h before: outside the window
               (T - 30 * 86400, 7.0)]
    assert tm.volume_before(candles, T, 24) == 15.0
    assert tm.volume_before(candles, T, None) == 10.0 + 5.0 + 99.0 + 7.0


def test_terciles():
    lo, hi = tm.terciles([float(v) for v in range(1, 10)])
    assert (lo, hi) == (3.0, 6.0)


def test_verdict_bars():
    assert tm.verdict(0.8, 200, 3, 1, 2, 50, 1)[0] == "HOLD (instrument)"
    assert tm.verdict(0.9, 99, 3, 1, 2, 50, 1)[0] == "HOLD"
    assert tm.verdict(0.9, 100, -0.1, -2, 0.5, 50, 1)[0] == "KILL"
    assert tm.verdict(0.9, 100, 3, 1, 2, 40, -0.1)[0] == "KILL"
    assert tm.verdict(0.9, 100, 2.0, 0.1, 1.0, 40, 0.1)[0] == "PROMOTE"
    assert tm.verdict(0.9, 100, 2.0, 0.1, 1.0, 39, 0.1)[0] == "HOLD"
    assert tm.verdict(0.9, 100, 2.0, 0.1, 0.9, 40, 0.1)[0] == "HOLD"
    assert tm.verdict(0.9, 100, 2.0, -0.1, 1.0, 40, 0.1)[0] == "HOLD"
    assert tm.verdict(0.9, 100, 2.0, None, None, 0, None)[0] == "HOLD"
