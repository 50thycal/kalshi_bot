"""Migrating the interim contract-findings registry into durable issues.

The registry was display-only and stateless; the issues that replace it carry
workflow state and history. The migration must therefore be careful about the
things a stateful record can get wrong and a stateless one could not:

  * running twice must not produce two tickets for one defect;
  * a defect must bind to the exact Experiment AND Version it was proven
    against, so a corrected successor Version drops it on its own;
  * nothing may be invented for a finding the registry did not record;
  * nothing about the experiment, its gates or its verdicts may move.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kalshi_bot.experiment_os import control_tower as ct
from kalshi_bot.experiment_os import findings as fx
from kalshi_bot.experiment_os import issue_import, read
from kalshi_bot.experiment_os import issue_policy as ipol
from kalshi_bot.experiment_os import issues as ix
from kalshi_bot.experiment_os import service as svc

UTC = timezone.utc
T0 = datetime(2026, 8, 1, tzinfo=UTC)

#: The two experiments the registry named. Both must migrate when present.
FINDING_KEYS = ("mmsell-scheduled-settle-live", "theta4-fat-tail")


def _live_canary_book(s, key):
    """The real shape the findings describe: an epoch holding live + paper_twin
    and no paper deployment, with a gate whose clauses default to paper."""
    exp = svc.create_experiment(s, key=key, origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="lever", now=T0,
        control_exemption_reason="imported live canary",
    )
    svc.add_arm(s, ver, arm_key="treatment", role="treatment", strategy_tag=f"{key}-l")
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    live = svc.register_deployment(
        s, epoch, deployment_key=f"{key}-live", stage="LIVE_CANARY", kind="live",
        arms={"treatment": f"{key}-l"}, started_at=T0, _sanctioned_canary=True,
    )
    svc.register_deployment(
        s, epoch, deployment_key=f"{key}-twin", stage="LIVE_CANARY",
        kind="paper_twin", arms={"treatment": f"{key}-pt"}, started_at=T0,
        twin_of=live,
    )
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    gate = svc.register_gate(
        s, ver, gate_key="paper_to_live_canary", kind="promotion",
        spec={"pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                            "op": ">", "value": 0}]},
        from_state="PAPER", to_state="LIVE_CANARY", registered_at=T0,
    )
    svc.mark_gate_evidence_started(s, gate, at=T0)
    s.commit()
    return exp, ver, epoch, gate


def _both_books(s):
    return {key: _live_canary_book(s, key) for key in FINDING_KEYS}


# ---------------------------------------------------------------------------
# The payload itself
# ---------------------------------------------------------------------------


def test_the_payload_carries_exactly_the_two_registered_findings():
    keys = {f["experiment_key"] for f in issue_import.LEGACY_CONTRACT_FINDINGS}
    assert keys == set(FINDING_KEYS)
    assert all(f["version"] == 1 for f in issue_import.LEGACY_CONTRACT_FINDINGS)


def test_every_migrated_finding_cites_a_document_that_exists():
    """A finding's authority is the research that proved it; a citation that does
    not resolve is an assertion."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    for f in issue_import.LEGACY_CONTRACT_FINDINGS:
        assert (root / f["evidence_doc"]).is_file(), f["evidence_doc"]


def test_the_theta_finding_keeps_its_second_independent_defect():
    """The theta gate is defective twice over — wrong addressing AND the wrong
    evidence basis for a LIVE_CANARY→PRODUCTION decision. Losing the second in
    migration would make the remedy look like a smaller change than it is."""
    theta = next(f for f in issue_import.LEGACY_CONTRACT_FINDINGS
                 if f["experiment_key"] == "theta4-fat-tail")
    detail = " ".join(theta["detail"])
    assert "wrong question" in detail
    assert "LIVE_CANARY -> PRODUCTION" in detail
    assert "contemporaneous" in detail


# ---------------------------------------------------------------------------
# Import behaviour
# ---------------------------------------------------------------------------


def test_both_findings_migrate_exactly_once(xos_session, xos_platform):
    s = xos_session
    _both_books(s)
    report = issue_import.import_contract_findings(s)
    s.commit()

    assert {c["experiment"] for c in report["created"]} == set(FINDING_KEYS)
    assert report["skipped"] == []
    issues = ix.list_issues(s, detector=ipol.DETECTOR_CONTRACT_DEFECT)
    assert len(issues) == 2
    assert len({i.issue_key for i in issues}) == 2


