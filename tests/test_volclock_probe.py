"""scripts/volclock_probe.py — pins the pre-registered constants (docs/VOLCLOCK_PREREG.md) and checks
the analysis recovers a known answer on synthetic data, without the DB or network."""

from __future__ import annotations

import datetime as dt
import importlib.util
import math
import pathlib
import random
import sys

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("volclock_probe", _SCRIPTS / "volclock_probe.py")
vp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vp)  # type: ignore[union-attr]


def test_constants_match_the_preregistration():
    assert vp.SERIES == "KXBTCD"
    assert vp.WINDOW_HOURS == (9, 10, 16) and vp.CONTROL_HOURS == (12, 13, 14)
    assert (vp.MTC_LO, vp.MTC_HI, vp.MTC_TARGET) == (35.0, 50.0, 40.0)
    assert (vp.MID_LO, vp.MID_HI) == (10.0, 90.0)
    assert vp.CLOSE_SPOT_TOL_MIN == 3
    assert (vp.P0_MIN_TRADING_DAYS, vp.P0_MIN_ROWS_PER_CLASS) == (35, 300)
    assert (vp.P0_MAX_EXCLUSION, vp.P0_MIN_CLOSE_COVERAGE, vp.P0_MIN_HOUR_MATCH) == (0.05, 0.90, 0.99)
    assert vp.P1_MIN_RATIO == 1.15
    assert (vp.KILL_CENTS, vp.P2_SIGNAL_CENTS, vp.P3_NET_EV_CENTS) == (1.5, 3.0, 1.5)
    assert (vp.FEE_QTY, vp.BOOT_N, vp.BOOT_SEED) == (10, 2000, 20261106)
    assert dt.date(2026, 9, 7) in vp.NYSE_HOLIDAYS and dt.date(2026, 10, 12) not in vp.NYSE_HOLIDAYS


def test_fee_matches_kalshi_formula():
    assert vp.fee_cents(50.0) == 1.8
    assert vp.fee_cents(90.0) == 0.7 and vp.fee_cents(10.0) == 0.7
    assert vp.fee_cents(0.0) == 0.0


def test_hour_classes_and_trading_days():
    thu = dt.date(2026, 10, 15)
    assert vp.hour_class(thu, 9) == "WINDOW" and vp.hour_class(thu, 16) == "WINDOW"
    assert vp.hour_class(thu, 13) == "CONTROL" and vp.hour_class(thu, 11) is None
    assert vp.hour_class(dt.date(2026, 10, 17), 10) == "WKND_W"      # Saturday
    assert vp.hour_class(dt.date(2026, 10, 18), 12) == "WKND_C"      # Sunday
    assert vp.hour_class(dt.date(2026, 9, 7), 9) is None             # Labor Day: excluded
    assert vp.hour_class(dt.date(2026, 10, 12), 9) == "WINDOW"       # Columbus Day trades


def test_exact_tickers_never_a_string_range():
    evs = vp.day_event_tickers("KXBTCD", dt.date(2026, 10, 15))
    assert len(evs) == 24 and evs[9] == "KXBTCD-26OCT1509" and vp.ticker_hour(evs[16]) == 16
    assert "= any(%(evs)s)" in vp.DAY_SQL and "= any(%(dec_evs)s)" in vp.DAY_SQL


def test_et_conversion_across_the_dst_change():
    # 2026-10-15 13:00Z = 09:00 EDT; 2026-11-03 14:00Z = 09:00 EST (DST ended 11-01 06:00Z).
    assert vp.utc_to_et(dt.datetime(2026, 10, 15, 13, 0)).hour == 9
    assert vp.utc_to_et(dt.datetime(2026, 11, 3, 14, 0)).hour == 9
    assert vp.utc_to_et(dt.datetime(2026, 11, 1, 5, 59)).hour == 1      # still EDT
    assert vp.utc_to_et(dt.datetime(2026, 11, 1, 6, 0)).hour == 1       # now EST
    aware = dt.datetime(2026, 10, 15, 12, 20, tzinfo=dt.timezone.utc)   # T-40 for the 09:00 close
    assert vp.close_hour_et(aware, 40.0) == 9
    assert vp.close_hour_et(dt.datetime(2026, 11, 3, 13, 21), 38.5) == 9


def test_crossing_side():
    below = vp.crossing(30.0, 31.0, 29.0, spot=100.0, strike=101.0, y=1)
    assert below["side"] == "yes" and below["px_mid"] == 30.0 and below["yx"] == 1
    assert below["buy_cross"] == 31.0 and below["buy_other"] == 71.0
    above = vp.crossing(70.0, 71.0, 69.0, spot=102.0, strike=101.0, y=1)
    assert above["side"] == "no" and above["px_mid"] == 30.0 and above["yx"] == 0
    assert above["buy_cross"] == 31.0 and above["buy_other"] == 71.0
    assert vp.crossing(50.0, 51.0, 49.0, spot=101.0, strike=101.0, y=1) is None


