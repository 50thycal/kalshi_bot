import json
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from kalshi_bot.catalog.economics import (
    SCORING_REQUIREMENTS,
    ledger_assessments,
    ledger_fill,
    live_economics,
)
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


def canonical_fill(id=1, action="buy", quantity=10, price=80, side="no", **changes):
    row = fill(id, action, quantity, price, side=side, order_side=side, **changes)
    outcome = side if action == "buy" else {"yes": "no", "no": "yes"}[side]
    book = {"yes": "bid", "no": "ask"}[outcome]
    yes_price = price / 100 if side == "yes" else 1 - price / 100
    no_price = 1 - yes_price
    row.update(
        kalshi_order_id=f"order-{id}",
        side=outcome,
        action="sell" if book == "ask" else "buy",
        price=round((yes_price if outcome == "yes" else no_price) * 100),
    )
    row["raw_fill_json"].update(
        outcome_side=outcome,
        book_side=book,
        side=row["side"],
        action=row["action"],
        order_id=row["kalshi_order_id"],
        trade_id=row["kalshi_fill_id"],
        ticker=row["market_ticker"],
        yes_price_dollars=f"{yes_price:.4f}",
        no_price_dollars=f"{no_price:.4f}",
        ts=int(
            datetime.fromisoformat(
                row["raw_fill_json"]["created_time"].replace("Z", "+00:00")
            ).timestamp()
        ),
    )
    row["raw_order_json"] = {
        "outcome_side": outcome,
        "book_side": book,
        "side": "yes",
        "action": "sell" if book == "ask" else "buy",
        "order_id": row["kalshi_order_id"],
        "ticker": row["market_ticker"],
    }
    return row


def test_production_canonical_no_entry_is_a_purchase_not_an_inventory_deficit(store):
    settled(store, result="no", settlement_value_dollars="0")
    row = canonical_fill()
    original = json.loads(json.dumps(row))
    result = calculate(store, [row])
    assert result["status"] == "attributed_source_ledger"
    assert result["side"] == "no"
    assert Decimal(result["net_pnl_dollars"]) == Decimal("1.90")
    assert result["buy_contracts"] == "10"
    assert result["method_version"] == "exclusive-binary-ledger-v4"
    assert row == original  # Original legacy labels remain evidence, never rewritten.


def test_canonical_exit_uses_held_leg_price_and_preserves_cashflows(store):
    settled(store, result="no", settlement_value_dollars="0")
    rows = [
        canonical_fill(market_fill_count=2),
        canonical_fill(2, "sell", 4, 90, market_fill_count=2),
    ]
    assert rows[1]["side"] == "yes" and rows[1]["price"] == 10
    result = calculate(store, rows)
    assert result["status"] == "attributed_source_ledger"
    assert Decimal(result["net_pnl_dollars"]) == Decimal("1.40")
    assert result["remaining_settlement_contracts"] == "6"


@pytest.mark.parametrize(
    "side,action", [("yes", "buy"), ("no", "buy"), ("yes", "sell"), ("no", "sell")]
)
def test_canonical_direction_maps_all_four_order_intents(side, action):
    normalized = ledger_fill(canonical_fill(side=side, action=action))
    assert (normalized["side"], normalized["action"]) == (side, action)
    assert float(normalized["price"]) == 80


@pytest.mark.parametrize(
    "payload,field,value",
    [
        ("raw_fill_json", "outcome_side", "yes"),
        ("raw_fill_json", "book_side", "bid"),
        ("raw_fill_json", "outcome_side", None),
        ("raw_fill_json", "order_id", "other"),
        ("raw_fill_json", "trade_id", "other"),
        ("raw_fill_json", "ticker", "other"),
        ("raw_fill_json", "market_ticker", "other"),
        ("raw_fill_json", "side", "yes"),
        ("raw_fill_json", "action", "buy"),
        ("raw_fill_json", "yes_price_dollars", "0.3"),
        ("raw_order_json", "outcome_side", "yes"),
        ("raw_order_json", "order_id", None),
        ("raw_order_json", "ticker", "other"),
    ],
)
def test_canonical_translation_requires_identity_direction_and_exact_prices(
    store, payload, field, value
):
    settled(store)
    row = canonical_fill()
    row[payload][field] = value
    result = calculate(store, [row])
    assert "order_fill_identity_inconsistent_or_unverified" in result["blocked_reasons"]
    assert result["net_pnl_dollars"] is None


