"""Tests for scripts/kalshi_widequote_census.py — the C1-C4 gate measurements, with particular
focus on prior_spread_cents' no-lookahead correctness (a trade may only see a candle that closed
strictly BEFORE it, never one at or after — using a later candle would silently manufacture a
"crossed the wide spread" read for a trade that actually happened after the spread had tightened).
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))  # imports kalshi_arb + kalshi_mm + xvenue_leadlag as siblings


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


wq = _load("kalshi_widequote_census")

NOW = int(time.time())


def _iso(days_from_now: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW + days_from_now * 86400))


def test_series_of_takes_the_leading_ticker_token():
    assert wq._series_of("KXGOVCA-28-DDN") == "KXGOVCA"
    assert wq._series_of("") == "?"


def test_group_by_series_buckets_markets_under_their_series_ticker():
    events = [
        {"series_ticker": "KXGOVCA", "markets": [{"ticker": "A1"}, {"ticker": "A2"}]},
        {"series_ticker": "KXMAYORLA", "markets": [{"ticker": "B1"}]},
    ]
    by_series = wq.group_by_series(events)
    assert {m["ticker"] for m in by_series["KXGOVCA"]} == {"A1", "A2"}
    assert {m["ticker"] for m in by_series["KXMAYORLA"]} == {"B1"}


def test_rank_by_current_spread_excludes_thin_series_and_sorts_widest_first():
    by_series = {
        "WIDE": [{"volume_fp": 50000, "yes_bid_dollars": 0.10, "yes_ask_dollars": 0.90}],
        "TIGHT": [{"volume_fp": 50000, "yes_bid_dollars": 0.48, "yes_ask_dollars": 0.50}],
        "THIN": [{"volume_fp": 5, "yes_bid_dollars": 0.10, "yes_ask_dollars": 0.99}],
    }
    ranked = wq.rank_by_current_spread(by_series, min_volume=1000.0)
    series_order = [r[0] for r in ranked]
    assert series_order == ["WIDE", "TIGHT"]   # THIN dropped, WIDE ranked above TIGHT


def test_current_wide_fraction_excludes_fully_dead_quotes_and_uses_median():
    mkts = [
        {"yes_bid_dollars": 0.0, "yes_ask_dollars": 1.0},    # fully dead -- excluded
        {"yes_bid_dollars": 0.10, "yes_ask_dollars": 0.90},  # 80c live
        {"yes_bid_dollars": 0.40, "yes_ask_dollars": 0.60},  # 20c live
        {"yes_bid_dollars": 0.45, "yes_ask_dollars": 0.55},  # 10c live
    ]
    median, n_live = wq.current_wide_fraction(mkts, wide_threshold=20.0)
    assert n_live == 3
    assert abs(median - 20.0) < 1e-6   # sorted [10,20,80] -> median index 1 = 20


def test_current_wide_fraction_returns_none_with_no_live_quotes():
    mkts = [{"yes_bid_dollars": 0.0, "yes_ask_dollars": 1.0}]
    median, n_live = wq.current_wide_fraction(mkts, wide_threshold=20.0)
    assert median is None and n_live == 0


def test_prior_spread_cents_only_uses_candles_strictly_before_the_trade():
    """The no-lookahead guarantee: a candle at or after the trade timestamp must never be used,
    even if it is the 'closer' one in absolute time."""
    candles = [
        (1000, 0.10, 0.12),   # 2c spread, well before the trade
        (2000, 0.10, 0.90),   # 80c spread, AFTER the trade -- must be ignored
    ]
    # trade at ts=1500: only the ts=1000 candle qualifies (strictly before)
    assert abs(wq.prior_spread_cents(candles, trade_ts=1500) - 2.0) < 1e-6


def test_prior_spread_cents_picks_the_most_recent_qualifying_candle():
    candles = [(1000, 0.10, 0.12), (1400, 0.20, 0.60), (2000, 0.10, 0.90)]
    # trade at ts=1500: candles at 1000 and 1400 qualify (both < 1500); pick 1400 (most recent)
    assert wq.prior_spread_cents(candles, trade_ts=1500) == 40.0


def test_prior_spread_cents_returns_none_with_no_qualifying_candle():
    candles = [(2000, 0.10, 0.90)]
    assert wq.prior_spread_cents(candles, trade_ts=1500) is None


def test_cadence_gap_days_finds_the_minimum_gap_between_distinct_dates():
    day = 86400
    base = (NOW // day) * day
    mkts = [
        {"close_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(base))},
        {"close_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(base + 7 * day))},
        {"close_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(base + 90 * day))},
    ]
    assert wq.cadence_gap_days(mkts) == 7.0


def test_cadence_gap_days_none_with_fewer_than_two_distinct_dates():
    mkts = [{"close_time": _iso(10)}, {"close_time": _iso(10)}]   # same day
    assert wq.cadence_gap_days(mkts) is None


def test_trade_created_ts_parses_iso_created_time():
    ts = wq.trade_created_ts({"created_time": _iso(1)})
    assert abs(ts - (NOW + 86400)) < 5


def test_market_tape_stats_counts_wide_vs_narrow_trades(monkeypatch):
    """End-to-end wiring: fake trades + fake candles, verify the wide/narrow split lands right."""
    window_start = NOW - 100
    trades = [
        {"created_time": _iso_abs(NOW - 50), "count": 1},   # prior candle: 2c -- narrow
        {"created_time": _iso_abs(NOW - 10), "count": 1},   # prior candle: 40c -- wide
    ]
    candles = [(NOW - 200, 0.10, 0.12), (NOW - 60, 0.10, 0.12), (NOW - 20, 0.20, 0.60)]

    monkeypatch.setattr(wq.km, "fetch_trades", lambda ticker, cap: trades)
    monkeypatch.setattr(wq, "fetch_candles", lambda series, ticker, start_ts, end_ts: candles)

    n, wide = wq.market_tape_stats("SERIES", "TICK", window_start, NOW, wide_threshold=20.0,
                                   trade_cap=100)
    assert n == 2
    assert wide == 1


def _iso_abs(unix_ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(unix_ts))
