"""Capture Kalshi's settled market history for the mmsell regime series — FORWARD, forever.

THE PROBLEM THIS EXISTS FOR. Kalshi serves only a rolling **~70-day** window of settled markets.
Measured 2026-08-03: paging any series to *cursor exhaustion* (not a page cap) bottoms out on the
same date regardless of series size (KXNHLGAME 22 rows and KXMLBGAME 1,798 rows both stop at
2026-05-25); `KXNFLGAME` returns **zero** rows; `status=finalized`/`closed` return nothing; and
`min_close_ts`/`max_close_ts` cannot reach behind the wall. Authentication does not help — our own
`backfill_weather_markets` spans 119 days only because it has been *accumulating* for 53 of them.

So last season's NFL/NBA/NHL and the 2024 election ladders cannot be bought back at any price, and
`scripts/mmsell_regime_backtest.py` is bounded to whatever is inside the window on the day it
runs. **Every day without capture is a day of history permanently lost.** Started now, NFL week 1
settles in early September and the NFL regime is measurable at real n by October; left another
season, it is gone again. See `docs/MMSELL_SEASONAL_FORECAST.md`.

HOW IT RUNS. Same shape as `kalshi_bot/weather/backfill.py` — inside the worker (the only place
with Kalshi credentials and a read-write DB), as a bounded chunk per cycle so it never competes
with trading for API budget:

  1. every `MMSELL_HISTORY_ENUMERATE_MINUTES`, enumerate settled markets for each configured
     series and INSERT the unseen ones (insert-only: re-enumeration overlaps by design, and
     rewriting a row would reset its candles_fetched flag and re-fetch forever);
  2. every cycle, take up to `MMSELL_HISTORY_MARKETS_PER_CYCLE` markets that still lack candles,
     NEWEST first, and store their path over the final `MMSELL_HISTORY_CAPTURE_HOURS`.

The one difference from the weather backfill: that one reaches back once and finishes
(`_enumerated` latches). This one must re-enumerate forever, because the window it is reading
keeps sliding forward and away.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import timezone

from .. import repository as repo
from ..config import Settings
from ..kalshi.errors import KalshiAPIError
from ..scanner.metrics import parse_dt
from ..weather.backfill import candle_rows
from .regimes import event_ticker_of, regime_of

logger = logging.getLogger(__name__)


@dataclass
class RegimeCaptureSummary:
    series_enumerated: int = 0
    markets_enumerated: int = 0     # NEW rows only — re-seen markets are not counted
    markets_fetched: int = 0
    candles_stored: int = 0
    errors: int = 0
    pending: int = 0
    enumerated_this_cycle: bool = False


class RegimeHistoryCapture:
    def __init__(self, client, settings: Settings, *, sleep_s: float = 0.15):
        self.client = client
        self.settings = settings
        self.sleep_s = sleep_s
        # Monotonic timestamp of the last enumeration, or None for "never".
        # NOT 0.0: time.monotonic()'s zero point is arbitrary and is small early in a process's
        # life, so `monotonic() - 0.0 >= interval` is FALSE on a freshly started worker — which
        # would silently stall the first capture for a whole interval after every deploy.
        self._last_enumerate: float | None = None

    # -- step 1: which settled markets exist right now? -----------------------------

    def _due_to_enumerate(self) -> bool:
        if self._last_enumerate is None:
            return True
        interval = max(1.0, float(self.settings.mmsell_history_enumerate_minutes)) * 60.0
        return (time.monotonic() - self._last_enumerate) >= interval

    def _enumerate(self, session, summary: RegimeCaptureSummary) -> None:
        s = self.settings
        for series in s.mmsell_history_series_list:
            summary.series_enumerated += 1
            try:
                for mk in self.client.iter_markets(
                    status="settled",
                    series_ticker=series,
                    page_size=1000,
                    max_markets=s.mmsell_history_max_markets_per_series,
                ):
                    ticker = mk.get("ticker")
                    result = (mk.get("result") or "").lower()
                    if not ticker or result not in ("yes", "no"):
                        continue        # void/unsettled rows cannot label a backtest
                    volume = _num(mk.get("volume") or mk.get("volume_fp"))
                    if volume < s.mmsell_history_min_volume:
                        continue        # thin markets the book would never have entered
                    created = repo.upsert_regime_market(
                        session,
                        market_ticker=ticker,
                        event_ticker=event_ticker_of(ticker),
                        series_ticker=series,
                        regime=regime_of(series),
                        title=(mk.get("title") or None),
                        result=result,
                        volume=volume,
                        open_interest=_num(mk.get("open_interest") or mk.get("open_interest_fp")),
                        open_time=parse_dt(mk.get("open_time")),
                        close_time=parse_dt(mk.get("close_time")),
                    )
                    summary.markets_enumerated += int(created)
            except Exception as exc:  # noqa: BLE001 — one series must not sink the rest
                summary.errors += 1
                logger.warning(
                    "regime capture: enumerate failed",
                    extra={"extra_fields": {"series": series, "error": str(exc)}},
                )
            if self.sleep_s:
                time.sleep(self.sleep_s)

    # -- step 2: candles for a bounded chunk ----------------------------------------

    def _fetch_candles(self, session, summary: RegimeCaptureSummary) -> None:
        s = self.settings
        for mkt in repo.pending_regime_markets(
            session, limit=s.mmsell_history_markets_per_cycle
        ):
            close_dt = mkt.close_time
            if close_dt is None:
                repo.mark_regime_fetched(session, mkt, candle_count=0)
                continue
            if close_dt.tzinfo is None:
                close_dt = close_dt.replace(tzinfo=timezone.utc)
            end_ts = int(close_dt.timestamp())
            start_ts = end_ts - int(s.mmsell_history_capture_hours * 3600)
            if mkt.open_time is not None:
                open_dt = mkt.open_time
                if open_dt.tzinfo is None:
                    open_dt = open_dt.replace(tzinfo=timezone.utc)
                start_ts = max(start_ts, int(open_dt.timestamp()))
            try:
                candles = self._candles_for(mkt, start_ts, end_ts)
            except Exception as exc:  # noqa: BLE001
                summary.errors += 1
                logger.warning(
                    "regime capture: candles failed",
                    extra={"extra_fields": {"ticker": mkt.market_ticker, "error": str(exc)}},
                )
                continue  # left pending, retried next cycle
            rows = candle_rows(
                mkt.market_ticker, candles, s.mmsell_history_period_minutes
            )
            repo.replace_regime_candles(session, mkt.market_ticker, rows)
            # Marked done even at zero rows: a market whose path Kalshi no longer serves must not
            # wedge the queue ahead of fresh settlements that are still fetchable.
            repo.mark_regime_fetched(session, mkt, candle_count=len(rows))
            summary.markets_fetched += 1
            summary.candles_stored += len(rows)
            if self.sleep_s:
                time.sleep(self.sleep_s)

    def _candles_for(self, mkt, start_ts: int, end_ts: int) -> list[dict]:
        period = self.settings.mmsell_history_period_minutes
        try:
            page = self.client.get_market_candlesticks(
                mkt.series_ticker,
                mkt.market_ticker,
                start_ts=start_ts,
                end_ts=end_ts,
                period_interval=period,
            )
        except KalshiAPIError as exc:
            if exc.status_code != 404:
                raise
            # Archived beyond the live cutoff -> the historical endpoint, same as the weather
            # backfill. This is the path that matters most as a market ages toward the wall.
            page = self.client.get_historical_market_candlesticks(
                mkt.market_ticker,
                start_ts=start_ts,
                end_ts=end_ts,
                period_interval=period,
            )
        return page.get("candlesticks") or page.get("candles") or []

    # -- entry point ----------------------------------------------------------------

    def run_once(self, session) -> RegimeCaptureSummary:
        summary = RegimeCaptureSummary()
        if self._due_to_enumerate():
            self._last_enumerate = time.monotonic()
            summary.enumerated_this_cycle = True
            self._enumerate(session, summary)
        self._fetch_candles(session, summary)
        summary.pending = repo.count_regime_pending(session)
        return summary


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
