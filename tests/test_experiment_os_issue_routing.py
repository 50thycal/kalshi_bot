"""Routing policy — who owns a problem, and when the honest answer is "unknown".

Routing is the part of this workflow most likely to go quietly wrong, because a
mis-routed ticket looks exactly like a routed one. Two failure modes are pinned
hardest here:

  * **Everything routed to Platform Change Review.** A gate blocked because
    nobody has implemented its metric yet changes no shared semantic; sending it
    to that role invites a Platform Revision no evidence calls for. This is the
    same misreading the Control Tower's `BLOCK_OWNER` table already exists to
    stop, now enforced where tickets are created.
  * **Premature certainty.** "Zero evidence" cannot distinguish a broken runtime
    from a selection rule that correctly finds nothing. Guessing produces a
    STRATEGY ticket that Research Lab cannot act on, or an OPS ticket Live Ops
    closes as "working as intended". The policy returns UNCLASSIFIED instead.
"""

from __future__ import annotations

import pytest

from kalshi_bot.experiment_os import issue_policy as ipol
from kalshi_bot.experiment_os import issues as ix

LIVE_OPS = "LIVE_OPS"
RESEARCH = "RESEARCH_LAB"
PCR = "PLATFORM_CHANGE_REVIEW"


# ---------------------------------------------------------------------------
# Diagnosis before classification (§2.3)
# ---------------------------------------------------------------------------


def test_zero_evidence_routes_to_live_ops_and_stays_unclassified():
    """The Tower detects the absence; it cannot tell whether the runtime is
    broken, enforcement rejected the tag, filters ate every candidate, or there
    is genuinely nothing qualifying. Live Ops answers that first."""
    rec = ipol.recommend_owner(detector=ipol.DETECTOR_ZERO_EVIDENCE)
    assert rec.owner_role == LIVE_OPS
    assert rec.classification == "UNCLASSIFIED"
    assert "operational diagnosis" in rec.rationale
    # The rationale must not read as a claim that the cause IS operational.
    assert "UNCLASSIFIED until" in rec.rationale


def test_healthy_runtime_moves_the_zero_evidence_question_to_research_lab():
    rec = ipol.recommend_owner(detector=ipol.DETECTOR_ZERO_EVIDENCE,
                               runtime_verified_healthy=True)
    assert rec.owner_role == RESEARCH
    assert rec.classification == "STRATEGY"
    assert "selection rule" in rec.rationale


def test_a_criteria_question_waits_for_the_runtime_to_be_cleared():
    """Asking whether the selection rule is right while the worker may be broken
    is how Research Lab spends a week on a config typo."""
    rec = ipol.recommend_owner(selection_rule_question=True,
                               runtime_verified_healthy=False)
    assert rec.owner_role == LIVE_OPS
    assert "NOT verified healthy" in rec.rationale

    rec = ipol.recommend_owner(selection_rule_question=True,
                               runtime_verified_healthy=True)
    assert rec.owner_role == RESEARCH
    assert rec.classification == "STRATEGY"


def test_an_ambiguous_problem_stays_explicitly_unclassified():
    rec = ipol.recommend_owner()
    assert rec.classification == "UNCLASSIFIED"
    assert rec.requires_operator_triage is True
    assert "NOT a claim that the cause is operational" in rec.rationale


# ---------------------------------------------------------------------------
# Evaluator verdicts
# ---------------------------------------------------------------------------


def test_blocked_data_routes_to_research_lab_not_platform_change_review():
    """Writing a provider that never existed changes no shared semantic."""
    rec = ipol.recommend_owner(gate_verdict="BLOCKED_DATA")
    assert rec.owner_role == RESEARCH
    assert rec.classification == "DATA"
    assert "task-specific" in rec.rationale, (
        "metrics work is a legitimate alternative owner and must be named"
    )
    assert "NOT a Platform Revision" in rec.rationale


