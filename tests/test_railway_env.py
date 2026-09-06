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


def test_evo_enabled_is_allowlisted_and_settable(monkeypatch, capsys):
    """The 2026-09-06 gap (docs/EVO_RUNBOOK.md "Pausing and emergencies"):
    EVO_ENABLED=false is the documented fleet-wide infrastructure pause, but it
    was missing from ALLOWED_VARS so the ops env path refused to set it, forcing
    a workaround (zeroing EVO_WEEKLY_LLM_CEILING_USD) that stops LLM spend but
    not the service's own compute."""
    assert "EVO_ENABLED" in renv.ALLOWED_VARS

    _creds(monkeypatch)
    sent = []

    def _fake(query, variables, token):
        sent.append(variables)
        return {}

    monkeypatch.setattr(renv, "_graphql", _fake)
    assert renv.run_set({"EVO_ENABLED": "false"}, redeploy=False) == 0
    assert sent, "EVO_ENABLED must actually reach the network, not be rejected locally"


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


# --- value type-checking (the 2026-08-30 worker outage) ---------------------
#
# `LIVE_SHAPE_PROBE=""` was set through this path to "clear" the flag — the
# documented way to clear the STRING command-transport vars. But the field is a
# BOOL, pydantic refused it, and because the worker's config is fail-closed and
# setting a var redeploys, the worker crash-looped "invalid configuration;
# refusing to start" for 17 hours: no scanning, no paper trades, no live orders,
# across every strategy. The name and size checks were already fail-closed here;
# the value's TYPE was not.


def test_run_set_refuses_a_bool_cleared_with_an_empty_string(monkeypatch, capsys):
    """The exact outage. An empty string is not a bool, and setting it redeploys."""
    _creds(monkeypatch)

    def _boom(*a, **k):
        raise AssertionError("must fail before any network call")

    monkeypatch.setattr(renv, "_graphql", _boom)
    rc = renv.run_set({"LIVE_SHAPE_PROBE": ""})

    assert rc == 1
    err = capsys.readouterr().err
    assert "LIVE_SHAPE_PROBE" in err
    assert "crash loop" in err            # says WHY it is refused, not just that


def test_run_set_still_allows_valid_bool_spellings(monkeypatch, capsys):
    """The guard must not block the correct way to turn a flag off."""
    _creds(monkeypatch)
    sent = []

    def _fake(query, variables, token):
        sent.append(variables)
        return {}

    monkeypatch.setattr(renv, "_graphql", _fake)
    assert renv.run_set({"LIVE_SHAPE_PROBE": "false"}, redeploy=False) == 0
    assert sent, "a valid value must reach the network"


def test_run_set_refuses_a_non_numeric_int(monkeypatch):
    """Same class of error on the caps we are about to change."""
    _creds(monkeypatch)

    def _boom(*a, **k):
        raise AssertionError("must fail before any network call")

    monkeypatch.setattr(renv, "_graphql", _boom)
    assert renv.run_set({"MMSELL_LIVE_MAX_OPEN_POSITIONS": "forty"}) == 1


def test_a_var_with_no_settings_field_is_left_to_the_allowlist(monkeypatch):
    """`EXPERIMENT_OS_ISSUE_COMMAND` and friends are transports, not Settings
    fields. The type-check must not invent an opinion about them — clearing one
    with "" is correct and must keep working."""
    _creds(monkeypatch)
    sent = []

    def _fake(query, variables, token):
        sent.append(variables)
        return {}

    monkeypatch.setattr(renv, "_graphql", _fake)
    assert renv.run_set({"EXPERIMENT_OS_ISSUE_COMMAND": ""}, redeploy=False) == 0
    assert sent, "clearing a string transport var must still reach the network"


def test_the_checker_allows_the_write_when_it_cannot_introspect(monkeypatch):
    """This guard must never become a new way for ops to be down. If the Settings
    model cannot be imported, the write proceeds rather than failing closed."""
    import builtins

    real_import = builtins.__import__

    def _no_config(name, *a, **k):
        if name == "kalshi_bot.config":
            raise ImportError("simulated")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_config)
    assert renv._unparseable({"LIVE_SHAPE_PROBE": ""}) == []


# --- the log that hid the reason for 17 hours ------------------------------


def test_config_error_summary_names_the_field_but_not_the_value():
    """`invalid configuration; refusing to start` said nothing about WHICH field,
    because the reason lived in extra_fields and the ops log view renders only the
    message. It must name the field — and must NOT echo the rejected value, since
    these lines are read back onto a public branch and a bad value can be a secret.
    """
    from pydantic import BaseModel, ValidationError

    from kalshi_bot.main import _config_error_summary

    class M(BaseModel):
        live_shape_probe: bool
        kalshi_private_key: str

    try:
        M(live_shape_probe="", kalshi_private_key=12345)
    except ValidationError as exc:
        summary = _config_error_summary(exc)

    assert "live_shape_probe" in summary          # names the field...
    assert "valid boolean" in summary             # ...and why it was rejected
    assert "12345" not in summary                 # but never the value
    assert "''" not in summary


def test_config_error_summary_survives_a_non_pydantic_error():
    """A plain exception must still produce something, not raise inside the
    handler that exists to report a failure."""
    from kalshi_bot.main import _config_error_summary

    assert "RuntimeError" in _config_error_summary(RuntimeError("boom"))


