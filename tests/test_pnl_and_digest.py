"""Tests for scripts/weather_pnl.py (decision-table aggregation) and the
weather_experiments digest runner's wiring."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

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


def test_city_of_maps_high_and_low_tickers():
    assert pnl.city_of("KXHIGHLAX-26JUN12-B74.5") == "LAX"
    assert pnl.city_of("KXLOWTNYC-26JUN12-B60.5") == "NYC"
    assert pnl.city_of("KXHIGHPHIL-26JUN12-T80") == "PHIL"
    assert pnl.city_of("KXNOTAWEATHER-X") is None
    assert pnl.city_of(None) is None


def test_granular_groups_and_ranks_by_city_window_book():
    # (strategy, market_ticker, resolved_value, pnl_dollars)
    rows = [
        ("weather_low_fav_h20", "KXLOWTLAX-26JUN12-B60", 100, 0.50),
        ("weather_low_fav_h20", "KXLOWTLAX-26JUN13-B60", 100, 0.40),
        ("weather_low_fav_h20", "KXLOWTNYC-26JUN12-B55", 0, -0.60),
        ("weather_fav_h8", "KXHIGHCHI-26JUN12-B80", 100, 0.30),
    ]
    full, by_city, by_win = pnl.granular(rows)
    # the LAX low-fav h20 cell has 2 trades, both winners
    assert full[("low", "fav", 20, "LAX")][0] == 2
    assert full[("low", "fav", 20, "LAX")][1] == 2
    # by-city rollup keys collapse the window
    assert ("low", "fav", "LAX") in by_city and ("low", "fav", "NYC") in by_city
    # ranking by per-trade with min_n=2 keeps only the LAX cell (n=2), NYC/CHI are n=1
    ranked = pnl._rank(full, min_n=2, top=5)
    assert len(ranked) == 1 and ranked[0][0] == ("low", "fav", 20, "LAX")


def test_report_best_runs_without_data(capsys):
    pnl.report_best([], top=5, min_n=4)
    out = capsys.readouterr().out
    assert "Best strategies" in out and "no cells with n >= 4" in out


def test_experiments_digest_wires_probes():
    exp = _load("weather_experiments")
    names = [m.__name__ for _t, m, _a in exp.SECTIONS]
    assert names == ["weather_model_check", "weather_exit_sweep",
                     "weather_window_sweep", "weather_entry_study",
                     "weather_strategy_compare"]
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


def test_digest_marks_no_positions_on_the_no_side():
    """A NO position (signed negative qty, cost basis on the NO side) must be marked against the
    NO quote (100 - yes_ask), not the yes-bid.

    Regression: the digest previously did `(yes_bid - avg) * |qty|` for every position, which on a
    mmsell/theta NO book reports a big LOSS on a winning position — these are cheap-tail sells, so
    the yes-quote collapsing toward zero is exactly what a WIN looks like."""
    digest = _load("weather_digest")

    # Bought NO at 93c; yes now 1/2c -> no-bid = 98c -> +5c/contract on 3 contracts.
    mark, unreal = digest._mark_position(-3.0, 93.0, yes_bid=1, yes_ask=2)
    assert mark == 98
    assert unreal == pytest.approx(0.15)

    # The same position under the old yes-bid arithmetic would have read ~-$2.76.
    assert unreal > 0

    # A YES position still marks at the yes-bid (sell side), unchanged.
    mark, unreal = digest._mark_position(3.0, 40.0, yes_bid=55, yes_ask=57)
    assert mark == 55
    assert unreal == pytest.approx(0.45)

    # A losing NO position still reads as a loss: bought NO at 93c, yes ran to 60c -> no-bid 40c.
    mark, unreal = digest._mark_position(-3.0, 93.0, yes_bid=58, yes_ask=60)
    assert mark == 40
    assert unreal == pytest.approx(-1.59)


def test_digest_mark_is_best_effort_on_a_missing_quote():
    """A quote lookup failure must omit the mark, never fall back to the wrong side."""
    digest = _load("weather_digest")
    assert digest._mark_position(-3.0, 93.0, yes_bid=1, yes_ask=None) == (None, None)
    assert digest._mark_position(3.0, 40.0, yes_bid=None, yes_ask=57) == (None, None)


class _FakeResp:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _quote_with(monkeypatch, digest, market: dict):
    monkeypatch.setattr(digest.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResp({"market": market}))
    return digest._kalshi_quote("KXANY-TICKER")


def test_digest_ignores_a_zero_size_quote_on_a_closed_market(monkeypatch):
    """A closed market keeps serving `yes_ask: 1.0000` with `yes_ask_size_fp: 0.00` — a
    placeholder, not an offer. Marking a NO position against it gives 100 - 100 = 0, i.e. a
    total loss reported on a position that is actually winning.

    Regression: measured 2026-08-15, 11 closed-but-unsettled WTI positions read -$10.33
    unrealized this way while their last prints (yes 1-2c) put them ~+$0.60 to the good."""
    digest = _load("weather_digest")
    bid, ask, src = _quote_with(monkeypatch, digest, {
        "status": "closed",
        "yes_bid_dollars": "0.0000", "yes_bid_size_fp": "0.00",
        "yes_ask_dollars": "1.0000", "yes_ask_size_fp": "0.00",
        "last_price_dollars": "0.0100",
    })
    assert src == "stale"
    assert (bid, ask) == (1, 1)                       # falls back to the last TRADE, not the book

    # And the mark that comes out is the winning one, not a wipeout.
    mark, unreal = digest._mark_position(-1.0, 94.0, bid, ask)
    assert mark == 99 and unreal == pytest.approx(0.05)


def test_digest_keeps_a_live_quote_with_real_size(monkeypatch):
    """The ordinary open-market path is untouched: sized quotes are used as-is and flagged live."""
    digest = _load("weather_digest")
    bid, ask, src = _quote_with(monkeypatch, digest, {
        "status": "active",
        "yes_bid_dollars": "0.0100", "yes_bid_size_fp": "250.00",
        "yes_ask_dollars": "0.0200", "yes_ask_size_fp": "300.00",
        "last_price_dollars": "0.5000",
    })
    assert (bid, ask, src) == (1, 2, "live")


def test_digest_omits_the_mark_when_nothing_priceable_survives(monkeypatch):
    """No sized quote AND no last trade -> no mark at all, rather than a fabricated one."""
    digest = _load("weather_digest")
    bid, ask, src = _quote_with(monkeypatch, digest, {
        "status": "closed",
        "yes_ask_dollars": "1.0000", "yes_ask_size_fp": "0.00",
    })
    assert (bid, ask, src) == (None, None, "none")
    assert digest._mark_position(-1.0, 94.0, bid, ask) == (None, None)
