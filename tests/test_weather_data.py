"""Weather data-collection tests: daily lows, station observations, ensemble
distributions, and bucket-ladder snapshots."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import respx
from httpx import Response
from sqlalchemy import func, select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot.weather.ensemble import OpenMeteoEnsembleClient
from kalshi_bot.weather.forecast import NwsForecastClient, OpenMeteoForecastClient
from kalshi_bot.weather.tracker import WeatherTracker

# --- NWS client: daily low + station observations -----------------------------


@respx.mock
def test_nws_daily_low_parse():
    respx.get("https://api.weather.gov/points/40.779,-73.9693").mock(
        return_value=Response(200, json={"properties": {"forecast": "https://api.weather.gov/gridpoints/OKX/33,35/forecast"}})
    )
    respx.get("https://api.weather.gov/gridpoints/OKX/33,35/forecast").mock(
        return_value=Response(200, json={"properties": {"periods": [
            {"name": "Tonight", "isDaytime": False, "startTime": "2026-06-07T18:00:00-04:00",
             "endTime": "2026-06-08T06:00:00-04:00", "temperature": 61, "temperatureUnit": "F"},
            {"name": "Monday", "isDaytime": True, "startTime": "2026-06-08T06:00:00-04:00",
             "endTime": "2026-06-08T18:00:00-04:00", "temperature": 79, "temperatureUnit": "F"},
            {"name": "Monday Night", "isDaytime": False, "startTime": "2026-06-08T18:00:00-04:00",
             "endTime": "2026-06-09T06:00:00-04:00", "temperature": 64, "temperatureUnit": "F"},
        ]}})
    )
    with NwsForecastClient("test-agent") as nws:
        # The low for Jun 8 is the overnight period ENDING Jun 8 morning.
        assert nws.daily_low_f(40.779, -73.9693, date(2026, 6, 8)) == 61.0
        # The low for Jun 9 is the next night (ends Jun 9 morning).
        assert nws.daily_low_f(40.779, -73.9693, date(2026, 6, 9)) == 64.0
        # No overnight period ends on Jun 10 -> no forecast.
        assert nws.daily_low_f(40.779, -73.9693, date(2026, 6, 10)) is None


@respx.mock
def test_nws_observed_extremes():
    def obs(ts, c):
        return {"properties": {"timestamp": ts, "temperature": {"unitCode": "wmoUnit:degC", "value": c}}}

    respx.get("https://api.weather.gov/stations/KNYC/observations").mock(
        return_value=Response(200, json={"features": [
            obs("2026-06-08T10:51:00+00:00", 18.0),   # 06:51 ET -> 64.4F
            obs("2026-06-08T14:51:00+00:00", 25.0),   # 10:51 ET -> 77.0F
            obs("2026-06-08T18:51:00+00:00", None),   # QC-failed reading -> skipped
            obs("2026-06-08T19:51:00+00:00", 26.7),   # 15:51 ET -> 80.1F
            obs("2026-06-08T02:51:00+00:00", 30.0),   # 22:51 ET Jun 7 local -> excluded
        ]})
    )
    with NwsForecastClient("test-agent") as nws:
        out = nws.observed_extremes_f("KNYC", date(2026, 6, 8), "America/New_York")
    assert out is not None
    assert out.max_f == 80.1 and out.min_f == 64.4
    assert out.obs_count == 3  # null + out-of-day readings excluded


# --- Open-Meteo ensemble client ------------------------------------------------


@respx.mock
def test_ensemble_daily_extremes():
    hours = [f"2026-06-08T{h:02d}:00" for h in range(24)] + [f"2026-06-09T{h:02d}:00" for h in range(24)]
    # Member 0 peaks at 80 / dips to 60 on Jun 8; member 1 peaks 84 / dips 62.
    # Day 2 (Jun 9) runs hotter — must NOT leak into Jun 8's extremes.
    m0 = [70.0] * 24 + [90.0] * 24
    m1 = [71.0] * 24 + [90.0] * 24
    m0[5], m0[14] = 60.0, 80.0
    m1[5], m1[14] = 62.0, 84.0
    respx.get("https://ensemble-api.open-meteo.com/v1/ensemble").mock(
        return_value=Response(200, json={"hourly": {
            "time": hours,
            "temperature_2m": m0,
            "temperature_2m_member01": m1,
            "relative_humidity_2m": [50] * 48,  # non-temperature key -> ignored
        }})
    )
    with OpenMeteoEnsembleClient() as ens:
        days = ens.daily_extremes(40.78, -73.97, "America/New_York", date(2026, 6, 8), ["gfs_seamless"])
    assert len(days) == 1
    day = days[0]
    assert day.model == "gfs_seamless"
    assert sorted(day.highs) == [80.0, 84.0]  # per-member Jun 8 maxima (Jun 9 excluded)
    assert sorted(day.lows) == [60.0, 62.0]


# --- Open-Meteo HRRR point-forecast client -------------------------------------


@respx.mock
def test_hrrr_daily_extremes_parse():
    hours = [f"2026-06-08T{h:02d}:00" for h in range(24)] + [f"2026-06-09T{h:02d}:00" for h in range(24)]
    temps = [70.0] * 24 + [90.0] * 24  # Jun 9 hotter — must NOT leak into Jun 8.
    temps[5], temps[14] = 55.0, 88.0   # Jun 8 dip / peak
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=Response(200, json={"hourly": {"time": hours, "temperature_2m": temps}})
    )
    with OpenMeteoForecastClient() as hrrr:
        assert hrrr.daily_high_f(40.78, -73.97, "America/New_York", date(2026, 6, 8)) == 88.0
        assert hrrr.daily_low_f(40.78, -73.97, "America/New_York", date(2026, 6, 8)) == 55.0


@respx.mock
def test_hrrr_short_series_returns_none():
    # Out-of-CONUS / >48h: too few target-date hours to trust an extreme -> None (fail soft).
    hours = [f"2026-06-08T{h:02d}:00" for h in range(5)]
    respx.get("https://api.open-meteo.com/v1/forecast").mock(
        return_value=Response(200, json={"hourly": {"time": hours, "temperature_2m": [70.0] * 5}})
    )
    with OpenMeteoForecastClient() as hrrr:
        assert hrrr.daily_high_f(40.78, -73.97, "America/New_York", date(2026, 6, 8)) is None


@respx.mock
def test_hrrr_http_error_returns_none():
    respx.get("https://api.open-meteo.com/v1/forecast").mock(return_value=Response(400))
    with OpenMeteoForecastClient() as hrrr:
        assert hrrr.daily_high_f(0.0, 0.0, "UTC", date(2026, 6, 8)) is None


# --- Tracker: HRRR collection ---------------------------------------------------


class FakeHrrr:
    def __init__(self, high=78.3, low=59.1):
        self.calls = 0
        self.high = high
        self.low = low

    def daily_high_f(self, lat, lon, tz, target):
        self.calls += 1
        return self.high

    def daily_low_f(self, lat, lon, tz, target):
        self.calls += 1
        return self.low


def _high_event_client():
    close = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    event = {
        "event_ticker": "KXHIGHNY-26JUN08",
        "series_ticker": "KXHIGHNY",
        "close_time": close,
        "markets": [_bucket("KXHIGHNY-26JUN08-B74.5", "74° to 75°", 55, 57)],
    }

    class Client:
        def get_events(self, series_ticker, status="open", with_nested_markets=True):
            if series_ticker == "KXHIGHNY" and status == "open":
                return {"events": [event]}
            return {"events": []}

        def get_orderbook(self, ticker, depth=None):
            return {"orderbook_fp": {"yes_dollars": [["0.5500", "300"]], "no_dollars": [["0.4300", "300"]]}}

    return Client()


def test_hrrr_forecast_stored_and_throttled(settings):
    settings.bot_mode = "weather"
    settings.weather_strategies = "favorite"
    settings.weather_track_lows = False
    db.init_engine(settings.database_url)
    db.create_all()

    hrrr = FakeHrrr()
    tracker = WeatherTracker(_high_event_client(), settings, forecast=None, hrrr=hrrr)
    with db.session_scope() as session:
        s1 = tracker.run_once(session)
    assert s1.hrrr_stored == 1
    with db.session_scope() as session:
        rows = session.scalars(select(m.WeatherForecast)).all()
        assert len(rows) == 1  # only HRRR (forecast=None -> no NWS row written)
        assert rows[0].source == "openmeteo_hrrr"
        assert rows[0].forecast_high_f == 78.3 and rows[0].kind == "high"

    # Immediately after: within the HRRR interval -> no fetch, no new row.
    with db.session_scope() as session:
        s2 = tracker.run_once(session)
        assert s2.hrrr_stored == 0
        assert session.scalar(select(func.count()).select_from(m.WeatherForecast)) == 1
    assert hrrr.calls == 1


def test_hrrr_disabled_no_store(settings):
    settings.bot_mode = "weather"
    settings.weather_strategies = "favorite"
    settings.weather_track_lows = False
    settings.weather_hrrr_enabled = False
    db.init_engine(settings.database_url)
    db.create_all()

    hrrr = FakeHrrr()
    tracker = WeatherTracker(_high_event_client(), settings, forecast=None, hrrr=hrrr)
    with db.session_scope() as session:
        s1 = tracker.run_once(session)
    assert s1.hrrr_stored == 0
    assert hrrr.calls == 0
    with db.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(m.WeatherForecast)) == 0


def test_ensemble_default_model_list_widened(settings):
    # Default now spans GFS + ECMWF + ICON-EPS + GEM-EPS (wider disagreement -> better sigma).
    assert settings.weather_ensemble_model_list == [
        "gfs_seamless", "ecmwf_ifs025", "icon_seamless", "gem_global",
    ]


# --- Tracker: low books, ladder snapshots, settlement kinds ---------------------


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


def _low_event(hours_ahead):
    close = (datetime.now(timezone.utc) + timedelta(hours=hours_ahead)).isoformat()
    return {
        "event_ticker": "KXLOWTNYC-26JUN08",
        "series_ticker": "KXLOWTNYC",
        "title": "Lowest temperature in New York City today?",
        "close_time": close,
        "markets": [
            _bucket("KXLOWTNYC-26JUN08-B58.5", "58° to 59°", 55, 57),  # favorite
            _bucket("KXLOWTNYC-26JUN08-B60.5", "60° to 61°", 20, 22),
        ],
    }


_LOW_BOOKS = {
    "KXLOWTNYC-26JUN08-B58.5": {"orderbook_fp": {"yes_dollars": [["0.5500", "300"]], "no_dollars": [["0.4300", "300"]]}},
    "KXLOWTNYC-26JUN08-B60.5": {"orderbook_fp": {"yes_dollars": [["0.2000", "300"]], "no_dollars": [["0.7800", "300"]]}},
}


class LowOnlyClient:
    def get_events(self, series_ticker, status="open", with_nested_markets=True):
        if series_ticker == "KXLOWTNYC" and status == "open":
            return {"events": [_low_event(hours_ahead=3)]}
        return {"events": []}

    def get_orderbook(self, ticker, depth=None):
        return _LOW_BOOKS[ticker]


class LowForecast:
    def daily_high_f(self, lat, lon, target):
        raise AssertionError("high forecast should not be fetched for a low event")

    def daily_low_f(self, lat, lon, target):
        return 61.0  # -> bucket 60-61, NOT the favorite


def test_low_book_buys_low_forecast_bucket(settings):
    settings.bot_mode = "weather"
    settings.weather_strategies = "favorite,nws"
    settings.weather_track_lows = True
    settings.weather_entry_hours = "12,8,4"
    db.init_engine(settings.database_url)
    db.create_all()

    tracker = WeatherTracker(LowOnlyClient(), settings, LowForecast())
    with db.session_scope() as session:
        summary = tracker.run_once(session)
    assert summary.opened == 6  # 3 favorite + 3 nws windows

    with db.session_scope() as session:
        trades = session.scalars(select(m.PaperTrade)).all()
        fav = [t for t in trades if (t.strategy or "").startswith("weather_low_fav")]
        nws = [t for t in trades if (t.strategy or "").startswith("weather_low_nws")]
        assert len(fav) == 3 and len(nws) == 3
        assert all(t.market_ticker == "KXLOWTNYC-26JUN08-B58.5" for t in fav)
        assert all(t.market_ticker == "KXLOWTNYC-26JUN08-B60.5" for t in nws)
        # Low forecasts land with kind='low'.
        kinds = {f.kind for f in session.scalars(select(m.WeatherForecast)).all()}
        assert kinds == {"low"}


class LowNoForecast:
    """Low event where the NWS overnight period has already passed (daily_low_f -> None),
    but the station's running minimum is available."""

    def daily_high_f(self, lat, lon, target):
        raise AssertionError("high forecast should not be fetched for a low event")

    def daily_low_f(self, lat, lon, target):
        return None  # overnight period ending today is no longer served

    def observed_extremes_f(self, station, target, tz):
        from kalshi_bot.weather.forecast import ObservedExtremes

        # Running min 60.5 -> bucket 60-61, NOT the favorite (58-59).
        return ObservedExtremes(max_f=72.0, min_f=60.5, obs_count=12, last_obs_at=None)


