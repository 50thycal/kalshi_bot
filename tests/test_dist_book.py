"""The weather_dist / weather_low_dist distribution edge book."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot import repository as repo
from kalshi_bot.weather.distribution import (
    best_bucket_by_edge,
    member_bucket_prob,
    model_bucket_probs,
)
from kalshi_bot.weather.forecast import ObservedExtremes
from kalshi_bot.weather.tracker import WeatherTracker

# --- probability math --------------------------------------------------------------


def test_member_bucket_prob_kernel_and_indicator():
    # sigma 0 -> hard indicator on the rounded member
    assert member_bucket_prob(74.4, 74, 75, 0.0) == 1.0
    assert member_bucket_prob(76.0, 74, 75, 0.0) == 0.0
    # a tight kernel centered in-bucket -> most mass inside [73.5, 75.5)
    p = member_bucket_prob(74.5, 74, 75, 1.0)
    assert 0.6 < p < 0.95
    # open-ended top bucket "95 or above": a member well above keeps ~all mass
    assert member_bucket_prob(99.0, 95, None, 1.0) > 0.99


def test_model_bucket_probs_blend_normalizes_and_concentrates():
    buckets = [(None, 72), (73, 74), (75, 76), (77, None)]
    members = {"gfs": [75.0, 75.4, 74.8, 75.6], "ecmwf": [75.2, 74.9, 75.5, 75.1]}
    probs = model_bucket_probs(members, buckets, sigma=1.0)
    assert probs is not None and abs(sum(probs) - 1.0) < 1e-9
    # all members cluster ~75 -> the (75,76) bucket carries the most mass
    assert probs.index(max(probs)) == 2
    assert model_bucket_probs({}, buckets, sigma=1.0) is None


def test_best_bucket_by_edge_picks_underpriced_bucket():
    # model ~certain the high is 75-76; that bucket's ask is cheap (40c) -> big YES edge.
    ladder = [((None, 72), 5.0), ((73, 74), 20.0), ((75, 76), 40.0), ((77, None), 5.0)]
    members = {"gfs": [75.4, 75.5, 75.2, 75.6], "ecmwf": [75.3, 75.1, 75.5, 75.4]}
    pick = best_bucket_by_edge(
        ladder, members, sigma=1.0, min_edge_cents=5.0, fee_cents=lambda a: 0.0
    )
    assert pick is not None
    idx, prob, edge = pick
    assert idx == 2 and prob > 0.6 and edge > 5.0
    # when every bucket is richly priced (ask 99c), no edge can clear the threshold
    rich = [(rng, 99.0) for rng, _ask in ladder]
    assert best_bucket_by_edge(
        rich, members, sigma=1.0, min_edge_cents=5.0, fee_cents=lambda a: 0.0
    ) is None


# --- live entry --------------------------------------------------------------------


def _bucket(ticker, sub, yb, ya):
    return {
        "ticker": ticker, "yes_sub_title": sub, "status": "active",
        "yes_bid_dollars": f"{yb / 100:.4f}", "yes_ask_dollars": f"{ya / 100:.4f}",
        "volume_fp": "1000.00", "open_interest_fp": "500.00",
    }


# Favorite is 74-75 (mid 56). The 76-77 bucket is cheap (ask 45) but the ensemble says
# the high is ~76.5 -> the dist book should buy 76-77, not the market favorite.
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
                    _bucket("KXHIGHLAX-26JUN12-B74.5", "74° to 75°", 55, 57),
                    _bucket("KXHIGHLAX-26JUN12-B76.5", "76° to 77°", 43, 45),
                ],
            }]}
        return {"events": []}

    def get_orderbook(self, ticker, depth=None):
        return _BOOKS[ticker]


class _Forecast:
    def daily_high_f(self, lat, lon, target):
        return 75.0

    def observed_extremes_f(self, station, target, tz):
        return ObservedExtremes(max_f=70.0, min_f=60.0, obs_count=5, last_obs_at=None)


def _settings(settings, *, enabled=True, sigma=1.0, min_edge=5.0):
    settings.bot_mode = "weather"
    settings.weather_strategies = "favorite"
    settings.weather_track_lows = False
    settings.weather_entry_hours = "4"
    settings.weather_polymarket_enabled = False
    settings.weather_city_window_enabled = False
    settings.weather_obs_entry_enabled = False
    settings.weather_dist_enabled = enabled
    settings.weather_dist_sigma = sigma
    settings.weather_dist_min_edge_cents = min_edge
    return settings


def _seed_ensemble(session):
    # both models cluster the high near 76.5 -> the 76-77 bucket carries the mass
    for model in ("gfs_seamless", "ecmwf_ifs025"):
        repo.insert_weather_ensemble(
            session, city="LAX", target_date="2026-06-12", kind="high",
            model=model, members=[76.3, 76.5, 76.7, 76.4, 76.6],
        )


def test_dist_book_buys_ensemble_bucket(settings):
    _settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    tracker = WeatherTracker(LaxHighClient(), settings, _Forecast())
    with db.session_scope() as session:
        _seed_ensemble(session)
        tracker.run_once(session)
        trades = session.scalars(select(m.PaperTrade)).all()
        dist = [t for t in trades if (t.strategy or "").startswith("weather_dist")]
        assert len(dist) == 1
        assert dist[0].market_ticker == "KXHIGHLAX-26JUN12-B76.5"  # the model's bucket
        assert dist[0].model_probability is not None and dist[0].model_probability > 0.6


def test_dist_book_skips_without_ensemble(settings):
    _settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    tracker = WeatherTracker(LaxHighClient(), settings, _Forecast())
    with db.session_scope() as session:
        tracker.run_once(session)  # no ensemble seeded
        trades = session.scalars(select(m.PaperTrade)).all()
        assert not any((t.strategy or "").startswith("weather_dist") for t in trades)


def test_dist_book_respects_disable_flag(settings):
    _settings(settings, enabled=False)
    db.init_engine(settings.database_url)
    db.create_all()
    tracker = WeatherTracker(LaxHighClient(), settings, _Forecast())
    with db.session_scope() as session:
        _seed_ensemble(session)
        tracker.run_once(session)
        trades = session.scalars(select(m.PaperTrade)).all()
        assert not any((t.strategy or "").startswith("weather_dist") for t in trades)
