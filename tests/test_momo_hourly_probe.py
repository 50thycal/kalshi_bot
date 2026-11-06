"""scripts/momo_hourly_probe.py — pins the pre-registered constants (docs/MOMO_HOURLY_PREREG.md)
and checks the analysis recovers a known answer on synthetic data, without the DB or network."""

from __future__ import annotations

import datetime as dt
import importlib.util
import math
import pathlib
import random
import sys

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("momo_hourly_probe", _SCRIPTS / "momo_hourly_probe.py")
mp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mp)  # type: ignore[union-attr]


def test_constants_match_the_preregistration():
    assert mp.SERIES == "KXBTCD"
    assert (mp.MTC_LO, mp.MTC_HI, mp.MTC_TARGET) == (20.0, 40.0, 30.0)
    assert (mp.MID_LO, mp.MID_HI) == (15.0, 85.0)
    assert (mp.P0_MIN_DAYS, mp.P0_MIN_DECISIONS, mp.P0_MAX_EXCLUSION) == (40, 1000, 0.10)
    assert (mp.P1_SIGNAL_CENTS, mp.KILL_CENTS, mp.P2_NET_EV_CENTS) == (3.0, 1.5, 1.5)
    assert (mp.FEE_QTY, mp.BOOT_N, mp.RECON_MIN_CORR) == (10, 2000, 0.9)


def test_fee_matches_kalshi_formula():
    # 10 contracts at 50c: ceil(0.07*10*0.25*100)=ceil(17.5)=18c total -> 1.8c each
    assert mp.fee_cents(50.0) == 1.8
    # 10 at 90c: ceil(0.07*10*0.09*100)=ceil(6.3)=7 -> 0.7c
    assert mp.fee_cents(90.0) == 0.7
    assert mp.fee_cents(0.0) == 0.0 and mp.fee_cents(100.0) == 0.0


def test_event_prefix_covers_every_hour_of_the_date():
    lo, hi = mp.event_prefix("KXBTCD", dt.date(2026, 10, 15))
    assert lo == "KXBTCD-26OCT15"
    for h in ("00", "17", "23"):
        assert lo <= f"KXBTCD-26OCT15{h}" < hi
    assert not (lo <= "KXBTCD-26OCT1600" < hi)


def test_momentum_and_buckets():
    assert mp.momentum_bp(None, 1.0) is None
    assert abs(mp.momentum_bp(101.0, 100.0) - 99.503) < 1e-3
    edges = mp.quintile_edges([float(i) for i in range(100)])
    assert edges == [20.0, 40.0, 60.0, 80.0]
    assert [mp.bucket(x, edges) for x in (0, 20, 79.9, 99)] == [0, 1, 3, 4]


def test_split_rows_counts_only_label_failures_as_exclusion():
    raw = [{"mk": "A", "m": 1.0, "ask": 50.0, "bid": 49.0},
           {"mk": "B", "m": None, "ask": 50.0, "bid": 49.0},
           {"mk": "C", "m": 1.0, "ask": 50.0, "bid": 49.0}]
    usable, no_label, dropped = mp.split_rows(raw, {"A": 1, "B": 0})
    assert [r["mk"] for r in usable] == ["A"] and no_label == 1 and dropped == 1


def _synthetic(beta: float, n_days: int = 60, per_day: int = 40, seed: int = 7) -> list[dict]:
    """Quotes at mid = 100*p_fair; truth = p_fair shifted by beta*momentum (beta=0: efficient)."""
    rng = random.Random(seed)
    rows = []
    for d in range(n_days):
        for _ in range(per_day):
            m = rng.gauss(0, 20)                      # bp
            p = rng.uniform(0.2, 0.8)
            mid = 100 * p
            p_true = min(0.99, max(0.01, p + beta * m / 100))
            rows.append({"day": f"d{d}", "m": m, "mid": mid, "ask": mid + 0.5, "bid": mid - 0.5,
                         "y": 1 if rng.random() < p_true else 0, "fmid": None, "tm15": m})
    return rows


def test_efficient_market_is_killed():
    out = mp.analyse(_synthetic(0.0), 2400, 0)
    assert out["verdict"].startswith(("KILL", "HOLD (weak"))
    assert abs(out["S"]) < 5


def test_injected_underreaction_is_detected_as_follow():
    out = mp.analyse(_synthetic(0.6), 2400, 0)   # strong under-reaction
    assert out["S"] > 3 and out["trade_sign"] == "follow"
    assert out["S_ci90"][0] > 0
    assert out["verdict"].startswith(("PASS", "P1 SIGNAL"))


def test_p0_floor_and_reconstruction_gate():
    assert mp.verdict(39, 5000, 0.0, 0.99, 5, 1, 9, 3, 1).startswith("HOLD (instrument: P0")
    assert mp.verdict(60, 5000, 0.11, 0.99, 5, 1, 9, 3, 1).startswith("HOLD (instrument: P0")
    assert mp.verdict(60, 5000, 0.0, 0.5, 5, 1, 9, 3, 1).startswith("HOLD (instrument: momentum")
    assert mp.verdict(60, 5000, 0.0, math.nan, 5, 1, 9, 3, 1).startswith("PASS")
    assert mp.verdict(60, 5000, 0.0, 0.99, -5, -9, -1, 0.5, -1).startswith("P1 SIGNAL")
    assert mp.verdict(60, 5000, 0.0, 0.99, 2.0, 0.5, 3.5, 3, 1).startswith("HOLD (weak")


# --- scripts/short_window_census.py (same run, recon only) ----------------------------------
_SPEC2 = importlib.util.spec_from_file_location("short_window_census", _SCRIPTS / "short_window_census.py")
swc = importlib.util.module_from_spec(_SPEC2)
_SPEC2.loader.exec_module(swc)  # type: ignore[union-attr]


def test_census_summarize_counts_volume_yes_share_and_spread():
    settled = [{"volume_fp": "100.00", "result": "yes"}, {"volume_fp": "300.00", "result": "no"},
               {"volume_fp": "200.00", "result": "yes"}]
    open_mkts = [{"yes_bid_dollars": "0.4800", "yes_ask_dollars": "0.5000"},
                 {"yes_bid_dollars": "0.0000", "yes_ask_dollars": "0.0300"}]   # one-sided: ignored
    r = swc.summarize("KXTEST", settled, open_mkts, 2.0)
    assert r["exists"] and r["settled"] == 3 and r["settled_per_day"] == 1.5
    assert r["settled_volume"] == 600 and r["volume_per_day"] == 300 and r["median_vol_per_mkt"] == 200
    assert r["yes_share"] == 0.667 and r["open"] == 2 and r["open_median_spread_c"] == 2.0


def test_census_absent_series_and_allowlist():
    assert swc.summarize("KXNONE", [], [], 7.0)["exists"] is False
    import ops_runner  # noqa: E402 - scripts/ is on sys.path above
    assert "short_window_census" in ops_runner.ALLOWED_SCRIPTS
    assert "momo_hourly_probe" in ops_runner.ALLOWED_SCRIPTS
