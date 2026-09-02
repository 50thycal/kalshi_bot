"""The ops channel's vNext contract: red when it fails, honest about what it can do.

Four defect classes are pinned here, each of which has either bitten this project
or is one refactor away from doing so:

1. **A failed request left the workflow green.** The result was published — the
   requester could read the error — but the Actions run said success, so a failed
   ops request was indistinguishable from a successful one to anything that reads
   run status rather than result text.
2. **Documented-into-existence capability** (XOS-000005, again). A request type or
   an allowlist entry that prose advertises and the runner refuses.
3. **A production change that reads like a read.** `{"type":"env"}` and
   `{"type":"env","set":{…}}` differed by one key, and nothing in the result said
   which one had happened.
4. **A change nobody can verify afterwards.** "set + redeploy requested" is a
   statement about what was asked for, not about the system.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github/workflows/ops-runner.yml"
sys.path.insert(0, str(REPO / "scripts"))

import ops_meta  # noqa: E402
import ops_runner  # noqa: E402
import railway_env  # noqa: E402


@pytest.fixture(scope="module")
def steps() -> list[dict]:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["run"]["steps"]


def _step(steps: list[dict], name: str) -> dict:
    return next(s for s in steps if s.get("name") == name)


# ---------------------------------------------------------------------------
# 1. A failed request must turn the run red
# ---------------------------------------------------------------------------


def test_the_run_step_captures_the_real_runner_status(steps):
    body = _step(steps, "Run ops request")["run"]
    assert "PIPESTATUS[0]" in body, "the runner's status must survive the tee pipeline"
    assert "ops_status" in body, "the status must be handed to the later steps"
    assert "ops_runner.py 2>&1 | tee ops/result.txt || true" not in body, (
        "`|| true` discards the request's exit status — that is the bug this fixes"
    )


def test_a_failing_request_fails_the_run_after_publication(steps):
    """Publish first, then fail: both requirements, in that order."""
    names = [s.get("name") for s in steps]
    gate = "Fail the run if the ops request failed"
    assert gate in names
    assert names.index(gate) > names.index("Publish result to ops branch"), (
        "the failure must be re-raised AFTER the result is published, or a failed "
        "request never reaches its requester"
    )
    body = _step(steps, gate)["run"]
    assert 'exit "$status"' in body
    assert _step(steps, gate)["if"] == "always()"


def test_publication_failure_still_fails_the_run(steps):
    publish = _step(steps, "Publish result to ops branch")
    assert publish["if"] == "always()", "a failed request must still be published"
    assert "exit 1" in publish["run"], (
        "a publication failure must fail loudly even when the request succeeded"
    )


def test_the_status_gate_defaults_to_failure_when_the_status_is_missing(steps):
    """A run step that died before writing a status is a failure, not a pass."""
    body = _step(steps, "Fail the run if the ops request failed")["run"]
    assert re.search(r'ops_status.*\|\|\s*echo 1', body), (
        "a missing status file must read as failure"
    )


# ---------------------------------------------------------------------------
# 2. Capability cannot be documented into existence
# ---------------------------------------------------------------------------


def _dispatched_types() -> set[str]:
    """Every `type` the runner actually dispatches, read out of its source."""
    source = (REPO / "scripts/ops_runner.py").read_text()
    found = set(re.findall(r'rtype == "([a-z]+)"', source))
    found |= set(re.findall(r'rtype in \("", "([a-z]+)"\)', source))
    return found


def test_every_dispatched_request_type_is_in_the_capability_surface():
    missing = _dispatched_types() - set(ops_meta.REQUEST_TYPES_BY_NAME)
    assert not missing, (
        f"the runner dispatches {sorted(missing)} but `capabilities` does not "
        "advertise them — a request type must not be invisible to introspection"
    )


def test_every_advertised_request_type_is_actually_dispatched():
    extra = set(ops_meta.REQUEST_TYPES_BY_NAME) - _dispatched_types()
    assert not extra, (
        f"`capabilities` advertises {sorted(extra)} which the runner does not "
        "dispatch — this is XOS-000005's defect class, in a new place"
    )


def test_the_docs_advertise_only_request_types_that_exist():
    """The runbook and the ops README are checked against the registry, not prose."""
    known = set(ops_meta.REQUEST_TYPES_BY_NAME)
    for doc in ("docs/OPS_RUNBOOK.md", "ops/README.md"):
        text = (REPO / doc).read_text()
        advertised = set(re.findall(r'"type"\s*:\s*"([a-z]+)"', text))
        unknown = advertised - known
        assert not unknown, f"{doc} advertises unsupported request types: {sorted(unknown)}"


def test_the_docs_describe_every_request_type_that_exists():
    text = (REPO / "docs/OPS_RUNBOOK.md").read_text() + (REPO / "ops/README.md").read_text()
    advertised = set(re.findall(r'"type"\s*:\s*"([a-z]+)"', text))
    missing = set(ops_meta.REQUEST_TYPES_BY_NAME) - advertised
    assert not missing, (
        f"{sorted(missing)} exist but are documented nowhere an operator reads — "
        "the stale-README failure, inverted"
    )


def test_every_targetable_service_has_its_secret_passed_through(steps):
    """A service the runner can select but the workflow does not pass a secret for
    is a service that answers 'not configured' forever, for no visible reason."""
    env = _step(steps, "Run ops request")["env"]
    for name, secret in ops_runner._SERVICE_ID_SECRET.items():
        assert secret in env, (
            f"service {name!r} needs {secret} in the Run ops request step's env"
        )
        assert f"secrets.{secret}" in env[secret]


def test_the_capability_surface_never_prints_a_service_id(monkeypatch):
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "super-secret-service-id")
    snapshot = ops_meta.capability_snapshot()
    rendered = ops_meta.render_capabilities(snapshot)
    assert "super-secret-service-id" not in rendered
    assert "super-secret-service-id" not in json.dumps(snapshot)
    assert snapshot["services"]["main"]["configured"] is True


def test_the_capability_surface_reports_the_live_allowlists():
    snapshot = ops_meta.capability_snapshot()
    assert snapshot["xos_commands"] == sorted(ops_runner.xos_allowlist())
    assert snapshot["scripts"] == sorted(ops_runner.ALLOWED_SCRIPTS)
    assert snapshot["env"]["readable_settable"] == sorted(railway_env.ALLOWED_VARS)
    assert snapshot["env"]["redacted"] == sorted(railway_env.REDACTED_VARS)


def test_no_secret_bearing_variable_is_advertised_as_settable():
    """The allowlist is the guard; this is the alarm if someone widens it."""
    forbidden = ("KALSHI_API_KEY", "KALSHI_PRIVATE_KEY", "DATABASE_URL",
                 "RAILWAY_TOKEN", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID")
    assert not [v for v in railway_env.ALLOWED_VARS if v in forbidden]


# ---------------------------------------------------------------------------
# 3. A mutation must be unmistakable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("request_body", [
    {"type": "noop"}, {"type": "logs"}, {"type": "db", "sql": "select 1"},
    {"type": "xos", "command": "control-tower"}, {"type": "capabilities"},
    {"type": "doctor"}, {"type": "incident"}, {"type": "script", "name": "evo_digest"},
    {"type": "env"}, {"type": "env", "action": "get"},
])
def test_reads_are_classified_as_reads(request_body):
    assert ops_meta.classify(request_body) == ops_meta.READ


@pytest.mark.parametrize("request_body", [
    {"type": "env", "set": {"KILL_SWITCH": "true"}},
    {"type": "env", "action": "set", "values": {"KILL_SWITCH": "true"}},
])
def test_both_mutation_spellings_are_classified_as_mutating(request_body):
    assert ops_meta.classify(request_body) == ops_meta.MUTATING
    assert ops_meta.env_mutation(request_body) == {"KILL_SWITCH": "true"}


@pytest.mark.parametrize("request_body", [
    {"type": "env", "action": "get", "values": {"KILL_SWITCH": "true"}},
    {"type": "env", "action": "set"},
    {"type": "env", "action": "set", "values": {}},
    {"type": "env", "action": "delete", "values": {"KILL_SWITCH": "true"}},
])
def test_an_ambiguous_env_request_is_refused_rather_than_guessed(request_body):
    with pytest.raises(ops_meta.OpsRequestError):
        ops_meta.env_mutation(request_body)


def test_an_ambiguous_request_is_refused_by_the_runner(tmp_path, monkeypatch, capsys):
    path = tmp_path / "request.json"
    path.write_text(json.dumps(
        {"type": "env", "action": "get", "values": {"KILL_SWITCH": "true"}}))
    monkeypatch.setattr(ops_runner, "REQUEST_PATH", str(path))
    assert ops_runner.main() == 1
    out = capsys.readouterr()
    assert "UNCLASSIFIED" in out.out
    assert "ambiguous" in out.err


def test_the_result_header_shouts_before_a_mutation_is_shown():
    request_body = {"type": "env", "action": "set", "values": {"LIVE_ENABLED": "true"},
                    "id": "arm-1", "actor": "live-ops"}
    receipt = ops_meta.build_receipt(request_body, started_at="2026-01-01T00:00:00Z")
    header = ops_meta.header(request_body, receipt)
    first = header.splitlines()[1]
    assert "MUTATING" in first, "the classification must precede any output"
    assert "LIVE_ENABLED" in header and "actor=live-ops" in header


def test_a_read_header_does_not_shout():
    request_body = {"type": "db", "sql": "select 1", "id": "q"}
    receipt = ops_meta.build_receipt(request_body, started_at="2026-01-01T00:00:00Z")
    assert ops_meta.header(request_body, receipt).startswith("# ops request [READ]")


def test_provenance_is_recorded_but_is_never_authority():
    request_body = {"type": "db", "sql": "select 1", "actor": "x" * 500,
                    "purpose": "p", "workstream": "WS-012", "role": "ADMIN"}
    receipt = ops_meta.build_receipt(request_body, started_at="t")
    assert receipt["provenance"]["workstream"] == "WS-012"
    assert len(receipt["provenance"]["actor"]) == ops_meta.MAX_PROVENANCE_CHARS
    assert "role" not in receipt["provenance"], (
        "only the known provenance fields are carried; an unknown field must not "
        "become part of the record just because a producer wrote it"
    )


# ---------------------------------------------------------------------------
# 4. Receipts, verification and the durable archive
# ---------------------------------------------------------------------------


def test_the_runner_writes_a_receipt_for_a_plain_read(tmp_path, monkeypatch):
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"type": "noop", "id": "r1", "purpose": "smoke"}))
    receipt_path = tmp_path / "receipt.json"
    monkeypatch.setattr(ops_runner, "REQUEST_PATH", str(request))
    monkeypatch.setenv(ops_runner.RECEIPT_PATH_ENV, str(receipt_path))

    assert ops_runner.main() == 0
    receipt = json.loads(receipt_path.read_text())
    assert receipt["type"] == "noop"
    assert receipt["class"] == ops_meta.READ
    assert receipt["exit_status"] == 0
    assert receipt["provenance"] == {"purpose": "smoke"}
    assert receipt["started_at"] and receipt["finished_at"]


def test_a_receipt_exists_even_when_the_runner_wrote_none():
    """The runner can die before writing; the workflow still records the attempt."""
    final = ops_meta.finalize_receipt(
        {}, {"type": "env", "id": "arm-2", "set": {"LIVE_ENABLED": "true"}},
        status=1, rid="arm-2")
    assert final["outcome"] == "FAILED"
    assert "MISSING" in final["runner_receipt"]
    assert final["result_file"] == "ops/results/arm-2.txt"


def test_finalize_carries_the_workflow_facts():
    receipt = ops_meta.build_receipt({"type": "db", "sql": "select 1", "id": "q"},
                                     started_at="t")
    final = ops_meta.finalize_receipt(receipt, {}, status=0, rid="q")
    assert final["outcome"] == "SUCCEEDED"
    assert final["publication"] == "COMMITTED"
    assert final["receipt_file"] == "ops/results/q.receipt.json"
    assert final["audit_worthy"] is False


@pytest.mark.parametrize("names,expected", [
    (["LIVE_STRATEGIES"], True),
    (["KILL_SWITCH"], True),
    (["EXPERIMENT_OS_EXPERIMENT_COMMAND"], True),
    (["MAX_TOTAL_EXPOSURE"], True),
    (["LOG_LEVEL"], False),
    (["EVO_MAX_ACTIVE_AGENTS"], False),
])
def test_only_production_changing_mutations_are_durably_archived(names, expected):
    assert ops_meta.is_audit_worthy(names) is expected
    final = ops_meta.finalize_receipt(
        ops_meta.build_receipt({"type": "env", "set": {n: "x" for n in names}},
                               started_at="t"),
        {}, status=0, rid="r")
    assert final["audit_worthy"] is expected


def test_a_failed_arm_is_still_archived():
    """'Someone tried to arm this and it was refused' is history too."""
    final = ops_meta.finalize_receipt(
        ops_meta.build_receipt({"type": "env", "set": {"LIVE_ENABLED": "true"}},
                               started_at="t"),
        {}, status=1, rid="r")
    assert final["audit_worthy"] is True


def test_every_audit_worthy_variable_is_actually_settable():
    """An archive rule naming a variable the channel cannot set is dead prose."""
    unknown = ops_meta.AUDIT_WORTHY_VARS - railway_env.ALLOWED_VARS
    assert not unknown, f"not settable through this channel: {sorted(unknown)}"


def test_the_workflow_archives_only_what_the_code_calls_audit_worthy(steps):
    body = _step(steps, "Archive a production-changing receipt")["run"]
    assert 'verdict" != "audit"' in body, (
        "the archive decision must come from ops_meta.finalize, not from shell"
    )
    assert "ops-audit" in body
    assert "ops_request.json" not in body, (
        "receipts are archived, not request payloads"
    )


def test_the_receipt_is_finalized_where_code_is_allowed_to_run(steps):
    """The publish step's own invariant is that it touches the transport and
    nothing else (tests/test_ops_runner_freshness.py). So the receipt is
    completed in the step that already executes default-branch code."""
    assert "ops_meta.py finalize" in _step(steps, "Run ops request")["run"]
    assert "ops_meta.py finalize" not in _step(steps, "Publish result to ops branch")["run"]


def test_the_receipt_is_published_beside_the_result_and_pruned_with_it(steps):
    body = _step(steps, "Publish result to ops branch")["run"]
    assert "${rid}.receipt.json" in body
    assert "ops/results/*.receipt.json" in body, (
        "receipts must be pruned with the results they belong to, or the transport "
        "accumulates one without the other"
    )


def test_a_run_that_never_finalized_still_publishes_a_receipt(steps):
    body = _step(steps, "Publish result to ops branch")["run"]
    assert "MISSING" in body, (
        "a missing receipt must be published as a missing receipt, not skipped — "
        "and must not abort publication"
    )


def test_the_workflow_asks_the_runner_which_deps_a_request_needs(steps):
    body = _step(steps, "Install full deps for Experiment OS reads")["run"]
    assert "ops_meta.py needs-full-deps" in body
    assert '= "xos" ' not in body, "the dependency test must not be re-hardcoded in shell"


@pytest.mark.parametrize("request_body,expected", [
    ({"type": "xos", "command": "control-tower"}, True),
    ({"type": "doctor"}, True),
    ({"type": "incident"}, True),
    ({"type": "db", "sql": "select 1"}, False),
    ({"type": "logs"}, False),
    ({"type": "capabilities"}, False),
    ({"type": "env"}, False),
    ({"type": "env", "set": {"LIVE_STRATEGIES": "Lmmsell10"}}, True),
    ({"type": "env", "set": {"LOG_LEVEL": "DEBUG"}}, False),
])
def test_the_dependency_decision_matches_what_each_request_reaches(request_body, expected):
    assert ops_meta.needs_full_deps(request_body) is expected


def test_a_malformed_request_never_triggers_an_install():
    assert ops_meta.needs_full_deps({"type": "env", "action": "set"}) is False
    assert ops_meta.needs_full_deps({"type": "nonsense"}) is False


# ---------------------------------------------------------------------------
# Verification: what a mutation says afterwards
# ---------------------------------------------------------------------------


@pytest.fixture
def railway(monkeypatch):
    """A fake Railway with the four calls this code makes and nothing else."""

    class Fake:
        def __init__(self) -> None:
            self.vars = {"KILL_SWITCH": "false", "LOG_LEVEL": "INFO"}
            self.redeployed = 0
            self.upsert_fails: set[str] = set()
            self.readback_drift: dict[str, str] = {}

        def graphql(self, query, variables, token, **kwargs):
            if "variableUpsert" in query:
                name = variables["input"]["name"]
                if name in self.upsert_fails:
                    raise railway_env.RailwayError("upstream refused")
                self.vars[name] = self.readback_drift.get(name, variables["input"]["value"])
                return {}
            if "serviceInstanceRedeploy" in query:
                self.redeployed += 1
                return {}
            return {"variables": dict(self.vars)}

    fake = Fake()
    monkeypatch.setattr(railway_env, "_graphql", fake.graphql)
    monkeypatch.setattr(railway_env, "_ctx", lambda: ("t", "p", "e", "s"))
    return fake


def test_a_verified_mutation_says_so_with_before_and_after(railway, capsys):
    status, receipt = railway_env.apply_set({"KILL_SWITCH": "true"})
    out = capsys.readouterr().out
    assert status == 0
    assert receipt["verdict"] == "VERIFIED"
    assert receipt["changes"]["KILL_SWITCH"] == {
        "before": "false", "after": "true", "requested": "true"}
    assert "# BEFORE:" in out and "# AFTER:" in out
    assert railway.redeployed == 1
    assert receipt["redeploy"] == "TRIGGERED"


def test_a_change_that_does_not_read_back_is_not_called_verified(railway):
    railway.readback_drift["KILL_SWITCH"] = "false"        # the write silently lost
    status, receipt = railway_env.apply_set({"KILL_SWITCH": "true"})
    assert status == 0                                     # the write was accepted…
    assert receipt["verdict"] == "APPLIED_BUT_UNVERIFIED"  # …and did not stick


def test_a_refused_write_is_a_failed_verdict(railway):
    railway.upsert_fails.add("KILL_SWITCH")
    status, receipt = railway_env.apply_set({"KILL_SWITCH": "true"})
    assert status == 1
    assert receipt["verdict"] == "FAILED"
    assert receipt["set_failed"] == ["KILL_SWITCH"]


def test_an_unreadable_railway_downgrades_the_verdict_rather_than_claiming_success(
        railway, monkeypatch):
    def unreadable():
        raise railway_env.RailwayError("Railway unreachable")

    monkeypatch.setattr(railway_env, "read_vars", unreadable)
    status, receipt = railway_env.apply_set({"KILL_SWITCH": "true"})
    assert status == 0
    assert receipt["verdict"] == "APPLIED_BUT_UNVERIFIED"


def test_a_redacted_variable_stays_redacted_on_both_sides(railway, capsys):
    secret_body = '{"command_id":"x","action":"ARM_CANARY"}'
    railway.vars["EXPERIMENT_OS_EXPERIMENT_COMMAND"] = "previous body"
    _, receipt = railway_env.apply_set(
        {"EXPERIMENT_OS_EXPERIMENT_COMMAND": secret_body})
    out = capsys.readouterr().out
    assert secret_body not in out
    assert "previous body" not in out
    change = receipt["changes"]["EXPERIMENT_OS_EXPERIMENT_COMMAND"]
    assert all(v.startswith("<redacted") for v in change.values())


def test_a_non_allowlisted_variable_is_still_refused_before_any_call(railway):
    status, receipt = railway_env.apply_set({"KALSHI_PRIVATE_KEY": "x"})
    assert status == 1
    assert receipt["verdict"] == "FAILED"
    assert railway.vars == {"KILL_SWITCH": "false", "LOG_LEVEL": "INFO"}


def test_canonical_checks_are_owed_only_where_they_mean_something():
    assert ops_meta.verification_hooks({"LIVE_STRATEGIES": "x"}) == ("enforcement", "readiness")
    assert ops_meta.verification_hooks({"EXPERIMENT_OS_CUTOVER_ID": "x"})
    assert ops_meta.verification_hooks({"LOG_LEVEL": "DEBUG"}) == ()


# ---------------------------------------------------------------------------
# doctor / incident stay read-only and bounded
# ---------------------------------------------------------------------------


def test_doctor_and_incident_cannot_write(monkeypatch):
    """The strongest statement available: the write path is booby-trapped."""
    import ops_doctor

    def forbidden(*args, **kwargs):
        raise AssertionError("a diagnostic command attempted a mutation")

    monkeypatch.setattr(railway_env, "apply_set", forbidden)
    monkeypatch.setattr(railway_env, "run_set", forbidden)
    monkeypatch.setattr(ops_doctor, "railway_vars", lambda service: {"KILL_SWITCH": "true"})
    monkeypatch.setattr(ops_doctor, "railway_deployment", lambda service: "deployment dep-1")
    monkeypatch.setattr(ops_doctor, "railway_logs_text",
                        lambda service, **kw: "line one\nline two")
    monkeypatch.setattr(ops_doctor, "db_rows", lambda *a, **k: ([], []))
    monkeypatch.setattr(ops_doctor, "xos_read", lambda argv: (True, "ENFORCEMENT: NEW_ONLY"))

    assert ops_doctor.doctor({}) == 0
    assert ops_doctor.incident({"service": "main", "window_minutes": 30}) == 0


def test_doctor_reports_an_unreachable_subsystem_instead_of_failing(monkeypatch, capsys):
    import ops_doctor

    def broken(*args, **kwargs):
        raise RuntimeError("Postgres unreachable")

    monkeypatch.setattr(ops_doctor, "db_rows", broken)
    monkeypatch.setattr(ops_doctor, "railway_deployment", broken)
    monkeypatch.setattr(ops_doctor, "railway_vars", broken)
    monkeypatch.setattr(ops_doctor, "xos_read", lambda argv: (False, "no database"))

    assert ops_doctor.doctor({}) == 0, "a partial snapshot is the point, not a failure"
    out = capsys.readouterr().out
    assert "WARNINGS" in out and "Postgres unreachable" in out


def test_the_incident_window_is_bounded(monkeypatch, capsys):
    import ops_doctor

    monkeypatch.setattr(ops_doctor, "db_rows", lambda *a, **k: ([], []))
    monkeypatch.setattr(ops_doctor, "railway_deployment", lambda service: "dep")
    monkeypatch.setattr(ops_doctor, "railway_vars", lambda service: {})
    monkeypatch.setattr(ops_doctor, "railway_logs_text", lambda service, **kw: "")
    monkeypatch.setattr(ops_doctor, "xos_read", lambda argv: (True, ""))

    assert ops_doctor.incident({"window_minutes": 99999}) == 0
    assert f"last {ops_doctor.MAX_WINDOW_MINUTES} minutes" in capsys.readouterr().out


def test_a_long_log_block_is_truncated_not_dumped(monkeypatch, capsys):
    import ops_doctor

    monkeypatch.setattr(ops_doctor, "db_rows", lambda *a, **k: ([], []))
    monkeypatch.setattr(ops_doctor, "railway_deployment", lambda service: "dep")
    monkeypatch.setattr(ops_doctor, "railway_vars", lambda service: {})
    monkeypatch.setattr(ops_doctor, "railway_logs_text",
                        lambda service, **kw: "\n".join(f"line {i}" for i in range(5000)))
    monkeypatch.setattr(ops_doctor, "xos_read", lambda argv: (True, ""))

    ops_doctor.incident({})
    out = capsys.readouterr().out
    assert "more lines (bounded)" in out
    assert "line 4999" not in out
