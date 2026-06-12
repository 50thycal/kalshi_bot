"""Tests for scripts/weather_backfill_calib.py — window picking, calibration, Wilson."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bc = _load("weather_backfill_calib")


def test_band_and_fee():
    assert bc.band_of(55.0) == (40, 60)
    assert bc.band_of(99.0) == (80, 99)
    assert bc.band_of(0.5) is None
    from kalshi_bot.paper.engine import kalshi_fee
    for price in (25, 50, 75):
        assert bc.fee_cents(price) == kalshi_fee(price, 1, True) * 100


def test_wilson_interval_brackets_rate():
    lo, hi = bc.wilson(50, 100)
    assert lo < 0.5 < hi and 0.39 < lo < 0.41 and 0.59 < hi < 0.61
    # more data -> tighter band around the same rate
    lo2, hi2 = bc.wilson(500, 1000)
    assert (hi2 - lo2) < (hi - lo)
    assert bc.wilson(0, 0) == (0.0, 0.0)


def test_pick_candle_nearest_within_tolerance():
    cs = [
        bc.Candle(hours_to_close=21.0, mid=30.0, ask=31.0, bid=29.0),
        bc.Candle(hours_to_close=19.5, mid=33.0, ask=34.0, bid=32.0),
        bc.Candle(hours_to_close=8.0, mid=80.0, ask=81.0, bid=79.0),
    ]
    assert bc.pick_candle(cs, 20.0).hours_to_close == 19.5  # nearest to 20
    assert bc.pick_candle(cs, 14.0) is None  # 8.0 and 19.5 both >2.5h away
    assert bc.pick_candle([bc.Candle(10.0, None, None, None)], 10.0) is None  # no usable mid


def _market(ticker, result, candles):
    return {"market_ticker": ticker, "event_ticker": "KXHIGHNY-26JUN08",
            "result": result, "candles": candles}


def test_calibrate_pools_bands_and_favorite():
    # Two buckets at h20: a cheap winner (mid 30, underpriced) and a rich loser (mid 60).
    winner = _market("A", "yes", [bc.Candle(20.0, 30.0, 31.0, 29.0)])
    loser = _market("B", "no", [bc.Candle(20.0, 55.0, 56.0, 54.0)])
    band_cells, fav_cells, used = bc.calibrate(
        {"KXHIGHNY-26JUN08": [winner, loser]}, [20.0]
    )
    assert used == 1
    # winner lands in 20-40 band and won.
    n, imp, wins, yev, nev = band_cells[(20.0, (20, 40))]
    assert n == 1 and wins == 1
    assert yev == 100.0 - 31.0 - bc.fee_cents(31.0)  # YES bought at 31, paid off
    # loser in 40-60 band, lost; NO side (100-54=46 ask) paid off.
    n, imp, wins, yev, nev = band_cells[(20.0, (40, 60))]
    assert wins == 0
    assert nev == 100.0 - 46.0 - bc.fee_cents(46.0)
    # favorite = highest mid (the loser at 55) -> favorite lost here.
    fn, fwins, fev = fav_cells[20.0]
    assert fn == 1 and fwins == 0


def test_calibrate_skips_events_without_exactly_one_winner():
    # two winners -> ambiguous backfill coverage -> event dropped
    a = _market("A", "yes", [bc.Candle(20.0, 50.0, 51.0, 49.0)])
    b = _market("B", "yes", [bc.Candle(20.0, 50.0, 51.0, 49.0)])
    _bands, _fav, used = bc.calibrate({"KXHIGHNY-26JUN08": [a, b]}, [20.0])
    assert used == 0
    # zero winners (incomplete) -> also dropped
    c = _market("C", "no", [bc.Candle(20.0, 50.0, 51.0, 49.0)])
    _bands, _fav, used = bc.calibrate({"E": [c]}, [20.0])
    assert used == 0


def test_ops_runner_allowlists_backfill_calib(tmp_path, monkeypatch):
    import json

    req = tmp_path / "request.json"
    monkeypatch.setenv("OPS_REQUEST_PATH", str(req))
    monkeypatch.delenv("DATABASE_URL_RO", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    runner = _load("ops_runner")
    assert "weather_backfill_calib" in runner.ALLOWED_SCRIPTS
    req.write_text(json.dumps({"type": "script", "name": "weather_backfill_calib", "args": []}))
    assert runner.main() == 1  # dispatches; exits 1 cleanly with no DB URL
