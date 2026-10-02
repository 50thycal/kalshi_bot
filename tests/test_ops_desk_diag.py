"""Ops `{"type":"desks"}`: validation, one bounded request, redaction and per-ID results."""
from __future__ import annotations

import email.message
import io
import json
import pathlib
import sys
import threading
import urllib.error
from datetime import timedelta

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import desk_diag  # noqa: E402
import ops_meta  # noqa: E402
import ops_runner  # noqa: E402

TOKEN = "diagnostic-secret-" + "d" * 40
ENV = {"DESKS_DIAGNOSTIC_TOKEN": TOKEN}


def good_report(**overrides):
    report = {
        "schema": "desk-diagnostics/1", "generated_at": "2026-10-02T12:00:00+00:00",
        "verdict": "attention", "attention": ["claude_latest_job_failed:market_context_rate_limited"],
        "diagnostic": {"read_only": True, "failed_sections": []},
        "service": {"research_mode": "session", "alert_mode": "session", "account_mode": "isolated",
                    "live_enabled": True, "round_id": "desks-round-2", "round_started": True,
                    "started_at": "2026-09-30T00:00:00+00:00",
                    "worker": {"last_tick": "2026-10-02T11:59:50+00:00", "last_tick_age_seconds": 10,
                               "monitor_fresh": True, "error": None}},
        "desks": {d: {"health": "recovering", "activity": "waiting_for_continue", "reasons": [],
                      "ready": True, "paused": False, "pause_category": None, "book_status": "running",
                      "completed_cycles": 4, "pending_jobs": 0, "recent_failures": 1,
                      "last_completed_at": "2026-10-01T10:00:00+00:00", "unreviewed_settlements": 0}
                  for d in ("chatgpt", "claude")},
        "readiness": {"ready": True, "blockers": [], "note": "cached launch checks; nothing was refreshed"},
        "jobs": {"limit_per_desk": 8, "recent": [
            {"job_id": "session-desks-round-2-claude-" + "ab" * 16, "desk": "claude", "state": "failed",
             "error_category": "market_context_rate_limited", "created_at": "2026-10-01T03:00:00+00:00",
             "updated_at": "2026-10-01T03:00:05+00:00", "lease_active": False}]},
        "board": {"progressive_board": {"pages_seen": 40, "completed_passes": 2, "cached_markets": 900,
                                        "sample_markets": 20, "continuation_pending": True,
                                        "snapshot_as_of": "2026-10-02T11:00:00+00:00", "snapshot_age_seconds": 3600,
                                        "scan_lease": {"active": False, "seconds_remaining": 0}},
                  "whole_board_index": {"state": "not_built", "scan_in_progress": False}},
        "market_probe": {"target": "public_market_list", "outcome": "rate_limited", "http_status": 429,
                         "latency_ms": 120, "request_sent": True, "cached": False,
                         "retry_after": {"present": True, "valid": True, "seconds": 30}},
    }
    report.update(overrides)
    return report


class FakeResponse:
    def __init__(self, status, body, content_type="application/json"):
        self.status, self._body = status, body if isinstance(body, bytes) else json.dumps(body).encode()
        self.headers = email.message.Message()
        self.headers["Content-Type"] = content_type

    def read(self, limit=-1):
        return self._body if limit < 0 else self._body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    def __init__(self, outcome):
        self.outcome, self.requests, self.timeouts = outcome, [], []

    def open(self, request, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def http_error(code):
    return urllib.error.HTTPError("https://x/api/diagnostics", code, "err", email.message.Message(),
                                  io.BytesIO(b"raw body secret"))


def run(req, outcome, capsys, environ=ENV, results_dir=None):
    opener = FakeOpener(outcome)
    status = desk_diag.run(req, environ=environ, opener=opener, results_dir=results_dir)
    out = capsys.readouterr()
    return status, out.out, out.err, opener


REQ = {"type": "desks", "id": "desk-diag-test-1"}


# ---------------------------------------------------------------------------
# Request validation and the channel contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("req, reason", [
    ({"type": "desks"}, "unique 'id'"),
    ({"type": "desks", "id": "a"}, "unique 'id'"),
    ({"type": "desks", "id": "bad id/../x"}, "unique 'id'"),
    ({"type": "desks", "id": "ok-id", "url": "https://evil.example"}, "unknown field"),
    ({"type": "desks", "id": "ok-id", "token": "x"}, "unknown field"),
    ({"type": "desks", "id": "ok-id", "market_probe": "yes"}, "market_probe"),
    ({"type": "desks", "id": "ok-id", "format": "html"}, "format"),
])
def test_invalid_requests_are_refused_before_any_network_call(req, reason, capsys):
    status, out, err, opener = run(req, FakeResponse(200, good_report()), capsys)
    assert status == 1 and reason in err and opener.requests == []
    assert "FAILED — invalid_request" in out and "NOT a health result" in out


