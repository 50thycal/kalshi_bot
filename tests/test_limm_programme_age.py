"""scripts/limm_programme_age.py — pins the pre-registered buckets, measure, paired ratio,
bootstrap, verdict and S4 rule (docs/LIMM_PROGRAMME_AGE.md), and runs end to end on synthetic
rows without the DB."""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import sys
from collections import defaultdict

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("limm_programme_age", _SCRIPTS / "limm_programme_age.py")
pa = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pa)  # type: ignore[union-attr]

UTC = dt.timezone.utc
S0 = dt.datetime(2026, 9, 20, 0, tzinfo=UTC)


def test_constants_match_the_preregistration():
    assert pa.SINCE == "2026-09-08T00:00:00+00:00"
    assert pa.FILL_UNTIL == "2026-10-30T16:00:00+00:00"        # study 5's window is never read
    assert [b[2] for b in pa.BUCKETS] == ["0-6h", "6-24h", "1-3d", "3d+"]
    assert (pa.RATIO_BAR, pa.MIN_PAIRED_PROGRAMMES, pa.MIN_PAIRED_EVENTS) == (2.0, 10, 5)
    assert (pa.BOOT_N, pa.BOOT_SEED, pa.CALIBRATION, pa.LATE_PAYS_BAR_USD) == (2000, 20261108, 1.34, 0.15)
    assert (pa.BAND_LOW, pa.BAND_HIGH, pa.CENSUS_DAYS) == (0.30, 1.00, 28)


def test_bucket_edges():
    assert pa.bucket(0) == "0-6h" and pa.bucket(5.99) == "0-6h" and pa.bucket(6) == "6-24h"
    assert pa.bucket(24) == "1-3d" and pa.bucket(72) == "3d+" and pa.bucket(-1) is None


def test_reward_per_contract_hour_and_the_thin_rule():
    # $240 over 10 days = $1/h; $10 at 2c = 500 contracts against a field of 500 -> share 0.5
    r, thin = pa.per_contract_hour(240.0, S0, S0 + dt.timedelta(days=10), 2, 500)
    assert not thin and abs(r - (1.0 * 0.5 / 2) / 500) < 1e-12
    r, thin = pa.per_contract_hour(240.0, S0, S0 + dt.timedelta(days=10), 2, 100)
    assert thin and r == 0.0                                   # we would be 83% of the side


def test_paired_ratio_bootstrap_and_verdict():
    progs = {f"p{i}": (f"e{i % 6}", 3.0, 1.0) for i in range(12)}
    assert pa.paired_ratio(progs) == 3.0
    ci = pa.bootstrap_ci(progs)
    assert ci == pa.bootstrap_ci(progs) and ci[0] == 3.0          # seeded, no spread here
    assert pa.verdict(progs, ci, 0.01, 0.02) == "EARLY-PAYS"
    assert pa.verdict(progs, ci, 0.03, 0.02) == "NOT-EARLY"        # early fills more
    assert pa.verdict(dict(list(progs.items())[:9]), ci, 0.0, 0.0) == "INSUFFICIENT"
    flat = {f"p{i}": (f"e{i % 6}", 1.5, 1.0) for i in range(12)}
    assert pa.verdict(flat, pa.bootstrap_ci(flat), 0.0, 0.0) == "NOT-EARLY"


def test_s4_rule():
    assert pa.s4_rule("EARLY-PAYS", 0.20) == {"s4_burst_window_hours": 24.0, "s4_after_burst": "flat"}
    assert pa.s4_rule("NOT-EARLY", 0.10) == {"s4_burst_window_hours": None, "s4_after_burst": "off"}
    assert pa.s4_rule("INSUFFICIENT", None)["s4_after_burst"] == "off"


def _snap(tk, hr, start, *, yb=2, nb=97, field=1000.0, pool=240.0, days=10, depth=100.0):
    end = start + dt.timedelta(days=days)
    return (tk, hr, yb, nb, depth, 50.0, field, 5000.0, True, True, tk.split("-")[0],
            tk.rsplit("-", 1)[0], "liquidity", pool, start, end, end + dt.timedelta(days=5))


def test_end_to_end_on_synthetic_rows():
    snaps = []
    for i in range(12):
        tk = f"KXS{i % 6}-26-M{i}"
        start = S0 + dt.timedelta(hours=i)
        for h in range(0, 24, 3):          # early: a thin field (not thin-rule) -> high per-contract reward
            snaps.append(_snap(tk, start + dt.timedelta(hours=h), start, field=600.0))
        for h in range(80, 120, 4):        # late: a crowded field
            snaps.append(_snap(tk, start + dt.timedelta(hours=h), start, field=4000.0))
    hours = pa.build_hours(snaps)
    assert {h["bucket"] for h in hours} == {"0-6h", "6-24h", "3d+"}
    # one early print through our level (100 ahead, 300 traded) -> one early fill
    first = hours[0]
    nxt = first["hr"] + dt.timedelta(hours=1)
    tbh = defaultdict(lambda: defaultdict(list))
    tbh[first["tk"]][nxt].append((nxt, 2, 300, "no"))
    s = pa.summarise(hours, tbh, pa._aware(pa.FILL_UNTIL))
    assert s["paired_programmes"] == 12 and s["paired_events"] == 6
    assert s["ratio_early_over_late"] > 2.0 and s["ci95"][0] > 1.0
    assert s["early_fill_rate"] > 0 and s["late_fill_rate"] == 0
    assert s["verdict"] == "NOT-EARLY"                  # early fills more: the bar is not met
    assert "VERDICT: NOT-EARLY" in pa.render(s)
    s2 = pa.summarise(hours, defaultdict(lambda: defaultdict(list)), pa._aware(pa.FILL_UNTIL))
    assert s2["verdict"] == "EARLY-PAYS" and s2["s4"]["s4_burst_window_hours"] == 24.0


def test_fills_after_study_5_opens_are_never_replayed():
    start = dt.datetime(2026, 10, 31, 0, tzinfo=UTC)
    hours = pa.build_hours([_snap("KXA-26-M", start + dt.timedelta(hours=1), start)])
    nxt = hours[0]["hr"] + dt.timedelta(hours=1)
    tbh = defaultdict(lambda: defaultdict(list))
    tbh["KXA-26-M"][nxt].append((nxt, 2, 10_000, "no"))
    s = pa.summarise(hours, tbh, pa._aware(pa.FILL_UNTIL))
    assert all(b["fill_hours"] == 0 for b in s["buckets"])


def test_census_counts_the_band_per_week():
    now = dt.datetime(2026, 11, 8, 12, tzinfo=UTC)
    start = now - dt.timedelta(days=3)
    # $5 pool over 3 days, field 4,000 at 2c: ~$0.0087/h x ~72h... below or in band by pool
    snaps = [_snap("KXT-26-A", start + dt.timedelta(hours=h), start, pool=12.0, days=3,
                   field=500.0) for h in range(0, 24, 2)]
    hours = pa.build_hours(snaps)
    progs = [("KXT-26-A", "KXT", start, start + dt.timedelta(days=3), None, 12.0),
             ("KXU-26-B", "KXU", start, start + dt.timedelta(days=10), None, 50.0)]
    c = pa.census(hours, progs, now)
    (wk,) = c["weeks"]
    assert wk["all_programmes"] == 2 and wk["all_short_lt_72h"] == 0 and wk["observed"] == 1
    assert wk["all_thin_pool_2_50"] == 2 and wk["thin_pool_series"] == ["KXT:1", "KXU:1"]
    assert wk["band"] + wk["below"] + wk["above"] == 1
    assert "week" in pa.render_census(c)
