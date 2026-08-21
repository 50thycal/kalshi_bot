"""Adopting a Control Tower candidate into a durable issue.

Without this path the documented workflow was broken end to end, in a way that
looked like it worked: the Tower detects an anomaly, computes its deterministic
fingerprint and exact lineage, and prints it under UNTICKETED ANOMALIES — but
`issue create` could carry neither. So the ticket you opened never matched the
candidate that caused it, and the very next report showed the same anomaly as
still unticketed. Two records of one problem, neither aware of the other.

The end-to-end test below is the one that matters: candidate → adopt → the same
anomaly is covered, gone from UNTICKETED, present in OPEN INVESTIGATIONS as
recurring, and a second adoption refused. Everything else here is a refusal
path, because a command that silently opens the wrong ticket is worse than one
that refuses.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kalshi_bot.experiment_os import cli, read
from kalshi_bot.experiment_os import control_tower as ct
from kalshi_bot.experiment_os import issue_policy as ipol
from kalshi_bot.experiment_os import issues as ix
from kalshi_bot.experiment_os import service as svc

UTC = timezone.utc
T0 = datetime(2026, 8, 1, tzinfo=UTC)

UNPROVIDED_SPEC = {
    "pass_all": [{"metric": "candidate_rejection_rate_pct", "arm": "treatment",
                  "op": ">", "value": 0}],
}


def _book(s, key="quiet-book", *, spec=None, evidence=False):
    """A registered, deployed paper book. With no trades it is a zero-evidence
    candidate; with `spec` it also carries a blocked gate."""
    tag = f"{key}-t"
    exp = svc.create_experiment(s, key=key, origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="lever", now=T0,
        control_exemption_reason="test shape",
    )
    svc.add_arm(s, ver, arm_key="treatment", role="treatment", strategy_tag=tag)
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    dep = svc.register_deployment(
        s, epoch, deployment_key=f"{key}-paper", stage="PAPER", kind="paper",
        arms={"treatment": tag}, started_at=T0,
    )
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    gate = None
    if spec is not None:
        gate = svc.register_gate(
            s, ver, gate_key="paper_to_live_canary", kind="promotion", spec=spec,
            from_state="PAPER", to_state="LIVE_CANARY", registered_at=T0,
        )
        svc.mark_gate_evidence_started(s, gate, at=T0)
    if evidence:
        from kalshi_bot.models import PaperTrade
        for n in range(6):
            s.add(PaperTrade(market_ticker=f"T-{key}-{n}", strategy=tag,
                             status="settled", pnl=0.1, quantity=1,
                             created_at=T0))
    s.commit()
    return exp, ver, epoch, dep, gate


def _candidate(s, detector, *, evaluate=False):
    rep = ct.build_report(s, evaluate=evaluate)
    matches = [c for c in rep.issue_candidates if c["detector"] == detector]
    assert matches, f"expected a {detector} candidate"
    return matches[0]


# ---------------------------------------------------------------------------
# The end-to-end path the docs promise
# ---------------------------------------------------------------------------


def test_adopting_a_candidate_covers_the_anomaly_end_to_end(xos_session,
                                                            xos_platform):
    s = xos_session
    exp, ver, epoch, _dep, _gate = _book(s, "quiet-book")

    # 1. The Tower detects it and reports it as unticketed.
    before = ct.build_report(s, evaluate=False)
    cand = next(c for c in before.issue_candidates
                if c["detector"] == ipol.DETECTOR_ZERO_EVIDENCE)
    assert "quiet-book" in ct.render(before, session=s)

    # 2. Adopt it through the CLI, exactly as documented.
    args = cli.build_parser().parse_args([
        "issue", "open-candidate", cand["fingerprint"],
        "--actor", "cal", "--opened-by-role", "EXPERIMENT_CONTROL_TOWER",
        "--no-evaluate",
    ])
    assert cli.cmd_issue(s, args) == 0

    # 3. One issue, scoped exactly to what was detected.
    issues = ix.list_issues(s, open_only=True)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.detector == ipol.DETECTOR_ZERO_EVIDENCE
    assert issue.detector_fingerprint == cand["fingerprint"]
    assert (issue.experiment_id, issue.version_id, issue.epoch_id) == (
        exp.id, ver.id, epoch.id)
    assert issue.current_owner_role == cand["recommended_owner"]
    assert issue.classification == cand["recommended_classification"]
    assert (issue.severity, issue.priority) == (cand["severity"], cand["priority"])

    # 4. The SAME candidate is now covered: gone from UNTICKETED, present in
    #    OPEN INVESTIGATIONS, and flagged as still recurring.
    after = ct.build_report(s, evaluate=False)
    assert not [c for c in after.issue_candidates
                if c["fingerprint"] == cand["fingerprint"]]
    covered = [c for c in after.covered_candidates
               if c["fingerprint"] == cand["fingerprint"]]
    assert covered and covered[0]["covered_by"] == issue.issue_key

    listed = [i for i in after.open_issues if i["issue_key"] == issue.issue_key]
    assert listed and listed[0]["currently_recurring"] is True

    out = ct.render(after, session=s)
    assert issue.issue_key in out
    unticketed = out.split("=== UNTICKETED ANOMALIES ===")[1].split("===")[0]
    assert issue.issue_key not in unticketed

    # 5. A second adoption of the same fingerprint is refused.
    assert cli.cmd_issue(s, cli.build_parser().parse_args([
        "issue", "open-candidate", cand["fingerprint"],
        "--actor", "cal", "--opened-by-role", "EXPERIMENT_CONTROL_TOWER",
        "--no-evaluate",
    ])) == 1
    assert len(ix.list_issues(s, open_only=True)) == 1, "still exactly one ticket"


def test_a_blocked_gate_candidate_adopts_with_its_gate_and_verdict(xos_session,
                                                                   xos_platform):
    """The scope that matters most for a gate anomaly is the gate itself."""
    s = xos_session
    exp, ver, _epoch, _dep, gate = _book(s, "blocked-book", spec=UNPROVIDED_SPEC,
                                         evidence=True)
    cand = _candidate(s, ipol.DETECTOR_GATE_BLOCKED, evaluate=True)

    issue = ix.open_issue_from_candidate(
        s, cand["fingerprint"], actor="cal",
        opened_by_role="EXPERIMENT_CONTROL_TOWER", evaluate=True)
    s.commit()

    assert issue.gate_id == gate.id
    assert (issue.experiment_id, issue.version_id) == (exp.id, ver.id)
    assert issue.details_json["anomaly_kind"] == "BLOCKED_DATA"
    assert issue.details_json["gate_verdict"] == "BLOCKED_DATA"
    # ...and it routes where a BLOCKED_DATA gate should, not to Platform Review.
    assert issue.current_owner_role == "RESEARCH_LAB"
    assert issue.classification == "DATA"


def test_adoption_records_the_control_tower_as_evidence(xos_session, xos_platform):
    """Where the problem came from is itself a fact worth citing."""
    s = xos_session
    _book(s, "quiet-book")
    cand = _candidate(s, ipol.DETECTOR_ZERO_EVIDENCE)
    issue = ix.open_issue_from_candidate(
        s, cand["fingerprint"], actor="cal",
        opened_by_role="EXPERIMENT_CONTROL_TOWER", evaluate=False)
    s.commit()

    evidence = ix.issue_evidence(s, issue)
    assert [e.evidence_type for e in evidence] == ["CONTROL_TOWER"]
    assert ipol.DETECTOR_ZERO_EVIDENCE in evidence[0].summary
    assert issue.details_json["adopted_from_candidate"] is True


def test_adoption_does_not_record_a_spurious_owner_override(xos_session,
                                                            xos_platform):
    """The candidate's routing IS the policy's routing, so adopting it must not
    look like a human overrode the recommendation."""
    s = xos_session
    _book(s, "quiet-book")
    cand = _candidate(s, ipol.DETECTOR_ZERO_EVIDENCE)
    issue = ix.open_issue_from_candidate(
        s, cand["fingerprint"], actor="cal",
        opened_by_role="EXPERIMENT_CONTROL_TOWER", evaluate=False)
    s.commit()
    created = ix.issue_events(s, issue)[0]
    assert "owner_override" not in (created.payload_json or {})


# ---------------------------------------------------------------------------
# Refusals — a command that opens the wrong ticket is worse than one that stops
# ---------------------------------------------------------------------------


def test_an_unknown_fingerprint_is_refused(xos_session, xos_platform):
    s = xos_session
    _book(s, "quiet-book")
    with pytest.raises(ix.CandidateNotAdoptable, match="no candidate with fingerprint"):
        ix.open_issue_from_candidate(
            s, "de" * 32, actor="cal", opened_by_role="OPERATOR", evaluate=False)


def test_a_stale_fingerprint_is_refused_rather_than_resurrected(xos_session,
                                                                xos_platform):
    """A fingerprint read from an older report whose anomaly has since cleared
    must not become a ticket for a problem that no longer exists."""
    s = xos_session
    _book(s, "quiet-book")
    stale = _candidate(s, ipol.DETECTOR_ZERO_EVIDENCE)["fingerprint"]

    # The book starts producing evidence, so the anomaly clears.
    from kalshi_bot.models import PaperTrade
    for n in range(6):
        s.add(PaperTrade(market_ticker=f"T-late-{n}", strategy="quiet-book-t",
                         status="settled", pnl=0.1, quantity=1, created_at=T0))
    s.commit()
    assert not [c for c in ct.build_report(s, evaluate=False).issue_candidates
                if c["detector"] == ipol.DETECTOR_ZERO_EVIDENCE]

    with pytest.raises(ix.CandidateNotAdoptable, match="cleared|not.*detected"):
        ix.open_issue_from_candidate(
            s, stale, actor="cal", opened_by_role="OPERATOR", evaluate=False)
    assert ix.list_issues(s) == []


def test_an_already_covered_candidate_is_refused_by_name(xos_session, xos_platform):
    s = xos_session
    _book(s, "quiet-book")
    cand = _candidate(s, ipol.DETECTOR_ZERO_EVIDENCE)
    first = ix.open_issue_from_candidate(
        s, cand["fingerprint"], actor="cal", opened_by_role="OPERATOR",
        evaluate=False)
    s.commit()

    with pytest.raises(ix.CandidateNotAdoptable, match=first.issue_key):
        ix.open_issue_from_candidate(
            s, cand["fingerprint"], actor="cal", opened_by_role="OPERATOR",
            evaluate=False)


def test_an_ambiguous_fingerprint_is_refused_rather_than_guessed(xos_session,
                                                                 xos_platform):
    """Two detections sharing one fingerprint is a bug, not a tie to break."""
    s = xos_session
    _book(s, "quiet-book")
    cand = _candidate(s, ipol.DETECTOR_ZERO_EVIDENCE)
    doubled = [cand, dict(cand)]
    with pytest.raises(ix.CandidateNotAdoptable, match="matches 2 candidates"):
        ix.open_issue_from_candidate(
            s, cand["fingerprint"], actor="cal", opened_by_role="OPERATOR",
            candidates=doubled)


def test_a_blank_fingerprint_is_refused(xos_session, xos_platform):
    with pytest.raises(ix.IssueError, match="fingerprint is required"):
        ix.open_issue_from_candidate(
            xos_session, "   ", actor="cal", opened_by_role="OPERATOR",
            candidates=[])


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------


def test_open_candidate_is_a_write_and_refuses_the_read_only_connection(
        monkeypatch):
    monkeypatch.setenv("DATABASE_URL_RO", "postgresql://ro@example/db")
    assert "open-candidate" not in cli._ISSUE_READ_ACTIONS
    args = type("A", (), {"issue_action": "open-candidate"})()
    assert cli.cmd_issue(None, args) == 2


def test_adoption_changes_no_lifecycle_gate_or_verdict(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch, dep, gate = _book(s, "blocked-book", spec=UNPROVIDED_SPEC,
                                       evidence=True)
    before = (exp.state, ver.frozen_at, gate.spec_hash, gate.evidence_started_at,
              epoch.ended_at, dep.ended_at,
              len(read.transitions_for(s, exp)),
              len(read.gate_results_for(s, gate)))

    cand = _candidate(s, ipol.DETECTOR_GATE_BLOCKED, evaluate=True)
    ix.open_issue_from_candidate(
        s, cand["fingerprint"], actor="cal",
        opened_by_role="EXPERIMENT_CONTROL_TOWER", evaluate=True)
    s.commit()
    s.refresh(exp)

    assert before == (exp.state, ver.frozen_at, gate.spec_hash,
                      gate.evidence_started_at, epoch.ended_at, dep.ended_at,
                      len(read.transitions_for(s, exp)),
                      len(read.gate_results_for(s, gate)))


def test_detecting_candidates_writes_nothing(xos_session, xos_platform):
    """The resolution path an adoption runs first must stay a pure read."""
    from sqlalchemy import event

    s = xos_session
    _book(s, "quiet-book")
    written: list[str] = []

    def _spy(session, flush_context, instances):
        for obj in list(session.new) + list(session.dirty) + list(session.deleted):
            written.append(type(obj).__name__)

    event.listen(type(s), "before_flush", _spy)
    try:
        assert ct.detected_candidates(s, evaluate=False)
    finally:
        event.remove(type(s), "before_flush", _spy)
    assert written == []