def test_rerunning_the_import_is_idempotent(xos_session, xos_platform):
    s = xos_session
    _both_books(s)
    first = issue_import.import_contract_findings(s)
    s.commit()
    keys = {c["issue_key"] for c in first["created"]}

    for _ in range(3):
        again = issue_import.import_contract_findings(s)
        s.commit()
        assert again["created"] == []
        assert {a["issue_key"] for a in again["already_present"]} == keys

    assert len(ix.list_issues(s, detector=ipol.DETECTOR_CONTRACT_DEFECT)) == 2


def test_an_absent_experiment_is_skipped_not_fabricated(xos_session, xos_platform):
    """Inventing an experiment to hang a finding on would be the opposite of the
    point — and would create lineage the rest of the system would then trust."""
    s = xos_session
    _live_canary_book(s, "mmsell-scheduled-settle-live")
    report = issue_import.import_contract_findings(s)
    s.commit()

    assert [c["experiment"] for c in report["created"]] == [
        "mmsell-scheduled-settle-live"]
    assert [x["experiment"] for x in report["skipped"]] == ["theta4-fat-tail"]
    assert "not registered" in report["skipped"][0]["reason"]
    assert read.get_experiment(s, "theta4-fat-tail") is None


def test_an_absent_version_is_skipped(xos_session, xos_platform):
    s = xos_session
    svc.create_experiment(s, key="theta4-fat-tail", origin="operator")
    s.commit()
    report = issue_import.import_contract_findings(s)
    s.commit()
    skipped = {x["experiment"]: x["reason"] for x in report["skipped"]}
    assert "version 1 does not exist" in skipped["theta4-fat-tail"]


def test_the_citation_and_provenance_are_preserved_verbatim(xos_session,
                                                            xos_platform):
    s = xos_session
    _both_books(s)
    issue_import.import_contract_findings(s)
    s.commit()

    for finding in issue_import.LEGACY_CONTRACT_FINDINGS:
        exp = read.get_experiment(s, finding["experiment_key"])
        issue = ix.list_issues(s, experiment=exp,
                               detector=ipol.DETECTOR_CONTRACT_DEFECT)[0]
        details = issue.details_json
        assert details["evidence_doc"] == finding["evidence_doc"]
        assert details["proven_at"] == finding["proven_at"]
        assert details["proven_by"] == finding["proven_by"]
        assert details["detail"] == list(finding["detail"])
        assert details["independent_of_evaluator"] is True
        assert details["migrated_from"].endswith("findings.py")
        # ...and the document is a first-class evidence row, not just a string.
        docs = [e for e in ix.issue_evidence(s, issue)
                if e.evidence_type == "RESEARCH_DOCUMENT"]
        assert [d.source_ref for d in docs] == [finding["evidence_doc"]]


def test_each_issue_binds_to_the_exact_experiment_and_version(xos_session,
                                                              xos_platform):
    s = xos_session
    books = _both_books(s)
    issue_import.import_contract_findings(s)
    s.commit()

    for key, (exp, ver, _epoch, _gate) in books.items():
        issue = ix.list_issues(s, experiment=exp,
                               detector=ipol.DETECTOR_CONTRACT_DEFECT)[0]
        assert issue.experiment_id == exp.id
        assert issue.version_id == ver.id, f"{key} must bind to the proven version"


def test_the_migrated_issue_lands_in_the_state_the_work_is_actually_in(
        xos_session, xos_platform):
    """Not OPEN (nobody has looked) and not RESOLVED (nothing is fixed): the
    defect is proven and the remedy is known, so it is ACTION_REQUIRED."""
    s = xos_session
    _both_books(s)
    issue_import.import_contract_findings(s)
    s.commit()

    for issue in ix.list_issues(s, detector=ipol.DETECTOR_CONTRACT_DEFECT):
        assert issue.status == "ACTION_REQUIRED"
        assert issue.classification == "STRATEGY"
        assert issue.current_owner_role == "RESEARCH_LAB"
        assert issue.disposition == "NEW_VERSION"
        assert issue.requires_new_version is True
        assert issue.severity == "HIGH"
        types = [e.event_type for e in ix.issue_events(s, issue)]
        assert types[0] == "CREATED"
        assert "DISPOSITION_DECIDED" in types


