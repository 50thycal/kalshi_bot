"""Tests for scripts/weather_backfill_edges.py — pure edge math."""

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


be = _load("weather_backfill_edges")


def _b(ticker, low, high, bid, ask):
    mid = (bid + ask) / 2 if bid is not None and ask is not None else None
    return be.Bkt(ticker, low, high, bid, ask, mid)


def test_bucket_mid_and_value_in_bucket():
    assert be.bucket_mid_f(74, 75) == 74.5
    assert be.bucket_mid_f(None, 73) == 73        # "X or below" -> edge
    assert be.bucket_mid_f(95, None) == 95        # "X or above" -> edge
    assert be.value_in_bucket(74.4, 74, 75) and not be.value_in_bucket(76.0, 74, 75)


def test_pearson_basic():
    assert abs(be.pearson([1, 2, 3, 4], [2, 4, 6, 8]) - 1.0) < 1e-9
    assert abs(be.pearson([1, 2, 3, 4], [4, 3, 2, 1]) + 1.0) < 1e-9
    assert be.pearson([1, 1, 1], [1, 2, 3]) is None  # zero variance
    assert be.pearson([1], [1]) is None              # too few


def test_implied_mean_and_ladder_sums():
    buckets = [_b("A", None, 73, 4, 6), _b("B", 74, 75, 54, 56), _b("C", 76, 77, 19, 21)]
    # weighted mean ~ dominated by B (mid 55) at 74.5
    im = be.implied_mean_f(buckets)
    assert 73.5 < im < 75.0
    sm, sb, sa = be.ladder_sums(buckets)
    assert sm == 5 + 55 + 20 and sb == 4 + 54 + 19 and sa == 6 + 56 + 21
    assert be.ladder_sums([_b("A", 1, 2, 5, 7)]) is None  # too sparse


def test_overround_detects_sellable_credit():
    # A degenerate ladder whose BIDS sum to >100 (would be a free sell-the-ladder arb).
    over = [_b("A", None, 73, 40, 42), _b("B", 74, 75, 40, 42), _b("C", 76, 77, 40, 42)]
    # htc 10h -> falls in the 6-12 bin
    ev = be.Event("NYC", "high", "2026-06-10", "B", 74.5, [be.Cycle(10.0, over)])
    bins, sellable = be.overround([ev], fees=True)
    assert len(bins[(6, 12)]) == 1 and bins[(6, 12)][0] == 123  # sum_mid (3 x mid 41)
    n, cred, net = sellable[(6, 12)]
    # sum_bid=120, minus 100 payout, minus 3 leg fees -> still a credit
    assert n == 1 and cred == 1 and net > 0


def test_persistence_strategy_and_correlation():
    # Two-day series, same (city, kind). Yesterday landed in bucket B (74.5); today the
    # open ladder is identical and B wins again -> the "yesterday bucket" entry wins.
    ladder = [_b("A", None, 73, 4, 6), _b("B", 74, 75, 50, 52), _b("C", 76, 77, 30, 32)]
    d1 = be.Event("NYC", "high", "2026-06-09", "B", 74.5, [be.Cycle(24.0, ladder)])
    d2 = be.Event("NYC", "high", "2026-06-10", "B", 74.5, [be.Cycle(24.0, ladder)])
    res = be.persistence([d1, d2], fees=True)
    # persistence strategy bought B (contains yesterday's 74.5) at ask 52, won
    pn, pw, ppnl = res["strat"]["pers"]
    assert pn == 1 and pw == 1
    assert ppnl == 100.0 - 52.0 - be.fee_cents(52.0)
    # favorite is also B (highest mid) -> same here
    assert res["strat"]["fav"][0] == 1


def test_ops_runner_allowlists_backfill_edges(tmp_path, monkeypatch):
    import json

    req = tmp_path / "request.json"
    monkeypatch.setenv("OPS_REQUEST_PATH", str(req))
    monkeypatch.delenv("DATABASE_URL_RO", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    runner = _load("ops_runner")
    assert "weather_backfill_edges" in runner.ALLOWED_SCRIPTS
    req.write_text(json.dumps({"type": "script", "name": "weather_backfill_edges", "args": []}))
    assert runner.main() == 1
