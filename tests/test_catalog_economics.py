import json
import threading
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from kalshi_bot.catalog.economics import SCORING_REQUIREMENTS, live_economics
from kalshi_bot.catalog.evaluators import refresh
from kalshi_bot.catalog.ingest import ROOT, SOURCE_QUERIES, Discovery, DiscoveryDeferred, seed
from kalshi_bot.catalog.service import make_server
from kalshi_bot.catalog.store import SEMANTIC_FIELDS, Store

MOMENT = datetime(2026, 10, 8, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "catalog.sqlite3")


def fill(id=1, action="buy", quantity=10, price=80, **changes):
    return {
        "id": id,
        "kalshi_fill_id": f"exchange-{id}",
        "market_ticker": "KXTEST-E-M",
        "order_market_ticker": "KXTEST-E-M",
        "order_side": "yes",
        "order_action": action,
        "strategy": "mmsell10",
        "experiment_deployment_arm_id": 1,
        "side": "yes",
        "action": action,
        "quantity": quantity,
        "price": price,
        "fee": "99",  # DB estimate must never be used as actual cost.
        "market_owner_count": 1,
        "order_owner_count": 1,
        "market_fill_count": 1,
        "filled_at": None,
        "raw_fill_json": {
            "count_fp": str(quantity),
            "yes_price_dollars": str(price / 100),
            "fee_cost": "0.10",
            "created_time": f"2026-10-01T00:00:0{id}Z",
        },
        **changes,
    }


def settled(store, **changes):
    return store.upsert(
        "market",
        "KXTEST-E-M",
        {
            "status": "settled",
            "market_type": "binary",
            "result": "yes",
            "notional_value_dollars": "1",
            "settlement_value_dollars": "1",
            "settlement_ts": "2026-10-02T00:00:00Z",
            **changes,
        },
        "KXTEST",
    )


def calculate(store, rows):
    return next(live_economics(store, [("live", row) for row in rows], MOMENT))


def test_cashflows_partial_exit_and_remaining_settlement_use_actual_fees(store):
    settled(store)
    rows = [fill(market_fill_count=2), fill(2, "sell", 4, 90, market_fill_count=2)]
    result = calculate(store, rows)
    # Buy 10 at .8; sell 4 at .9; settle 6 at $1; actual fees .2.
    assert result["net_pnl_dollars"] == "1.40"
    assert result["actual_fees_dollars"] == "0.20"
    assert result["remaining_settlement_contracts"] == "6"
    assert result["status"] == "attributed_source_ledger"
    assert result["confidence_score"] is None and not result["qualified"]
    assert not result["exchange_fill_coverage_verified"]


def test_no_side_settlement_and_realized_exit(store):
    settled(store, result="no", settlement_value_dollars="0")
    row = fill(side="no", order_side="no")
    row["raw_fill_json"]["no_price_dollars"] = "0.8"
    assert calculate(store, [row])["net_pnl_dollars"] == "1.90"
    settled(store, result="yes", settlement_value_dollars="1")
    assert calculate(store, [row])["net_pnl_dollars"] == "-8.10"


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"order_market_ticker": "KXOTHER-E-M"}, "order_fill_identity_inconsistent_or_unverified"),
        ({"order_side": "no"}, "order_fill_identity_inconsistent_or_unverified"),
        ({"market_owner_count": 2}, "ownership_ambiguous_or_unverified"),
        ({"order_owner_count": None}, "ownership_ambiguous_or_unverified"),
        ({"market_fill_count": 2}, "source_market_fill_coverage_incomplete"),
        ({"kalshi_fill_id": None}, "exchange_fill_identity_missing_or_duplicate"),
        ({"side": "other"}, "mixed_or_unknown_side"),
        ({"quantity": 0}, "actual_fill_costs_missing_or_invalid"),
        ({"action": "sell"}, "inventory_history_incomplete"),
    ],
)
def test_ambiguous_or_incomplete_ledgers_withhold_pnl(store, change, reason):
    settled(store)
    result = calculate(store, [fill(**change)])
    assert reason in result["blocked_reasons"]
    assert result["net_pnl_dollars"] is None


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("fee_cost", None, "actual_fill_costs_missing_or_invalid"),
        ("fee_cost", "NaN", "actual_fill_costs_missing_or_invalid"),
        ("fee_cost", "-1", "actual_fill_costs_missing_or_invalid"),
        ("count_fp", "10.5", "raw_quantity_missing_or_inconsistent"),
        ("yes_price_dollars", "0.805", "raw_price_missing_or_inconsistent"),
        ("created_time", None, "fill_time_missing_or_future"),
        ("created_time", "2027-01-01T00:00:00Z", "fill_time_missing_or_future"),
        ("created_time", "2026-10-03T00:00:00Z", "fill_after_settlement"),
    ],
)
def test_raw_exchange_evidence_not_rounded_or_estimated_columns(store, field, value, reason):
    settled(store)
    row = fill()
    row["raw_fill_json"][field] = value
    result = calculate(store, [row])
    assert reason in result["blocked_reasons"]
    assert result["net_pnl_dollars"] is None


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"status": "determined"}, "final_settlement_missing"),
        ({"is_provisional": True}, "final_settlement_missing"),
        ({"result": "scalar"}, "unsupported_or_conflicting_settlement"),
        ({"settlement_value_dollars": "0.5"}, "unsupported_or_conflicting_settlement"),
        ({"notional_value_dollars": "10"}, "unsupported_or_conflicting_settlement"),
        ({"settlement_ts": None}, "settlement_time_missing_or_future"),
    ],
)
def test_only_final_supported_settlements_have_pnl(store, change, reason):
    settled(store, **change)
    result = calculate(store, [fill()])
    assert reason in result["blocked_reasons"]
    assert result["net_pnl_dollars"] is None


