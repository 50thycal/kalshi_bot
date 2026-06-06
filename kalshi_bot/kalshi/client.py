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
    ) -> dict:
        params: dict[str, Any] = {"status": status, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        if series_ticker:
            params["series_ticker"] = series_ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
        return self._request("GET", "/markets", params=params)

    def iter_markets(
        self,
        *,
        status: str = "open",
        page_size: int = 100,
        max_markets: int | None = None,
        series_ticker: str | None = None,
        event_ticker: str | None = None,
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

    def get_orderbook(self, ticker: str, depth: int | None = None) -> dict:
        params = {"depth": depth} if depth else None
        return self._request("GET", f"/markets/{ticker}/orderbook", params=params)

    def get_positions(self, **params: Any) -> dict:
        return self._request("GET", "/portfolio/positions", params=params or None)

    def get_orders(self, **params: Any) -> dict:
        return self._request("GET", "/portfolio/orders", params=params or None)

    def get_fills(self, **params: Any) -> dict:
        return self._request("GET", "/portfolio/fills", params=params or None)

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
