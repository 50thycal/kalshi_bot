import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest

from kalshi_bot.catalog.evaluators import EVALUATORS, mmsell, refresh, register
from kalshi_bot.catalog.ingest import (
    SOURCE_COVERAGE_QUERIES,
    Discovery,
    DiscoveryDeferred,
    seed,
    source_page,
)
from kalshi_bot.catalog.service import make_server, run_job
from kalshi_bot.catalog.store import (
    DOCUMENT_TABLES,
    SEMANTIC_FIELDS,
    Store,
    digest,
    encode,
    pack,
    unpack,
)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "catalog.db")


def review_payload(doc, template=False):
    semantics = dict.fromkeys(SEMANTIC_FIELDS)
    semantics.update(
        resolution_mechanism="fixed_observation",
        settlement_source="official",
        shared_exposure_key="macro-release",
    )
    return dict(
        kind=doc["kind"],
        ticker=doc["ticker"],
        rules_hash=doc["rules_hash"],
        actor="test-reviewer",
        rationale="read official rules",
        semantics=semantics,
        template=template,
    )


def trade(id=1, **changes):
    return {
        "id": id,
        "market_ticker": "KXTEST-EVENT-RUNG",
        "strategy": "mmsell10",
        "side": "no",
        "assumed_price": 90,
        "quantity": 2,
        "pnl": 0.20,
        "fees": 0.01,
        "status": "settled",
        "created_at": "2026-10-01T00:00:00+00:00",
        "fill_assumption": "maker",
        "experiment_deployment_arm_id": 1,
        **changes,
    }


def test_registry_is_provenance_not_permission(store):
    seed(store)
    assert store.status()["objects"]["series"] == 140
    doc = store.get("series", "KXTRUMPSAY")
    assert doc["legacy_registry"]["rules_reviewed_at"]
    assert doc["review_status"] == "needs_review"
    assert doc["semantics"]["observation_start"] is None


def test_idempotent_import_replay_and_persistent_cursor(store):
    store.evidence_page("paper", [trade()], {"after": 1})
    store.evidence_page("paper", [trade(pnl=-1)], {"after": 1})
    reopened = Store(store.path)
    assert reopened.status()["evidence"]["paper"] == 1
    assert reopened.evidence()[0][1]["pnl"] == -1
    assert reopened.state("cursor:paper") == {"after": 1}


def test_rules_change_invalidates_review_without_erasing_history(store):
    doc = store.upsert("market", "KXTEST-E-M", {"rules_primary": "old"}, "KXTEST")
    store.review(review_payload(doc))
    assert store.get("market", doc["ticker"])["review_status"] == "reviewed"
    store.upsert("market", doc["ticker"], {"rules_primary": "new"}, "KXTEST")
    assert store.get("market", doc["ticker"])["review_status"] == "needs_review"
    assert len(store.history("revisions", "market", doc["ticker"])) == 2
    assert len(store.history("reviews", "market", doc["ticker"])) == 1
    with pytest.raises(LookupError):
        store.review(review_payload(doc))


def test_template_exact_match_same_series_only(store):
    raw = {"rules_primary": "fixed criteria", "close_time": "2026-11-01T00:00:00Z"}
    doc = store.upsert("market", "KXTEST-E-M1", raw, "KXTEST")
    store.review(review_payload(doc, template=True))
    store.upsert("market", "KXTEST-E-M2", raw, "KXTEST")
    assert store.get("market", "KXTEST-E-M2")["review_status"] == "reviewed"
    store.upsert("market", "KXTEST-E-M3", {**raw, "close_time": "2026-12-01T00:00:00Z"}, "KXTEST")
    store.upsert("market", "KXOTHER-E-M", raw, "KXOTHER")
    assert store.get("market", "KXTEST-E-M3")["review_status"] == "needs_review"
    assert store.get("market", "KXOTHER-E-M")["review_status"] == "needs_review"


