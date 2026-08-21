"""Control Tower × issues — the report displays investigations and writes nothing.

Two properties matter more than the formatting:

  * **Rendering is read-only.** The Tower is safe to point at `DATABASE_URL_RO`,
    and a report that opened a ticket (or advanced a recurrence counter) as a
    side effect of being rendered would make that false — and would make the
    counter a measure of how often somebody ran the report.
  * **A ticket is not a verdict.** An open issue does not make a PASS
    provisional, a resolved one does not make a gate evaluable, and a resolved
    one must never suppress an anomaly that is happening right now.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import event, select

from kalshi_bot.experiment_os import control_tower as ct
from kalshi_bot.experiment_os import issue_policy as ipol
from kalshi_bot.experiment_os import issues as ix
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.models import (
    ExperimentIssue,
    ExperimentIssueEvent,
)
from kalshi_bot.models import PaperTrade

UTC = timezone.utc
T0 = datetime(2026, 8, 1, tzinfo=UTC)

UNPROVIDED_SPEC = {
    "pass_all": [{"metric": "candidate_rejection_rate_pct", "arm": "treatment",
                  "op": ">", "value": 0}],
}
COMPUTABLE_SPEC = {
    "sample": {"treatment": {"metric": "settled_trades", "op": ">=", "value": 5}},
    "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                  "op": ">", "value": 0}],
}


def _trade(s, tag, *, pnl=0.05, status="settled"):
    s.add(PaperTrade(
        market_ticker=f"T-{tag}-{id(object())}", strategy=tag, status=status,
        pnl=pnl, quantity=1, created_at=T0 + timedelta(hours=1),
    ))


def _experiment(s, key, *, spec=COMPUTABLE_SPEC, tag=None, trades=0):
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
        s, ver, gate_key="paper_to_live_canary", kind="promotion", spec=spec,
        from_state="PAPER", to_state="LIVE_CANARY", registered_at=T0,
    )
    svc.mark_gate_evidence_started(s, gate, at=T0)
    for _ in range(trades):
        _trade(s, tag, pnl=0.10)
    s.commit()
    return exp, ver, epoch, dep, gate, tag


def _candidates_by_detector(rep, detector):
    return [c for c in rep.issue_candidates if c["detector"] == detector]


# ---------------------------------------------------------------------------
# Read-only, structurally
# ---------------------------------------------------------------------------


def test_building_and_rendering_the_report_writes_nothing(xos_session, xos_platform):
    """Every insert/update/delete that reaches a flush is captured. The Tower
    detects several anomalies here, so this is a real test of the guarantee."""
    s = xos_session
    _experiment(s, "silent-book", spec=UNPROVIDED_SPEC)
    exp2, *_ = _experiment(s, "noisy-book", trades=6)
    svc.record_integrity_event(s, exp2, kind="MIXED_SNAPSHOTS",
                               description="two snapshots inside one epoch")
    s.commit()

    written: list[str] = []

    def _spy(session, flush_context, instances):
        for obj in list(session.new) + list(session.dirty) + list(session.deleted):
            written.append(type(obj).__name__)

    event.listen(type(s), "before_flush", _spy)
    try:
        rep = ct.build_report(s, evaluate=True)
        out = ct.render(rep, session=s)
    finally:
        event.remove(type(s), "before_flush", _spy)

    assert written == [], f"the Control Tower wrote {written}"
    assert rep.issue_candidates, "and it still detected the anomalies"
    assert "=== OPEN INVESTIGATIONS ===" in out


def test_no_issue_rows_are_created_by_rendering(xos_session, xos_platform):
    s = xos_session
    _experiment(s, "book", spec=UNPROVIDED_SPEC)
    s.commit()
    for _ in range(3):
        ct.render(ct.build_report(s, evaluate=True), session=s)
    assert s.scalar(select(ExperimentIssue).limit(1)) is None
    assert s.scalar(select(ExperimentIssueEvent).limit(1)) is None


def test_rendering_never_advances_a_recurrence_counter(xos_session, xos_platform):
    """A counter that moves when a report is rendered measures how often somebody
    ran the report."""
    s = xos_session
    _experiment(s, "book", spec=UNPROVIDED_SPEC)
    issue = ix.create_issue(
        s, title="collector stale", opened_by_role="LIVE_OPS",
        detector=ipol.DETECTOR_COLLECTOR_HEALTH,
        anomaly_kind="weather_forecasts:STALE",
        detector_fingerprint=ipol.issue_fingerprint(
            detector=ipol.DETECTOR_COLLECTOR_HEALTH,
            anomaly_kind="weather_forecasts:STALE"),
    )
    s.commit()
    for _ in range(3):
        ct.build_report(s, evaluate=False)
    s.refresh(issue)
    assert issue.occurrence_count == 1
    assert len(ix.issue_events(s, issue)) == 1


# ---------------------------------------------------------------------------
# Coverage: open issues vs unticketed anomalies
# ---------------------------------------------------------------------------


def _cover(s, candidate, *, owner="LIVE_OPS"):
    """Open an issue that covers a detected candidate, by fingerprint."""
    issue = ix.create_issue(
        s, title=candidate["title"], opened_by_role="EXPERIMENT_CONTROL_TOWER",
        opened_by_actor="tower", current_owner_role=owner,
        classification=candidate["recommended_classification"],
        detector=candidate["detector"], anomaly_kind=candidate["anomaly_kind"],
        detector_fingerprint=candidate["fingerprint"],
        experiment=candidate["experiment_id"],
        version=candidate["version_id"], epoch=candidate["epoch_id"],
        deployment=candidate["deployment_id"], gate=candidate["gate_id"],
    )
    s.commit()
    return issue


def test_an_open_issue_covers_its_matching_candidate(xos_session, xos_platform):
    s = xos_session
    _experiment(s, "blocked-book", spec=UNPROVIDED_SPEC)
    s.commit()

    before = ct.build_report(s, evaluate=True)
    candidate = _candidates_by_detector(before, ipol.DETECTOR_GATE_BLOCKED)[0]
    issue = _cover(s, candidate, owner="RESEARCH_LAB")

    after = ct.build_report(s, evaluate=True)
    assert not _candidates_by_detector(after, ipol.DETECTOR_GATE_BLOCKED), (
        "a covered anomaly is no longer unticketed"
    )
    covered = [i for i in after.open_issues if i["issue_key"] == issue.issue_key]
    assert covered and covered[0]["currently_recurring"] is True, (
        "and the report says the anomaly is still occurring"
    )
    out = ct.render(after, session=s)
    assert issue.issue_key in out
    assert "recurring" in out


def test_an_uncovered_anomaly_stays_visible(xos_session, xos_platform):
    """Covering one anomaly must not hide a different one."""
    s = xos_session
    _experiment(s, "blocked-book", spec=UNPROVIDED_SPEC)
    _experiment(s, "other-blocked-book", spec=UNPROVIDED_SPEC)
    s.commit()

    rep = ct.build_report(s, evaluate=True)
    first = _candidates_by_detector(rep, ipol.DETECTOR_GATE_BLOCKED)[0]
    _cover(s, first)

    after = ct.build_report(s, evaluate=True)
    remaining = _candidates_by_detector(after, ipol.DETECTOR_GATE_BLOCKED)
    assert len(remaining) == 1
    assert remaining[0]["experiment"] != first["experiment"]
    assert remaining[0]["title"] in ct.render(after, session=s)


def test_a_closed_issue_does_not_suppress_a_recurring_anomaly(xos_session,
                                                              xos_platform):
    """"We fixed that once" is not evidence that it is fixed now."""
    s = xos_session
    _experiment(s, "blocked-book", spec=UNPROVIDED_SPEC)
    s.commit()
    candidate = _candidates_by_detector(
        ct.build_report(s, evaluate=True), ipol.DETECTOR_GATE_BLOCKED)[0]
    issue = _cover(s, candidate, owner="RESEARCH_LAB")

    for status in ("TRIAGE", "INVESTIGATING", "ACTION_REQUIRED", "VALIDATING"):
        ix.set_issue_status(s, issue, status=status, actor="r",
                            actor_role="RESEARCH_LAB", reason="working")
    ix.record_validation_result(s, issue, passed=True, summary="provider landed",
                                actor="r", actor_role="RESEARCH_LAB")
    ix.resolve_issue(s, issue, resolution_summary="implemented the provider",
                     actor="r", actor_role="RESEARCH_LAB")
    s.commit()

    after = ct.build_report(s, evaluate=True)
    assert _candidates_by_detector(after, ipol.DETECTOR_GATE_BLOCKED), (
        "the gate is STILL blocked, so the anomaly must reappear as unticketed"
    )
    assert issue.issue_key not in {i["issue_key"] for i in after.open_issues}
    assert "=== UNTICKETED ANOMALIES ===" in ct.render(after, session=s)


def test_a_resolved_issue_does_not_make_a_gate_evaluable(xos_session, xos_platform):
    """The clearest form of "a ticket is not a verdict"."""
    s = xos_session
    _experiment(s, "blocked-book", spec=UNPROVIDED_SPEC)
    s.commit()
    before = ct.build_report(s, evaluate=True)
    verdicts_before = {(b["experiment"], b["gate_key"], b["verdict"])
                       for b in before.blocked}

    candidate = _candidates_by_detector(before, ipol.DETECTOR_GATE_BLOCKED)[0]
    issue = _cover(s, candidate, owner="RESEARCH_LAB")
    for status in ("TRIAGE", "INVESTIGATING", "ACTION_REQUIRED", "VALIDATING"):
        ix.set_issue_status(s, issue, status=status, actor="r",
                            actor_role="RESEARCH_LAB", reason="w")
    ix.record_validation_result(s, issue, passed=True, summary="done", actor="r",
                                actor_role="RESEARCH_LAB")
    ix.resolve_issue(s, issue, resolution_summary="done", actor="r",
                     actor_role="RESEARCH_LAB")
    s.commit()

    after = ct.build_report(s, evaluate=True)
    assert verdicts_before == {(b["experiment"], b["gate_key"], b["verdict"])
                               for b in after.blocked}
    assert verdicts_before, "the gate really was blocked"


def test_an_open_ticket_does_not_change_a_pass(xos_session, xos_platform):
    s = xos_session
    exp, ver, _e, _d, gate, _tag = _experiment(s, "healthy-book", trades=6)
    s.commit()

    def _verdicts(rep):
        return {
            (v["key"], g["gate_key"]): (g.get("live_verdict"),
                                        (g.get("latest_result") or {}).get("verdict"))
            for views in rep.by_state.values() for v in views for g in v["gates"]
        }

    before = _verdicts(ct.build_report(s, evaluate=True))
    ix.create_issue(s, title="somebody is suspicious of this book",
                    opened_by_role="RESEARCH_LAB", experiment=exp, gate=gate,
                    current_owner_role="RESEARCH_LAB", classification="STRATEGY")
    s.commit()
    after_rep = ct.build_report(s, evaluate=True)
    assert _verdicts(after_rep) == before
    assert after_rep.open_issues, "the issue is still REPORTED"


# ---------------------------------------------------------------------------
# What becomes a candidate — and what deliberately does not
# ---------------------------------------------------------------------------


def test_a_blocked_gate_becomes_a_candidate_routed_by_its_verdict(xos_session,
                                                                  xos_platform):
    s = xos_session
    _experiment(s, "blocked-book", spec=UNPROVIDED_SPEC)
    s.commit()
    rep = ct.build_report(s, evaluate=True)
    cand = _candidates_by_detector(rep, ipol.DETECTOR_GATE_BLOCKED)[0]
    assert cand["anomaly_kind"] == "BLOCKED_DATA"
    assert cand["recommended_owner"] == "RESEARCH_LAB", (
        "a missing provider is not a Platform Revision"
    )
    assert cand["gate_id"] is not None and cand["version_id"] is not None


def test_a_zero_evidence_experiment_becomes_a_live_ops_candidate(xos_session,
                                                                 xos_platform):
    s = xos_session
    _experiment(s, "quiet-book", trades=0)
    s.commit()
    rep = ct.build_report(s, evaluate=False)
    cand = _candidates_by_detector(rep, ipol.DETECTOR_ZERO_EVIDENCE)
    assert len(cand) == 1
    assert cand[0]["recommended_owner"] == "LIVE_OPS"
    assert cand[0]["recommended_classification"] == "UNCLASSIFIED"


def test_an_experiment_with_evidence_is_not_a_zero_evidence_candidate(xos_session,
                                                                      xos_platform):
    s = xos_session
    _experiment(s, "busy-book", trades=6)
    s.commit()
    rep = ct.build_report(s, evaluate=False)
    assert not _candidates_by_detector(rep, ipol.DETECTOR_ZERO_EVIDENCE)


def test_an_unresolved_integrity_event_becomes_a_candidate(xos_session,
                                                           xos_platform):
    s = xos_session
    exp, *_ = _experiment(s, "contaminated-book", trades=6)
    svc.record_integrity_event(s, exp, kind="MIXED_SNAPSHOTS",
                               description="two snapshots inside one epoch")
    s.commit()
    rep = ct.build_report(s, evaluate=False)
    cand = _candidates_by_detector(rep, ipol.DETECTOR_INTEGRITY_EVENT)
    assert len(cand) == 1
    assert cand[0]["anomaly_kind"] == "MIXED_SNAPSHOTS"
    assert cand[0]["requires_operator_triage"] is True, (
        "an unknown integrity cause must not be guessed at"
    )


def test_a_recorded_stand_down_produces_no_candidate(xos_session, xos_platform):
    """Its cause is already recorded, so there is nothing to diagnose. This is
    the same rule that keeps INACTIVE collectors out of the action list, and
    manufacturing a ticket here is how a deliberate pause starts reading as an
    unexplained failure."""
    s = xos_session
    exp, *_ = _experiment(s, "stood-down-book", trades=0)
    svc.record_integrity_event(s, exp, kind="EXPERIMENT_EXECUTION_STOOD_DOWN",
                               description="the runtime live allowlist is empty")
    s.commit()
    rep = ct.build_report(s, evaluate=False)
    assert not [c for c in rep.issue_candidates if c["experiment"] == "stood-down-book"]
    # ...including the zero-evidence one: the absence is explained.
    assert not _candidates_by_detector(rep, ipol.DETECTOR_ZERO_EVIDENCE)
    assert rep.recorded_state, "and it is still visible as recorded state"


def test_inactive_collectors_create_no_candidate(xos_session, xos_platform):
    """`INACTIVE` means "not part of this deployment". Only STALE/EMPTY/
    UNAVAILABLE are collector-health problems."""
    s = xos_session
    rep = ct.build_report(s, evaluate=False)
    kinds = {c["anomaly_kind"] for c in
             _candidates_by_detector(rep, ipol.DETECTOR_COLLECTOR_HEALTH)}
    assert kinds, "the in-memory database has empty collectors, so some fire"
    assert not any(k.endswith(":INACTIVE") for k in kinds)
    assert all(k.split(":")[-1] in ct.COLLECTOR_WARNING_STATUSES for k in kinds)


def test_no_contract_defect_is_manufactured_by_heuristic(xos_session, xos_platform):
    """Nothing infers a defect from the shape of a gate. A heuristic would be a
    second, unreviewed opinion competing with the canonical contract."""
    s = xos_session
    _experiment(s, "unregistered-book", spec=UNPROVIDED_SPEC)
    s.commit()
    rep = ct.build_report(s, evaluate=True)
    assert not _candidates_by_detector(rep, ipol.DETECTOR_CONTRACT_DEFECT)
    assert rep.contract_findings == []
    assert rep.blocked, "the real evaluator block is still shown"


# ---------------------------------------------------------------------------
# Both blocker classes, and the report's ordering
# ---------------------------------------------------------------------------


def test_an_evaluator_block_and_a_contract_defect_both_display(xos_session,
                                                               xos_platform):
    """Clearing one does not clear the other, and reading the first as the whole
    diagnosis is the standing misreading this section exists to prevent."""
    s = xos_session
    exp, ver, *_ = _experiment(s, "malformed-book", spec=UNPROVIDED_SPEC)
    ix.create_issue(
        s, title="imported gate addresses deployment_kind=paper",
        opened_by_role="RESEARCH_LAB", experiment=exp, version=ver,
        classification="STRATEGY", current_owner_role="RESEARCH_LAB",
        detector=ipol.DETECTOR_CONTRACT_DEFECT,
        details_json={
            "detail": ["clauses default to paper",
                       "epoch has live + paper_twin only"],
            "independent_of_evaluator": True,
            "evidence_doc": "docs/RESEARCH_LIVE_CANARY_CONTRACT_DEFECT.md",
            "proven_at": "2026-08-17", "proven_by": "Research Lab",
        },
    )
    s.commit()

    out = ct.render(ct.build_report(s, evaluate=True), session=s)
    assert "BLOCKED_DATA" in out and "missing providers:" in out
    assert "CONTRACT DEFECT" in out
    assert "epoch has live + paper_twin only" in out
    assert "is NOT the whole" in out
    assert "docs/RESEARCH_LIVE_CANARY_CONTRACT_DEFECT.md" in out


def test_a_contract_defect_names_its_investigation(xos_session, xos_platform):
    s = xos_session
    exp, ver, *_ = _experiment(s, "malformed-book", spec=UNPROVIDED_SPEC)
    issue = ix.create_issue(
        s, title="imported gate addresses deployment_kind=paper",
        opened_by_role="RESEARCH_LAB", experiment=exp, version=ver,
        classification="STRATEGY", current_owner_role="RESEARCH_LAB",
        detector=ipol.DETECTOR_CONTRACT_DEFECT,
        details_json={"detail": ["clauses default to paper"],
                      "independent_of_evaluator": True},
    )
    s.commit()
    out = ct.render(ct.build_report(s, evaluate=True), session=s)
    assert f"issue show {issue.issue_key}" in out


def test_integrity_still_precedes_performance(xos_session, xos_platform):
    """The new sections sit between SYSTEM/INTEGRITY and the lifecycle groups —
    an open investigation is a statement about whether the numbers below can be
    trusted, and reading it afterwards is reading it too late."""
    s = xos_session
    _experiment(s, "book", trades=6)
    s.commit()
    out = ct.render(ct.build_report(s, evaluate=True), session=s)
    order = [
        out.index("=== SYSTEM / INTEGRITY ==="),
        out.index("=== OPEN INVESTIGATIONS ==="),
        out.index("=== UNTICKETED ANOMALIES ==="),
        out.index("=== PAPER ("),
        out.index("=== PORTFOLIO"),
    ]
    assert order == sorted(order)


def test_the_render_states_that_it_cannot_open_a_ticket(xos_session, xos_platform):
    s = xos_session
    _experiment(s, "quiet-book", trades=0)
    s.commit()
    out = ct.render(ct.build_report(s, evaluate=False), session=s)
    assert "this report cannot open one" in out
    assert "read-only" in out.lower()
    assert "never changes a gate verdict" in out


def test_recommendations_use_the_tickets_current_owner(xos_session, xos_platform):
    """A ticket may legitimately have been transferred; re-recommending the
    original role would undo that in the reader's head."""
    s = xos_session
    _experiment(s, "quiet-book", trades=0)
    s.commit()
    cand = _candidates_by_detector(
        ct.build_report(s, evaluate=False), ipol.DETECTOR_ZERO_EVIDENCE)[0]
    issue = _cover(s, cand, owner="LIVE_OPS")
    ix.add_issue_evidence(s, issue, evidence_type="OPS_RESULT",
                          summary="worker healthy; filters reject every candidate")
    ix.transfer_issue(s, issue, to_owner_role="RESEARCH_LAB", actor="ops",
                      actor_role="LIVE_OPS", classification="STRATEGY",
                      reason="runtime verified healthy")
    s.commit()

    rep = ct.build_report(s, evaluate=False)
    line = next(r for r in rep.recommendations if issue.issue_key in r)
    assert "RESEARCH_LAB" in line
    assert "LIVE_OPS" not in line


def test_an_uncovered_candidate_recommends_the_initial_owner(xos_session,
                                                             xos_platform):
    s = xos_session
    _experiment(s, "quiet-book", trades=0)
    s.commit()
    rep = ct.build_report(s, evaluate=False)
    line = next(r for r in rep.recommendations if "quiet-book" in r)
    assert "NO open issue covers this" in line
    assert "LIVE_OPS" in line
    assert "Opening it is a WRITE" in line
