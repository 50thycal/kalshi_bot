from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from kalshi_bot.desks.contracts import DeskError, OrderReport, Quote
from kalshi_bot.desks.exchange import ExchangeWriteHTTPError
from kalshi_bot.desks.smoke import run_chatgpt_smoke, smoke_error_id, smoke_ids
from kalshi_bot.desks.store import DeskStore

NOW = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)


class FakeExchange:
    subaccount = 1

    def __init__(self):
        self.order = None
        self.orders = {}
        self.submissions = 0
        self.balance = "30.00"

    def find_order(self, client_order_id):
        return self.orders.get(client_order_id, self.order)

    def check_isolation(self):
        return {"verified": True, "subaccount": 1, "balance": self.balance}

    def check_clean_book(self):
        return {"clean": True, "subaccount": 1}

    def quote(self, ticker, side):
        return Quote(
            ticker=ticker,
            event_id="EVENT",
            side=side,
            ask="0.40",
            available_quantity=2,
            fetched_at=NOW,
            closes_at=NOW + timedelta(hours=1),
            rules_sha256="a" * 64,
            fee_rate="0.07",
        )

    def submit_ioc(self, client_order_id, ticker, side, quantity, limit_price):
        self.submissions += 1
        assert quantity == 1 and limit_price == Decimal("0.01")
        return OrderReport(
            client_order_id=client_order_id,
            order_id="smoke-order",
            status="terminal",
            observed_at=NOW,
        )


@pytest.fixture
def smoke_case(tmp_path):
    store = DeskStore(f"sqlite:///{tmp_path / 'smoke.db'}")
    store.initialize("round-1", NOW)
    store.ready("chatgpt", NOW)
    return store, FakeExchange()


def test_preview_is_read_only(smoke_case):
    store, exchange = smoke_case
    result = run_chatgpt_smoke(store, exchange, "TEST", "yes", now=NOW)
    assert result["mode"] == "preview"
    assert result["limit_price"] == "0.01"
    assert exchange.submissions == 0
    assert store.snapshot(NOW)["publications"] == []


def test_execute_is_one_shot_and_durably_recorded(smoke_case):
    store, exchange = smoke_case
    first = run_chatgpt_smoke(store, exchange, "TEST", "yes", execute=True, now=NOW)
    second = run_chatgpt_smoke(store, exchange, "TEST", "yes", execute=True, now=NOW)
    assert first == second
    assert first["report"]["status"] == "terminal"
    assert exchange.submissions == 1
    assert [row["kind"] for row in store.snapshot(NOW)["publications"]] == [
        "live_smoke_claim",
        "live_smoke_result",
    ]


def test_claim_without_visible_order_blocks_resubmission(smoke_case):
    store, exchange = smoke_case
    _, claim_id, _ = smoke_ids(1)
    store.publish("chatgpt", "live_smoke_claim", {"claimed": True}, NOW, record_id=claim_id)
    with pytest.raises(DeskError, match="smoke_submission_ambiguous"):
        run_chatgpt_smoke(store, exchange, "TEST", "yes", execute=True, now=NOW)
    assert exchange.submissions == 0


def test_existing_order_is_reconciled_without_new_post(smoke_case):
    store, exchange = smoke_case
    client_id, _, _ = smoke_ids(1)
    report = OrderReport(
        client_order_id=client_id,
        order_id="existing",
        status="terminal",
        observed_at=NOW,
    )
    exchange.order = ("OLD-TICKER", report)
    result = run_chatgpt_smoke(store, exchange, "NEW-TICKER", "no", execute=True, now=NOW)
    assert result["mode"] == "recovered"
    assert result["ticker"] == "OLD-TICKER"
    assert exchange.submissions == 0


def test_pending_recovery_is_not_frozen_as_final_result(smoke_case):
    store, exchange = smoke_case
    client_id, _, result_id = smoke_ids(1)
    exchange.order = (
        "OLD-TICKER",
        OrderReport(
            client_order_id=client_id,
            order_id="existing",
            status="pending",
            observed_at=NOW,
        ),
    )
    result = run_chatgpt_smoke(store, exchange, "OLD-TICKER", "yes", execute=True, now=NOW)
    assert result["report"]["status"] == "pending"
    assert result_id not in {row["record_id"] for row in store.snapshot(NOW)["publications"]}


