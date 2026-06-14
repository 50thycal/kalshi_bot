"""Tests for scripts/weather_exit_sweep.py — the TP/SL replay math."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from kalshi_bot.paper.engine import kalshi_fee

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclass field resolution needs the module registered
    spec.loader.exec_module(mod)
    return mod


es = _load("weather_exit_sweep")


def _trade(entry=60, fee=2.0, resolved=100, bids=()):
    return es.Trade(
        trade_id=1, market_ticker="KXHIGHNY-26JUN10-B1", strategy="weather_fav_h20",
        entry_cents=entry, entry_fee_cents=fee, resolved_value=resolved, bids=list(bids),
    )


def test_replay_tp_triggers_at_snapshot_bid_with_exit_fee():
    # Entry 60, TP 10 -> first bid >= 70 triggers; exit at that bid (72), exit fee applied.
    t = _trade(bids=[65.0, 72.0, 90.0])
    pnl, kind = es.replay(t, tp=10, sl=None)
    assert kind == "tp"
    assert pnl == 72.0 - 60.0 - 2.0 - es.fee_cents(72.0)


def test_replay_sl_gap_through_exits_at_gapped_bid():
    # Entry 60, SL 10 -> trigger at bid <= 50; price gaps from 58 to 30, exit at 30.
    t = _trade(bids=[58.0, 30.0, 5.0], resolved=0)
    pnl, kind = es.replay(t, tp=None, sl=10)
    assert kind == "sl"
    assert pnl == 30.0 - 60.0 - 2.0 - es.fee_cents(30.0)


def test_replay_no_trigger_settles_without_exit_fee():
    t = _trade(bids=[62.0, 65.0, 68.0], resolved=100)
    pnl, kind = es.replay(t, tp=10, sl=15)
    assert kind == "settle"
    assert pnl == 100.0 - 60.0 - 2.0  # no exit fee on settlement
    # Hold baseline (no TP/SL) always settles, even through wild swings.
    t2 = _trade(bids=[5.0, 95.0], resolved=0)
    pnl2, kind2 = es.replay(t2, tp=None, sl=None)
    assert kind2 == "settle" and pnl2 == 0.0 - 60.0 - 2.0


def test_replay_empty_path_settles():
    pnl, kind = es.replay(_trade(bids=[]), tp=5, sl=5)
    assert kind == "settle"


def test_fee_matches_engine():
    for price in (1, 25, 37, 50, 56, 75, 99):
        assert es.fee_cents(price) == kalshi_fee(price, 1, True) * 100


def test_sweep_pairs_combos_on_identical_trades():
    # A winner that dips first and a loser that pops first: SL hurts the winner,
    # TP saves nothing on the loser bound for 0 -> hold beats tight exits here.
    winner = _trade(entry=60, fee=2.0, resolved=100, bids=[52.0, 55.0, 80.0, 95.0])
    loser = _trade(entry=60, fee=2.0, resolved=0, bids=[68.0, 50.0, 20.0, 4.0])
    grid = [(None, None), (5.0, 5.0)]
    res = es.sweep([winner, loser], grid)
    assert all(r.n == 2 for r in res)
    hold = next(r for r in res if r.tp is None)
    tight = next(r for r in res if r.tp == 5.0)
    # hold: (100-60-2) + (0-60-2) = -24c total
    assert hold.pnl_cents == -24.0 and hold.settled == 2
    # tight: winner SLs at 52 (52-60-2-fee), loser TPs at 68 (68-60-2-fee)
    assert tight.exits_sl == 1 and tight.exits_tp == 1
    expected = (52.0 - 60.0 - 2.0 - es.fee_cents(52.0)) + (68.0 - 60.0 - 2.0 - es.fee_cents(68.0))
    assert tight.pnl_cents == expected


def test_replay_break_even_arms_then_exits_at_entry():
    # Entry 60. Runs up to 68 (arms BE at +5), then falls back through entry to 58 -> exit.
    t = _trade(entry=60, fee=2.0, resolved=0, bids=[63.0, 68.0, 58.0, 5.0])
    pnl, kind = es.replay(t, tp=None, sl=None, be=5)
    assert kind == "be"
    assert pnl == 58.0 - 60.0 - 2.0 - es.fee_cents(58.0)  # caps the loss near break-even


def test_replay_break_even_not_armed_holds_to_settle():
    # Never gains +5, so BE never arms -> the loser rides to settlement (0).
    t = _trade(entry=60, fee=2.0, resolved=0, bids=[61.0, 63.0, 30.0, 4.0])
    pnl, kind = es.replay(t, tp=None, sl=None, be=5)
    assert kind == "settle"
    assert pnl == 0.0 - 60.0 - 2.0


def test_replay_break_even_winner_keeps_running_to_settlement():
    # Arms at +5 but never falls back to entry -> settles as a full winner.
    t = _trade(entry=60, fee=2.0, resolved=100, bids=[66.0, 80.0, 95.0])
    pnl, kind = es.replay(t, tp=None, sl=None, be=5)
    assert kind == "settle"
    assert pnl == 100.0 - 60.0 - 2.0


def test_fee_per_contract_amortizes_ceil_with_size():
    # qty=1 rounds 1.75c up to 2c; larger size amortizes the ceil toward the true 1.75c.
    assert es.fee_per_contract(50, 1) == 2.0
    assert es.fee_per_contract(50, 100) == 1.75
    assert es.fee_per_contract(50, 1, enabled=False) == 0.0


def test_parse_grid_and_book_mapping():
    assert es.parse_grid("none,5,12.5") == [None, 5.0, 12.5]
    assert es.book_of("weather_low_fav_h14") == ("low", "fav")
    assert es.book_of("weather_cal_h8") == ("high", "cal")
    assert es.book_of("momentum") is None


def test_ops_runner_allowlists_exit_sweep(tmp_path, monkeypatch):
    import json

    req = tmp_path / "request.json"
    monkeypatch.setenv("OPS_REQUEST_PATH", str(req))
    monkeypatch.delenv("DATABASE_URL_RO", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    runner = _load("ops_runner")
    assert "weather_exit_sweep" in runner.ALLOWED_SCRIPTS
    # Dispatches for real; with no DB URL it exits 1 cleanly.
    req.write_text(json.dumps({"type": "script", "name": "weather_exit_sweep", "args": []}))
    assert runner.main() == 1