def test_duplicate_exchange_fill_or_mixed_sides_block(store):
    settled(store)
    first, second = fill(market_fill_count=2), fill(2, market_fill_count=2)
    second["kalshi_fill_id"] = first["kalshi_fill_id"]
    assert (
        "exchange_fill_identity_missing_or_duplicate"
        in calculate(store, [first, second])["blocked_reasons"]
    )
    second["side"] = "no"
    assert "mixed_or_unknown_side" in calculate(store, [first, second])["blocked_reasons"]


def test_registry_migration_preserves_provenance_without_creating_approvals(store):
    manifest = json.loads((ROOT / "registry/series_manifest.json").read_text())["series"]
    seed(store)
    first = store.pipeline_items("review-migrations", 500)
    assert first["total"] == len(manifest)
    assert all(
        r["completion_percent"] == 0 and not r["approved_for_selection"] for r in first["items"]
    )
    assert sum(r["historical_signature_present"] for r in first["items"]) == sum(
        bool(r.get("rules_reviewed_at") and r.get("rules_reviewed_by")) for r in manifest
    )
    seed(store)
    assert store.pipeline_items("review-migrations", 500) == first
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM reviews").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM review_migrations").fetchone()[0] == len(manifest)
    assert store.get("series", manifest[0]["series"])["review_status"] == "needs_review"


def test_projection_upgrade_replays_live_once_without_erasing_initial_coverage(store):
    store.set_state("cursor:live", {"after": 99, "initial_coverage_verified": True})
    seed(store)
    assert store.state("cursor:live")["after"] == 0
    assert store.state("cursor:live")["initial_coverage_verified"]
    store.set_state("cursor:live", {"after": 15})
    seed(store)
    assert store.state("cursor:live")["after"] == 15
    assert "market_fill_count" in SOURCE_QUERIES["live"]


def test_outcome_corrections_refresh_snapshots_and_confidence_stays_withheld(store):
    settled(store)
    store.evidence_page("live", [fill()], {"after": 1})
    refresh(store, MOMENT)
    first = next(
        r for r in store.assessments() if r["evaluator_version"] == "exclusive-binary-ledger-v1"
    )
    assert first["net_pnl_dollars"] == "1.90"
    refresh(store, MOMENT + timedelta(minutes=1))
    assert first["assessment_id"] in {r["assessment_id"] for r in store.assessments()}
    settled(store, result="no", settlement_value_dollars="0")
    refresh(store, MOMENT + timedelta(minutes=2))
    current = next(
        r for r in store.assessments() if r["evaluator_version"] == "exclusive-binary-ledger-v1"
    )
    assert current["net_pnl_dollars"] == "-8.10"
    assert first["assessment_id"] != current["assessment_id"]
    assert not store.assessments(qualified=True)
    assert current["confidence_score"] is None
    assert not current["evidence_bar"]["numeric_requirements_calibrated"]
    assert "minimum_live_days" in SCORING_REQUIREMENTS["mmsell"]


def test_review_updates_are_dependencies_even_without_new_fills(store):
    series = store.upsert("series", "KXTEST", {"rules_primary": "official"})
    settled(store)
    store.evidence_page("live", [fill()], {"after": 1})
    refresh(store, MOMENT)
    before = {r["assessment_id"] for r in store.assessments()}
    semantics = dict.fromkeys(SEMANTIC_FIELDS)
    semantics.update(
        resolution_mechanism="fixed_observation",
        settlement_source="official",
        shared_exposure_key="release-1",
    )
    store.review(
        {
            "kind": "series",
            "ticker": "KXTEST",
            "rules_hash": series["rules_hash"],
            "actor": "reviewer",
            "rationale": "verified official rules",
            "semantics": semantics,
        }
    )
    refresh(store, MOMENT + timedelta(minutes=1))
    assert before.isdisjoint({r["assessment_id"] for r in store.assessments()})
    assert all(r["review_completion_percent"] == 100 for r in store.assessments())
    assert all(r["confidence_score"] is None for r in store.assessments())


