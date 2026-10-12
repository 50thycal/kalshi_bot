"""Selective detailed history (DEC-034): broad inventory, full history for selected contexts."""

import copy
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from kalshi_bot.catalog import selection, service
from kalshi_bot.catalog.evaluators import refresh
from kalshi_bot.catalog.ingest import Discovery, DiscoveryDeferred, reset_source_reconciliation
from kalshi_bot.catalog.research import cohort
from kalshi_bot.catalog.store import RULE_FIELDS, SEMANTIC_FIELDS, Store, digest, rule_hash, unpack

GIB = 1024**3


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "catalog.db")


@pytest.fixture
def selective():
    with patch.object(selection, "POLICY", "selective"):
        yield


def market(ticker="KXNEW-26OCT11-T1", **changes):
    return {
        "ticker": ticker,
        "event_ticker": ticker.rsplit("-", 1)[0],
        "series_ticker": ticker.split("-", 1)[0],
        "market_type": "binary",
        "status": "active",
        "rules_primary": "If the official value is above 1, the market resolves to Yes. " * 4,
        "rules_secondary": "The official source is the agency release.",
        "title": "Will it be above 1?",
        "close_time": "2026-10-12T00:00:00Z",
        "created_time": "2026-10-11T00:00:00Z",
        "notional_value_dollars": "1.0000",
        "yes_bid_dollars": "0.40",
        "yes_ask_dollars": "0.42",
        "volume_fp": "10.00",
        "open_interest_fp": "5.00",
        **changes,
    }


def ready(store):
    while not selection.membership_ready_for(store):
        selection.bootstrap_page(store)


def revisions(store, ticker, kind="market"):
    with store.connect() as db:
        return [
            (row[0], unpack(row[1]))
            for row in db.execute(
                "SELECT hash,document FROM revisions WHERE kind=? AND ticker=? ORDER BY rowid",
                (kind, ticker),
            )
        ]


def paper(id=1, ticker="KXOLD-26OCT11-T1", **changes):
    return {
        "id": id,
        "market_ticker": ticker,
        "strategy": "mmsell10",
        "side": "no",
        "assumed_price": 90,
        "quantity": 2,
        "pnl": -0.20,
        "fees": 0.01,
        "status": "settled",
        "created_at": "2026-10-01T00:00:00+00:00",
        "closed_at": "2026-10-02T00:00:00+00:00",
        "fill_assumption": "maker",
        "experiment_deployment_arm_id": 7,
        **changes,
    }


def review_payload(doc):
    semantics = dict.fromkeys(SEMANTIC_FIELDS)
    semantics.update(
        resolution_mechanism="fixed_observation",
        settlement_source="official",
        shared_exposure_key="macro-release",
    )
    return dict(kind=doc["kind"], ticker=doc["ticker"], rules_hash=doc["rules_hash"],
                actor="tester", rationale="read official rules", semantics=semantics)


# --- policy and membership ---------------------------------------------------------------


def test_default_policy_keeps_full_history_for_everything(store):
    store.upsert("market", "KXNEW-26OCT11-T1", market(), "KXNEW")
    [(key, document)] = revisions(store, "KXNEW-26OCT11-T1")
    assert selection.representation(document) == selection.FULL
    assert "representation" not in document  # stored exactly as before this policy
    assert store.status()["history_policy"]["policy_effective"] is False


def test_unready_membership_preserves_full_history_and_reports_unready(store, selective):
    store.upsert("market", "KXNEW-26OCT11-T1", market(), "KXNEW")
    [(_key, document)] = revisions(store, "KXNEW-26OCT11-T1")
    assert selection.representation(document) == selection.FULL
    status = store.status()["history_policy"]
    assert status["policy_configured"] == "selective"
    assert status["policy_effective"] is False and status["membership_ready"] is False


