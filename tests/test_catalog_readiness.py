import threading

import httpx
import pytest

from kalshi_bot.catalog.economics import SCORING_REQUIREMENTS
from kalshi_bot.catalog.ingest import seed
from kalshi_bot.catalog.readiness import (
    CHECK_PROVIDERS,
    PROVIDERS,
    calibration_inputs,
    register_readiness,
    review_packets,
)
from kalshi_bot.catalog.service import make_server
from kalshi_bot.catalog.store import SEMANTIC_FIELDS, Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "catalog.db")


def ledger(ticker="KXTEST-E-A", **changes):
    return {
        "market_ticker": ticker,
        "series_ticker": ticker.split("-", 1)[0],
        "strategy_version": "mmsell1",
        "deployment_arm_id": "arm-1",
        "side": "no",
        "status": "attributed_source_ledger",
        "source_fill_count": 1,
        "blocked_reasons": [],
        "first_fill_at": "2026-01-01T00:00:00+00:00",
        "last_fill_at": "2026-01-02T00:00:00+00:00",
        "exchange_execution_times_verified": True,
        "actual_fees_dollars": "0.01",
        "net_pnl_dollars": "0.29",
        "input_fingerprint": ticker,
        **changes,
    }


def listing(store, ticker="KXTEST-E-A"):
    series = ticker.split("-", 1)[0]
    store.upsert("series", series, {"contract_url": "https://example.test/rules"})
    return store.upsert(
        "market",
        ticker,
        {
            "rules_primary": "Exact official criteria",
            "rules_secondary": "Exceptions",
            "event_ticker": "KXTEST-E",
        },
        series,
    )


def approve(store, kind, ticker):
    document = store.get(kind, ticker)
    return store.review(
        {
            "kind": kind,
            "ticker": ticker,
            "rules_hash": document["rules_hash"],
            "actor": "fixture-reviewer",
            "rationale": "Fixture reviewed source",
            "semantics": {
                **dict.fromkeys(SEMANTIC_FIELDS),
                "resolution_mechanism": "fixed_observation",
                "settlement_source": "official",
                "shared_exposure_key": "release-E",
            },
        }
    )


def test_registry_provenance_does_not_approve_semantics_or_confidence(store):
    seed(store)
    packets = review_packets(store, limit=50)
    assert packets["total"] == 140
    assert len(packets["items"]) == 50
    assert all(p["series_review"]["known_field_count"] == 0 for p in packets["items"])
    assert all(p["legacy_is_semantic_approval"] is False for p in packets["items"])
    assert packets["qualified"] is False and packets["confidence_score"] is None
    assert len(review_packets(store, offset=100, limit=50)["items"]) == 40


def test_priority_context_isolation_unknown_owner_and_verified_duration(store):
    store.migrate_review({"series_ticker": "KXLEGACY", "historical_signature_present": True})
    records = [
        ledger(),
        ledger("KXTEST-E-B", deployment_arm_id="arm-2"),
        ledger(
            "KXTEST-E-C",
            exchange_execution_times_verified=False,
            first_fill_at="2020-01-01T00:00:00+00:00",
        ),
        ledger("KXTEST-E-D", strategy_version=None),
        ledger("KXTEST-E-F", strategy_version=None),
        ledger("KXOTHER-E-A", side="yes"),
    ]
    store.save_live_economics(records)
    data = review_packets(store)
    assert [p["series_ticker"] for p in data["items"]] == ["KXTEST", "KXOTHER", "KXLEGACY"]
    contexts = data["items"][0]["contexts"]
    assert len(contexts) == 4  # unknown owners never pooled together
    arm1 = next(
        c
        for c in contexts
        if c["strategy_version"] == "mmsell1" and c["deployment_arm_id"] == "arm-1"
    )
    assert arm1["live_markets"] == 2 and arm1["verified_clock_markets"] == 1
    assert arm1["verified_execution_span_days"] == 1
    assert not arm1["canonical_epoch_verified"]


def test_packets_current_hash_partial_review_and_parent_invalidation(store):
    document = listing(store)
    store.save_live_economics([ledger()])
    approve(store, "series", "KXTEST")
    approve(store, "market", document["ticker"])
    packet = review_packets(store)["items"][0]
    facts = packet["series_review"]
    assert facts["review_status"] == "reviewed"
    assert facts["known_field_count"] == 3 and facts["known_fields_percent"] == 30
    assert len(facts["missing_fields"]) == 7  # explicit null is still unknown
    sample = packet["sample_markets"][0]["review"]
    assert sample["rules_hash"] == document["rules_hash"]
    assert sample["rules"]["rules_primary"] == "Exact official criteria"
    before = calibration_inputs(store)["items"][0]
    assert before["requirements_met"]["current_semantic_review"]
    store.upsert("series", "KXTEST", {"contract_url": "https://example.test/changed"})
    after = calibration_inputs(store)["items"][0]
    assert after["market_review"]["parent_rules_changed"]
    assert after["market_review"]["review_id"] is None
    assert not after["requirements_met"]["current_semantic_review"]
    assert before["input_id"] != after["input_id"]