def test_a_reused_id_is_refused(tmp_path, capsys):
    (tmp_path / "desk-diag-test-1.txt").write_text("someone else's report")
    status, _, err, opener = run(REQ, FakeResponse(200, good_report()), capsys, results_dir=str(tmp_path))
    assert status == 1 and "already has a result" in err and opener.requests == []


def test_desks_is_a_read_on_the_fast_path():
    assert ops_meta.classify(REQ) == ops_meta.READ
    assert ops_meta.needs_full_deps(REQ) is False
    assert "desks" in ops_meta.REQUEST_TYPES_BY_NAME


def test_missing_token_is_not_configured_not_healthy(capsys):
    status, out, _, opener = run(REQ, FakeResponse(200, good_report()), capsys, environ={})
    assert status == 1 and opener.requests == []
    assert "FAILED — not_configured" in out and "docs/desks/DIAGNOSTICS.md" in out


@pytest.mark.parametrize("url", ["http://desk.example", "https://u:p@desk.example",
                                 "https://desk.example/api?x=1", "https://desk.example/sub"])
def test_service_url_override_must_be_a_plain_https_base(url, capsys):
    status, out, _, opener = run(REQ, FakeResponse(200, good_report()), capsys,
                                 environ={**ENV, "DESKS_DIAGNOSTIC_URL": url})
    assert status == 1 and "invalid_configuration" in out and opener.requests == []


def test_one_get_with_the_diagnostic_token_and_a_timeout(capsys):
    status, out, _, opener = run({**REQ, "market_probe": False}, FakeResponse(200, good_report()), capsys)
    assert status == 0 and len(opener.requests) == 1
    request = opener.requests[0]
    assert request.get_method() == "GET"
    assert request.full_url == "https://desk-service-production.up.railway.app/api/diagnostics?market_probe=0"
    assert request.get_header("Authorization") == "Bearer " + TOKEN
    assert opener.timeouts == [desk_diag.TIMEOUT_SECONDS] and desk_diag.TIMEOUT_SECONDS <= 30
    assert TOKEN not in out


# ---------------------------------------------------------------------------
# Failures are diagnostic failures, never a healthy desk; nothing is retried
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("outcome, code", [
    (http_error(401), "authentication_refused"),
    (http_error(403), "authentication_refused"),
    (http_error(404), "endpoint_not_deployed"),
    (http_error(400), "request_rejected"),
    (http_error(429), "service_rate_limited"),
    (http_error(503), "service_error"),
    (http_error(302), "redirect_refused"),
    (TimeoutError("timed out"), "timeout"),
    (urllib.error.URLError(TimeoutError("t")), "timeout"),
    (urllib.error.URLError("secret dns detail"), "connection_failed"),
    (ConnectionResetError("secret"), "connection_failed"),
    (FakeResponse(200, b"<html>raw secret page</html>", "text/html"), "invalid_response"),
    (FakeResponse(200, b"{not json"), "invalid_response"),
    (FakeResponse(200, b'{"schema":"desk-diagnostics/1","verdict":NaN}'), "invalid_response"),
    (FakeResponse(200, {"schema": "other/9", "verdict": "healthy"}), "invalid_response"),
    (FakeResponse(200, ["a"]), "invalid_response"),
    (FakeResponse(200, b"x" * (desk_diag.MAX_RESPONSE_BYTES + 10)), "response_too_large"),
])
def test_failures_are_classified_and_never_retried(outcome, code, capsys):
    status, out, _, opener = run(REQ, outcome, capsys)
    assert status == 1 and len(opener.requests) == 1
    assert f"FAILED — {code}" in out and "VERDICT    : UNKNOWN" in out
    assert "HEALTHY" not in out and "secret" not in out and "raw body" not in out


def test_json_failure_format(capsys):
    status, out, _, _ = run({**REQ, "format": "json"}, http_error(401), capsys)
    assert status == 1
    assert json.loads(out) == {"diagnostic": "failed", "failure": "authentication_refused",
                               "http_status": 401, "verdict": "unknown"}


