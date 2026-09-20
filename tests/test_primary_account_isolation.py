"""The existing worker must not reconcile or manage the autonomous desk books."""
import json

import httpx
import pytest

from kalshi_bot.kalshi.client import KalshiClient
from kalshi_bot.kalshi.errors import AuthError


@pytest.mark.parametrize("method,collection", [
    ("get_orders", "orders"), ("get_fills", "fills"),
    ("get_positions", "market_positions"), ("get_settlements", "settlements"),
    ("get_queue_positions", "queue_positions"),
])
def test_worker_reads_only_primary_records(settings, method, collection):
    def exchange(request):
        assert request.url.params["cursor"] == "page-two"
        rows = [{"subaccount_number": 0, "order_id": "main"}]
        if request.url.params.get("subaccount") != "0":
            rows.append({"subaccount_number": 2, "order_id": "desk"})
        return httpx.Response(200, json={collection: rows})

    with KalshiClient(settings, transport=httpx.MockTransport(exchange)) as client:
        result = getattr(client, method)(cursor="page-two")
    assert [r["order_id"] for r in result[collection]] == ["main"]


@pytest.mark.parametrize("selector", [1, 2, -1, None, True, False, "0", [0, 1]])
def test_non_primary_or_ambiguous_override_never_reaches_exchange(settings, selector):
    requests = []
    with KalshiClient(settings, transport=httpx.MockTransport(requests.append)) as client:
        with pytest.raises(ValueError, match="primary subaccount"):
            client.get_orders(subaccount=selector)
    assert requests == []


def test_foreign_response_stops_reconciliation(settings):
    def exchange(request):
        assert request.url.params["subaccount"] == "0"
        return httpx.Response(200, json={"fills": [
            {"subaccount_number": 0}, {"subaccount_number": 1},
        ]})

    with KalshiClient(settings, transport=httpx.MockTransport(exchange)) as client:
        with pytest.raises(AuthError, match="non-primary"):
            client.get_fills()


def test_placement_and_cancel_scope_preserve_shard_and_caller_payload(settings):
    settings.bot_mode = "live"
    settings.kill_switch = False
    requests = []

    def exchange(request):
        requests.append(request)
        return httpx.Response(200, json={"order_id": "main-order"})

    body = {"ticker": "TEST", "side": "bid", "count": "1.00", "price": "0.25"}
    with KalshiClient(settings, transport=httpx.MockTransport(exchange)) as client:
        client.create_events_order(body)
        # Cancellation must remain available when new placements are killed.
        settings.kill_switch = True
        client.cancel_events_order("main-order", exchange_index=3)
    assert "subaccount" not in body
    assert json.loads(requests[0].content)["subaccount"] == 0
    assert requests[1].url.params["subaccount"] == "0"
    assert requests[1].url.params["exchange_index"] == "3"


@pytest.mark.parametrize("method", ["create_events_order", "place_order", "create_v1_order"])
def test_foreign_order_placement_is_refused_locally(settings, method):
    settings.bot_mode = "live"
    settings.kill_switch = False
    requests = []
    with KalshiClient(settings, transport=httpx.MockTransport(requests.append)) as client:
        with pytest.raises(ValueError, match="primary subaccount"):
            if method == "create_v1_order":
                client.create_v1_order("user", {"subaccount": 1})
            elif method == "place_order":
                client.place_order(subaccount=1)
            else:
                client.create_events_order({"subaccount": 1})
    assert requests == []


def test_market_data_is_not_given_account_parameters(settings):
    def exchange(request):
        assert "subaccount" not in request.url.params
        return httpx.Response(200, json={"market": {"ticker": "TEST"}})

    with KalshiClient(settings, transport=httpx.MockTransport(exchange)) as client:
        client.get_market("TEST")
