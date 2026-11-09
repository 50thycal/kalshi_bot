"""scripts/levelhold_probe.py — pins the pre-registered constants (docs/LEVELHOLD_PREREG.md), checks the
no-lookahead bar construction and level detection, and checks the analysis recovers a planted effect
(and a null) on synthetic data, without the DB or network."""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import random
import sys

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("levelhold_probe", _SCRIPTS / "levelhold_probe.py")
lp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(lp)  # type: ignore[union-attr]

H = 4 * 3600


def test_constants_match_the_preregistration():
    assert lp.SERIES == "KXBTCD" and lp.SPOT_PRODUCT == "BTC-USD"
    assert (lp.MTC_LO, lp.MTC_HI, lp.MTC_TARGET) == (20.0, 40.0, 30.0)
    assert (lp.MID_LO, lp.MID_HI) == (15.0, 85.0)
    assert (lp.LOOKBACK_BARS, lp.ATR_N, lp.PIVOT_N, lp.LEVEL_BAND_ATR, lp.ROUND_GRID) == (30, 14, 2, 0.25, 1000)
    assert (lp.P0_MIN_DAYS, lp.P0_MIN_SWING, lp.P0_MAX_EXCLUSION, lp.P0_MIN_LOOKBACK_SHARE) == (40, 600, 0.10, 0.95)
    assert (lp.P1_SIGNAL_CENTS, lp.KILL_CENTS, lp.P2_NET_EV_CENTS) == (3.0, 1.5, 1.5)
    assert (lp.P1_CI_PRIMARY, lp.P1_CI_SECONDARY) == (0.90, 0.95)
    assert (lp.FEE_QTY, lp.BOOT_N, lp.BOOT_SEED) == (10, 2000, 20261109)


