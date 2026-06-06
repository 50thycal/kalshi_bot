"""Paper trading engine tests."""

from __future__ import annotations

from sqlalchemy import func, select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot.paper.engine import PaperTradingEngine, choose_entry, kalshi_fee
from kalshi_bot.risk.manager import RiskManager
from kalshi_bot.scanner.metrics import compute_metrics
from kalshi_bot.scanner.scanner import MarketScanner

# -- unit -------------------------------------------------------------------

def test_kalshi_fee_rounds_up_to_cent():
    # 0.07 * 1 * 0.61 * 0.39 = 0.016653 -> ceil to $0.02
    assert kalshi_fee(61, 1) == 0.02
    assert kalshi_fee(50, 10) == 0.18  # 0.07*10*0.25 = 0.175 -> 0.18
    assert kalshi_fee(61, 0) == 0.0
    assert kalshi_fee(61, 1, enabled=False) == 0.0


def test_choose_entry_buys_favorite(settings):
    settings.paper_strategy = "buy_favorite"
    # midpoint 60.5 -> favorite YES, entered at YES ask (61)
    ob = {"orderbook": {"yes": [[60, 300]], "no": [[39, 300]]}}
    metrics = compute_metrics({"ticker": "T", "close_time": "2030-01-01T00:00:00Z"}, ob)
    assert choose_entry(metrics, settings) == ("yes", "buy", 61)

    # midpoint 9.5 -> favorite NO, entered at NO ask (100 - best_yes_bid 9 = 91)
    ob2 = {"orderbook": {"yes": [[9, 300]], "no": [[90, 300]]}}
    metrics2 = compute_metrics({"ticker": "T", "close_time": "2030-01-01T00:00:00Z"}, ob2)
    side, action, price = choose_entry(metrics2, settings)
    assert side == "no" and action == "buy" and price == 91


# -- end-to-end -------------------------------------------------------------

def _liquid_event():
    return {
        "event_ticker": "KXFED",
        "category": "Economics",
        "title": "Fed rate decision",
        "close_time": "2030-01-01T00:00:00Z",
        "markets": [
            {
                "ticker": "KXFED-1",
                "title": "Fed cut?",
                "status": "active",
                "volume": 5000,
                "open_interest": 2000,
                "close_time": "2030-01-01T00:00:00Z",
                "rules_primary": "Resolves yes if ...",
            }
        ],
    }


# YES bid 60 / NO bid 39 -> YES ask 61, spread 1, midpoint 60.5 -> favorite YES @ 61.
_BOOK = {"KXFED-1": {"orderbook": {"yes": [[60, 300]], "no": [[39, 300]]}}}


class FakePaperClient:
    def __init__(self, market_state):
        self._market_state = market_state

    def iter_events(self, **_kw):
        yield _liquid_event()

    def get_orderbook(self, ticker, depth=None):
        return _BOOK[ticker]

    def get_market(self, ticker):
        return {"market": self._market_state[ticker]}


def _open_one_paper_trade(settings):
    settings.bot_mode = "paper"
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakePaperClient({"KXFED-1": {"status": "active"}})
    risk = RiskManager(settings)
    engine = PaperTradingEngine(client, settings, risk)
    scanner = MarketScanner(client, settings, risk)
    with db.session_scope() as session:
        engine.manage_open_positions(session)  # nothing open yet
        scanner.run_once(session, paper_engine=engine)
    return client, risk


def test_paper_open_then_settle_yes(settings):
    client, risk = _open_one_paper_trade(settings)

    with db.session_scope() as session:
        trade = session.scalar(select(m.PaperTrade))
        assert trade.status == "open"
        assert trade.side == "yes"
        assert trade.assumed_price == 61
        assert trade.quantity == 1
        assert float(trade.fees) == 0.02
        pos = session.scalar(select(m.PaperPosition))
        assert pos.status == "open" and pos.side == "yes"

    # Next cycle: market settles YES.
    client._market_state["KXFED-1"] = {"status": "settled", "result": "yes"}
    engine2 = PaperTradingEngine(client, settings, risk)
    with db.session_scope() as session:
        engine2.manage_open_positions(session)

    assert engine2.summary.closed_settled == 1
    with db.session_scope() as session:
        trade = session.scalar(select(m.PaperTrade))
        assert trade.status == "settled"
        assert trade.resolved_value == 100
        # gross = 1*(100-61)/100 = 0.39 ; minus entry fee 0.02 = 0.37
        assert abs(float(trade.pnl) - 0.37) < 1e-9
        assert session.scalar(
            select(func.count()).select_from(m.PaperPosition).where(m.PaperPosition.status == "open")
        ) == 0


def test_paper_timeout_exit_sells_at_bid(settings):
    settings.paper_max_hold_hours = 0  # force immediate timeout on the next manage pass
    client, risk = _open_one_paper_trade(settings)

    # Market still active; manage should exit at the held side's bid (YES bid 60).
    engine2 = PaperTradingEngine(client, settings, risk)
    with db.session_scope() as session:
        engine2.manage_open_positions(session)

    assert engine2.summary.closed_timeout == 1
    with db.session_scope() as session:
        trade = session.scalar(select(m.PaperTrade))
        assert trade.status == "closed_timeout"
        assert trade.exit_price == 60
        # gross = 1*(60-61)/100 = -0.01 ; minus entry fee 0.02 ; minus exit fee 0.02 = -0.05
        assert abs(float(trade.pnl) - (-0.05)) < 1e-9
