"""The weather_obs / weather_low_obs obs-confirmed late-entry book."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot.weather.forecast import ObservedExtremes
from kalshi_bot.weather.tracker import WeatherTracker


def _bucket(ticker, sub, yb, ya):
    return {
        "ticker": ticker, "yes_sub_title": sub, "status": "active",
        "yes_bid_dollars": f"{yb / 100:.4f}", "yes_ask_dollars": f"{ya / 100:.4f}",
        "volume_fp": "1000.00", "open_interest_fp": "500.00",
    }


# Favorite is 74-75 (mid 56); the running max points at 76-77, whose ask is 45.
_BOOKS = {
    "KXHIGHLAX-26JUN12-B74.5": {"orderbook_fp": {"yes_dollars": [["0.5500", "300"]],
                                                 "no_dollars": [["0.4300", "300"]]}},
    "KXHIGHLAX-26JUN12-B76.5": {"orderbook_fp": {"yes_dollars": [["0.4300", "300"]],
                                                 "no_dollars": [["0.5500", "300"]]}},  # ask 45
}


class LaxHighClient:
    def get_events(self, series_ticker, status="open", with_nested_markets=True):
        if series_ticker == "KXHIGHLAX" and status == "open":
            close = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
            return {"events": [{
                "event_ticker": "KXHIGHLAX-26JUN12", "series_ticker": "KXHIGHLAX",
                "close_time": close,
                "markets": [
                    _bucket("KXHIGHLAX-26JUN12-B74.5", "74° to 75°", 55, 57),  # favorite
                    _bucket("KXHIGHLAX-26JUN12-B76.5", "76° to 77°", 43, 45),
                ],
            }]}
        return {"events": []}

    def get_orderbook(self, ticker, depth=None):
        return _BOOKS[ticker]


class ObsForecast:
    """Forecast stub that serves observations: running max 76.4 -> bucket 76-77."""

    def daily_high_f(self, lat, lon, target):
        return 75.0

    def observed_extremes_f(self, station, target, tz):
        return ObservedExtremes(max_f=76.4, min_f=60.0, obs_count=12, last_obs_at=None)


def _settings(settings, *, cap=90.0):
    settings.bot_mode = "weather"
    settings.weather_strategies = "favorite"
    settings.weather_track_lows = False
    settings.weather_entry_hours = "4"
    settings.weather_polymarket_enabled = False
    settings.weather_city_window_enabled = False
    settings.weather_obs_entry_enabled = True
    settings.weather_obs_high_after_hour = 0  # always past cutoff (wall-clock independent)
    settings.weather_obs_ask_cap = cap
    return settings


def test_obs_book_buys_running_max_bucket(settings):
    _settings(settings, cap=90.0)
    db.init_engine(settings.database_url)
    db.create_all()
    tracker = WeatherTracker(LaxHighClient(), settings, ObsForecast())
    with db.session_scope() as session:
        tracker.run_once(session)
        trades = session.scalars(select(m.PaperTrade)).all()
        obs = [t for t in trades if (t.strategy or "").startswith("weather_obs")]
        assert len(obs) == 1
        assert obs[0].strategy == "weather_obs"
        # bought the running-max bucket (76-77), NOT the market favorite (74-75)
        assert obs[0].market_ticker == "KXHIGHLAX-26JUN12-B76.5"
        # an observation row was also stored
        assert session.scalar(select(m.WeatherObservation)) is not None


def test_obs_book_skips_when_ask_above_cap(settings):
    _settings(settings, cap=40.0)  # running-max bucket ask is 45 > 40
    db.init_engine(settings.database_url)
    db.create_all()
    tracker = WeatherTracker(LaxHighClient(), settings, ObsForecast())
    with db.session_scope() as session:
        tracker.run_once(session)
        trades = session.scalars(select(m.PaperTrade)).all()
        assert not any((t.strategy or "").startswith("weather_obs") for t in trades)


def test_obs_book_respects_cutoff_hour(settings):
    _settings(settings, cap=90.0)
    settings.weather_obs_high_after_hour = 25  # impossible hour -> never enters
    db.init_engine(settings.database_url)
    db.create_all()
    tracker = WeatherTracker(LaxHighClient(), settings, ObsForecast())
    with db.session_scope() as session:
        tracker.run_once(session)
        trades = session.scalars(select(m.PaperTrade)).all()
        assert not any((t.strategy or "").startswith("weather_obs") for t in trades)
