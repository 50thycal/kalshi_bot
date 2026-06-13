"""Tests for scripts/weather_window_sweep.py — window picking + entry replay."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ws = _load("weather_window_sweep")
T0 = datetime(2026, 6, 12, 0, 0, tzinfo=timezone.utc)


def _rows(htc, asks_mids):
    """asks_mids: {ticker: (low, high, ask, mid)} at a single snapshot time htc."""
    cap = T0 + timedelta(hours=100 - htc)  # monotonic so cycles cluster in time order
    return [
        {"event_ticker": "E", "market_ticker": tk, "low_f": lo, "high_f": hi,
         "yes_ask_cents": ask, "mid_cents": mid, "hours_to_close": htc, "captured_at": cap}
        for tk, (lo, hi, ask, mid) in asks_mids.items()
    ]


def test_pick_cycle_nearest_within_tolerance():
    cycles = [ws.Cycle(20.0, []), ws.Cycle(13.5, []), ws.Cycle(8.0, [])]
    assert ws.pick_cycle_nearest(cycles, 14.0).hours_to_close == 13.5
    assert ws.pick_cycle_nearest(cycles, 11.0) is None  # 13.5 and 8.0 both >1.5h away
    assert ws.pick_cycle_nearest([], 14.0) is None


def test_favorite_and_forecast_bucket_selection():
    cyc = ws.cluster_cycles(_rows(20.0, {
        "A": (None, 73, 5, 5.0),
        "B": (74, 75, 40, 55.0),   # highest mid -> favorite
        "C": (76, 77, 30, 22.0),
    }))[0]
    assert ws.favorite_bucket(cyc).market_ticker == "B"
    # forecast 76.4 rounds to 76 -> bucket C
    assert ws.forecast_bucket(cyc, 76.4).market_ticker == "C"
    assert ws.forecast_bucket(cyc, None) is None
    assert ws.forecast_bucket(cyc, 200.0) is None  # outside every bucket


def test_entry_pnl_winner_and_loser():
    win = ws.Bucket("B", 74, 75, 40.0, 55.0)
    assert ws._entry_pnl(win, "B", fees=True) == 100.0 - 40.0 - ws.fee_cents(40.0)
    assert ws._entry_pnl(win, "C", fees=True) == 0.0 - 40.0 - ws.fee_cents(40.0)
    assert ws._entry_pnl(ws.Bucket("B", 74, 75, None, 55.0), "B", fees=True) is None


def test_sweep_accumulates_fav_and_nws_per_window():
    # One high event: favorite bucket B (mid 55) loses, forecast bucket C wins.
    cyc_rows = _rows(20.0, {"A": (None, 73, 5, 5.0), "B": (74, 75, 40, 55.0), "C": (76, 77, 30, 22.0)})
    ev = {"kind": "high", "city": "LAX", "winner": "C", "forecast": 76.4,
          "cycles": ws.cluster_cycles(cyc_rows)}
    cell, cell_city = ws.sweep([ev], windows=[20.0], fees=True)
    # favorite = B, lost
    fn, fw, fc = cell["fav"][("high", 20.0)]
    assert fn == 1 and fw == 0
    assert fc == 0.0 - 40.0 - ws.fee_cents(40.0)
    # nws = C (contains 76.4), won, bought at 30
    nn, nw, nc = cell["nws"][("high", 20.0)]
    assert nn == 1 and nw == 1
    assert nc == 100.0 - 30.0 - ws.fee_cents(30.0)
    # city breakdown tracks favorite only
    assert cell_city[("high", "LAX", 20.0)][0] == 1
    # a window with no nearby snapshot contributes nothing
    cell2, _ = ws.sweep([ev], windows=[4.0], fees=True)
    assert cell2["fav"] == {} and cell2["nws"] == {}


def test_ops_runner_allowlists_window_sweep(tmp_path, monkeypatch):
    import json

    req = tmp_path / "request.json"
    monkeypatch.setenv("OPS_REQUEST_PATH", str(req))
    monkeypatch.delenv("DATABASE_URL_RO", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    runner = _load("ops_runner")
    assert "weather_window_sweep" in runner.ALLOWED_SCRIPTS
    req.write_text(json.dumps({"type": "script", "name": "weather_window_sweep", "args": []}))
    assert runner.main() == 1  # dispatches; exits 1 cleanly with no DB URL