def test_blocked_platform_routes_to_platform_change_review():
    rec = ipol.recommend_owner(gate_verdict="BLOCKED_PLATFORM")
    assert rec.owner_role == PCR
    assert rec.classification == "PLATFORM"


@pytest.mark.parametrize("cause,owner,classification", [
    ("runtime", LIVE_OPS, "OPS"),
    ("contract", RESEARCH, "STRATEGY"),
    ("shared_semantics", PCR, "PLATFORM"),
])
def test_blocked_integrity_routes_by_cause(cause, owner, classification):
    rec = ipol.recommend_owner(gate_verdict="BLOCKED_INTEGRITY",
                               integrity_cause=cause)
    assert (rec.owner_role, rec.classification) == (owner, classification)


def test_blocked_integrity_with_an_unknown_cause_does_not_guess():
    """The same verdict has three owners depending on what produced it. Picking
    one blind is right a third of the time."""
    rec = ipol.recommend_owner(gate_verdict="BLOCKED_INTEGRITY")
    assert rec.owner_role == LIVE_OPS
    assert rec.classification == "INTEGRITY"
    assert rec.requires_operator_triage is True
    assert "Do not guess" in rec.rationale


# ---------------------------------------------------------------------------
# Operational detectors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["STALE", "EMPTY", "UNAVAILABLE"])
def test_collector_failure_routes_to_live_ops(status):
    rec = ipol.recommend_owner(detector=ipol.DETECTOR_COLLECTOR_HEALTH,
                               anomaly_kind=status)
    assert rec.owner_role == LIVE_OPS
    assert rec.classification == "OPS"
    assert rec.disposition == "OPS_REPAIR"
    assert "fails no gate" in rec.rationale


def test_a_stale_collector_outranks_an_empty_one():
    """STALE means a running collector stopped; EMPTY may simply never have run."""
    stale = ipol.recommend_owner(detector=ipol.DETECTOR_COLLECTOR_HEALTH,
                                 anomaly_kind="STALE")
    empty = ipol.recommend_owner(detector=ipol.DETECTOR_COLLECTOR_HEALTH,
                                 anomaly_kind="EMPTY")
    assert ipol.priority_rank(stale.priority) < ipol.priority_rank(empty.priority)


def test_unexpected_live_exposure_is_critical_p0_and_live_ops():
    rec = ipol.recommend_owner(unexpected_live_exposure=True)
    assert (rec.owner_role, rec.severity, rec.priority) == (LIVE_OPS, "CRITICAL", "P0")


def test_real_money_outranks_every_other_signal():
    """Being wrong about exposure is the expensive direction, so it wins even
    when a scientific-sounding signal is also present."""
    rec = ipol.recommend_owner(
        unexpected_live_exposure=True, shared_semantic_confirmed=True,
        gate_verdict="BLOCKED_DATA", new_hypothesis=True,
    )
    assert rec.owner_role == LIVE_OPS
    assert rec.priority == "P0"


def test_a_missing_twin_is_live_ops_and_high():
    rec = ipol.recommend_owner(detector=ipol.DETECTOR_MISSING_TWIN)
    assert (rec.owner_role, rec.severity, rec.priority) == (LIVE_OPS, "HIGH", "P1")
    assert "adverse selection" in rec.rationale


def test_a_degraded_resolver_is_live_ops_and_high():
    rec = ipol.recommend_owner(detector=ipol.DETECTOR_RESOLVER_DEGRADED)
    assert (rec.owner_role, rec.severity, rec.priority) == (LIVE_OPS, "HIGH", "P1")


# ---------------------------------------------------------------------------
# Platform Change Review is narrow (§2.4)
# ---------------------------------------------------------------------------


def test_a_confirmed_shared_semantic_change_routes_to_platform_change_review():
    rec = ipol.recommend_owner(shared_semantic_confirmed=True)
    assert rec.owner_role == PCR
    assert rec.classification == "PLATFORM"
    assert rec.disposition == "PLATFORM_REVISION"


