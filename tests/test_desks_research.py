import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from kalshi_bot.desks.contracts import DeskError
from kalshi_bot.desks.research import (
    HTTPProvider,
    ModelResult,
    ProviderConfig,
    ProviderFailure,
    PublicFetcher,
)
from kalshi_bot.desks.research_models import ResearchBudget, ResearchJob
from kalshi_bot.desks.store import DeskStore
from kalshi_bot.desks.supervisor import Supervisor

NOW = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
OUTPUT = {"summary": "No information edge today.", "next_action": "Review the next government release."}


def store_at(tmp_path):
    store = DeskStore(f"sqlite:///{tmp_path / 'desks.db'}")
    store.initialize("research-test", NOW)
    return store


def runtime(store, **kwargs):
    return Supervisor(store, market_reader=lambda now: {"markets": [], "sources": []}, **kwargs)


class FakeProvider:
    config = ProviderConfig("openai", "test-model", "test-only", Decimal("1"), Decimal("2"))
    calls = 0

    def complete(self, system, context):
        self.calls += 1
        return ModelResult(json.dumps(OUTPUT), "test-model", 100)


def test_missing_provider_visible_and_zero_trades_valid(tmp_path):
    sup = runtime(store_at(tmp_path))
    result = sup.tick(NOW)
    assert result["health"]["chatgpt"]["status"] == "needs_operator"
    assert "unattended_runner_missing" in result["health"]["chatgpt"]["reasons"]
    job = sup.claim_external("chatgpt", "session-1", NOW)
    sup.complete_external(job["job_id"], job["claim_token"], OUTPUT, "test-model", NOW, desk_id="chatgpt")
    assert sup.status(NOW)["health"]["chatgpt"]["completed_cycles"] == 1
    assert not sup.store.snapshot(NOW)["decisions"]


def test_cross_desk_claim_completion_refused(tmp_path):
    sup = runtime(store_at(tmp_path))
    job = sup.claim_external("chatgpt", "session-1", NOW)
    with pytest.raises(DeskError, match="research_claim_mismatch"):
        sup.complete_external(job["job_id"], job["claim_token"], OUTPUT, "test", NOW, desk_id="claude")


def test_zero_budget_never_calls_provider(tmp_path):
    provider = FakeProvider()
    sup = runtime(store_at(tmp_path), providers={"chatgpt": provider})
    sup.tick(NOW)
    assert provider.calls == 0
    assert "paid_research_budget_not_authorized" in sup.status(NOW)["health"]["chatgpt"]["reasons"]


def test_provider_cost_reserved_and_released_to_actual(tmp_path):
    provider = FakeProvider()
    sup = runtime(store_at(tmp_path), providers={"chatgpt": provider}, monthly_budget_usd="2")
    sup.tick(NOW)
    assert provider.calls == 1
    with sup.store._tx() as session:
        budget = session.scalar(select(ResearchBudget))
        assert budget.committed_microusd == 100
    assert sup.status(NOW)["health"]["chatgpt"]["completed_cycles"] == 1


def test_unknown_provider_bill_is_never_retried_or_released(tmp_path):
    class Unknown(FakeProvider):
        def complete(self, system, context):
            self.calls += 1
            raise ProviderFailure("provider_bill_unknown", bill_unknown=True)
    provider = Unknown()
    sup = runtime(store_at(tmp_path), providers={"chatgpt": provider}, monthly_budget_usd="2")
    sup.tick(NOW)
    sup.tick(NOW + timedelta(hours=2))
    assert provider.calls == 1
    with sup.store._tx() as session:
        assert session.scalar(select(ResearchBudget)).committed_microusd == provider.config.reservation_microusd()
    assert "provider_bill_requires_reconciliation" in sup.status(NOW)["health"]["chatgpt"]["reasons"]


def test_known_free_retry_stops_after_three_attempts(tmp_path):
    class Retry(FakeProvider):
        def complete(self, system, context):
            self.calls += 1
            raise ProviderFailure("provider_rate_limited", bill_unknown=False, retryable=True)
    provider = Retry()
    sup = runtime(store_at(tmp_path), providers={"chatgpt": provider}, monthly_budget_usd="2")
    for minutes in (0, 6, 17, 25):
        sup.tick(NOW + timedelta(minutes=minutes))
    assert provider.calls == 3
    with sup.store._tx() as session:
        assert session.scalar(select(ResearchBudget)).committed_microusd == 0


