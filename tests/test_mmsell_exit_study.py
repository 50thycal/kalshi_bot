"""The mmsell exit-rule replay (scripts/mmsell_exit_study) — the counterfactual that decides, per
book, whether a confirmed catastrophic stop or a volatility exit beats hold-to-settlement. Pins
the fee model and the trigger logic independently of the DB, so the numbers the study reports are
trustworthy.

Accounting: a mmsell position buys NO at entry_no (cost basis); it settles NO=100 (longshot
missed) or 0 (longshot hit). An early exit sells the NO back at the current no-bid (taker fee both
entry and exit). The stop fires on yes-mid rising through L for K consecutive ticks; the vol exit
fires when the yes-mid's range over the trailing window exceeds V.
"""

from __future__ import annotations

import importlib.util
import pathlib

_SPEC = importlib.util.spec_from_file_location(
    "mmsell_exit_study",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "mmsell_exit_study.py",
)
ex = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ex)  # type: ignore[union-attr]


def test_fee_matches_paper_formula():
    assert ex.fee_c(92) == 1     # ceil(7*0.92*0.08)=ceil(0.515)
    assert ex.fee_c(50) == 2     # ceil(7*0.25)=ceil(1.75)
    assert ex.fee_c(6) == 1
    assert ex.fee_c(0) == 0 and ex.fee_c(100) == 0


def test_hold_to_settlement_no_triggers():
    # NO bought at 92c, longshot misses -> NO settles 100. No exit rules -> hold.
    pnl, kind = ex.replay_exit(92, 100, [(10, 90), (9, 91), (8, 92)])
    assert kind == "settle"
    assert pnl == 100 - 92 - 1        # +7c, entry fee only


def test_hold_loss_when_longshot_hits():
    # longshot hits -> NO settles 0 -> near-full-stake loss (the tail the stop targets).
    pnl, kind = ex.replay_exit(92, 0, [(10, 90), (30, 70)])   # no stop/vol configured
    assert kind == "settle" and pnl == 0 - 92 - 1             # -93c


def test_confirmed_stop_fires_after_k_ticks_and_caps_loss():
    # yes reprices up through 50 for 2 ticks -> exit at that tick's no-bid (42), capping the loss.
    path = [(10, 90), (55, 45), (58, 42)]
    pnl, kind = ex.replay_exit(92, 0, path, stop_l=50, stop_k=2)
    assert kind == "stop"
    assert pnl == 42 - 92 - ex.fee_c(92) - ex.fee_c(42)       # -53c, vs -93c holding
    assert pnl > (0 - 92 - 1)                                 # strictly better than the hold loss


def test_k2_confirm_defeats_single_spike_whipsaw():
    # one lone tick above the level then back down -> K2 must NOT exit (it holds and wins).
    path = [(10, 90), (55, 45), (12, 88)]
    pnl2, kind2 = ex.replay_exit(92, 100, path, stop_l=50, stop_k=2)
    assert kind2 == "settle" and pnl2 == 7
    # the same spike DOES whipsaw a K1 stop (exits at 45 for a needless loss) — why we confirm.
    pnl1, kind1 = ex.replay_exit(92, 100, path, stop_l=50, stop_k=1)
    assert kind1 == "stop" and pnl1 < 0


def test_volatility_exit_fires_on_range_over_window():
    # yes-mid swings 10->12->30 (range 20 >= 15) once >=3 ticks are in the window -> exit at 70.
    path = [(10, 90), (12, 88), (30, 70)]
    pnl, kind = ex.replay_exit(92, 0, path, vol_v=15)
    assert kind == "vol"
    assert pnl == 70 - 92 - ex.fee_c(92) - ex.fee_c(70)


def test_volatility_needs_minimum_ticks():
    # a big jump on only 2 ticks is below VOL_MIN_TICKS -> no vol trigger -> holds.
    pnl, kind = ex.replay_exit(92, 100, [(10, 90), (40, 60)], vol_v=15)
    assert kind == "settle"


def test_low_volatility_does_not_trigger():
    pnl, kind = ex.replay_exit(92, 100, [(10, 90), (12, 88), (13, 87), (11, 89)], vol_v=15)
    assert kind == "settle" and pnl == 7


def test_none_ticks_are_skipped():
    path = [(None, None), (55, 45), (58, 42)]
    pnl, kind = ex.replay_exit(92, 0, path, stop_l=50, stop_k=2)
    assert kind == "stop"          # the two real ticks still confirm the stop


def test_combined_rule_vol_can_fire_when_stop_would_not():
    # yes-mid never reaches the stop level (max 40 < 50) but its range trips the vol exit.
    path = [(6, 94), (8, 92), (40, 60)]
    pnl, kind = ex.replay_exit(92, 0, path, stop_l=50, stop_k=2, vol_v=15)
    assert kind == "vol" and pnl == 60 - 92 - ex.fee_c(92) - ex.fee_c(60)


def test_rules_grid_has_hold_baseline_first():
    rules = ex._rules()
    assert rules[0][0] == "HOLD"
    labels = [r[0] for r in rules]
    assert any(lbl.startswith("stop L50 K2") for lbl in labels)
    assert any(lbl.startswith("vol V15") for lbl in labels)
