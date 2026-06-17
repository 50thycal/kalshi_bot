"""Tests for the persisted forecast->settlement validation dataset
(kalshi_bot/weather/validation.py + weather_forecast_outcomes)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot import repository as repo
from kalshi_bot.weather.validation import (
    WeatherValidationBackfill,
    materialize_event,
)

EV = "KXHIGHNY-26JUN08"
CITY = "NYC"
KIND = "high"
TARGET = "2026-06-08"
T0 = datetime(2026, 6, 8, 0, 0, tzinfo=timezone.utc)

# 3-bucket ladder; B72.5 ("72° to 73°") is the favorite and the eventual winner.
_BUCKETS = [
    ("KXHIGHNY-26JUN08-B70.5", "70° to 71°", 70.0, 71.0, 20.0),
    ("KXHIGHNY-26JUN08-B72.5", "72° to 73°", 72.0, 73.0, 60.0),
    ("KXHIGHNY-26JUN08-B74.5", "74° to 75°", 74.0, 75.0, 20.0),
]
WINNER_TICKER = "KXHIGHNY-26JUN08-B72.5"
WINNER_SUB = "72° to 73°"


def _init_db(settings):
    db.init_engine(settings.database_url)
    db.create_all()


def _add_buckets(session, captured_at, htc):
    for ticker, sub, low, high, mid in _BUCKETS:
        session.add(m.WeatherBucketSnapshot(
            captured_at=captured_at, event_ticker=EV, market_ticker=ticker, city=CITY,
            kind=KIND, subtitle=sub, low_f=low, high_f=high, mid_cents=mid,
            yes_bid_cents=mid - 1, yes_ask_cents=mid + 1, hours_to_close=htc, status="active",
        ))


def _add_settlement(session, event_ticker=EV, actual_high=72.0):
    session.add(m.WeatherSettlement(
        event_ticker=event_ticker, city=CITY, series_ticker="KXHIGHNY", target_date=TARGET,
        kind=KIND, winning_ticker=WINNER_TICKER, winning_subtitle=WINNER_SUB,
        actual_low_f=60.0, actual_high_f=actual_high, captured_at=T0 + timedelta(hours=20),
    ))


def _seed_full_event(session):
    """Two market-state cycles (>90s apart) plus forecasts straddling cycle 1 (for the
    no-lookahead check), an ensemble, an observation, and Polymarket snapshots."""
    c1 = T0 + timedelta(hours=8)   # htc 12
    c2 = T0 + timedelta(hours=14)  # htc 6
    _add_buckets(session, c1, htc=12.0)
    _add_buckets(session, c2, htc=6.0)
    # Forecasts: A before cycle 1, B after cycle 1 -> cycle 1 must use A, cycle 2 uses B.
    session.add(m.WeatherForecast(captured_at=c1 - timedelta(minutes=1), city=CITY,
                                  event_ticker=EV, target_date=TARGET, kind=KIND,
                                  forecast_high_f=71.0, source="nws"))
    session.add(m.WeatherForecast(captured_at=c1 + timedelta(minutes=1), city=CITY,
                                  event_ticker=EV, target_date=TARGET, kind=KIND,
                                  forecast_high_f=99.0, source="nws"))
    session.add(m.WeatherEnsemble(captured_at=c1 - timedelta(minutes=2), city=CITY,
                                  target_date=TARGET, kind=KIND, model="gfs",
                                  member_count=4, members_json=[72.0, 72.0, 73.0, 72.0]))
    session.add(m.WeatherObservation(captured_at=c1 - timedelta(minutes=3), city=CITY,
                                     station="KNYC", target_date=TARGET,
                                     running_max_f=70.0, running_min_f=61.0, obs_count=5))
    for ticker, sub, low, high, _mid in _BUCKETS:
        prob = 0.6 if ticker == WINNER_TICKER else 0.2
        session.add(m.PolymarketSnapshot(captured_at=c1 - timedelta(minutes=2), city=CITY,
                                         kind=KIND, target_date=TARGET, subtitle=sub,
                                         low_f=low, high_f=high, yes_prob=prob))
    _add_settlement(session)
    return c1, c2


def test_materialize_event_builds_labeled_cycles(settings):
    _init_db(settings)
    with db.session_scope() as s:
        c1, c2 = _seed_full_event(s)
        st = s.scalars(select(m.WeatherSettlement)).one()
        n = materialize_event(s, st, settings=settings)
    assert n == 2

    with db.session_scope() as s:
        rows = s.scalars(
            select(m.WeatherForecastOutcome).order_by(m.WeatherForecastOutcome.captured_at)
        ).all()
        assert [r.hours_to_close for r in rows] == [12.0, 6.0]
        r1, r2 = rows

        # No lookahead: cycle 1 used the EARLIER forecast (71.0), not the later 99.0.
        assert r1.forecast_f == 71.0
        assert r2.forecast_f == 99.0

        # Market implied mean = 0.2*70.5 + 0.6*72.5 + 0.2*74.5 = 72.5; favorite is B72.5.
        assert abs(r1.market_implied_mean_f - 72.5) < 1e-6
        assert r1.market_fav_low_f == 72.0 and r1.market_fav_high_f == 73.0
        assert abs(r1.divergence_f - (71.0 - 72.5)) < 1e-6

        # Outcome labels (actual high 72.0).
        assert r1.forecast_abs_err_f == 1.0          # |71 - 72|
        assert abs(r1.market_abs_err_f - 0.5) < 1e-6  # |72.5 - 72|
        assert r1.winning_low_f == 72.0 and r1.winning_high_f == 73.0

        # Sigma-free market calibration on the winner: normalized mid of B72.5 = 0.6.
        assert abs(r1.market_prob_winner - 0.6) < 1e-6
        assert r1.ens_prob_winner is not None and 0.0 < r1.ens_prob_winner <= 1.0

        # Context features attached.
        assert r1.ens_mean_f is not None and r1.ens_models == 1
        assert r1.obs_running_max_f == 70.0
        assert r1.pm_implied_mean_f is not None and abs(r1.pm_implied_mean_f - 72.5) < 1e-6
        assert r1.n_buckets == 3 and r1.raw_json["buckets"][1]["subtitle"] == WINNER_SUB


def test_materialize_is_idempotent(settings):
    _init_db(settings)
    with db.session_scope() as s:
        _seed_full_event(s)
        st = s.scalars(select(m.WeatherSettlement)).one()
        materialize_event(s, st, settings=settings)
    with db.session_scope() as s:
        st = s.scalars(select(m.WeatherSettlement)).one()
        n2 = materialize_event(s, st, settings=settings)
    assert n2 == 2
    with db.session_scope() as s:
        rows = s.scalars(select(m.WeatherForecastOutcome)).all()
        assert len(rows) == 2  # delete-then-insert: no duplication


def test_sparse_event_writes_sentinel_and_clears_queue(settings):
    """A settled event with no bucket snapshots gets one sentinel row so the work queue
    stops re-selecting it; the sentinel is excluded from graded analysis (htc NULL)."""
    _init_db(settings)
    with db.session_scope() as s:
        _add_settlement(s)
        assert repo.count_pending_outcome_settlements(s) == 1
        st = s.scalars(select(m.WeatherSettlement)).one()
        n = materialize_event(s, st, settings=settings)
        assert n == 1
    with db.session_scope() as s:
        row = s.scalars(select(m.WeatherForecastOutcome)).one()
        assert row.hours_to_close is None and row.n_buckets == 0
        assert row.actual_high_f == 72.0
        assert repo.count_pending_outcome_settlements(s) == 0
        assert repo.pending_outcome_settlements(s, limit=10) == []


def test_bounded_backfill_drains_queue(settings):
    _init_db(settings)
    settings.weather_validation_events_per_cycle = 5
    with db.session_scope() as s:
        for i in range(12):
            _add_settlement(s, event_ticker=f"KXHIGHNY-26JUN{i:02d}")
    backfill = WeatherValidationBackfill(settings)

    with db.session_scope() as s:
        sum1 = backfill.run_once(s)
    assert sum1.events_materialized == 5 and sum1.pending == 7 and sum1.errors == 0

    with db.session_scope() as s:
        backfill.run_once(s)
    with db.session_scope() as s:
        sum3 = backfill.run_once(s)
    assert sum3.pending == 0
    with db.session_scope() as s:
        assert repo.count_pending_outcome_settlements(s) == 0


def test_materialize_grades_hrrr_separately(settings):
    """HRRR rows (source='openmeteo_hrrr') share weather_forecasts with NWS but are graded
    into their own columns — forecast_f must stay NWS even when an HRRR row is later."""
    _init_db(settings)
    with db.session_scope() as s:
        c1 = T0 + timedelta(hours=8)  # htc 12
        _add_buckets(s, c1, htc=12.0)
        # NWS (71.0) earlier; HRRR (72.0) LATER. Without the source-split, _pick_latest_at
        # would wrongly take the HRRR row as forecast_f.
        s.add(m.WeatherForecast(captured_at=c1 - timedelta(minutes=2), city=CITY,
                                event_ticker=EV, target_date=TARGET, kind=KIND,
                                forecast_high_f=71.0, source="nws"))
        s.add(m.WeatherForecast(captured_at=c1 - timedelta(minutes=1), city=CITY,
                                event_ticker=EV, target_date=TARGET, kind=KIND,
                                forecast_high_f=72.0, source="openmeteo_hrrr"))
        _add_settlement(s)  # actual_high 72.0; market implied mean 72.5
        st = s.scalars(select(m.WeatherSettlement)).one()
        materialize_event(s, st, settings=settings)
    with db.session_scope() as s:
        row = s.scalars(select(m.WeatherForecastOutcome)).one()
        assert row.forecast_f == 71.0 and row.forecast_source == "nws"
        assert row.hrrr_f == 72.0
        assert row.hrrr_abs_err_f == 0.0  # |72 - 72|
        assert abs(row.hrrr_divergence_f - (72.0 - 72.5)) < 1e-6


def test_city_bias_ignores_hrrr_forecasts(settings):
    """The cal bias is NWS-anchored: an earlier HRRR row must not be picked as the
    'earliest forecast' and skew the offset."""
    _init_db(settings)
    with db.session_scope() as s:
        # HRRR row is EARLIER (would be the 'earliest' without the source filter).
        s.add(m.WeatherForecast(captured_at=T0, city=CITY, event_ticker=EV,
                                target_date=TARGET, kind=KIND, forecast_high_f=50.0,
                                source="openmeteo_hrrr"))
        s.add(m.WeatherForecast(captured_at=T0 + timedelta(hours=1), city=CITY, event_ticker=EV,
                                target_date=TARGET, kind=KIND, forecast_high_f=70.0, source="nws"))
        _add_settlement(s)  # actual_high 72, actual_low 60 -> mid 66
        bias = repo.weather_city_bias(s, shrinkage=3.0, min_events=1)
    # Uses NWS (70), not HRRR (50): (66 - 70) * 1/(1+3) = -1.0.
    assert bias[(CITY, KIND)] == -1.0


def test_low_kind_uses_low_extreme(settings):
    _init_db(settings)
    with db.session_scope() as s:
        c1 = T0 + timedelta(hours=8)
        s.add(m.WeatherBucketSnapshot(
            captured_at=c1, event_ticker="KXLOWTNYC-26JUN08", market_ticker="KXLOWTNYC-26JUN08-B58.5",
            city=CITY, kind="low", subtitle="58° to 59°", low_f=58.0, high_f=59.0,
            mid_cents=80.0, yes_bid_cents=79, yes_ask_cents=81, hours_to_close=10.0, status="active",
        ))
        s.add(m.WeatherForecast(captured_at=c1 - timedelta(minutes=1), city=CITY,
                                event_ticker="KXLOWTNYC-26JUN08", target_date=TARGET, kind="low",
                                forecast_high_f=57.0, source="nws"))
        s.add(m.WeatherSettlement(
            event_ticker="KXLOWTNYC-26JUN08", city=CITY, series_ticker="KXLOWTNYC",
            target_date=TARGET, kind="low", winning_ticker="KXLOWTNYC-26JUN08-B58.5",
            winning_subtitle="58° to 59°", actual_low_f=58.0, actual_high_f=80.0,
            captured_at=T0 + timedelta(hours=20),
        ))
        st = s.scalars(select(m.WeatherSettlement)).one()
        materialize_event(s, st, settings=settings)
    with db.session_scope() as s:
        row = s.scalars(select(m.WeatherForecastOutcome)).one()
        # Error is measured against the daily LOW (58.0), not the high: |57 - 58| = 1.0.
        assert row.kind == "low"
        assert row.forecast_abs_err_f == 1.0
