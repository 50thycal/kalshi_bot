"""Backfill Kalshi market HISTORY for the temperature series — settled markets and
their hourly candlesticks — into the dedicated backfill_* tables.

Why separate tables: rows here come from Kalshi's REST archives (a reconstruction),
not from the bot observing markets live. Keeping the provenances apart means research
can compare or combine them deliberately instead of silently mixing a backfilled
price path with a captured one.

How it runs: inside the weather worker (the only place with Kalshi credentials and a
read-write DATABASE_URL), as a bounded chunk per cycle so it never competes with
trading for API budget:

  1. once per process start, enumerate settled markets for every city's high/low
     series within WEATHER_BACKFILL_DAYS (server-side bounded via min_close_ts) and
     upsert them into backfill_weather_markets (result = settled outcome);
  2. each cycle, take up to WEATHER_BACKFILL_MARKETS_PER_CYCLE markets that still
     lack candles (newest first), fetch hourly candlesticks over the market's final
     CAPTURE_HOURS, store them, and mark the market done.

At ~40 markets/cycle and a 5-minute cycle, 120 days of 7-city high+low history
(~10-12k markets) converges in under a day. Markets older than Kalshi's archival
cutoff fall back to the /historical candlesticks endpoint.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone

from .. import repository as repo
from ..config import Settings
from ..kalshi.errors import KalshiAPIError
from ..scanner.metrics import parse_dt, price_to_cents
from .buckets import parse_bucket_range
from .cities import CITIES

logger = logging.getLogger(__name__)

CAPTURE_HOURS = 48  # candle window before close (hourly candles -> <= ~49 rows/market)

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
_DATE_RE = re.compile(r"(\d{2})([A-Z]{3})(\d{2})")


def target_date_of(ticker: str | None) -> str | None:
    match = _DATE_RE.search(ticker or "")
    if not match:
        return None
    month = _MONTHS.get(match.group(2))
    if not month:
        return None
    try:
        return date(2000 + int(match.group(1)), month, int(match.group(3))).isoformat()
    except ValueError:
        return None


def event_ticker_of(market_ticker: str | None) -> str | None:
    """KXHIGHNY-26JUN08-B74.5 -> KXHIGHNY-26JUN08 (strip the strike token)."""
    if not market_ticker or "-" not in market_ticker:
        return market_ticker
    return market_ticker.rsplit("-", 1)[0]


def _cents(value) -> float | None:
    out = price_to_cents(value)
    return float(out) if out is not None else None


def candle_ohlc(candle: dict, base: str) -> dict[str, float | None]:
    """Tolerant OHLC extraction: nested dict (int cents / float / dollar strings,
    with or without *_dollars variants) or flat '<base>_close'-style keys."""
    node = candle.get(base)
    if node is None:
        node = candle.get(f"{base}_dollars")
    out: dict[str, float | None] = {}
    if isinstance(node, dict):
        for key in ("open", "high", "low", "close"):
            raw = node.get(key)
            if raw is None:
                raw = node.get(f"{key}_dollars")
            out[key] = _cents(raw)
        return out
    for key in ("open", "high", "low", "close"):
        raw = candle.get(f"{base}_{key}")
        if raw is None:
            raw = candle.get(f"{base}_{key}_dollars")
        out[key] = _cents(raw)
    if all(v is None for v in out.values()) and node is not None:
        out["close"] = _cents(node)  # scalar form
    return out


def _int_of(value) -> int | None:
    try:
        return int(float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def candle_rows(market_ticker: str, candles: list[dict], period_minutes: int) -> list[dict]:
    rows = []
    for c in candles:
        ts = c.get("end_period_ts") or c.get("end_ts") or c.get("ts")
        if ts is None:
            continue
        when = (
            datetime.fromtimestamp(int(ts), tz=timezone.utc)
            if isinstance(ts, (int, float, str)) and str(ts).isdigit()
            else parse_dt(str(ts))
        )
        if when is None:
            continue
        price = candle_ohlc(c, "price")
        bid = candle_ohlc(c, "yes_bid")
        ask = candle_ohlc(c, "yes_ask")
        rows.append(dict(
            market_ticker=market_ticker,
            end_period_ts=when,
            period_minutes=period_minutes,
            price_open=price["open"],
            price_high=price["high"],
            price_low=price["low"],
            price_close=price["close"],
            yes_bid_close=bid["close"],
            yes_ask_close=ask["close"],
            volume=_int_of(c.get("volume") or c.get("volume_fp")),
            open_interest=_int_of(c.get("open_interest") or c.get("open_interest_fp")),
        ))
    return rows


@dataclass
class BackfillSummary:
    markets_enumerated: int = 0
    markets_fetched: int = 0
    candles_stored: int = 0
    errors: int = 0
    pending: int = 0


class WeatherBackfill:
    def __init__(self, client, settings: Settings, *, sleep_s: float = 0.15):
        self.client = client
        self.settings = settings
        self.sleep_s = sleep_s
        self._enumerated = False

    def _series_map(self) -> dict[str, tuple[str, str]]:
        """series_ticker -> (city, kind), highs and lows (with env overrides)."""
        out: dict[str, tuple[str, str]] = {}
        for c in CITIES:
            out[c.series_high] = (c.code, "high")
            low = self.settings.weather_low_series_map.get(c.code) or c.series_low
            if low:
                out[low] = (c.code, "low")
        return out

    def run_once(self, session) -> BackfillSummary:
        summary = BackfillSummary()
        if not self._enumerated:
            self._enumerate(session, summary)
            self._enumerated = True
        self._fetch_candles(session, summary)
        summary.pending = repo.count_backfill_pending(session)
        return summary

    # -- step 1: which settled markets exist? --------------------------------------

    def _enumerate(self, session, summary: BackfillSummary) -> None:
        s = self.settings
        min_close = int(time.time()) - int(s.weather_backfill_days * 86400)
        for series, (city, kind) in self._series_map().items():
            try:
                for mk in self.client.iter_markets(
                    status="settled",
                    series_ticker=series,
                    page_size=1000,
                    min_close_ts=min_close,
                ):
                    ticker = mk.get("ticker")
                    if not ticker:
                        continue
                    subtitle = mk.get("yes_sub_title") or mk.get("subtitle")
                    low, high = parse_bucket_range(subtitle)
                    created = repo.upsert_backfill_market(
                        session,
                        market_ticker=ticker,
                        event_ticker=event_ticker_of(ticker),
                        series_ticker=series,
                        city=city,
                        kind=kind,
                        target_date=target_date_of(ticker),
                        subtitle=subtitle,
                        low_f=low,
                        high_f=high,
                        result=(mk.get("result") or "").lower() or None,
                        open_time=parse_dt(mk.get("open_time")),
                        close_time=parse_dt(mk.get("close_time")),
                    )
                    summary.markets_enumerated += int(created)
            except Exception as exc:  # noqa: BLE001 — one series must not sink the rest
                summary.errors += 1
                logger.warning(
                    "backfill: enumerate failed",
                    extra={"extra_fields": {"series": series, "error": str(exc)}},
                )

    # -- step 2: candles for a bounded chunk ----------------------------------------

    def _fetch_candles(self, session, summary: BackfillSummary) -> None:
        s = self.settings
        markets = repo.pending_backfill_markets(
            session, limit=s.weather_backfill_markets_per_cycle
        )
        for m in markets:
            close_dt = m.close_time
            if close_dt is None:
                repo.mark_backfill_fetched(session, m, candle_count=0)
                continue
            if close_dt.tzinfo is None:
                close_dt = close_dt.replace(tzinfo=timezone.utc)
            end_ts = int(close_dt.timestamp())
            start_ts = end_ts - CAPTURE_HOURS * 3600
            if m.open_time is not None:
                open_dt = m.open_time
                if open_dt.tzinfo is None:
                    open_dt = open_dt.replace(tzinfo=timezone.utc)
                start_ts = max(start_ts, int(open_dt.timestamp()))
            try:
                candles = self._candles_for(m, start_ts, end_ts)
            except Exception as exc:  # noqa: BLE001
                summary.errors += 1
                logger.warning(
                    "backfill: candles failed",
                    extra={"extra_fields": {"ticker": m.market_ticker, "error": str(exc)}},
                )
                continue  # retried next cycle
            rows = candle_rows(m.market_ticker, candles, s.weather_backfill_period_minutes)
            repo.replace_backfill_candles(session, m.market_ticker, rows)
            repo.mark_backfill_fetched(session, m, candle_count=len(rows))
            summary.markets_fetched += 1
            summary.candles_stored += len(rows)
            if self.sleep_s:
                time.sleep(self.sleep_s)

    def _candles_for(self, m, start_ts: int, end_ts: int) -> list[dict]:
        period = self.settings.weather_backfill_period_minutes
        try:
            page = self.client.get_market_candlesticks(
                m.series_ticker,
                m.market_ticker,
                start_ts=start_ts,
                end_ts=end_ts,
                period_interval=period,
            )
        except KalshiAPIError as exc:
            if exc.status_code != 404:
                raise
            # Archived beyond the live cutoff -> historical endpoint.
            page = self.client.get_historical_market_candlesticks(
                m.market_ticker,
                start_ts=start_ts,
                end_ts=end_ts,
                period_interval=period,
            )
        return page.get("candlesticks") or page.get("candles") or []
