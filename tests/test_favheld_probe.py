"""scripts/favheld_probe.py — pins the pre-registered constants (docs/FAVHELD_PREREG.md), the two-look
verdict rule and the forward-only guard, and checks the analysis on synthetic data (no DB, no network)."""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import random
import sys

import pytest

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("favheld_probe", _SCRIPTS / "favheld_probe.py")
fp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fp)  # type: ignore[union-attr]


def test_constants_match_the_preregistration():
    assert fp.SERIES == "KXBTCD" and fp.FORWARD_START == dt.date(2026, 11, 10)
    assert fp.LOOK_MIN_DAYS == {1: 60, 2: 120}
    assert (fp.P0_MIN_DECISIONS, fp.P0_MAX_EXCLUSION) == (4000, 0.10)
    assert (fp.P1_RESID_CENTS, fp.P1_CI, fp.P2_NET_EV_CENTS, fp.P2_LOWER_PCT) == (2.0, 0.90, 1.0, 0.025)
    assert (fp.FEE_QTY, fp.BOOT_N, fp.BOOT_SEED, fp.WINDOW_HOURS) == (10, 2000, 20261110, (9, 10, 16))


def test_hour_class_uses_weekday_non_holiday_close_hours():
    tue = dt.date(2026, 11, 17)
    assert fp.hour_class(tue, "KXBTCD-26NOV1710") == "WINDOW"
    assert fp.hour_class(tue, "KXBTCD-26NOV1712") == "QUIET"
    assert fp.hour_class(dt.date(2026, 11, 21), "KXBTCD-26NOV2110") == "QUIET"   # Saturday
    assert fp.hour_class(dt.date(2026, 11, 26), "KXBTCD-26NOV2610") == "QUIET"   # Thanksgiving


def test_verdict_two_look_rule():
    ok = dict(n_days=60, n_dec=5000, exclusion=0.0)
    assert fp.verdict(1, **ok, r=3.0, r_ci=(1.0, 5.0), e=2.5, e_lo=0.3).startswith("PASS")
    assert fp.verdict(1, **ok, r=3.0, r_ci=(1.0, 5.0), e=1.2, e_lo=-0.5).startswith("HOLD (P1 holds")
    assert fp.verdict(1, **ok, r=1.0, r_ci=(-1.0, 3.0), e=0.5, e_lo=-2.0).startswith("KILL (P1 fail")
    assert fp.verdict(1, **ok, r=3.0, r_ci=(1.0, 5.0), e=-0.5, e_lo=-2.0).startswith("KILL (P2 fail")
    ok2 = dict(n_days=120, n_dec=9000, exclusion=0.0)
    final = fp.verdict(2, **ok2, r=3.0, r_ci=(1.0, 5.0), e=1.2, e_lo=-0.1)
    assert final.startswith("KILL (P2 fail") and "unproven at 120 days" in final   # no third look
    assert fp.verdict(2, **ok2, r=3.0, r_ci=(1.0, 5.0), e=1.6, e_lo=0.1).startswith("PASS")
    assert fp.verdict(2, n_days=90, n_dec=9000, exclusion=0.0, r=3.0, r_ci=(1.0, 5.0), e=2.0,
                      e_lo=0.5).startswith("HOLD (instrument")
    assert "label exclusion" in fp.verdict(1, n_days=60, n_dec=5000, exclusion=0.2, r=3.0,
                                           r_ci=(1.0, 5.0), e=2.0, e_lo=0.5)


def _raw(edge: float, days: int, per_day: int = 90, seed: int = 3):
    rng = random.Random(seed)
    raw, results = [], {}
    for d in range(days):
        day = fp.FORWARD_START + dt.timedelta(days=d)
        for i in range(per_day):
            hour = i % 24
            ev = f"KXBTCD-{day:%y}{day.strftime('%b').upper()}{day:%d}{hour:02d}"
            mk = f"{ev}-T{100000 + i}"
            mid_held = rng.uniform(50, 85)
            held_yes = rng.random() < 0.5
            mid = mid_held if held_yes else 100 - mid_held
            y_held = 1 if rng.random() < min(1.0, mid_held / 100 + edge / 100) else 0
            raw.append({"ev": ev, "mk": mk, "day": day.isoformat(), "mid": mid, "ask": mid + 0.5,
                        "bid": mid - 0.5, "k": 100000.0, "spot": 100100.0 if held_yes else 99900.0})
            results[mk] = y_held if held_yes else 1 - y_held
    return raw, results


def test_prepare_maps_held_side_and_counts_missing_labels():
    raw, results = _raw(edge=0.0, days=2, per_day=10)
    results.pop(raw[0]["mk"])
    rows, no_label = fp.prepare(raw, results)
    assert no_label == 1 and len(rows) == 19
    r = rows[0]
    assert r["y_held"] == (r["y"] if r["held_yes"] else 1 - r["y"]) and r["cls"] in ("QUIET", "WINDOW")


def test_analysis_passes_a_planted_edge_and_kills_a_null():
    raw, results = _raw(edge=6.0, days=60)
    rows, nl = fp.prepare(raw, results)
    out = fp.analyse(rows, len(raw), nl, look=1)
    assert out["R"] > 4 and out["E"] > 2 and out["verdict"].startswith("PASS")
    assert set(out["descriptive_by_hour"]) == {"QUIET", "WINDOW"}
    raw, results = _raw(edge=0.0, days=60, seed=9)
    rows, nl = fp.prepare(raw, results)
    out = fp.analyse(rows, len(raw), nl, look=1)
    assert out["verdict"].startswith("KILL")


def test_refuses_a_start_before_the_forward_window():
    with pytest.raises(SystemExit, match="refused"):
        fp.main(["--start", "2026-11-09", "--end", "2027-01-08", "--look", "1"])


def test_probe_is_allowlisted():
    import ops_runner  # noqa: E402 - scripts/ is on sys.path above
    assert "favheld_probe" in ops_runner.ALLOWED_SCRIPTS
