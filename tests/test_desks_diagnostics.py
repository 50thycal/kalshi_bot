"""Desk-service diagnostics: least-privilege role, sanitized report, bounded market probe."""
from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from test_desks_research import NOW, store_at
from test_desks_service import (  # noqa: F401
    TOKENS,
    FakeNotifier,
    api,
    decision_body,
    service,
    settings,
)

from kalshi_bot.desks.diagnostics import MarketProbe, build_report, sanitize_retry_after
from kalshi_bot.desks.markets import MarketBrowser
from kalshi_bot.desks.models import DeskPublication
from kalshi_bot.desks.research import PublicMarketReader
from kalshi_bot.desks.research_models import ResearchBoard, ResearchJob
from kalshi_bot.desks.service import DeskService
from kalshi_bot.desks.supervisor import Supervisor

DIAG = "diagnostic-secret-" + "d" * 40
SECRETS = [*TOKENS.values(), DIAG, "chatgpt-exchange-secret", "claude-exchange-secret",
           "chatgpt-signing-secret", "claude-signing-secret", "operator-webhook-secret"]


@pytest.fixture
def diag_service(service):  # noqa: F811
    service.settings = settings(diagnostic_token=DIAG)
    return service


# ---------------------------------------------------------------------------
# Authentication and authorization
# ---------------------------------------------------------------------------


def test_diagnostic_token_is_optional_distinct_and_long():
    assert settings().diagnostic_token.get_secret_value() == ""
    assert settings(diagnostic_token=DIAG).diagnostic_token.get_secret_value() == DIAG
    with pytest.raises(ValidationError, match="diagnostic token"):
        settings(diagnostic_token="short")
    with pytest.raises(ValidationError, match="diagnostic token"):
        settings(diagnostic_token=TOKENS["operator"])


def test_diagnostic_role_reads_only_the_diagnostic_report(api, diag_service):  # noqa: F811
    status, body, headers = api("/api/diagnostics?market_probe=0", token=DIAG)
    assert status == 200 and body["schema"] == "desk-diagnostics/1"
    assert headers["Cache-Control"] == "no-store"
    for path in ("/api/status", "/api/context", "/api/research/schema", "/api/decisions/x",
                 "/api/markets", "/api/markets/X", "/api/events", "/api/series", "/api/categories"):
        assert api(path, token=DIAG)[:2] == (403, {"error": "role_forbidden"}), path


@pytest.mark.parametrize("path", [
    "/api/round/preflight", "/api/round/start", "/api/alerts/test",
    "/api/desks/claude/continue", "/api/desks/claude/claim", "/api/desks/claude/source",
    "/api/desks/claude/complete", "/api/desks/claude/decisions", "/api/desks/claude/ready",
    "/api/desks/claude/publications", "/api/desks/claude/pause", "/api/desks/claude/resume",
])
def test_diagnostic_role_can_never_write(api, diag_service, path):  # noqa: F811
    before = diag_service.store.snapshot(NOW)
    body = decision_body("claude") if path.endswith("decisions") else {"worker_id": "x", "reason": "r"}
    assert api(path, method="POST", body=body, token=DIAG)[:2] == (403, {"error": "role_forbidden"})
    after = diag_service.store.snapshot(NOW)
    assert after == before and diag_service.supervisor.ticks == []


def test_unconfigured_diagnostic_role_matches_nothing(api, service):  # noqa: F811
    assert api("/api/diagnostics", token="")[0] == 401
    assert api("/api/diagnostics", token=DIAG)[0] == 401
    assert api("/api/diagnostics", role=None)[0] == 401


def test_existing_roles_may_also_read_diagnostics(api, diag_service):  # noqa: F811
    for role in ("operator", "chatgpt", "claude"):
        assert api("/api/diagnostics?market_probe=0", role=role)[0] == 200


@pytest.mark.parametrize("query", ["market_probe=2", "refresh=1", "market_probe=0&market_probe=1",
                                   "market_probe=0&x=1"])
def test_diagnostic_query_is_strict(api, diag_service, query):  # noqa: F811
    assert api("/api/diagnostics?" + query, token=DIAG)[:2] == (400, {"error": "invalid_query"})


# ---------------------------------------------------------------------------
# Report content and redaction
# ---------------------------------------------------------------------------


