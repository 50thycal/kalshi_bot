"""Polymarket client parsing/index + the weather_pm cross-market paper book."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import respx
from httpx import Response
from sqlalchemy import select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot.weather import polymarket as pmod
from kalshi_bot.weather.polymarket import PmBucket, PolymarketClient
from kalshi_bot.weather.tracker import WeatherTracker

NOW = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)


# --- pure parsing -----------------------------------------------------------------


def test_yes_prob_prefers_bid_ask_midpoint():
    assert pmod._yes_prob({"bestBid": 0.78, "bestAsk": 0.82}) == 0.80
    assert pmod._yes_prob({"outcomePrices": '["0.30", "0.70"]'}) == 0.30
    assert pmod._yes_prob({"outcomePrices": ["0.45", "0.55"]}) == 0.45
    assert pmod._yes_prob({"lastTradePrice": 0.12}) == 0.12
    assert pmod._yes_prob({}) is None


def test_parse_helpers():
    allowed = {"LAX", "MIA", "AUS"}
    assert pmod.match_city("Highest temperature in Los Angeles on June 12?", allowed) == "LAX"
    assert pmod.match_city("Highest temperature in NYC?", allowed) is None  # not station-matched
    assert pmod.parse_kind("Lowest temperature in Miami") == "low"
    assert pmod.parse_date("... on June 12?", now=NOW) == "2026-06-12"
    assert pmod.parse_bucket("76-77°F") == (76.0, 77.0)
    assert pmod.parse_bucket("100°F or higher") == (100.0, None)


@respx.mock
def test_client_refresh_and_lookup():
    event = {
        "title": "Highest temperature in Los Angeles on June 12?",
        "markets": [
            {"groupItemTitle": "74-75°F", "outcomePrices": '["0.30","0.70"]'},
            {"groupItemTitle": "76-77°F", "bestBid": 0.78, "bestAsk": 0.82},
        ],
    }
    respx.get("https://gamma-api.polymarket.com/events").mock(
        return_value=Response(200, json=[event])
    )
    with PolymarketClient() as pm:
        n = pm.refresh({"LAX", "MIA", "AUS"}, now=NOW)
    assert n == 1
    buckets = pm.lookup("LAX", "high", date(2026, 6, 12))
    by_range = {(b.low_f, b.high_f): b.yes_prob for b in buckets}
    assert by_range[(74.0, 75.0)] == 0.30
    assert by_range[(76.0, 77.0)] == 0.80
    assert pm.lookup("MIA", "high", date(2026, 6, 12)) == []  # not in index


# --- tracker integration: the weather_pm book ------------------------------------


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


class LaxClient:
    def get_events(self, series_ticker, status="open", with_nested_markets=True):
        if series_ticker == "KXHIGHLAX" and status == "open":
            close = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
            return {"events": [{
                "event_ticker": "KXHIGHLAX-26JUN12",
                "series_ticker": "KXHIGHLAX",
                "close_time": close,
                "markets": [
                    _bucket("KXHIGHLAX-26JUN12-B74.5", "74° to 75°", 55, 57),  # market favorite
                    _bucket("KXHIGHLAX-26JUN12-B76.5", "76° to 77°", 20, 22),
                ],
            }]}
        return {"events": []}

    def get_orderbook(self, ticker, depth=None):
        return _BOOKS[ticker]


class FakePolymarket:
    """Duck-typed PolymarketClient: Polymarket favors the 76-77 bucket (not Kalshi's fav)."""

    def __init__(self):
        self.refreshed = None

    def refresh(self, cities, *, now=None):
        self.refreshed = set(cities)
        return 1

    def lookup(self, city, kind, target):
        if city == "LAX" and kind == "high":
            return [PmBucket(74.0, 75.0, "74-75°F", 0.20),
                    PmBucket(76.0, 77.0, "76-77°F", 0.85)]
        return []


def _tracker_settings(settings):
    settings.bot_mode = "weather"
    settings.weather_strategies = "favorite"   # only fav baseline + the pm signal book
    settings.weather_track_lows = False
    settings.weather_entry_hours = "4"          # single window -> one entry per book
    settings.weather_polymarket_enabled = True
    settings.weather_pm_cities = "LAX,MIA,AUS"
    return settings


def test_pm_book_trades_toward_polymarket(settings):
    _tracker_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()

    pm = FakePolymarket()
    tracker = WeatherTracker(LaxClient(), settings, forecast=None, polymarket=pm)
    with db.session_scope() as session:
        summary = tracker.run_once(session)

    assert pm.refreshed == {"LAX", "MIA", "AUS"}
    assert summary.pm_snaps == 2  # one row per Polymarket bucket
    with db.session_scope() as session:
        trades = session.scalars(select(m.PaperTrade)).all()
        fav = [t for t in trades if (t.strategy or "").startswith("weather_pm")]
        assert len(fav) == 1
        # pm signal buys the bucket Polymarket favors (76-77), NOT the market favorite.
        assert fav[0].market_ticker == "KXHIGHLAX-26JUN12-B76.5"
        assert abs((fav[0].model_probability or 0) - 0.85) < 1e-9
        snaps = session.scalars(select(m.PolymarketSnapshot)).all()
        assert {s.subtitle for s in snaps} == {"74-75°F", "76-77°F"}
        assert all(s.city == "LAX" and s.kind == "high" for s in snaps)


def test_pm_book_skips_non_pm_cities(settings):
    _tracker_settings(settings)
    settings.weather_pm_cities = "MIA,AUS"  # LAX excluded
    db.init_engine(settings.database_url)
    db.create_all()

    tracker = WeatherTracker(LaxClient(), settings, forecast=None, polymarket=FakePolymarket())
    with db.session_scope() as session:
        summary = tracker.run_once(session)
    assert summary.pm_snaps == 0
    with db.session_scope() as session:
        trades = session.scalars(select(m.PaperTrade)).all()
        assert not any((t.strategy or "").startswith("weather_pm") for t in trades)
        assert session.scalar(select(m.PolymarketSnapshot)) is None
