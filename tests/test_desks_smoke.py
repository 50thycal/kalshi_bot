from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from kalshi_bot.desks.contracts import DeskError, OrderReport, Quote
from kalshi_bot.desks.smoke import run_chatgpt_smoke, smoke_ids
from kalshi_bot.desks.store import DeskStore

NOW = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)


class FakeExchange:
    subaccount = 1

    def __init__(self):
        self.order = None
        self.submissions = 0

    def find_order(self, client_order_id):
        return self.order

    def check_isolation(self):
        return {"verified": True, "subaccount": 1, "balance": "30.00"}

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
