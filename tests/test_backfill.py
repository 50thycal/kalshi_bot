"""Kalshi history backfill: enumeration, candle parsing/storage, pacing, fallback."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot.kalshi.errors import KalshiAPIError
from kalshi_bot.weather.backfill import (
    WeatherBackfill,
    candle_ohlc,
    candle_rows,
    event_ticker_of,
    target_date_of,
)

NOW = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)


def test_ticker_helpers():
    assert event_ticker_of("KXHIGHNY-26JUN08-B74.5") == "KXHIGHNY-26JUN08"
    assert event_ticker_of("KXLOWTCHI-26JUN10-T65") == "KXLOWTCHI-26JUN10"
    assert target_date_of("KXHIGHNY-26JUN08-B74.5") == "2026-06-08"
    assert target_date_of("NONSENSE") is None


def test_candle_ohlc_variants():
    # Nested dict with integer cents.
    c = {"price": {"open": 40, "high": 55, "low": 38, "close": 52}}
    assert candle_ohlc(c, "price") == {"open": 40.0, "high": 55.0, "low": 38.0, "close": 52.0}
    # Nested dict with *_dollars fixed-point strings.
    c = {"yes_bid": {"open_dollars": "0.4000", "close_dollars": "0.5100"}}
    out = candle_ohlc(c, "yes_bid")
    assert out["open"] == 40.0 and out["close"] == 51.0
    # Top-level *_dollars node.
    c = {"yes_ask_dollars": {"close": "0.5700"}}
    assert candle_ohlc(c, "yes_ask")["close"] == 57.0
    # Flat keys.
    c = {"price_close": 61}
    assert candle_ohlc(c, "price")["close"] == 61.0


def test_candle_rows_parses_unix_ts_and_skips_missing():
    candles = [
        {"end_period_ts": 1750000000, "price": {"close": 52}, "yes_bid": {"close": 51},
         "yes_ask": {"close": 53}, "volume": "120.00", "open_interest": 80},
        {"price": {"close": 50}},  # no timestamp -> skipped
    ]
    rows = candle_rows("KXHIGHNY-26JUN08-B74.5", candles, 60)
    assert len(rows) == 1
    row = rows[0]
    assert row["end_period_ts"] == datetime.fromtimestamp(1750000000, tz=timezone.utc)
    assert row["price_close"] == 52.0 and row["yes_bid_close"] == 51.0
    assert row["volume"] == 120 and row["open_interest"] == 80
    assert row["period_minutes"] == 60


def _market(ticker, result="yes", close=NOW):
    return {
        "ticker": ticker,
        "yes_sub_title": "74° to 75°",
        "result": result,
        "open_time": (close - timedelta(hours=30)).isoformat(),
        "close_time": close.isoformat(),
    }


class FakeClient:
    """Settled-market pages + candlesticks; one ticker 404s to the historical path."""

    def __init__(self):
        self.candle_calls: list[str] = []
        self.historical_calls: list[str] = []

    def iter_markets(self, *, status, series_ticker, page_size, min_close_ts=None, **_kw):
        assert status == "settled" and min_close_ts is not None
        if series_ticker == "KXHIGHNY":
            yield _market("KXHIGHNY-26JUN10-B74.5", result="yes")
            yield _market("KXHIGHNY-26JUN10-B76.5", result="no")
            yield _market("KXHIGHNY-26JUN09-B72.5", result="yes", close=NOW - timedelta(days=1))
        # every other series: nothing settled

    def get_market_candlesticks(self, series_ticker, ticker, *, start_ts, end_ts, period_interval):
        self.candle_calls.append(ticker)
        if ticker == "KXHIGHNY-26JUN09-B72.5":
            raise KalshiAPIError(404, "archived", "/series/.../candlesticks")
        return {"candlesticks": [
            {"end_period_ts": end_ts - 3600, "price": {"open": 40, "close": 52},
             "yes_bid": {"close": 51}, "yes_ask": {"close": 53}, "volume": 10},
            {"end_period_ts": end_ts, "price": {"close": 99},
             "yes_bid": {"close": 99}, "yes_ask": {"close": 100}, "volume": 5},
        ]}

    def get_historical_market_candlesticks(self, ticker, *, start_ts, end_ts, period_interval):
        self.historical_calls.append(ticker)
        return {"candlesticks": [
            {"end_period_ts": end_ts, "price": {"close": 97}, "yes_bid": {"close": 96},
             "yes_ask": {"close": 98}, "volume": 1},
        ]}


def test_backfill_end_to_end_chunked(settings):
    settings.weather_backfill_days = 30.0
    settings.weather_backfill_markets_per_cycle = 2
    settings.weather_backfill_period_minutes = 60
    db.init_engine(settings.database_url)
    db.create_all()

    client = FakeClient()
    backfill = WeatherBackfill(client, settings, sleep_s=0)

    with db.session_scope() as session:
        s1 = backfill.run_once(session)
    assert s1.markets_enumerated == 3
    assert s1.markets_fetched == 2  # chunk cap
    assert s1.pending == 1

    with db.session_scope() as session:
        s2 = backfill.run_once(session)
        assert s2.markets_enumerated == 0  # enumeration runs once per process
        assert s2.markets_fetched == 1 and s2.pending == 0

        markets = session.scalars(select(m.BackfillWeatherMarket)).all()
        assert {mk.market_ticker for mk in markets} == {
            "KXHIGHNY-26JUN10-B74.5", "KXHIGHNY-26JUN10-B76.5", "KXHIGHNY-26JUN09-B72.5",
        }
        by_ticker = {mk.market_ticker: mk for mk in markets}
        winner = by_ticker["KXHIGHNY-26JUN10-B74.5"]
        assert (winner.city, winner.kind, winner.result) == ("NYC", "high", "yes")
        assert winner.target_date == "2026-06-10" and winner.event_ticker == "KXHIGHNY-26JUN10"
        assert (winner.low_f, winner.high_f) == (74.0, 75.0)
        assert all(mk.candles_fetched for mk in markets)

        candles = session.scalars(select(m.BackfillWeatherCandle)).all()
        # 2 candles for each of the two live-endpoint markets + 1 via historical.
        assert len(candles) == 5
        assert client.historical_calls == ["KXHIGHNY-26JUN09-B72.5"]
        one = next(c for c in candles if c.market_ticker == "KXHIGHNY-26JUN09-B72.5")
        assert one.price_close == 97.0 and one.yes_bid_close == 96.0

    # Re-enumeration in a fresh process must not duplicate markets, and refetching
    # candles replaces rather than appends.
    backfill2 = WeatherBackfill(client, settings, sleep_s=0)
    with db.session_scope() as session:
        s3 = backfill2.run_once(session)
        assert s3.markets_enumerated == 0
        assert session.scalar(select(func.count()).select_from(m.BackfillWeatherMarket)) == 3
        assert session.scalar(select(func.count()).select_from(m.BackfillWeatherCandle)) == 5


def test_backfill_error_leaves_market_pending(settings):
    settings.weather_backfill_markets_per_cycle = 10
    db.init_engine(settings.database_url)
    db.create_all()

    class ErrClient(FakeClient):
        def get_market_candlesticks(self, *a, **kw):
            raise RuntimeError("boom")

        def get_historical_market_candlesticks(self, *a, **kw):
            raise RuntimeError("boom")

    backfill = WeatherBackfill(ErrClient(), settings, sleep_s=0)
    with db.session_scope() as session:
        summary = backfill.run_once(session)
    assert summary.markets_fetched == 0
    assert summary.errors == 3  # one per market, all retried next cycle
    assert summary.pending == 3