def test_an_incomplete_report_is_a_failed_run(capsys):
    report = good_report(verdict="diagnostic_incomplete", desks={"error": "section_unavailable"},
                         diagnostic={"read_only": True, "failed_sections": ["desks"]})
    status, out, _, _ = run(REQ, FakeResponse(200, report), capsys)
    assert status == 1
    assert "DIAGNOSTIC : INCOMPLETE" in out and "SECTION FAILED: section_unavailable" in out


# ---------------------------------------------------------------------------
# Redaction: the service's report is untrusted too
# ---------------------------------------------------------------------------


def test_rendered_report_shows_requested_fields(capsys):
    status, out, _, _ = run(REQ, FakeResponse(200, good_report()), capsys)
    assert status == 0
    for text in ("DIAGNOSTIC : OK", "VERDICT    : ATTENTION", "market_context_rate_limited",
                 "session-desks-round-2-claude-" + "ab" * 16, "2026-10-01T03:00:05+00:00",
                 "claim board cached markets", "900", "scan lease active", "HTTP status", "429",
                 "latency (ms)", "120", "Retry-After", "30s", "recovering (waiting_for_continue)"):
        assert text in out, text


def test_unexpected_or_hostile_fields_are_dropped_or_redacted(capsys):
    hostile = good_report(cash="30.00", claim_token="claim-secret-1")
    hostile["service"]["round_id"] = "round with spaces postgresql://u:pw@h/db"
    hostile["service"]["worker"]["error"] = "Traceback (most recent call): key=sk-live-secret"
    hostile["desks"]["chatgpt"]["pause_category"] = "operator typed 4111111111111111"
    hostile["desks"]["chatgpt"]["balance"] = "29.99"
    hostile["desks"]["claude"]["reasons"] = ["trading_paused:free text secret", "x:y:z", "ok_code"]
    hostile["jobs"]["recent"][0]["job_id"] = "session-x-claude-abc; claim_token=abcdef"
    hostile["jobs"]["recent"][0]["claim_token"] = "claim-secret-2"
    hostile["jobs"]["recent"][0]["error_category"] = "deadbeefdeadbeefdeadbeef_token"
    hostile["market_probe"]["retry_after"] = {"present": True, "valid": True, "seconds": "<raw header>"}
    hostile["market_probe"]["body"] = "raw api body secret"
    hostile["attention"] = ["fine_code", "bad code secret"]
    status, out, _, _ = run({**REQ, "format": "json"}, FakeResponse(200, hostile), capsys)
    assert status == 0
    for leaked in ("30.00", "29.99", "claim-secret", "postgresql", "pw@", "sk-live", "4111111111111111",
                   "free text", "claim_token=", "deadbeefdeadbeef", "<raw header>", "raw api body",
                   "bad code", '"balance"', '"cash"', '"body"'):
        assert leaked not in out, leaked
    data = json.loads(out)["report"]
    assert data["service"]["round_id"] == "[redacted]"
    assert data["desks"]["claude"]["reasons"] == ["[redacted]", "[redacted]", "ok_code"]
    assert data["market_probe"]["retry_after"]["seconds"] is None


def test_a_report_echoing_the_token_is_withheld(capsys):
    report = good_report()
    report["service"]["research_mode"] = TOKEN  # cannot pass the projection; the guard is the backstop
    status, out, _, _ = run(REQ, FakeResponse(200, report), capsys)
    assert TOKEN not in out
    assert desk_diag._guard("prefix " + TOKEN, [TOKEN]).startswith("DESK SERVICE DIAGNOSTIC")
    assert "FAILED — redaction_guard" in desk_diag._guard("x " + TOKEN, [TOKEN])


# ---------------------------------------------------------------------------
# Runner dispatch, per-ID result and the workflow passthrough
# ---------------------------------------------------------------------------


def test_runner_dispatches_desks_and_writes_a_read_receipt(tmp_path, monkeypatch, capsys):
    (tmp_path / "results").mkdir()
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"type": "desks", "id": "desk-diag-runner-1"}))
    receipt = tmp_path / "receipt.json"
    monkeypatch.setattr(ops_runner, "REQUEST_PATH", str(request))
    monkeypatch.setenv("OPS_RECEIPT_PATH", str(receipt))
    monkeypatch.delenv("DESKS_DIAGNOSTIC_TOKEN", raising=False)
    assert ops_runner.main() == 1
    out = capsys.readouterr().out
    assert "# ops request [READ]" in out and "id=desk-diag-runner-1" in out
    assert "FAILED — not_configured" in out
    data = json.loads(receipt.read_text())
    assert data["class"] == "READ" and data["type"] == "desks" and data["exit_status"] == 1


