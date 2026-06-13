"""Weather tracker: trade parallel paper books on Kalshi's daily HIGH and LOW
temperature markets, and collect the datasets a real forecast-edge model needs.

Per cycle, per tracked (city, kind=high|low) event:
- store the NWS point forecast (the books' signal),
- store the running station observations for the local day (the high is often
  already locked in by mid-afternoon while the market lags),
- store Open-Meteo ensemble member extremes (the forecast *distribution* behind
  P(temperature lands in bucket)), throttled,
- snapshot the full bucket ladder (the market's implied distribution), throttled,
- enter the favorite / nws / cal / pm / dist books at each hours-to-settlement window,
  plus the once-per-event cwin (per-city window) and obs (running-extreme) books.

The `dist` book is the forecast-edge model: it prices every bucket off the stored
ensemble distribution (weather/distribution.py) and buys the bucket whose model
probability most beats its ask — the live counterpart of scripts/weather_model_check.py.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .. import repository as repo
from ..config import Settings
from ..paper.engine import kalshi_fee
from ..scanner.metrics import (
    compute_metrics,
    compute_time_to_close,
    market_last_price,
    market_volume,
    parse_dt,
    price_to_cents,
)
from .buckets import forecast_in_bucket, parse_bucket_range
from .cities import CITIES, City
from .distribution import best_bucket_by_edge
from .ensemble import OpenMeteoEnsembleClient
from .forecast import NwsForecastClient, ObservedExtremes
from .polymarket import PolymarketClient

logger = logging.getLogger(__name__)

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
_DATE_RE = re.compile(r"(\d{2})([A-Z]{3})(\d{2})")

KINDS = ("high", "low")


@dataclass
class WeatherCycleSummary:
    events_seen: int = 0
    tracked: int = 0
    forecasts_stored: int = 0
    obs_stored: int = 0
    ensembles_stored: int = 0
    bucket_snaps: int = 0
    opened: int = 0
    skipped_no_book: int = 0
    settlements_captured: int = 0
    open_positions: int = 0
    pm_snaps: int = 0
    per_window: dict[str, int] = field(default_factory=dict)


def _implied_mid_cents(market: dict) -> float | None:
    """A bucket's implied probability (cents), from the market object's bid/ask."""
    yb, ya = _bid_ask_cents(market)
    if yb is not None and ya is not None:
        return (yb + ya) / 2
    if yb is not None:
        return float(yb)
    if ya is not None:
        return float(ya)
    last = market_last_price(market)
    return float(last) if last is not None else None


def _bid_ask_cents(market: dict) -> tuple[float | None, float | None]:
    yb = price_to_cents(market.get("yes_bid") if market.get("yes_bid") is not None else market.get("yes_bid_dollars"))
    ya = price_to_cents(market.get("yes_ask") if market.get("yes_ask") is not None else market.get("yes_ask_dollars"))
    return (float(yb) if yb is not None else None, float(ya) if ya is not None else None)


def _target_date(event: dict) -> date | None:
    match = _DATE_RE.search(event.get("event_ticker") or "")
    if match:
        month = _MONTHS.get(match.group(2))
        if month:
            try:
                return date(2000 + int(match.group(1)), month, int(match.group(3)))
            except ValueError:
                pass
    dt = parse_dt(event.get("close_time"))
    return dt.date() if dt else None


def _age_minutes(dt, now: datetime) -> float | None:
    """Minutes since dt; naive timestamps (sqlite) are treated as UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 60.0


@dataclass
class _Tracked:
    city: City
    kind: str  # "high" | "low"
    series: str
    event: dict
    favorite: dict
    favorite_mid: float
    hours_to_close: float | None
    volume: int