def test_unselected_market_gets_compact_rules_lifecycle_and_full_current_object(store, selective):
    ready(store)
    doc = store.upsert("market", "KXNEW-26OCT11-T1", market(), "KXNEW")
    [(key, compact)] = revisions(store, "KXNEW-26OCT11-T1")
    assert compact["representation"] == selection.COMPACT
    assert compact["full_payload_retained"] is False and compact["satisfies_payload_proof"] is False
    # Rules travel exactly, so the stored rules hash can be re-derived from the compact row.
    assert set(compact["rules"]) == set(RULE_FIELDS)
    assert digest({"rules": digest(compact["rules"]),
                   "parents": compact["parent_rules_hashes"]}) == compact["rules_hash"]
    assert digest(compact["rules"]) == rule_hash(market())
    assert compact["lifecycle"]["status"] == "active"
    assert compact["identity"]["event_ticker"] == "KXNEW-26OCT11"
    assert "yes_bid_dollars" not in str(compact) and "volume_fp" not in str(compact)
    content = {k: v for k, v in compact.items() if k != "content_sha256"}
    assert compact["content_sha256"] == digest(content)
    assert key == selection.compact_key(compact["semantic_hash"])
    # Current object (read by economics, readiness, documents) keeps the complete payload.
    assert store.get("market", "KXNEW-26OCT11-T1")["raw"] == market()
    assert doc["raw"]["yes_bid_dollars"] == "0.40"
    assert store.status()["history_policy"]["revision_writes"]["compact"] == 1


def test_quote_changes_stay_idempotent_and_material_changes_stay_traceable(store, selective):
    ready(store)
    ticker = "KXNEW-26OCT11-T1"
    store.upsert("market", ticker, market(), "KXNEW")
    _doc, revised = store.upsert_revision("market", ticker, market(yes_bid_dollars="0.55",
                                                                    volume_fp="99.0"), "KXNEW")
    assert revised is False
    steps = [
        market(status="closed"),
        market(status="closed", rules_secondary="Corrected source."),
        market(status="finalized", rules_secondary="Corrected source.", result="no",
               settlement_ts="2026-10-12T00:05:00Z", settlement_value_dollars="0.0000"),
    ]
    for raw in steps:
        assert store.upsert_revision("market", ticker, raw, "KXNEW")[1] is True
    history = [doc for _key, doc in revisions(store, ticker)]
    assert [d["lifecycle"]["status"] for d in history] == ["active", "closed", "closed",
                                                           "finalized"]
    # Rules are carried on first sight and on each rules change, not on lifecycle-only rows.
    assert [d["rules"] is not None for d in history] == [True, False, True, False]
    assert history[2]["rules"]["rules_secondary"] == "Corrected source."
    assert history[3]["lifecycle"]["settlement_value_dollars"] == "0.0000"
    assert "parent_rules_hashes" not in history[3] and "identity" not in history[3]
    assert history[2]["parent_rules_hashes"] == history[0]["parent_rules_hashes"]


def test_parent_rule_change_is_a_new_compact_revision(store, selective):
    ready(store)
    store.upsert("event", "KXNEW-26OCT11", {"event_ticker": "KXNEW-26OCT11", "title": "a"},
                 "KXNEW")
    store.upsert("market", "KXNEW-26OCT11-T1", market(), "KXNEW")
    store.upsert("event", "KXNEW-26OCT11", {"event_ticker": "KXNEW-26OCT11", "title": "b"},
                 "KXNEW")
    assert store.upsert_revision("market", "KXNEW-26OCT11-T1", market(), "KXNEW")[1] is True
    first, second = (doc for _key, doc in revisions(store, "KXNEW-26OCT11-T1"))
    assert first["parent_rules_hashes"] != second["parent_rules_hashes"]
    assert second["rules"] is not None  # combined rules hash changed with the parent


@pytest.mark.parametrize(
    "promote",
    [
        lambda s: s.evidence_page("live", [paper(1, "KXNEW-26OCT11-T1")], {"after": 1}),
        lambda s: s.evidence_page("paper", [paper(1, "KXNEW-26OCT11-T1")], {"after": 1}),
        lambda s: selection.research_request(s, {
            "scope": "series", "ticker": "KXNEW", "actor": "lab", "reason": "thesis cohort",
            "provider": "weather-lab"}),
        lambda s: selection.research_request(s, {
            "scope": "event", "ticker": "KXNEW-26OCT11", "actor": "lab", "reason": "event"}),
    ],
    ids=["live", "paper", "series-research-unrelated-provider", "event-research"],
)
def test_relevance_promotes_full_history_and_survives_restart(store, selective, promote):
    ready(store)
    promote(store)
    reopened = Store(store.path)
    reopened.upsert("market", "KXNEW-26OCT11-T1", market(), "KXNEW")
    [(_key, document)] = revisions(reopened, "KXNEW-26OCT11-T1")
    assert selection.representation(document) == selection.FULL
    assert document["raw"]["yes_bid_dollars"] == "0.40"


