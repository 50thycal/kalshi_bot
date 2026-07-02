from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot.mmsell.tracker import MmSellTracker
from kalshi_bot.paper.engine import PaperTradingEngine
from kalshi_bot.risk.manager import RiskManager


def _mkt(ticker, sub, yes_bid_c, yes_ask_c, vol=500, hours=48):
    close = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    return {
        "ticker": ticker,
        "yes_sub_title": sub,
        "close_time": close,
        "volume_fp": f"{vol}.0",
        "yes_bid_dollars": f"{yes_bid_c / 100:.4f}",
        "yes_ask_dollars": f"{yes_ask_c / 100:.4f}",
    }


def _event(markets):
    return {"event_ticker": "KXTEAM-26", "series_ticker": "KXTEAM", "markets": markets}


def _ob(yes_bid_c, yes_ask_c):
    # book: yes bids at yes_bid; no bids at (100 - yes_ask) -> derives yes_ask
    no_bid_c = 100 - yes_ask_c
    return {"orderbook_fp": {
        "yes_dollars": [[f"{yes_bid_c / 100:.4f}", "300"]],
        "no_dollars": [[f"{no_bid_c / 100:.4f}", "300"]],
    }}


class FakeClient:
    def __init__(self, events, books, market_state=None):
        self._events = events
        self._books = books
        self._state = market_state or {}

    def get_exchange_status(self):
        return {"exchange_active": True, "trading_active": True}

    def get_events(self, status="open", with_nested_markets=True, limit=200, cursor=None):
        return {"events": self._events, "cursor": ""}

    def get_orderbook(self, ticker, depth=None):
        return self._books[ticker]

    def get_market(self, ticker):
        return {"market": self._state[ticker]}


def _setup(settings):
    settings.bot_mode = "mmsell"
    db.init_engine(settings.database_url)
    db.create_all()


def test_enters_buy_no_at_no_bid_for_cheap_yes(settings):
    _setup(settings)
    # a cheap longshot: yes 18/20 (mid 19, in 5-40 band) -> sell yes == buy no at 100-20 = 80
    ev = _event([_mkt("KXTEAM-26-A", "Team A wins", 18, 20)])
    client = FakeClient([ev], {"KXTEAM-26-A": _ob(18, 20)})
    tracker = MmSellTracker(client, settings)

    with db.session_scope() as session:
        summ = tracker.run_once(session)
    assert summ.opened == 1

    with db.session_scope() as session:
        trades = session.scalars(select(m.PaperTrade)).all()
        assert len(trades) == 1
        t = trades[0]
        assert t.strategy == "mmsell" and t.side == "no" and t.action == "buy"
        assert t.assumed_price == 80          # maker buys NO at the no-bid (= 100 - yes_ask)


def test_skips_out_of_band_and_dedups(settings):
    _setup(settings)
    ev = _event([
        _mkt("KXTEAM-26-A", "A", 18, 20),        # mid 19 -> in band
        _mkt("KXTEAM-26-B", "B", 55, 57),        # mid 56 -> out of band (favorite)
        _mkt("KXTEAM-26-C", "C", 1, 2),          # mid 1.5 -> below lo (penny) -> skip
    ])
    books = {"KXTEAM-26-A": _ob(18, 20), "KXTEAM-26-B": _ob(55, 57), "KXTEAM-26-C": _ob(1, 2)}
    client = FakeClient([ev], books)

    with db.session_scope() as session:
        summ = MmSellTracker(client, settings).run_once(session)
    assert summ.opened == 1                       # only the in-band cheap one

    # second pass -> dedup, nothing new
    with db.session_scope() as session:
        s2 = MmSellTracker(client, settings).run_once(session)
        assert s2.opened == 0
        assert session.scalar(select(func.count()).select_from(m.PaperTrade)) == 1


def test_hold_to_settlement_no_timeout_then_settle_pnl(settings):
    _setup(settings)
    settings.paper_max_hold_hours = 0.0           # would force-close a non-holding book instantly
    ev = _event([_mkt("KXTEAM-26-A", "A", 18, 20)])
    client = FakeClient([ev], {"KXTEAM-26-A": _ob(18, 20)})
    engine = PaperTradingEngine(client, settings, RiskManager(settings))

    with db.session_scope() as session:
        MmSellTracker(client, settings).run_once(session)

    # market still open -> manage must NOT timeout-close the mmsell position (holds to settlement)
    client._state["KXTEAM-26-A"] = {"status": "active", "result": ""}
    with db.session_scope() as session:
        engine.manage_open_positions(session)
        assert session.scalar(
            select(func.count()).select_from(m.PaperTrade).where(m.PaperTrade.status == "open")
        ) == 1

    # now it settles NO (the longshot missed) -> buy-no at 80 wins +0.20 gross, minus entry fee
    client._state["KXTEAM-26-A"] = {"status": "settled", "result": "no"}
    with db.session_scope() as session:
        engine.manage_open_positions(session)
        t = session.scalars(select(m.PaperTrade)).one()
        assert t.status == "settled"
        assert t.pnl > 0.15                        # +0.20 gross less a small entry fee


def test_settles_no_loss_when_longshot_hits(settings):
    _setup(settings)
    ev = _event([_mkt("KXTEAM-26-A", "A", 18, 20)])
    client = FakeClient([ev], {"KXTEAM-26-A": _ob(18, 20)})
    engine = PaperTradingEngine(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        MmSellTracker(client, settings).run_once(session)
    client._state["KXTEAM-26-A"] = {"status": "settled", "result": "yes"}   # longshot HIT
    with db.session_scope() as session:
        engine.manage_open_positions(session)
        t = session.scalars(select(m.PaperTrade)).one()
        assert t.status == "settled"
        assert t.pnl < -0.75                       # buy-no at 80 loses the 80c


def test_skips_configured_series(settings):
    _setup(settings)
    settings.mmsell_skip_series = "KXTEAM"         # skip our own series
    ev = _event([_mkt("KXTEAM-26-A", "A", 18, 20)])
    client = FakeClient([ev], {"KXTEAM-26-A": _ob(18, 20)})
    with db.session_scope() as session:
        summ = MmSellTracker(client, settings).run_once(session)
    assert summ.opened == 0 and summ.events_seen == 0