def test_calibration_retains_blocked_outcomes_without_independence_or_forward_claim(store):
    listing(store)
    store.save_live_economics(
        [
            ledger(),
            ledger(
                "KXTEST-E-B",
                status="blocked",
                actual_fees_dollars=None,
                blocked_reasons=["final_settlement_missing"],
            ),
        ]
    )
    store.set_state("source_coverage:live", {"ids_match": True, "checked_at": "yesterday"})
    data = calibration_inputs(store)
    assert data["total"] == 2
    assert {r["ledger"]["status"] for r in data["items"]} == {"blocked", "attributed_source_ledger"}
    assert data["items"][0]["candidate_event_group"] == "KXTEST-E"
    for item in data["items"]:
        assert item["partition"] == "unassigned"
        assert not item["candidate_group_verified_independent"]
        assert item["requirements_met"]["verified_source_ids"]
        assert not item["requirements_met"]["verified_exchange_fill_coverage"]
        assert not item["requirements_met"]["known_strategy_lineage"]
        assert not item["requirements_met"]["forward_validation"]
        assert item["confidence_score"] is None and not item["qualified"]
    assert not data["snapshot_frozen"] and not data["opportunity_coverage_verified"]
    assert data["forward_boundary"] is None
    assert data["requirements"]["minimum_live_days"] is None


def test_input_lineage_changes_with_review_and_ledger_not_wall_clock(store):
    listing(store)
    store.save_live_economics([ledger()])
    first = calibration_inputs(store)["items"][0]["input_id"]
    assert calibration_inputs(store)["items"][0]["input_id"] == first
    approve(store, "series", "KXTEST")
    second = calibration_inputs(store)["items"][0]["input_id"]
    assert second != first
    store.save_live_economics([ledger(net_pnl_dollars="0.15")])
    assert calibration_inputs(store)["items"][0]["input_id"] != second


def test_future_strategy_must_supply_own_provider_and_bar(store, monkeypatch):
    with pytest.raises(ValueError):
        calibration_inputs(store, strategy="future")
    with pytest.raises(ValueError):
        register_readiness("future", lambda *_: [])
    monkeypatch.setitem(
        SCORING_REQUIREMENTS, "future", {"version": "future-v1", "required": ["custom_evidence"]}
    )
    monkeypatch.setattr("kalshi_bot.catalog.readiness.PROVIDERS", dict(PROVIDERS))
    monkeypatch.setattr("kalshi_bot.catalog.readiness.CHECK_PROVIDERS", dict(CHECK_PROVIDERS))
    register_readiness("future", lambda *_: [{"record_id": "immutable-future", **ledger()}])
    data = calibration_inputs(store, "future")
    assert data["requirements"]["version"] == "future-v1"
    assert data["items"][0]["requirements_met"] == {"custom_evidence": False}
    assert "minimum_live_days" not in data["requirements"]
    # A truthy count is not an explicit proof, and even a proven custom check does
    # not turn this preparation API into a qualification path.
    from kalshi_bot.catalog import readiness

    readiness.CHECK_PROVIDERS["future"] = lambda *_: {"custom_evidence": 1}
    assert not calibration_inputs(store, "future")["items"][0]["requirements_met"][
        "custom_evidence"
    ]
    readiness.CHECK_PROVIDERS["future"] = lambda *_: {"custom_evidence": True}
    assert calibration_inputs(store, "future")["items"][0]["requirements_met"]["custom_evidence"]
    assert not calibration_inputs(store, "future")["items"][0]["qualified"]
    with pytest.raises(ValueError):
        register_readiness("future", lambda *_: [])


def test_readiness_reads_only_requested_listings_and_preserves_history(store, monkeypatch):
    records = [ledger(f"KXTEST-E-{i:03}") for i in range(100)]
    store.save_live_economics(records)
    calls, original = [], store.get

    def tracked(kind, ticker):
        calls.append((kind, ticker))
        return original(kind, ticker)

    monkeypatch.setattr(store, "get", tracked)
    monkeypatch.setattr(
        store, "evidence", lambda *_: pytest.fail("must not load all source evidence")
    )
    data = calibration_inputs(store, offset=95, limit=2)
    assert data["total"] == 100 and len(data["items"]) == 2
    assert len(calls) == 4
    calls.clear()
    packets = review_packets(store, limit=1)
    assert len(packets["items"][0]["sample_markets"]) == 3
    assert len(calls) == 4
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM live_economics").fetchone()[0] == 100
        assert db.execute("SELECT count(*) FROM reviews").fetchone()[0] == 0


def test_many_unresolved_contexts_have_explicit_output_truncation(store):
    store.save_live_economics(
        [ledger(f"KXTEST-E-{i:03}", strategy_version=None) for i in range(60)]
    )
    packet = review_packets(store)["items"][0]
    assert packet["live_markets"] == 60
    assert packet["contexts_total"] == 60 and packet["contexts_truncated"]
    assert len(packet["contexts"]) == 50
    assert calibration_inputs(store, offset=50)["total"] == 60


def test_api_auth_filters_bounds_and_empty_page(store):
    store.save_live_economics([ledger(), ledger("KXOTHER-E-A")])
    server = make_server(store, "a" * 32, ("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False
        ) as client:
            for path in ("review-packets", "calibration-inputs"):
                assert client.get("/v1/" + path).status_code == 401
            client.headers["Authorization"] = "Bearer " + "a" * 32
            for path in ("review-packets", "calibration-inputs"):
                response = client.get("/v1/" + path + "?series=KXTEST&limit=500")
                assert response.status_code == 200
                assert response.json()["total"] == 1 and response.json()["limit"] == 50
                assert client.get("/v1/" + path + "?strategy=unregistered").status_code == 400
                assert client.get("/v1/" + path + "?limit=oops").status_code == 400
                assert client.get("/v1/" + path + "?offset=100").json()["items"] == []
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.parametrize("limit,offset", [(0, 0), (51, 0), (1, -1)])
def test_library_bounds(store, limit, offset):
    with pytest.raises(ValueError):
        review_packets(store, limit=limit, offset=offset)