def session_service(tmp_path, *, browser=None, probe=None, reader=None):
    store = store_at(tmp_path)
    supervisor = Supervisor(store, research_mode="session",
                            market_reader=reader or (lambda now: {"markets": [], "sources": []}))
    config = settings(research_mode="session", alert_mode="session", live_enabled=False,
                      diagnostic_token=DIAG, round_id="research-test")
    return DeskService(config, store, supervisor, {}, notifier=FakeNotifier(),
                       browser=browser, market_probe=probe)


def counts(store):
    with store._tx() as session:
        return (session.scalar(select(func.count()).select_from(ResearchJob)),
                session.scalar(select(func.count()).select_from(DeskPublication)),
                [(j.state, j.error, j.lease_until, j.claim_token) for j in session.scalars(select(ResearchJob))])


def test_report_covers_health_jobs_and_never_leaks_secrets(tmp_path):
    svc = session_service(tmp_path)
    sup = svc.supervisor
    claim = sup.claim_external("claude", "claude-app-worker-secret", NOW)
    with svc.store._tx() as session:
        failed = ResearchJob(job_id="session-research-test-chatgpt-" + "f" * 32, desk_id="chatgpt",
                             round_id="research-test", created_at=NOW - timedelta(hours=1),
                             updated_at=NOW - timedelta(minutes=50), state="failed",
                             error="Traceback: postgresql://user:hunter2@db/x", context={},
                             reserved_microusd=0, claim_token="claimsecret")
        session.add(failed)
    svc.store.publish("claude", "lesson", {"note": "research-payload-secret"}, NOW)
    svc.store.pause("chatgpt", "operator note with account 123456789 and secret words")
    svc.last_tick, svc.last_error = NOW - timedelta(seconds=30), "worker_cycle_failed"
    before = counts(svc.store)

    report = build_report(svc, probe=False, now=NOW)
    text = json.dumps(report)

    assert counts(svc.store) == before            # read-only: no job/publication/lease change
    for secret in [*SECRETS, claim["claim_token"], "hunter2", "claimsecret", "claude-app-worker-secret",
                   "research-payload-secret", "123456789", "secret words"]:
        assert secret not in text, secret
    for key in ("cash", "available_cash", "balance", "pnl", "claim_token", "worker_id", "payload",
                "context", "excerpt", "publications", "decisions"):
        assert f'"{key}"' not in text, key
    assert report["verdict"] == "attention"
    assert report["diagnostic"] == {"read_only": True, "failed_sections": []}
    assert report["service"]["worker"]["monitor_fresh"] is True
    assert report["service"]["worker"]["error"] == "worker_cycle_failed"
    chatgpt = report["desks"]["chatgpt"]
    assert chatgpt["paused"] is True and chatgpt["pause_category"] == "operator_text_redacted"
    assert "trading_paused:operator_text_redacted" in chatgpt["reasons"]
    jobs = {j["desk"]: j for j in report["jobs"]["recent"]}
    assert jobs["claude"]["state"] == "claimed" and jobs["claude"]["lease_active"] is True
    assert jobs["claude"]["job_id"] == claim["job_id"]
    assert jobs["chatgpt"] == {"job_id": "session-research-test-chatgpt-" + "f" * 32, "desk": "chatgpt",
                               "state": "failed", "error_category": "unrecognized",
                               "created_at": (NOW - timedelta(hours=1)).isoformat(),
                               "updated_at": (NOW - timedelta(minutes=50)).isoformat(),
                               "lease_active": False}
    assert set(report["attention"]) >= {"worker_error:worker_cycle_failed", "chatgpt_paused",
                                        "chatgpt_latest_job_failed:unrecognized"}
    assert report["readiness"]["ready"] is False
    assert "live_execution_disabled" in report["readiness"]["blockers"]
    assert report["market_probe"] == {"outcome": "skipped_by_request", "request_sent": False}


def test_known_failure_categories_are_reported_verbatim(tmp_path):
    svc = session_service(tmp_path)
    svc.last_tick = NOW
    with svc.store._tx() as session:
        session.add(ResearchJob(job_id="session-research-test-claude-" + "a1" * 16, desk_id="claude",
                                round_id="research-test", created_at=NOW, updated_at=NOW, state="failed",
                                error="market_context_rate_limited", context={}, reserved_microusd=0))
    report = build_report(svc, probe=False, now=NOW)
    assert report["jobs"]["recent"][0]["error_category"] == "market_context_rate_limited"
    assert "claude_latest_job_failed:market_context_rate_limited" in report["attention"]


