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