def test_runner_refuses_a_reused_id_from_the_transport_results(tmp_path, monkeypatch, capsys):
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "desk-diag-old.txt").write_text("old")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"type": "desks", "id": "desk-diag-old"}))
    monkeypatch.setattr(ops_runner, "REQUEST_PATH", str(request))
    monkeypatch.setenv("DESKS_DIAGNOSTIC_TOKEN", TOKEN)
    assert ops_runner.main() == 1
    assert "already has a result" in capsys.readouterr().err


def test_per_id_result_key_matches_the_workflow_sanitizer():
    """The workflow writes ops/results/<id>.txt after `tr -c 'A-Za-z0-9._-' '_' | cut -c1-64`;
    an accepted id must survive that unchanged so the requester finds its own file."""
    import re
    for rid in ("desk-diag-20261002-1", "a.b_c-1", "x" * 64):
        assert desk_diag.ID_PATTERN.match(rid)
        assert re.sub(r"[^A-Za-z0-9._-]", "_", rid)[:64] == rid


def test_the_workflow_passes_only_the_diagnostic_token_to_the_runner():
    steps = yaml.safe_load((REPO / ".github/workflows/ops-runner.yml").read_text())["jobs"]["run"]["steps"]
    env = next(s for s in steps if s.get("name") == "Run ops request")["env"]
    assert env["DESKS_DIAGNOSTIC_TOKEN"] == "${{ secrets.DESKS_DIAGNOSTIC_TOKEN }}"
    text = json.dumps(steps)
    for forbidden in ("DESKS_OPERATOR_TOKEN", "DESKS_CHATGPT_TOKEN", "DESKS_CLAUDE_TOKEN",
                      "DESK_SESSION_TOKEN", "DESKS_DATABASE_URL"):
        assert forbidden not in text


def test_capabilities_report_configuration_but_never_the_token(monkeypatch):
    monkeypatch.setenv("DESKS_DIAGNOSTIC_TOKEN", TOKEN)
    snap = ops_meta.capability_snapshot()
    rendered = ops_meta.render_capabilities(snap)
    assert snap["desk_diagnostics"]["configured"] is True
    assert TOKEN not in rendered and TOKEN not in json.dumps(snap)


# ---------------------------------------------------------------------------
# End to end against a real local desk service
# ---------------------------------------------------------------------------


def test_end_to_end_against_a_local_desk_service(tmp_path, capsys):
    import httpx
    from test_desks_research import NOW, store_at
    from test_desks_service import FakeNotifier, settings

    from kalshi_bot.desks.diagnostics import MarketProbe
    from kalshi_bot.desks.server import make_server
    from kalshi_bot.desks.service import DeskService
    from kalshi_bot.desks.supervisor import Supervisor

    store = store_at(tmp_path)
    supervisor = Supervisor(store, research_mode="session", market_reader=lambda now: {"markets": [], "sources": []})
    probe = MarketProbe(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    service = DeskService(settings(research_mode="session", alert_mode="session", live_enabled=False,
                                   diagnostic_token=TOKEN, round_id="research-test"),
                          store, supervisor, {}, notifier=FakeNotifier(), market_probe=probe)
    service.last_tick = NOW
    supervisor.claim_external("claude", "claude-app", NOW - timedelta(minutes=5))
    server = make_server(service, ("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        base = f"http://{host}:{port}"
        payload = desk_diag.fetch(base + "/api/diagnostics?market_probe=1", TOKEN)
        report = desk_diag.sanitize(payload)
        text = desk_diag.render(report, 5)
        with pytest.raises(desk_diag.DiagError) as refused:
            desk_diag.fetch(base + "/api/diagnostics", "wrong-" + TOKEN)
        assert refused.value.code == "authentication_refused"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    assert report["market_probe"]["outcome"] == "ok"
    assert any(j["desk"] == "claude" and j["state"] == "claimed" for j in report["jobs"]["recent"])
    assert "DIAGNOSTIC : OK" in text and TOKEN not in text