def test_board_cache_and_scan_lease_are_read_without_scanning(tmp_path):
    def fetch(url, now):
        return {"source_id": "s", "url": url, "retrieved_at": now.isoformat(), "excerpt": "{}",
                "sha256": "a" * 64, "_cursor": "next",
                "_market_data": [{"ticker": "SER-EV-M", "event_ticker": "SER-EV", "market_type": "binary",
                                  "close_time": (now + timedelta(days=2)).isoformat()}]}
    store_probe = []
    browse_calls = []

    def browse(path, params=None):
        browse_calls.append(path)
        return {"events": [{"event_ticker": "SER-EV", "series_ticker": "SER", "markets": [
            {"ticker": "SER-EV-M", "status": "active", "close_time": (NOW + timedelta(days=2)).isoformat()}]}],
            "cursor": ""}

    browser = MarketBrowser(browse, clock=lambda: NOW)
    svc = session_service(tmp_path, browser=browser, probe=store_probe.append)
    reader = PublicMarketReader(fetch, store=svc.store)
    reader(NOW - timedelta(minutes=10))
    report = build_report(svc, probe=False, now=NOW)
    board = report["board"]["progressive_board"]
    assert board["pages_seen"] == 2 and board["cached_markets"] == 1
    assert board["snapshot_age_seconds"] == 600 and board["continuation_pending"] is True
    assert board["scan_lease"] == {"active": False, "seconds_remaining": 0}
    assert report["board"]["whole_board_index"] == {"state": "not_built", "scan_in_progress": False}
    assert browse_calls == [] and store_probe == []   # never builds the index or probes when told not to

    with svc.store._tx() as session:
        row = session.get(ResearchBoard, "research-test")
        row.lease_until, row.claim_token = NOW + timedelta(seconds=90), "scan-secret-token"
    browser.index()
    report = build_report(svc, probe=False, now=NOW + timedelta(seconds=30))
    assert report["board"]["progressive_board"]["scan_lease"] == {"active": True, "seconds_remaining": 60}
    index = report["board"]["whole_board_index"]
    assert index["state"] == "cached" and index["markets"] == 1 and index["age_seconds"] == 30
    assert browse_calls == ["/events"] and "scan-secret-token" not in json.dumps(report)


def test_a_failing_section_is_a_diagnostic_failure_not_a_healthy_desk(tmp_path):
    svc = session_service(tmp_path)
    svc.last_tick = NOW

    def broken(now):
        raise RuntimeError("postgresql://user:hunter2@db/desks connection refused")
    svc.supervisor.status = broken
    report = build_report(svc, probe=False, now=NOW)
    assert report["verdict"] == "diagnostic_incomplete"
    assert "desks" in report["diagnostic"]["failed_sections"]
    assert report["desks"] == {"error": "section_unavailable"}
    assert "hunter2" not in json.dumps(report)