def test_legacy_action_mismatch_without_canonical_proof_remains_blocked(store):
    settled(store)
    row = fill()
    row["action"] = "sell"
    assert (
        "order_fill_identity_inconsistent_or_unverified"
        in calculate(store, [row])["blocked_reasons"]
    )


def test_canonical_translation_keeps_loss_fees_and_confidence_guards(store):
    settled(store, result="yes", settlement_value_dollars="1")
    row = canonical_fill()
    result = calculate(store, [row])
    assert Decimal(result["net_pnl_dollars"]) == Decimal("-8.10")
    assert not result["qualified"] and result["confidence_score"] is None
    del row["raw_fill_json"]["fee_cost"]
    result = calculate(store, [row])
    assert "actual_fill_costs_missing_or_invalid" in result["blocked_reasons"]
    assert result["net_pnl_dollars"] is None


@pytest.mark.parametrize("quantity", ["0.01", "0.25", "0.50", "1.25", "1.50", "2.50", "2.75"])
def test_exact_fractional_execution_recovers_source_rounding(store, quantity):
    settled(store, result="no", settlement_value_dollars="0")
    row = canonical_fill(quantity=quantity)
    row["quantity"] = int(round(float(quantity)))
    original = json.loads(json.dumps(row))
    result = calculate(store, [row])
    assert result["status"] == "attributed_source_ledger"
    assert Decimal(result["buy_contracts"]) == Decimal(quantity)
    assert Decimal(result["net_pnl_dollars"]) == Decimal(quantity) * Decimal("0.20") - Decimal(
        "0.10"
    )
    assert result["source_quantity_rounding_restored_fills"] == 1
    assert row == original
    assert not result["qualified"] and result["confidence_score"] is None


def test_fractional_entry_and_exit_keep_exact_inventory_and_fees(store):
    settled(store, result="no", settlement_value_dollars="0")
    rows = [
        canonical_fill(quantity="1.25", market_fill_count=2),
        canonical_fill(2, "sell", "0.50", 90, market_fill_count=2),
    ]
    for row in rows:
        row["quantity"] = int(round(float(row["quantity"])))
    result = calculate(store, rows)
    assert result["status"] == "attributed_source_ledger"
    assert Decimal(result["remaining_settlement_contracts"]) == Decimal("0.75")
    assert Decimal(result["net_pnl_dollars"]) == Decimal("0")
    assert Decimal(result["actual_fees_dollars"]) == Decimal("0.20")


@pytest.mark.parametrize(
    "raw_quantity,stored", [("1.25", 3), ("0", 0), ("-1", -1), ("NaN", 0), ("0.001", 0), (None, 1)]
)
def test_unexplained_or_invalid_raw_quantity_remains_blocked(store, raw_quantity, stored):
    settled(store)
    row = canonical_fill()
    row["quantity"] = stored
    row["raw_fill_json"]["count_fp"] = raw_quantity
    result = calculate(store, [row])
    assert "raw_quantity_missing_or_inconsistent" in result["blocked_reasons"]
    assert result["net_pnl_dollars"] is None


def test_fractional_recovery_does_not_relax_identity_fees_or_ownership(store):
    settled(store)
    row = canonical_fill(quantity="0.25", market_owner_count=2)
    row["quantity"] = 0
    result = calculate(store, [row])
    assert "ownership_ambiguous_or_unverified" in result["blocked_reasons"]
    row["market_owner_count"] = 1
    del row["raw_fill_json"]["fee_cost"]
    result = calculate(store, [row])
    assert "actual_fill_costs_missing_or_invalid" in result["blocked_reasons"]
    row["raw_fill_json"]["order_id"] = "conflicting"
    result = calculate(store, [row])
    assert "order_fill_identity_inconsistent_or_unverified" in result["blocked_reasons"]
    assert result["net_pnl_dollars"] is None


def test_old_projection_replays_raw_order_evidence_once(store):
    store.set_state("live_projection_version", "ownership-v1")
    store.set_state("cursor:live", {"after": 99, "initial_coverage_verified": True})
    seed(store)
    assert store.state("live_projection_version") == "canonical-direction-v2"
    assert store.state("cursor:live")["after"] == 0
    assert not store.state("cursor:live")["reconciliation_complete"]
    store.set_state("cursor:live", {"after": 10})
    seed(store)
    assert store.state("cursor:live")["after"] == 10
    assert "o.raw_order_json AS raw_order_json" in SOURCE_QUERIES["live"]


def exchange_timestamp(row, value):
    row["raw_fill_json"]["created_time"] = value
    row["raw_fill_json"]["ts"] = int(
        datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    )
    return row