def test_reviews_and_registry_provenance_select_their_contexts(store, selective):
    store.upsert("series", "KXREV", {"title": "Reviewed", "contract_url": None}, "KXREV")
    store.upsert("market", "KXREV-26OCT11-T1", market("KXREV-26OCT11-T1"), "KXREV")
    store.review(review_payload(store.get("series", "KXREV")))
    store.review(review_payload(store.get("market", "KXREV-26OCT11-T1")))
    store.migrate_review({"series_ticker": "KXREG", "migration_version": "registry-v1"})
    ready(store)
    reasons = {(m["scope"], m["ticker"], m["reason"]) for m in
               selection.members(store, active=True, limit=500)["items"]}
    assert {("series", "KXREV", "series_review"), ("market", "KXREV-26OCT11-T1", "market_review"),
            ("series", "KXREG", "legacy_registry")} <= reasons
    assert all(not m["selection_is_qualification"] for m in
               selection.members(store, limit=500)["items"])
    store.upsert("market", "KXREG-26OCT11-T1", market("KXREG-26OCT11-T1"), "KXREG")
    [(_key, document)] = revisions(store, "KXREG-26OCT11-T1")
    assert selection.representation(document) == selection.FULL


def test_bootstrap_is_bounded_resumable_and_backfills_evidence_index(store, selective):
    with store.connect() as db:  # rows written by an older build: no market, no membership
        for i in range(1, 6):
            db.execute("INSERT INTO evidence (source,source_id,series,document) VALUES "
                       "('paper',?,'KXOLD',?)", (i, json.dumps(paper(i))))
    assert selection.bootstrap_page(store, batch=2) >= 0
    reopened = Store(store.path)
    while not selection.membership_ready_for(reopened):
        selection.bootstrap_page(reopened, batch=2)
    with reopened.connect() as db:
        assert db.execute("SELECT count(*) FROM evidence_markets").fetchone()[0] == 5
    status = reopened.status()["history_policy"]
    assert status["membership_ready"] and status["policy_effective"]
    assert status["active_by_reason"]["paper_evidence"] == 1
    assert status["bootstrap"]["examined"] >= 5


def test_membership_publish_cannot_race_an_upsert_into_compact(store, selective):
    ready(store)
    holder = sqlite3.connect(store.path, timeout=30)
    holder.execute("BEGIN IMMEDIATE")
    selection.select_in(holder, "market", "KXNEW-26OCT11-T1", "live_evidence", "catalog")
    done = threading.Event()

    def writer():
        store.upsert("market", "KXNEW-26OCT11-T1", market(), "KXNEW")
        done.set()

    thread = threading.Thread(target=writer)
    thread.start()
    time.sleep(0.3)
    assert not done.is_set()  # the upsert waits for the membership transaction
    holder.commit()
    holder.close()
    thread.join(10)
    [(_key, document)] = revisions(store, "KXNEW-26OCT11-T1")
    assert selection.representation(document) == selection.FULL


def test_deselection_never_removes_automatic_relevance_or_history(store, selective):
    ready(store)
    store.evidence_page("live", [paper(1, "KXNEW-26OCT11-T1")], {"after": 1})
    request = {"scope": "market", "ticker": "KXNEW-26OCT11-T1", "actor": "lab", "reason": "r"}
    selection.research_request(store, request)
    store.upsert("market", "KXNEW-26OCT11-T1", market(), "KXNEW")
    result = selection.research_request(store, {**request, "action": "deselect"})
    assert result["still_selected"] and result["history_deleted"] is False
    assert result["remaining_memberships"] == [{"reason": "live_evidence", "provider": "catalog"}]
    with pytest.raises(LookupError):
        selection.research_request(store, {**request, "action": "deselect"})
    assert len(revisions(store, "KXNEW-26OCT11-T1")) == 1
    events = selection.members(store, ticker="KXNEW-26OCT11-T1", limit=10)["items"]
    assert {(e["reason"], e["active"]) for e in events} == {("live_evidence", True),
                                                            ("research", False)}


