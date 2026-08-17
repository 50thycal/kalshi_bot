from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot.pin15.tracker import Pin15Tracker


def _now_minute() -> int:
    return int(time.time()) // 60 * 60


def _closes(price: float, n_minutes: int = 20, end: int | None = None) -> dict[int, float]:
    """Canned spot candles ending NOW — evaluated when the test runs, not at import.

    Pinning `end` to import time made these a time bomb: the tracker asks for a
    window relative to the current clock, so once the suite took longer than the
    20 minutes of candles here, pin15 saw no spot at all (`skipped_no_spot`) and
    four tests failed purely because they ran late. That is a function of suite
    duration, not of anything under test — CI hit it at ~10 minutes in."""
    end = _now_minute() if end is None else end
    return {end - i * 60: price for i in range(0, n_minutes)}


class FakeSpotClient:
    def __init__(self, closes: dict[int, float]):
        self._closes = dict(closes)

    def candles(self, product: str, start: int, end: int) -> dict[int, float]:
        return {ts: c for ts, c in self._closes.items() if start <= ts < end}


def _mkt(ticker, floor, seconds=150, vol=5000):
    close = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
    return {
        "ticker": ticker,
        "close_time": close,
        "volume_fp": f"{vol}.0",
        "strike_type": "greater_or_equal",
        "floor_strike": floor,
    }


def _ob(yes_bid_c, yes_ask_c, depth=300):
    no_bid_c = 100 - yes_ask_c
    return {"orderbook_fp": {
        "yes_dollars": [[f"{yes_bid_c / 100:.4f}", str(depth)]],
        "no_dollars": [[f"{no_bid_c / 100:.4f}", str(depth)]],
    }}


class FakeKalshi:
    def __init__(self, markets_by_series, books):
        self._markets = markets_by_series
        self._books = books

    def get_markets(self, *, status="open", series_ticker=None, limit=200, cursor=None,
                    event_ticker=None, min_close_ts=None):
        return {"markets": self._markets.get(series_ticker, []), "cursor": ""}

    def get_orderbook(self, ticker, depth=None):
        return self._books[ticker]


def _setup(settings):
    db.init_engine(settings.database_url)
    db.create_all()


def _tracker(settings, markets_by_series, books, spot_price=64100.0, closes=None):
    settings.pin15_series = "KXBTC15M:BTC-USD"
    client = FakeKalshi(markets_by_series, books)
    spot = FakeSpotClient(closes if closes is not None else _closes(spot_price))
    return Pin15Tracker(client, settings, spot_client=spot)


def test_pin15_buys_up_favorite_on_positive_displacement(settings):
    """Spot 64100 vs target 64000 = +15.6bp (>= 5) -> Up pinned -> TAKER buy YES at the ask."""
    _setup(settings)
    mkts = [_mkt("KXBTC15M-26JUL110945-45", 64000.0)]
    books = {"KXBTC15M-26JUL110945-45": _ob(92, 94)}     # yes-ask 94
    tracker = _tracker(settings, {"KXBTC15M": mkts}, books, spot_price=64100.0)
    with db.session_scope() as session:
        summ = tracker.run_once(session)
    assert summ.products_ok == 1 and summ.in_window == 1 and summ.pinned == 1 and summ.opened == 1
    with db.session_scope() as session:
        t = session.scalars(select(m.PaperTrade)).one()
        assert t.strategy == "pin15" and t.side == "yes" and t.action == "buy"
        assert t.assumed_price == 94                     # taker buy YES at the ask
        assert t.quantity == settings.pin15_order_size
        assert t.fill_assumption.startswith("[pin15] buy yes")


def test_pin15_buys_down_favorite_on_negative_displacement(settings):
    """Spot 63900 vs target 64000 = -15.6bp -> Down pinned -> TAKER buy NO at the no-ask
    (= 100 - yes_bid = 100 - 6 = 94)."""
    _setup(settings)
    mkts = [_mkt("KXBTC15M-26JUL110945-45", 64000.0)]
    books = {"KXBTC15M-26JUL110945-45": _ob(6, 8)}       # yes-bid 6 -> no-ask 94
    tracker = _tracker(settings, {"KXBTC15M": mkts}, books, spot_price=63900.0)
    with db.session_scope() as session:
        summ = tracker.run_once(session)
    assert summ.pinned == 1 and summ.opened == 1
    with db.session_scope() as session:
        t = session.scalars(select(m.PaperTrade)).one()
        assert t.side == "no" and t.action == "buy" and t.assumed_price == 94
        assert t.fill_assumption.startswith("[pin15] buy no")


def test_pin15_skips_below_disp_threshold(settings):
    """Spot 64010 vs target 64000 = +1.6bp (< 5bp) -> not pinned -> no trade."""
    _setup(settings)
    mkts = [_mkt("KXBTC15M-26JUL110945-45", 64000.0)]
    books = {"KXBTC15M-26JUL110945-45": _ob(50, 52)}
    tracker = _tracker(settings, {"KXBTC15M": mkts}, books, spot_price=64010.0)
    with db.session_scope() as session:
        summ = tracker.run_once(session)
    assert summ.in_window == 1 and summ.priced == 1 and summ.pinned == 0 and summ.opened == 0


def test_pin15_entry_window_gate(settings):
    """A window 300s out (> pin15_entry_max_seconds 210) is skipped; one at 150s is in-window."""
    _setup(settings)
    early = _mkt("KXBTC15M-26JUL111000-00", 64000.0, seconds=300)
    good = _mkt("KXBTC15M-26JUL110945-45", 64000.0, seconds=150)
    books = {early["ticker"]: _ob(92, 94), good["ticker"]: _ob(92, 94)}
    tracker = _tracker(settings, {"KXBTC15M": [early, good]}, books, spot_price=64100.0)
    with db.session_scope() as session:
        summ = tracker.run_once(session)
    assert summ.in_window == 1 and summ.opened == 1
    with db.session_scope() as session:
        t = session.scalars(select(m.PaperTrade)).one()
        assert t.market_ticker == "KXBTC15M-26JUL110945-45"


def test_pin15_no_spot_no_trade(settings):
    _setup(settings)
    mkts = [_mkt("KXBTC15M-26JUL110945-45", 64000.0)]
    books = {"KXBTC15M-26JUL110945-45": _ob(92, 94)}
    tracker = _tracker(settings, {"KXBTC15M": mkts}, books, closes={})
    with db.session_scope() as session:
        summ = tracker.run_once(session)
    assert summ.products_ok == 0 and summ.opened == 0 and summ.skipped_no_spot == 1


def test_pin15_dedup_and_event_cap(settings):
    """One entry per window (max_per_event 1): a re-run re-sees the open ticker, no double-entry."""
    _setup(settings)
    mkts = [_mkt("KXBTC15M-26JUL110945-45", 64000.0)]
    books = {"KXBTC15M-26JUL110945-45": _ob(92, 94)}
    tracker = _tracker(settings, {"KXBTC15M": mkts}, books, spot_price=64100.0)
    with db.session_scope() as session:
        assert tracker.run_once(session).opened == 1
    with db.session_scope() as session:
        summ2 = tracker.run_once(session)
    assert summ2.opened == 0 and summ2.already_open == 1