def _candles(n_bars: int, base: float = 100_000.0, start: int = 1_786_000_000 // H * H):
    out = []
    for b in range(n_bars):
        for m in range(240):
            out.append((start + b * H + m * 60, base + 10 * b + (m % 7)))
    return out, start


def test_bars_align_to_utc_4h_and_never_use_an_open_bar():
    candles, start = _candles(32)
    bars = lp.build_bars(candles)
    assert len(bars) == 32 and all(b["valid"] and b["end"] - b["start"] == H for b in bars)
    ends = [b["end"] for b in bars]
    # at 1 minute before bar 30 closes, only bars 0..29 are complete -> exactly 30, all completed
    t = start + 30 * H + H - 60
    win = lp.completed_lookback(bars, ends, t)
    assert win is not None and win[-1]["end"] <= t and len(win) == lp.LOOKBACK_BARS
    assert lp.completed_lookback(bars, ends, start + 29 * H) is None  # only 29 completed


def test_lookback_is_not_full_when_a_bar_is_missing_or_thin():
    candles, start = _candles(32)
    gap = [c for c in candles if not (start + 10 * H <= c[0] < start + 11 * H)]
    bars = lp.build_bars(gap)
    assert lp.completed_lookback(bars, [b["end"] for b in bars], start + 32 * H) is None
    thin = [c for c in candles if not (start + 10 * H <= c[0] < start + 10 * H + 150 * 60)]
    bars = lp.build_bars(thin)
    assert lp.completed_lookback(bars, [b["end"] for b in bars], start + 32 * H) is None


def _bar(h, lo, c):
    return {"high": h, "low": lo, "close": c}


def test_swing_levels_detect_pivots_and_flips():
    bars = [_bar(10, 5, 8), _bar(11, 6, 9), _bar(20, 7, 15), _bar(12, 6, 10), _bar(11, 5, 9),
            _bar(9, 1, 3), _bar(10, 4, 8), _bar(12, 6, 21)]
    lv = lp.swing_levels(bars)
    highs = [x for x in lv if x["kind"] == "high"]
    lows = [x for x in lv if x["kind"] == "low"]
    assert [x["level"] for x in highs] == [20] and highs[0]["flipped"] is True   # a later close 21 > 20
    assert [x["level"] for x in lows] == [1] and lows[0]["flipped"] is False
    flags = lp.arm_flags(20.5, lv, atr=4.0)     # band 1.0 -> near the flipped 20
    assert flags == {"swing": True, "flip": True, "round": False}
    assert lp.arm_flags(101_000.0, lv, atr=4.0) == {"swing": False, "flip": False, "round": True}


def test_atr_is_wilder_and_held_view_maps_sides():
    bars = [_bar(10 + i, 8 + i, 9 + i) for i in range(20)]
    assert abs(lp.wilder_atr(bars) - 2.0) < 1e-9
    assert lp.held_view({"spot": 101.0, "k": 100.0, "mid": 60.0}) == {"held_yes": True, "mid_held": 60.0}
    assert lp.held_view({"spot": 99.0, "k": 100.0, "mid": 60.0}) == {"held_yes": False, "mid_held": 40.0}
    assert lp.held_view({"spot": 100.0, "k": 100.0, "mid": 60.0}) is None


def test_held_leg_ev_uses_ask_or_inverted_bid_and_fee():
    r = {"held_yes": True, "ask": 51.0, "bid": 49.0, "y": 1}
    assert abs(lp.held_leg_ev(r, True) - (100 - 51 - 1.8)) < 1e-9          # buy YES at ask
    assert abs(lp.held_leg_ev(r, False) - (0 - 51 - 1.8)) < 1e-9           # buy NO at 100-49
    r2 = {"held_yes": False, "ask": 31.0, "bid": 29.0, "y": 0}
    assert abs(lp.held_leg_ev(r2, True) - (100 - 71 - 1.5)) < 1e-9         # held = NO at 100-29


def _synthetic(effect: float, seed: int = 7, days: int = 50, per_day: int = 40):
    rng = random.Random(seed)
    rows = []
    for d in range(days):
        day = (dt.date(2026, 9, 1) + dt.timedelta(days=d)).isoformat()
        for _ in range(per_day):
            mid_held = rng.uniform(15, 85)
            swing = rng.random() < 0.35
            rnd = rng.random() < 0.10
            p = mid_held / 100 + (effect / 100 if swing else 0.0)
            y_held = 1 if rng.random() < min(max(p, 0), 1) else 0
            held_yes = rng.random() < 0.5
            mid = mid_held if held_yes else 100 - mid_held
            rows.append({"day": day, "mid_held": mid_held, "held_yes": held_yes, "y_held": y_held,
                         "y": y_held if held_yes else 1 - y_held, "ask": mid + 0.5, "bid": mid - 0.5,
                         "swing": swing, "flip": swing and rng.random() < 0.3, "round": rnd})
    return rows


def test_analysis_recovers_a_planted_level_effect():
    rows = _synthetic(effect=12.0)
    out = lp.analyse(rows, n_raw=len(rows), n_no_label=0, n_no_lookback=0)
    sw = out["arms"]["swing"]
    assert sw["L"] > 8 and sw["ci"][0] > 0 and sw["trade"] == "buy held side"
    assert sw["call"] == "P1+P2" and out["verdict"].startswith("PASS")
    assert "FORWARD CONFIRMATION only" in out["verdict"]


def test_analysis_kills_on_a_null():
    # realistic arm sizes (SWING ~2,600 / ROUND ~750): at the 40/day toy size a single null draw
    # can reach |L| ~3.7c (SD ~1.9c), which is noise, not a bug - see the prereg's note A0
    rows = _synthetic(effect=0.0, seed=11, per_day=150)
    out = lp.analyse(rows, n_raw=len(rows), n_no_label=0, n_no_lookback=0)
    assert abs(out["arms"]["swing"]["L"]) < 3.0
    assert out["arms"]["swing"]["call"] != "P1+P2"
    assert out["verdict"].startswith("KILL") or out["verdict"].startswith("HOLD (weak")


def test_p0_floor_holds_on_thin_data():
    rows = _synthetic(effect=12.0, days=20)
    out = lp.analyse(rows, n_raw=len(rows), n_no_label=0, n_no_lookback=0)
    assert out["verdict"].startswith("HOLD (instrument")
    rows = _synthetic(effect=12.0)
    out = lp.analyse(rows, n_raw=len(rows) * 2, n_no_label=len(rows), n_no_lookback=0)
    assert "label exclusion" in out["verdict"]


def test_probe_is_allowlisted():
    import ops_runner  # noqa: E402 - scripts/ is on sys.path above
    assert "levelhold_probe" in ops_runner.ALLOWED_SCRIPTS