def test_existing_full_revisions_and_review_bindings_survive_policy_switch(store):
    ticker = "KXOLD-26OCT11-T1"
    store.upsert("market", ticker, market(ticker), "KXOLD")
    before = revisions(store, ticker)
    store.review(review_payload(store.get("market", ticker)))
    with patch.object(selection, "POLICY", "selective"):
        ready(store)
        store.upsert("market", ticker, market(ticker, status="closed"), "KXOLD")
    after = revisions(store, ticker)
    assert after[0] == before[0]
    assert store.get("market", ticker)["review_status"] == "reviewed"  # market_review selected


# --- hydration and gaps --------------------------------------------------------------------


def discovery_with(store, routes):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        for needle, response in routes:
            if needle in str(request.url):
                return response(request) if callable(response) else response
        return httpx.Response(404, json={})

    client = httpx.Client(base_url="https://test", transport=httpx.MockTransport(handler))
    return Discovery(store, client), calls


def test_selection_after_inventory_shows_gap_and_hydrates_without_rewriting(store, selective):
    ready(store)
    ticker = "KXNEW-26OCT11-T1"
    store.upsert("market", ticker, market(), "KXNEW")
    compact_before = revisions(store, ticker)
    store.evidence_page("paper", [paper(1, ticker)], {"after": 1})
    with store.connect() as db:
        coverage = selection.coverage_in(db, "market", ticker, store.get("market", ticker))
    assert "compact_revisions_without_full_payload" in coverage["missing"]
    assert "hydration_pending" in coverage["missing"]
    assert coverage["full_payload_history_complete"] is False
    discovery, calls = discovery_with(store, [
        ("/markets/" + ticker, httpx.Response(200, json={"market": market(status="closed")}))])
    assert selection.hydrate_step(store, discovery) == 1
    history = revisions(store, ticker)
    assert history[: len(compact_before)] == compact_before  # never replaced
    assert selection.representation(history[-1][1]) == selection.FULL
    with store.connect() as db:
        coverage = selection.coverage_in(db, "market", ticker, store.get("market", ticker))
    assert coverage["hydration"][0]["status"] == "completed"
    assert coverage["revisions_compact"] == 1 and coverage["revisions_full"] == 1
    assert coverage["unobserved_history_recoverable"] is False
    assert "compact_revisions_without_full_payload" in coverage["missing"]


def test_unavailable_archive_is_a_recorded_gap_not_coverage(store, selective):
    ready(store)
    ticker = "KXNEW-26OCT11-T1"
    store.upsert("market", ticker, market(), "KXNEW")
    store.evidence_page("live", [paper(1, ticker)], {"after": 1})
    discovery, calls = discovery_with(store, [])
    assert selection.hydrate_step(store, discovery) == 1
    assert any("/historical/markets/" in call for call in calls)
    with store.connect() as db:
        coverage = selection.coverage_in(db, "market", ticker, store.get("market", ticker))
    assert coverage["hydration"][0]["status"] == "unavailable"
    assert "hydration_unavailable_from_exchange" in coverage["missing"]


def test_series_hydration_pages_and_provider_backoff_keeps_cursor(store, selective):
    ready(store)
    store.upsert("market", "KXNEW-26OCT11-T1", market(), "KXNEW")  # compact history exists
    selection.research_request(store, {"scope": "series", "ticker": "KXNEW", "actor": "a",
                                       "reason": "comparables"})
    pages = {
        "first": {"markets": [market("KXNEW-26OCT11-T1")], "cursor": "c2"},
        "second": {"markets": [market("KXNEW-26OCT11-T2")], "cursor": ""},
    }
    state = {"limited": False}

    def markets(request):
        if state["limited"]:
            return httpx.Response(429, headers={"Retry-After": "60"})
        return httpx.Response(200, json=pages["second" if "cursor=c2" in str(request.url)
                                              else "first"])

    discovery, _calls = discovery_with(store, [
        ("/series/KXNEW", httpx.Response(200, json={"series": {"ticker": "KXNEW"}})),
        ("/markets?", markets),
    ])
    assert selection.hydrate_step(store, discovery) == 1
    with store.connect() as db:
        row = db.execute("SELECT status,cursor FROM hydration_targets WHERE scope='series'"
                         ).fetchone()
    assert tuple(row) == ("pending", "c2")
    state["limited"] = True
    with pytest.raises(httpx.HTTPStatusError):
        selection.hydrate_step(store, discovery)
    with pytest.raises(DiscoveryDeferred):
        selection.hydrate_step(store, discovery)
    with store.connect() as db:
        row = db.execute("SELECT status,cursor FROM hydration_targets WHERE scope='series'"
                         ).fetchone()
    assert row[1] == "c2"
    state["limited"] = False
    store.set_state("discovery:backoff", {"failures": 0, "not_before_unix": 0})
    with store.connect() as db:
        db.execute("UPDATE hydration_targets SET next_check=0")
    assert selection.hydrate_step(store, discovery) == 1
    with store.connect() as db:
        assert db.execute("SELECT status FROM hydration_targets WHERE scope='series'"
                          ).fetchone()[0] == "completed"
    assert selection.representation(revisions(store, "KXNEW-26OCT11-T2")[0][1]) == selection.FULL