def test_low_nws_falls_back_to_running_min(settings):
    settings.bot_mode = "weather"
    settings.weather_strategies = "favorite,nws"
    settings.weather_track_lows = True
    settings.weather_entry_hours = "12,8,4"
    settings.weather_obs_enabled = True
    settings.weather_obs_entry_enabled = False  # isolate the nws fallback from the obs book
    db.init_engine(settings.database_url)
    db.create_all()

    tracker = WeatherTracker(LowOnlyClient(), settings, LowNoForecast())
    with db.session_scope() as session:
        summary = tracker.run_once(session)
    # 3 favorite windows + 3 nws windows (nws now trades off the running-min fallback).
    assert summary.opened == 6

    with db.session_scope() as session:
        trades = session.scalars(select(m.PaperTrade)).all()
        fav = [t for t in trades if (t.strategy or "").startswith("weather_low_fav")]
        nws = [t for t in trades if (t.strategy or "").startswith("weather_low_nws")]
        assert len(fav) == 3 and len(nws) == 3
        assert all(t.market_ticker == "KXLOWTNYC-26JUN08-B58.5" for t in fav)
        # nws buys the running-min bucket (60-61), not the favorite.
        assert all(t.market_ticker == "KXLOWTNYC-26JUN08-B60.5" for t in nws)
        # The stored NWS forecast is still null (we didn't fabricate a forecast row).
        fc = session.scalars(select(m.WeatherForecast)).all()
        assert all(f.forecast_high_f is None and f.kind == "low" for f in fc)


