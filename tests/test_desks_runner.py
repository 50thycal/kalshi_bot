"""External worker tests: no live HTTP or actual model invocation."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from kalshi_bot.desks.runner import RunnerConfig, RunnerError, SessionRunner, bounded_command

NOW = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
OUTPUT = {"summary": "No evidence-backed opportunity today.", "next_action": "Inspect the next source update."}


def config(tmp_path, **updates):
    values = {"service_url": "https://desk.example", "token": "private-service-bearer",
              "desk": "chatgpt", "worker_id": "test-worker", "model_id": "test-model",
              "command": ["/configured/session-command"], "state_dir": tmp_path / "state"}
    values.update(updates)
    return RunnerConfig(**values)


def claim():
    return {"job_id": "research-job-123", "claim_token": "private-claim-token", "desk_id": "chatgpt",
            "lease_until": (NOW + timedelta(minutes=30)).isoformat(),
            "system": "Use sources and return JSON.", "context": {"desk_id": "chatgpt", "round_id": "round-1"}}


class FakeService:
    def __init__(self):
        self.actions = []
        self.completions = []
        self.complete_errors = 0
        self.publishing = 0
        self.source_failure = False
        self.claim_value = claim()

    def __call__(self, request):
        assert request.headers["authorization"] == "Bearer private-service-bearer"
        action = request.url.path.rsplit("/", 1)[-1]
        self.actions.append(action)
        body = json.loads(request.content)
        if action == "claim":
            return httpx.Response(200, json=self.claim_value)
        if action == "source":
            if self.source_failure:
                return httpx.Response(503, json={"error": "unavailable"})
            return httpx.Response(200, json={"source_id": "source-1", "url": body["url"],
                    "excerpt": "Verified research evidence", "sha256": "a" * 64, "retrieved_at": NOW.isoformat()})
        if action == "complete":
            self.completions.append(body)
            if self.complete_errors:
                self.complete_errors -= 1
                raise httpx.ReadTimeout("do not log raw provider error")
            if self.publishing:
                self.publishing -= 1
                return httpx.Response(200, json={"job_id": body["job_id"], "state": "publishing"})
            return httpx.Response(200, json={"job_id": body["job_id"], "state": "completed"})
        raise AssertionError("Runner attempted an unauthorized service action")


def runner(tmp_path, service=None, command=None, **kwargs):
    service = service or FakeService()
    command = command or (lambda *args, **kw: json.dumps(OUTPUT))
    return SessionRunner(config(tmp_path), transport=httpx.MockTransport(service),
                         command_runner=command, clock=kwargs.pop("clock", lambda: NOW), **kwargs)


def test_complete_one_job_without_exposing_tokens_to_command(tmp_path):
    service, inputs = FakeService(), []
    def command(argv, payload, **kwargs):
        inputs.append(payload)
        assert "private-service-bearer" not in json.dumps(payload)
        assert "private-claim-token" not in json.dumps(payload)
        assert payload["phase"] == "research"
        assert payload["output_schema"]
        return json.dumps(OUTPUT)
    work = runner(tmp_path, service, command)
    assert work.once()["status"] == "completed"
    assert service.actions == ["claim", "complete"]
    assert len(inputs) == 1
    assert (tmp_path / "state" / "pending.json").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "state").stat().st_mode & 0o777 == 0o700


def test_ambiguous_completion_replays_after_expiry_without_regenerating(tmp_path):
    service, calls = FakeService(), []
    service.complete_errors = 1
    def command(*args, **kwargs):
        calls.append(1)
        return json.dumps(OUTPUT)
    work = runner(tmp_path, service, command)
    assert work.once()["status"] == "recovering"
    restarted = runner(tmp_path, service, command, clock=lambda: NOW + timedelta(hours=1))
    assert restarted.once()["status"] == "completed"
    assert len(calls) == 1
    assert service.completions[0] == service.completions[1]


def test_pending_server_publication_waits_without_rerunning(tmp_path):
    service, calls = FakeService(), []
    service.publishing = 1
    def command(*args, **kwargs):
        calls.append(1)
        return json.dumps(OUTPUT)
    work = runner(tmp_path, service, command)
    assert work.once() == {"status": "recovering", "reason": "completion_publishing"}
    assert work.once()["status"] == "completed"
    assert len(calls) == 1


def test_one_source_followup_and_failure_is_data_not_regeneration(tmp_path):
    service, phases = FakeService(), []
    service.source_failure = True
    def command(argv, payload, **kwargs):
        phases.append(payload["phase"])
        if payload["phase"] == "research":
            return json.dumps({**OUTPUT, "source_requests": ["https://api.weather.gov/points/1,2"]})
        assert payload["context"]["requested_sources"][0]["error"] == "source_request_unknown"
        return json.dumps(OUTPUT)
    assert runner(tmp_path, service, command).once()["status"] == "completed"
    assert phases == ["research", "final"]
    assert service.actions == ["claim", "source", "complete"]


def test_captured_first_output_survives_restart(tmp_path):
    service, phases = FakeService(), []
    class Crash(BaseException):
        pass
    def command(argv, payload, **kwargs):
        phases.append(payload["phase"])
        return json.dumps({**OUTPUT, "source_requests": ["https://api.weather.gov/"]} if payload["phase"] == "research" else OUTPUT)
    work = runner(tmp_path, service, command)
    save = work._save
    def crashing_save(state):
        save(state)
        if state["phase"] == "captured_research":
            raise Crash
    work._save = crashing_save
    with pytest.raises(Crash):
        work.once()
    assert runner(tmp_path, service, command).once()["status"] == "completed"
    assert phases == ["research", "final"]


def test_uncertain_command_is_not_reinvoked(tmp_path):
    service, calls = FakeService(), []
    class Crash(BaseException):
        pass
    def command(*args, **kwargs):
        calls.append(1)
        raise Crash
    with pytest.raises(Crash):
        runner(tmp_path, service, command).once()
    result = runner(tmp_path, service, command).once()
    assert result == {"status": "needs_operator", "reason": "research_command_outcome_unknown"}
    assert len(calls) == 1
    assert service.actions == ["claim"]


def test_lease_expiry_and_cross_desk_claim_never_invoke_command(tmp_path):
    service = FakeService()
    service.claim_value["lease_until"] = (NOW - timedelta(seconds=1)).isoformat()
    def command(*args, **kwargs):
        pytest.fail("Model must not run")
    assert runner(tmp_path, service, command).once()["status"] == "needs_operator"
    service.claim_value = {**claim(), "desk_id": "claude"}
    with pytest.raises(RunnerError, match="invalid_service_claim"):
        runner(tmp_path / "other", service, command).once()


def test_cross_process_lock_and_state_identity_binding(tmp_path):
    first = runner(tmp_path)
    second = runner(tmp_path)
    with first._lock(), pytest.raises(RunnerError, match="runner_already_active"):
        second.once()
    first.once()
    different = SessionRunner(config(tmp_path, model_id="different-model"),
                               transport=httpx.MockTransport(FakeService()), clock=lambda: NOW)
    with pytest.raises(RunnerError, match="runner_state_identity_mismatch"):
        different.once()


def test_invalid_captured_output_is_retained_without_model_retry(tmp_path):
    calls = []
    def command(*args, **kwargs):
        calls.append(1)
        return "not-json"
    work = runner(tmp_path, command=command)
    assert work.once()["status"] == "needs_operator"
    assert work.once()["status"] == "needs_operator"
    assert len(calls) == 1
    state = json.loads((tmp_path / "state" / "pending.json").read_text())
    assert state["raw"] == "not-json"


def test_command_environment_is_allowlisted_and_output_bounded(tmp_path, monkeypatch):
    for name in ("DESK_SESSION_TOKEN", "DESK_OPERATOR_TOKEN", "KALSHI_API_KEY", "DATABASE_URL", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CODEX_HOME"):
        monkeypatch.setenv(name, "private-value")
    script = "import os,json,sys; payload=json.load(sys.stdin); print(json.dumps({'keys':sorted(os.environ),'home':os.environ['HOME'],'payload':payload}))"
    result = json.loads(bounded_command([sys.executable, "-c", script], {"test": 1}, home=tmp_path, timeout=5))
    assert not any(name.endswith("KEY") or "TOKEN" in name or name == "DATABASE_URL" for name in result["keys"])
    assert result["home"] == str(tmp_path)
    assert result["payload"] == {"test": 1}
    with pytest.raises(RunnerError, match="research_stdout_limit"):
        bounded_command([sys.executable, "-c", "print('x'*70000)"], {}, home=tmp_path, timeout=5)
    with pytest.raises(RunnerError, match="research_stderr_limit"):
        bounded_command([sys.executable, "-c", "import sys;sys.stderr.write('x'*20000)"], {}, home=tmp_path, timeout=5)
    with pytest.raises(RunnerError, match="research_command_timeout"):
        bounded_command([sys.executable, "-c", "import time;time.sleep(10)"], {}, home=tmp_path, timeout=0.1)


def test_runner_config_refuses_shell_and_untrusted_transport(tmp_path):
    with pytest.raises(RunnerError, match="json_argv"):
        config(tmp_path, command="echo untrusted")
    with pytest.raises(RunnerError, match="requires_https"):
        config(tmp_path, service_url="http://public.example")
    with pytest.raises(RunnerError, match="invalid_service_credentials"):
        config(tmp_path, service_url="https://user:password@public.example")


def test_definite_claim_denial_requires_operator_without_unknown_wait(tmp_path):
    def unauthorized(request):
        return httpx.Response(401, json={"error": "authentication_required"})
    work = SessionRunner(config(tmp_path), transport=httpx.MockTransport(unauthorized), clock=lambda: NOW)
    assert work.once() == {"status": "needs_operator", "reason": "service_authentication_failed"}
    assert json.loads((tmp_path / "state" / "pending.json").read_text())["phase"] == "completed"


def test_service_replies_require_identity_encoding(tmp_path):
    def compressed(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, content=b"{}", headers={"content-encoding": "br"})
    work = SessionRunner(config(tmp_path), transport=httpx.MockTransport(compressed), clock=lambda: NOW)
    with pytest.raises(RunnerError, match="compressed_service_response_refused"):
        work._post("claim", {"worker_id": "test"})


def test_sigterm_unwinds_subprocess_cleanup(tmp_path):
    import signal
    import subprocess
    import time

    pidfile = tmp_path / "research.pid"
    child_code = f"import os,time;from pathlib import Path;Path({str(pidfile)!r}).write_text(str(os.getpid()));time.sleep(30)"
    worker_code = "\n".join([
        "import signal,sys",
        "from pathlib import Path",
        "from kalshi_bot.desks.runner import bounded_command,_stop_worker",
        "signal.signal(signal.SIGTERM,_stop_worker)",
        f"bounded_command([sys.executable,'-c',{child_code!r}],{{}},home=Path({str(tmp_path)!r}),timeout=30)",
    ])
    worker = subprocess.Popen([sys.executable, "-c", worker_code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 5
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pidfile.exists()
        child_pid = int(pidfile.read_text())
        worker.send_signal(signal.SIGTERM)
        assert worker.wait(timeout=5) == 143
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait()
