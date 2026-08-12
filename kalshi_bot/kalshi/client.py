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
        # Cumulative count of transient (retryable) responses by HTTP status, since process
        # start; key 0 is a network-level failure with no status. A 429 (rate limit) and a 502
        # (Kalshi hiccup) are the same event to the retry loop but MEAN opposite things — the
        # first says we are over Kalshi's token bucket and must scan less or slower, the second
        # says nothing about us. They were indistinguishable in production because both logged
        # the same message and Railway's log endpoint drops structured fields, so the status
        # never survived. The client only COUNTS; a caller that owns a DB session persists the
        # snapshot (see MmSellTracker._record_scan_telemetry) — the HTTP client stays I/O-pure.
        self._transient_counts: dict[int, int] = {}

    # -- diagnostics -------------------------------------------------------
    def transient_counts(self) -> dict[int, int]:
        """Snapshot of retryable responses by HTTP status since process start (0 = network
        error). Cumulative on purpose: consecutive telemetry rows subtract to a per-cycle rate,
        and a counter that resets loses every event between reads."""
        return dict(self._transient_counts)

    def _count_transient(self, status: int) -> None:
        self._transient_counts[status] = self._transient_counts.get(status, 0) + 1

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
        return self._send(method, suffix.lstrip("/"), sign_path, params=params, json=json, auth=auth)

    def _request_v1(
        self, method: str, path: str, *, json: dict | None = None, auth: bool = True
    ) -> dict[str, Any]:
        """Call a v1 endpoint (e.g. /v1/users/{id}/orders), which lives OUTSIDE the
        /trade-api/v2 prefix. We sign the full v1 path and hit it at the host root, reusing
        the same RSA API-key auth. (The web app uses these v1 endpoints to close range-bucket
        markets that the v2 /portfolio/orders endpoint rejects.)"""
        from urllib.parse import urlsplit

        parts = urlsplit(self._base_url)
        url = f"{parts.scheme}://{parts.netloc}{path}"
        return self._send(method, url, path, json=json, auth=auth)

    def _send(
        self,
        method: str,
        url: str,
        sign_path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        attempts = len(self._retry_backoffs) + 1
        last_exc: Exception | None = None

        for attempt in range(attempts):
            headers = {"Accept": "application/json"}
            if auth:
                headers.update(self._signer.auth_headers(method, sign_path))
            try:
                resp = self._client.request(
                    method, url, params=params, json=json, headers=headers
                )
            except httpx.HTTPError as exc:
                last_exc = TransientError(f"network error: {exc}")
                self._count_transient(0)
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
                self._count_transient(status)
                # The status is in the MESSAGE, not only in extra_fields: Railway's log endpoint
                # returns the message text and drops the structured fields, so a 429 was
                # indistinguishable from a 502 in production. This makes rate limiting greppable
                # with {"type":"logs","filter":"transient response 429"}.
                logger.warning(
                    f"kalshi transient response {status}",
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

    def get_account_limits(self) -> dict:
        """This account's API tier and its rate limits — the authoritative answer to a question
        we have otherwise been guessing at.

        Kalshi meters reads and writes from separate token buckets (default 10 tokens per
        request), and the tier decides their size: Basic 200/100 tokens/sec -> 20 reads/sec,
        Advanced 300/300 -> 30/sec. The mmsell entry scan bursts at roughly 6-25 requests/sec,
        so which side of 20 we are on determines whether the scan is inside its budget or
        eating 429 backoffs — and until now nothing in the system knew. Requires auth, which is
        why neither the sandbox nor the ops runner can answer it and the worker must.

        Read-only: reports the tier, never changes it. Upgrading is the separate POST below,
        which no code path reaches unless an operator sets KALSHI_UPGRADE_API_TIER."""
        return self._request("GET", "/account/limits")

    def upgrade_api_usage_level(self) -> dict:
        """Request the permanent ADVANCED API usage-level grant. Writes to the account.

        Measured 2026-08-12, we are on `basic`: read 200 tokens/sec (20 requests/sec at the
        default 10 tokens each), write 100/sec, `grants: []`. Advanced is 300/300 — reads +50%,
        writes 3x — and it is free and self-service; the only requirement is that at least one
        of the last 100 orders was placed via the API, which the live mmsell run satisfies. That
        matters because the mmsell scan bursts at ~6-25 requests/sec against the 20/sec ceiling
        and books hundreds of 429s a day, each costing a 2s backoff and, on final retry
        failure, silently dropping that market's candidates.

        This is the ONLY method on this client that changes account state rather than reading
        it, so it is deliberately awkward to reach: nothing calls it unless the operator sets
        `KALSHI_UPGRADE_API_TIER`, and main._maybe_upgrade_api_tier refuses to fire it when the
        account is already above basic. Kalshi returns 201 with no body; the caller must re-read
        get_account_limits() to see the resulting grant, which is also what makes the operation
        verifiable rather than merely attempted."""
        return self._request("POST", "/account/api_usage_level/upgrade")

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

    def get_market_trades(
        self,
        *,
        ticker: str,
        limit: int = 1000,
        cursor: str | None = None,
        min_ts: int | None = None,
        max_ts: int | None = None,
    ) -> dict:
        """Public trade tape for a market, newest first ({trades: [...], cursor}).
        min_ts/max_ts are unix seconds; the XGAME collector uses min_ts to fetch
        only the gap since its stored high-water mark."""
        params: dict[str, Any] = {"ticker": ticker, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        if min_ts is not None:
            params["min_ts"] = min_ts
        if max_ts is not None:
            params["max_ts"] = max_ts
        return self._request("GET", "/markets/trades", params=params)

    def get_positions(self, **params: Any) -> dict:
        return self._request("GET", "/portfolio/positions", params=params or None)

    def get_orders(self, **params: Any) -> dict:
        return self._request("GET", "/portfolio/orders", params=params or None)

    def get_fills(self, **params: Any) -> dict:
        return self._request("GET", "/portfolio/fills", params=params or None)

    def get_settlements(self, **params: Any) -> dict:
        return self._request("GET", "/portfolio/settlements", params=params or None)

    # -- guarded write endpoints (out of scope for Scanner MVP) ------------
    # LEGACY: Kalshi deprecated /portfolio/orders (2026-07) — a POST now returns
    # 410 deprecated_v1_order_endpoint. These remain only for the not-yet-migrated
    # weather live path (inert). New order flow uses create_events_order below.
    def place_order(self, **order: Any) -> dict:
        self._ensure_live_enabled()
        return self._request("POST", "/portfolio/orders", json=order)

    def cancel_order(self, order_id: str) -> dict:
        self._ensure_live_enabled()
        return self._request("DELETE", f"/portfolio/orders/{order_id}")

    def create_events_order(self, order: dict[str, Any]) -> dict:
        """Place an order via Kalshi's current V2 endpoint (POST /portfolio/events/orders),
        which replaced the deprecated /portfolio/orders. Body shape (verified against recorded
        live requests): ticker, client_order_id (UUID), side ("bid"=buy YES / "ask"=sell YES),
        count and price as DECIMAL STRINGS (numeric types are rejected 400), price in YES-side
        dollars, time_in_force, optional post_only / self_trade_prevention_type."""
        self._ensure_live_enabled()
        return self._request("POST", "/portfolio/events/orders", json=order)

    def cancel_events_order(self, order_id: str) -> dict:
        """Cancel a V2 order (DELETE /portfolio/events/orders/{order_id}) — the partner of
        create_events_order."""
        self._ensure_live_enabled()
        return self._request("DELETE", f"/portfolio/events/orders/{order_id}")

    def create_v1_order(self, user_id: str, order: dict[str, Any]) -> dict:
        """Place an order via the v1 user-scoped endpoint — the path the web app uses to CLOSE
        range-bucket markets (the v2 endpoint rejects those closes). `order` is the v1 body
        (market_id, user_side, side, order_action, order_type, count_fp, price_dollars,
        sell_position_capped, ...)."""
        self._ensure_live_enabled()
        return self._request_v1("POST", f"/v1/users/{user_id}/orders", json=order)

    def get_v1_event(self, series_ticker: str, event_ticker: str) -> dict:
        """v1 event object with nested markets — each nested market carries the UUID
        (`id`) that the v1 order endpoint requires and the v2 objects don't expose."""
        return self._request_v1(
            "GET", f"/v1/series/{series_ticker}/events/{event_ticker}",
            json=None,
        )

    def _ensure_live_enabled(self) -> None:
        if self._settings.bot_mode != "live" or self._settings.kill_switch:
            raise RuntimeError(
                "Live order placement is disabled: requires BOT_MODE=live and "
                "KILL_SWITCH=false. This is out of scope for the scanner MVP."
            )
