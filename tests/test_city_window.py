"""The weather_cwin per-city entry-window book."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot.weather.tracker import WeatherTracker


def _bucket(ticker, sub, yb, ya):
    return {
        "ticker": ticker, "yes_sub_title": sub, "status": "active",
        "yes_bid_dollars": f"{yb / 100:.4f}", "yes_ask_dollars": f"{ya / 100:.4f}",
        "volume_fp": "1000.00", "open_interest_fp": "500.00",
    }


_BOOKS = {
    "KXHIGHLAX-26JUN12-B74.5": {"orderbook_fp": {"yes_dollars": [["0.5500", "300"]],
                                                 "no_dollars": [["0.4300", "300"]]}},
    "KXHIGHLAX-26JUN12-B76.5": {"orderbook_fp": {"yes_dollars": [["0.2000", "300"]],
                                                 "no_dollars": [["0.7800", "300"]]}},
}


class LaxHighClient:
    def get_events(self, series_ticker, status="open", with_nested_markets=True):
        if series_ticker == "KXHIGHLAX" and status == "open":
            close = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
            return {"events": [{
                "event_ticker": "KXHIGHLAX-26JUN12",
                "series_ticker": "KXHIGHLAX",
                "close_time": close,
                "markets": [
                    _bucket("KXHIGHLAX-26JUN12-B74.5", "74° to 75°", 55, 57),  # favorite
                    _bucket("KXHIGHLAX-26JUN12-B76.5", "76° to 77°", 20, 22),
                ],
            }]}
        return {"events": []}

    def get_orderbook(self, ticker, depth=None):
        return _BOOKS[ticker]


def _settings(settings, windows="LAX:18"):
    settings.bot_mode = "weather"
    settings.weather_strategies = "favorite"
    settings.weather_track_lows = False
    settings.weather_entry_hours = "4"
    settings.weather_polymarket_enabled = False
    settings.weather_city_window_enabled = True
    settings.weather_city_windows = windows
    return settings


def test_cwin_enters_favorite_at_city_window(settings):
    _settings(settings, windows="LAX:18")
    db.init_engine(settings.database_url)
    db.create_all()
    tracker = WeatherTracker(LaxHighClient(), settings, forecast=None)
    with db.session_scope() as session:
        tracker.run_once(session)
        trades = session.scalars(select(m.PaperTrade)).all()
        cwin = [t for t in trades if (t.strategy or "").startswith("weather_cwin")]
        assert len(cwin) == 1
        assert cwin[0].strategy == "weather_cwin_h18"
        assert cwin[0].market_ticker == "KXHIGHLAX-26JUN12-B74.5"  # the favorite


def test_cwin_skips_unmapped_city(settings):
    _settings(settings, windows="CHI:18")  # LAX not in the map
    db.init_engine(settings.database_url)
    db.create_all()
    tracker = WeatherTracker(LaxHighClient(), settings, forecast=None)
    with db.session_scope() as session:
        tracker.run_once(session)
        trades = session.scalars(select(m.PaperTrade)).all()
        assert not any((t.strategy or "").startswith("weather_cwin") for t in trades)


def test_cwin_disabled(settings):
    _settings(settings, windows="LAX:18")
    settings.weather_city_window_enabled = False
    db.init_engine(settings.database_url)
    db.create_all()
    tracker = WeatherTracker(LaxHighClient(), settings, forecast=None)
    with db.session_scope() as session:
        tracker.run_once(session)
        trades = session.scalars(select(m.PaperTrade)).all()
        assert not any((t.strategy or "").startswith("weather_cwin") for t in trades)
