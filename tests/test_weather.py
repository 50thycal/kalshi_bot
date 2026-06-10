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
from kalshi_bot.weather.buckets import forecast_in_bucket, parse_bucket_range
from kalshi_bot.weather.forecast import NwsForecastClient
from kalshi_bot.weather.tracker import WeatherTracker


def test_parse_bucket_range_and_forecast_in_bucket():
    assert parse_bucket_range("74° to 75°") == (74.0, 75.0)
    assert parse_bucket_range("83° or below") == (None, 83.0)
    assert parse_bucket_range("95° or above") == (95.0, None)
    assert forecast_in_bucket(77, 76.0, 77.0) is True
    assert forecast_in_bucket(77, 74.0, 75.0) is False
    assert forecast_in_bucket(93, None, 83.0) is False  # NWS 93 not <= 83
    assert forecast_in_bucket(80, 95.0, None) is False


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
    settings.weather_entry_hours = "12,8,4"
    settings.weather_strategies = "favorite"
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


class FakeForecast:
    def __init__(self, high):
        self.high = high

    def daily_high_f(self, lat, lon, target):
        return self.high


def test_weather_nws_book_buys_forecast_bucket(settings):
    settings.bot_mode = "weather"
    settings.weather_strategies = "favorite,nws"
    db.init_engine(settings.database_url)
    db.create_all()

    book = {
        "KXHIGHNY-26JUN08-B74.5": {"orderbook_fp": {"yes_dollars": [["0.5500", "300"]], "no_dollars": [["0.4300", "300"]]}},
        "KXHIGHNY-26JUN08-B76.5": {"orderbook_fp": {"yes_dollars": [["0.2000", "300"]], "no_dollars": [["0.7800", "300"]]}},
    }

    class Client(FakeWeatherClient):
        def get_orderbook(self, ticker, depth=None):
            return book[ticker]

    client = Client(_nyc_event(hours_ahead=3))  # favorite = 74-75
    tracker = WeatherTracker(client, settings, FakeForecast(77.0))  # NWS 77 -> bucket 76-77
    with db.session_scope() as session:
        summary = tracker.run_once(session)

    assert summary.opened == 6  # 3 favorite windows + 3 nws windows
    with db.session_scope() as session:
        trades = session.scalars(select(m.PaperTrade)).all()
        fav = [t for t in trades if (t.strategy or "").startswith("weather_fav")]
        nws = [t for t in trades if (t.strategy or "").startswith("weather_nws")]
        assert len(fav) == 3 and len(nws) == 3
        assert all(t.market_ticker == "KXHIGHNY-26JUN08-B74.5" for t in fav)  # market favorite
        assert all(t.market_ticker == "KXHIGHNY-26JUN08-B76.5" for t in nws)  # NWS bucket
        assert all(t.assumed_price == 22 for t in nws)  # bought the forecast bucket at its ask


def _seed_nyc_settlement(session, event_ticker, fc_high, sub, low, high):
    repo.insert_weather_settlement(
        session, event_ticker=event_ticker, city="NYC", series_ticker="KXHIGHNY",
        target_date=None, winning_ticker=None, winning_subtitle=sub,
        actual_low_f=low, actual_high_f=high,
    )
    repo.insert_weather_forecast(
        session, city="NYC", series_ticker="KXHIGHNY", event_ticker=event_ticker,
        target_date=None, station="KNYC", forecast_high_f=fc_high, source="nws",
    )


def test_weather_city_bias_shrinks_toward_zero(settings):
    db.init_engine(settings.database_url)
    db.create_all()
    with db.session_scope() as session:
        # Two events: forecast 81 but settled 78-79 (mid 78.5) -> raw bias -2.5.
        for ev in ("KXHIGHNY-26JUN05", "KXHIGHNY-26JUN06"):
            _seed_nyc_settlement(session, ev, 81.0, "78° to 79°", 78.0, 79.0)
        bias = repo.weather_city_bias(session, shrinkage=3.0, min_events=1)
        # mean(actual-fc) = -2.5; shrunk *2/(2+3)=0.4 -> -1.0; keyed by (city, kind)
        assert bias[("NYC", "high")] == -1.0
        # min_events gate: requiring 3 events drops NYC (only 2).
        assert repo.weather_city_bias(session, shrinkage=3.0, min_events=3) == {}


