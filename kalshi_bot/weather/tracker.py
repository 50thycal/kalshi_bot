"""Weather baseline tracker: buy the favorite bucket of each city's daily high-temp
event at several hours-to-settlement windows, and collect NWS forecasts.

No forecast trading yet — that's the next round. This establishes the buy-the-favorite
baseline and the forecast dataset to evaluate it against.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

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
from .cities import CITIES, City
from .forecast import NwsForecastClient

logger = logging.getLogger(__name__)

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
_DATE_RE = re.compile(r"(\d{2})([A-Z]{3})(\d{2})")


@dataclass
class WeatherCycleSummary:
    events_seen: int = 0
    tracked: int = 0
    forecasts_stored: int = 0
    opened: int = 0
    skipped_no_book: int = 0
    open_positions: int = 0
    per_window: dict[str, int] = field(default_factory=dict)


def _implied_mid_cents(market: dict) -> float | None:
    """A bucket's implied probability (cents), from the market object's bid/ask."""
    yb = price_to_cents(market.get("yes_bid") if market.get("yes_bid") is not None else market.get("yes_bid_dollars"))
    ya = price_to_cents(market.get("yes_ask") if market.get("yes_ask") is not None else market.get("yes_ask_dollars"))
    if yb is not None and ya is not None:
        return (yb + ya) / 2
    if yb is not None:
        return float(yb)
    if ya is not None:
        return float(ya)
    last = market_last_price(market)
    return float(last) if last is not None else None


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


@dataclass
class _Tracked:
    city: City
    event: dict
    favorite: dict
    favorite_mid: float
    hours_to_close: float | None
    volume: int


class WeatherTracker:
    def __init__(self, client, settings: Settings, forecast: NwsForecastClient | None):
        self.client = client
        self.settings = settings
        self.forecast = forecast

    def run_once(self, session) -> WeatherCycleSummary:
        s = self.settings
        summary = WeatherCycleSummary()
        now = datetime.now(timezone.utc)

        tracked: list[_Tracked] = []
        for city in CITIES:
            try:
                page = self.client.get_events(
                    series_ticker=city.series_ticker, status="open", with_nested_markets=True
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "weather: events fetch failed",
                    extra={"extra_fields": {"city": city.code, "error": str(exc)}},
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
                        event=event,
                        favorite=favorite,
                        favorite_mid=fav_mid,
                        hours_to_close=(htc / 3600.0 if htc is not None else None),
                        volume=sum(market_volume(mk) for mk in markets),
                    )
                )

        tracked.sort(key=lambda t: t.volume, reverse=True)
        tracked = tracked[: s.weather_top_n]
        summary.tracked = len(tracked)

        for t in tracked:
            if s.weather_forecast_enabled and self.forecast is not None:
                self._store_forecast(session, t, summary)
            if t.hours_to_close is None:
                continue
            event_ticker = t.event.get("event_ticker")
            fav_metrics = None
            for hours in s.weather_entry_hours_list:
                strategy = f"weather_fav_h{int(hours)}"
                if t.hours_to_close > hours:
                    continue
                if not event_ticker or repo.weather_entered(session, event_ticker, strategy):
                    continue
                if fav_metrics is None:
                    fav_metrics = self._favorite_metrics(t.favorite)
                    if fav_metrics is None:
                        summary.skipped_no_book += 1
                        break
                self._enter(session, strategy, t, fav_metrics, summary)

        summary.open_positions = repo.count_open_paper_positions(session)
        return summary

    def _favorite_metrics(self, market: dict):
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

    def _enter(self, session, strategy: str, t: _Tracked, metrics, summary) -> None:
        s = self.settings
        qty = min(s.paper_order_size, metrics.depth_at_best_ask)
        if qty <= 0:
            summary.skipped_no_book += 1
            return
        price = metrics.best_yes_ask
        fee = kalshi_fee(price, qty, s.paper_fees_enabled)
        bucket = t.favorite.get("yes_sub_title") or t.favorite.get("subtitle") or ""
        repo.create_paper_trade(
            session,
            signal_id=None,
            ticker=t.favorite.get("ticker"),
            strategy=strategy,
            side="yes",
            action="buy",
            assumed_price=price,
            quantity=qty,
            fill_assumption=f"[{strategy}] {t.city.code} favorite '{bucket}' @ {price}c",
            entry_fee=fee,
            model_probability=(metrics.midpoint / 100.0 if metrics.midpoint is not None else None),
            edge=0.0,
        )
        repo.open_paper_position_for_trade(
            session, ticker=t.favorite.get("ticker"), strategy=strategy, side="yes",
            quantity=qty, avg_price=price,
        )
        summary.opened += 1
        summary.per_window[strategy] = summary.per_window.get(strategy, 0) + 1

    def _store_forecast(self, session, t: _Tracked, summary) -> None:
        target = _target_date(t.event)
        high = None
        try:
            high = self.forecast.daily_high_f(t.city.lat, t.city.lon, target) if target else None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "weather: forecast fetch failed",
                extra={"extra_fields": {"city": t.city.code, "error": str(exc)}},
            )
        repo.insert_weather_forecast(
            session,
            city=t.city.code,
            series_ticker=t.city.series_ticker,
            event_ticker=t.event.get("event_ticker"),
            target_date=target.isoformat() if target else None,
            station=t.city.station,
            forecast_high_f=high,
            source="nws",
            raw={"name": t.city.name, "favorite_mid": t.favorite_mid},
        )
        summary.forecasts_stored += 1
