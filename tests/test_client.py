from __future__ import annotations

import base64
import re

import pytest
import respx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from httpx import Response

from kalshi_bot.kalshi.client import KalshiClient
from kalshi_bot.kalshi.errors import AuthError


@respx.mock
def test_balance_sends_valid_signed_headers(settings, rsa_keypair):
    key, _ = rsa_keypair
    base = settings.kalshi_base_url
    route = respx.get(f"{base}/portfolio/balance").mock(
        return_value=Response(200, json={"balance": 123456})
    )
    with KalshiClient(settings) as client:
        data = client.get_balance()

    assert data["balance"] == 123456
    req = route.calls.last.request
    assert req.headers["KALSHI-ACCESS-KEY"] == "test-key-id"
    ts = req.headers["KALSHI-ACCESS-TIMESTAMP"]
    sig = base64.b64decode(req.headers["KALSHI-ACCESS-SIGNATURE"])
    key.public_key().verify(
        sig,
        f"{ts}GET/trade-api/v2/portfolio/balance".encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )


@respx.mock
def test_retries_on_5xx_then_succeeds(settings):
    base = settings.kalshi_base_url
    route = respx.get(f"{base}/exchange/status").mock(
        side_effect=[Response(503), Response(200, json={"exchange_active": True})]
    )
    with KalshiClient(settings, retry_backoffs=(0, 0, 0)) as client:
        data = client.get_exchange_status()
    assert data["exchange_active"] is True
    assert route.call_count == 2


@respx.mock
def test_auth_error_raises_immediately(settings):
    base = settings.kalshi_base_url
    route = respx.get(f"{base}/portfolio/balance").mock(return_value=Response(401))
    with KalshiClient(settings, retry_backoffs=(0, 0, 0)) as client:
        with pytest.raises(AuthError):
            client.get_balance()
    assert route.call_count == 1  # no retry on auth failure


@respx.mock
def test_get_orderbook(settings):
    base = settings.kalshi_base_url
    respx.get(re.compile(rf"{re.escape(base)}/markets/[^/]+/orderbook")).mock(
        return_value=Response(200, json={"orderbook": {"yes": [[48, 10]], "no": [[49, 10]]}})
    )
    with KalshiClient(settings) as client:
        ob = client.get_orderbook("SOME-TICKER", depth=10)
    assert ob["orderbook"]["yes"] == [[48, 10]]


def test_place_order_is_guarded_in_scanner_mode(settings):
    with KalshiClient(settings) as client:
        with pytest.raises(RuntimeError):
            client.place_order(
                ticker="X", action="buy", side="yes", type="limit", count=1, yes_price=50
            )