def test_bulk_pause_defers_series_hydration_but_selected_markets_continue(store, selective):
    ready(store)
    for ticker in ("KXNEW-26OCT11-T1", "KXSER-26OCT11-T1"):
        store.upsert("market", ticker, market(ticker), ticker.split("-")[0])
    store.evidence_page("live", [paper(1, "KXNEW-26OCT11-T1")], {"after": 1})
    selection.research_request(store, {"scope": "series", "ticker": "KXSER", "actor": "a",
                                       "reason": "r"})
    store.set_state("storage:guard", {"bulk_paused": True})
    discovery, calls = discovery_with(store, [
        ("/markets/KXNEW", httpx.Response(200, json={"market": market()}))])
    assert selection.hydrate_step(store, discovery, budget=5) == 1
    assert not any("series" in call for call in calls)
    with store.connect() as db:
        assert db.execute("SELECT status FROM hydration_targets WHERE scope='series'"
                          ).fetchone()[0] == "pending"


def test_no_hydration_when_no_compact_history_was_ever_written(store, selective):
    store.upsert("market", "KXNEW-26OCT11-T1", market(), "KXNEW")
    ready(store)
    store.evidence_page("live", [paper(1, "KXNEW-26OCT11-T1")], {"after": 1})
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM hydration_targets").fetchone()[0] == 0


# --- discovery sweeps and loop fairness ----------------------------------------------------


def test_unfinished_sweep_is_never_reset_by_the_clock(store):
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    store.set_state("discovery:events", {"cursor": "keep", "complete": False,
                                         "sweep_started_at": old})
    store.set_state("discovery:series", {"cursor": None, "complete": True,
                                         "sweep_started_at": old, "sweep_records": 9})
    discovery = Discovery(store, httpx.Client(base_url="https://test"))
    assert discovery.restart_completed_sweeps() == ["series"]
    assert store.state("discovery:events")["cursor"] == "keep"
    series = store.state("discovery:series")
    assert series["complete"] is False and series["previous_sweep"]["sweep_records"] == 9


def test_completed_recent_sweep_waits_and_legacy_complete_restarts(store):
    recent = datetime.now(timezone.utc).isoformat()
    store.set_state("discovery:series", {"cursor": None, "complete": True,
                                         "sweep_started_at": recent})
    store.set_state("discovery:events", {"cursor": None, "complete": True})
    discovery = Discovery(store, httpx.Client(base_url="https://test"))
    assert discovery.restart_completed_sweeps() == ["events"]


def test_sweep_records_start_and_completion(store):
    pages = iter([{"events": [{"event_ticker": "KXA-E", "series_ticker": "KXA",
                               "markets": [market("KXA-E-1")]}], "cursor": "p2"},
                  {"events": [], "cursor": ""}])
    client = httpx.Client(base_url="https://test", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=next(pages))))
    discovery = Discovery(store, client)
    discovery.page("events")
    first = store.state("discovery:events")
    discovery.page("events")
    second = store.state("discovery:events")
    assert first["sweep_started_at"] == second["sweep_started_at"]
    assert second["complete"] and second["sweep_pages"] == 2 and second["sweep_records"] == 1


def test_source_pass_is_reset_only_after_it_caught_up_and_a_day_passed(store):
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    store.set_state("cursor:paper", {"after": 500, "pass_started_at": old})
    store.set_state("cursor:live", {"after": 9, "pass_started_at": old,
                                    "caught_up_at": datetime.now(timezone.utc).isoformat()})
    assert reset_source_reconciliation(store) == ["live"]
    assert store.state("cursor:paper")["after"] == 500
    assert store.state("cursor:live")["after"] == 0
    assert reset_source_reconciliation(store) == []  # the new live pass has not caught up


