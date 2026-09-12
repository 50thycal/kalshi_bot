"""Tests for the three recon censuses promoted by the 2026-09-12 idea-model run
(docs/IDEA_MODEL_20260912.md): METALHALT, PERPMM, EARNBEAT. Pure-function coverage of the
classifiers and the no-lookahead reads; the network paths are exercised only via the ops
channel. Each script is also asserted onto the ops-runner allowlist so a docs/runner drift
cannot advertise a census the runner refuses (XOS-000005's lesson).
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


mh = _load("kalshi_metalhalt_census")
pc = _load("perp_candle_census")
kc = _load("kalshi_kpi_census")

ZoneInfo = pytest.importorskip("zoneinfo").ZoneInfo
ET = ZoneInfo("America/New_York")


def _et(y, mo, d, h, mi=0) -> float:
    return datetime(y, mo, d, h, mi, tzinfo=ET).timestamp()


# --- METALHALT: the Pyth metals halt calendar -------------------------------------------


def test_feed_halted_daily_break_and_weekend():
    wed = 2026, 9, 9                      # a Wednesday
    assert not mh.feed_halted(_et(*wed, 12, 30), ET)
    assert mh.feed_halted(_et(*wed, 17, 0), ET)
    assert mh.feed_halted(_et(*wed, 17, 59), ET)
    assert not mh.feed_halted(_et(*wed, 18, 0), ET)
    assert mh.feed_halted(_et(2026, 9, 11, 17, 0), ET)    # Friday 17:00 → closed
    assert mh.feed_halted(_et(2026, 9, 12, 3, 0), ET)     # Saturday
    assert mh.feed_halted(_et(2026, 9, 13, 17, 59), ET)   # Sunday before reopen
    assert not mh.feed_halted(_et(2026, 9, 13, 18, 0), ET)


def test_classify_window_outside_tail_inside_boundary():
    wed = 2026, 9, 9
    cls, dec = mh.classify_window(_et(*wed, 12, 0), _et(*wed, 12, 15), ET)
    assert cls == "outside" and dec == _et(*wed, 12, 15)
    cls, dec = mh.classify_window(_et(*wed, 16, 50), _et(*wed, 17, 5), ET)
    assert cls == "tail" and dec == _et(*wed, 17, 0)
    cls, dec = mh.classify_window(_et(*wed, 17, 15), _et(*wed, 17, 30), ET)
    assert cls == "inside" and dec == _et(*wed, 17, 15)
    cls, _ = mh.classify_window(_et(*wed, 17, 45), _et(*wed, 18, 0), ET)
    assert cls == "boundary"            # settles on the first post-resume print → not frozen
    cls, dec = mh.classify_window(_et(2026, 9, 11, 16, 0), _et(2026, 9, 12, 9, 0), ET)
    assert cls == "tail" and dec == _et(2026, 9, 11, 17, 0)   # Friday close → Saturday
    assert mh.classify_window(0.0, 0.0, ET)[0] == "unknown"


def test_discount_cents_uses_the_winning_side():
    c = {"yes_ask": {"close_dollars": 0.94}, "yes_bid": {"close_dollars": 0.05}}
    assert mh.discount_cents(c, "yes") == pytest.approx(6.0)
    assert mh.discount_cents(c, "no") == pytest.approx(5.0)
    assert mh.discount_cents({}, "yes") is None


def test_metal_series_prefix_is_precise():
    assert mh.METAL_SERIES.search("KXGOLD15M")
    assert mh.METAL_SERIES.search("KXSILVERW")
    assert not mh.METAL_SERIES.search("KXOLYMPICGOLD")
    assert not mh.METAL_SERIES.search("KXCORN")


# --- PERPMM: candle fields + summary ---------------------------------------------------------


def test_flatten_keys_and_high_low_detection():
    keys = pc.flatten_keys({"price": {"open": 1, "high": 2, "low": 0, "close": 1}, "volume": 3})
    assert "price.high" in keys and "price.low" in keys and "volume" in keys
    assert pc.has_high_low(keys)
    assert not pc.has_high_low(pc.flatten_keys({"price": {"close": 1}, "bid": {"close": 1}}))


def test_summarize_reports_activity_spread_and_range():
    cs = [
        {"price": {"close": 100.0, "high": 100.2, "low": 99.9}, "bid": {"close": 99.95},
         "ask": {"close": 100.05}, "volume": 5, "volume_notional": 500},
        {"price": {"close": 100.0, "high": 100.0, "low": 100.0}, "bid": {"close": 99.95},
         "ask": {"close": 100.05}, "volume": 0},
    ]
    s = pc.summarize(cs)
    assert s["n"] == 2 and s["active"] == 1 and s["active_share"] == pytest.approx(0.5)
    assert s["spread_bps"] == pytest.approx(10.0)
    assert s["range_bps"] == pytest.approx((30.0 + 0.0) / 2, rel=1e-3)
    assert s["vol_per_active_min"] == pytest.approx(5.0)
    assert pc.summarize([])["active_share"] == 0.0


def test_field_reader_tolerates_flat_dollar_keys():
    assert pc._field({"price_close_dollars": "2.5"}, "price", "close") == pytest.approx(2.5)
    assert pc._field({"price": {"close_dollars": "2.5"}}, "price", "close") == pytest.approx(2.5)


# --- EARNBEAT: classifier, bands, no-lookahead quote --------------------------------------


def test_kpi_type_classifier():
    assert kc.kpi_type_of("Will Tesla deliver at least 400,000 vehicles in Q3?") == "kpi_threshold"
    assert kc.kpi_type_of("Netflix Q3 revenue between $11.5B and $11.8B?") == "kpi_range"
    assert kc.kpi_type_of('Will Apple say "AI" on the earnings call?') == "mention"
    assert kc.kpi_type_of("Something unrelated") == "other"


def test_is_kpi_requires_a_kpi_token_with_a_company_category():
    ev = {"category": "Companies", "title": "Uber Q3 revenue"}
    assert kc.is_kpi(ev, {"title": "Above $12B?"})
    assert not kc.is_kpi({"category": "Companies", "title": "CEO steps down?"}, {})
    assert kc.is_kpi({"category": "Politics"}, {"series_ticker": "KXKPIUBER"})


def test_band_and_calibration():
    assert kc.band_of(0) == "00-10" and kc.band_of(55) == "50-70" and kc.band_of(99) == "90-100"
    cal = kc.calibration([(52.0, "yes"), (48.0, "yes"), (60.0, "no"), (5.0, "no")])
    assert cal["50-70"]["n"] == 2 and cal["50-70"]["yes_rate"] == pytest.approx(50.0)
    assert cal["30-50"]["gap"] == pytest.approx(100.0 - 48.0)


def test_quote_at_or_before_never_reads_a_later_candle():
    cs = [
        {"end_period_ts": 100, "yes_bid": {"close_dollars": 0.40}, "yes_ask": {"close_dollars": 0.44}},
        {"end_period_ts": 200, "yes_bid": {"close_dollars": 0.90}, "yes_ask": {"close_dollars": 0.94}},
    ]
    assert kc.quote_at_or_before(cs, 150) == pytest.approx(42.0)
    assert kc.quote_at_or_before(cs, 200) == pytest.approx(92.0)
    assert kc.quote_at_or_before(cs, 50) is None


# --- ops-runner allowlist parity ---------------------------------------------------------


def test_censuses_are_allowlisted():
    from ops_runner import ALLOWED_SCRIPTS
    for name in ("kalshi_metalhalt_census", "perp_candle_census", "kalshi_kpi_census"):
        assert name in ALLOWED_SCRIPTS
