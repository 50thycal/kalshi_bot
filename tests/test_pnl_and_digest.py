"""Tests for scripts/weather_pnl.py (decision-table aggregation) and the
weather_experiments digest runner's wiring."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))  # so weather_experiments can import its siblings


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pnl = _load("weather_pnl")


def test_book_and_window_parsing():
    assert pnl.book_of("weather_low_fav_h14") == ("low", "fav")
    assert pnl.book_of("weather_cal_h8") == ("high", "cal")
    assert pnl.book_of("momentum") is None
    assert pnl.window_of("weather_fav_h20") == 20
    assert pnl.window_of("weather_low_nws_h8") == 8
    assert pnl.window_of("weather_fav") is None


def test_aggregate_sums_windows_and_pnl_to_cents():
    rows = [
        # (strategy, settled, wins, pnl_dollars)
        ("weather_fav_h20", 14, 7, -1.36),
        ("weather_fav_h14", 14, 6, -2.68),
        ("weather_fav_h8", 9, 9, 0.44),
        ("weather_nws_h20", 14, 7, -0.39),
        ("weather_low_fav_h20", 6, 6, 0.67),
    ]
    rollup, grid = pnl.aggregate(rows)
    # high fav rollup sums its three windows.
    n, wins, cents = rollup[("high", "fav")]
    assert n == 37 and wins == 22
    assert round(cents, 0) == round((-1.36 - 2.68 + 0.44) * 100, 0)  # -360c
    # grid keeps windows separate (keyed kind, window, book); pnl dollars -> cents.
    assert grid[("high", 14, "fav")][0] == 14
    assert round(grid[("high", 14, "fav")][2], 0) == -268.0
    assert ("low", 20, "fav") in grid
    # Non-weather / unparseable strategies are ignored.
    assert pnl.aggregate([("momentum", 5, 2, 0.1)]) == ({}, {})


def test_report_runs_without_data(capsys):
    pnl.report([])
    out = capsys.readouterr().out
    assert "no settled weather trades yet" in out


def test_experiments_digest_wires_three_probes():
    exp = _load("weather_experiments")
    names = [m.__name__ for _t, m, _a in exp.SECTIONS]
    assert names == ["weather_model_check", "weather_exit_sweep", "weather_entry_study"]
    # The model-check probe is run with --no-live to keep the digest tight.
    model_args = next(a for _t, m, a in exp.SECTIONS if m.__name__ == "weather_model_check")
    assert model_args == ["--no-live"]


def test_ops_runner_allowlists_pnl_and_experiments(tmp_path, monkeypatch):
    req = tmp_path / "request.json"
    monkeypatch.setenv("OPS_REQUEST_PATH", str(req))
    monkeypatch.delenv("DATABASE_URL_RO", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    runner = _load("ops_runner")
    assert {"weather_pnl", "weather_experiments"} <= set(runner.ALLOWED_SCRIPTS)
    req.write_text(json.dumps({"type": "script", "name": "weather_pnl", "args": []}))
    assert runner.main() == 1  # dispatches; exits 1 cleanly with no DB URL
