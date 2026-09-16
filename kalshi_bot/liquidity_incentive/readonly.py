"""The ONLY Kalshi surface the shadow collector may touch: GET methods and the WebSocket
upgrade plumbing. Same construction as `execution.readonly.ReadOnlyKalshi` and enforced the
same way (`tests/test_liquidity_incentive_collector.py` asserts no write method exists)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..execution.readonly import WS_PATH
from ..execution.readonly import ReadOnlyKalshi as _ExecReadOnly


class IncentiveReadOnlyKalshi:
    def __init__(self, client, settings) -> None:
        self._client = client
        self._inner = _ExecReadOnly(client, settings)

    def iter_incentive_programs(self, **params: Any) -> Iterator[dict]:
        return self._client.iter_incentive_programs(**params)

    def get_market(self, ticker: str) -> dict:
        return self._client.get_market(ticker)

    def get_series(self, series_ticker: str) -> dict:
        return self._client.get_series(series_ticker)

    def get_orderbook(self, ticker: str, depth: int | None = None) -> dict:
        return self._client.get_orderbook(ticker, depth=depth)

    @property
    def ws_url(self) -> str:
        return self._inner.ws_url

    def ws_headers(self) -> dict[str, str]:
        return self._inner.ws_headers()


__all__ = ["IncentiveReadOnlyKalshi", "WS_PATH"]
