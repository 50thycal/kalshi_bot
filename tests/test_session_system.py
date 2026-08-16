"""Guard for the Claude Session System (docs/CLAUDE_SESSION_SYSTEM.md).

The failure this prevents is specific and has happened before in this repo:
several generations of status workflow ended up active at once, each claiming to
be the current way to determine experiment state, and different sessions reached
different conclusions. Experiment OS is now the single source of truth, so the
instruction surface has to stay single too — that is not something code enforces
on its own, so it is asserted here.
"""

from __future__ import annotations

import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SESSIONS = ROOT / ".claude" / "sessions"
SKILLS = ROOT / ".claude" / "skills"

# Session-facing workflows replaced by the Control Tower + role playbooks. Git
# history is the reference; an active skill directory is an instruction surface.
RETIRED_SKILLS = (
    "full-update",
    "kalshi_Loop_checker",
    "kalshi_loop_checker_phase_3",
    "mm_check_1",
    "mmsell-seasonal-check",
)

REQUIRED_ROLES = (
    "experiment-control-tower",
    "evo-control-tower",
    "evo-ticket-workshop",
    "platform-change-review",
    "research-lab",
    "experiment-migration",
    "live-ops",
)

REQUIRED_SECTIONS = (
    "## PURPOSE",
    "## DEFAULT MODE / PERMISSIONS",
    "## LOAD FIRST",
    "## STARTUP ROUTINE",
    "## STANDARD WORKFLOW",
    "## STANDARD OUTPUT",
    "## HANDOFF / ROLE-CHANGE RULES",
    "## MAY MODIFY",
    "## MUST NOT MODIFY",
    "## SHARED SKILLS",
    "## CLEANUP / SESSION END",
)


@pytest.mark.parametrize("name", RETIRED_SKILLS)
def test_retired_session_workflows_are_gone(name):
    assert not (SKILLS / name).exists(), (
        f".claude/skills/{name} is back. It was retired because it reconstructs "
        "experiment state outside Experiment OS and competes with the Experiment "
        "Control Tower. If you need its analysis, call the underlying script; if "
        "you need its report, run the Control Tower."
    )


@pytest.mark.parametrize("role", REQUIRED_ROLES)
def test_role_playbook_exists_and_has_the_schema(role):
    path = SESSIONS / f"{role}.md"
    assert path.exists(), f"missing role playbook {path}"
    body = path.read_text()
    missing = [s for s in REQUIRED_SECTIONS if s not in body]
    assert not missing, f"{role}.md is missing sections: {missing}"


def test_router_is_reachable_from_every_new_session():
    """CLAUDE.md is auto-loaded and the hook prints the menu — both must route."""
    claude_md = (ROOT / "CLAUDE.md").read_text()
    assert "Which session role should I follow?" in claude_md
    assert ".claude/sessions/" in claude_md
    assert "sticky" in claude_md.lower()

    router = (SESSIONS / "ROUTER.txt").read_text()
    assert "Which session role should I follow?" in router

    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
    hooks = settings.get("hooks", {}).get("SessionStart", [])
    commands = [h.get("command", "") for entry in hooks for h in entry.get("hooks", [])]
    assert any("ROUTER.txt" in c for c in commands), (
        "the SessionStart hook no longer surfaces the role menu"
    )


def test_claude_md_points_at_experiment_os_as_canonical():
    body = (ROOT / "CLAUDE.md").read_text()
    assert "NEW_ONLY" in body
    assert "Experiment OS" in body
    # The router must not grow back into a status catalogue: the whole point of
    # the refactor is that current standings live in Experiment OS, not here.
    assert len(body.splitlines()) < 200, (
        "CLAUDE.md is growing back into an operating diary — current experiment "
        "state belongs in Experiment OS, procedures in skills/playbooks/docs"
    )


def test_no_active_instruction_advertises_a_retired_workflow():
    """A retired name may appear in history/records, never as a live instruction."""
    surfaces = [ROOT / "CLAUDE.md", *SESSIONS.glob("*.md"), SESSIONS / "ROUTER.txt"]
    for path in surfaces:
        body = path.read_text()
        for name in RETIRED_SKILLS:
            assert name not in body, (
                f"{path.name} references the retired workflow {name!r}. Point at "
                "the Control Tower or the underlying analysis script instead."
            )


def test_control_tower_is_read_only_by_construction():
    """The Tower must not be able to write, whatever a future edit intends."""
    src = (ROOT / "kalshi_bot" / "experiment_os" / "control_tower.py").read_text()
    assert "persist=False" in src, "gate evaluation must stay a dry run"
    for forbidden in ("record_enforcement_change", "transition_experiment",
                      "session.commit()", "register_deployment", "apply_new_epoch"):
        assert forbidden not in src, (
            f"control_tower.py calls {forbidden!r} — the Control Tower is READ ONLY"
        )
