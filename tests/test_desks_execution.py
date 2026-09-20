from datetime import timedelta
from decimal import Decimal as D
from uuid import uuid4

import httpx
import pytest

from kalshi_bot.desks.contracts import Decision, DeskError, OrderReport, Quote, utcnow
from kalshi_bot.desks.exchange import KalshiDeskExchange
from kalshi_bot.desks.execution import DeskExecutor, conservative_cost
from kalshi_bot.desks.store import DeskStore


@pytest.fixture
def desk_case(tmp_path):
    now = utcnow()
    store = DeskStore(f"sqlite:///{tmp_path / 'desk.db'}")
    store.initialize("test", now - timedelta(minutes=1))
    for desk in ("chatgpt", "claude"):
        store.ready(desk, now)
    store.start_round("test", now - timedelta(seconds=1))
    decision = Decision(
        decision_id="decision-1234",
        desk_id="chatgpt",
        round_id="test",
        ticker="TEST-YES",
        event_id="TEST",
        side="yes",
        observed_price="0.40",
        quote_at=now,
        max_price="0.42",
        probability="0.80",
        probability_low="0.70",
        probability_high="0.90",
        expected_net_profit="0.50",
        settlement_source="https://example.com/source",
        settlement_rule="Official published yes outcome",
        rules_sha256="a" * 64,
        thesis="The evidence implies a high probability",
        counterargument="The source may be wrong",
        invalidation="Source changes",
        edge_class="information",
        evidence=[
            dict(
                url="https://example.com/source",
                retrieved_at=now,
                excerpt="Official data",
                sha256="b" * 64,
            )
        ],
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        author_model="test",
    )
    quote = Quote(
        ticker=decision.ticker,
        event_id=decision.event_id,
        side="yes",
        ask="0.40",
        available_quantity=10,
        fetched_at=now,
        closes_at=now + timedelta(days=1),
        rules_sha256=decision.rules_sha256,
        fee_rate="0.07",
    )

    class Fake:
        subaccount = 1
        calls = 0

        def quote(self, *args):
            return quote

        def submit_ioc(self, client_id, *args):
            self.calls += 1
            return OrderReport(
                client_order_id=client_id,
                order_id="order-1",
                status="terminal",
                filled_quantity="0.25",
                fill_cost="0.10",
                fees="0.0043",
                observed_at=now,
            )

        def reconcile(self, client_id, *args):
            return OrderReport(client_order_id=client_id, status="unknown", observed_at=now)

    exchange = Fake()
    executor = DeskExecutor(
        store,
        exchange,
        "chatgpt",
        live_enabled=True,
        isolation_verified=True,
        existing_workers_isolated=True,
    )
    return store, decision, quote, exchange, executor, now


def test_submit_fractional_fill_exact_accounting_and_no_duplicate(desk_case):
    store, decision, quote, exchange, executor, now = desk_case
    result = executor.submit(decision, now)
    assert D(result["filled_quantity"]) == D(".25")
    assert D(result["fees"]) == D(".0043")
    assert D(store.snapshot(now)["desks"][0]["cash"]) == D("29.8957")
    assert executor.submit(decision, now)["order_id"] == "order-1"
    assert exchange.calls == 1


def test_unknown_submission_reserves_and_pauses_without_retry(desk_case):
    store, decision, quote, exchange, executor, now = desk_case

    def fail(*args):
        exchange.calls += 1
        raise TimeoutError("ambiguous")

    exchange.submit_ioc = fail
    result = executor.submit(decision, now)
    assert result["status"] == "unknown"
    executor.submit(decision, now)
    executor.reconcile(now=now)
    assert exchange.calls == 1
    desk = store.snapshot(now)["desks"][0]
    assert desk["paused"]
    assert D(desk["committed"]) > 0


@pytest.mark.parametrize(
    "change,code",
    [
        ({"ask": D("0.43")}, "price_cap_exceeded"),
        ({"rules_sha256": "c" * 64}, "settlement_rules_changed"),
        ({"event_id": "other"}, "quote_identity_mismatch"),
        ({"status": "closed"}, "market_not_open"),
    ],
)
def test_quote_rejections_never_reserve(desk_case, change, code):
    store, decision, quote, exchange, executor, now = desk_case
    exchange.quote = lambda *args: quote.model_copy(update=change)
    with pytest.raises(DeskError, match=code):
        executor.submit(decision, now)
    assert not store.snapshot(now)["decisions"]
    assert exchange.calls == 0


