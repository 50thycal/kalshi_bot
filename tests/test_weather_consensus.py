"""Consensus ('con') book: the pure convergence logic + tracker wiring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot.weather import consensus as con
from kalshi_bot.weather.tracker import WeatherTracker

RANGES = [(70.0, 71.0), (72.0, 73.0), (74.0, 75.0)]


# --- pure logic --------------------------------------------------------------------


def test_bucket_index_for_temp():
    assert con.bucket_index_for_temp(72.4, RANGES) == 1
    assert con.bucket_index_for_temp(70.0, RANGES) == 0
    assert con.bucket_index_for_temp(69.0, RANGES) == 0   # nearest below
    assert con.bucket_index_for_temp(80.0, RANGES) == 2   # nearest above
    assert con.bucket_index_for_temp(None, RANGES) is None


def test_k_pick_counts_within_tolerance():
    # fc/ens/obs all in bucket 1, pm missing.
    votes = {"fc": 1, "ens": 1, "obs": 1, "pm": None}
    assert con.k_pick(votes, k=3, tol=1) == (1, 3)
    assert con.k_pick(votes, k=4, tol=1) is None          # only 3 present
    # within tolerance: buckets 1 and 2 count together at tol=1.
    spread = {"fc": 1, "ens": 2, "obs": 2, "pm": None}
    idx, count = con.k_pick(spread, k=3, tol=1)
    assert count == 3 and idx in (1, 2)
    assert con.k_pick(spread, k=3, tol=0) is None          # exact: max 2 share a bucket


def test_weighted_pick_weights_obs_pm():
    weights = dict(con.DEFAULT_WEIGHTS)  # obs/pm = 2
    votes = {"fc": 1, "ens": 1, "obs": 1, "pm": None}      # mass at 1 = 1+1+2 = 4
    assert con.weighted_pick(votes, weights=weights, tol=1, min_mass=3) == (1, 4.0)
    assert con.weighted_pick(votes, weights=weights, tol=1, min_mass=5) is None
    # fc+ens alone (no obs/pm) is mass 2 -> below the 3 threshold.
    fc_ens = {"fc": 0, "ens": 0, "obs": None, "pm": None}
    assert con.weighted_pick(fc_ens, weights=weights, tol=1, min_mass=3) is None


def test_choose_bucket_early_high_deviates_only():
    p = con.ConsensusParams()
    votes = {"fc": 1, "ens": 1, "obs": 1, "pm": None}      # consensus -> bucket 1
    # favorite is bucket 0 -> deviation -> trade bucket 1.
    pick = con.choose_bucket("high", 20.0, votes, fav_idx=0, params=p)
    assert pick is not None and pick[0] == 1
    # favorite IS bucket 1 -> confirming an early-high favorite is skipped.
    assert con.choose_bucket("high", 20.0, votes, fav_idx=1, params=p) is None


def test_choose_bucket_confirm_mode_needs_unanimity_on_favorite():
    p = con.ConsensusParams(confirm_k=4)
    agree4 = {"fc": 2, "ens": 2, "obs": 2, "pm": 2}        # all four agree on bucket 2
    # low (any window) -> confirm: must land on the favorite.
    assert con.choose_bucket("low", 8.0, agree4, fav_idx=2, params=p) == (2, 4.0)
    assert con.choose_bucket("low", 8.0, agree4, fav_idx=0, params=p) is None   # not the favorite
    # high LATE window also uses confirm mode.
    assert con.choose_bucket("high", 8.0, agree4, fav_idx=2, params=p) == (2, 4.0)
    # only 3 present -> below confirm_k=4 -> skip.
    agree3 = {"fc": 2, "ens": 2, "obs": 2, "pm": None}
    assert con.choose_bucket("low", 8.0, agree3, fav_idx=2, params=p) is None


def test_params_from_settings(settings):
    settings.weather_consensus_weights = "fc=1,ens=1,obs=3,pm=2"
    settings.weather_consensus_early_windows = "20,14"
    settings.weather_consensus_confirm_k = 3
    p = con.params_from_settings(settings)
    assert p.weights["obs"] == 3.0 and p.confirm_k == 3
    assert p.early_windows == frozenset({20.0, 14.0})


# --- tracker integration: early-high deviation -------------------------------------


def _bucket(ticker, sub, yb, ya):
    return {
        "ticker": ticker, "yes_sub_title": sub, "status": "active",
        "yes_bid_dollars": f"{yb / 100:.4f}", "yes_ask_dollars": f"{ya / 100:.4f}",
        "volume_fp": "1000.00", "open_interest_fp": "500.00",
    }


class _ConvergeForecast:
    """NWS stub: forecast high + running obs both land in the NON-favorite bucket (72-73)."""

    def daily_high_f(self, lat, lon, target):
        return 72.4

    def observed_extremes_f(self, station, target, tz):
        from kalshi_bot.weather.forecast import ObservedExtremes
        return ObservedExtremes(max_f=72.1, min_f=60.0, obs_count=12, last_obs_at=None)


class _Ensemble:
    def daily_extremes(self, lat, lon, tz, target, models):
        from kalshi_bot.weather.ensemble import EnsembleDay
        # high members mean ~72.5 -> bucket 72-73 (agrees with fc + obs, not the favorite).
        return [EnsembleDay(model="gfs_seamless", highs=[72.0, 73.0, 72.0, 73.0],
                            lows=[60.0, 61.0, 60.0, 61.0])]


_BOOKS = {
    "KXHIGHNY-26JUN08-B70.5": {"orderbook_fp": {"yes_dollars": [["0.5500", "300"]], "no_dollars": [["0.4300", "300"]]}},
    "KXHIGHNY-26JUN08-B72.5": {"orderbook_fp": {"yes_dollars": [["0.2000", "300"]], "no_dollars": [["0.7700", "300"]]}},
    "KXHIGHNY-26JUN08-B74.5": {"orderbook_fp": {"yes_dollars": [["0.1000", "300"]], "no_dollars": [["0.8700", "300"]]}},
}


class _Client:
    def __init__(self):
        close = (datetime.now(timezone.utc) + timedelta(hours=19.5)).isoformat()
        self.event = {
            "event_ticker": "KXHIGHNY-26JUN08", "series_ticker": "KXHIGHNY", "close_time": close,
            "markets": [
                _bucket("KXHIGHNY-26JUN08-B70.5", "70° to 71°", 55, 57),  # favorite (highest mid)
                _bucket("KXHIGHNY-26JUN08-B72.5", "72° to 73°", 20, 22),
                _bucket("KXHIGHNY-26JUN08-B74.5", "74° to 75°", 10, 12),
            ],
        }

    def get_events(self, series_ticker, status="open", with_nested_markets=True):
        if series_ticker == "KXHIGHNY" and status == "open":
            return {"events": [self.event]}
        return {"events": []}

    def get_orderbook(self, ticker, depth=None):
        return _BOOKS[ticker]


def test_con_book_deviates_to_converged_bucket_on_early_high(settings):
    settings.bot_mode = "weather"
    settings.weather_strategies = "favorite"        # isolate: only fav + con
    settings.weather_entry_hours = "20"
    settings.weather_track_lows = False
    settings.weather_dist_enabled = False
    settings.weather_obs_entry_enabled = False
    settings.weather_city_window_enabled = False
    settings.weather_consensus_enabled = True
    db.init_engine(settings.database_url)
    db.create_all()

    tracker = WeatherTracker(_Client(), settings, _ConvergeForecast(), ensemble=_Ensemble())
    with db.session_scope() as session:
        tracker.run_once(session)

    with db.session_scope() as session:
        trades = session.scalars(select(m.PaperTrade)).all()
        by_strat = {t.strategy: t for t in trades}
        # consensus deviated to the converged NON-favorite bucket (72-73).
        assert "weather_con_h20" in by_strat
        assert by_strat["weather_con_h20"].market_ticker == "KXHIGHNY-26JUN08-B72.5"
        # the plain favorite book still bought the favorite (70-71).
        assert by_strat["weather_fav_h20"].market_ticker == "KXHIGHNY-26JUN08-B70.5"


def test_con_book_skips_when_signals_disagree(settings):
    settings.bot_mode = "weather"
    settings.weather_strategies = "favorite"
    settings.weather_entry_hours = "20"
    settings.weather_track_lows = False
    settings.weather_dist_enabled = False
    settings.weather_obs_entry_enabled = False
    settings.weather_city_window_enabled = False
    settings.weather_consensus_enabled = True
    settings.weather_obs_enabled = False           # drop obs -> only fc present (no convergence)
    settings.weather_ensemble_enabled = False      # drop ens -> mass < 3
    db.init_engine(settings.database_url)
    db.create_all()

    tracker = WeatherTracker(_Client(), settings, _ConvergeForecast())
    with db.session_scope() as session:
        tracker.run_once(session)
    with db.session_scope() as session:
        strats = {t.strategy for t in session.scalars(select(m.PaperTrade)).all()}
        assert "weather_con_h20" not in strats   # fc alone (mass 1) can't reach the threshold
        assert "weather_fav_h20" in strats
