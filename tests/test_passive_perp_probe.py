"""Independent economic counterexamples and missing-data guardrails."""
from datetime import timedelta

import pytest

from scripts import passive_perp_probe as p


def row(i, premium=0, bid=100, ask=100, reference=100):
    return {"ticker": "KXBTCPERP", "captured_at": p.START + timedelta(minutes=3 * i),
            "bid": bid, "ask": ask, "premium_bps": premium,
            "reference_price": reference, "settlement_mark_price": (bid + ask) / 2}


def path():
    return [row(i, (-1) ** i) for i in range(20)] + [
        row(20, 100, 101, 101), row(21, 0, 110.5, 110.5, 110.5)]


def test_premium_can_converge_while_short_loses():
    trades, censored, _ = p.score(path())
    assert not censored
    assert len(trades) == 1
    assert trades[0]["premium_convergence_bps"] == 100
    assert trades[0]["maker_gross_bps"] == pytest.approx(-9.5 / 101 * 10000)


def test_quote_sides_and_fee_notionals():
    e, x = row(0, bid=99, ask=101), row(1, bid=109, ask=111)
    long, short = p.returns(e, x, 1), p.returns(e, x, -1)
    assert long["maker_gross_bps"] == pytest.approx(12 / 99 * 10000)
    assert long["taker_gross_bps"] == pytest.approx(8 / 101 * 10000)
    assert short["maker_gross_bps"] == pytest.approx(-8 / 101 * 10000)
    assert short["taker_gross_bps"] == pytest.approx(-12 / 99 * 10000)
    assert short["maker_fee_scenario_ex_funding_bps"] == pytest.approx(
        -8 / 101 * 10000 - 2 * (1 + 109 / 101))


def test_terminal_entry_is_censored_not_dropped():
    trades, censored, _ = p.score(path()[:-1])
    assert not trades
    assert censored[0]["reason"] == "end_of_tape"


@pytest.mark.parametrize("fault", ["missing", "gap", "crossed"])
def test_invalid_exit_path_cannot_supply_profit(fault):
    rows = path()
    if fault == "missing":
        rows[-1]["bid"] = None
    elif fault == "crossed":
        rows[-1]["ask"] = 1
    else:
        rows[-1]["captured_at"] += timedelta(hours=1)
    trades, censored, _ = p.score(rows)
    assert not trades
    assert censored[0]["reason"] == "gap_or_invalid_exit_path"


def test_current_observation_is_not_in_training_window():
    trades, _, _ = p.score(path())
    # Prior alternating +/-1 sample sd = sqrt(20/19), mean zero.
    assert trades[0]["entry_z"] == pytest.approx(100 / (20 / 19) ** 0.5)


def test_empty_and_single_asset_never_promote():
    for rows in ([], path()):
        result = p.summarize(rows)
        assert result["verdict"] == "HOLD"
        assert result["net_pnl_bps"] is None
        assert result["assets"]["KXETHPERP"]["maker_gross_bps"] is None
        assert result == p.summarize(rows)


def test_ro_environment_required(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL_RO", raising=False)
    monkeypatch.setenv("DATABASE_URL", "must-not-be-used")
    assert p.main([]) == 1
    assert "HOLD" in capsys.readouterr().out


@pytest.mark.parametrize("gross,expected", [(-1, "KILL_LEANING"), (10, "HOLD")])
def test_adequate_screen_never_claims_net_profit(monkeypatch, gross, expected):
    template = p.returns(row(0), row(1), 1)
    trades = [{**template, "ticker": ticker,
               "entry_at": f"2026-08-{30 + i % 2}T00:00:00+00:00" if i % 3 else "2026-09-01T00:00:00+00:00",
               "maker_gross_bps": gross, "maker_fee_scenario_ex_funding_bps": gross - 4,
               "control_maker_gross_bps": 0}
              for ticker in p.TICKERS for i in range(30)]
    monkeypatch.setattr(p, "score", lambda rows: (trades, [], {}))
    result = p.summarize([])
    assert result["verdict"] == expected
    assert result["net_pnl_bps"] is None