def test_paper_never_acquires_live_maturity_and_twins_excluded():
    records = [("paper", trade(i)) for i in range(100)]
    records.append(("paper", trade(101, is_twin=True, strategy="mmsell10_twin")))
    results = list(mmsell(records, datetime(2026, 10, 5, tzinfo=timezone.utc)))
    assert len(results) == 2
    assert all(r["executions"] == 100 and r["maturity"] == "paper_provisional" for r in results)
    assert all(r["confidence_score"] is None and not r["qualified"] for r in results)
    assert results[0]["provisional_event_groups"] == 1
    assert results[0]["edge_cents_per_contract"] == pytest.approx(10)


def test_contexts_and_recent_history_do_not_pool():
    records = [
        ("paper", trade()),
        ("paper", trade(2, experiment_deployment_arm_id=2)),
        ("paper", trade(3, side="yes")),
        ("paper", trade(4, assumed_price=70)),
        ("paper", trade(5, fill_assumption="taker")),
        ("paper", trade(6, created_at="2026-01-01T00:00:00Z")),
        ("live", trade(7, price=91, action="buy", filled_at="2026-10-01T00:00:00Z")),
    ]
    results = list(mmsell(records, datetime(2026, 10, 5, tzinfo=timezone.utc)))
    assert len(results) == 12
    history = next(
        r
        for r in results
        if r["deployment_arm_id"] == 1
        and r["entry_band_cents"][0] == 90
        and r["side"] == "no"
        and r["execution_policy"] == "maker"
        and r["window"] == "history"
    )
    assert history["executions"] == 2
    live = next(r for r in results if r["evidence_source"] == "live")
    assert live["edge_cents_per_contract"] is None
    assert "attributed_live_net_pnl_missing" in live["qualification_reasons"]


def test_future_strategy_is_separate_and_snapshots_immutable(store):
    store.evidence_page("paper", [trade()], {"after": 1})
    moment = datetime(2026, 10, 5, tzinfo=timezone.utc)
    refresh(store, moment)
    ids = {r["assessment_id"] for r in store.assessments()}
    refresh(store, moment + timedelta(minutes=1))
    assert {r["assessment_id"] for r in store.assessments()} == ids
    register("test_future", lambda records, as_of: iter(()))
    try:
        assert "test_future" in EVALUATORS
        assert store.assessments(strategy="test_future") == []
        assert store.assessments(qualified=True) == []
    finally:
        EVALUATORS.pop("test_future")


def test_source_read_only_and_cursor_not_advanced_on_failure(store):
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchall.return_value = [trade()]
    conn.execute.return_value.fetchone.side_effect = [
        dict(
            rolsuper=False,
            rolcreatedb=False,
            rolcreaterole=False,
            rolreplication=False,
            rolbypassrls=False,
        ),
        {"writable": False},
    ]
    with patch("kalshi_bot.catalog.ingest.psycopg.connect", return_value=conn) as connect:
        assert source_page(store, "postgresql://private/source", "paper") == 1
        assert conn.read_only is True
        assert "default_transaction_read_only=on" in connect.call_args.kwargs["options"]
        assert conn.execute.call_args.args[0].lstrip().startswith("SELECT")
    with patch("kalshi_bot.catalog.ingest.psycopg.connect", side_effect=RuntimeError("secret")):
        assert run_job(store, "import:paper", lambda: source_page(store, "secret", "paper")) is None
    assert store.state("cursor:paper")["after"] == 1
    assert "secret" not in json.dumps(store.status())


def test_paginated_discovery_restart_and_no_duplicate_objects(store):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if "cursor=page2" in str(request.url):
            return httpx.Response(200, json={"events": [], "cursor": ""})
        return httpx.Response(
            200,
            json={
                "events": [
                    {
                        "event_ticker": "KXTEST-E",
                        "series_ticker": "KXTEST",
                        "markets": [{"ticker": "KXTEST-E-M", "rules_primary": "r"}],
                    }
                ],
                "cursor": "page2",
            },
        )

    with httpx.Client(base_url="https://test", transport=httpx.MockTransport(handler)) as client:
        discovery = Discovery(store, client)
        assert discovery.page("events") == 1
        discovery = Discovery(Store(store.path), client)
        assert discovery.page("events") == 0
        discovery.reset()
        assert discovery.page("events") == 1
    assert store.status()["objects"] == {"event": 1, "market": 1}
    assert "cursor=page2" in calls[1]


