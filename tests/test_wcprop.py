from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot import repository as repo
from kalshi_bot.wcprop.tracker import WcPropTracker


def _settled_match(ticker, result, closed_min_ago):
    close = (datetime.now(timezone.utc) - timedelta(minutes=closed_min_ago)).isoformat()
    return {"ticker": ticker, "result": result, "close_time": close}


def _winner(ticker, yb_c, ya_c):
    return {
        "ticker": ticker,
        "yes_bid_dollars": f"{yb_c / 100:.4f}",
        "yes_ask_dollars": f"{ya_c / 100:.4f}",
    }


def _ob(yes_bid_c, yes_ask_c, depth=300):
    no_bid_c = 100 - yes_ask_c
    return {"orderbook_fp": {
        "yes_dollars": [[f"{yes_bid_c / 100:.4f}", str(depth)]],
        "no_dollars": [[f"{no_bid_c / 100:.4f}", str(depth)]],
    }}


class FakeKalshi:
    def __init__(self):
        self.settled: dict[str, list] = {}
        self.winners: list = []
        self.books: dict[str, dict] = {}

    def get_markets(self, *, status="open", series_ticker=None, limit=200, cursor=None,
                    min_close_ts=None, event_ticker=None):
        if status == "settled":
            return {"markets": self.settled.get(series_ticker, []), "cursor": ""}
        if series_ticker == "KXMENWORLDCUP":
            return {"markets": self.winners, "cursor": ""}
        return {"markets": [], "cursor": ""}

    def get_orderbook(self, ticker, depth=None):
        return self.books[ticker]


def _setup(settings):
    settings.wcprop_match_series = "KXWCGAME"
    settings.wcprop_winner_series = "KXMENWORLDCUP"
    db.init_engine(settings.database_url)
    db.create_all()


def _set_winner(client, ticker, yb, ya):
    client.winners = [_winner(ticker, yb, ya)]
    client.books[ticker] = _ob(yb, ya)


def test_wcprop_triggers_and_rides_rising_rung(settings):
    """A match settled 10 min ago triggers the window. The first cycle only seeds the mid; the
    second cycle, with the winner rung repriced UP >= min_move, opens a taker BUY YES."""
    _setup(settings)
    client = FakeKalshi()
    client.settled["KXWCGAME"] = [_settled_match("KXWCGAME-26JUL04PORCRO-POR", "yes", 10)]
    _set_winner(client, "KXMENWORLDCUP-26-POR", 40, 43)      # mid 41.5
    tracker = WcPropTracker(client, settings)

    with db.session_scope() as session:
        s1 = tracker.run_once(session)
    assert s1.triggered and s1.recent_settled == 1 and s1.opened == 0   # no prior mid yet

    _set_winner(client, "KXMENWORLDCUP-26-POR", 44, 47)      # mid 45.5 -> move +4
    with db.session_scope() as session:
        s2 = tracker.run_once(session)
    assert s2.moved == 1 and s2.opened == 1 and s2.per_side == {"yes": 1}
    with db.session_scope() as session:
        t = session.scalars(select(m.PaperTrade)).one()
        assert t.strategy == "wcprop" and t.side == "yes" and t.action == "buy"
        assert t.assumed_price == 47        # taker buy YES at the ask
        assert len(t.fill_assumption) <= 64 and t.fill_assumption.startswith("[wcprop]")


def test_wcprop_rides_falling_rung_as_no(settings):
    _setup(settings)
    client = FakeKalshi()
    client.settled["KXWCGAME"] = [_settled_match("KXWCGAME-26JUL04PORCRO-POR", "no", 8)]
    _set_winner(client, "KXMENWORLDCUP-26-CRO", 60, 63)      # mid 61.5
    tracker = WcPropTracker(client, settings)
    with db.session_scope() as session:
        tracker.run_once(session)
    _set_winner(client, "KXMENWORLDCUP-26-CRO", 56, 59)      # mid 57.5 -> move -4
    with db.session_scope() as session:
        s2 = tracker.run_once(session)
    assert s2.opened == 1 and s2.per_side == {"no": 1}
    with db.session_scope() as session:
        t = session.scalars(select(m.PaperTrade)).one()
        assert t.side == "no" and t.assumed_price == 44    # no-ask = 100 - yes_bid(56)