def test_stale_and_low_edge_rejected(desk_case):
    store, decision, quote, exchange, executor, now = desk_case
    with pytest.raises(DeskError, match="stale_or_future_quote"):
        executor.submit(decision, now + timedelta(seconds=61))
    weak = decision.model_copy(update={"probability_low": D("0.41")})
    with pytest.raises(DeskError, match="insufficient_conservative_edge"):
        executor.submit(weak, now)
    assert not store.snapshot(now)["decisions"]


def test_disabled_or_unverified_executor_never_calls_exchange(desk_case):
    store, decision, quote, exchange, executor, now = desk_case
    executor.existing_workers_isolated = False
    with pytest.raises(DeskError, match="desk_isolation_unverified"):
        executor.submit(decision, now)
    assert exchange.calls == 0


def test_fee_bound_covers_curve_peak_and_fractional_rounding():
    assert conservative_cost(1, D(".80"), D(".07")) == D(".83")
    assert conservative_cost(2, D(".42"), D(".07")) == D(".90")


def adapter(rsa_keypair, handler):
    return KalshiDeskExchange(
        "https://example.com/trade-api/v2",
        "test",
        rsa_keypair[1],
        1,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_no_ioc_mapping_subaccount_and_single_post(rsa_keypair):
    calls = []
    client_id = str(uuid4())

    def handler(request):
        import json

        calls.append(request)
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["side"] == "ask" and body["price"] == "0.6000"
            assert body["subaccount"] == 1 and body["exchange_index"] == -1
            assert body["time_in_force"] == "immediate_or_cancel"
            assert "expiration_time" not in body
            return httpx.Response(201, json={"order_id": "one", "client_order_id": client_id})
        assert request.url.params["subaccount"] == "1"
        if request.url.path.endswith("/fills"):
            return httpx.Response(
                200,
                json={
                    "fills": [
                        {
                            "fill_id": "f1",
                            "order_id": "one",
                            "ticker": "TEST",
                            "outcome_side": "no",
                            "subaccount_number": 1,
                            "count_fp": ".25",
                            "yes_price_dollars": ".60",
                            "no_price_dollars": ".40",
                            "fee_cost": ".0043",
                        }
                    ],
                    "cursor": "",
                },
            )
        return httpx.Response(
            200,
            json={
                "orders": [
                    {
                        "order_id": "one",
                        "client_order_id": client_id,
                        "subaccount_number": 1,
                        "outcome_side": "no",
                        "ticker": "TEST",
                        "status": "canceled",
                        "fill_count_fp": "0.25",
                        "remaining_count_fp": "0.00",
                        "taker_fill_cost_dollars": "0.1000",
                        "maker_fill_cost_dollars": "0",
                        "taker_fees_dollars": "0.0043",
                        "maker_fees_dollars": "0",
                    }
                ],
                "cursor": "",
            },
        )

    result = adapter(rsa_keypair, handler).submit_ioc(client_id, "TEST", "no", 1, D(".40"))
    assert result.status == "terminal" and result.filled_quantity == D(".25")
    assert sum(r.method == "POST" for r in calls) == 1


def test_post_timeout_never_retried(rsa_keypair):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("timeout")

    with pytest.raises(httpx.ReadTimeout):
        adapter(rsa_keypair, handler).submit_ioc(str(uuid4()), "TEST", "yes", 1, D(".4"))
    assert len(calls) == 1


def test_absent_order_stays_unknown(rsa_keypair):
    exchange = adapter(
        rsa_keypair, lambda req: httpx.Response(200, json={"orders": [], "cursor": ""})
    )
    assert exchange.reconcile(str(uuid4()), "TEST").status == "unknown"


def test_isolation_requires_denied_other_account(rsa_keypair):
    exchange = adapter(rsa_keypair, lambda req: httpx.Response(200, json={"balance": 3000}))
    with pytest.raises(DeskError, match="unrestricted_exchange_key"):
        exchange.check_isolation()

    def restricted(req):
        return (
            httpx.Response(200, json={"balance": 3000})
            if req.url.params["subaccount"] == "1"
            else httpx.Response(403)
        )

    assert adapter(rsa_keypair, restricted).check_isolation()["verified"]


def test_quote_reads_current_rules_and_event_fee_override(rsa_keypair):
    from kalshi_bot.desks.exchange import rules_hash

    now = utcnow()
    market = {
        "ticker": "TEST",
        "event_ticker": "EVENT",
        "market_type": "binary",
        "notional_value_dollars": "1",
        "rules_primary": "Official event outcome",
        "status": "active",
        "close_time": (now + timedelta(days=1)).isoformat(),
        "yes_ask_dollars": ".40",
        "no_ask_dollars": ".61",
        "yes_ask_size_fp": "2.50",
        "yes_bid_size_fp": "1.50",
        "price_ranges": [{"start": "0", "end": "1", "step": ".01"}],
    }

    def handler(req):
        suffix = req.url.path.split("/v2")[-1]
        data = {
            "/markets/TEST": {"market": market},
            "/events/EVENT": {"event": {"series_ticker": "SERIES"}},
            "/series/SERIES": {"series": {"fee_type": "quadratic", "fee_multiplier": "1"}},
            "/events/fee_changes": {
                "event_fee_changes": [
                    {
                        "event_ticker": "EVENT",
                        "scheduled_ts": (now - timedelta(days=1)).isoformat(),
                        "fee_type_override": "quadratic",
                        "fee_multiplier_override": "2",
                    }
                ],
                "cursor": "",
            },
        }[suffix]
        return httpx.Response(200, json=data)

    quote = adapter(rsa_keypair, handler).quote("TEST", "no")
    assert quote.fee_rate == D(".14")
    assert quote.ask == D(".61") and quote.available_quantity == 1
    assert quote.rules_sha256 == rules_hash(market)


def test_paginated_reconciliation_never_scans_another_subaccount(rsa_keypair):
    client_id = str(uuid4())

    def handler(req):
        assert req.url.params["subaccount"] == "1"
        if "cursor" not in req.url.params:
            return httpx.Response(200, json={"orders": [], "cursor": "page2"})
        return httpx.Response(
            200,
            json={
                "orders": [
                    {
                        "client_order_id": client_id,
                        "order_id": "one",
                        "subaccount_number": 1,
                        "ticker": "TEST",
                        "status": "canceled",
                        "fill_count_fp": "0.00",
                        "remaining_count_fp": "0.00",
                    }
                ],
                "cursor": "",
            },
        )

    assert adapter(rsa_keypair, handler).reconcile(client_id, "TEST").status == "terminal"


def test_clean_book_rejects_existing_positions(rsa_keypair):
    def handler(req):
        assert req.url.params["subaccount"] == "1"
        return httpx.Response(
            200, json={"market_positions": [{"position_fp": "0.01"}], "cursor": ""}
        )

    with pytest.raises(DeskError, match="subaccount_has_existing_positions"):
        adapter(rsa_keypair, handler).check_clean_book()


@pytest.mark.parametrize("seconds,expired", [(121, False), (10, True)])
def test_crash_before_claim_releases_without_exchange_or_pause(desk_case, seconds, expired):
    store, decision, quote, exchange, executor, now = desk_case
    if expired:
        decision = decision.model_copy(update={"expires_at": now + timedelta(seconds=5)})
    store.reserve(decision, 1, D(".45"), now)

    def unexpected(*args):
        pytest.fail("unclaimed reservations must never be reconciled through exchange")

    exchange.reconcile = unexpected
    result = executor.reconcile(now=now + timedelta(seconds=seconds))
    assert result[0]["status"] == "terminal"
    desk = store.snapshot(now)["desks"][0]
    assert not desk["paused"] and D(desk["committed"]) == 0
    assert D(desk["available_cash"]) == D(30)
    assert desk["filled_today"] == 0
    assert not store.claim_submission(decision.decision_id, now + timedelta(seconds=seconds))


def test_unclaimed_nonstale_reservation_is_untouched(desk_case):
    store, decision, quote, exchange, executor, now = desk_case
    store.reserve(decision, 1, D(".45"), now)

    def unexpected(*args):
        pytest.fail("fresh reservation is being prepared, not missing at exchange")

    exchange.reconcile = unexpected
    assert executor.reconcile(now=now + timedelta(seconds=120)) == []
    assert store.get_decision(decision.decision_id)["status"] == "reserved"
    assert not store.snapshot(now)["desks"][0]["paused"]


def test_claimed_unknown_cannot_be_released_as_abandoned(desk_case):
    store, decision, quote, exchange, executor, now = desk_case
    store.reserve(decision, 1, D(".45"), now)
    assert store.claim_submission(decision.decision_id, now)
    assert not store.release_unsubmitted(decision.decision_id, now + timedelta(minutes=10))
    executor.reconcile(now=now + timedelta(minutes=10))
    desk = store.snapshot(now)["desks"][0]
    assert desk["paused"] and D(desk["committed"]) == D(".45")
    assert store.get_decision(decision.decision_id)["status"] == "unknown"