def test_assessment_transaction_rolls_back_snapshot_and_fingerprint_on_failure(store):
    def broken():
        yield "scope", {"as_of": MOMENT.isoformat(), "input_fingerprint": "first"}
        raise RuntimeError("interrupted refresh")

    with pytest.raises(RuntimeError):
        store.assessment_batch(broken())
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM assessments").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM current_assessments").fetchone()[0] == 0
    assert store.state("assessment_input:scope") is None


def test_targeted_archived_outcome_refresh_retries_without_skipping_cursor(store):
    store.evidence_page("live", [fill()], {"after": 1})
    calls = []

    def respond(request):
        calls.append(request.url.path)
        if "/historical/" not in request.url.path:
            return httpx.Response(404)
        return httpx.Response(200, json={"market": {"status": "settled", "result": "yes"}})

    client = httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(respond))
    discovery = Discovery(store, client)
    assert discovery.live_outcomes() == 1
    assert calls == ["/markets/KXTEST-E-M", "/historical/markets/KXTEST-E-M"]
    assert store.get("market", "KXTEST-E-M")["raw"]["status"] == "settled"
    discovery.live_outcomes()  # reset completed sweep
    store.set_state("discovery:backoff", {"not_before_unix": 9999999999})
    with pytest.raises(DiscoveryDeferred):
        discovery.live_outcomes()
    assert store.state("discovery:live_outcomes")["after"] == ""
    client.close()


def test_new_pipeline_endpoints_require_auth_and_status_omits_internal_fingerprints(store):
    seed(store)
    store.set_state("assessment_input:private", "fingerprint")
    assert "assessment_input:private" not in store.status()["jobs"]
    token = "a" * 32
    server = make_server(store, token, ("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False
        ) as client:
            for endpoint in ("review-migrations", "live-economics", "scoring-requirements"):
                assert client.get("/v1/" + endpoint).status_code == 401
                response = client.get(
                    "/v1/" + endpoint, headers={"Authorization": "Bearer " + token}
                )
                assert response.status_code == 200
                assert response.json()["advisory_only"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_additional_strategy_has_separate_bar_and_cannot_inherit_confidence(store):
    from kalshi_bot.catalog.evaluators import EVALUATORS, register

    record = {
        "strategy_id": "future",
        "evaluator_version": "v1",
        "strategy_version": "new",
        "series_ticker": "KXTEST",
        "deployment_arm_id": 1,
        "side": "yes",
        "entry_band_cents": None,
        "execution_policy": "own-policy",
        "evidence_source": "live",
        "window": "history",
        "as_of": MOMENT.isoformat(),
        "input_fingerprint": "input",
        "qualified": True,
        "confidence_score": 100,
        "edge_cents_per_contract": 5,
    }
    register("future", lambda records, as_of: iter([dict(record)]), {"version": "future-bar-v1"})
    try:
        refresh(store, MOMENT)
        result = store.assessments(strategy="future")[0]
        assert result["scoring_requirements_version"] == "future-bar-v1"
        assert result["confidence_score"] is None and not result["qualified"]
        assert SCORING_REQUIREMENTS["mmsell"]["version"] == "evidence-bar-v1"
        assert store.assessments(strategy="mmsell") == []
    finally:
        EVALUATORS.pop("future")
        SCORING_REQUIREMENTS.pop("future")


def test_assessment_page_filters_and_counts_before_pagination(store):
    def rows():
        for index in range(6):
            yield (
                str(index),
                {
                    "strategy_id": "mmsell",
                    "series_ticker": "KXTEST",
                    "qualified": False,
                    "edge_cents_per_contract": index,
                    "evidence_source": "live" if index % 2 else "paper",
                    "confidence_score": None,
                    "as_of": MOMENT.isoformat(),
                    "input_fingerprint": str(index),
                },
            )

    store.assessment_batch(rows())
    page = store.assessment_page(source="live", limit=1, offset=1)
    assert page["total"] == 3 and len(page["items"]) == 1
    assert page["items"][0]["edge_cents_per_contract"] == 3
    assert store.assessment_page(source="live", minimum=1)["total"] == 0
    assert store.assessment_page(qualified=True)["total"] == 0