def test_low_nws_no_trade_without_forecast_or_obs(settings):
    """With no forecast and no observations, the nws low book stays silent (fail-closed)."""
    settings.bot_mode = "weather"
    settings.weather_strategies = "favorite,nws"
    settings.weather_track_lows = True
    settings.weather_entry_hours = "12,8,4"
    settings.weather_obs_enabled = False  # no observation fallback available
    db.init_engine(settings.database_url)
    db.create_all()

    tracker = WeatherTracker(LowOnlyClient(), settings, LowNoForecast())
    with db.session_scope() as session:
        summary = tracker.run_once(session)
    assert summary.opened == 3  # favorite only; nws has no point estimate

    with db.session_scope() as session:
        trades = session.scalars(select(m.PaperTrade)).all()
        assert all((t.strategy or "").startswith("weather_low_fav") for t in trades)


def test_bucket_ladder_snapshot_and_throttle(settings):
    settings.bot_mode = "weather"
    settings.weather_strategies = "favorite"
    settings.weather_track_lows = False
    db.init_engine(settings.database_url)
    db.create_all()

    close = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    event = {
        "event_ticker": "KXHIGHNY-26JUN08",
        "series_ticker": "KXHIGHNY",
        "close_time": close,
        "markets": [
            _bucket("KXHIGHNY-26JUN08-B74.5", "74° to 75°", 55, 57),
            _bucket("KXHIGHNY-26JUN08-B76.5", "76° to 77°", 20, 22),
        ],
    }

    class Client:
        def get_events(self, series_ticker, status="open", with_nested_markets=True):
            if series_ticker == "KXHIGHNY" and status == "open":
                return {"events": [event]}
            return {"events": []}

        def get_orderbook(self, ticker, depth=None):
            return {"orderbook_fp": {"yes_dollars": [["0.5500", "300"]], "no_dollars": [["0.4300", "300"]]}}

    tracker = WeatherTracker(Client(), settings, forecast=None)
    with db.session_scope() as session:
        s1 = tracker.run_once(session)
    assert s1.bucket_snaps == 2  # one row per bucket
    with db.session_scope() as session:
        snaps = session.scalars(select(m.WeatherBucketSnapshot)).all()
        assert {x.subtitle for x in snaps} == {"74° to 75°", "76° to 77°"}
        by_sub = {x.subtitle: x for x in snaps}
        assert by_sub["74° to 75°"].mid_cents == 56.0
        assert by_sub["74° to 75°"].kind == "high"
        assert by_sub["76° to 77°"].low_f == 76.0 and by_sub["76° to 77°"].high_f == 77.0

    # Second cycle straight after: throttled — no new ladder rows.
    with db.session_scope() as session:
        s2 = tracker.run_once(session)
        assert s2.bucket_snaps == 0
        assert session.scalar(select(func.count()).select_from(m.WeatherBucketSnapshot)) == 2


