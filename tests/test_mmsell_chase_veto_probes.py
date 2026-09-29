"""RUNAWAY-CHASE and FLOW-VETO probes: the pure decision logic, on inputs whose answers are
known by construction (docs/MMSELL_RUNAWAY_CHASE_THESIS.md, docs/MMSELL_FLOW_VETO_THESIS.md).

The failure modes pinned: a trigger that fires on a flicker, a trigger that reads a tick from
after the fill, a veto window that leaks the decision instant, and a verdict that drifts from
the pre-registered bars.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import mmsell_chase_probe as ch  # noqa: E402
import mmsell_flow_veto_probe as fv  # noqa: E402
import ops_runner  # noqa: E402

T0 = dt.datetime(2026, 9, 20, 12, 0, tzinfo=dt.timezone.utc)


def _t(sec, bid, ask, *, valid=True, usable=True, our=7):
    return ch.Tick(T0 + dt.timedelta(seconds=sec), bid, ask, our, valid, usable)


def test_allowlisted():
    assert "mmsell_chase_probe" in ops_runner.ALLOWED_SCRIPTS
    assert "mmsell_flow_veto_probe" in ops_runner.ALLOWED_SCRIPTS


def test_taker_fee_band():
    # 90-97c NO: 0.07 * P * (1-P) * 100 is 0.20-0.63 -> ceil 1c.
    for c in range(90, 98):
        assert ch.taker_fee_cents(c) == 1
    assert ch.taker_fee_cents(50) == 2   # 1.75 -> 2


def test_trigger_needs_persistence():
    # our yes price p=7; a better NO bid (ask 6) and the YES bid at 5 (NO ask 95 = L+2).
    ticks = [_t(0, 5, 6), _t(10, 5, 6), _t(20, 5, 6)]
    hit = ch.find_trigger(ticks, 7, 2, 15)
    assert hit is not None and hit[0].at == T0 + dt.timedelta(seconds=20) and hit[1] == 95
    assert ch.find_trigger(ticks[:2], 7, 2, 15) is None       # 10 s < 15 s


def test_trigger_run_is_broken_by_a_flicker_or_invalid_book():
    ticks = [_t(0, 5, 6), _t(10, 5, 7), _t(20, 5, 6), _t(30, 5, 6)]   # ask back at our price
    assert ch.find_trigger(ticks, 7, 2, 15) is None
    ticks = [_t(0, 5, 6), _t(10, 5, 6, valid=False), _t(20, 5, 6), _t(30, 5, 6)]
    assert ch.find_trigger(ticks, 7, 2, 15) is None


def test_trigger_respects_k_and_never_reads_unusable_ticks():
    far = [_t(0, 4, 6), _t(20, 4, 6)]            # NO ask 96 = L+3
    assert ch.find_trigger(far, 7, 2, 15) is None
    assert ch.find_trigger(far, 7, 3, 15) is not None
    after_fill = [_t(0, 5, 6, usable=False), _t(20, 5, 6, usable=False)]
    assert ch.find_trigger(after_fill, 7, 2, 0) is None


def test_trigger_requires_being_passed():
    ticks = [_t(0, 5, 7), _t(20, 5, 7)]          # we ARE the best NO bid: not passed
    assert ch.find_trigger(ticks, 7, 2, 0) is None


def test_chase_verdict_bars():
    assert ch.verdict(0.5, 100, 5, 1, 5)[0] == "HOLD (instrument)"
    assert ch.verdict(0.9, 39, 5, 1, 5)[0] == "HOLD"
    assert ch.verdict(0.9, 40, -0.1, -2, 3)[0] == "KILL"
    assert ch.verdict(0.9, 40, 1.0, 0.5, -0.1)[0] == "KILL"
    assert ch.verdict(0.9, 40, 2.0, 0.1, 1.0)[0] == "PROMOTE"
    assert ch.verdict(0.9, 40, 1.9, 0.1, 1.0)[0] == "HOLD"
    assert ch.verdict(0.9, 40, 3.0, -0.1, 1.0)[0] == "HOLD"


def test_bootstrap_is_deterministic_and_sane():
    vals = [1.0] * 50
    assert ch.bootstrap_lb(vals) == 1.0
    assert ch.bootstrap_lb([0.0, 10.0] * 30) == ch.bootstrap_lb([0.0, 10.0] * 30)
    assert fv.bootstrap_diff_lb([3.0] * 20, [1.0] * 20) == 2.0


def test_fill_time_prefers_ws_then_rest_created_then_shifted_reconcile():
    ws = [("a", int(T0.timestamp() * 1000), 1)]
    rest = [("a", None, T0, 1), ("b", "2026-09-20T12:05:00Z", T0, 1), ("c", None, T0, 1)]
    got = ch.fill_times(ws, rest)
    assert got["a"][0] == T0
    assert got["b"][0] == T0 + dt.timedelta(minutes=5)
    assert got["c"][0] == T0 - dt.timedelta(seconds=300)


def test_veto_window_excludes_the_decision_instant():
    t_end = T0.timestamp()
    tape = [(t_end, "yes", 50.0),              # AT the decision instant: must not count
            (t_end - 30, "yes", 2.0), (t_end - 60, "no", 1.0),
            (t_end - 700, "yes", 99.0)]        # outside 10 min
    assert fv.flow(tape, t_end, 600) == (2.0, 1.0)
    assert fv.veto(2.0, 1.0)
    assert not fv.veto(1.0, 1.0)               # tie is not net pressure
    assert not fv.veto(0.5, 0.0)               # under one contract


def test_parse_trade_variants():
    assert fv.parse_trade({"created_time": "2026-09-20T12:00:00Z", "taker_side": "yes",
                           "count_fp": "3.00"})[1:] == ("yes", 3.0)
    assert fv.parse_trade({"created_time": "2026-09-20T12:00:00Z", "taker_side": "no",
                           "count": 2})[1:] == ("no", 2.0)
    assert fv.parse_trade({"created_time": "x", "taker_side": "yes"}) is None


def test_veto_verdict_bars():
    assert fv.verdict(0.8, 100, -3, 5, 1, 0.3)[0] == "HOLD (instrument)"
    assert fv.verdict(0.95, 39, -3, 5, 1, 0.3)[0] == "HOLD"
    assert fv.verdict(0.95, 40, 1, -0.5, -2, 0.3)[0] == "KILL"
    assert fv.verdict(0.95, 40, -3, 1.0, 0.1, 0.6)[0] == "KILL"
    assert fv.verdict(0.95, 40, -1.0, 2.0, 0.1, 0.5)[0] == "PROMOTE"
    assert fv.verdict(0.95, 40, -0.9, 2.5, 0.1, 0.3)[0] == "HOLD"