# --- the guard has to actually run where it matters ------------------------


def test_the_ops_runner_installs_what_the_env_guard_needs():
    """The type-check imports kalshi_bot.config, which needs pydantic. The ops
    runner installs only psycopg on the non-`xos` fast path, so on 2026-08-31 the
    guard degraded to a printed note and allowed the write — inert in the exact
    place it exists to protect. Observed live as "value type-check skipped
    (ModuleNotFoundError: No module named 'pydantic')".

    It fails OPEN rather than closed on purpose: the ops channel is how a human
    stops live trading, and a guard that blocked every env write when a dependency
    was missing would be a far worse failure than the one it prevents. That makes
    the dependency's presence a property worth pinning, since nothing at runtime
    will complain loudly enough.
    """
    import yaml

    wf = yaml.safe_load(
        (Path(__file__).resolve().parents[1]
         / ".github" / "workflows" / "ops-runner.yml").read_text()
    )
    steps = wf["jobs"][next(iter(wf["jobs"]))]["steps"]
    installs = " ".join(s.get("run", "") for s in steps if "run" in s)

    assert "pydantic" in installs, (
        "the ops runner must install pydantic for env requests, or the value "
        "type-check in run_set silently skips and the guard does nothing"
    )
    assert "pydantic-settings" in installs, (
        "kalshi_bot.config imports pydantic_settings too; without it the import "
        "still fails and the guard still skips"
    )


# ---------------------------------------------------------------------------
# The unconsumed-command guard, wired into apply_set
# ---------------------------------------------------------------------------

ISSUE_VAR = "EXPERIMENT_OS_ISSUE_COMMAND"


def _pending(command_id="other-session-1"):
    return json.dumps({"command_id": command_id, "action": "TRIAGE", "actor": "cal",
                       "actor_role": "LIVE_OPS", "schema_version": 1, "payload": {}})


def _guarded_env(monkeypatch, *, terminal):
    """Wire apply_set to a Railway whose transport slot already holds `_pending()`,
    with the ledger answering `terminal` for it."""
    _creds(monkeypatch)
    guard = _load("ops_command_guard")
    monkeypatch.setattr(guard, "_terminal_ids",
                        lambda table, ids: set(ids) if terminal else set())
    calls = []

    def _fake(query, variables, token):
        calls.append("upsert" if "variableUpsert" in query
                     else "redeploy" if "serviceInstanceRedeploy" in query else "read")
        return {"variables": {ISSUE_VAR: _pending()}}

    monkeypatch.setattr(renv, "_graphql", _fake)
    return calls


def test_overwriting_an_UNCONSUMED_command_is_refused_before_any_write(
    monkeypatch, capsys
):
    """The multi-session collision: another session's envelope is still waiting for
    the boot that would run it. Overwriting the slot would discard it silently and
    the ledger could not catch it, because the ledger is only asked afterwards."""
    calls = _guarded_env(monkeypatch, terminal=False)
    rc, receipt = renv.apply_set({ISSUE_VAR: _pending("my-command-1")})
    err = capsys.readouterr().err
    assert rc == 1
    assert receipt["verdict"] == "REFUSED"
    assert receipt["error"] == "unconsumed command transport"
    assert receipt["command_guard"][ISSUE_VAR]["unconsumed_command_ids"] \
        == ["other-session-1"]
    assert "other-session-1" in err
    # Read the pre-state, then stop. No upsert, and above all no redeploy.
    assert "upsert" not in calls and "redeploy" not in calls


def test_a_SPENT_command_slot_is_replaced_normally(monkeypatch, capsys):
    """The common case — a finished session leaves its last command in the
    variable. A terminal receipt means the slot is spent, not pending."""
    calls = _guarded_env(monkeypatch, terminal=True)
    rc, receipt = renv.apply_set({ISSUE_VAR: _pending("my-command-1")})
    capsys.readouterr()
    assert rc == 0
    assert "upsert" in calls
    assert receipt["command_guard"][ISSUE_VAR]["blocked"] is False
    assert receipt["command_guard"][ISSUE_VAR]["conclusive"] is True


def test_force_replace_overrides_the_guard_and_is_RECORDED(monkeypatch, capsys):
    """There are real reasons to need it — an abandoned session's envelope will
    never be consumed because nobody is going to redeploy for it — so the override
    exists, is explicit, and leaves a receipt saying it was used."""
    calls = _guarded_env(monkeypatch, terminal=False)
    rc, receipt = renv.apply_set({ISSUE_VAR: _pending("my-command-1")},
                                 force_replace=True)
    capsys.readouterr()
    assert rc == 0
    assert "upsert" in calls
    assert receipt["force_replace"] is True
    # The override does not rewrite what was found: the collision is still on the
    # record, so an operator can see what was discarded and by whose decision.
    assert receipt["command_guard"][ISSUE_VAR]["blocked"] is True


def test_an_unguarded_variable_is_unaffected(monkeypatch, capsys):
    """The guard must be invisible to every other var — it only knows about the
    three Experiment OS write transports."""
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
    capsys.readouterr()
    assert rc == 0 and receipt["verdict"] == "VERIFIED"
    assert "command_guard" not in receipt
