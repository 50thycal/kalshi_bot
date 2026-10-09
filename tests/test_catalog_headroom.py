import threading
from types import SimpleNamespace

import httpx
import pytest

from kalshi_bot.catalog import headroom, service
from kalshi_bot.catalog.headroom import GIB, StorageDeferred, measure, require_space
from kalshi_bot.catalog.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "catalog.db")


@pytest.fixture
def disk(monkeypatch):
    usage = SimpleNamespace(total=20 * GIB, free=10 * GIB, used=10 * GIB)
    monkeypatch.setattr(headroom.shutil, "disk_usage", lambda path: usage)
    return usage


def test_persisted_bulk_hysteresis_retains_existing_records_and_resumes(store, disk):
    store.upsert("series", "TEST", {"title": "Keep me"})
    before = store.get("series", "TEST")
    disk.free = 3 * GIB
    with pytest.raises(StorageDeferred) as blocked:
        require_space(store, bulk=True)
    assert blocked.value.tier == "bulk"
    assert not require_space(store)["writes_paused"]
    disk.free = int(4.5 * GIB)
    reopened = Store(store.path)
    assert measure(reopened)["bulk_paused"]  # above pause point, below resume point
    disk.free = 5 * GIB
    assert not measure(reopened)["bulk_paused"]
    assert reopened.get("series", "TEST")["rules_hash"] == before["rules_hash"]


def test_critical_floor_and_hysteresis_apply_to_essential_jobs(store, disk):
    disk.free = GIB - 1
    with pytest.raises(StorageDeferred) as blocked:
        require_space(store)
    assert blocked.value.tier == "writes"
    disk.free = GIB + 1
    assert measure(Store(store.path))["writes_paused"]
    disk.free = 3 * GIB // 2
    assert not require_space(store)["writes_paused"]
    assert measure(store)["bulk_paused"]


def test_job_deferral_does_not_run_action_advance_cursor_or_claim_success(store, disk):
    store.set_state("cursor:test", "kept")
    assert service.run_job(store, "discovery:events", lambda: 7, bulk=True) == 7
    success = store.state("job:discovery:events")["last_success_at"]
    disk.free = 3 * GIB
    assert (
        service.run_job(
            store, "discovery:events", lambda: pytest.fail("Bulk action ran"), bulk=True
        )
        is None
    )
    state = store.state("job:discovery:events")
    assert state["error"] == "StorageDeferred" and state["storage_deferral"] == "bulk"
    assert state["last_success_at"] == success and state["records"] == 7
    assert store.state("cursor:test") == "kept"
    assert service.run_job(store, "import:live", lambda: 1) == 1
    disk.free = 6 * GIB
    assert service.run_job(store, "discovery:events", lambda: 2, bulk=True) == 2
    assert store.state("job:discovery:events")["storage_deferral"] is None
    assert store.state("job:discovery:events")["error"] is None


def test_failed_measurement_defers_writes_without_exposing_exception_text(store, monkeypatch):
    def failure(path):
        raise OSError("secret mount or credential")

    monkeypatch.setattr(headroom.shutil, "disk_usage", failure)
    assert service.run_job(store, "import:paper", lambda: pytest.fail("Write ran")) is None
    state = store.state("storage:guard")
    assert state["measurement_error"] == "OSError" and state["writes_paused"]
    assert "secret" not in str(store.status())
    assert service.run_job(store, "storage:metrics", lambda: 0, guard=False) == 0


@pytest.mark.parametrize("free,essential_runs", [(3 * GIB, True), (GIB // 2, False)])
def test_collection_loop_retains_bulk_cursor_and_critical_evaluation_request(
    store, disk, monkeypatch, free, essential_runs
):
    calls = []
    disk.free = free
    store.set_state("discovery:events", {"cursor": "retain-me", "complete": False})
    store.set_state("storage:compression", {"complete": True})
    store.set_state("evaluation_requested", "request-kept-until-evaluated")

    class Discovery:
        def __init__(self, value):
            self.client = SimpleNamespace(close=lambda: calls.append("discovery-close"))

        def page(self, job):
            pytest.fail("Bulk discovery ran")

        def updates(self):
            pytest.fail("Bulk updates ran")

        def reset(self):
            pytest.fail("Paused cursor reset")

        def live_outcomes(self):
            calls.append("outcomes")
            return 1

    class Contracts:
        def __init__(self, value):
            self.client = SimpleNamespace(close=lambda: calls.append("contracts-close"))

        def step(self):
            calls.append("documents")
            return 1

    class Stopped:
        stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, interval):
            self.stopped = True

    monkeypatch.setattr(service, "Discovery", Discovery)
    monkeypatch.setattr(service, "ContractCapture", Contracts)
    monkeypatch.setattr(
        service, "source_page", lambda value, url, source: calls.append(source) or 1
    )
    monkeypatch.setattr(service, "refresh", lambda value: calls.append("evaluation") or 1)
    monkeypatch.setattr(service, "release_memory", lambda: None)
    service.collect(store, Stopped(), "never-printed-secret", 1)
    assert store.state("discovery:events")["cursor"] == "retain-me"
    for name in ("documents", "paper", "live", "outcomes", "evaluation"):
        assert (name in calls) is essential_runs
    assert (store.state("evaluation_requested") is None) is essential_runs
    assert store.state("job:storage:metrics")["error"] is None
    assert "discovery-close" in calls and "contracts-close" in calls


def test_authenticated_writes_return_503_but_reads_and_auth_guards_remain(store, disk):
    disk.free = GIB // 2
    server = service.make_server(store, "a" * 32, ("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False
        ) as client:
            headers = {"Authorization": "Bearer " + "a" * 32}
            payload = {
                "source": "live",
                "records": [{"id": 1, "market_ticker": "TEST-E-M"}],
                "provenance": "fixture",
            }
            assert client.post("/v1/import", json=payload).status_code == 401
            assert client.post("/v1/import", json=payload, headers=headers).status_code == 503
            assert (
                client.post("/v1/reviews", json={"kind": "market"}, headers=headers).status_code
                == 503
            )
            assert store.status()["evidence"] == {}
            assert client.get("/health").status_code == 200
            response = client.get("/v1/status", headers=headers)
            assert (
                response.status_code == 200
                and response.json()["jobs"]["storage:guard"]["writes_paused"]
            )
            disk.free = 2 * GIB
            assert client.post("/v1/import", json=payload, headers=headers).status_code == 201
            assert store.status()["evidence"]["live"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
