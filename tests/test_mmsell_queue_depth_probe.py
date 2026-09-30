"""QUEUE-DEPTH probe: the pure logic on inputs whose answers are known by construction
(docs/MMSELL_QUEUE_DEPTH_THESIS.md). Pinned: the split is an absolute outcome-blind cut on the
true queue reading, censored orders are never silently dropped, and the verdict matches the
pre-registered bars."""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import mmsell_queue_depth_probe as qd  # noqa: E402
import ops_runner  # noqa: E402


def test_allowlisted():
    assert "mmsell_queue_depth_probe" in ops_runner.ALLOWED_SCRIPTS


def test_split_and_bands():
    assert qd.is_deep(500.0) is True
    assert qd.is_deep(499.99) is False
    assert qd.is_deep(None) is None
    assert [qd.band_of(a) for a in (0, 49.9, 50, 199, 200, 499, 500, 1999, 2000, None)] == \
        ["<50", "<50", "50-199", "50-199", "200-499", "200-499", "500-1999", "500-1999",
         ">=2000", "censored"]


def test_verdict_bars():
    ok = dict(cov_p=1.0, cov_c=1.0, n_deep=150, n_not=150, sep=1.5, lb=0.2,
              c_n_deep=150, c_n_not=150, c_sep=0.5, policy_usd=5.0, all_usd=4.0)

    def v(**kw):
        return qd.verdict(**{**ok, **kw})[0]

    assert v() == "PROMOTE"
    assert v(cov_p=0.79) == "HOLD (instrument)"
    assert v(cov_c=0.79) == "HOLD (instrument)"
    # Q1 short -> accrual, unless the confirmation already kills
    assert v(n_deep=99) == "HOLD (accrual)"
    assert v(n_deep=99, c_sep=-0.1) == "KILL"
    assert v(n_deep=99, c_sep=-0.1, c_n_not=99) == "HOLD (accrual)"   # Q3 floor not met
    # Q2
    assert v(sep=0.0) == "KILL"
    assert v(sep=0.5) == "HOLD (underpowered)"
    assert v(sep=1.5, lb=-0.1) == "HOLD (underpowered)"
    # Q3
    assert v(c_sep=0.0) == "KILL"
    assert v(c_n_deep=99) == "HOLD (accrual)"
    # Q4
    assert v(policy_usd=3.9) == "HOLD (policy)"
    assert v(policy_usd=4.0) == "PROMOTE"
    assert v(policy_usd=None) == "HOLD (policy)"
