"""scripts/bandrev_probe.py — pins the pre-registered constants (docs/BANDREV_PREREG.md), checks the
band-replay fill/stop/settle mechanics on hand-built paths, and checks the event study recovers a
planted over-reaction (and a null) on synthetic paths, without the network."""

from __future__ import annotations

import importlib.util
import pathlib
import random
import sys

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("bandrev_probe", _SCRIPTS / "bandrev_probe.py")
bp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bp)  # type: ignore[union-attr]


def _px(mid, lo=None, hi=None, spread=2.0, ts=0):
    return {"ts": ts, "bid": mid - spread / 2, "ask": mid + spread / 2, "ok": True, "mid": mid,
            "lo": lo, "hi": hi}


def test_constants_match_the_preregistration():
    assert bp.NFL_SERIES == ("KXNFLGAME",)
    assert (bp.WINDOW_START, bp.WINDOW_END) == ("2026-09-01T00:00:00Z", "2026-11-10T00:00:00Z")
    assert (bp.LOOKBACK_S, bp.MOVE_C, bp.CTRL_LO, bp.CTRL_HI) == (180, 5.0, 1.0, 5.0)
    assert (bp.BAND_LO, bp.BAND_HI, bp.MAX_SPREAD) == (30.0, 70.0, 6.0)
    assert bp.HORIZONS == (1, 3, 5, 10, 15) and bp.PRIMARY_H == 5
    assert (bp.COST_C, bp.CTRL_MARGIN, bp.MIN_EVENTS, bp.MIN_MARKETS) == (3.5, 2.0, 500, 40)
    assert (bp.MIN_COVERAGE, bp.MIN_TRIPS, bp.CLIP, bp.BOOT, bp.SEED) == (0.80, 300, 10, 2000, 20261110)
    assert bp.DESIGN == (5, 10, 2) and bp.MAX_MARKETS == 400
    assert bp.MIN_VOLUME == {"NFL": 5000.0, "MENTION": 1000.0}


def test_taker_fee_at_fifty_cents():
    assert bp.taker_fee_c(50) == 1.8      # ceil(17.5c)/10
    assert bp.taker_fee_c(50, 1) == 2.0


def test_band_take_profit_round_trip():
    path = [_px(50), _px(48), _px(46, lo=44), _px(50), _px(56, hi=56)]
    trips = bp.band_replay(path, "no", 5, 10, 2)
    assert trips == [(10, "tp")]          # long YES at 45, offer at 55 traded through


def test_band_touch_is_not_a_fill_unless_optimistic():
    path = [_px(50), _px(46, lo=45), _px(50)]
    assert bp.band_replay(path, "no", 5, 10, 2) == []
    assert bp.band_replay(path, "no", 5, 10, 2, optimistic=True)[0][1] == "settle"


def test_band_stop_pays_the_gap_and_the_taker_fee():
    # filled at 45 on a sweep to 38; next close bid 37 <= 45-2 -> sell 37, fee 1.7c (ceil(16.3)/10)
    path = [_px(50), _px(39, lo=38), _px(38)]
    (pnl, kind), = bp.band_replay(path, "yes", 5, 10, 2)
    assert kind == "stop"
    assert abs(pnl - (37 - 45 - bp.taker_fee_c(37))) < 1e-9


def test_band_short_side_and_settlement_mark():
    # YES trades through 55 -> NO bid at 45 fills; never exits; YES settles -> NO worth 0
    path = [_px(50), _px(54, hi=56), _px(55)]
    (pnl, kind), = bp.band_replay(path, "yes", 5, 10, None)
    assert kind == "settle" and pnl == -45


def test_tp_and_stop_in_one_minute_resolves_to_stop():
    path = [_px(50), _px(46, lo=44), _px(42, hi=56)]
    (_, kind), = bp.band_replay(path, "no", 5, 10, 2)
    assert kind == "stop"


def _walk(n, rng, revert):
    """Mid path near 50 with occasional 6c shocks; `revert` of each shock unwinds over 3 minutes."""
    mids, pending = [50.0], []
    for _ in range(n):
        m = mids[-1] + rng.gauss(0, 0.4)
        if rng.random() < 0.03:
            s = rng.choice((-6.0, 6.0))
            m += s
            pending.extend([-s * revert / 3] * 3)
        if pending:
            m += pending.pop(0)
        mids.append(min(68.0, max(32.0, m)))
    return [_px(x) for x in mids]


def test_event_study_recovers_a_planted_reversion_and_a_null():
    rng = random.Random(1)
    rev = [bp.event_study(_walk(600, rng, 0.8))[0] for _ in range(30)]
    null = [bp.event_study(_walk(600, rng, 0.0))[0] for _ in range(30)]
    r5 = bp.cluster_boot([[r["R"][5] for r in g] for g in rev])[0]
    n5 = bp.cluster_boot([[r["R"][5] for r in g] for g in null])[0]
    assert r5 > 3.0
    assert abs(n5) < 1.5


def test_cluster_boot_brackets_the_mean():
    m, lo, hi = bp.cluster_boot([[1.0, 2.0], [3.0], [4.0, 5.0, 6.0]])
    assert lo <= m <= hi and abs(m - 3.5) < 1e-9
