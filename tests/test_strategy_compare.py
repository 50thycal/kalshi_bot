"""Tests for scripts/weather_strategy_compare.py — the backfill/live/TP-SL comparison."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))  # so the compare script can import its siblings


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


cmp = _load("weather_strategy_compare")
be = _load("weather_backfill_edges")


def _b(ticker, low, high, bid, ask):
    mid = (bid + ask) / 2
    return be.Bkt(ticker, low, high, bid, ask, mid)


def _event(kind, city, date, winner, htc=20.0):
    # favorite is the high-mid bucket B; it wins.
    buckets = [_b("A", None, 71, 4, 6), _b("B", 74, 75, 54, 56), _b("C", 76, 77, 19, 21)]
    return be.Event(city, kind, date, winner, 74.5, [be.Cycle(htc, buckets)])


def test_backfill_favorite_replays_per_kind_window():
    evs = [
        _event("high", "NYC", "2026-06-10", "B", htc=20.0),
        _event("high", "NYC", "2026-06-11", "A", htc=20.0),  # favorite B loses (A wins)
        _event("low", "LAX", "2026-06-10", "B", htc=14.0),
    ]
    bf = cmp.backfill_favorite(evs, [20, 14], fees=True)
    # high h20: 2 favorite entries (both events have a cycle near 20), 1 win
    assert bf[("high", 20)][0] == 2 and bf[("high", 20)][1] == 1
    # low h14: 1 entry, 1 win
    assert bf[("low", 14)][0] == 1 and bf[("low", 14)][1] == 1
    # no low events near h20 -> empty cell
    assert bf[("low", 20)][0] == 0


def test_backfill_favorite_by_city_groups():
    evs = [
        _event("high", "NYC", "2026-06-10", "B", htc=20.0),
        _event("high", "MIA", "2026-06-10", "B", htc=14.0),
    ]
    by_city = cmp.backfill_favorite_by_city(evs, [20, 14], fees=True)
    assert by_city[("high", "NYC")][0] == 1
    assert by_city[("high", "MIA")][0] == 1


def test_ev_and_win_formatting():
    assert cmp._ev([0, 0, 0.0]) == "n/a"
    assert cmp._ev([2, 1, 100.0]) == "+50.0c"
    assert cmp._win([2, 1, 0.0]) == "50%"


def test_ops_runner_allowlists_strategy_compare(tmp_path, monkeypatch):
    req = tmp_path / "request.json"
    monkeypatch.setenv("OPS_REQUEST_PATH", str(req))
    monkeypatch.delenv("DATABASE_URL_RO", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    runner = _load("ops_runner")
    assert "weather_strategy_compare" in runner.ALLOWED_SCRIPTS
    req.write_text(json.dumps({"type": "script", "name": "weather_strategy_compare", "args": []}))
    assert runner.main() == 1  # dispatches; exits 1 cleanly with no DB URL
