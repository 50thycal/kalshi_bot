"""The weather_favband per-city favorite price-band book (e.g. LAX 50-70c)."""

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
    """LAX high event; favorite B74.5 has mid (55+57)/2 = 56c -> inside a 50-70 band."""

    def get_events(self, series_ticker, status="open", with_nested_markets=True):
        if series_ticker == "KXHIGHLAX" and status == "open":
            close = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
            return {"events": [{
                "event_ticker": "KXHIGHLAX-26JUN12",
                "series_ticker": "KXHIGHLAX",
                "close_time": close,
                "markets": [
                    _bucket("KXHIGHLAX-26JUN12-B74.5", "74° to 75°", 55, 57),  # favorite, mid 56c
                    _bucket("KXHIGHLAX-26JUN12-B76.5", "76° to 77°", 20, 22),
                ],
            }]}
        return {"events": []}

    def get_orderbook(self, ticker, depth=None):
        return _BOOKS[ticker]


def _settings(settings, bands="LAX:50-70", enabled=True):
    settings.bot_mode = "weather"
    settings.weather_strategies = "favorite"
    settings.weather_track_lows = False
    settings.weather_entry_hours = "4"
    settings.weather_polymarket_enabled = False
    settings.weather_city_window_enabled = False  # isolate the favband book
    settings.weather_obs_entry_enabled = False
    settings.weather_favband_enabled = enabled
    settings.weather_favband_bands = bands
    return settings


def _run(settings):
    db.init_engine(settings.database_url)
    db.create_all()
    tracker = WeatherTracker(LaxHighClient(), settings, forecast=None)
    with db.session_scope() as session:
        tracker.run_once(session)
        return session.scalars(select(m.PaperTrade)).all()


def _favband(trades):
    return [t for t in trades if (t.strategy or "").startswith("weather_favband")]


def test_favband_enters_when_favorite_in_band(settings):
    trades = _run(_settings(settings, bands="LAX:50-70"))
    fb = _favband(trades)
    assert len(fb) == 1
    assert fb[0].strategy == "weather_favband_h4"
    assert fb[0].market_ticker == "KXHIGHLAX-26JUN12-B74.5"  # the favorite


def test_favband_skips_when_favorite_out_of_band(settings):
    # favorite mid is 56c; a 10-30 band excludes it -> no favband entry
    trades = _run(_settings(settings, bands="LAX:10-30"))
    assert not _favband(trades)


def test_favband_skips_unmapped_city(settings):
    trades = _run(_settings(settings, bands="CHI:50-70"))  # LAX not mapped
    assert not _favband(trades)


def test_favband_disabled(settings):
    trades = _run(_settings(settings, bands="LAX:50-70", enabled=False))
    assert not _favband(trades)