def test_low_settlement_captured_with_kind(settings):
    settings.bot_mode = "weather"
    settings.weather_track_lows = True
    db.init_engine(settings.database_url)
    db.create_all()

    settled = {
        "event_ticker": "KXLOWTNYC-26JUN07",
        "series_ticker": "KXLOWTNYC",
        "close_time": "2026-06-08T05:00:00Z",
        "markets": [
            {"ticker": "KXLOWTNYC-26JUN07-B58.5", "yes_sub_title": "58° to 59°", "result": "yes"},
            {"ticker": "KXLOWTNYC-26JUN07-B60.5", "yes_sub_title": "60° to 61°", "result": "no"},
        ],
    }

    class SettledClient:
        def get_events(self, series_ticker, status="open", with_nested_markets=True):
            if series_ticker == "KXLOWTNYC" and status == "settled":
                return {"events": [settled]}
            return {"events": []}

    tracker = WeatherTracker(SettledClient(), settings, forecast=None)
    with db.session_scope() as session:
        summary = tracker.run_once(session)
    assert summary.settlements_captured == 1
    with db.session_scope() as session:
        st = session.scalar(select(m.WeatherSettlement))
        assert st.event_ticker == "KXLOWTNYC-26JUN07"
        assert st.kind == "low"
        assert (st.actual_low_f, st.actual_high_f) == (58.0, 59.0)


