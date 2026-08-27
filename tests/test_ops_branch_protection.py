"""XOS-000007 — the ops transport is not force-refreshed, and cannot be.

`ops` carries three things at once: every session's in-flight `ops/request.json`,
the durable per-run `ops/results/*.txt` files that let concurrent producers each
read their own output, and the Ops Runner workflow FILE that GitHub Actions loads
for a push to this branch. A force-refresh rewrites all three — dropping results,
clobbering another session's request, and potentially reinstating a workflow file
predating the default-branch checkout that XOS-000005 installed.

Two halves, and both are needed. The capability is taken away by a repository
ruleset on `refs/heads/ops` (`deletion` + `non_fast_forward`), whose desired state
is checked in here and applied by the `Ops Branch Protection` workflow. The
INSTRUCTIONS are policed by this test, because documentation drifts back: the
doc/allowlist test alone was what failed to catch XOS-000005.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
RULESET = REPO / ".github/rulesets/ops-transport-guard.json"
PROTECT_WF = REPO / ".github/workflows/ops-branch-protection.yml"
RUNBOOK = REPO / "docs/OPS_RUNBOOK.md"

# Every surface a session actually reads for standing instructions. Historical
# research write-ups under docs/ are included deliberately: a stale "refresh ops"
# line in a journal entry is still an instruction someone can follow.
INSTRUCTION_FILES = sorted(
    {
        REPO / "CLAUDE.md",
        *(REPO / "docs").rglob("*.md"),
        *(REPO / ".claude/sessions").glob("*"),
        *(REPO / ".claude/skills").rglob("*.md"),
    }
)

# A force-refresh of ops, in the forms the retired instructions actually used.
FORCE_REFRESH = re.compile(
    r"""(
          git\s+push\s+(-[^\s]*\s+)*(-f|--force)\b(?![-\s]*with-lease)[^\n]*\bops\b
        | git\s+checkout\s+-B\s+ops\b
        | push\s+-f\s+origin\s+ops\b
        )""",
    re.VERBOSE,
)


def _ruleset() -> dict:
    return json.loads(RULESET.read_text())


def test_ruleset_targets_only_the_ops_branch():
    rs = _ruleset()
    assert rs["target"] == "branch"
    assert rs["enforcement"] == "active"
    assert rs["conditions"]["ref_name"]["include"] == ["refs/heads/ops"]
    # A bypass actor would hand the capability straight back to ordinary sessions.
    assert rs.get("bypass_actors") == []


def test_ruleset_blocks_force_push_and_deletion():
    types = {r["type"] for r in _ruleset()["rules"]}
    assert "non_fast_forward" in types, "force pushes to ops must be rejected"
    assert "deletion" in types, "ops must not be deletable"


def test_ruleset_never_blocks_the_transport_itself():
    """A protection that stops ordinary requests is worse than the hazard.

    Request commits and the runner's own result commits are direct fast-forward
    pushes with no review and no checks. Any of these rules would silently take
    the channel down, so they are asserted absent rather than merely 'not added'.
    """
    types = {r["type"] for r in _ruleset()["rules"]}
    for forbidden in (
        "pull_request",
        "required_status_checks",
        "required_linear_history",
        "required_signatures",
        "required_deployments",
        "creation",
        "update",
    ):
        assert forbidden not in types, (
            f"{forbidden!r} would block ordinary ops request/result commits"
        )


def test_protection_workflow_applies_the_checked_in_ruleset():
    wf = yaml.safe_load(PROTECT_WF.read_text())
    job = wf["jobs"]["protect"]
    # `administration` is not a permission the built-in GITHUB_TOKEN can hold, so
    # the workflow must reach for an explicit admin PAT and keep its own default
    # permissions minimal.
    assert wf["permissions"] == {"contents": "read"}
    assert wf["concurrency"] == {
        "group": "ops-branch-protection",
        "cancel-in-progress": False,
    }, "ruleset apply is list-then-create/update and must be serialized"
    body = PROTECT_WF.read_text()
    assert "OPS_ADMIN_TOKEN" in body
    assert ".github/rulesets/ops-transport-guard.json" in body, (
        "the workflow must apply the checked-in desired state, not an inline literal"
    )
    triggers = set(wf[True])
    assert "workflow_dispatch" in triggers, "an operator must be able to re-apply on demand"
    # A push only applies on the DEFAULT branch — a feature branch push reports.
    assert "github.event.repository.default_branch" in body
    assert any("Verify" in (s.get("name") or "") for s in job["steps"])


@pytest.mark.parametrize("path", INSTRUCTION_FILES, ids=lambda p: str(p.relative_to(REPO)))
def test_no_standing_instruction_to_force_refresh_ops(path: pathlib.Path):
    if path == RUNBOOK:
        pytest.skip("the runbook documents the deliberate exception; asserted below")
    text = path.read_text(errors="ignore")
    for line in text.splitlines():
        match = FORCE_REFRESH.search(line)
        assert not match, (
            f"{path.relative_to(REPO)} still tells a session to force-refresh ops "
            f"({line.strip()!r}). After XOS-000005 the runner sources its code from "
            "the default branch on every request, so there is nothing to refresh."
        )


def test_runbook_documents_the_deliberate_maintenance_procedure():
    text = RUNBOOK.read_text()
    assert "### Protecting the `ops` branch" in text
    required = {
        "idle channel check": '{"type":"noop"}',
        "backup branch": "ops-backup-",
        "expected-SHA lease": "--force-with-lease=refs/heads/ops:",
        "post-change validation": "OPS_RUNNER_CODE_SOURCE",
        "recovery": "Recovery",
        "ruleset name": "ops-transport-guard",
    }
    missing = [k for k, needle in required.items() if needle not in text]
    assert not missing, f"maintenance procedure is missing: {missing}"


def test_admin_token_is_temporary_and_removed_after_validation():
    """The high-privilege PAT must not become a standing repository credential."""
    workflow = PROTECT_WF.read_text()
    runbook = RUNBOOK.read_text()
    for text in (workflow, runbook):
        assert "shortest practical expiration" in text
        assert "delete the secret" in text
        assert "revoke the PAT" in text
    assert "ruleset remains active" in workflow
    assert "protection remains active" in runbook


def test_runbook_only_uses_a_lease_never_a_bare_force():
    """Even inside the documented exception, `-f` must not reappear."""
    for line in RUNBOOK.read_text().splitlines():
        if "force-with-lease" in line:
            continue
        assert not FORCE_REFRESH.search(line), (
            f"the runbook's maintenance procedure must lease, not force: {line.strip()!r}"
        )