def test_common_start_and_small_price_gap_block_smoke(smoke_case):
    store, exchange = smoke_case
    store.ready("claude", NOW)
    store.start_round("round-1", NOW)
    with pytest.raises(DeskError, match="smoke_after_common_start_forbidden"):
        run_chatgpt_smoke(store, exchange, "TEST", "yes", now=NOW)

    other = DeskStore(f"sqlite:///{store.engine.url.database}.other")
    other.initialize("round-2", NOW)
    other.ready("chatgpt", NOW)
    original = exchange.quote
    exchange.quote = lambda *args: original(*args).model_copy(update={"ask": Decimal("0.02")})
    with pytest.raises(DeskError, match="smoke_price_gap_too_small"):
        run_chatgpt_smoke(other, exchange, "TEST", "yes", now=NOW)


def _claim_v1(store, when=NOW - timedelta(minutes=10)):
    _, claim_id, _ = smoke_ids(1, 1)
    store.publish(
        "chatgpt",
        "live_smoke_claim",
        {"protocol": "chatgpt-live-smoke-v1", "mode": "claimed"},
        when,
        record_id=claim_id,
    )


def test_v2_recovery_preview_preserves_v1_and_is_read_only(smoke_case):
    store, exchange = smoke_case
    _claim_v1(store)
    result = run_chatgpt_smoke(
        store, exchange, "TEST", "yes", recovery_v2=True, now=NOW
    )
    assert result["protocol"] == "chatgpt-live-smoke-v2"
    assert result["mode"] == "preview"
    assert result["supersedes"]["protocol"] == "chatgpt-live-smoke-v1"
    assert exchange.submissions == 0
    assert [row["kind"] for row in store.snapshot(NOW)["publications"]] == [
        "live_smoke_claim"
    ]


def test_v2_recovery_is_one_shot_with_distinct_order_id(smoke_case):
    store, exchange = smoke_case
    _claim_v1(store)
    result = run_chatgpt_smoke(
        store, exchange, "TEST", "yes", execute=True, recovery_v2=True, now=NOW
    )
    again = run_chatgpt_smoke(
        store, exchange, "TEST", "yes", execute=True, recovery_v2=True, now=NOW
    )
    assert result == again
    assert result["client_order_id"] == smoke_ids(1, 2)[0]
    assert result["client_order_id"] != smoke_ids(1, 1)[0]
    assert exchange.submissions == 1


def test_v2_recovery_refuses_without_old_claim_or_unchanged_balance(smoke_case):
    store, exchange = smoke_case
    with pytest.raises(DeskError, match="smoke_v2_requires_v1_claim"):
        run_chatgpt_smoke(store, exchange, "TEST", "yes", recovery_v2=True, now=NOW)
    _claim_v1(store)
    exchange.balance = "31.00"
    with pytest.raises(DeskError, match="smoke_v2_requires_unchanged_balance"):
        run_chatgpt_smoke(store, exchange, "TEST", "yes", recovery_v2=True, now=NOW)
    assert exchange.submissions == 0


def test_v2_recovery_waits_and_recovers_old_order_instead(smoke_case):
    store, exchange = smoke_case
    _claim_v1(store, NOW - timedelta(minutes=1))
    with pytest.raises(DeskError, match="smoke_v1_recovery_delay_active"):
        run_chatgpt_smoke(store, exchange, "TEST", "yes", recovery_v2=True, now=NOW)

    old_client_id = smoke_ids(1, 1)[0]
    exchange.orders[old_client_id] = (
        "OLD",
        OrderReport(
            client_order_id=old_client_id,
            order_id="old-order",
            status="terminal",
            observed_at=NOW,
        ),
    )
    recovered = run_chatgpt_smoke(
        store, exchange, "TEST", "yes", recovery_v2=True, now=NOW
    )
    assert recovered["protocol"] == "chatgpt-live-smoke-v1"
    assert recovered["mode"] == "recovered"
    assert exchange.submissions == 0


def test_write_http_error_is_durably_classified(smoke_case):
    store, exchange = smoke_case

    def fail(*args):
        exchange.submissions += 1
        request = httpx.Request("POST", "https://example.com")
        response = httpx.Response(
            422,
            json={"code": "bad_order", "message": "rejected"},
            request=request,
        )
        error = httpx.HTTPStatusError("rejected", request=request, response=response)
        raise ExchangeWriteHTTPError("submit", error)

    exchange.submit_ioc = fail
    with pytest.raises(ExchangeWriteHTTPError):
        run_chatgpt_smoke(store, exchange, "TEST", "yes", execute=True, now=NOW)
    rows = {row["record_id"]: row for row in store.snapshot(NOW)["publications"]}
    error = rows[smoke_error_id(1, 1)]["payload"]
    assert error["stage"] == "submit" and error["http_status"] == 422


def test_store_normalizes_railway_postgresql_url():
    store = DeskStore("postgresql://user:password@localhost/example")
    assert store.engine.url.drivername == "postgresql+psycopg"