def test_collect_loop_runs_every_job_once_and_keeps_unfinished_sweep(store, monkeypatch):
    from kalshi_bot.catalog import headroom

    usage = SimpleNamespace(total=40 * GIB, free=30 * GIB, used=10 * GIB)
    monkeypatch.setattr(headroom.shutil, "disk_usage", lambda path: usage)
    calls = []
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    store.set_state("discovery:events", {"cursor": "mid", "complete": False,
                                         "sweep_started_at": old})
    store.set_state("storage:compression", {"complete": True})

    class FakeDiscovery(Discovery):
        def __init__(self, value):
            super().__init__(value, SimpleNamespace(close=lambda: None))

        def page(self, job):
            calls.append(job)
            return 1

        def updates(self):
            calls.append("updates")
            return 1

        def live_outcomes(self):
            calls.append("outcomes")
            return 0

    class Contracts:
        def __init__(self, value):
            self.client = SimpleNamespace(close=lambda: None)

        def step(self):
            calls.append("documents")
            return 0

    class Stopped:
        cycles = 0

        def is_set(self):
            return self.cycles >= 2

        def wait(self, interval):
            self.cycles += 1

    monkeypatch.setattr(service, "Discovery", FakeDiscovery)
    monkeypatch.setattr(service, "ContractCapture", Contracts)
    monkeypatch.setattr(service, "source_page",
                        lambda value, url, source: calls.append(source) or 0)
    monkeypatch.setattr(service.selection, "hydrate_step",
                        lambda value, discovery: calls.append("hydration") or 0)
    monkeypatch.setattr(service, "refresh", lambda value: 0)
    monkeypatch.setattr(service, "release_memory", lambda: None)
    service.collect(store, Stopped(), "source", 1)
    for job in ("series", "events", "updates", "hydration", "documents", "paper", "live",
                "outcomes"):
        assert calls.count(job) == 2, job
    assert store.state("discovery:events")["cursor"] == "mid"
    assert store.state("job:selection:bootstrap")["error"] is None
    assert selection.membership_ready_for(store)


# --- APIs ----------------------------------------------------------------------------------


@pytest.fixture
def server(store):
    instance = service.make_server(store, "a" * 32, ("127.0.0.1", 0))
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    with httpx.Client(base_url=f"http://127.0.0.1:{instance.server_port}",
                      trust_env=False) as client:
        yield client
    instance.shutdown()
    instance.server_close()


def test_selection_api_auth_bounds_and_visibility(store, server, selective):
    headers = {"Authorization": "Bearer " + "a" * 32}
    body = {"scope": "series", "ticker": "KXNEW", "actor": "lab", "reason": "weather thesis",
            "provider": "weather-lab"}
    assert server.post("/v1/selection", json=body).status_code == 401
    assert server.get("/v1/selection").status_code == 401
    big = {**body, "reason": "x" * 5000}
    assert server.post("/v1/selection", json=big, headers=headers).status_code == 400
    bad = {**body, "ticker": "../etc"}
    assert server.post("/v1/selection", json=bad, headers=headers).status_code == 400
    assert server.post("/v1/selection", json={**body, "provider": "Bad Name"},
                       headers=headers).status_code == 400
    created = server.post("/v1/selection", json=body, headers=headers)
    assert created.status_code == 201 and created.json()["selection_is_qualification"] is False
    listed = server.get("/v1/selection?scope=series", headers=headers).json()
    assert listed["items"][0]["provider"] == "weather-lab"
    assert listed["policy"]["membership_ready"] is False
    missing = server.post("/v1/selection", json={**body, "action": "deselect",
                                                 "provider": "other"}, headers=headers)
    assert missing.status_code == 409
    status = server.get("/v1/status", headers=headers).json()["history_policy"]
    assert status["active_by_reason"]["research"] == 1
    assert status["compact_revisions_satisfy_payload_proof"] is False