def test_collection_after_settlement_does_not_mean_execution_after_settlement(store):
    settled(
        store,
        result="no",
        settlement_value_dollars="0",
        settlement_ts="2026-08-10T20:02:05.469593Z",
    )
    row = exchange_timestamp(
        canonical_fill(filled_at="2026-08-10T20:03:25.875334Z"),
        "2026-08-10T19:59:55.567904Z",
    )
    original = json.loads(json.dumps(row))
    result = calculate(store, [row])
    assert result["status"] == "attributed_source_ledger"
    assert result["first_fill_at"] == "2026-08-10T19:59:55.567904+00:00"
    assert result["source_execution_time_restored_fills"] == 1
    assert result["execution_time_method"] == "verified-exchange-execution-time-v1"
    assert result["exchange_execution_times_verified"]
    assert row == original
    assert not result["qualified"] and result["confidence_score"] is None


def test_exchange_execution_after_settlement_still_blocks(store):
    settled(store)
    row = exchange_timestamp(
        canonical_fill(filled_at="2026-10-03T00:01:00Z"), "2026-10-03T00:00:00Z"
    )
    result = calculate(store, [row])
    assert "fill_after_settlement" in result["blocked_reasons"]
    assert result["net_pnl_dollars"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("created_time", None),
        ("created_time", "invalid"),
        ("created_time", "2026-10-01T00:00:01"),
        ("ts", None),
        ("ts", "NaN"),
        ("ts", 0),
        ("ts", -1),
        ("ts", 1790812801.5),
        ("ts", 1790812801000),
    ],
)
def test_missing_or_conflicting_exchange_time_remains_blocked(store, field, value):
    settled(store)
    row = canonical_fill(filled_at="2026-10-01T00:01:00Z")
    row["raw_fill_json"][field] = value
    result = calculate(store, [row])
    assert "exchange_fill_time_missing_or_inconsistent" in result["blocked_reasons"]
    assert result["net_pnl_dollars"] is None


@pytest.mark.parametrize("collected", ["invalid", "2026-09-30T23:59:00Z"])
def test_collection_before_execution_or_invalid_source_time_remains_blocked(store, collected):
    settled(store)
    result = calculate(store, [canonical_fill(filled_at=collected)])
    assert "exchange_fill_time_missing_or_inconsistent" in result["blocked_reasons"]
    assert result["net_pnl_dollars"] is None


def test_future_collection_is_not_hidden_by_past_execution_time(store):
    settled(store)
    result = calculate(store, [canonical_fill(filled_at="2027-01-01T00:00:00Z")])
    assert "fill_time_missing_or_future" in result["blocked_reasons"]
    assert result["net_pnl_dollars"] is None


def test_exchange_timestamp_preserves_future_execution_guard(store):
    settled(store)
    row = exchange_timestamp(canonical_fill(), "2027-01-01T00:00:00Z")
    result = calculate(store, [row])
    assert "fill_time_missing_or_future" in result["blocked_reasons"]
    assert result["net_pnl_dollars"] is None


def test_execution_order_not_collection_order_drives_inventory(store):
    settled(store, result="no", settlement_value_dollars="0")
    rows = [
        canonical_fill(market_fill_count=2, filled_at="2026-10-01T00:00:05Z"),
        canonical_fill(2, "sell", 4, 90, market_fill_count=2, filled_at="2026-10-01T00:00:04Z"),
    ]
    result = calculate(store, rows)
    assert result["status"] == "attributed_source_ledger"
    assert result["net_pnl_dollars"] == "1.4000"
    assert result["source_execution_time_restored_fills"] == 2


def test_timestamp_repair_never_bypasses_raw_identity_proof(store):
    settled(store)
    row = canonical_fill(filled_at="2026-10-03T00:00:00Z")
    row["raw_fill_json"]["order_id"] = "conflicting"
    result = calculate(store, [row])
    assert "order_fill_identity_inconsistent_or_unverified" in result["blocked_reasons"]
    assert result["source_execution_time_restored_fills"] == 0
    assert result["net_pnl_dollars"] is None


def test_attributed_duration_uses_exchange_times_not_recollection_span(store):
    settled(store, settlement_ts="2026-10-05T00:00:00Z")
    documents = []
    for index, executed in enumerate(["2026-10-01T00:00:01Z", "2026-10-03T00:00:01Z"]):
        ticker = f"KXTEST-E-{index}"
        store.upsert("market", ticker, store.get("market", "KXTEST-E-M")["raw"], "KXTEST")
        row = exchange_timestamp(
            canonical_fill(
                market_ticker=ticker, order_market_ticker=ticker, filled_at="2026-10-06T00:00:00Z"
            ),
            executed,
        )
        documents.append(calculate(store, [row]))
    assessment = next(ledger_assessments(documents, MOMENT))
    assert assessment["observation_span_days"] == 2
    assert assessment["active_days"] == 2
    assert assessment["exchange_execution_times_verified"]
    assert not assessment["qualified"] and assessment["confidence_score"] is None


