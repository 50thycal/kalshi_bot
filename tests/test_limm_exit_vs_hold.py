"""scripts/limm_exit_vs_hold.py — pins the pre-registered hold-vs-exit arithmetic and verdict
(docs/LIMM_PROGRAMME_OUTCOMES.md §9) without the DB or the network."""

from __future__ import annotations

import importlib.util
import math
import pathlib
import sys

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("limm_exit_vs_hold",
                                               _SCRIPTS / "limm_exit_vs_hold.py")
le = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(le)  # type: ignore[union-attr]


def test_fill_settle_pnl_acquire_and_dispose():
    assert math.isclose(le.fill_settle_pnl("buy", "no", 5, 100, 0.0, "no"), 95.0)
    assert math.isclose(le.fill_settle_pnl("buy", "no", 5, 100, 0.0, "yes"), -5.0)
    assert math.isclose(le.fill_settle_pnl("sell", "no", 3, 100, 0.0, "no"), -97.0)
    assert math.isclose(le.fill_settle_pnl("sell", "no", 3, 100, 0.0, "yes"), 3.0)


def test_stopped_winner_shows_negative_exit_value():
    # bought 100 NO at 6c, stopped: sold as a YES acquisition at 97 (= NO sold at 3c); NO won.
    fills = [("buy", "buy", "no", 6, 100, 0.0), ("sell", "buy", "yes", 97, 100, 0.0)]
    v = le.market_view(fills, "no", realized=-3.0)
    assert math.isclose(v["hold"], 94.0)
    assert math.isclose(v["exit_value"], -97.0)
    assert math.isclose(v["exit_value_fills"], -97.0)
    assert v["kind"] == "stop"


def test_take_profit_and_no_exit_kinds():
    take = [("buy", "buy", "no", 5, 10, 0.0), ("sell", "sell", "no", 10, 10, 0.0)]
    assert le.market_view(take, "yes", realized=0.5)["kind"] == "take"
    held = [("buy", "buy", "no", 5, 10, 0.0)]
    v = le.market_view(held, "no", realized=9.5)
    assert v["kind"] == "none" and math.isclose(v["exit_value"], 0.0)
    assert le.market_view(held, "", realized=1.0) is None


def test_verdict_floor_and_bands():
    assert le.verdict({"n": 29, "events": 20, "mean": 5, "se": 0.1}).startswith("HOLD")
    assert le.verdict({"n": 40, "events": 20, "mean": 1.0, "se": 0.2}).startswith("PASS")
    assert le.verdict({"n": 40, "events": 20, "mean": -1.0, "se": 0.2}).startswith("FAIL")
    assert le.verdict({"n": 40, "events": 20, "mean": 0.1, "se": 0.2}) == "HOLD"


def test_allowlisted_on_the_ops_runner():
    import ops_runner

    assert "limm_exit_vs_hold" in ops_runner.ALLOWED_SCRIPTS