def test_revision_history_api_labels_representation_and_coverage(store, server, selective):
    ready(store)
    store.upsert("market", "KXNEW-26OCT11-T1", market(), "KXNEW")
    headers = {"Authorization": "Bearer " + "a" * 32}
    result = server.get("/v1/objects/market/KXNEW-26OCT11-T1/revisions", headers=headers).json()
    assert result["representations"] == [selection.COMPACT]
    assert result["coverage"]["full_payload_history_complete"] is False
    coverage = server.get("/v1/objects/market/KXNEW-26OCT11-T1/coverage", headers=headers)
    assert coverage.json()["revisions_compact"] == 1


# --- research cohort -----------------------------------------------------------------------


def test_cohort_masks_information_after_as_of_and_names_gaps(store, selective):
    ready(store)
    ticker = "KXOLD-26OCT11-T1"
    store.upsert("market", ticker, market(ticker), "KXOLD")
    early = datetime.now(timezone.utc)
    time.sleep(0.01)
    store.evidence_page("paper", [paper(1, ticker, closed_at=datetime.now(timezone.utc)
                                        .isoformat(), created_at=early.isoformat())],
                        {"after": 1})
    store.upsert("market", ticker, market(ticker, status="finalized", result="no",
                                          rules_secondary="changed"), "KXOLD")
    page = cohort(store, series="KXOLD", as_of=early.isoformat(), include_rows=True)
    [item] = page["items"]
    assert item["as_of_observation"]["status"] == "active"
    assert item["as_of_observation"]["representation"] == selection.COMPACT
    assert item["rules_changed_after_as_of"] is True
    assert item["current"]["status"] == "finalized"
    group = item["evidence"]["groups"][0]
    assert group["outcomes_after_as_of"] == 1 and group["losses_known_by_as_of"] == 0
    assert item["evidence"]["rows"][0]["pnl"] is None
    assert item["selection"]["selected_now"] and not item["selection"]["selected_by_as_of"]
    for code in ("as_of_observation_compact", "selection_recorded_after_as_of",
                 "market_review_missing_at_as_of", "historical_contract_document_binding_unverified"):
        assert code in item["missing"]
    assert page["selected_sample_is_unbiased_control"] is False
    assert page["prefix_population_complete"] is True
    assert "unfilled_live_orders_not_imported" in page["missing_denominators"]
    later = cohort(store, series="KXOLD", include_rows=True)["items"][0]
    assert later["evidence"]["groups"][0]["losses_known_by_as_of"] == 1
    assert later["evidence"]["rows"][0]["pnl"] == -0.20
    again = cohort(store, series="KXOLD", as_of=early.isoformat(), include_rows=True)
    assert again["page_fingerprint"] == page["page_fingerprint"]
    assert again["cohort_id"] == page["cohort_id"]


