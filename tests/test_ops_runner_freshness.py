"""XOS-000005 — the runner cannot go stale, and it fails closed if it might have.

The documentation/allowlist test in `test_ops_channel_receipt_reads.py` catches a
command that is documented but not allowlisted. It cannot catch what actually
happened in production: the default branch was correct all along, and the copy of
the runner DEPLOYED on the long-lived `ops` transport branch was months behind.
Both sides of that test were green while the channel refused valid work.

So the repair is in the workflow: the ops branch is the transport, and the CODE
comes from the default branch on every run. These tests pin that source-selection
invariant against the real workflow file, and pin the runner's fail-closed guard
for the case where someone regresses the workflow later.
"""

from __future__ import annotations

import pathlib
import sys

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github/workflows/ops-runner.yml"
sys.path.insert(0, str(REPO / "scripts"))


@pytest.fixture(scope="module")
def workflow() -> dict:
    # `on:` is parsed by PyYAML as the boolean True (YAML 1.1); irrelevant here,
    # but it is why this reads `jobs` and never `on`.
    return yaml.safe_load(WORKFLOW.read_text())


@pytest.fixture(scope="module")
def steps(workflow) -> list[dict]:
    return workflow["jobs"]["run"]["steps"]


def _checkouts(steps: list[dict]) -> list[dict]:
    return [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout")]


def _run_step(steps: list[dict]) -> dict:
    return next(s for s in steps if s.get("name") == "Run ops request")


# ---------------------------------------------------------------------------
# The source-selection invariant
# ---------------------------------------------------------------------------


def test_the_workflow_checks_out_both_the_transport_and_the_code(steps):
    checkouts = _checkouts(steps)
    assert len(checkouts) == 2, (
        "expected exactly two checkouts: the ops transport, and the default-branch code"
    )
    transport, code = checkouts
    # The transport is the triggering ref (ops), at the workspace root, so the
    # publish and archive steps keep working unchanged.
    assert "ref" not in (transport.get("with") or {})
    assert "path" not in (transport.get("with") or {})
    # The code checkout is explicitly pinned to the DEFAULT branch.
    assert code["with"]["ref"] == "${{ github.event.repository.default_branch }}"
    assert code["with"]["path"] == ".ops-runner-code"


def test_the_runner_is_executed_from_the_default_branch_checkout(steps):
    """The invariant itself: no code is executed out of the transport branch."""
    body = _run_step(steps)["run"]
    assert ".ops-runner-code/scripts/ops_runner.py" in body
    # ...and never the transport copy.
    assert "python scripts/ops_runner.py" not in body


def test_dependencies_are_installed_from_the_default_branch_checkout(steps):
    """A current runner against a stale requirements.txt is still a stale runner."""
    install = next(s for s in steps if s.get("name") == "Install full deps for Experiment OS reads")
    assert ".ops-runner-code/requirements.txt" in install["run"]


def test_the_transport_is_used_only_as_the_request_and_result_channel(steps):
    """The ops checkout supplies request.json and receives results — never code."""
    run = _run_step(steps)
    assert run["env"]["OPS_REQUEST_PATH"] == "${{ github.workspace }}/ops/request.json"
    publish = next(s for s in steps if s.get("name") == "Publish result to ops branch")
    assert "origin/ops" in publish["run"]
    assert ".ops-runner-code" not in publish["run"]


def test_the_workflow_attests_the_code_source_to_the_runner(steps):
    import ops_runner

    env = _run_step(steps)["env"]
    assert env[ops_runner.CODE_SOURCE_ENV] == ops_runner.EXPECTED_CODE_SOURCE


def test_the_workflow_still_only_triggers_on_the_ops_request_file(workflow):
    """Guard the surrounding contract while editing the workflow: results are
    published back to `ops`, so a broader trigger would loop."""
    trigger = workflow[True] if True in workflow else workflow["on"]
    assert trigger["push"]["branches"] == ["ops"]
    assert trigger["push"]["paths"] == ["ops/request.json"]


# ---------------------------------------------------------------------------
# The fail-closed guard, for when someone regresses the workflow
# ---------------------------------------------------------------------------


def test_the_runner_refuses_to_serve_when_it_cannot_prove_it_is_current(monkeypatch):
    import ops_runner

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv(ops_runner.CODE_SOURCE_ENV, raising=False)
    assert ops_runner.refuse_if_stale() == 1


def test_a_wrong_code_source_attestation_is_also_refused(monkeypatch):
    import ops_runner

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv(ops_runner.CODE_SOURCE_ENV, "ops-branch")
    assert ops_runner.refuse_if_stale() == 1


def test_the_correct_attestation_is_accepted(monkeypatch):
    import ops_runner

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv(ops_runner.CODE_SOURCE_ENV, ops_runner.EXPECTED_CODE_SOURCE)
    assert ops_runner.refuse_if_stale() is None


def test_the_guard_is_inert_outside_github_actions(monkeypatch):
    """A developer running the runner locally is looking at the checkout."""
    import ops_runner

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv(ops_runner.CODE_SOURCE_ENV, raising=False)
    assert ops_runner.refuse_if_stale() is None


def test_a_stale_runner_serves_no_request_at_all(monkeypatch, tmp_path, capsys):
    """Fail closed end to end: serve() returns before dispatching the request."""
    import ops_runner

    request = tmp_path / "request.json"
    request.write_text('{"type":"noop"}')
    monkeypatch.setattr(ops_runner, "REQUEST_PATH", str(request))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv(ops_runner.CODE_SOURCE_ENV, raising=False)

    assert ops_runner.serve() == 1
    err = capsys.readouterr().err
    assert "REFUSING TO SERVE" in err
    # The message must tell the operator what to do, not just that it failed.
    assert "ops-runner.yml" in err


def test_the_guard_does_not_fire_on_plain_dispatch_under_actions(monkeypatch, tmp_path, capsys):
    """The regression this test exists for: the guard used to live in main().

    CI's own test job runs under GitHub Actions and never sets the attestation,
    and several existing tests dispatch a request by calling main() directly — so
    a guard keyed on "am I under Actions" refused them and broke the build. The
    question it must ask is "am I SERVING a request", which is what serve() means.
    """
    import ops_runner

    request = tmp_path / "request.json"
    request.write_text('{"type":"noop"}')
    monkeypatch.setattr(ops_runner, "REQUEST_PATH", str(request))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv(ops_runner.CODE_SOURCE_ENV, raising=False)

    assert ops_runner.main() == 0
    assert "REFUSING TO SERVE" not in capsys.readouterr().err


def test_serve_dispatches_when_the_attestation_is_present(monkeypatch, tmp_path):
    import ops_runner

    request = tmp_path / "request.json"
    request.write_text('{"type":"noop"}')
    monkeypatch.setattr(ops_runner, "REQUEST_PATH", str(request))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv(ops_runner.CODE_SOURCE_ENV, ops_runner.EXPECTED_CODE_SOURCE)

    assert ops_runner.serve() == 0


def test_the_script_entry_point_serves_rather_than_dispatching():
    """The guard is only worth anything if the production entry point calls it."""
    source = pathlib.Path(REPO / "scripts/ops_runner.py").read_text()
    assert "raise SystemExit(serve())" in source
    assert "raise SystemExit(main())" not in source


def test_the_refusal_names_the_defect_it_prevents(monkeypatch, capsys):
    import ops_runner

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv(ops_runner.CODE_SOURCE_ENV, raising=False)
    ops_runner.refuse_if_stale()
    assert "XOS-000005" in capsys.readouterr().err
