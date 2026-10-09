import hashlib
import threading
from types import SimpleNamespace

import httpx
import pytest

from kalshi_bot.catalog import documents
from kalshi_bot.catalog.documents import ContractCapture, DocumentCaptureError, references
from kalshi_bot.catalog.readiness import calibration_inputs, review_facts
from kalshi_bot.catalog.service import make_server, run_job
from kalshi_bot.catalog.store import SEMANTIC_FIELDS, Store

URL = "https://assets.kalshi.com/contract_terms/BTC.pdf"
PDF = b"%PDF-1.7\nopaque fixture bytes; deliberately not parsed"


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "catalog.db")
    value.upsert("series", "KXTEST", {"contract_terms_url": URL})
    return value


def collector(store, handler):
    return ContractCapture(store, httpx.Client(transport=httpx.MockTransport(handler)))


def due(store):
    with store.connect() as db:
        db.execute("UPDATE contract_targets SET next_check=0")


def table_counts(store):
    with store.connect() as db:
        return tuple(
            db.execute("SELECT count(*) FROM " + table).fetchone()[0]
            for table in ("contract_blobs", "contract_observations", "contract_targets")
        )


def test_archive_deduplicates_content_and_preserves_changed_versions_across_restart(store):
    bodies = iter([PDF, PDF, PDF + b"changed"])
    capture = collector(store, lambda request: httpx.Response(200, content=next(bodies)))
    assert capture.step() == 1
    first = references(store, store.get("series", "KXTEST"))[0]
    assert first["content_sha256"] == hashlib.sha256(PDF).hexdigest()
    assert first["current_capture_fresh"] and not first["version_at_execution_verified"]
    assert capture.step() == 0  # daily TTL; neither another request nor another observation
    due(store)
    assert capture.step() == 1
    assert table_counts(store) == (1, 2, 1)
    due(store)
    assert capture.step() == 1
    reopened = Store(store.path)
    assert table_counts(reopened) == (2, 3, 1)
    assert (
        references(reopened, reopened.get("series", "KXTEST"))[0]["content_sha256"]
        != first["content_sha256"]
    )
    with reopened.connect() as db:
        assert (
            bytes(
                db.execute(
                    "SELECT content FROM contract_blobs WHERE sha256=?", (first["content_sha256"],)
                ).fetchone()[0]
            )
            == PDF
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://assets.kalshi.com/contract_terms/BTC.pdf",
        "https://assets.kalshi.com.evil.test/contract_terms/BTC.pdf",
        "https://127.0.0.1/contract_terms/BTC.pdf",
        "https://assets.kalshi.com:443/contract_terms/BTC.pdf",
        "https://user:secret@assets.kalshi.com/contract_terms/BTC.pdf",
        URL + "?token=secret",
        URL + "#fragment",
        "https://assets.kalshi.com/contract_terms/../BTC.pdf",
        "https://assets.kalshi.com/contract_terms/%2e%2e/BTC.pdf",
        "https://assets.kalshi.com/other/BTC.pdf",
    ],
)
def test_untrusted_references_are_visible_but_never_fetched(store, url):
    store.upsert("series", "KXTEST", {"contract_terms_url": url})
    capture = collector(store, lambda request: pytest.fail("Untrusted network request"))
    assert capture.step() == 0
    fact = references(store, store.get("series", "KXTEST"))[0]
    assert fact["status"] == "unsupported_url" and fact["capture"] is None
    assert table_counts(store) == (0, 0, 0)


@pytest.mark.parametrize(
    "response,reason",
    [
        (httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"}), "http_302"),
        (httpx.Response(200, content=b"<html>error</html>"), "not_pdf"),
        (
            httpx.Response(
                200, content=PDF, headers={"Content-Length": str(documents.MAX_BYTES + 1)}
            ),
            "body_size",
        ),
        (httpx.Response(200, content=PDF, headers={"Content-Encoding": "br"}), "encoded_body"),
    ],
)
def test_invalid_response_preserves_archive_and_records_failed_refresh(store, response, reason):
    capture = collector(store, lambda request: httpx.Response(200, content=PDF))
    capture.step()
    first = store.get("series", "KXTEST")["contract_documents"][0]
    due(store)
    capture.client = httpx.Client(transport=httpx.MockTransport(lambda request: response))
    assert run_job(store, "documents:capture", capture.step) is None
    last = store.get("series", "KXTEST")["contract_documents"][0]
    assert last["content_sha256"] == first["content_sha256"]
    assert last["status"] == "refresh_failed" and last["error"] == reason
    assert last["captured_bytes_available"] and not last["current_capture_fresh"]
    assert table_counts(store) == (1, 1, 1)
    assert run_job(store, "independent", lambda: 7) == 7


def test_stream_limit_without_content_length_and_storage_reserve(store, monkeypatch):
    class Chunks(httpx.SyncByteStream):
        def __iter__(self):
            yield PDF
            yield b"x" * documents.MAX_BYTES

    capture = collector(store, lambda request: httpx.Response(200, stream=Chunks()))
    with pytest.raises(DocumentCaptureError, match="body_size"):
        capture.step()
    assert table_counts(store) == (0, 0, 1)
    due(store)
    monkeypatch.setattr(documents.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))
    capture.client = httpx.Client(
        transport=httpx.MockTransport(lambda request: pytest.fail("Low disk fetch"))
    )
    with pytest.raises(DocumentCaptureError, match="storage_reserve"):
        capture.step()
    assert store.get("series", "KXTEST")["contract_documents"][0]["error"] == "storage_reserve"