def test_cohort_population_filters_pagination_and_validation(store, selective):
    ready(store)
    for i in range(5):
        store.upsert("market", f"KXSET-26OCT11-T{i}", market(f"KXSET-26OCT11-T{i}"), "KXSET")
    store.upsert("market", "KXSETX-26OCT11-T1", market("KXSETX-26OCT11-T1"), "KXSETX")
    store.evidence_page("live", [paper(1, "KXSET-26OCT11-T3")], {"after": 1})
    selected = cohort(store, series="KXSET", population="selected")
    assert [i["market_ticker"] for i in selected["items"]] == ["KXSET-26OCT11-T3"]
    seen, cursor = [], None
    while True:
        page = cohort(store, series="KXSET", population="unselected", cursor=cursor, limit=2)
        seen += [i["market_ticker"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == [f"KXSET-26OCT11-T{i}" for i in (0, 1, 2, 4)]  # KXSETX never leaks in
    for kwargs in ({}, {"series": "KXSET", "population": "winners"},
                   {"series": "KXSET", "as_of": "2026-10-01T00:00:00"},
                   {"series": "KXSET", "as_of": "2099-01-01T00:00:00Z"},
                   {"series": "KXSET", "limit": 500}):
        with pytest.raises(ValueError):
            cohort(store, **kwargs)


# --- evaluation memory ---------------------------------------------------------------------


def test_series_partitioned_evaluation_matches_whole_table(store, tmp_path):
    rows = []
    for i in range(1, 41):
        series = ("KXA", "KXB", "KXC")[i % 3]
        rows.append(paper(i, f"{series}-26OCT{i % 4:02d}-T{i}", experiment_deployment_arm_id=i % 2,
                          assumed_price=50 + i, pnl=(-1) ** i * 0.1 * i))
    store.evidence_page("paper", rows, {"after": 40})
    as_of = datetime(2026, 10, 11, tzinfo=timezone.utc)
    twin = Store(tmp_path / "twin.db")
    twin.evidence_page("paper", copy.deepcopy(rows), {"after": 40})
    assert refresh(store, as_of, partitioned=True) == refresh(twin, as_of, partitioned=False)

    def current(target):
        with target.connect() as db:
            return {r[0]: unpack(r[1]) for r in db.execute(
                "SELECT c.scope,a.document FROM current_assessments c JOIN assessments a "
                "ON a.id=c.assessment_id")}

    assert current(store) == current(twin) and current(store)


def test_unpartitioned_evaluator_still_receives_the_whole_table(store):
    from kalshi_bot.catalog import evaluators

    seen = []

    def wide(records, as_of):
        seen.append(len(list(records)))
        return iter(())

    store.evidence_page("paper", [paper(1, "KXA-E-1"), paper(2, "KXB-E-1")], {"after": 2})
    with patch.dict(evaluators.EVALUATORS, {"wide": wide}), \
            patch.dict(evaluators.PARTITIONS, {"wide": None}):
        refresh(store, datetime(2026, 10, 11, tzinfo=timezone.utc))
    assert seen == [2]


def test_cohort_reports_capture_before_as_of_without_claiming_governing_terms(store):
    url = "https://assets.kalshi.com/contract_terms/KXOLD.pdf"
    ticker = "KXOLD-26OCT11-T1"
    store.upsert("market", ticker, market(ticker, contract_terms_url=url), "KXOLD")
    with store.connect() as db:
        for sha, stamp in (("a" * 64, "2026-10-01T00:00:00+00:00"),
                           ("b" * 64, "2026-10-09T00:00:00+00:00")):
            db.execute("INSERT INTO contract_observations VALUES (?,?,?,?,?)",
                       (sha, url, sha, stamp, json.dumps({"id": sha})))
    page = cohort(store, series="KXOLD", as_of="2026-10-05T00:00:00Z")
    [reference] = [r for r in page["items"][0]["contract_documents"]
                   if r["field"] == "contract_terms_url"]
    assert reference["capture_at_or_before_as_of"] == {
        "content_sha256": "a" * 64, "retrieved_at": "2026-10-01T00:00:00+00:00",
        "governing_terms_proven": False}
    assert reference["version_at_execution_verified"] is False
    early = cohort(store, series="KXOLD", as_of="2026-09-01T00:00:00Z")["items"][0]
    assert early["as_of_observation"] is None and "not_observed_by_as_of" in early["missing"]


def test_legacy_cursors_keep_the_old_reconciliation_clock(store):
    store.set_state("last_reconciliation", time.time() - 3600)
    store.set_state("cursor:paper", {"after": 70, "caught_up_at": datetime.now(timezone.utc)
                                     .isoformat()})
    store.set_state("cursor:live", {"after": 9})
    assert reset_source_reconciliation(store) == []  # a day has not passed on the old clock
    store.set_state("last_reconciliation", time.time() - 90000)
    assert reset_source_reconciliation(store) == ["paper"]  # live has not caught up yet
    assert store.state("cursor:live")["after"] == 9


def test_rows_written_by_an_older_image_reopen_membership_at_startup(store, selective):
    ready(store)
    assert selection.verify_bootstrap(store) is False
    store.evidence_page("paper", [paper(1, "KXNEW-26OCT11-T1")], {"after": 1})
    assert selection.verify_bootstrap(store) is False  # current code indexed and selected it
    with store.connect() as db:  # an older image's positional insert still works
        db.execute("INSERT OR REPLACE INTO evidence VALUES ('live',9,'KXLATE',?)",
                   (json.dumps(paper(9, "KXLATE-26OCT11-T1")),))
    assert selection.verify_bootstrap(store) is True
    store.upsert("market", "KXLATE-26OCT11-T1", market("KXLATE-26OCT11-T1"), "KXLATE")
    assert selection.representation(revisions(store, "KXLATE-26OCT11-T1")[0][1]) == selection.FULL
    ready(store)
    assert selection.verify_bootstrap(store) is False
    reasons = {m["reason"] for m in selection.members(store, ticker="KXLATE-26OCT11-T1")["items"]}
    assert reasons == {"live_evidence"}
