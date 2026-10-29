"""The MMSELL fill forward read (scripts/mmsell_fill_forward_read.py) — pins the 2026-10-03
pre-registered P-SLOW / P-SPREAD bars and the 2026-10-29 read rules
(docs/MMSELL_FILL_FORWARD_READ.md) independently of the DB and the network.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("mmsell_fill_forward_read",
                                               _SCRIPTS / "mmsell_fill_forward_read.py")
ff = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ff)  # type: ignore[union-attr]


def test_frozen_bars():
    assert ff.FORWARD_AT == "2026-10-01T00:00:00+00:00"
    assert ff.LINEAGE == ("Fmmsell10", "Hmmsell10", "Jmmsell10", "Kmmsell10")
    assert ff.SLOW_CELL == {"event_stat", "mention", "exact_score", "outright", "game_prop"}
    assert (ff.SLOW_FLOOR, ff.SLOW_MIN_MEAN) == (100, 2.0)
    assert (ff.SPREAD_FLOOR, ff.SPREAD_MIN_SEP) == (80, 3.0)
    assert (ff.BOOT_N, ff.BOOT_SEED) == (5000, 20261003)


def test_outcome_is_settle_no_minus_limit():
    assert ff.outcome_cents("no", 93) == 7
    assert ff.outcome_cents("yes", 93) == -93
    assert ff.outcome_cents("", 93) is None
    assert ff.outcome_cents("scalar", 93) is None


def test_cell_and_spread_sides():
    assert ff.in_slow_cell("mention") is True
    assert ff.in_slow_cell("spread") is False
    assert ff.in_slow_cell("unclassified") is None
    assert ff.spread_side(3) == "wide" and ff.spread_side(2) == "narrow"
    assert ff.spread_side(None) is None


def test_block_bootstrap_detects_a_real_separation():
    a = [(f"d{i % 20}", 5.0 + (i % 3)) for i in range(120)]
    b = [(f"d{i % 20}", -1.0 + (i % 3)) for i in range(200)]
    sep, p5 = ff.block_bootstrap_sep(a, b)
    assert abs(sep - 6.0) < 0.2 and p5 > 0
    assert ff.block_bootstrap_sep([], b) == (None, None)


def test_verdicts():
    assert ff.p_slow_verdict(99, 9.0, 5.0, 1.0).startswith("HOLD")
    assert ff.p_slow_verdict(100, 2.0, 0.1, 0.01) == "PASS"
    assert ff.p_slow_verdict(100, 1.9, 5.0, 1.0).startswith("NO PASS")
    assert ff.p_slow_verdict(150, 3.0, 2.0, -0.5).startswith("NO PASS")
    assert ff.p_spread_verdict(79, 9.0, 2.0).startswith("HOLD")
    assert ff.p_spread_verdict(80, -0.1, -3.0) == "KILL"
    assert ff.p_spread_verdict(80, 3.0, 0.2) == "PASS"
    assert ff.p_spread_verdict(80, 2.0, 0.5) == "HOLD"


def test_allowlisted_on_the_ops_runner():
    import ops_runner

    assert "mmsell_fill_forward_read" in ops_runner.ALLOWED_SCRIPTS
