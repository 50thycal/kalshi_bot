"""Tests for scripts/railway_env.py — the allowlist guardrail (no network needed)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


renv = _load("railway_env")


def test_secrets_are_never_allowlisted():
    for secret in ("KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY", "DATABASE_URL",
                   "DATABASE_URL_RO", "RAILWAY_TOKEN", "RAILWAY_PROJECT_ID"):
        assert secret not in renv.ALLOWED_VARS


def test_live_switches_are_allowlisted():
    for v in ("BOT_MODE", "KILL_SWITCH", "LIVE_ENABLED", "LIVE_STRATEGIES",
              "LIVE_CITIES", "LIVE_WINDOWS", "LIVE_STOP_LOSS_CENTS", "MAX_DAILY_LOSS"):
        assert v in renv.ALLOWED_VARS


def test_run_set_rejects_non_allowlisted_before_any_network(monkeypatch, capsys):
    # Even with creds present, a non-allowlisted var is rejected without a network call.
    monkeypatch.setenv("RAILWAY_TOKEN", "t")
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "e")
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "s")

    def _boom(*a, **k):  # if this is called, the guard failed
        raise AssertionError("network call attempted for a rejected var")

    monkeypatch.setattr(renv, "_graphql", _boom)
    rc = renv.run_set({"KILL_SWITCH": "false", "DATABASE_URL": "postgres://x"})
    assert rc == 1
    assert "non-allowlisted" in capsys.readouterr().err


def test_run_set_requires_credentials(monkeypatch):
    for k in ("RAILWAY_TOKEN", "RAILWAY_PROJECT_ID", "RAILWAY_ENVIRONMENT_ID", "RAILWAY_SERVICE_ID"):
        monkeypatch.delenv(k, raising=False)
    # allowlisted var, but no creds -> fails closed (returns 1, no crash)
    assert renv.run_set({"KILL_SWITCH": "false"}) == 1


def _creds(monkeypatch):
    monkeypatch.setenv("RAILWAY_TOKEN", "t")
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "e")
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "s")


def test_run_set_upserts_allowlisted_and_redeploys(monkeypatch, capsys):
    _creds(monkeypatch)
    calls = []

    def _fake(query, variables, token):
        calls.append((query, variables))
        return {}

    monkeypatch.setattr(renv, "_graphql", _fake)
    rc = renv.run_set({"KILL_SWITCH": "false", "LIVE_ENABLED": "true"}, redeploy=True)
    out = capsys.readouterr().out
    assert rc == 0
    # DEC-009: a mutation reads the state BEFORE it applies and BACK afterwards,
    # so the sequence is read + two upserts + redeploy + readback. The fake
    # returns no variables, so the readback cannot confirm anything — and the
    # verdict says exactly that rather than claiming success.
    kinds = [("read" if "query" in q else "upsert" if "variableUpsert" in q else "redeploy")
             for q, _ in calls]
    assert kinds == ["read", "upsert", "upsert", "redeploy", "read"]
    assert "redeploy triggered" in out
    assert "VERDICT: APPLIED_BUT_UNVERIFIED" in out


def test_a_verified_mutation_reports_before_and_after(monkeypatch, capsys):
    """The happy path DEC-009 exists for: the change is read back and confirmed."""
    _creds(monkeypatch)
    state = {"KILL_SWITCH": "false"}

    def _fake(query, variables, token):
        if "variableUpsert" in query:
            state[variables["input"]["name"]] = variables["input"]["value"]
            return {}
        if "serviceInstanceRedeploy" in query:
            return {}
        return {"variables": dict(state)}

    monkeypatch.setattr(renv, "_graphql", _fake)
    rc, receipt = renv.apply_set({"KILL_SWITCH": "true"})
    out = capsys.readouterr().out
    assert rc == 0
    assert receipt["verdict"] == "VERIFIED"
    assert receipt["changes"]["KILL_SWITCH"]["before"] == "false"
    assert receipt["changes"]["KILL_SWITCH"]["after"] == "true"
    assert "# BEFORE:" in out and "# AFTER:" in out


def test_graphql_retries_transient_timeout(monkeypatch):
    # A transient TimeoutError on the first attempt is retried and then succeeds.
    monkeypatch.setattr(renv.time, "sleep", lambda *_: None)
    n = {"calls": 0}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"data": {"ok": true}}'

    def _urlopen(req, timeout=None):
        n["calls"] += 1
        if n["calls"] == 1:
            raise TimeoutError("The read operation timed out")
        return _Resp()

    monkeypatch.setattr(renv.urllib.request, "urlopen", _urlopen)
    data = renv._graphql("q", {}, "tok", attempts=3)
    assert data == {"ok": True} and n["calls"] == 2  # retried once, then OK


def test_run_set_continues_past_a_failing_var(monkeypatch, capsys):
    # One var fails persistently -> the batch still sets the others and reports rc=1.
    _creds(monkeypatch)
    monkeypatch.setattr(renv.time, "sleep", lambda *_: None)

    def _fake(query, variables, token):
        name = (variables.get("input") or {}).get("name")
        if name == "LIVE_ENABLED":
            raise renv.RailwayError("network/timeout: boom")
        return {}

    monkeypatch.setattr(renv, "_graphql", _fake)
    rc = renv.run_set({"KILL_SWITCH": "true", "LIVE_ENABLED": "true"}, redeploy=False)
    out = capsys.readouterr()
    assert rc == 1
    assert "set KILL_SWITCH=true" in out.out          # the good one still applied
    assert "1/2 variables set" in out.out
    assert "FAILED LIVE_ENABLED" in out.err


def test_ops_runner_dispatches_env(tmp_path, monkeypatch):
    req = tmp_path / "request.json"
    monkeypatch.setenv("OPS_REQUEST_PATH", str(req))
    for k in ("RAILWAY_TOKEN", "RAILWAY_PROJECT_ID", "RAILWAY_ENVIRONMENT_ID", "RAILWAY_SERVICE_ID"):
        monkeypatch.delenv(k, raising=False)
    runner = _load("ops_runner")
    # a set with a bad var -> dispatched to railway_env -> rejected (rc 1)
    req.write_text(json.dumps({"type": "env", "set": {"NOT_ALLOWED": "x"}}))
    assert runner.main() == 1