class FakeObsForecast:
    """Forecast client stub that also serves observations."""

    def daily_high_f(self, lat, lon, target):
        return 77.0

    def observed_extremes_f(self, station, target, tz):
        from kalshi_bot.weather.forecast import ObservedExtremes

        return ObservedExtremes(max_f=75.2, min_f=61.0, obs_count=10, last_obs_at=None)


def test_observations_stored_and_throttled(settings):
    settings.bot_mode = "weather"
    settings.weather_strategies = "favorite"
    settings.weather_track_lows = False
    db.init_engine(settings.database_url)
    db.create_all()

    close = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    event = {
        "event_ticker": "KXHIGHNY-26JUN08",
        "series_ticker": "KXHIGHNY",
        "close_time": close,
        "markets": [_bucket("KXHIGHNY-26JUN08-B74.5", "74° to 75°", 55, 57)],
    }

    class Client:
        def get_events(self, series_ticker, status="open", with_nested_markets=True):
            if series_ticker == "KXHIGHNY" and status == "open":
                return {"events": [event]}
            return {"events": []}

        def get_orderbook(self, ticker, depth=None):
            return {"orderbook_fp": {"yes_dollars": [["0.5500", "300"]], "no_dollars": [["0.4300", "300"]]}}

    tracker = WeatherTracker(Client(), settings, FakeObsForecast())
    with db.session_scope() as session:
        s1 = tracker.run_once(session)
    assert s1.obs_stored == 1
    with db.session_scope() as session:
        row = session.scalar(select(m.WeatherObservation))
        assert row.running_max_f == 75.2 and row.running_min_f == 61.0
        assert row.station == "KNYC" and row.obs_count == 10

    # Immediately after: within the obs interval -> no new row.
    with db.session_scope() as session:
        s2 = tracker.run_once(session)
        assert s2.obs_stored == 0
        assert session.scalar(select(func.count()).select_from(m.WeatherObservation)) == 1


