from __future__ import annotations

from kalshi_bot.risk.manager import RiskManager
from kalshi_bot.scanner.metrics import compute_metrics


def _good_metrics():
    ob = {"orderbook": {"yes": [[48, 300]], "no": [[49, 300]]}}
    market = {
        "ticker": "T",
        "volume": 5000,
        "open_interest": 2000,
        "close_time": "2030-01-01T00:00:00Z",
    }
    return compute_metrics(market, ob)


def test_blocks_in_scanner_mode(settings):
    decision = RiskManager(settings).evaluate(
        signal=None, metrics=_good_metrics(), account_state={"cash_balance": 100.0}
    )
    assert decision.approved is False
    assert "KILL_SWITCH_ON" in decision.reason_codes
    assert "MODE_NOT_LIVE" in decision.reason_codes
    assert decision.max_allowed_quantity == 0


def test_balance_unavailable_blocks(settings):
    decision = RiskManager(settings).evaluate(
        signal=None, metrics=_good_metrics(), account_state=None
    )
    assert "BALANCE_UNAVAILABLE" in decision.reason_codes


def test_wide_spread_blocks(settings):
    ob = {"orderbook": {"yes": [[20, 300]], "no": [[20, 300]]}}  # spread 60
    metrics = compute_metrics(
        {"ticker": "T", "volume": 5000, "open_interest": 2000, "close_time": "2030-01-01T00:00:00Z"},
        ob,
    )
    settings.kill_switch = False
    settings.bot_mode = "live"
    decision = RiskManager(settings).evaluate(
        signal=None, metrics=metrics, account_state={"cash_balance": 100.0}
    )
    assert decision.approved is False
    assert "SPREAD_TOO_WIDE" in decision.reason_codes


def test_market_exposure_exceeded_blocks(settings):
    settings.kill_switch = False
    settings.bot_mode = "live"
    decision = RiskManager(settings).evaluate(
        signal=None,
        metrics=_good_metrics(),
        account_state={"cash_balance": 100.0},
        existing_exposure=settings.max_market_exposure + 1,
    )
    assert "MARKET_EXPOSURE_EXCEEDED" in decision.reason_codes


def test_for_paper_skips_live_gates(settings):
    # Default settings: kill_switch on, mode scanner, no account balance.
    decision = RiskManager(settings).evaluate(
        signal=None, metrics=_good_metrics(), account_state=None, for_paper=True
    )
    assert decision.approved is True
    assert decision.reason_codes == []


def test_for_paper_still_blocks_quality_failures(settings):
    ob = {"orderbook": {"yes": [[20, 300]], "no": [[20, 300]]}}  # spread 60
    metrics = compute_metrics(
        {"ticker": "T", "volume": 5000, "open_interest": 2000, "close_time": "2030-01-01T00:00:00Z"},
        ob,
    )
    decision = RiskManager(settings).evaluate(
        signal=None, metrics=metrics, account_state=None, for_paper=True
    )
    assert decision.approved is False
    assert "SPREAD_TOO_WIDE" in decision.reason_codes


def test_approves_when_all_clear(settings):
    settings.kill_switch = False
    settings.bot_mode = "live"
    decision = RiskManager(settings).evaluate(
        signal=None, metrics=_good_metrics(), account_state={"cash_balance": 100.0}
    )
    assert decision.approved is True
    assert decision.reason_codes == []
    assert decision.max_allowed_quantity == settings.max_order_size
    assert decision.max_allowed_price == 51  # 100 - best_no_bid(49)