class WeatherTracker:
    def __init__(
        self,
        client,
        settings: Settings,
        forecast: NwsForecastClient | None,
        ensemble: OpenMeteoEnsembleClient | None = None,
        polymarket: PolymarketClient | None = None,
    ):
        self.client = client
        self.settings = settings
        self.forecast = forecast
        self.ensemble = ensemble
        self.polymarket = polymarket

    def _series_for(self, city: City, kind: str) -> str | None:
        if kind == "high":
            return city.series_high
        if not self.settings.weather_track_lows:
            return None
        return self.settings.weather_low_series_map.get(city.code) or city.series_low

    def run_once(self, session) -> WeatherCycleSummary:
        s = self.settings
        summary = WeatherCycleSummary()
        now = datetime.now(timezone.utc)

        tracked: list[_Tracked] = []
        for city in CITIES:
            for kind in KINDS:
                series = self._series_for(city, kind)
                if not series:
                    continue
                try:
                    page = self.client.get_events(
                        series_ticker=series, status="open", with_nested_markets=True
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "weather: events fetch failed",
                        extra={"extra_fields": {"city": city.code, "kind": kind, "error": str(exc)}},
                    )
                    continue
                for event in page.get("events") or []:
                    markets = event.get("markets") or []
                    if not markets:
                        continue
                    summary.events_seen += 1
                    favorite, fav_mid = None, -1.0
                    for mk in markets:
                        mid = _implied_mid_cents(mk)
                        if mid is not None and mid > fav_mid:
                            favorite, fav_mid = mk, mid
                    if favorite is None:
                        continue
                    htc = compute_time_to_close(
                        event.get("close_time") or favorite.get("close_time"), now=now
                    )
                    tracked.append(
                        _Tracked(
                            city=city,
                            kind=kind,
                            series=series,
                            event=event,
                            favorite=favorite,
                            favorite_mid=fav_mid,
                            hours_to_close=(htc / 3600.0 if htc is not None else None),
                            volume=sum(market_volume(mk) for mk in markets),
                        )
                    )

        # Rank by volume within each kind so lows don't crowd out highs (or vice versa).
        keep: list[_Tracked] = []
        for kind in KINDS:
            group = sorted(
                (t for t in tracked if t.kind == kind), key=lambda t: t.volume, reverse=True
            )
            keep.extend(group[: s.weather_top_n])
        tracked = keep
        summary.tracked = len(tracked)

        strategies = s.weather_strategy_list
        city_bias = (
            repo.weather_city_bias(
                session,
                shrinkage=s.weather_bias_shrinkage,
                min_events=s.weather_bias_min_events,
            )
            if "cal" in strategies
            else {}
        )
        obs_cache: dict[tuple[str, str], ObservedExtremes | None] = {}
        ensemble_done: set[tuple[str, str]] = set()

        # Polymarket cross-market signal (only the station-matched cities). Refresh the
        # index once per cycle; failures disable it for this cycle without touching books.
        pm_cities = set(s.weather_pm_city_list)
        pm_enabled = s.weather_polymarket_enabled and self.polymarket is not None and bool(pm_cities)
        if pm_enabled:
            try:
                n_pm = self.polymarket.refresh(pm_cities, now=now)
                logger.info("polymarket index", extra={"extra_fields": {"events": n_pm}})
            except Exception as exc:  # noqa: BLE001
                logger.warning("weather: polymarket refresh failed",
                               extra={"extra_fields": {"error": str(exc)}})
                pm_enabled = False

        for t in tracked:
            target = _target_date(t.event)
            forecast_val = None
            if s.weather_forecast_enabled and self.forecast is not None:
                forecast_val = self._store_forecast(session, t, target, summary)
            obs = self._collect_observations(session, t, target, now, summary, obs_cache)
            self._collect_ensemble(session, t, target, now, summary, ensemble_done)
            self._snapshot_ladder(session, t, now, summary)

            bias = city_bias.get((t.city.code, t.kind), 0.0)
            # For LOW markets the NWS overnight period ENDING on target_date is no longer
            # served by the time the books run (anchored near next-morning settlement), so
            # daily_low_f returns None and the nws/cal books would never trade. Fall back to
            # the day's running-minimum observation: the daily low lands in the early morning,
            # so once any entry window is open the coldest reading has already occurred and the
            # running min is a sound point estimate (often better than a stale forecast).
            effective_forecast = forecast_val
            forecast_source = "nws"
            if (
                t.kind == "low"
                and forecast_val is None
                and obs is not None
                and obs.min_f is not None
            ):
                effective_forecast = obs.min_f
                forecast_source = "obs"
            cal_val = (
                round(effective_forecast + bias, 1)
                if effective_forecast is not None and "cal" in strategies
                else None
            )
            rules = t.favorite.get("rules_primary") or t.event.get("rules_primary") or ""
            logger.info(
                "weather market",
                extra={"extra_fields": {
                    "city": t.city.code,
                    "kind": t.kind,
                    "station": t.city.station,
                    "event": t.event.get("event_ticker"),
                    "hours_to_close": round(t.hours_to_close, 2) if t.hours_to_close is not None else None,
                    "favorite": t.favorite.get("yes_sub_title") or t.favorite.get("subtitle"),
                    "favorite_mid": round(t.favorite_mid, 1),
                    "forecast_f": forecast_val,
                    "forecast_effective_f": effective_forecast,
                    "forecast_source": forecast_source,
                    "bias_offset_f": bias if "cal" in strategies else None,
                    "forecast_cal_f": cal_val,
                    "obs_running_f": (
                        (obs.max_f if t.kind == "high" else obs.min_f) if obs else None
                    ),
                    "obs_count": obs.obs_count if obs else 0,
                    "settlement": rules[:200],
                }},
            )
            event_ticker = t.event.get("event_ticker")
            if t.hours_to_close is None or not event_ticker:
                continue
            if t.hours_to_close > max(s.weather_entry_hours_list):
                continue  # too early — no window open yet

            markets = t.event.get("markets") or []
            # Resolve each book's target bucket once, sharing order-book metrics when two
            # books land on the same ticker (avoids redundant order-book fetches).
            metrics_cache: dict[str, object] = {}
            fav_market = t.favorite if "favorite" in strategies else None
            nws_market = (
                self._value_bucket(markets, effective_forecast)
                if "nws" in strategies and effective_forecast is not None
                else None
            )
            cal_market = self._value_bucket(markets, cal_val) if cal_val is not None else None

            # Polymarket signal: store its bucket prices and pick the Kalshi bucket whose
            # ask most underprices Polymarket's implied probability (trade toward it).
            pm_market, pm_prob = None, None
            if pm_enabled and target is not None and t.city.code in pm_cities:
                pm_buckets = self.polymarket.lookup(t.city.code, t.kind, target)
                if pm_buckets:
                    self._store_pm_snapshot(session, t, target, pm_buckets, now, summary)
                    pm_market, pm_prob = self._pm_best_bucket(markets, pm_buckets)

            # Distribution edge: price every bucket off the stored ensemble and pick the
            # one whose model probability most beats its ask (the forecast-edge book).
            dist_market, dist_prob = self._dist_best_bucket(session, t, target, markets)

            prefix = "weather_" if t.kind == "high" else "weather_low_"
            for hours in s.weather_entry_hours_list:
                if t.hours_to_close > hours:
                    continue
                if fav_market is not None:
                    self._maybe_enter(
                        session, f"{prefix}fav_h{int(hours)}", event_ticker, t, fav_market,
                        self._cached_metrics(fav_market, metrics_cache),
                        t.favorite_mid / 100.0, summary,
                    )
                if nws_market is not None:
                    self._maybe_enter(
                        session, f"{prefix}nws_h{int(hours)}", event_ticker, t, nws_market,
                        self._cached_metrics(nws_market, metrics_cache), None, summary,
                    )
                if cal_market is not None:
                    self._maybe_enter(
                        session, f"{prefix}cal_h{int(hours)}", event_ticker, t, cal_market,
                        self._cached_metrics(cal_market, metrics_cache), None, summary,
                    )
                if pm_market is not None:
                    self._maybe_enter(
                        session, f"{prefix}pm_h{int(hours)}", event_ticker, t, pm_market,
                        self._cached_metrics(pm_market, metrics_cache), pm_prob, summary,
                    )
                if dist_market is not None:
                    self._maybe_enter(
                        session, f"{prefix}dist_h{int(hours)}", event_ticker, t, dist_market,
                        self._cached_metrics(dist_market, metrics_cache), dist_prob, summary,
                    )

            # City-window book: the backfill-validated per-city entry window for HIGHs
            # (e.g. h18 for CHI/LAX/DEN). Buy the favorite once, at that city's window.
            cwin = (
                s.weather_city_window_map.get(t.city.code)
                if s.weather_city_window_enabled and t.kind == "high" and fav_market is not None
                else None
            )
            if cwin is not None and t.hours_to_close <= cwin:
                self._maybe_enter(
                    session, f"weather_cwin_h{int(cwin)}", event_ticker, t, fav_market,
                    self._cached_metrics(fav_market, metrics_cache),
                    t.favorite_mid / 100.0, summary,
                )

            # Obs-confirmed late entry: after the local cutoff hour the day's extreme has
            # usually already occurred, so the running max/min is a near-locked bound the
            # market can lag. Buy the bucket containing it once, if its ask is still cheap.
            self._obs_entry(session, t, obs, markets, event_ticker, metrics_cache, now, summary)

        self._capture_settlements(session, summary)
        summary.open_positions = repo.count_open_paper_positions(session)
        return summary

    # --- data collectors -------------------------------------------------------

    def _store_forecast(self, session, t: _Tracked, target: date | None, summary) -> float | None:
        value = None
        try:
            if target is not None:
                if t.kind == "high":
                    value = self.forecast.daily_high_f(t.city.lat, t.city.lon, target)
                else:
                    value = self.forecast.daily_low_f(t.city.lat, t.city.lon, target)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "weather: forecast fetch failed",
                extra={"extra_fields": {"city": t.city.code, "kind": t.kind, "error": str(exc)}},
            )
        repo.insert_weather_forecast(
            session,
            city=t.city.code,
            series_ticker=t.series,
            event_ticker=t.event.get("event_ticker"),
            target_date=target.isoformat() if target else None,
            station=t.city.station,
            kind=t.kind,
            forecast_high_f=value,
            source="nws",
            raw={"name": t.city.name, "favorite_mid": t.favorite_mid},
        )
        summary.forecasts_stored += 1
        return value

    def _collect_observations(
        self, session, t: _Tracked, target: date | None, now, summary, cache
    ) -> ObservedExtremes | None:
        s = self.settings
        if not s.weather_obs_enabled or self.forecast is None or target is None:
            return None
        fetch = getattr(self.forecast, "observed_extremes_f", None)
        if fetch is None:
            return None
        key = (t.city.code, target.isoformat())
        if key in cache:
            return cache[key]
        row = repo.latest_weather_observation(session, t.city.code, target.isoformat())
        age = _age_minutes(row.captured_at, now) if row is not None else None
        if age is not None and age < s.weather_obs_interval_minutes:
            obs = (
                ObservedExtremes(row.running_max_f, row.running_min_f, row.obs_count or 0, row.last_obs_at)
                if row.running_max_f is not None and row.running_min_f is not None
                else None
            )
            cache[key] = obs
            return obs
        obs = None
        try:
            obs = fetch(t.city.station, target, t.city.tz)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "weather: observations fetch failed",
                extra={"extra_fields": {"city": t.city.code, "error": str(exc)}},
            )
        if obs is not None:
            repo.insert_weather_observation(
                session,
                city=t.city.code,
                station=t.city.station,
                target_date=target.isoformat(),
                running_max_f=obs.max_f,
                running_min_f=obs.min_f,
                obs_count=obs.obs_count,
                last_obs_at=obs.last_obs_at,
            )
            summary.obs_stored += 1
        cache[key] = obs
        return obs

    def _collect_ensemble(self, session, t: _Tracked, target: date | None, now, summary, done) -> None:
        s = self.settings
        if not s.weather_ensemble_enabled or self.ensemble is None or target is None:
            return
        key = (t.city.code, target.isoformat())
        if key in done:
            return
        done.add(key)
        last = repo.latest_weather_ensemble_at(session, t.city.code, target.isoformat())
        age = _age_minutes(last, now)
        if age is not None and age < s.weather_ensemble_interval_minutes:
            return
        try:
            days = self.ensemble.daily_extremes(
                t.city.lat, t.city.lon, t.city.tz, target, s.weather_ensemble_model_list
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "weather: ensemble fetch failed",
                extra={"extra_fields": {"city": t.city.code, "error": str(exc)}},
            )
            return
        for day in days:
            for kind, members in (("high", day.highs), ("low", day.lows)):
                repo.insert_weather_ensemble(
                    session,
                    city=t.city.code,
                    target_date=target.isoformat(),
                    kind=kind,
                    model=day.model,
                    members=members,
                )
                summary.ensembles_stored += 1

    def _snapshot_ladder(self, session, t: _Tracked, now, summary) -> None:
        event_ticker = t.event.get("event_ticker")
        if not event_ticker:
            return
        last = repo.latest_bucket_snapshot_at(session, event_ticker)
        age = _age_minutes(last, now)
        if age is not None and age < self.settings.weather_ladder_interval_minutes:
            return
        rows = []
        for mk in t.event.get("markets") or []:
            subtitle = mk.get("yes_sub_title") or mk.get("subtitle")
            low, high = parse_bucket_range(subtitle)
            yb, ya = _bid_ask_cents(mk)
            rows.append(dict(
                event_ticker=event_ticker,
                market_ticker=mk.get("ticker"),
                city=t.city.code,
                kind=t.kind,
                subtitle=subtitle,
                low_f=low,
                high_f=high,
                yes_bid_cents=yb,
                yes_ask_cents=ya,
                mid_cents=_implied_mid_cents(mk),
                volume=market_volume(mk),
                hours_to_close=t.hours_to_close,
                status=mk.get("status"),
            ))
        if rows:
            summary.bucket_snaps += repo.insert_weather_bucket_snapshots(session, rows)

    # --- Polymarket cross-market signal ------------------------------------------

    def _store_pm_snapshot(self, session, t: _Tracked, target, pm_buckets, now, summary) -> None:
        last = repo.latest_polymarket_snapshot_at(session, t.city.code, t.kind, target.isoformat())
        age = _age_minutes(last, now)
        if age is not None and age < self.settings.weather_pm_interval_minutes:
            return
        rows = [dict(
            city=t.city.code, kind=t.kind, target_date=target.isoformat(),
            subtitle=b.subtitle, low_f=b.low_f, high_f=b.high_f, yes_prob=b.yes_prob,
        ) for b in pm_buckets]
        if rows:
            summary.pm_snaps += repo.insert_polymarket_snapshots(session, rows)

    def _pm_best_bucket(self, markets: list[dict], pm_buckets):
        """The Kalshi bucket whose ask most underprices Polymarket's implied probability,
        if that edge clears the cost hurdle. Returns (market, pm_prob) or (None, None)."""
        pm_by_range = {(b.low_f, b.high_f): b.yes_prob for b in pm_buckets}
        min_edge = self.settings.paper_min_edge_cents
        best = None  # (edge, market, prob)
        for mk in markets:
            rng = parse_bucket_range(mk.get("yes_sub_title") or mk.get("subtitle"))
            prob = pm_by_range.get(rng)
            if prob is None:
                continue
            _yb, ya = _bid_ask_cents(mk)
            if ya is None or not (1 <= ya <= 99):
                continue
            edge = prob * 100.0 - ya - kalshi_fee(int(round(ya)), 1, self.settings.paper_fees_enabled)
            if edge >= min_edge and (best is None or edge > best[0]):
                best = (edge, mk, prob)
        if best is None:
            return (None, None)
        return (best[1], best[2])

    def _dist_best_bucket(self, session, t: _Tracked, target, markets: list[dict]):
        """The Kalshi bucket whose ensemble-model probability most beats its ask, net of
        the fee, if that edge clears the threshold. Returns (market, model_prob) or
        (None, None). Reads the latest stored ensemble for this (city, date, kind)."""
        s = self.settings
        if not s.weather_dist_enabled or target is None:
            return (None, None)
        members_by_model = repo.latest_weather_ensemble_members(
            session, t.city.code, target.isoformat(), t.kind
        )
        if not members_by_model:
            return (None, None)
        ladder = []
        for mk in markets:
            rng = parse_bucket_range(mk.get("yes_sub_title") or mk.get("subtitle"))
            _yb, ya = _bid_ask_cents(mk)
            ladder.append((rng, ya))
        pick = best_bucket_by_edge(
            ladder, members_by_model,
            sigma=s.weather_dist_sigma,
            min_edge_cents=s.weather_dist_min_edge_cents,
            fee_cents=lambda ask: kalshi_fee(int(round(ask)), 1, s.paper_fees_enabled),
        )
        if pick is None:
            return (None, None)
        idx, prob, _edge = pick
        return (markets[idx], prob)

    # --- settlements + entries ---------------------------------------------------

    def _capture_settlements(self, session, summary) -> None:
        """Record the actual winning bucket for recently settled events (the ground
        truth to grade NWS forecast vs the market favorite)."""
        for city in CITIES:
            for kind in KINDS:
                series = self._series_for(city, kind)
                if not series:
                    continue
                try:
                    page = self.client.get_events(
                        series_ticker=series, status="settled", with_nested_markets=True
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "weather: settled events fetch failed",
                        extra={"extra_fields": {"city": city.code, "kind": kind, "error": str(exc)}},
                    )
                    continue
                for event in page.get("events") or []:
                    event_ticker = event.get("event_ticker")
                    if not event_ticker or repo.weather_settlement_exists(session, event_ticker):
                        continue
                    winner = next(
                        (mk for mk in (event.get("markets") or []) if (mk.get("result") or "").lower() == "yes"),
                        None,
                    )
                    if winner is None:
                        continue
                    subtitle = winner.get("yes_sub_title") or winner.get("subtitle")
                    low, high = parse_bucket_range(subtitle)
                    target = _target_date(event)
                    repo.insert_weather_settlement(
                        session,
                        event_ticker=event_ticker,
                        city=city.code,
                        series_ticker=series,
                        target_date=target.isoformat() if target else None,
                        kind=kind,
                        winning_ticker=winner.get("ticker"),
                        winning_subtitle=subtitle,
                        actual_low_f=low,
                        actual_high_f=high,
                    )
                    summary.settlements_captured += 1

    def _obs_entry(self, session, t: _Tracked, obs, markets, event_ticker,
                   metrics_cache, now, summary) -> None:
        """Buy the bucket containing the station's running extreme once, after the local
        cutoff hour and only if its ask is still <= the cap (the thermometer-lag edge)."""
        s = self.settings
        if not s.weather_obs_entry_enabled or obs is None:
            return
        extreme = obs.max_f if t.kind == "high" else obs.min_f
        if extreme is None:
            return
        cutoff = (s.weather_obs_high_after_hour if t.kind == "high"
                  else s.weather_obs_low_after_hour)
        try:
            local_hour = now.astimezone(ZoneInfo(t.city.tz)).hour
        except Exception:  # noqa: BLE001 — bad tz shouldn't break the cycle
            return
        if local_hour < cutoff:
            return
        market = self._value_bucket(markets, extreme)
        if market is None:
            return
        metrics = self._cached_metrics(market, metrics_cache)
        if metrics is None or metrics.best_yes_ask is None:
            return
        if metrics.best_yes_ask > s.weather_obs_ask_cap:
            return  # too expensive — the lag has already been priced in
        strategy = "weather_obs" if t.kind == "high" else "weather_low_obs"
        self._maybe_enter(session, strategy, event_ticker, t, market, metrics, None, summary)

    def _value_bucket(self, markets: list[dict], value: float | None) -> dict | None:
        """The bucket whose range contains the given (forecast) temperature."""
        if value is None:
            return None
        for mk in markets:
            low, high = parse_bucket_range(mk.get("yes_sub_title") or mk.get("subtitle"))
            if forecast_in_bucket(value, low, high):
                return mk
        return None

    def _cached_metrics(self, market: dict | None, cache: dict):
        """Order-book metrics for a bucket, memoized per ticker within a cycle so two
        books that pick the same bucket don't double-fetch the order book."""
        if market is None:
            return None
        tk = market.get("ticker")
        if tk not in cache:
            cache[tk] = self._bucket_metrics(market)
        return cache[tk]

    def _bucket_metrics(self, market: dict):
        ticker = market.get("ticker")
        try:
            ob = self.client.get_orderbook(ticker, depth=self.settings.orderbook_depth)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "weather: orderbook fetch failed",
                extra={"extra_fields": {"ticker": ticker, "error": str(exc)}},
            )
            return None
        metrics = compute_metrics(market, ob, top_n=self.settings.orderbook_depth)
        return metrics if (metrics.two_sided and metrics.best_yes_ask is not None) else None

    def _maybe_enter(
        self, session, strategy: str, event_ticker: str, t: _Tracked, market: dict,
        metrics, model_probability: float | None, summary,
    ) -> None:
        if repo.weather_entered(session, event_ticker, strategy):
            return
        if metrics is None:
            summary.skipped_no_book += 1
            return
        s = self.settings
        qty = min(s.paper_order_size, metrics.depth_at_best_ask)
        if qty <= 0:
            summary.skipped_no_book += 1
            return
        price = metrics.best_yes_ask
        fee = kalshi_fee(price, qty, s.paper_fees_enabled)
        bucket = market.get("yes_sub_title") or market.get("subtitle") or ""
        ticker = market.get("ticker")
        repo.create_paper_trade(
            session,
            signal_id=None,
            ticker=ticker,
            strategy=strategy,
            side="yes",
            action="buy",
            assumed_price=price,
            quantity=qty,
            fill_assumption=f"[{strategy}] {t.city.code} '{bucket}' @ {price}c",
            entry_fee=fee,
            model_probability=model_probability,
            edge=0.0,
        )
        repo.open_paper_position_for_trade(
            session, ticker=ticker, strategy=strategy, side="yes", quantity=qty, avg_price=price,
        )
        summary.opened += 1
        summary.per_window[strategy] = summary.per_window.get(strategy, 0) + 1