def test_rate_limit_defers_all_documents_across_restart(store):
    store.upsert("series", "OTHER", {"contract_terms_url": URL.replace("BTC", "OTHER")})
    capture = collector(
        store, lambda request: httpx.Response(429, headers={"Retry-After": "90000"})
    )
    before = documents.time.time()
    with pytest.raises(DocumentCaptureError, match="http_429"):
        capture.step()
    assert store.state("documents:backoff") >= before + 90000
    restarted = collector(Store(store.path), lambda request: pytest.fail("Backoff bypassed"))
    assert restarted.step() == 0
    assert table_counts(store) == (0, 0, 2)


def test_shared_urls_one_fetch_and_bounded_scan_resumes(store):
    for index in range(215):
        store.upsert("series", f"S{index:03}", {"contract_terms_url": URL})
    calls = []
    capture = collector(
        store, lambda request: (calls.append(request.url), httpx.Response(200, content=PDF))[1]
    )
    for _ in range(4):
        capture.step()
    assert len(calls) == 1 and table_counts(store) == (1, 1, 1)
    assert store.state("documents:scan")["restart_at"] > documents.time.time()
    assert store.get("series", "S214")["contract_documents"][0]["capture"] is not None


def test_capture_never_approves_rules_and_calibration_identity_uses_content_not_fetch_time(store):
    series = store.get("series", "KXTEST")
    store.review(
        {
            "kind": "series",
            "ticker": "KXTEST",
            "rules_hash": series["rules_hash"],
            "actor": "fixture",
            "rationale": "Fixture only",
            "semantics": {
                **dict.fromkeys(SEMANTIC_FIELDS),
                "resolution_mechanism": "index",
                "settlement_source": "official",
                "shared_exposure_key": "candidate",
            },
        }
    )
    store.upsert("market", "KXTEST-E-A", {}, "KXTEST")
    store.save_live_economics(
        [{"market_ticker": "KXTEST-E-A", "series_ticker": "KXTEST", "status": "blocked"}]
    )
    initial = calibration_inputs(store)["items"][0]
    capture = collector(
        store,
        lambda request: httpx.Response(
            200, content=PDF, headers={"Last-Modified": "Fri, 01 Jan 2021 00:00:00 GMT"}
        ),
    )
    capture.step()
    captured = calibration_inputs(store)["items"][0]
    facts = review_facts(store.get("series", "KXTEST"))
    assert facts["rules_hash"] == series["rules_hash"] and facts["review_id"]
    assert facts["known_field_count"] == 3 and not facts["contract_document_binding_verified"]
    assert not captured["requirements_met"]["verified_contract_document_binding"]
    assert not captured["qualified"] and captured["confidence_score"] is None
    assert initial["input_id"] != captured["input_id"]
    due(store)
    capture.step()
    assert calibration_inputs(store)["items"][0]["input_id"] == captured["input_id"]
    assert facts["referenced_contract_documents"][0]["capture"]["historical_effective_at"] is None


def test_authenticated_api_exposes_metadata_only_and_upgrade_preserves_old_rows(store):
    before = store.get("series", "KXTEST")
    collector(store, lambda request: httpx.Response(200, content=PDF)).step()
    server = make_server(Store(store.path), "a" * 32, ("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False
        ) as client:
            path = "/v1/objects/series/KXTEST"
            assert client.get(path).status_code == 401
            response = client.get(path, headers={"Authorization": "Bearer " + "a" * 32})
            assert response.status_code == 200 and PDF not in response.content
            after = response.json()
            assert after["rules_hash"] == before["rules_hash"]
            assert after["review_status"] == before["review_status"] == "needs_review"
            assert after["contract_documents"][0]["content_sha256"]
            status = client.get(
                "/v1/status", headers={"Authorization": "Bearer " + "a" * 32}
            ).json()
            assert status["contract_archive"]["blobs"] == 1
            assert not status["contract_archive"]["historical_versions_verified"]
            assert client.get("/v1/contract-documents").status_code == 401
            history = client.get(
                "/v1/contract-documents",
                params={"url": URL, "limit": 1},
                headers={"Authorization": "Bearer " + "a" * 32},
            )
            assert history.status_code == 200 and history.json()["total"] == 1
            assert PDF not in history.content
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_additive_schema_upgrade_keeps_preexisting_catalog_identity(store):
    original = store.get("series", "KXTEST")
    with store.connect() as db:
        for table in ("contract_blobs", "contract_targets", "contract_observations"):
            db.execute("DROP TABLE " + table)
    reopened = Store(store.path)
    assert reopened.get("series", "KXTEST")["rules_hash"] == original["rules_hash"]
    assert table_counts(reopened) == (0, 0, 0)


def test_stream_deadline_preserves_empty_archive(store, monkeypatch):
    stamps = iter([0, 21])
    monkeypatch.setattr(documents.time, "monotonic", lambda: next(stamps))
    capture = collector(store, lambda request: httpx.Response(200, content=PDF))
    with pytest.raises(DocumentCaptureError, match="fetch_deadline"):
        capture.step()
    assert table_counts(store) == (0, 0, 1)