def test_a_fill_model_change_is_the_worked_example(xos_session, xos_platform):
    """A shared fill-model semantic change is the canonical PLATFORM issue, and
    the disposition may only be recorded once it is owned by that role."""
    s = xos_session
    assert "FILL_MODEL" in ipol.PLATFORM_SEMANTICS
    rec = ipol.recommend_owner(shared_semantic_confirmed=True)
    issue = ix.create_issue(
        s, title="fill model now assumes queue position on partial fills",
        opened_by_role="PLATFORM_CHANGE_REVIEW",
        current_owner_role=rec.owner_role, classification=rec.classification,
    )
    for status in ("TRIAGE", "INVESTIGATING", "ACTION_REQUIRED"):
        ix.set_issue_status(s, issue, status=status, actor="p", actor_role=PCR,
                            reason="working")
    ix.record_disposition(
        s, issue, disposition="PLATFORM_REVISION", actor="p", actor_role=PCR,
        requires_platform_revision=True,
        reason="every experiment pinning the old fill model is affected",
    )
    s.commit()
    assert issue.disposition == "PLATFORM_REVISION"
    assert issue.current_owner_role == PCR


@pytest.mark.parametrize("kwargs", [
    {"gate_verdict": "BLOCKED_DATA"},                       # missing provider
    {"detector": ipol.DETECTOR_COLLECTOR_HEALTH, "anomaly_kind": "STALE"},
    {"detector": ipol.DETECTOR_ZERO_EVIDENCE},              # wiring / filters unknown
    {"malformed_frozen_contract": True},                    # bad experiment contract
])
def test_touching_experiment_os_is_not_enough_for_platform_change_review(kwargs):
    """§2.4, enforced: a missing provider, a missing transform, a broken
    collector, a wiring bug and a malformed contract are none of them a change
    to shared semantics."""
    assert ipol.recommend_owner(**kwargs).owner_role != PCR


# ---------------------------------------------------------------------------
# Contracts and research
# ---------------------------------------------------------------------------


def test_a_malformed_frozen_contract_is_research_lab_and_new_version():
    """A frozen Version is immutable, so the remedy is never an in-place repair."""
    rec = ipol.recommend_owner(malformed_frozen_contract=True)
    assert rec.owner_role == RESEARCH
    assert rec.classification == "STRATEGY"
    assert rec.disposition == "NEW_VERSION"
    assert "cannot be repaired in place" in rec.rationale


def test_a_registered_contract_defect_detector_routes_the_same_way():
    rec = ipol.recommend_owner(detector=ipol.DETECTOR_CONTRACT_DEFECT)
    assert (rec.owner_role, rec.classification, rec.disposition) == (
        RESEARCH, "STRATEGY", "NEW_VERSION")


def test_a_new_hypothesis_is_research_lab_and_low_priority():
    rec = ipol.recommend_owner(new_hypothesis=True)
    assert rec.owner_role == RESEARCH
    assert rec.priority == "P3"
    assert rec.disposition == "RESEARCH_ONLY"


def test_unmapped_legacy_history_routes_to_legacy_migration():
    rec = ipol.recommend_owner(legacy_history_problem=True)
    assert rec.owner_role == "LEGACY_MIGRATION"


# ---------------------------------------------------------------------------
# Structural guarantees about the role set
# ---------------------------------------------------------------------------


def test_there_is_no_generic_fixer_role():
    roles = {r.value for r in ipol.IssueOwnerRole}
    assert "FIXER" not in roles
    assert "INVESTIGATION" not in roles
    assert "EXPERIMENT_CONTROL_TOWER" not in roles
    assert roles == {
        "LIVE_OPS", "RESEARCH_LAB", "PLATFORM_CHANGE_REVIEW",
        "LEGACY_MIGRATION", "TASK_SPECIFIC", "OPERATOR",
    }