def test_the_defect_is_strategy_not_data(xos_session, xos_platform):
    """The evaluator says BLOCKED_DATA, which is true and incomplete: the
    ADDRESSING is what is wrong, so implementing every named provider would
    leave the Version exactly as unevaluable. Classifying it DATA would send it
    to be fixed the way the evaluator's own verdict suggests, which does not work."""
    s = xos_session
    _both_books(s)
    issue_import.import_contract_findings(s)
    s.commit()
    for issue in ix.list_issues(s, detector=ipol.DETECTOR_CONTRACT_DEFECT):
        assert issue.classification == "STRATEGY"
        assert issue.details_json["independent_of_evaluator"] is True


def test_importing_changes_no_experiment_state_or_gate(xos_session, xos_platform):
    s = xos_session
    books = _both_books(s)
    before = {
        key: (exp.state, ver.frozen_at, gate.spec_hash, gate.evidence_started_at,
              len(read.transitions_for(s, exp)),
              len(read.gate_results_for(s, gate)))
        for key, (exp, ver, _epoch, gate) in books.items()
    }
    issue_import.import_contract_findings(s)
    s.commit()
    for key, (exp, ver, _epoch, gate) in books.items():
        s.refresh(exp)
        assert before[key] == (
            exp.state, ver.frozen_at, gate.spec_hash, gate.evidence_started_at,
            len(read.transitions_for(s, exp)),
            len(read.gate_results_for(s, gate)),
        )


# ---------------------------------------------------------------------------
# Display: exactly once, bound to the operating version
# ---------------------------------------------------------------------------


def test_the_control_tower_shows_each_migrated_defect_exactly_once(xos_session,
                                                                   xos_platform):
    """The retired registry is empty, so nothing can render the same defect from
    two sources that could disagree."""
    s = xos_session
    _both_books(s)
    issue_import.import_contract_findings(s)
    s.commit()

    rep = ct.build_report(s, evaluate=True)
    keys = [f["experiment"] for f in rep.contract_findings]
    assert sorted(keys) == sorted(FINDING_KEYS)
    assert len(keys) == len(set(keys))

    out = ct.render(rep, session=s)
    assert out.count("imported gate addresses deployment_kind=paper; epoch has "
                     "no paper deployment") >= 1
    for key in FINDING_KEYS:
        assert out.count(f"    {key} · v1") == 1


def test_a_corrected_successor_version_does_not_inherit_the_blocker(xos_session,
                                                                    xos_platform):
    """Bound to the version it was proven against — so it expires on its own
    instead of lingering as a permanent scare line over a fixed book."""
    s = xos_session
    exp, ver, _epoch, _gate = _live_canary_book(s, "mmsell-scheduled-settle-live")
    issue_import.import_contract_findings(s)
    s.commit()
    assert read.contract_defect_findings(s, exp, 1), "it applies to v1"

    successor = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="lever", now=T0,
        control_exemption_reason="corrected",
        change_reason="gate re-addressed to the kinds the epoch actually holds",
    )
    s.commit()
    assert read.contract_defect_findings(s, exp, successor.version) == [], (
        "the corrected successor Version must not inherit the old blocker"
    )


def test_the_historical_issue_remains_queryable_after_it_stops_applying(
        xos_session, xos_platform):
    s = xos_session
    exp, _ver, _epoch, _gate = _live_canary_book(s, "mmsell-scheduled-settle-live")
    report = issue_import.import_contract_findings(s)
    s.commit()
    key = report["created"][0]["issue_key"]

    svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="lever", now=T0,
        control_exemption_reason="corrected", change_reason="re-addressed",
    )
    s.commit()

    issue = ix.get_issue(s, key)
    assert issue is not None
    assert issue.version_id is not None
    assert issue.details_json["evidence_doc"]
    assert ix.issue_events(s, issue), "its history survives too"