def test_parallel_enqueue_has_one_job_per_desk(tmp_path):
    store = store_at(tmp_path)
    sup = runtime(store)
    second = runtime(DeskStore(f"sqlite:///{tmp_path / 'desks.db'}"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda i: (sup if i % 2 else second).enqueue("claude", NOW), range(12)))
    assert len(set(ids)) == 1
    with store._tx() as session:
        assert len(list(session.scalars(select(ResearchJob)))) == 1


def test_sources_refuse_credentials_redirects_private_hosts_and_oversized():
    fetcher = PublicFetcher(transport=httpx.MockTransport(lambda request: httpx.Response(
        302, headers={"location": "http://127.0.0.1/private"})))
    for url in ("http://api.weather.gov", "https://127.0.0.1", "https://api.weather.gov:444/a", "https://user:pass@api.weather.gov"):
        with pytest.raises(DeskError, match="source_not_allowlisted"):
            fetcher(url, NOW)
    with pytest.raises(DeskError, match="source_http_failure"):
        fetcher("https://api.weather.gov/a", NOW)


def test_provider_http_request_and_usage_parsing():
    def handle(request):
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert body["max_completion_tokens"] == 3000
        return httpx.Response(200, json={"model": "test-model", "choices": [{"message": {"content": json.dumps(OUTPUT)}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}})
    provider = HTTPProvider(FakeProvider.config, transport=httpx.MockTransport(handle))
    result = provider.complete("system", {})
    assert result.cost_microusd == 140


def test_external_runner_requires_a_real_recent_completion(tmp_path):
    sup = runtime(store_at(tmp_path), external_runners_verified=True)
    assert sup.status(NOW)["health"]["chatgpt"]["status"] == "needs_operator"
    job = sup.claim_external("chatgpt", "scheduled-session-runner", NOW)
    sup.complete_external(job["job_id"], job["claim_token"], OUTPUT, "test-model", NOW, desk_id="chatgpt")
    assert sup.status(NOW)["health"]["chatgpt"]["status"] == "healthy"
    assert sup.status(NOW + timedelta(hours=25))["health"]["chatgpt"]["status"] == "needs_operator"


def test_unregistered_source_ids_cannot_support_research(tmp_path):
    sup = runtime(store_at(tmp_path))
    job = sup.claim_external("chatgpt", "session", NOW)
    bad = {**OUTPUT, "candidates": [{"ticker": "TEST", "hypothesis": "An information edge",
                                   "reason": "A fabricated source", "source_ids": ["invented"]}]}
    with pytest.raises(DeskError, match="unverified_source_id"):
        sup.complete_external(job["job_id"], job["claim_token"], bad, "test", NOW, desk_id="chatgpt")


def test_progressive_scanner_continues_cursor_and_shares_snapshot(tmp_path):
    from urllib.parse import parse_qs, urlsplit

    from kalshi_bot.desks.research import PublicMarketReader
    requests = []
    def fetch(url, now):
        cursor = parse_qs(urlsplit(url).query).get("cursor", [""])[0]
        requests.append(cursor)
        index = int(cursor or 0)
        return {"source_id": "source", "url": url, "retrieved_at": now.isoformat(),
                "excerpt": "{}", "sha256": "a" * 64,
                "_market_data": [{"ticker": f"SERIES{index}-EVENT-MARKET",
                                  "event_ticker": f"SERIES{index}-EVENT", "market_type": "binary",
                                  "close_time": (now + timedelta(days=3)).isoformat()}],
                "_cursor": str(index + 1) if index < 3 else ""}
    store = store_at(tmp_path)
    reader = PublicMarketReader(fetch, store=store)
    first = reader(NOW)
    second_desk = reader(NOW)
    assert requests == ["", "1"]
    assert first["markets"] == second_desk["markets"]
    assert first["sources"][0]["source_id"] != second_desk["sources"][0]["source_id"]
    restarted = PublicMarketReader(fetch, store=store)
    later = restarted(NOW + timedelta(hours=1))
    assert requests == ["", "1", "2", "3"]
    assert later["coverage"]["completed_passes"] == 1
    assert later["coverage"]["cached_markets"] == 4


def test_autonomous_postmortems_clear_settlement_backlog(tmp_path):
    from test_desks_store import NOW as TRADE_NOW
    from test_desks_store import report, reserve, start

    from kalshi_bot.desks.contracts import Settlement

    store = start(DeskStore(f"sqlite:///{tmp_path / 'reviews.db'}"))
    reviews = []
    for n in range(3):
        row = reserve(store, n)
        report(store, row)
        store.settlement(row["decision_id"], Settlement(ticker=f"MARKET-{n}", yes_payout=Decimal(0),
                         settled_at=TRADE_NOW, source="official_test_source"))
        reviews.append({"decision_id": row["decision_id"], "thesis_correct": False,
                        "price_wrong": True, "failure_category": "overconfidence",
                        "analysis": "The source estimate was insufficiently calibrated.",
                        "next_change": "Reject this edge class until fresh evidence exists."})
    sup = runtime(store, external_runners_verified=True)
    assert "settlement_learning_backlog" in sup.status(TRADE_NOW)["health"]["chatgpt"]["reasons"]
    job = sup.claim_external("chatgpt", "scheduled-runner", TRADE_NOW)
    sup.complete_external(job["job_id"], job["claim_token"], {**OUTPUT, "postmortems": reviews},
                          "test-model", TRADE_NOW, desk_id="chatgpt")
    status = sup.status(TRADE_NOW)["health"]["chatgpt"]
    assert status["unreviewed_settlements"] == 0
    assert status["status"] == "healthy"


def test_context_prioritizes_own_pending_reviews_despite_grouped_books(tmp_path):
    sup = runtime(store_at(tmp_path))
    original_snapshot = sup.store.snapshot
    snapshot = original_snapshot(NOW)
    snapshot["decisions"] = []
    for desk in ("chatgpt", "claude"):
        for index in range(25):
            snapshot["decisions"].append({"decision_id": f"{desk}-decision-{index:03}", "desk_id": desk,
                "settled": True, "fill_cost": "0.8", "fees": "0.02", "pnl": "-0.82", "payout": "0",
                "thesis": "A thesis " * 200, "counterargument": "A counterargument " * 200,
                "updated_at": (NOW + timedelta(minutes=index)).isoformat(),
                "settlement": {"yes_payout": "0", "settled_at": NOW.isoformat(), "source": "official"}})
    sup.store.snapshot = lambda now: snapshot
    for desk in ("chatgpt", "claude"):
        job = sup.claim_external(desk, "runner", NOW)
        context = job["context"]
        assert context["own_unreviewed_settlements"] == 25
        first = context["recent_decisions"][0]
        assert first["decision_id"] == f"{desk}-decision-000"
        assert first["needs_postmortem"] is True
        assert first["fill_cost"] == "0.8"
        assert first["settlement"]["yes_payout"] == "0"
        assert first["thesis"] and first["counterargument"]
        fitted = sup._fit_context(context, "system", 100)
        assert len(fitted["recent_decisions"]) == 8
        assert all(row["desk_id"] == desk and row["needs_postmortem"] for row in fitted["recent_decisions"])


def test_publication_recovery_does_not_resubmit_a_recorded_refusal(tmp_path):
    from test_desks_store import decision

    from kalshi_bot.desks.research import ResearchOutput

    calls = []
    def submit(pick, now):
        calls.append(now)
        raise DeskError("price_cap_exceeded" if len(calls) == 1 else "expired_or_future_decision")
    sup = runtime(store_at(tmp_path), submit_decision=submit)
    job = sup.claim_external("chatgpt", "runner", NOW)
    pick = decision(at=NOW).model_copy(update={"round_id": "research-test"})
    output = ResearchOutput.model_validate({**OUTPUT,
        "decisions": [{"decision": pick.model_dump(mode="json"), "source_ids": ["test"]}]})
    # Simulate a process crash after publications and refusal were durable but
    # before the job transitioned to completed.
    sup._publish(job["job_id"], output, "chatgpt", "test", NOW)
    sup.tick(NOW + timedelta(minutes=31))
    assert len(calls) == 1
    refusals = [p for p in sup.store.snapshot(NOW)["publications"] if p["kind"] == "decision_refused"]
    assert len(refusals) == 1
    assert refusals[0]["payload"]["reason"] == "price_cap_exceeded"
    with sup.store._tx() as session:
        assert session.get(ResearchJob, job["job_id"]).state == "completed"