def test_every_recommendation_names_a_real_owner_role():
    """A recommendation that cannot be assigned is not a recommendation."""
    signals = [
        {}, {"detector": ipol.DETECTOR_ZERO_EVIDENCE},
        {"detector": ipol.DETECTOR_COLLECTOR_HEALTH, "anomaly_kind": "EMPTY"},
        {"detector": ipol.DETECTOR_INTEGRITY_EVENT},
        {"detector": ipol.DETECTOR_INTEGRITY_EVENT,
         "anomaly_kind": "HELD_CONSTANT_CHANGED"},
        {"detector": ipol.DETECTOR_PLATFORM_IMPACT},
        {"detector": ipol.DETECTOR_REVISION_UNSAFE},
        {"detector": ipol.DETECTOR_MISSING_TWIN},
        {"detector": ipol.DETECTOR_UNSTAMPED_LINEAGE},
        {"detector": ipol.DETECTOR_GATE_BLOCKED, "gate_verdict": "BLOCKED_DATA"},
        {"gate_verdict": "BLOCKED_INTEGRITY"},
        {"new_hypothesis": True}, {"legacy_history_problem": True},
        {"shared_semantic_confirmed": True}, {"unexpected_live_exposure": True},
    ]
    owners = {r.value for r in ipol.IssueOwnerRole}
    classes = {c.value for c in ipol.IssueClassification}
    severities = {s.value for s in ipol.IssueSeverity}
    priorities = {p.value for p in ipol.IssuePriority}
    for signal in signals:
        rec = ipol.recommend_owner(**signal)
        assert rec.owner_role in owners, signal
        assert rec.classification in classes, signal
        assert rec.severity in severities, signal
        assert rec.priority in priorities, signal
        assert rec.rationale.strip(), signal


def test_severity_and_priority_are_independent_axes():
    """A high-impact problem nobody can act on yet is not the next thing to do,
    so the two must not be derivable from each other."""
    pairs = set()
    for signal in ({"detector": ipol.DETECTOR_ZERO_EVIDENCE},
                   {"detector": ipol.DETECTOR_COLLECTOR_HEALTH,
                    "anomaly_kind": "STALE"},
                   {"gate_verdict": "BLOCKED_DATA"},
                   {"unexpected_live_exposure": True},
                   {"new_hypothesis": True}):
        rec = ipol.recommend_owner(**signal)
        pairs.add((rec.severity, rec.priority))
    assert len({s for s, _ in pairs}) > 1 and len({p for _, p in pairs}) > 1
    # MEDIUM severity appears at more than one priority — proof they are not
    # a relabelling of the same axis.
    assert ("MEDIUM", "P1") in pairs and ("MEDIUM", "P2") in pairs


def test_nothing_is_hardcoded_to_one_severity():
    seen = {
        ipol.recommend_owner(**s).severity
        for s in ({"unexpected_live_exposure": True},
                  {"detector": ipol.DETECTOR_MISSING_TWIN},
                  {"detector": ipol.DETECTOR_COLLECTOR_HEALTH,
                   "anomaly_kind": "STALE"},
                  {"detector": ipol.DETECTOR_ZERO_EVIDENCE},
                  {"new_hypothesis": True})
    }
    assert seen == {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}


def test_a_caller_may_override_the_recommendation(xos_session, xos_platform):
    """The policy recommends; the human decides. `create_issue` must not
    substitute its own opinion for a stated one."""
    s = xos_session
    issue = ix.create_issue(
        s, title="zero evidence but we already know why",
        opened_by_role="OPERATOR", detector=ipol.DETECTOR_ZERO_EVIDENCE,
        current_owner_role="RESEARCH_LAB", classification="STRATEGY",
        severity="HIGH", priority="P1",
        owner_rationale="the operator reproduced the empty candidate set by hand",
    )
    s.commit()
    assert (issue.current_owner_role, issue.classification, issue.severity,
            issue.priority) == ("RESEARCH_LAB", "STRATEGY", "HIGH", "P1")
