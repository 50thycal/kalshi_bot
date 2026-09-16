"""The ONLY Kalshi surface the telemetry collector may touch.

Everything the collector needs is a read: queue positions, an order book, a market. It gets
this wrapper instead of the real client, so the absence of a write path is structural rather
than a matter of discipline — `tests/test_execution_telemetry.py` asserts that no method on
this class can place, amend, cancel or upgrade anything.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from ..kalshi.auth import KalshiSigner

WS_PATH = "/trade-api/ws/v2"


class ReadOnlyKalshi:
    """GET-only view of a `KalshiClient`, plus the WebSocket URL and upgrade headers."""

    def __init__(self, client, settings) -> None:
        self._client = client
        self._base_url = settings.kalshi_base_url
        self._signer = KalshiSigner(settings.kalshi_api_key_id, settings.private_key_pem)

    # -- reads -----------------------------------------------------------------------------
    def get_queue_positions(self, **params: Any) -> dict:
        return self._client.get_queue_positions(**params)

    def get_order_queue_position(self, order_id: str) -> dict:
        return self._client.get_order_queue_position(order_id)

    def get_orderbook(self, ticker: str, depth: int | None = None) -> dict:
        return self._client.get_orderbook(ticker, depth=depth)

    def get_market(self, ticker: str) -> dict:
        return self._client.get_market(ticker)

    # -- websocket plumbing (no request is made here) ------------------------------------------
    @property
    def ws_url(self) -> str:
        host = urlparse(self._base_url).netloc
        return f"wss://{host}{WS_PATH}"

    def ws_headers(self) -> dict[str, str]:
        """The same RSA-PSS headers as REST, signed over `timestamp + GET + /trade-api/ws/v2`."""
        return self._signer.auth_headers("GET", WS_PATH)
