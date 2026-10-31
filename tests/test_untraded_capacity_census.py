"""scripts/untraded_capacity_census.py — pins the pre-registered census measures, tiers and
shortlist rule (docs/UNTRADED_CAPACITY_CENSUS.md) without the network or the DB."""

from __future__ import annotations

import importlib.util
import pathlib
import sys

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("untraded_capacity_census",
                                               _SCRIPTS / "untraded_capacity_census.py")
uc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(uc)  # type: ignore[union-attr]


def _m(t, ev, day, vol, res):
    return {"ticker": t, "event_ticker": ev, "close_time": f"2026-10-{day:02d}T20:00:00Z",
            "volume_fp": str(vol), "result": res}


def test_summarize_counts_cadence_ladder_and_yes_share():
    ms = [_m("KXA-E1-1", "KXA-E1", 1, 100, "no"), _m("KXA-E1-2", "KXA-E1", 1, 300, "yes"),
          _m("KXA-E2-1", "KXA-E2", 2, 500, "no")]
    s = uc.summarize(ms)
    assert (s["settled"], s["events"], s["close_dates"]) == (3, 2, 2)
    assert s["total_volume"] == 900 and s["median_volume"] == 300
    assert s["ladder_width"] == 1.5 and abs(s["yes_share"] - 1 / 3) < 1e-9


def test_volume_prefers_fp_then_int():
    assert uc.market_volume({"volume_fp": "12.5", "volume": 3}) == 12.5
    assert uc.market_volume({"volume": 7}) == 7.0
    assert uc.market_volume({}) == 0.0


def test_tiers_and_flags():
    assert uc.tier("KXA", {"KXA"}, set()) == "traded"
    assert uc.tier("KXB", set(), {"KXB"}) == "B"
    assert uc.tier("KXC", set(), set()) == "A"
    assert uc.flags("Climate and Weather") == "graveyard-weather"
    assert uc.flags("Crypto") == "crypto"
    assert uc.flags("Sports") == ""


def test_shortlist_rule_is_all_four_floors():
    ok = {"settled": 60, "close_dates": 20, "median_volume": 500.0, "total_volume": 100_000.0}
    assert uc.passes(ok)
    for k, v in (("settled", 59), ("close_dates", 19), ("median_volume", 499.0),
                 ("total_volume", 99_999.0)):
        assert not uc.passes({**ok, k: v})


def test_allowlisted_on_the_ops_runner():
    import ops_runner

    assert "untraded_capacity_census" in ops_runner.ALLOWED_SCRIPTS
