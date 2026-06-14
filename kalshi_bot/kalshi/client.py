"""Authenticated Kalshi REST client (httpx).

Design rules from the spec:
- All requests are signed; secrets are never logged.
- Transient errors (5xx / 429 / network) are retried with exponential backoff.
- Authentication errors (401/403) hard-fail immediately.
- Order placement is guarded: it only works when BOT_MODE=live and KILL_SWITCH is
  off. It is out of scope for the Scanner MVP and refuses otherwise.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx

from ..config import Settings
from .auth import KalshiSigner
from .errors import AuthError, KalshiAPIError, TransientError

logger = logging.getLogger(__name__)

API_PREFIX = "/trade-api/v2"
DEFAULT_RETRY_BACKOFFS = (2.0, 4.0, 8.0)


class KalshiClient:
    def __init__(
        self,
        settings: Settings,
        *,
        timeout: float = 15.0,
        retry_backoffs: tuple[float, ...] = DEFAULT_RETRY_BACKOFFS,
        transport: httpx.BaseTransport | None = None,
    ):
        self._settings = settings
        self._base_url = settings.kalshi_base_url
        self._signer = KalshiSigner(settings.kalshi_api_key_id, settings.private_key_pem)
        self._retry_backoffs = retry_backoffs
        self._client = httpx.Client(base_url=self._base_url, timeout=timeout, transport=transport)

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> KalshiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- core request ------------------------------------------------------
    def _request(
        self,
        method: str,
        suffix: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        # `sign_path` is the full path including the API prefix (Kalshi signs this).
        # `request_path` is relative to the httpx base_url, which already carries the
        # API prefix — so we must NOT repeat it or the URL ends up doubled.
        sign_path = f"{API_PREFIX}{suffix}"
        request_path = suffix.lstrip("/")
        attempts = len(self._retry_backoffs) + 1
        last_exc: Exception | None = None

        for attempt in range(attempts):
            headers = {"Accept": "application/json"}
            if auth:
                headers.update(self._signer.auth_headers(method, sign_path))
            try:
                resp = self._client.request(
                    method, request_path, params=params, json=json, headers=headers
                )
            except httpx.HTTPError as exc:
                last_exc = TransientError(f"network error: {exc}")
                logger.warning(
                    "kalshi request network error",
                    extra={"extra_fields": {"method": method, "path": sign_path, "attempt": attempt}},
                )
                self._sleep(attempt)
                continue

            status = resp.status_code
            if status in (401, 403):
                raise AuthError(f"Kalshi auth failed ({status}) on {sign_path}")
            if status == 429 or status >= 500:
                last_exc = TransientError(f"transient status {status}")
                logger.warning(
                    "kalshi transient response",
                    extra={"extra_fields": {"method": method, "path": sign_path, "status": status, "attempt": attempt}},
                )
                self._sleep(attempt)
                continue
            if status >= 400:
                raise KalshiAPIError(status, resp.text[:300], sign_path)

            logger.debug(
                "kalshi ok",
                extra={"extra_fields": {"method": method, "path": sign_path, "status": status}},
            )
            if not resp.content:
                return {}
            return resp.json()

        raise last_exc or TransientError("request failed")

    def _sleep(self, attempt: int) -> None:
        if attempt < len(self._retry_backoffs):
            time.sleep(self._retry_backoffs[attempt])

    # -- read endpoints ----------------------------------------------------
    def get_exchange_status(self) -> dict:
        return self._request("GET", "/exchange/status")

    def get_balance(self) -> dict:
        return self._request("GET", "/portfolio/balance")

    def get_markets(
        self,
        *,
        status: str = "open",
        limit: int = 100,
        cursor: str | None = None,
        series_ticker: str | None = None,
        event_ticker: str | None = None,
        min_close_ts: int | None = None,
    ) -> dict:
        params: dict[str, Any] = {"status": status, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        if series_ticker:
            params["series_ticker"] = series_ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
        if min_close_ts is not None:
            params["min_close_ts"] = min_close_ts
        return self._request("GET", "/markets", params=params)

    def iter_markets(
        self,
        *,
        status: str = "open",
        page_size: int = 100,
        max_markets: int | None = None,
        series_ticker: str | None = None,
        event_ticker: str | None = None,
        min_close_ts: int | None = None,
    ) -> Iterator[dict]:
        """Yield markets across all pages (cursor pagination), capped at max_markets."""
        fetched = 0
        cursor: str | None = None
        while True:
            page = self.get_markets(
                status=status,
                limit=page_size,
                cursor=cursor,
                series_ticker=series_ticker,
                event_ticker=event_ticker,
                min_close_ts=min_close_ts,
            )
            markets = page.get("markets") or []
            for market in markets:
                yield market
                fetched += 1
                if max_markets is not None and fetched >= max_markets:
                    return
            cursor = page.get("cursor")
            if not cursor or not markets:
                return

    def get_market(self, ticker: str) -> dict:
        return self._request("GET", f"/markets/{ticker}")

    def get_market_candlesticks(
        self,
        series_ticker: str,
        ticker: str,
        *,
        start_ts: int,
        end_ts: int,
        period_interval: int = 60,
    ) -> dict:
        """OHLC price/bid/ask candlesticks for a market (live/recent data set).
        period_interval is in minutes: 1, 60 or 1440."""
        return self._request(
            "GET",
            f"/series/{series_ticker}/markets/{ticker}/candlesticks",
            params={"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_interval},
        )

    def get_historical_market_candlesticks(
        self,
        ticker: str,
        *,
        start_ts: int,
        end_ts: int,
        period_interval: int = 60,
    ) -> dict:
        """Candlesticks for markets archived out of the live data set (older
        settlements). Same shape as get_market_candlesticks."""
        return self._request(
            "GET",
            f"/historical/markets/{ticker}/candlesticks",
            params={"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_interval},
        )

    def get_events(
        self,
        *,
        status: str = "open",
        limit: int = 200,
        cursor: str | None = None,
        with_nested_markets: bool = True,
        series_ticker: str | None = None,
    ) -> dict:
        params: dict[str, Any] = {
            "status": status,
            "limit": limit,
            "with_nested_markets": "true" if with_nested_markets else "false",
        }
        if cursor:
            params["cursor"] = cursor
        if series_ticker:
            params["series_ticker"] = series_ticker
        return self._request("GET", "/events", params=params)

    def iter_events(
        self,
        *,
        status: str = "open",
        page_size: int = 200,
        max_events: int | None = None,
        with_nested_markets: bool = True,
    ) -> Iterator[dict]:
        """Yield events across all pages (cursor pagination), capped at max_events.

        Events carry `category` and (with nested markets) a `markets` array, which is
        how we filter by category — the market object itself has no category field."""
        fetched = 0
        cursor: str | None = None
        while True:
            page = self.get_events(
                status=status,
                limit=page_size,
                cursor=cursor,
                with_nested_markets=with_nested_markets,
            )
            events = page.get("events") or []
            for event in events:
                yield event
                fetched += 1
                if max_events is not None and fetched >= max_events:
                    return
            cursor = page.get("cursor")
            if not cursor or not events:
                return

    def get_orderbook(self, ticker: str, depth: int | None = None) -> dict:
        params = {"depth": depth} if depth else None
        return self._request("GET", f"/markets/{ticker}/orderbook", params=params)

    def get_positions(self, **params: Any) -> dict:
        return self._request("GET", "/portfolio/positions", params=params or None)

    def get_orders(self, **params: Any) -> dict:
        return self._request("GET", "/portfolio/orders", params=params or None)

    def get_fills(self, **params: Any) -> dict:
        return self._request("GET", "/portfolio/fills", params=params or None)

    def get_settlements(self, **params: Any) -> dict:
        return self._request("GET", "/portfolio/settlements", params=params or None)

    # -- guarded write endpoints (out of scope for Scanner MVP) ------------
    def place_order(self, **order: Any) -> dict:
        self._ensure_live_enabled()
        return self._request("POST", "/portfolio/orders", json=order)

    def cancel_order(self, order_id: str) -> dict:
        self._ensure_live_enabled()
        return self._request("DELETE", f"/portfolio/orders/{order_id}")

    def _ensure_live_enabled(self) -> None:
        if self._settings.bot_mode != "live" or self._settings.kill_switch:
            raise RuntimeError(
                "Live order placement is disabled: requires BOT_MODE=live and "
                "KILL_SWITCH=false. This is out of scope for the scanner MVP."
            )
