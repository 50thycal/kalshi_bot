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


def test_create_events_order_is_guarded_in_scanner_mode(settings):
    with KalshiClient(settings) as client:
        with pytest.raises(RuntimeError):
            client.create_events_order({"ticker": "X", "side": "ask", "price": "0.08"})


@respx.mock
def test_create_events_order_posts_to_v2_events_endpoint(settings):
    # The current Kalshi order endpoint (replaced the deprecated /portfolio/orders).
    settings.bot_mode = "live"
    settings.kill_switch = False
    base = settings.kalshi_base_url
    route = respx.post(f"{base}/portfolio/events/orders").mock(
        return_value=Response(201, json={"order": {"order_id": "K-1", "status": "resting"}})
    )
    body = {
        "ticker": "KXTEAM-26-A", "client_order_id": "cid", "side": "ask",
        "count": "1.00", "price": "0.0800", "time_in_force": "good_till_canceled",
        "post_only": True,
    }
    with KalshiClient(settings) as client:
        resp = client.create_events_order(body)
    assert resp["order"]["order_id"] == "K-1"
    assert route.call_count == 1
    import json as _json
    assert _json.loads(route.calls.last.request.content)["side"] == "ask"


@respx.mock
def test_cancel_events_order_deletes_v2_events_path(settings):
    settings.bot_mode = "live"
    settings.kill_switch = False
    base = settings.kalshi_base_url
    route = respx.delete(f"{base}/portfolio/events/orders/K-1").mock(return_value=Response(200))
    with KalshiClient(settings) as client:
        client.cancel_events_order("K-1")
    assert route.call_count == 1


@respx.mock
def test_tier_upgrade_sends_a_json_content_type(settings):
    """Kalshi rejects the ADVANCED upgrade with `400 invalid_content_type` unless the POST
    carries a JSON content type — observed live 2026-08-12.

    The endpoint takes no parameters, so the natural call omits the body; httpx then sends no
    `Content-Type` header at all and the request is refused. An empty `{}` body is what makes
    httpx emit the header. Asserted at the transport layer because nothing above it can see the
    difference: the call looks identical either way, and the whole thing failed in production
    while every unit test passed."""
    base = settings.kalshi_base_url
    route = respx.post(f"{base}/account/api_usage_level/upgrade").mock(
        return_value=Response(201)
    )
    with KalshiClient(settings) as client:
        client.upgrade_api_usage_level()

    req = route.calls.last.request
    assert req.headers.get("content-type", "").startswith("application/json")
    assert req.content == b"{}"


@respx.mock
def test_account_limits_is_a_plain_get(settings):
    """The read path must stay a GET with no body — it is the one call that must never be able
    to mutate the account."""
    base = settings.kalshi_base_url
    route = respx.get(f"{base}/account/limits").mock(
        return_value=Response(200, json={"usage_tier": "basic"})
    )
    with KalshiClient(settings) as client:
        assert client.get_account_limits() == {"usage_tier": "basic"}

    assert route.calls.last.request.method == "GET"
    assert not route.calls.last.request.content