def test_a_defect_bound_to_another_experiment_never_leaks(xos_session,
                                                          xos_platform):
    s = xos_session
    books = _both_books(s)
    issue_import.import_contract_findings(s)
    s.commit()
    mm_exp = books["mmsell-scheduled-settle-live"][0]
    found = read.contract_defect_findings(s, mm_exp, 1)
    assert [f["experiment"] for f in found] == ["mmsell-scheduled-settle-live"]


# ---------------------------------------------------------------------------
# The retired registry
# ---------------------------------------------------------------------------


def test_the_registry_is_a_deprecation_shim(xos_session, xos_platform):
    assert fx.CONTRACT_FINDINGS == ()
    with pytest.warns(DeprecationWarning, match="durable issues"):
        assert fx.findings_for("mmsell-scheduled-settle-live", 1) == []


def test_the_control_tower_no_longer_reads_the_registry():
    """Structural, not behavioural: if it still imported the module, an entry
    re-added there would silently render beside the durable issue."""
    import pathlib

    source = pathlib.Path(ct.__file__).read_text()
    assert "findings as findings_mod" not in source
    assert "findings_mod" not in source


# ---------------------------------------------------------------------------
# Reconciliation with the operator's later decision
# ---------------------------------------------------------------------------
#
# The remedy recorded at migration time — "a corrected native successor Version"
# — is STALE. Both live canaries were stood down and both proposed v2 contracts
# were withdrawn, so importing the findings unreconciled would put two tickets in
# front of the next reader asking for work that has already been declined.
#
# The defect itself is NOT withdrawn: it remains true of the historical Version,
# keeps its original evidence and Version binding, and stays queryable. What
# changes is only what follows from it, which is nothing.


def _import_and_reconcile(s):
    report = issue_import.import_and_reconcile(s)
    s.commit()
    return report