def test_http_auth_review_stale_conflict_and_selection(store):
    doc = store.upsert("market", "KXTEST-E-M", {"rules_primary": "old"}, "KXTEST")
    server = make_server(store, "a" * 32, ("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False
        ) as client:
            assert client.get("/health").status_code == 200
            assert client.get("/v1/status").status_code == 401
            client.headers["Authorization"] = "Bearer " + "a" * 32
            assert client.get("/v1/status").status_code == 200
            assert client.get("/v1/markets?limit=oops").status_code == 400
            assert client.post("/v1/reviews", json=review_payload(doc)).status_code == 201
            store.upsert("market", doc["ticker"], {"rules_primary": "changed"}, "KXTEST")
            assert client.post("/v1/reviews", json=review_payload(doc)).status_code == 409
            assert client.get("/v1/select?qualified=true").json()["items"] == []
            payload = {"source": "paper", "records": [trade()], "provenance": "read-only fixture"}
            assert client.post("/v1/import", json=payload).status_code == 201
            assert client.post("/v1/import", json=payload).status_code == 201
            assert store.status()["evidence"]["paper"] == 1
            assert store.state("cursor:paper")["after"] == 0
            assert store.state("evaluation_requested")
            assert client.get("/v1/select?min_confidence=101").status_code == 400
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_parent_rules_change_invalidates_market_review(store):
    store.upsert("series", "KXTEST", {"contract_url": "old"})
    doc = store.upsert("market", "KXTEST-E-M", {"rules_primary": "criteria"}, "KXTEST")
    store.review(review_payload(doc))
    store.upsert("series", "KXTEST", {"contract_url": "new"})
    current = store.get("market", doc["ticker"])
    assert current["parent_rules_changed"] and current["review_status"] == "needs_review"
    with pytest.raises(LookupError):
        store.review(review_payload(doc))


def test_recent_window_expires_but_snapshot_survives(store):
    store.evidence_page("paper", [trade()], {"after": 1})
    refresh(store, datetime(2026, 10, 5, tzinfo=timezone.utc))
    old = store.assessments()
    refresh(store, datetime(2026, 12, 5, tzinfo=timezone.utc))
    assert len(store.assessments()) == 1
    assert store.assessments()[0]["window"] == "history"
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM assessments").fetchone()[0] == len(old)


def test_quote_changes_update_current_without_archiving_deep_telemetry(store):
    raw = {"rules_primary": "criteria", "yes_bid_dollars": "0.10"}
    store.upsert("market", "KXTEST-E-M", raw, "KXTEST")
    store.upsert("market", "KXTEST-E-M", {**raw, "yes_bid_dollars": "0.11"}, "KXTEST")
    assert len(store.history("revisions", "market", "KXTEST-E-M")) == 1
    assert store.get("market", "KXTEST-E-M")["raw"]["yes_bid_dollars"] == "0.11"


def test_admin_source_role_is_refused_before_import(store):
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchone.side_effect = [{"rolsuper": True}, {"writable": True}]
    with patch("kalshi_bot.catalog.ingest.psycopg.connect", return_value=conn):
        with pytest.raises(PermissionError):
            source_page(store, "postgresql://private/source", "paper")
    assert store.evidence() == []
    assert store.state("cursor:paper") is None
    permissions = store.state("source_permissions")
    assert permissions["elevated_flags"] == ["rolsuper"]
    assert permissions["public_table_write_privileges"]
    assert not permissions["accepted"]
    assert "private" not in json.dumps(permissions)


def test_write_grants_without_elevated_role_still_refused(store):
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchone.side_effect = [{"rolsuper": False}, {"writable": True}]
    with patch("kalshi_bot.catalog.ingest.psycopg.connect", return_value=conn):
        with pytest.raises(PermissionError):
            source_page(store, "postgresql://private/source", "paper")
    assert not store.state("source_permissions")["accepted"]
    assert conn.execute.call_count == 2  # No trading data read after refusal.


def coverage(ids, through=3):
    return {
        "source_records": len(ids),
        "source_ids_fingerprint": hashlib.sha256(
            ",".join(str(i) for i in sorted(ids)).encode()
        ).hexdigest(),
        "through_source_id": through,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "definition": "sorted-source-ids-sha256-v1",
    }


def test_backfill_requires_matching_ids_not_just_counts(store):
    store.evidence_page("paper", [trade(1), trade(3)], {"after": 3, "initial_complete": False})
    result = store.evidence_page("paper", [], store.state("cursor:paper"), coverage([1, 2]))
    assert result["source_records"] == result["local_records"] == 2
    assert not result["ids_match"]
    assert not store.state("cursor:paper")["initial_complete"]
    assert not store.state("cursor:paper")["reconciliation_complete"]
    assert store.state("cursor:paper").get("last_complete_at") is None
    assert store.status()["backfill"]["paper"]["coverage"]["ids_match"] is False
    # A fully matching coverage checkpoint is durable, but is not a confidence score.
    result = store.evidence_page("paper", [], store.state("cursor:paper"), coverage([1, 3]))
    reopened = Store(store.path)
    assert result["ids_match"] and not result["payload_parity_verified"]
    assert reopened.status()["backfill"]["paper"]["initial_complete"]
    assert reopened.state("cursor:paper")["last_complete_at"] == result["checked_at"]
    refresh(reopened, datetime(2026, 10, 5, tzinfo=timezone.utc))
    assert all(r["confidence_score"] is None and not r["qualified"] for r in reopened.assessments())


def test_coverage_checkpoint_and_cursor_rollback_together(store):
    cursor = {"after": 1, "initial_complete": False}
    store.evidence_page("paper", [trade()], cursor)
    with store.connect() as db:
        db.execute("""CREATE TRIGGER refuse_cursor BEFORE INSERT ON state
                      WHEN NEW.key='cursor:paper'
                      BEGIN SELECT RAISE(ABORT, 'test interruption'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        store.evidence_page("paper", [], cursor, coverage([1], through=1))
    assert store.state("source_coverage:paper") is None
    assert store.state("cursor:paper") == cursor


def test_eof_audits_read_only_source_and_throttles_repeat_checks(store):
    store.evidence_page("paper", [trade()], {"after": 1, "initial_complete": False})
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchall.return_value = []
    source = coverage([1], through=1)
    conn.execute.return_value.fetchone.side_effect = [
        {"rolsuper": False},
        {"writable": False},
        {k: source[k] for k in ("source_records", "source_ids_fingerprint")},
    ]
    with patch("kalshi_bot.catalog.ingest.psycopg.connect", return_value=conn):
        assert source_page(store, "postgresql://private/source", "paper") == 0
    assert conn.execute.call_args.args == (SOURCE_COVERAGE_QUERIES["paper"], (1,))
    assert conn.read_only and store.state("cursor:paper")["initial_complete"]
    conn.reset_mock()
    conn.execute.return_value.fetchall.return_value = []
    conn.execute.return_value.fetchone.side_effect = [{"rolsuper": False}, {"writable": False}]
    with patch("kalshi_bot.catalog.ingest.psycopg.connect", return_value=conn):
        source_page(store, "postgresql://private/source", "paper")
    assert conn.execute.call_count == 3  # Privileges + page; no repeated whole-source hash.


def test_empty_source_is_complete_only_after_audit(store):
    assert not store.status()["backfill"]["live"]["initial_complete"]
    result = store.evidence_page("live", [], {"after": 0}, coverage([], through=0))
    assert result["ids_match"]
    assert store.status()["backfill"]["live"]["initial_complete"]


def test_legacy_eof_flag_does_not_imply_verified_coverage(store):
    store.set_state("cursor:paper", {"after": 3, "initial_complete": True})
    store.evidence_page("paper", [trade(1), trade(3)], store.state("cursor:paper"))
    assert not store.status()["backfill"]["paper"]["initial_complete"]
    store.evidence_page("paper", [], store.state("cursor:paper"), coverage([1, 2]))
    assert not store.state("cursor:paper")["initial_complete"]


def test_compression_preserves_legacy_documents_hashes_and_restart(store):
    raw = {"rules_primary": "Repeated official rules with Unicode: café. " * 100}
    doc = store.upsert("market", "KXTEST-E-M", raw, "KXTEST")
    payload = review_payload(doc)
    payload["rationale"] *= 150
    review = store.review(payload)
    store.evidence_page("paper", [trade(notes="historical context " * 200)], {"after": 1})
    refresh(store, datetime(2026, 10, 5, tzinfo=timezone.utc))
    # Construct an actual original-format database to exercise the upgrade path.
    with store.connect() as db:
        for table in DOCUMENT_TABLES:
            for row in db.execute(f"SELECT rowid,document FROM {table}").fetchall():
                db.execute(
                    f"UPDATE {table} SET document=? WHERE rowid=?",
                    (encode(unpack(row[1])), row[0]),
                )
    before_docs = {}
    with store.connect() as db:
        for table in DOCUMENT_TABLES:
            before_docs[table] = [
                (row[0], unpack(row[1]))
                for row in db.execute(f"SELECT rowid,document FROM {table} ORDER BY rowid")
            ]
    before_bytes = sum(t["document_bytes"] for t in store.storage()["tables"].values())
    assert store.compress_page(batch_size=1) == 1
    reopened = Store(store.path)
    while not reopened.state("storage:compression")["complete"]:
        reopened.compress_page(batch_size=1)
    with reopened.connect() as db:
        for table in DOCUMENT_TABLES:
            after = [
                (row[0], unpack(row[1]))
                for row in db.execute(f"SELECT rowid,document FROM {table} ORDER BY rowid")
            ]
            assert after == before_docs[table]
    after_bytes = sum(t["document_bytes"] for t in reopened.storage()["tables"].values())
    assert after_bytes < before_bytes / 2
    assert reopened.get("market", doc["ticker"])["raw"] == raw
    assert reopened.list_objects("market")[0]["rules_hash"] == doc["rules_hash"]
    assert reopened.history("reviews", "market", doc["ticker"])[0] == review
    assert digest({k: v for k, v in review.items() if k != "id"}) == review["id"]
    assert reopened.evidence()[0][1]["notes"] == "historical context " * 200
    assert reopened.assessments() == store.assessments()
    assert reopened.compress_page() == 0


def test_compression_failure_rolls_back_page_and_cursor(store):
    doc = store.upsert("market", "KXTEST-E-M", {"rules_primary": "rules " * 1000}, "KXTEST")
    with store.connect() as db:
        db.execute("UPDATE objects SET document=?", (encode(doc),))
        db.execute("""CREATE TRIGGER refuse_reencoding BEFORE UPDATE ON objects
                      BEGIN SELECT RAISE(ABORT, 'test interruption'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        store.compress_page()
    assert store.state("storage:compression") is None
    with store.connect() as db:
        assert db.execute("SELECT typeof(document) FROM objects").fetchone()[0] == "text"
        db.execute("DROP TRIGGER refuse_reencoding")
    assert store.compress_page() == 1
    assert store.get("market", doc["ticker"])["raw"] == doc["raw"]


def test_compression_serializes_with_concurrent_evidence_updates(store):
    record = trade(notes="history " * 1000)
    store.evidence_page("paper", [record], {"after": 1})
    with store.connect() as db:
        db.execute("UPDATE evidence SET document=?", (encode(record),))
    store.set_state(
        "storage:compression", {"table_index": DOCUMENT_TABLES.index("evidence"), "after": 0}
    )
    blocked = []

    def concurrent_update(value):
        other = sqlite3.connect(store.path, timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("UPDATE evidence SET document=?", (encode({**record, "pnl": 999}),))
            blocked.append(True)
        finally:
            other.close()
        return pack(value)

    with patch("kalshi_bot.catalog.store.pack", side_effect=concurrent_update):
        assert store.compress_page() == 1
    assert blocked and store.evidence()[0][1] == record
    # Once maintenance commits, the writer can retry and its new value survives.
    store.evidence_page("paper", [{**record, "pnl": 999}], {"after": 1})
    assert store.evidence()[0][1]["pnl"] == 999


def test_precompression_backup_is_consistent_and_never_overwritten(store):
    doc = store.upsert("market", "KXTEST-E-M", {"rules_primary": "rules " * 1000}, "KXTEST")
    with store.connect() as db:
        db.execute("UPDATE objects SET document=?", (encode(doc),))
    backup = store.backup_before_compression()
    store.compress_page()
    store.upsert("market", doc["ticker"], {"rules_primary": "changed"}, "KXTEST")
    assert store.backup_before_compression() == backup
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        # The original version can still read this saved JSON TEXT record.
        original = json.loads(db.execute("SELECT document FROM objects").fetchone()[0])
        assert original == doc


def test_compressed_assessment_lookup_over_http(store):
    result = {
        "as_of": "2026-10-05T00:00:00Z",
        "qualification_reasons": ["missing data " * 200],
    }
    identity = store.assessment("context", result)
    server = make_server(store, "a" * 32, ("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}",
            headers={"Authorization": "Bearer " + "a" * 32},
            trust_env=False,
        ) as client:
            response = client.get("/v1/assessments/" + identity)
            assert response.status_code == 200 and response.json() == result
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_consumer_adapter_is_qualified_by_default():
    from kalshi_bot.catalog.client import CatalogClient

    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"items": [], "advisory_only": True})

    client = CatalogClient("https://catalog", "test-token", httpx.MockTransport(handler))
    try:
        assert client.select("mmsell", "fixed_observation", 1, 80, "live")["items"] == []
        assert seen[0].url.params["qualified"] == "true"
        assert seen[0].url.params["settlement_type"] == "fixed_observation"
        assert seen[0].headers["Authorization"] == "Bearer test-token"
    finally:
        client.close()


def test_incremental_scan_preserves_upper_boundary_across_restart(store):
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        if "cursor" in request.url.params:
            return httpx.Response(200, json={"markets": [], "cursor": ""})
        return httpx.Response(200, json={"markets": [], "cursor": "second"})

    with httpx.Client(base_url="https://test", transport=httpx.MockTransport(handler)) as client:
        Discovery(store, client).updates()
        before = store.state("discovery:updates")
        Discovery(Store(store.path), client).updates()
    assert seen[0]["max_updated_ts"] == seen[1]["max_updated_ts"]
    assert store.state("discovery:updates")["since"] == before["scan_started_at"] - 60


def test_rate_backoff_preserves_cursor_and_success_across_restart(store):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json={"events": [], "cursor": "page2"})
        if len(calls) == 2:
            return httpx.Response(429, headers={"Retry-After": "180"}, text="private text")
        return httpx.Response(200, json={"events": [], "cursor": ""})

    with httpx.Client(base_url="https://test", transport=httpx.MockTransport(handler)) as client:
        discovery = Discovery(store, client)
        run_job(store, "discovery:events", lambda: discovery.page("events"))
        success = store.state("job:discovery:events")["last_success_at"]
        with patch("kalshi_bot.catalog.ingest.time.time", return_value=1000):
            assert run_job(store, "discovery:events", lambda: discovery.page("events")) is None
        assert store.state("discovery:events")["cursor"] == "page2"
        assert store.state("discovery:backoff")["not_before_unix"] == 1180
        restarted = Discovery(Store(store.path), client)
        with patch("kalshi_bot.catalog.ingest.time.time", return_value=1100):
            assert run_job(store, "discovery:events", lambda: restarted.page("events")) is None
            with pytest.raises(DiscoveryDeferred):
                restarted.updates()  # Shared provider throttle across endpoints.
        assert len(calls) == 2
        assert store.state("job:discovery:events")["last_success_at"] == success
        assert store.state("job:discovery:events")["http_status"] == 429
        assert "private text" not in json.dumps(store.status())
        with patch("kalshi_bot.catalog.ingest.time.time", return_value=1181):
            assert run_job(store, "discovery:events", lambda: restarted.page("events")) == 0
        assert calls[-1].url.params["cursor"] == "page2"
        assert store.state("discovery:events")["complete"]
        assert store.state("job:discovery:events")["error"] is None
        assert store.state("discovery:backoff")["failures"] == 0


@pytest.mark.parametrize("hint", ["Thu, 01 Jan 1970 00:21:40 GMT", "NaN", "invalid", None])
def test_provider_backoff_accepts_http_date_and_ignores_nonfinite_delay(store, hint):
    def handler(request):
        return httpx.Response(503, headers={"Retry-After": hint} if hint is not None else {})

    with httpx.Client(base_url="https://test", transport=httpx.MockTransport(handler)) as client:
        with patch("kalshi_bot.catalog.ingest.time.time", return_value=1000):
            with pytest.raises(httpx.HTTPStatusError):
                Discovery(store, client).page("events")
    expected = 1300 if hint and hint.startswith("Thu") else 1060
    assert store.state("discovery:backoff")["not_before_unix"] == expected


def test_retry_after_starts_when_response_arrives(store):
    clock = MagicMock(return_value=1000)

    def handler(request):
        clock.return_value = 1030
        return httpx.Response(429, headers={"Retry-After": "120"})

    with httpx.Client(base_url="https://test", transport=httpx.MockTransport(handler)) as client:
        with patch("kalshi_bot.catalog.ingest.time.time", clock):
            with pytest.raises(httpx.HTTPStatusError):
                Discovery(store, client).page("events")
    assert store.state("discovery:backoff")["not_before_unix"] == 1150


def test_dashboard_shell_is_public_but_data_requires_token(store):
    store.upsert("series", "KXTEST", {"title": "A <script> is only text"})
    server = make_server(store, "a" * 32, ("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False
        ) as client:
            for path in (
                "/",
                "/dashboard",
                "/dashboard/",
                "/dashboard/app.js",
                "/dashboard/style.css",
            ):
                response = client.get(path)
                assert response.status_code == 200
                assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
                assert response.headers["Cache-Control"] == "no-store"
                assert "a" * 32 not in response.text
            assert client.get("/v1/status").status_code == 401
            assert client.get("/v1/assessments").status_code == 401
            assert client.get("/dashboard/not-an-asset").status_code == 401
            client.headers["Authorization"] = "Bearer " + "a" * 32
            status = client.get("/v1/status").json()
            assert status["series_reviews"] == {"reviewed": 0, "needs_review": 1}
            assert status["captured_at"]
            assert status["confidence_calibrated"] is False
            assert status["consumer_cutover"] is False
            assert not status["backfill"]["paper"]["initial_complete"]
            doc = store.get("series", "KXTEST")
            store.review(review_payload(doc))
            assert store.status()["series_reviews"] == {"reviewed": 1, "needs_review": 0}
            store.upsert("series", "KXTEST", {"title": "Changed rules"})
            assert store.status()["series_reviews"] == {"reviewed": 0, "needs_review": 1}
            assert client.get("/dashboard/not-an-asset").status_code == 404
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


# --- WS-024 D2-B: resource use (identical re-imports, cheap storage reports, freed heap) -----


def _rowid(store, source, source_id):
    with store.connect() as db:
        return db.execute(
            "SELECT rowid FROM evidence WHERE source=? AND source_id=?", (source, source_id)
        ).fetchone()[0]


def test_identical_reimport_is_not_a_change_and_is_not_rewritten(store):
    store.evidence_page("paper", [trade(1), trade(2)], {"after": 2})
    assert store.state("evidence:changes") == 2
    first = _rowid(store, "paper", 1)
    # A reconciliation pass re-reads both rows unchanged: nothing is rewritten or counted.
    store.evidence_page("paper", [trade(1), trade(2)], {"after": 2})
    assert store.state("evidence:changes") == 2
    assert _rowid(store, "paper", 1) == first
    # A real change is still applied and counted.
    store.evidence_page("paper", [trade(1, pnl=-1)], {"after": 2})
    assert store.state("evidence:changes") == 3
    assert [r["pnl"] for s, r in store.evidence() if r["id"] == 1] == [-1]


def test_identical_reimport_matches_a_compressed_stored_row(store):
    record = trade(1, notes="historical context " * 200)
    store.evidence_page("paper", [record], {"after": 1})
    with store.connect() as db:
        stored = db.execute("SELECT document FROM evidence").fetchone()[0]
    assert isinstance(stored, bytes)  # large enough to be stored compressed
    store.evidence_page("paper", [record], {"after": 1})
    assert store.state("evidence:changes") == 1


def test_cheap_storage_report_carries_table_figures_without_rescanning(store):
    store.evidence_page("paper", [trade(1)], {"after": 1})
    full = store.storage()
    assert full["tables_captured_at"] == full["captured_at"]
    store.evidence_page("paper", [trade(2)], {"after": 2})
    cheap = store.storage(detail=False, previous=full)
    # Table figures are the last full pass's, labelled with when it ran; file figures are new.
    assert cheap["tables"] == full["tables"]
    assert cheap["tables_captured_at"] == full["captured_at"]
    assert cheap["captured_at"] >= full["captured_at"]
    assert set(cheap["file_bytes"]) == {"database", "-wal", "-shm"}
    assert cheap["volume_total_bytes"] > 0
    # Carried figures survive a second cheap report.
    again = store.storage(detail=False, previous=cheap)
    assert again["tables_captured_at"] == full["captured_at"]


def test_cheap_storage_report_reads_no_documents(store):
    store.evidence_page("paper", [trade(1)], {"after": 1})
    statements = []
    real_connect = store.connect

    from contextlib import contextmanager

    @contextmanager
    def tracing():
        with real_connect() as db:
            db.set_trace_callback(statements.append)
            yield db

    store.connect = tracing
    store.storage(detail=False, previous=None)
    assert not any("document" in sql for sql in statements)


def test_release_memory_is_safe_anywhere():
    from kalshi_bot.catalog.service import release_memory

    release_memory()


def test_full_storage_report_sizes_every_table_and_index(store):
    store.evidence_page("paper", [trade(1)], {"after": 1})
    full = store.storage()
    sizes = full["objects_bytes"]
    if sizes is None:
        pytest.skip("SQLite built without DBSTAT_VTAB")
    # Tables outside DOCUMENT_TABLES are reported too, and so are their indexes.
    assert {"evidence", "live_economics", "contract_blobs", "state"} <= set(sizes)
    assert "evidence_series" in sizes
    assert sum(sizes.values()) == full["allocated_bytes"]
    cheap = store.storage(detail=False, previous=full)
    assert cheap["objects_bytes"] == sizes


def test_storage_detail_runs_off_the_collect_loop_and_keeps_its_own_key(store):
    from kalshi_bot.catalog.service import report_storage_detail

    store.evidence_page("paper", [trade(1)], {"after": 1})
    report_storage_detail(store)
    detail = store.state("storage:detail")
    assert detail["tables_captured_at"] == detail["captured_at"]
    assert "objects_bytes" in detail
    cheap = store.storage(detail=False, previous=detail)
    assert cheap["objects_bytes"] == detail["objects_bytes"]
    assert cheap["tables"] == detail["tables"]