def test_server_turns_an_unexpected_failure_into_a_generic_503(api, diag_service, monkeypatch):  # noqa: F811
    import kalshi_bot.desks.diagnostics as diagnostics

    monkeypatch.setattr(diagnostics, "build_report", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret")))
    assert api("/api/diagnostics", token=DIAG)[:2] == (503, {"error": "diagnostics_unavailable"})


# ---------------------------------------------------------------------------
# The one bounded public market GET
# ---------------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def probe_with(handler, clock=None):
    calls = []

    def handle(request):
        calls.append(request)
        return handler(request)
    return MarketProbe(transport=httpx.MockTransport(handle), clock=clock or Clock()), calls


def test_probe_reports_status_and_latency_without_credentials():
    probe, calls = probe_with(lambda r: httpx.Response(200, json={"markets": []}))
    result = probe(NOW)
    assert result["outcome"] == "ok" and result["http_status"] == 200
    assert isinstance(result["latency_ms"], int) and result["request_sent"] is True
    assert result["retry_after"] == {"present": False, "valid": None, "seconds": None}
    request = calls[0]
    assert request.method == "GET" and request.url.host == "api.elections.kalshi.com"
    assert request.url.path == "/trade-api/v2/markets" and request.url.params["limit"] == "1"
    assert "authorization" not in request.headers and "cookie" not in request.headers
    assert not any(k.lower().startswith("kalshi-access") for k in request.headers)


def test_probe_never_retries_and_honours_rate_limit_cooldown():
    clock = Clock()
    probe, calls = probe_with(lambda r: httpx.Response(429, headers={"Retry-After": "300"}), clock)
    first = probe(NOW)
    assert len(calls) == 1                               # single attempt, no in-call retry
    assert first["outcome"] == "rate_limited" and first["retry_after"]["seconds"] == 300
    clock.t += 200
    second = probe(NOW)
    assert len(calls) == 1 and second["outcome"] == "skipped_rate_limit_cooldown"
    assert second["request_sent"] is False and second["cooldown_remaining_seconds"] == 100
    assert second["previous"]["outcome"] == "rate_limited"
    clock.t += 101
    probe(NOW)
    assert len(calls) == 2


def test_probe_cooldown_has_a_floor_and_a_ceiling():
    clock = Clock()
    probe, calls = probe_with(lambda r: httpx.Response(429), clock)
    probe(NOW)
    clock.t += 119
    assert probe(NOW)["outcome"] == "skipped_rate_limit_cooldown" and len(calls) == 1
    clock = Clock()
    probe, calls = probe_with(lambda r: httpx.Response(429, headers={"Retry-After": "86400"}), clock)
    probe(NOW)
    clock.t += 3601
    probe(NOW)
    assert len(calls) == 2


def test_probe_results_are_reused_briefly_so_diagnostics_cannot_hammer():
    clock = Clock()
    probe, calls = probe_with(lambda r: httpx.Response(200), clock)
    probe(NOW)
    clock.t += 30
    cached = probe(NOW)
    assert len(calls) == 1 and cached["cached"] is True and cached["request_sent"] is False
    clock.t += 31
    probe(NOW)
    assert len(calls) == 2


@pytest.mark.parametrize("exc, outcome", [(httpx.ReadTimeout("t"), "timeout"),
                                          (httpx.ConnectTimeout("t"), "timeout"),
                                          (httpx.ConnectError("secret dns detail"), "connection_failure")])
def test_probe_timeouts_and_connection_failures_are_classified(exc, outcome):
    def fail(request):
        raise exc
    probe, calls = probe_with(fail)
    result = probe(NOW)
    assert result["outcome"] == outcome and result["http_status"] is None and len(calls) == 1
    assert "secret" not in json.dumps(result)


def test_probe_http_error_and_timeout_budget():
    probe, _ = probe_with(lambda r: httpx.Response(503, text="raw upstream body secret"))
    result = probe(NOW)
    assert result["outcome"] == "http_error" and result["http_status"] == 503
    assert "secret" not in json.dumps(result)
    assert MarketProbe().timeout <= 5


@pytest.mark.parametrize("header, expected", [
    (None, {"present": False, "valid": None, "seconds": None}),
    ("7", {"present": True, "valid": True, "seconds": 7}),
    ("2.2", {"present": True, "valid": True, "seconds": 3}),
    ("Sun, 20 Sep 2026 12:00:45 GMT", {"present": True, "valid": True, "seconds": 45}),
    ("<script>token=abc</script>", {"present": True, "valid": False, "seconds": None}),
    ("nan", {"present": True, "valid": False, "seconds": None}),
    ("999999999", {"present": True, "valid": False, "seconds": None}),
])
def test_retry_after_is_sanitized_and_never_echoed(header, expected):
    assert sanitize_retry_after(header, NOW) == expected


def test_report_carries_the_probe_and_flags_throttling(tmp_path):
    probe, _ = probe_with(lambda r: httpx.Response(429, headers={"Retry-After": "evil-raw-value"}))
    svc = session_service(tmp_path, probe=probe)
    svc.last_tick = NOW
    report = build_report(svc, probe=True, now=NOW)
    assert report["market_probe"]["outcome"] == "rate_limited"
    assert report["market_probe"]["retry_after"] == {"present": True, "valid": False, "seconds": None}
    assert "public_market_rate_limited" in report["attention"]
    assert "evil-raw-value" not in json.dumps(report)