def test_the_withdrawal_artifacts_all_exist():
    """The closure's authority is the merged decision. A citation that does not
    resolve is an assertion — the same rule that governs the defect itself."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    docs = [
        issue_import.WITHDRAWAL_EVIDENCE_DOC,
        issue_import.STAND_DOWN_EVIDENCE_DOC,
        *issue_import.WITHDRAWAL_CAUSE_DOC.values(),
    ]
    for doc in docs:
        assert (root / doc).is_file(), doc
    # ...and the withdrawal document actually says it was withdrawn.
    banner = (root / issue_import.WITHDRAWAL_EVIDENCE_DOC).read_text()
    assert "WITHDRAWN" in banner
    assert "not to be frozen" in banner or "not to be created" in banner
    assert set(issue_import.WITHDRAWAL_CAUSE_DOC) == set(FINDING_KEYS)


def test_both_findings_close_no_action_with_the_stand_down_recorded(xos_session,
                                                                    xos_platform):
    s = xos_session
    _both_books(s)
    report = _import_and_reconcile(s)

    assert {r["experiment"] for r in report["reconcile"]["reconciled"]} == set(
        FINDING_KEYS)
    issues = ix.list_issues(s, detector=ipol.DETECTOR_CONTRACT_DEFECT)
    assert len(issues) == 2
    for issue in issues:
        assert issue.status == "CLOSED_NO_ACTION"
        assert issue.disposition == "NO_ACTION"
        assert issue.requires_new_version is False
        assert issue.requires_new_epoch is False
        assert issue.requires_platform_revision is False
        # TRUE and staying true: the contract is stood down, and that is the
        # standing operational state this issue is closed against.
        assert issue.requires_pause_or_stand_down is True
        # Not RESOLVED: nothing was fixed.
        assert issue.resolved_at is None


def test_the_closure_explains_itself_without_erasing_the_defect(xos_session,
                                                                xos_platform):
    s = xos_session
    _both_books(s)
    _import_and_reconcile(s)

    for issue in ix.list_issues(s, detector=ipol.DETECTOR_CONTRACT_DEFECT):
        closing = [e for e in ix.issue_events(s, issue)
                   if e.event_type == "CLOSED_NO_ACTION"]
        assert len(closing) == 1
        reason = closing[0].reason
        assert "remains true of historical Version 1" in reason
        assert "stood down" in reason
        assert "withdrawn" in reason
        assert "paper research" in reason
        # The original defect detail is still on the issue, untouched.
        assert issue.details_json["detail"], "the proven defect must survive"
        assert issue.details_json["evidence_doc"] == (
            "docs/RESEARCH_LIVE_CANARY_CONTRACT_DEFECT.md")


def test_the_history_shows_the_remedy_being_withdrawn(xos_session, xos_platform):
    """The point of an append-only history: a reader can see that a successor
    Version WAS the plan and that the operator withdrew it, not just the tidy
    end state."""
    s = xos_session
    _both_books(s)
    _import_and_reconcile(s)

    for issue in ix.list_issues(s, detector=ipol.DETECTOR_CONTRACT_DEFECT):
        events = ix.issue_events(s, issue)
        types = [e.event_type for e in events]
        # The migration's own reasoning survives...
        dispositions = [e for e in events if e.event_type == "DISPOSITION_DECIDED"]
        assert [d.payload_json["disposition"] for d in dispositions] == [
            "NEW_VERSION", "NO_ACTION"]
        # ...and so does the moment it stopped applying.
        withdrawn = [e for e in events
                     if e.event_type == "STATUS_CHANGED"
                     and e.to_status == "INVESTIGATING"
                     and "WITHDRAWN" in (e.reason or "")]
        assert len(withdrawn) == 1
        assert types[-1] == "CLOSED_NO_ACTION"


def test_the_decision_is_linked_not_just_asserted(xos_session, xos_platform):
    s = xos_session
    _both_books(s)
    _import_and_reconcile(s)

    for issue in ix.list_issues(s, detector=ipol.DETECTOR_CONTRACT_DEFECT):
        refs = {link_row.reference for link_row in ix.issue_links(s, issue)}
        assert issue_import.WITHDRAWAL_EVIDENCE_DOC in refs
        assert issue_import.STAND_DOWN_EVIDENCE_DOC in refs
        assert issue_import.WITHDRAWAL_PR_REF in refs
        # the per-book cause doc is linked too
        assert any(r.endswith("_DECONFOUNDING.md") or r.endswith("_DIAGNOSIS.md")
                   for r in refs)
        # ...and the withdrawal is cited as evidence, not only as a link.
        docs = [e.source_ref for e in ix.issue_evidence(s, issue)]
        assert issue_import.WITHDRAWAL_EVIDENCE_DOC in docs
        assert "docs/RESEARCH_LIVE_CANARY_CONTRACT_DEFECT.md" in docs, (
            "the ORIGINAL research evidence must be preserved"
        )


def test_reconciliation_is_idempotent(xos_session, xos_platform):
    """A repeat run must not reopen, reset status, restore NEW_VERSION, or
    duplicate evidence and events."""
    s = xos_session
    _both_books(s)
    _import_and_reconcile(s)

    def _snapshot():
        return {
            i.issue_key: (
                i.status, i.disposition, i.requires_new_version,
                i.requires_pause_or_stand_down,
                len(ix.issue_events(s, i)), len(ix.issue_evidence(s, i)),
                len(ix.issue_links(s, i)),
            )
            for i in ix.list_issues(s)
        }

    before = _snapshot()
    for _ in range(3):
        again = _import_and_reconcile(s)
        assert again["import"]["created"] == []
        assert again["reconcile"]["reconciled"] == []
        assert {r["issue_key"] for r in again["reconcile"]["already_reconciled"]} == (
            set(before)
        )
    assert _snapshot() == before


def test_a_rerun_cannot_resurrect_the_stale_disposition(xos_session, xos_platform):
    """The specific regression this guards: a second import restoring
    ACTION_REQUIRED / NEW_VERSION and re-scheduling withdrawn work."""
    s = xos_session
    _both_books(s)
    _import_and_reconcile(s)
    for _ in range(2):
        _import_and_reconcile(s)
    for issue in ix.list_issues(s):
        assert issue.status != "ACTION_REQUIRED"
        assert issue.disposition != "NEW_VERSION"
        assert issue.requires_new_version is False


def test_reconciling_before_importing_is_a_clean_skip(xos_session, xos_platform):
    s = xos_session
    _both_books(s)
    report = issue_import.reconcile_withdrawn_successors(s)
    s.commit()
    assert report["reconciled"] == []
    assert {r["reason"] for r in report["skipped"]} == {
        "finding has not been imported yet"}


def test_reconciliation_changes_no_experiment_state_or_gate(xos_session,
                                                            xos_platform):
    """Closing a ticket about a stood-down contract must not touch the contract."""
    s = xos_session
    books = _both_books(s)
    before = {
        key: (exp.state, ver.frozen_at, gate.spec_hash, gate.evidence_started_at,
              epoch.ended_at, len(read.transitions_for(s, exp)),
              len(read.gate_results_for(s, gate)),
              len(read.versions_for(s, exp)), len(read.epochs_for(s, ver)))
        for key, (exp, ver, epoch, gate) in books.items()
    }
    _import_and_reconcile(s)
    for key, (exp, ver, epoch, gate) in books.items():
        s.refresh(exp)
        assert before[key] == (
            exp.state, ver.frozen_at, gate.spec_hash, gate.evidence_started_at,
            epoch.ended_at, len(read.transitions_for(s, exp)),
            len(read.gate_results_for(s, gate)),
            len(read.versions_for(s, exp)), len(read.epochs_for(s, ver)))


def test_no_withdrawn_successor_version_is_created(xos_session, xos_platform):
    """The reconciliation records that v2 will NOT be created. Creating one
    would be the exact opposite of the decision it is recording."""
    s = xos_session
    books = _both_books(s)
    _import_and_reconcile(s)
    for _key, (exp, _ver, _epoch, _gate) in books.items():
        versions = read.versions_for(s, exp)
        assert [v.version for v in versions] == [1], (
            "no successor Version may exist after recording that it was withdrawn"
        )


def test_a_closed_finding_is_no_longer_an_active_control_tower_blocker(
        xos_session, xos_platform):
    """The stood-down contracts must stop demanding attention — while the
    investigation stays queryable for anyone asking what happened."""
    s = xos_session
    books = _both_books(s)
    _import_and_reconcile(s)

    rep = ct.build_report(s, evaluate=True)
    assert rep.contract_findings == []
    assert "CONTRACT DEFECT" not in ct.render(rep, session=s)
    for _key, (exp, _ver, _epoch, _gate) in books.items():
        assert read.contract_defect_findings(s, exp, 1) == []

    # ...and it is still fully readable.
    assert len(ix.list_issues(s)) == 2
    for issue in ix.list_issues(s):
        detail = read.issue_detail(s, issue)
        assert detail["status"] == "CLOSED_NO_ACTION"
        assert detail["events"] and detail["evidence"] and detail["links"]


# ---------------------------------------------------------------------------
# The read-only production preview
# ---------------------------------------------------------------------------


def test_the_plan_is_read_only_and_describes_the_work(xos_session, xos_platform):
    """The ops channel cannot run a write-and-rollback dry run, so the plan must
    answer the same question with plain selects."""
    from sqlalchemy import event

    s = xos_session
    _both_books(s)
    written: list[str] = []

    def _spy(session, flush_context, instances):
        for obj in list(session.new) + list(session.dirty) + list(session.deleted):
            written.append(type(obj).__name__)

    event.listen(type(s), "before_flush", _spy)
    try:
        plan = issue_import.plan(s)
    finally:
        event.remove(type(s), "before_flush", _spy)

    assert written == []
    assert {r["action"] for r in plan["plan"]} == {"IMPORT_THEN_CLOSE_NO_ACTION"}
    assert plan["reconciliation_key"] == issue_import.RECONCILIATION_KEY


def test_the_plan_reports_a_no_op_once_reconciled(xos_session, xos_platform):
    s = xos_session
    _both_books(s)
    _import_and_reconcile(s)
    plan = issue_import.plan(s)
    assert {r["action"] for r in plan["plan"]} == {"NO_OP"}
    assert all(r["status"] == "CLOSED_NO_ACTION" for r in plan["plan"])


def test_the_plan_says_skip_when_the_experiment_is_absent(xos_session,
                                                          xos_platform):
    s = xos_session
    plan = issue_import.plan(s)
    assert {r["action"] for r in plan["plan"]} == {"SKIP"}
    assert all("not registered" in r["reason"] for r in plan["plan"])