def test_wcprop_no_trigger_no_trade(settings):
    """No recent settled match -> the window is closed; even a big winner move does nothing."""
    _setup(settings)
    client = FakeKalshi()                                    # no settled matches
    _set_winner(client, "KXMENWORLDCUP-26-POR", 40, 43)
    tracker = WcPropTracker(client, settings)
    with db.session_scope() as session:
        s1 = tracker.run_once(session)
    _set_winner(client, "KXMENWORLDCUP-26-POR", 50, 53)      # move +10 but no trigger
    with db.session_scope() as session:
        s2 = tracker.run_once(session)
    assert not s1.triggered and not s2.triggered
    assert s2.opened == 0
    with db.session_scope() as session:
        assert session.scalar(select(m.PaperTrade)) is None


def test_wcprop_skips_wide_spread_and_small_move(settings):
    _setup(settings)
    client = FakeKalshi()
    client.settled["KXWCGAME"] = [_settled_match("KXWCGAME-26JUL04A-A", "yes", 10)]
    # rung 1 moves only +1c (< min_move 3); rung 2 moves +5c but has a 9c spread (> cap 5)
    client.winners = [_winner("KXMENWORLDCUP-26-A", 40, 42), _winner("KXMENWORLDCUP-26-B", 30, 33)]
    client.books = {"KXMENWORLDCUP-26-A": _ob(40, 42), "KXMENWORLDCUP-26-B": _ob(30, 33)}
    tracker = WcPropTracker(client, settings)
    with db.session_scope() as session:
        tracker.run_once(session)
    client.winners = [_winner("KXMENWORLDCUP-26-A", 41, 43), _winner("KXMENWORLDCUP-26-B", 34, 43)]
    client.books = {"KXMENWORLDCUP-26-A": _ob(41, 43), "KXMENWORLDCUP-26-B": _ob(34, 43)}
    with db.session_scope() as session:
        s2 = tracker.run_once(session)
    assert s2.opened == 0
    assert s2.skipped_spread == 1        # rung B moved enough but the quote is too wide


def test_wcprop_engine_times_out_but_not_hold_to_settlement(settings):
    """WCPROP is a deliberately TIMED book: the shared engine must close it at wcprop_hold_minutes
    (here shrunk), unlike the hold-to-settlement mmsell/theta/tfav books."""
    from kalshi_bot.paper.engine import PaperTradingEngine
    from kalshi_bot.risk.manager import RiskManager
    from kalshi_bot.scanner.metrics import compute_metrics

    _setup(settings)
    settings.wcprop_hold_minutes = 120.0
    with db.session_scope() as session:
        t = repo.create_paper_trade(
            session, signal_id=None, ticker="KXMENWORLDCUP-26-POR", strategy="wcprop",
            side="yes", action="buy", assumed_price=45, quantity=5,
            fill_assumption="x", entry_fee=0.05,
        )
        t.created_at = datetime.now(timezone.utc) - timedelta(hours=3)   # past the 2h horizon
        repo.open_paper_position_for_trade(
            session, ticker=t.market_ticker, strategy="wcprop", side="yes",
            quantity=5, avg_price=45,
        )
    engine = PaperTradingEngine(None, settings, RiskManager(settings))
    metrics = compute_metrics({"ticker": "KXMENWORLDCUP-26-POR"}, _ob(48, 51), top_n=10)
    with db.session_scope() as session:
        trade = session.scalars(select(m.PaperTrade)).one()
        engine._mark_or_exit(session, trade, metrics)
    with db.session_scope() as session:
        trade = session.scalars(select(m.PaperTrade)).one()
        assert trade.status == "closed_timeout"    # timed out at the horizon, closed at the bid
