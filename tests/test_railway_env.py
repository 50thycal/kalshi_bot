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

    monkeypatch.setattr(renv, "_gql", _boom)
    rc = renv.run_set({"KILL_SWITCH": "false", "DATABASE_URL": "postgres://x"})
    assert rc == 1
    assert "non-allowlisted" in capsys.readouterr().err


def test_run_set_requires_credentials(monkeypatch):
    for k in ("RAILWAY_TOKEN", "RAILWAY_PROJECT_ID", "RAILWAY_ENVIRONMENT_ID", "RAILWAY_SERVICE_ID"):
        monkeypatch.delenv(k, raising=False)
    # allowlisted var, but no creds -> fails closed (returns 1, no crash)
    assert renv.run_set({"KILL_SWITCH": "false"}) == 1


def test_run_set_upserts_allowlisted_and_redeploys(monkeypatch, capsys):
    monkeypatch.setenv("RAILWAY_TOKEN", "t")
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "e")
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "s")
    calls = []

    def _fake_gql(query, variables, token):
        calls.append((query, variables))
        return {}

    monkeypatch.setattr(renv, "_gql", _fake_gql)
    rc = renv.run_set({"KILL_SWITCH": "false", "LIVE_ENABLED": "true"}, redeploy=True)
    assert rc == 0
    # two upserts + one redeploy
    assert len(calls) == 3
    assert "redeploy triggered" in capsys.readouterr().out


def test_ops_runner_dispatches_env(tmp_path, monkeypatch):
    req = tmp_path / "request.json"
    monkeypatch.setenv("OPS_REQUEST_PATH", str(req))
    for k in ("RAILWAY_TOKEN", "RAILWAY_PROJECT_ID", "RAILWAY_ENVIRONMENT_ID", "RAILWAY_SERVICE_ID"):
        monkeypatch.delenv(k, raising=False)
    runner = _load("ops_runner")
    # a set with a bad var -> dispatched to railway_env -> rejected (rc 1)
    req.write_text(json.dumps({"type": "env", "set": {"NOT_ALLOWED": "x"}}))
    assert runner.main() == 1