def _synthetic(window_edge: float, window_vol: float = 1.5, n_days: int = 46, per_ev: int = 4,
               seed: int = 11) -> tuple[list[dict], list[dict]]:
    """Each trading day: 3 WINDOW + 3 CONTROL events. Quote = fair crossing probability; in WINDOW
    events the TRUE crossing probability is higher by window_edge (cents), i.e. under-priced."""
    rng = random.Random(seed)
    rows, events = [], []
    for d in range(n_days):
        day = f"d{d:03d}"
        for cls, hours in (("WINDOW", (9, 10, 16)), ("CONTROL", (12, 13, 14))):
            for h in hours:
                ev = f"{day}-{h}"
                vol = window_vol if cls == "WINDOW" else 1.0
                events.append({"ev": ev, "day": day, "cls": cls, "wd": d % 5, "hour": h,
                               "move": abs(rng.gauss(0, 20 * vol))})
                shock = rng.random()        # one shared path per event: strikes are clustered
                for _ in range(per_ev):
                    px = rng.uniform(10, 50)
                    p_true = min(0.99, (px + (window_edge if cls == "WINDOW" else 0.0)) / 100)
                    yx = 1 if (0.7 * shock + 0.3 * rng.random()) < p_true else 0
                    rows.append({"ev": ev, "day": day, "cls": cls, "wd": d % 5, "hour": h,
                                 "px_mid": px, "yx": yx, "buy_cross": px + 0.5,
                                 "buy_other": 100 - px + 0.5})
    return rows, events


_COUNTS = {"exclusion": 0.0, "dropped": 0, "hour_match": 1.0}


def test_efficient_clock_is_killed():
    rows, events = _synthetic(0.0)
    out = vp.analyse(rows, events, _COUNTS)
    assert out["R"] > 1.15 and not out["p0_failures"]
    assert out["verdict"].startswith(("KILL (P2", "HOLD (P2"))


def test_no_extra_volatility_kills_on_p1():
    rows, events = _synthetic(10.0, window_vol=1.0)
    assert vp.analyse(rows, events, _COUNTS)["verdict"].startswith("KILL (P1")


def test_injected_window_underpricing_is_detected():
    rows, events = _synthetic(12.0, n_days=120)
    out = vp.analyse(rows, events, _COUNTS)
    assert out["D"] > 3 and out["D_ci90"][0] > 0 and out["trade"] == "buy crossing side"
    assert out["verdict"].startswith(("PASS", "P2 SIGNAL"))


def test_verdict_ladder():
    ok = []
    assert vp.verdict(["x"], 2, 1.5, 9, 5, 12, 3, 1).startswith("HOLD (instrument")
    assert vp.verdict(ok, 1.10, 1.02, 9, 5, 12, 3, 1).startswith("KILL (P1")
    assert vp.verdict(ok, 1.30, 0.99, 9, 5, 12, 3, 1).startswith("KILL (P1")
    assert vp.verdict(ok, 1.30, 1.10, 1.0, 0.2, 1.8, 3, 1).startswith("KILL (P2: the clock is priced)")
    assert vp.verdict(ok, 1.30, 1.10, 2.0, -1, 5, 3, 1).startswith("KILL (P2")
    assert vp.verdict(ok, 1.30, 1.10, 4.0, -1, 9, 3, 1).startswith("HOLD (P2 under-powered")
    assert vp.verdict(ok, 1.30, 1.10, 2.0, 0.5, 3.5, 3, 1).startswith("HOLD (P2 weak")
    assert vp.verdict(ok, 1.30, 1.10, 4.0, 1.0, 7.0, 2.0, 0.1).startswith("PASS")
    assert vp.verdict(ok, 1.30, 1.10, -4.0, -7.0, -1.0, 1.0, -0.5).startswith("P2 SIGNAL")
    assert vp.verdict(ok, math.nan, math.nan, 4, 1, 7, 2, 1).startswith("KILL (P1")


def test_label_tally_adds_up_and_reads_both_tiers():
    raw = [{"ev": "E1", "mk": "A"}, {"ev": "E1", "mk": "B"}, {"ev": "E2", "mk": "C"},
           {"ev": "E3", "mk": "D"}]
    tally = vp.label_tally(raw, {"A": 1, "C": 0}, {"E1": vp.ks.LIVE, "E2": vp.ks.HISTORICAL})
    assert tally.counts == {vp.ks.LIVE: 1, vp.ks.HISTORICAL: 1, vp.ks.NOT_FOUND: 2}
    assert tally.total == len(raw)


def test_p0_failures_and_allowlist():
    assert vp.p0_failures(46, 500, 500, 0.0, 1.0, 1.0) == []
    assert len(vp.p0_failures(30, 200, 500, 0.06, 0.8, 0.98)) == 5
    import ops_runner  # noqa: E402 - scripts/ is on sys.path above
    assert "volclock_probe" in ops_runner.ALLOWED_SCRIPTS