def test_weather_cal_book_buys_bias_corrected_bucket(settings):
    settings.bot_mode = "weather"
    settings.weather_strategies = "cal"
    settings.weather_bias_shrinkage = 0.0  # full correction -> deterministic
    settings.weather_bias_min_events = 1
    db.init_engine(settings.database_url)
    db.create_all()

    # Prior settled NYC event: forecast 81 but actually settled 76-77 -> station runs
    # ~4.5 degF cooler than the raw forecast.
    with db.session_scope() as session:
        _seed_nyc_settlement(session, "KXHIGHNY-26JUN07", 81.0, "76° to 77°", 76.0, 77.0)

    book = {
        "KXHIGHNY-26JUN08-B74.5": {"orderbook_fp": {"yes_dollars": [["0.5500", "300"]], "no_dollars": [["0.4300", "300"]]}},
        "KXHIGHNY-26JUN08-B76.5": {"orderbook_fp": {"yes_dollars": [["0.2000", "300"]], "no_dollars": [["0.7800", "300"]]}},
    }

    class Client(FakeWeatherClient):
        def get_orderbook(self, ticker, depth=None):
            return book[ticker]

    client = Client(_nyc_event(hours_ahead=3))  # favorite = 74-75
    # Raw forecast 81 -> corrected 81 + (-4.5) = 76.5 -> bucket 76-77 (not the favorite).
    tracker = WeatherTracker(client, settings, FakeForecast(81.0))
    with db.session_scope() as session:
        tracker.run_once(session)

    with db.session_scope() as session:
        cal = [t for t in session.scalars(select(m.PaperTrade)).all()
               if (t.strategy or "").startswith("weather_cal")]
        assert len(cal) == 3  # one per entry window
        assert all(t.market_ticker == "KXHIGHNY-26JUN08-B76.5" for t in cal)


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


def test_capture_settlements_records_winning_bucket(settings):
    settings.bot_mode = "weather"
    db.init_engine(settings.database_url)
    db.create_all()

    settled_event = {
        "event_ticker": "KXHIGHNY-26JUN07",
        "series_ticker": "KXHIGHNY",
        "close_time": "2026-06-08T05:00:00Z",
        "markets": [
            {"ticker": "KXHIGHNY-26JUN07-B72.5", "yes_sub_title": "72° to 73°", "result": "no"},
            {"ticker": "KXHIGHNY-26JUN07-B76.5", "yes_sub_title": "76° to 77°", "result": "yes"},
        ],
    }

    class SettledClient:
        def get_events(self, series_ticker, status="open", with_nested_markets=True):
            if series_ticker == "KXHIGHNY" and status == "settled":
                return {"events": [settled_event]}
            return {"events": []}

    tracker = WeatherTracker(SettledClient(), settings, forecast=None)
    with db.session_scope() as session:
        summary = tracker.run_once(session)
    assert summary.settlements_captured == 1
    with db.session_scope() as session:
        st = session.scalar(select(m.WeatherSettlement))
        assert st.event_ticker == "KXHIGHNY-26JUN07"
        assert st.winning_subtitle == "76° to 77°"
        assert (st.actual_low_f, st.actual_high_f) == (76.0, 77.0)

    # Idempotent: a second pass doesn't duplicate.
    with db.session_scope() as session:
        s2 = tracker.run_once(session)
        assert s2.settlements_captured == 0
        assert session.scalar(select(func.count()).select_from(m.WeatherSettlement)) == 1


@respx.mock
def test_nws_daily_high_parse():
    respx.get("https://api.weather.gov/points/40.779,-73.9693").mock(
        return_value=Response(200, json={"properties": {"forecast": "https://api.weather.gov/gridpoints/OKX/33,35/forecast"}})
    )
    respx.get("https://api.weather.gov/gridpoints/OKX/33,35/forecast").mock(
        return_value=Response(200, json={"properties": {"periods": [
            {"name": "Tonight", "isDaytime": False, "startTime": "2026-06-07T18:00:00-04:00", "temperature": 60, "temperatureUnit": "F"},
            {"name": "Today", "isDaytime": True, "startTime": "2026-06-08T06:00:00-04:00", "temperature": 77, "temperatureUnit": "F"},
            {"name": "Monday", "isDaytime": True, "startTime": "2026-06-09T06:00:00-04:00", "temperature": 81, "temperatureUnit": "F"},
        ]}})
    )
    with NwsForecastClient("test-agent") as nws:
        assert nws.daily_high_f(40.779, -73.9693, date(2026, 6, 8)) == 77.0
        assert nws.daily_high_f(40.779, -73.9693, date(2026, 6, 9)) == 81.0  # picks the right day
