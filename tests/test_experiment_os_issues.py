"""Issue-workflow model and service invariants.

The tests here target the properties that make an investigation record TRUSTWORTHY
rather than merely present:

  * a ticket can never move an experiment, a gate or a verdict — that is what
    makes it safe to open one on a suspicion;
  * history is append-only, so an investigation cannot be tidied up afterwards;
  * lineage is validated, so a ticket never points a later reader at the wrong
    contract;
  * a resolution means something: it needs a summary and either a passed
    validation or an explicitly recorded waiver.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kalshi_bot.experiment_os import issue_policy as ipol
from kalshi_bot.experiment_os import issues as ix
from kalshi_bot.experiment_os import read
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.service import ImmutableRecord

UTC = timezone.utc
T0 = datetime(2026, 8, 1, tzinfo=UTC)

#: Every detector constant, so a new one cannot quietly outgrow its column.
_ALL_DETECTORS = [
    getattr(ipol, name) for name in dir(ipol) if name.startswith("DETECTOR_")
    and isinstance(getattr(ipol, name), str)
]


def _experiment(s, key="book-a", *, tag=None):
    """A fully registered experiment: version, arm, frozen contract, epoch,
    deployment, gate — everything an issue may legitimately be scoped to."""
    tag = tag or f"{key}-t"
    exp = svc.create_experiment(s, key=key, origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="lever", now=T0,
        control_exemption_reason="test shape: absolute threshold",
    )
    svc.add_arm(s, ver, arm_key="treatment", role="treatment", strategy_tag=tag)
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    dep = svc.register_deployment(
        s, epoch, deployment_key=f"{key}-paper-1", stage="PAPER", kind="paper",
        arms={"treatment": tag}, started_at=T0,
    )
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    gate = svc.register_gate(
        s, ver, gate_key="paper_to_live_canary", kind="promotion",
        spec={"pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                            "op": ">", "value": 0}]},
        from_state="PAPER", to_state="LIVE_CANARY", registered_at=T0,
    )
    s.commit()
    return exp, ver, epoch, dep, gate


def _open(s, **kwargs):
    kwargs.setdefault("title", "something is wrong")
    kwargs.setdefault("opened_by_role", "EXPERIMENT_CONTROL_TOWER")
    issue = ix.create_issue(s, **kwargs)
    s.commit()
    return issue


def _walk_to(s, issue, status):
    """Advance an issue along the legal path to `status`."""
    path = ["TRIAGE", "INVESTIGATING", "ACTION_REQUIRED", "VALIDATING"]
    for step in path:
        if issue.status == status:
            break
        ix.set_issue_status(s, issue, status=step, actor="a", actor_role="LIVE_OPS",
                            reason="advancing for the test")
        if step == status:
            break
    s.commit()
    return issue


# ---------------------------------------------------------------------------
# Identity and creation
# ---------------------------------------------------------------------------


def test_issue_keys_are_stable_unique_and_cli_shaped(xos_session, xos_platform):
    s = xos_session
    keys = [_open(s, title=f"problem {n}").issue_key for n in range(3)]
    assert keys == ["XOS-000001", "XOS-000002", "XOS-000003"]
    assert len(set(keys)) == 3
    # Zero padded so lexical order equals numeric order, and usable as a word.
    assert all(" " not in k for k in keys)
    assert ix.get_issue(s, "XOS-000002").title == "problem 1"
    assert ix.get_issue(s, "xos-000002") is not None, "lookup is case-insensitive"


def test_the_title_is_not_identity(xos_session, xos_platform):
    """Two issues may legitimately share a title; they are still distinct
    investigations, and renaming one must not change what it is."""
    s = xos_session
    a = _open(s, title="zero evidence")
    b = _open(s, title="zero evidence")
    assert a.issue_key != b.issue_key


def test_creation_records_the_routing_recommendation(xos_session, xos_platform):
    s = xos_session
    issue = _open(s, detector=ipol.DETECTOR_ZERO_EVIDENCE)
    assert issue.current_owner_role == "LIVE_OPS"
    assert issue.classification == "UNCLASSIFIED", (
        "zero evidence with an unknown cause must NOT be pre-classified"
    )
    created = ix.issue_events(s, issue)[0]
    assert created.event_type == "CREATED"
    assert created.payload_json["recommended_owner"]["owner_role"] == "LIVE_OPS"


def test_an_owner_override_is_recorded_as_a_disagreement(xos_session, xos_platform):
    """The policy recommends; it does not decide. But a rule that is overridden
    every time is a rule that is wrong, so the disagreement is durable."""
    s = xos_session
    issue = _open(
        s, detector=ipol.DETECTOR_ZERO_EVIDENCE, current_owner_role="RESEARCH_LAB",
        owner_rationale="operator already verified the runtime by hand",
    )
    override = ix.issue_events(s, issue)[0].payload_json["owner_override"]
    assert override["chosen"] == "RESEARCH_LAB"
    assert override["recommended"] == "LIVE_OPS"
    assert "verified the runtime" in override["rationale"]


def test_the_control_tower_may_open_but_may_never_own(xos_session, xos_platform):
    """It is read-only, so it can detect a problem and can never be the role that
    fixes one. There is no generic fixer role to fall back on either."""
    s = xos_session
    assert _open(s, opened_by_role="EXPERIMENT_CONTROL_TOWER").issue_key

    for refused in ("EXPERIMENT_CONTROL_TOWER", "SYSTEM", "FIXER", "INVESTIGATION"):
        with pytest.raises(ipol.IssuePolicyError, match="may not own"):
            _open(s, current_owner_role=refused)


def test_an_unknown_role_names_the_legal_set(xos_session, xos_platform):
    s = xos_session
    with pytest.raises(ipol.IssuePolicyError, match="RESEARCH_LAB"):
        _open(s, current_owner_role="RESEARCH")


# ---------------------------------------------------------------------------
# Lineage (§3.2)
# ---------------------------------------------------------------------------


def test_valid_lineage_is_accepted_and_ancestors_are_derived(xos_session,
                                                             xos_platform):
    """Naming a gate already determines its version and experiment, so the
    foreign keys get populated without the caller restating them."""
    s = xos_session
    exp, ver, epoch, dep, gate = _experiment(s)
    issue = _open(s, gate=gate)
    assert issue.gate_id == gate.id
    assert issue.version_id == ver.id
    assert issue.experiment_id == exp.id


def test_a_version_from_another_experiment_is_refused(xos_session, xos_platform):
    s = xos_session
    exp_a, ver_a, *_ = _experiment(s, "book-a")
    exp_b, _ver_b, *_ = _experiment(s, "book-b")
    with pytest.raises(ix.IssueScopeError, match="experiment"):
        _open(s, experiment=exp_b, version=ver_a)


def test_an_epoch_from_another_version_is_refused(xos_session, xos_platform):
    s = xos_session
    _exp_a, _ver_a, epoch_a, *_ = _experiment(s, "book-a")
    _exp_b, ver_b, *_ = _experiment(s, "book-b")
    with pytest.raises(ix.IssueScopeError, match="version"):
        _open(s, version=ver_b, epoch=epoch_a)


def test_a_deployment_from_another_epoch_is_refused(xos_session, xos_platform):
    s = xos_session
    _a, _va, _ea, dep_a, _ga = _experiment(s, "book-a")
    _b, _vb, epoch_b, _db, _gb = _experiment(s, "book-b")
    with pytest.raises(ix.IssueScopeError, match="epoch"):
        _open(s, epoch=epoch_b, deployment=dep_a)


def test_a_platform_only_issue_needs_no_experiment(xos_session, xos_platform):
    """A collector outage belongs to no experiment. Forcing one would be a lie
    with a foreign key on it."""
    s = xos_session
    issue = _open(s, detector=ipol.DETECTOR_COLLECTOR_HEALTH,
                  anomaly_kind="weather_forecasts:STALE")
    assert issue.experiment_id is None
    assert read.issue_scope_label(s, issue) == "system-wide"


def test_a_nonexistent_scope_object_is_refused(xos_session, xos_platform):
    s = xos_session
    with pytest.raises(ix.IssueScopeError, match="does not exist"):
        _open(s, experiment=99999)


# ---------------------------------------------------------------------------
# Status machine (§3.5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("current,target", [
    ("OPEN", "TRIAGE"),
    ("TRIAGE", "INVESTIGATING"),
    ("INVESTIGATING", "ACTION_REQUIRED"),
    ("ACTION_REQUIRED", "VALIDATING"),
    ("VALIDATING", "RESOLVED"),
    ("OPEN", "CLOSED_NO_ACTION"),
    ("INVESTIGATING", "CLOSED_NO_ACTION"),
    ("ACTION_REQUIRED", "DUPLICATE"),
    ("RESOLVED", "INVESTIGATING"),
    ("CLOSED_NO_ACTION", "INVESTIGATING"),
])
def test_legal_transitions(current, target):
    ipol.validate_issue_status_transition(current, target)


@pytest.mark.parametrize("current,target", [
    ("OPEN", "RESOLVED"),          # no investigation happened
    ("OPEN", "VALIDATING"),        # nothing to validate
    ("TRIAGE", "RESOLVED"),
    ("DUPLICATE", "INVESTIGATING"),  # the survivor carries the work
    ("RESOLVED", "OPEN"),
    ("VALIDATING", "CLOSED_NO_ACTION"),
])
def test_illegal_transitions_are_refused(current, target):
    with pytest.raises(ipol.IllegalIssueTransition):
        ipol.validate_issue_status_transition(current, target)


def test_a_self_transition_is_not_a_transition():
    with pytest.raises(ipol.IllegalIssueTransition, match="self-transition"):
        ipol.validate_issue_status_transition("OPEN", "OPEN")


def test_a_failed_validation_can_return_to_work():
    """The back edges exist so a FAILED validation is recordable. Without them
    the only way forward would be to misreport the outcome."""
    ipol.validate_issue_status_transition("VALIDATING", "ACTION_REQUIRED")
    ipol.validate_issue_status_transition("VALIDATING", "INVESTIGATING")
    ipol.validate_issue_status_transition("ACTION_REQUIRED", "INVESTIGATING")


def test_issue_statuses_never_collide_with_lifecycle_or_verdicts():
    """Sharing a word with a lifecycle state or a gate verdict is how the two get
    confused in a report that prints both."""
    from kalshi_bot.experiment_os.lifecycle import GateVerdict, LifecycleState

    statuses = {s.value for s in ipol.IssueStatus}
    assert not statuses & {s.value for s in LifecycleState}
    assert not statuses & {v.value for v in GateVerdict}


def test_the_service_refuses_an_illegal_move(xos_session, xos_platform):
    s = xos_session
    issue = _open(s)
    with pytest.raises(ipol.IllegalIssueTransition):
        ix.set_issue_status(s, issue, status="RESOLVED", actor="a",
                            actor_role="LIVE_OPS", reason="skipping ahead")


# ---------------------------------------------------------------------------
# History: append-only, and complete
# ---------------------------------------------------------------------------


def test_every_workflow_move_writes_an_event(xos_session, xos_platform):
    s = xos_session
    exp, ver, *_ = _experiment(s)
    issue = _open(s, experiment=exp, detector=ipol.DETECTOR_ZERO_EVIDENCE)
    ix.triage_issue(s, issue, actor="ops", actor_role="LIVE_OPS",
                    reason="runtime first", severity="MEDIUM")
    ix.set_issue_status(s, issue, status="INVESTIGATING", actor="ops",
                        actor_role="LIVE_OPS", reason="checking the worker")
    ix.add_issue_evidence(s, issue, evidence_type="OPS_RESULT",
                          summary="worker healthy; 412 candidates, 0 pass filter",
                          source_ref="ops/results/lo-9.txt", captured_by="ops")
    ix.transfer_issue(s, issue, to_owner_role="RESEARCH_LAB", actor="ops",
                      actor_role="LIVE_OPS", classification="STRATEGY",
                      reason="runtime verified healthy; the criteria are the question")
    s.commit()
    types = [e.event_type for e in ix.issue_events(s, issue)]
    assert types == ["CREATED", "TRIAGED", "STATUS_CHANGED", "EVIDENCE_ADDED",
                     "CLASSIFIED", "TRANSFERRED"]


def test_events_are_immutable(xos_session, xos_platform):
    s = xos_session
    issue = _open(s)
    event = ix.issue_events(s, issue)[0]
    event.reason = "a tidier story"
    with pytest.raises(ImmutableRecord, match="append-only"):
        s.flush()
    s.rollback()


def test_events_may_not_be_deleted(xos_session, xos_platform):
    s = xos_session
    issue = _open(s)
    s.delete(ix.issue_events(s, issue)[0])
    with pytest.raises(ImmutableRecord):
        s.flush()
    s.rollback()


def test_evidence_is_immutable(xos_session, xos_platform):
    s = xos_session
    issue = _open(s)
    row = ix.add_issue_evidence(s, issue, evidence_type="LOG",
                                summary="worker restarted at 14:02")
    s.commit()
    row.summary = "worker was fine actually"
    with pytest.raises(ImmutableRecord):
        s.flush()
    s.rollback()


def test_links_are_immutable(xos_session, xos_platform):
    s = xos_session
    issue = _open(s)
    row = ix.add_issue_link(s, issue, link_type="PR", reference="#251")
    s.commit()
    row.reference = "#999"
    with pytest.raises(ImmutableRecord):
        s.flush()
    s.rollback()


def test_a_classification_change_requires_a_reason(xos_session, xos_platform):
    s = xos_session
    issue = _open(s)
    with pytest.raises(ix.IssueError, match="reason is required"):
        ix.classify_issue(s, issue, classification="OPS", actor="a",
                          actor_role="LIVE_OPS", reason="  ")


def test_classification_history_survives_the_change(xos_session, xos_platform):
    s = xos_session
    issue = _open(s, classification="UNCLASSIFIED")
    ix.classify_issue(s, issue, classification="OPS", actor="a",
                      actor_role="LIVE_OPS", reason="worker crash-looping")
    ix.classify_issue(s, issue, classification="STRATEGY", actor="b",
                      actor_role="RESEARCH_LAB",
                      reason="the crash was downstream; the rule selects nothing")
    s.commit()
    changes = [(e.from_classification, e.to_classification, e.reason)
               for e in ix.issue_events(s, issue) if e.event_type == "CLASSIFIED"]
    assert changes == [
        ("UNCLASSIFIED", "OPS", "worker crash-looping"),
        ("OPS", "STRATEGY", "the crash was downstream; the rule selects nothing"),
    ]


def test_re_recording_the_same_classification_is_refused(xos_session, xos_platform):
    """History without a change is noise that makes the real changes harder to find."""
    s = xos_session
    issue = _open(s, classification="OPS")
    with pytest.raises(ix.IssueError, match="already classified"):
        ix.classify_issue(s, issue, classification="OPS", actor="a",
                          actor_role="LIVE_OPS", reason="restating")


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


def test_transfer_requires_a_reason_and_evidence(xos_session, xos_platform):
    """A transfer without evidence is just moving a problem nobody has looked at."""
    s = xos_session
    issue = _open(s, current_owner_role="LIVE_OPS")
    with pytest.raises(ix.IssueError, match="requires recorded evidence"):
        ix.transfer_issue(s, issue, to_owner_role="RESEARCH_LAB", actor="a",
                          actor_role="LIVE_OPS", reason="not mine")
    with pytest.raises(ix.IssueError, match="reason is required"):
        ix.transfer_issue(s, issue, to_owner_role="RESEARCH_LAB", actor="a",
                          actor_role="LIVE_OPS", reason="",
                          evidence_waiver_reason="mis-routed at creation")


def test_a_transfer_keeps_one_investigation_not_two(xos_session, xos_platform):
    """The detector, the original classification and the diagnosis that justified
    the handoff all stay on the SAME ticket — that evidence is exactly what the
    next owner needs, and a fresh ticket would throw it away."""
    s = xos_session
    issue = _open(s, detector=ipol.DETECTOR_ZERO_EVIDENCE,
                  opened_by_role="EXPERIMENT_CONTROL_TOWER")
    ix.add_issue_evidence(s, issue, evidence_type="OPS_RESULT",
                          summary="worker healthy, candidates produced",
                          source_ref="ops/results/lo-9.txt")
    ix.transfer_issue(s, issue, to_owner_role="RESEARCH_LAB", actor="ops",
                      actor_role="LIVE_OPS", classification="STRATEGY",
                      reason="runtime verified healthy")
    s.commit()

    assert len(ix.list_issues(s, open_only=True)) == 1
    assert issue.current_owner_role == "RESEARCH_LAB"
    # The detecting context and the original classification remain readable.
    assert issue.opened_by_role == "EXPERIMENT_CONTROL_TOWER"
    assert issue.detector == ipol.DETECTOR_ZERO_EVIDENCE
    transfer = next(e for e in ix.issue_events(s, issue)
                    if e.event_type == "TRANSFERRED")
    assert (transfer.from_owner_role, transfer.to_owner_role) == (
        "LIVE_OPS", "RESEARCH_LAB")
    classified = next(e for e in ix.issue_events(s, issue)
                      if e.event_type == "CLASSIFIED")
    assert classified.from_classification == "UNCLASSIFIED"


def test_a_split_creates_a_child_with_its_own_scope(xos_session, xos_platform):
    """Two independently actionable problems must not stay trapped in one
    permanently ambiguous ticket (§6.1)."""
    s = xos_session
    exp, *_ = _experiment(s)
    parent = _open(s, title="live canary underperforming", experiment=exp)
    child = ix.open_child_issue(
        s, parent, title="worker rejected entries due to stale config",
        opened_by_role="LIVE_OPS", opened_by_actor="ops",
        classification="OPS", current_owner_role="LIVE_OPS", experiment=exp,
    )
    s.commit()
    assert child.discovered_from_issue_id == parent.id
    assert child.issue_key != parent.issue_key
    assert any(link.reference == child.issue_key
               for link in ix.issue_links(s, parent))


# ---------------------------------------------------------------------------
# Disposition (§5) — recorded, never performed
# ---------------------------------------------------------------------------


def test_new_version_requires_the_matching_flag(xos_session, xos_platform):
    s = xos_session
    issue = _walk_to(s, _open(s), "ACTION_REQUIRED")
    with pytest.raises(ix.IssueError, match="unknown"):
        ix.record_disposition(s, issue, disposition="NEW_VERSION", actor="a",
                              actor_role="RESEARCH_LAB", reason="the contract is wrong")
    ix.record_disposition(s, issue, disposition="NEW_VERSION", actor="a",
                          actor_role="RESEARCH_LAB", requires_new_version=True,
                          reason="a frozen Version cannot be repaired in place")
    s.commit()
    assert issue.disposition == "NEW_VERSION"
    assert issue.requires_new_version is True


def test_the_requires_flags_start_unknown_not_false(xos_session, xos_platform):
    """"Nobody has looked" and "we determined it is not needed" are different
    findings, and only one of them is a conclusion."""
    s = xos_session
    issue = _open(s)
    assert issue.requires_new_version is None
    assert issue.requires_new_epoch is None
    assert issue.requires_platform_revision is None
    assert issue.requires_pause_or_stand_down is None


def test_version_and_epoch_together_need_an_explanation(xos_session, xos_platform):
    """A Version is a changed contract; an Epoch is the same contract under a
    changed world. Asserting both usually means they have been conflated."""
    s = xos_session
    issue = _walk_to(s, _open(s), "ACTION_REQUIRED")
    with pytest.raises(ix.IssueError, match="may not both be the single remedy"):
        ix.record_disposition(s, issue, disposition="NEW_VERSION", actor="a",
                              actor_role="RESEARCH_LAB", reason="both",
                              requires_new_version=True, requires_new_epoch=True)
    ix.record_disposition(
        s, issue, disposition="NEW_VERSION", actor="a", actor_role="RESEARCH_LAB",
        reason="both", requires_new_version=True, requires_new_epoch=True,
        version_and_epoch_rationale=(
            "the fee model changed on the same day the contract was corrected"
        ),
    )
    s.commit()
    assert issue.disposition == "NEW_VERSION"


def test_a_platform_revision_disposition_must_route_to_platform_change_review(
        xos_session, xos_platform):
    s = xos_session
    issue = _walk_to(s, _open(s, current_owner_role="LIVE_OPS"), "ACTION_REQUIRED")
    with pytest.raises(ix.IssueError, match="PLATFORM_CHANGE_REVIEW"):
        ix.record_disposition(s, issue, disposition="PLATFORM_REVISION", actor="a",
                              actor_role="LIVE_OPS", reason="fill model changed",
                              requires_platform_revision=True)


def test_recording_a_disposition_performs_nothing(xos_session, xos_platform):
    """The load-bearing separation: `NEW_VERSION` schedules and authorizes
    nothing. If it did, opening a ticket would need the same guards as the
    transition itself and nobody would open one."""
    s = xos_session
    exp, ver, epoch, _dep, gate = _experiment(s)
    before = (
        exp.state,
        len(read.versions_for(s, exp)),
        len(read.epochs_for(s, ver)),
        len(read.gate_results_for(s, gate)),
        gate.spec_hash,
    )
    issue = _walk_to(s, _open(s, experiment=exp, version=ver), "ACTION_REQUIRED")
    ix.record_disposition(s, issue, disposition="NEW_VERSION", actor="a",
                          actor_role="RESEARCH_LAB", requires_new_version=True,
                          reason="corrected successor Version required")
    s.commit()
    s.refresh(exp)
    assert before == (
        exp.state,
        len(read.versions_for(s, exp)),
        len(read.epochs_for(s, ver)),
        len(read.gate_results_for(s, gate)),
        gate.spec_hash,
    )


def test_no_issue_operation_touches_lifecycle_or_gates(xos_session, xos_platform):
    """The whole workflow, end to end, against a fully registered experiment."""
    s = xos_session
    exp, ver, epoch, dep, gate = _experiment(s)
    snapshot = (exp.state, exp.paused_from_state, gate.spec_hash,
                gate.evidence_started_at, epoch.ended_at, dep.ended_at,
                ver.frozen_at)
    transitions_before = len(read.transitions_for(s, exp))

    issue = _open(s, experiment=exp, gate=gate)
    ix.triage_issue(s, issue, actor="a", actor_role="LIVE_OPS", reason="look")
    ix.set_issue_status(s, issue, status="INVESTIGATING", actor="a",
                        actor_role="LIVE_OPS", reason="working")
    ix.add_issue_evidence(s, issue, evidence_type="LOG", summary="stack trace")
    ix.propose_issue_fix(s, issue, proposed_fix="restart the collector",
                         actor="a", actor_role="LIVE_OPS")
    ix.record_disposition(s, issue, disposition="OPS_REPAIR", actor="a",
                          actor_role="LIVE_OPS", reason="config typo")
    ix.record_validation_plan(s, issue, validation_plan="collector fresh for 6h",
                              actor="a", actor_role="LIVE_OPS")
    ix.record_validation_result(s, issue, passed=True, summary="fresh for 8h",
                                actor="a", actor_role="LIVE_OPS")
    ix.resolve_issue(s, issue, resolution_summary="config corrected and deployed",
                     actor="a", actor_role="LIVE_OPS")
    ix.reopen_issue(s, issue, reason="recurred", actor="a", actor_role="LIVE_OPS")
    s.commit()
    s.refresh(exp)

    assert snapshot == (exp.state, exp.paused_from_state, gate.spec_hash,
                        gate.evidence_started_at, epoch.ended_at, dep.ended_at,
                        ver.frozen_at)
    assert len(read.transitions_for(s, exp)) == transitions_before
    assert read.gate_results_for(s, gate) == []


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_resolution_requires_a_summary(xos_session, xos_platform):
    s = xos_session
    issue = _walk_to(s, _open(s), "VALIDATING")
    ix.record_validation_result(s, issue, passed=True, summary="ok", actor="a",
                                actor_role="LIVE_OPS")
    with pytest.raises(ix.IssueError, match="resolution summary is required"):
        ix.resolve_issue(s, issue, resolution_summary="   ", actor="a",
                         actor_role="LIVE_OPS")


def test_resolution_requires_validation_or_an_explicit_waiver(xos_session,
                                                              xos_platform):
    s = xos_session
    issue = _walk_to(s, _open(s), "VALIDATING")
    with pytest.raises(ix.IssueError, match="no PASSED validation"):
        ix.resolve_issue(s, issue, resolution_summary="fixed it", actor="a",
                         actor_role="LIVE_OPS")
    ix.resolve_issue(s, issue, resolution_summary="fixed it", actor="a",
                     actor_role="LIVE_OPS",
                     validation_waiver_reason="the market no longer lists; "
                                              "nothing can be measured")
    s.commit()
    assert issue.status == "RESOLVED"
    resolved = next(e for e in ix.issue_events(s, issue)
                    if e.event_type == "RESOLVED")
    assert "no longer lists" in resolved.payload_json["validation_waiver_reason"]


def test_a_failed_validation_does_not_satisfy_resolution(xos_session, xos_platform):
    s = xos_session
    issue = _walk_to(s, _open(s), "VALIDATING")
    ix.record_validation_result(s, issue, passed=False,
                                summary="still stale after the restart",
                                actor="a", actor_role="LIVE_OPS")
    with pytest.raises(ix.IssueError, match="no PASSED validation"):
        ix.resolve_issue(s, issue, resolution_summary="fixed", actor="a",
                         actor_role="LIVE_OPS")
    # ...and the failure is durable, not swallowed.
    assert any(
        e.event_type == "VALIDATION_RECORDED" and e.payload_json["passed"] is False
        for e in ix.issue_events(s, issue)
    )


def test_close_no_action_is_not_a_resolution(xos_session, xos_platform):
    """"We looked and there is nothing to do" and "we fixed it" are different
    findings; collapsing them makes the second untrustworthy."""
    s = xos_session
    issue = _walk_to(s, _open(s), "INVESTIGATING")
    ix.close_no_action(s, issue, reason="the market delisted; nothing to fix",
                       actor="a", actor_role="LIVE_OPS")
    s.commit()
    assert issue.status == "CLOSED_NO_ACTION"
    assert issue.resolved_at is None
    assert issue.disposition == "NO_ACTION"


# ---------------------------------------------------------------------------
# Duplicates (§2.6)
# ---------------------------------------------------------------------------


def test_duplicate_requires_a_target(xos_session, xos_platform):
    s = xos_session
    issue = _open(s)
    with pytest.raises(ix.IssueError, match="no issue"):
        ix.mark_duplicate(s, issue, duplicate_of="XOS-999999", actor="a",
                          actor_role="LIVE_OPS", reason="same thing")


def test_an_issue_cannot_duplicate_itself(xos_session, xos_platform):
    s = xos_session
    issue = _open(s)
    with pytest.raises(ix.IssueError, match="duplicate of itself"):
        ix.mark_duplicate(s, issue, duplicate_of=issue, actor="a",
                          actor_role="LIVE_OPS", reason="loop")


def test_duplicate_cycles_are_refused(xos_session, xos_platform):
    """A→B while B→A leaves no ticket carrying the work."""
    s = xos_session
    a, b, c = _open(s), _open(s), _open(s)
    ix.mark_duplicate(s, b, duplicate_of=a, actor="x", actor_role="LIVE_OPS",
                      reason="same")
    ix.mark_duplicate(s, c, duplicate_of=b, actor="x", actor_role="LIVE_OPS",
                      reason="same")
    s.commit()
    with pytest.raises(ix.IssueError, match="cycle"):
        ix.mark_duplicate(s, a, duplicate_of=c, actor="x", actor_role="LIVE_OPS",
                          reason="closing the loop")


def test_fuzzy_similarity_suggests_and_never_merges(xos_session, xos_platform):
    """Auto-merging on text similarity would silently destroy the investigation
    history of whichever ticket lost."""
    s = xos_session
    a = _open(s, title="collector weather_forecasts is stale again")
    b = _open(s, title="collector weather_forecasts stale overnight")
    s.commit()
    suggestions = ix.suggest_duplicates(s, b)
    assert [x["issue_key"] for x in suggestions] == [a.issue_key]
    assert all(x["confirmation_required"] for x in suggestions)
    # Suggesting changed nothing.
    s.refresh(a)
    s.refresh(b)
    assert (a.status, b.status) == ("OPEN", "OPEN")
    assert b.duplicate_of_issue_id is None


# ---------------------------------------------------------------------------
# Reopen and recurrence
# ---------------------------------------------------------------------------


def test_reopen_preserves_the_prior_resolution(xos_session, xos_platform):
    s = xos_session
    issue = _walk_to(s, _open(s), "VALIDATING")
    ix.record_validation_result(s, issue, passed=True, summary="clean for 8h",
                                actor="a", actor_role="LIVE_OPS")
    ix.resolve_issue(s, issue, resolution_summary="restarted the collector",
                     actor="a", actor_role="LIVE_OPS")
    s.commit()
    resolved_at = issue.resolved_at
    assert resolved_at is not None

    ix.reopen_issue(s, issue, reason="stale again three days later", actor="a",
                    actor_role="LIVE_OPS")
    s.commit()
    assert issue.status == "INVESTIGATING"
    assert issue.resolved_at is None, "the cached timestamp clears"

    reopened = next(e for e in ix.issue_events(s, issue)
                    if e.event_type == "REOPENED")
    assert reopened.payload_json["prior_resolution_summary"] == (
        "restarted the collector")
    assert reopened.payload_json["prior_resolved_at"] is not None
    # The original RESOLVED event is still there — nothing was rewritten.
    assert any(e.event_type == "RESOLVED" for e in ix.issue_events(s, issue))


def test_occurrence_count_moves_only_on_an_explicit_write(xos_session, xos_platform):
    s = xos_session
    issue = _open(s, detector=ipol.DETECTOR_COLLECTOR_HEALTH)
    assert issue.occurrence_count == 1
    # Reading, listing and summarising must not advance it.
    ix.list_issues(s, open_only=True)
    read.issue_summary(s, issue)
    read.issue_detail(s, issue)
    assert issue.occurrence_count == 1

    ix.record_recurrence(s, issue, actor="ops", actor_role="LIVE_OPS",
                         note="stale again")
    s.commit()
    assert issue.occurrence_count == 2
    assert issue.last_observed_at is not None


# ---------------------------------------------------------------------------
# Fingerprints (§8)
# ---------------------------------------------------------------------------


def test_a_fingerprint_is_deterministic_and_scope_only():
    a = ipol.issue_fingerprint(detector="gate.blocked", experiment_id=7,
                               version_id=3, gate_id=11,
                               anomaly_kind="BLOCKED_DATA")
    b = ipol.issue_fingerprint(anomaly_kind="BLOCKED_DATA", gate_id=11,
                               version_id=3, experiment_id=7,
                               detector="gate.blocked")
    assert a == b, "argument order must not change the hash"
    assert a != ipol.issue_fingerprint(detector="gate.blocked", experiment_id=8,
                                       version_id=3, gate_id=11,
                                       anomaly_kind="BLOCKED_DATA")


def test_a_fingerprint_cannot_absorb_a_volatile_value():
    """Keyword-only and closed over a fixed field set: current P&L or an age in
    minutes would rehash every run and defeat recurrence detection entirely."""
    with pytest.raises(TypeError):
        ipol.issue_fingerprint(detector="gate.blocked", pnl_cents=-3.2)


def test_the_fingerprint_recipe_matches_the_canonical_hash():
    """One hashing recipe for the whole Experiment OS, pinned rather than
    coincidental."""
    payload = {
        "detector": "gate.blocked", "experiment_id": 7, "version_id": None,
        "epoch_id": None, "deployment_id": None, "gate_id": None,
        "anomaly_kind": None,
    }
    assert ipol.issue_fingerprint(detector="gate.blocked", experiment_id=7) == (
        svc.canonical_hash(payload)
    )
    assert set(payload) == set(ipol.FINGERPRINT_FIELDS)


def test_an_open_issue_covers_its_fingerprint_and_a_resolved_one_does_not(
        xos_session, xos_platform):
    """"We fixed that once" is not evidence that it is fixed now."""
    s = xos_session
    fp = ipol.issue_fingerprint(detector=ipol.DETECTOR_COLLECTOR_HEALTH,
                                anomaly_kind="weather_forecasts:STALE")
    issue = _open(s, detector=ipol.DETECTOR_COLLECTOR_HEALTH,
                  detector_fingerprint=fp)
    assert ix.find_open_issue_by_fingerprint(s, fp) is issue

    _walk_to(s, issue, "VALIDATING")
    ix.record_validation_result(s, issue, passed=True, summary="fresh", actor="a",
                                actor_role="LIVE_OPS")
    ix.resolve_issue(s, issue, resolution_summary="restarted", actor="a",
                     actor_role="LIVE_OPS")
    s.commit()
    assert ix.find_open_issue_by_fingerprint(s, fp) is None
    # ...but the historical investigation is still queryable.
    assert ix.get_issue(s, issue.issue_key) is not None


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def test_listings_are_worst_first(xos_session, xos_platform):
    s = xos_session
    _open(s, title="low", severity="LOW", priority="P3")
    _open(s, title="critical", severity="CRITICAL", priority="P0")
    _open(s, title="medium", severity="MEDIUM", priority="P2")
    s.commit()
    assert [i.title for i in ix.list_issues(s, open_only=True)] == [
        "critical", "medium", "low"]


def test_a_mistyped_filter_is_a_refusal_not_an_empty_result(xos_session,
                                                            xos_platform):
    """An empty result reads as "nothing is wrong", which is the worst possible
    answer to a typo."""
    s = xos_session
    _open(s)
    with pytest.raises(ipol.IssuePolicyError, match="status"):
        ix.list_issues(s, status="INVESTIGATNG")


def test_open_only_excludes_the_closed_statuses(xos_session, xos_platform):
    s = xos_session
    a = _open(s, title="still open")
    b = _walk_to(s, _open(s, title="closed"), "INVESTIGATING")
    ix.close_no_action(s, b, reason="not a defect", actor="x",
                       actor_role="LIVE_OPS")
    s.commit()
    assert [i.issue_key for i in ix.list_issues(s, open_only=True)] == [a.issue_key]
    assert len(ix.list_issues(s)) == 2


# ---------------------------------------------------------------------------
# Structural guarantees
# ---------------------------------------------------------------------------


def test_the_issue_service_imports_no_mutating_experiment_helper():
    """Behavioural tests prove no CURRENT method moves an experiment. This proves
    none CAN: the module never binds a helper that transitions, promotes, arms,
    freezes, opens an epoch or records a gate result."""
    import pathlib

    source = pathlib.Path(ix.__file__).read_text()
    forbidden = (
        "transition_experiment", "arm_live_canary", "record_gate_result",
        "register_gate", "open_epoch", "create_experiment", "freeze_version",
        "register_deployment", "activate_platform_revision", "propose_impact",
    )
    for name in forbidden:
        assert name not in source, f"issues.py must not reference {name}"
    # The one thing it does take from the service is the flush guard + base error.
    assert "installs the append-only flush guard" in source


def test_the_ops_channel_exposes_only_issue_reads():
    """The ops channel is read-only against Postgres by design and the worker is
    the only writer. Exposing a write here would break both at once."""
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    import ops_runner

    assert set(ops_runner.XOS_ISSUE_READS) == {
        "issue-list", "issue-show", "issue-candidates"}
    for argv in ops_runner.XOS_ISSUE_READS.values():
        assert argv[0] == "issue"
        assert argv[1] in {"list", "show", "candidates"}


def test_every_issue_write_command_refuses_the_read_only_connection(monkeypatch):
    """Same rule `evaluate-gates` already follows: fail loudly at the boundary
    rather than deep inside a Postgres permission error."""
    from kalshi_bot.experiment_os import cli

    monkeypatch.setenv("DATABASE_URL_RO", "postgresql://ro@example/db")
    parser = cli.build_parser()
    issue_sub = next(
        a for a in parser._actions
        if getattr(a, "choices", None) and "issue" in a.choices
    ).choices["issue"]
    actions = set(
        next(a for a in issue_sub._actions if getattr(a, "choices", None)).choices
    )
    writes = actions - cli._ISSUE_READ_ACTIONS - {"import-findings"}
    assert writes, "there are write subcommands to guard"
    for action in sorted(writes):
        args = type("A", (), {"issue_action": action})()
        assert cli.cmd_issue(None, args) == 2, f"{action} must refuse RO"
    assert cli._issue_write_guard() == 2


def test_every_issue_subcommand_is_dispatchable():
    """The nested version of the CLI's existing wiring check: a subcommand that
    parses and then dies on AttributeError at dispatch is invisible to a test
    that only calls the command function directly."""
    from kalshi_bot.experiment_os import cli

    def walk(parser, path=()):
        groups = [a for a in parser._actions if getattr(a, "choices", None)
                  and hasattr(a, "_name_parser_map")]
        if groups:
            for group in groups:
                for name, sub in group.choices.items():
                    walk(sub, (*path, name))
            return
        assert callable(parser.get_default("fn")), f"{' '.join(path)} has no fn"

    walk(cli.build_parser())


def test_every_enum_value_fits_the_column_that_stores_it():
    """SQLite ignores VARCHAR limits; Postgres enforces them.

    So a value one character too long for its column passes the whole test suite
    and then fails in production — the single worst place to discover it. This
    walks the real ORM columns and asserts every enum value fits with room to
    spare, so adding a longer role or event type fails here instead.

    `EXPERIMENT_CONTROL_TOWER` (24) is the longest name in the system and sat at
    exactly the original column width; the headroom below is deliberate."""
    from sqlalchemy import String

    from kalshi_bot.experiment_os.models import (
        ExperimentIssue,
        ExperimentIssueEvent,
        ExperimentIssueEvidence,
        ExperimentIssueLink,
    )

    role_values = sorted(ipol.OPENING_ROLES)
    checks = [
        (ExperimentIssue, "classification", [c.value for c in ipol.IssueClassification]),
        (ExperimentIssue, "status", [s.value for s in ipol.IssueStatus]),
        (ExperimentIssue, "severity", [s.value for s in ipol.IssueSeverity]),
        (ExperimentIssue, "priority", [p.value for p in ipol.IssuePriority]),
        (ExperimentIssue, "current_owner_role", role_values),
        (ExperimentIssue, "opened_by_role", role_values),
        (ExperimentIssue, "disposition", [d.value for d in ipol.IssueDisposition]),
        (ExperimentIssue, "detector", _ALL_DETECTORS),
        (ExperimentIssueEvent, "event_type", [e.value for e in ipol.IssueEventType]),
        (ExperimentIssueEvent, "actor_role", role_values),
        (ExperimentIssueEvent, "from_owner_role", role_values),
        (ExperimentIssueEvent, "to_owner_role", role_values),
        (ExperimentIssueEvent, "from_status", [s.value for s in ipol.IssueStatus]),
        (ExperimentIssueEvent, "to_status", [s.value for s in ipol.IssueStatus]),
        (ExperimentIssueEvent, "from_classification",
         [c.value for c in ipol.IssueClassification]),
        (ExperimentIssueEvent, "to_classification",
         [c.value for c in ipol.IssueClassification]),
        (ExperimentIssueEvidence, "evidence_type",
         [e.value for e in ipol.IssueEvidenceType]),
        (ExperimentIssueLink, "link_type", [link.value for link in ipol.IssueLinkType]),
    ]
    for model, column, values in checks:
        col = model.__table__.c[column]
        assert isinstance(col.type, String), f"{column} is not a String column"
        width = col.type.length
        longest = max(values, key=len)
        assert len(longest) <= width, (
            f"{model.__tablename__}.{column} is VARCHAR({width}) but must hold "
            f"{longest!r} ({len(longest)} chars)"
        )


def test_the_issue_key_column_holds_far_more_keys_than_we_will_ever_open():
    """`XOS-` plus six digits is 10 characters in a VARCHAR(24); the generator
    widens past six digits rather than wrapping, so the column has room for
    ~10^19 issues before the format itself is the limit."""
    from kalshi_bot.experiment_os.models import ExperimentIssue

    width = ExperimentIssue.__table__.c["issue_key"].type.length
    assert len(ipol.format_issue_key(10**6 - 1)) == 10
    assert len(ipol.format_issue_key(10**18)) <= width