class FakeEnsemble:
    def __init__(self):
        self.calls = 0

    def daily_extremes(self, lat, lon, tz, target, models):
        from kalshi_bot.weather.ensemble import EnsembleDay

        self.calls += 1
        return [EnsembleDay(model="gfs_seamless", highs=[76.0, 78.0, 80.0], lows=[60.0, 61.0, 62.0])]


def test_ensemble_stored_and_throttled(settings):
    settings.bot_mode = "weather"
    settings.weather_strategies = "favorite"
    settings.weather_track_lows = False
    db.init_engine(settings.database_url)
    db.create_all()

    close = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    event = {
        "event_ticker": "KXHIGHNY-26JUN08",
        "series_ticker": "KXHIGHNY",
        "close_time": close,
        "markets": [_bucket("KXHIGHNY-26JUN08-B74.5", "74° to 75°", 55, 57)],
    }

    class Client:
        def get_events(self, series_ticker, status="open", with_nested_markets=True):
            if series_ticker == "KXHIGHNY" and status == "open":
                return {"events": [event]}
            return {"events": []}

        def get_orderbook(self, ticker, depth=None):
            return {"orderbook_fp": {"yes_dollars": [["0.5500", "300"]], "no_dollars": [["0.4300", "300"]]}}

    ens = FakeEnsemble()
    tracker = WeatherTracker(Client(), settings, forecast=None, ensemble=ens)
    with db.session_scope() as session:
        s1 = tracker.run_once(session)
    assert s1.ensembles_stored == 2  # one 'high' + one 'low' row for the model
    with db.session_scope() as session:
        rows = session.scalars(select(m.WeatherEnsemble)).all()
        assert {r.kind for r in rows} == {"high", "low"}
        high = next(r for r in rows if r.kind == "high")
        assert high.member_count == 3
        assert high.mean_f == 78.0
        assert high.members_json == [76.0, 78.0, 80.0]

    # Second cycle: throttled (60-min interval) -> no extra fetch or rows.
    with db.session_scope() as session:
        s2 = tracker.run_once(session)
        assert s2.ensembles_stored == 0
    assert ens.calls == 1