def test_legacy_time_fallback_is_not_claimed_as_verified_exchange_time(store):
    settled(store)
    result = calculate(store, [fill()])
    assert result["status"] == "attributed_source_ledger"
    assert not result["exchange_execution_times_verified"]
    assert result["source_execution_time_restored_fills"] == 0
    assessment = next(ledger_assessments([result], MOMENT))
    assert not assessment["exchange_execution_times_verified"]


def test_timezone_offset_is_normalized_without_losing_execution_precision(store):
    settled(store)
    row = exchange_timestamp(
        canonical_fill(filled_at="2026-10-01T00:01:00Z"), "2026-09-30T19:00:01.123456-05:00"
    )
    result = calculate(store, [row])
    assert result["first_fill_at"] == "2026-10-01T00:00:01.123456+00:00"
    assert result["status"] == "attributed_source_ledger"


def test_exception_diagnostics_emit_only_bounded_public_market_samples(store, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="market_catalog")
    records = []
    for index in range(8):
        ticker = f"KXTEST-E-{index}"
        store.upsert(
            "market",
            ticker,
            {
                "status": "settled",
                "market_type": "binary",
                "result": "yes",
                "notional_value_dollars": "1",
                "settlement_ts": "2026-10-02T00:00:00Z",
            },
            "KXTEST",
        )
        row = fill(id=index + 1, market_ticker=ticker, order_market_ticker=ticker)
        row["raw_fill_json"]["created_time"] = "2026-10-03T00:00:00Z"
        records.append(row)
    store.evidence_page("live", records, {"after": 8})
    refresh(store, MOMENT)
    message = next(
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("catalog_evidence_exception_markets=")
    )
    sample = json.loads(message.split("=", 1)[1])
    assert len(sample["fill_after_settlement"]) == 5
    assert all(t.startswith("KXTEST-E-") for t in sample["fill_after_settlement"])
    assert "exchange-" not in message and "net_pnl" not in message


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
        r for r in store.assessments() if r["evaluator_version"] == "exclusive-binary-ledger-v4"
    )
    assert first["net_pnl_dollars"] == "1.90"
    refresh(store, MOMENT + timedelta(minutes=1))
    assert first["assessment_id"] in {r["assessment_id"] for r in store.assessments()}
    settled(store, result="no", settlement_value_dollars="0")
    refresh(store, MOMENT + timedelta(minutes=2))
    current = next(
        r for r in store.assessments() if r["evaluator_version"] == "exclusive-binary-ledger-v4"
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
        assert SCORING_REQUIREMENTS["mmsell"]["version"] == "evidence-bar-v2"
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


@pytest.mark.parametrize("status", ["settled", "finalized"])
def test_final_lifecycle_aliases_attribute_but_provisional_and_missing_timestamp_block(
    store, status
):
    settled(store, status=status)
    assert calculate(store, [fill()])["net_pnl_dollars"] == "1.90"
    settled(store, status=status, is_provisional=True)
    assert "final_settlement_missing" in calculate(store, [fill()])["blocked_reasons"]
    settled(store, status=status, settlement_ts=None)
    assert "settlement_time_missing_or_future" in calculate(store, [fill()])["blocked_reasons"]


def test_sanitized_summary_logs_expose_blockers_without_cost_payloads(store, caplog):
    import logging

    settled(store, status="finalized")
    row = fill()
    row["raw_fill_json"].pop("fee_cost")
    store.evidence_page("live", [row], {"after": 1})
    with caplog.at_level(logging.INFO, logger="market_catalog"):
        refresh(store, MOMENT)
        seed(store)
    messages = [r.getMessage() for r in caplog.records if r.name == "market_catalog"]
    economics = json.loads(
        next(m.split("=", 1)[1] for m in messages if m.startswith("catalog_live_economics="))
    )
    assert economics["blocked_reason_counts"]["actual_fill_costs_missing_or_invalid"] == 1
    assert economics["attributed"] == 0 and economics["blocked"] == 1
    assert all(
        "fee_cost" not in m and "net_pnl_dollars" not in m and "raw_fill_json" not in m
        for m in messages
    )
    assert any(m.startswith("catalog_review_migration=") for m in messages)
