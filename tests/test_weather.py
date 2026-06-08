"""Weather tracker + NWS forecast tests."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import respx
from httpx import Response
from sqlalchemy import func, select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot import repository as repo
from kalshi_bot.paper.engine import PaperTradingEngine
from kalshi_bot.risk.manager import RiskManager
from kalshi_bot.weather.forecast import NwsForecastClient
from kalshi_bot.weather.tracker import WeatherTracker


def _bucket(ticker, sub, yb, ya):
    return {
        "ticker": ticker,
        "yes_sub_title": sub,
        "status": "active",
        "yes_bid_dollars": f"{yb / 100:.4f}",
        "yes_ask_dollars": f"{ya / 100:.4f}",
        "volume_fp": "1000.00",
        "open_interest_fp": "500.00",
    }


def _nyc_event(hours_ahead):
    close = (datetime.now(timezone.utc) + timedelta(hours=hours_ahead)).isoformat()
    return {
        "event_ticker": "KXHIGHNY-26JUN08",
        "series_ticker": "KXHIGHNY",
        "category": "Climate and Weather",
        "title": "Highest temperature in New York City today?",
        "close_time": close,
        "markets": [
            _bucket("KXHIGHNY-26JUN08-B72.5", "72° to 73°", 20, 22),
            _bucket("KXHIGHNY-26JUN08-B74.5", "74° to 75°", 55, 57),  # favorite (mid 56)
            _bucket("KXHIGHNY-26JUN08-B76.5", "76° to 77°", 30, 32),
        ],
    }


_FAV = "KXHIGHNY-26JUN08-B74.5"
_BOOK = {_FAV: {"orderbook_fp": {"yes_dollars": [["0.5500", "300.00"]], "no_dollars": [["0.4300", "300.00"]]}}}


class FakeWeatherClient:
    def __init__(self, event, market_state=None):
        self.event = event
        self.market_state = market_state or {}

    def get_events(self, series_ticker, status="open", with_nested_markets=True):
        if series_ticker == "KXHIGHNY":
            return {"events": [self.event]}
        return {"events": []}

    def get_orderbook(self, ticker, depth=None):
        return _BOOK[ticker]

    def get_market(self, ticker):
        return {"market": self.market_state[ticker]}


def test_favorite_entered_per_window_and_dedup(settings):
    settings.bot_mode = "weather"
    db.init_engine(settings.database_url)
    db.create_all()

    client = FakeWeatherClient(_nyc_event(hours_ahead=3))  # 3h to close -> all of 12/8/4 fire
    tracker = WeatherTracker(client, settings, forecast=None)

    with db.session_scope() as session:
        summary = tracker.run_once(session)
    assert summary.opened == 3

    with db.session_scope() as session:
        trades = session.scalars(select(m.PaperTrade)).all()
        assert len(trades) == 3
        assert sorted(t.strategy for t in trades) == [
            "weather_fav_h12", "weather_fav_h4", "weather_fav_h8",
        ]
        assert all(t.market_ticker == _FAV and t.side == "yes" and t.assumed_price == 57 for t in trades)

    # Second pass: same event -> dedup, nothing new.
    tracker2 = WeatherTracker(client, settings, forecast=None)
    with db.session_scope() as session:
        s2 = tracker2.run_once(session)
        assert s2.opened == 0
        assert session.scalar(select(func.count()).select_from(m.PaperTrade)) == 3


def test_weather_holds_to_settlement_then_settles(settings):
    settings.bot_mode = "weather"
    settings.paper_max_hold_hours = 0  # would time out a normal trade immediately
    db.init_engine(settings.database_url)
    db.create_all()

    with db.session_scope() as session:
        repo.create_paper_trade(
            session, signal_id=None, ticker=_FAV, strategy="weather_fav_h4", side="yes",
            action="buy", assumed_price=57, quantity=1, fill_assumption="x", entry_fee=0.02,
        )
        repo.open_paper_position_for_trade(
            session, ticker=_FAV, strategy="weather_fav_h4", side="yes", quantity=1, avg_price=57
        )

    engine = PaperTradingEngine(
        FakeWeatherClient(_nyc_event(3), {_FAV: {"status": "active"}}), settings, RiskManager(settings)
    )
    with db.session_scope() as session:
        engine.manage_open_positions(session)
    # Despite max_hold_hours=0, the weather trade is held (marked, not timed out).
    assert engine.summary.closed_timeout == 0
    assert engine.summary.marked == 1

    # Now it settles YES.
    engine2 = PaperTradingEngine(
        FakeWeatherClient(_nyc_event(3), {_FAV: {"status": "settled", "result": "yes"}}),
        settings, RiskManager(settings),
    )
    with db.session_scope() as session:
        engine2.manage_open_positions(session)
        assert engine2.summary.closed_settled == 1
        trade = session.scalar(select(m.PaperTrade))
        assert trade.status == "settled" and trade.resolved_value == 100


def test_abandon_foreign_keeps_weather(settings):
    db.init_engine(settings.database_url)
    db.create_all()
    with db.session_scope() as session:
        for strat in ("momentum", "weather_fav_h8"):
            repo.create_paper_trade(
                session, signal_id=None, ticker="T", strategy=strat, side="yes", action="buy",
                assumed_price=50, quantity=1, fill_assumption="x", entry_fee=0.0,
            )
            repo.open_paper_position_for_trade(
                session, ticker="T", strategy=strat, side="yes", quantity=1, avg_price=50
            )
        n = repo.abandon_open_paper_trades(session, keep_prefixes=("weather",))
        assert n == 1
        statuses = {t.strategy: t.status for t in session.scalars(select(m.PaperTrade)).all()}
        assert statuses == {"momentum": "abandoned", "weather_fav_h8": "open"}


@respx.mock
def test_nws_daily_high_parse():
    respx.get("https://api.weather.gov/points/40.779,-73.9693").mock(
        return_value=Response(200, json={"properties": {"forecastGridData": "https://api.weather.gov/gridpoints/OKX/33,35"}})
    )
    respx.get("https://api.weather.gov/gridpoints/OKX/33,35").mock(
        return_value=Response(200, json={"properties": {"maxTemperature": {"uom": "wmoUnit:degC", "values": [
            {"validTime": "2026-06-08T06:00:00+00:00/PT13H", "value": 25.0},
        ]}}})
    )
    with NwsForecastClient("test-agent") as nws:
        high = nws.daily_high_f(40.779, -73.9693, date(2026, 6, 8))
    assert high == 77.0  # 25C -> 77F
