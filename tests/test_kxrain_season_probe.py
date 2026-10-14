"""Tests for scripts/kxrain_season_probe.py — the mmsell10 cell rule, no-lookahead quote reads,
the NO-side P&L sign, and an end-to-end run against a mocked public API."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


kp = _load("kxrain_season_probe")


def test_cell_rule_is_mmsell10_band_and_ceiling():
    assert kp.in_cell(5, 7)          # mid 6, ask 7
    assert not kp.in_cell(6, 8)      # ask above maxyes=7
    assert not kp.in_cell(2, 4)      # mid 3 below lo=5
    assert not kp.in_cell(10, 12)


def test_quote_at_never_reads_a_candle_after_the_target():
    cs = [
        {"end_period_ts": 1000, "yes_bid": {"close": 4}, "yes_ask": {"close": 6}},
        {"end_period_ts": 2000, "yes_bid": {"close": 40}, "yes_ask": {"close": 45}},
    ]
    assert kp.quote_at(cs, 1500) == (4, 6)
    assert kp.quote_at(cs, 999) is None


def test_quote_at_reads_dollar_shape():
    cs = [{"end_period_ts": 1000, "yes_bid": {"close_dollars": "0.05"},
           "yes_ask": {"close_dollars": "0.07"}}]
    assert kp.quote_at(cs, 1000) == (5.0, 7.0)


def test_city_of():
    assert kp.city_of("KXRAIN-26OCT13-NYC") == "NYC"
    assert kp.city_of("KXRAIN") == "?"


def test_end_to_end_with_mocked_api(monkeypatch, capsys):
    close_iso = "2026-01-15T23:59:00Z"
    close = kp._ts(close_iso)
    mkts = [
        {"ticker": "KXRAIN-26JAN15-NYC", "close_time": close_iso, "result": "no",
         "open_time": "2026-01-13T00:00:00Z", "volume_fp": "100"},
        {"ticker": "KXRAIN-26JAN15-SEA", "close_time": close_iso, "result": "yes",
         "open_time": "2026-01-13T00:00:00Z", "volume_fp": "100"},
    ]
    in_cell = [{"end_period_ts": close - h * 3600, "yes_bid": {"close": 5}, "yes_ask": {"close": 7}}
               for h in (1, 6, 12)]

    def fake_get(url: str):
        if "/candlesticks" in url:
            return {"candlesticks": in_cell} if "/series/" in url else {"candlesticks": []}
        if "/historical/markets?" in url:
            return {"markets": [], "cursor": ""}
        return {"markets": mkts, "cursor": ""}

    monkeypatch.setattr(kp.xl, "_get", fake_get)
    assert kp.main(["--max-candle-markets", "10"]) == 0
    out = capsys.readouterr().out
    assert "2026-01  yes=1    no=1" in out
    # One win (+7c) and one loss (-93c) at the first in-cell quote, one day.
    assert "KXRAIN" in out and "26JAN15 SEA" in out
    all_row = [ln for ln in out.splitlines() if ln.strip().startswith("ALL")][0]
    assert "-43.00" in all_row  # (7 - 93) / 2


def test_probe_is_allowlisted_on_the_ops_channel():
    ops_runner = _load("ops_runner")
    assert "kxrain_season_probe" in ops_runner.ALLOWED_SCRIPTS
