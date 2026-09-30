"""YOUNG-SERIES probe: the pure logic on inputs whose answers are known by construction
(docs/MMSELL_YOUNG_SERIES_THESIS.md). Pinned: AGE is measured strictly before the decision,
the split is an absolute outcome-blind cut, and the verdict matches the pre-registered bars."""

from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import mmsell_young_series_probe as ys  # noqa: E402
import ops_runner  # noqa: E402

UTC = dt.timezone.utc
T = dt.datetime(2026, 9, 1, tzinfo=UTC)


def test_allowlisted():
    assert "mmsell_young_series_probe" in ops_runner.ALLOWED_SCRIPTS


def test_age_is_days_before_the_decision_and_never_negative():
    assert ys.age_days(T, T - dt.timedelta(days=12)) == 12.0
    assert ys.age_days(T, T) == 0.0
    assert ys.age_days(T, T + dt.timedelta(seconds=1)) is None   # first seen after: unknown
    assert ys.age_days(T, None) is None


def test_split_is_absolute_thirty_days():
    assert ys.is_young(30.0) is True
    assert ys.is_young(30.01) is False
    assert ys.is_young(None) is None
    assert [ys.band_of(a) for a in (0.0, 7.0, 7.5, 30.0, 45.0, 61.0)] == \
        ["<=7d", "<=7d", "8-30d", "8-30d", "31-60d", ">60d"]


def test_verdict_bars():
    # Y0
    assert ys.verdict(0.9, 200, 2.0, 0.5, 150, 1.0, 0.9, 10)[0] == "HOLD (instrument)"
    # Y1 short -> accrual hold, unless the forward read already kills
    assert ys.verdict(1.0, 149, 2.0, 0.5, 50, 1.0, 0.9, 10)[0] == "HOLD (accrual)"
    assert ys.verdict(1.0, 149, 2.0, 0.5, 100, -0.1, 0.9, 10)[0] == "KILL"
    # Y2 kill at the floor
    assert ys.verdict(1.0, 150, 0.0, -1.0, 0, None, 0.9, 10)[0] == "KILL"
    assert ys.verdict(1.0, 150, -0.3, -1.0, 0, None, 0.9, 10)[0] == "KILL"
    # Y2 underpowered
    assert ys.verdict(1.0, 150, 0.5, 0.1, 0, None, 0.9, 10)[0] == "HOLD (underpowered)"
    assert ys.verdict(1.0, 150, 1.5, -0.1, 0, None, 0.9, 10)[0] == "HOLD (underpowered)"
    # Y2 pass, Y3 short
    assert ys.verdict(1.0, 150, 1.5, 0.1, 99, 1.0, 0.9, 10)[0] == "HOLD (accrual)"
    # Y3 kill
    assert ys.verdict(1.0, 150, 1.5, 0.1, 100, 0.0, 0.9, 10)[0] == "KILL"
    # Y4
    assert ys.verdict(1.0, 150, 1.5, 0.1, 100, 0.5, 0.49, 10)[0] == "HOLD (capacity)"
    assert ys.verdict(1.0, 150, 1.5, 0.1, 100, 0.5, 0.9, 4.9)[0] == "HOLD (capacity)"
    assert ys.verdict(1.0, 150, 1.5, 0.1, 100, 0.5, 0.9, 5.0)[0] == "PROMOTE"
