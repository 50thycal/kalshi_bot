"""Experiment OS service invariants — targeting false conclusions, not CRUD.

What these tests pin (spec §35 acceptance + §36 test philosophy):

  * a new experiment always receives the CURRENT complete platform snapshot, and
    cannot exist without one;
  * an incomplete platform registry blocks experiment/epoch creation by name;
  * version-vs-epoch: an environment change makes a new epoch on the SAME version,
    a scientific change makes a NEW version — and the two are not interchangeable;
  * frozen versions, registered gates, transitions, and gate results are immutable
    through the ORM (flush guard), not merely by convention;
  * money-adjacent promotions require operator approval AND a PASS gate result;
  * an illegal transition writes nothing;
  * a retired experiment is terminal; succession references it instead.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from kalshi_bot.experiment_os import read
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.lifecycle import (
    IllegalTransition,
    OperatorApprovalRequired,
)
from kalshi_bot.experiment_os.models import (
    STANDARD_PLATFORM_COMPONENTS,
    ExperimentStateTransition,
    PlatformSnapshot,
)

UTC = timezone.utc


def _dt(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


def _naive(dt: datetime) -> datetime:
    """sqlite drops tzinfo on round-trip; normalize for instant comparison."""
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


GATE_SPEC = {
    "sample": {"treatment": {"metric": "settled_trades", "op": ">=", "value": 100}},
    "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment", "op": ">", "value": 0}],
}


def _experiment(session, key="exp-1", **kw):
    return svc.create_experiment(session, key=key, origin="operator", **kw)


def _frozen_version(session, exp, *, control=True, **kw):
    ver = svc.create_experiment_version(
        session,
        exp,
        hypothesis="h",
        independent_variable="the lever",
        held_constant=["universe", "sizing"],
        control_required=control,
        **kw,
    )
    svc.add_arm(session, ver, arm_key="treatment", role="treatment")
    if control:
        svc.add_arm(session, ver, arm_key="control", role="control")
    svc.freeze_version(session, ver)
    return ver


# ---------------------------------------------------------------------------
# Platform snapshots
# ---------------------------------------------------------------------------


def test_experiment_requires_platform_snapshot(xos_session):
    """No registry → no experiment. A new experiment must inherit the shared
    platform baseline, never start without one."""
    with pytest.raises(svc.MissingPlatformSnapshot, match="no platform components"):
        _experiment(xos_session)


def test_incomplete_registry_blocks_by_name(xos_session):
    svc.ensure_standard_components(xos_session)
    svc.register_platform_revision(xos_session, "FEE_MODEL", version="v1", activate=True)
    with pytest.raises(svc.MissingPlatformSnapshot, match="FILL_MODEL"):
        _experiment(xos_session)


def test_new_experiment_receives_current_snapshot(xos_session, xos_platform):
    exp1 = _experiment(xos_session, key="exp-a")
    exp2 = _experiment(xos_session, key="exp-b")
    # Unchanged platform → the same content-addressed snapshot row, not a duplicate.
    assert exp1.platform_snapshot_id == xos_platform.id
    assert exp2.platform_snapshot_id == xos_platform.id
    assert xos_session.scalar(select(PlatformSnapshot.id).order_by(PlatformSnapshot.id.desc()))

    # A fee-model change activates → the NEXT experiment pins a NEW snapshot.
    svc.register_platform_revision(xos_session, "FEE_MODEL", version="v2", activate=True)
    exp3 = _experiment(xos_session, key="exp-c")
    assert exp3.platform_snapshot_id != xos_platform.id
    new_snap = xos_session.get(PlatformSnapshot, exp3.platform_snapshot_id)
    contents = dict(read.snapshot_contents(xos_session, new_snap))
    assert contents["FEE_MODEL"] == "v2"
    # The old snapshot still records the old world, untouched.
    old = dict(read.snapshot_contents(xos_session, xos_platform))
    assert old["FEE_MODEL"] == "v1"


def test_snapshot_is_complete_over_all_components(xos_session, xos_platform):
    assert svc.snapshot_missing_components(xos_session, xos_platform) == []
    assert len(read.snapshot_contents(xos_session, xos_platform)) == len(
        STANDARD_PLATFORM_COMPONENTS
    )


def test_activation_retires_the_prior_revision(xos_session, xos_platform):
    rev2 = svc.register_platform_revision(
        xos_session, "MARKET_TAXONOMY", version="v2", activate=True
    )
    from kalshi_bot.experiment_os.models import PlatformRevision

    rows = xos_session.scalars(
        select(PlatformRevision).where(
            PlatformRevision.component_id == rev2.component_id
        )
    ).all()
    by_version = {r.version: r for r in rows}
    assert by_version["v1"].status == "retired"
    assert by_version["v1"].retired_at is not None
    assert by_version["v2"].status == "active"


def test_duplicate_revision_is_refused(xos_session, xos_platform):
    with pytest.raises(svc.ExperimentOsError, match="immutable"):
        svc.register_platform_revision(xos_session, "FEE_MODEL", version="v1")


def test_pending_revision_semantics_are_still_editable(xos_session, xos_platform):
    """A pending revision is a draft declaration — refining it is fine."""
    rev = svc.register_platform_revision(
        xos_session, "FEE_MODEL", version="v2", description="draft"
    )
    xos_session.commit()
    rev.description = "maker entries billed at the measured maker rate"
    rev.normalization_available = True
    xos_session.flush()  # allowed: not yet activated


def test_activated_revision_semantics_are_frozen(xos_session, xos_platform):
    """Once activated, the semantic declaration history depends on is immutable —
    only lifecycle fields (status / activated_at / retired_at) may change."""
    rev = svc.register_platform_revision(
        xos_session, "FEE_MODEL", version="v2",
        description="the declared semantics", reason="fee fix",
        fingerprint="abc123", normalization_available=True,
        safety_class="routine", pr_ref="PR #150", activate=True,
    )
    xos_session.commit()
    for field, value in [
        ("fingerprint", "tampered"),
        ("description", "quietly relabeled"),
        ("reason", "revised rationale"),
        ("backward_compatibility", "suddenly compatible"),
        ("normalization_available", False),
        ("safety_class", "critical"),
        ("pr_ref", "PR #999"),
    ]:
        setattr(rev, field, value)
        with pytest.raises(svc.ImmutableRecord, match="semantic declaration is frozen"):
            xos_session.flush()
        xos_session.rollback()
    # Lifecycle transitions still work: active → retired.
    rev2 = svc.register_platform_revision(
        xos_session, "FEE_MODEL", version="v3", activate=True
    )
    assert rev2.status == "active"
    refreshed = xos_session.get(type(rev2), rev.id)
    assert refreshed.status == "retired" and refreshed.retired_at is not None


def test_unknown_activation_boundary_stays_explicit(xos_session, xos_platform):
    """boundary_unknown=True activates with activated_at NULL — the uncertainty is
    recorded, never papered over with an import timestamp. The boundary can be
    established later from measurement, exactly once, and doing so also closes the
    predecessor's open retired_at."""
    with pytest.raises(svc.ExperimentOsError, match="contradicts"):
        svc.register_platform_revision(
            xos_session, "FILL_MODEL", version="v2",
            activate=True, activated_at=_dt(2026, 8, 1), boundary_unknown=True,
        )
    rev = svc.register_platform_revision(
        xos_session, "FILL_MODEL", version="v2", activate=True, boundary_unknown=True
    )
    assert rev.status == "active"
    assert rev.activated_at is None
    # The prior revision's end boundary is equally unknown — NULL, not fabricated.
    from kalshi_bot.experiment_os.models import PlatformRevision

    v1 = xos_session.scalar(
        select(PlatformRevision).where(
            PlatformRevision.component_id == rev.component_id,
            PlatformRevision.version == "v1",
        )
    )
    assert v1.status == "retired" and v1.retired_at is None
    # An unknown boundary does not block snapshot resolution (the set is what it is).
    assert svc.resolve_active_platform_snapshot(xos_session) is not None

    svc.establish_activation_boundary(
        xos_session, rev, activated_at=_dt(2026, 8, 11, 14, 30)
    )
    assert _naive(rev.activated_at) == datetime(2026, 8, 11, 14, 30)
    assert _naive(v1.retired_at) == datetime(2026, 8, 11, 14, 30)
    # Established once: it cannot be moved.
    with pytest.raises(svc.ExperimentOsError, match="cannot be moved"):
        svc.establish_activation_boundary(
            xos_session, rev, activated_at=_dt(2026, 8, 12)
        )


# ---------------------------------------------------------------------------
# Lifecycle transitions through the service
# ---------------------------------------------------------------------------


def test_creation_writes_the_audit_row(xos_session, xos_platform):
    exp = _experiment(xos_session)
    rows = read.transitions_for(xos_session, exp)
    assert len(rows) == 1
    assert rows[0].from_state is None
    assert rows[0].to_state == "IDEA"
    assert rows[0].actor == "operator"


def test_full_forward_walk_is_audited(xos_session, xos_platform):
    exp = _experiment(xos_session)
    ver = _frozen_version(xos_session, exp)
    gate = svc.register_gate(
        xos_session,
        ver,
        gate_key="paper_to_live_canary",
        kind="promotion",
        from_state="PAPER",
        to_state="LIVE_CANARY",
        spec=GATE_SPEC,
    )
    svc.transition_experiment(xos_session, exp, "PROBE", actor="operator")
    svc.transition_experiment(xos_session, exp, "PAPER", actor="operator")
    result = svc.record_gate_result(
        xos_session, gate, verdict="PASS", explanation="paper gate cleared"
    )
    svc.transition_experiment(
        xos_session, exp, "LIVE_CANARY",
        actor="operator", approved_by="operator", gate_result=result,
    )
    result2 = svc.record_gate_result(
        xos_session, gate, verdict="PASS", explanation="canary gate cleared"
    )
    svc.transition_experiment(
        xos_session, exp, "PRODUCTION",
        actor="operator", approved_by="operator", gate_result=result2,
    )
    rows = read.transitions_for(xos_session, exp)
    assert [(t.from_state, t.to_state) for t in rows] == [
        (None, "IDEA"),
        ("IDEA", "PROBE"),
        ("PROBE", "PAPER"),
        ("PAPER", "LIVE_CANARY"),
        ("LIVE_CANARY", "PRODUCTION"),
    ]
    assert exp.state == "PRODUCTION"
    # The promotion rows carry their evidence and approval.
    assert rows[3].approved_by == "operator"
    assert rows[3].gate_result_id == result.id
    assert rows[4].gate_result_id == result2.id


def test_paper_entry_requires_a_frozen_version(xos_session, xos_platform):
    exp = _experiment(xos_session)
    svc.transition_experiment(xos_session, exp, "PROBE", actor="operator")
    with pytest.raises(svc.ExperimentOsError, match="frozen Experiment Version"):
        svc.transition_experiment(xos_session, exp, "PAPER", actor="operator")
    # A drafted-but-unfrozen version is not enough.
    ver = svc.create_experiment_version(xos_session, exp, hypothesis="h")
    svc.add_arm(xos_session, ver, arm_key="t", role="treatment")
    with pytest.raises(svc.ExperimentOsError, match="frozen Experiment Version"):
        svc.transition_experiment(xos_session, exp, "PAPER", actor="operator")


def test_live_canary_needs_pass_result_and_approval(xos_session, xos_platform):
    exp = _experiment(xos_session)
    ver = _frozen_version(xos_session, exp)
    gate = svc.register_gate(
        xos_session, ver, gate_key="paper_gate", kind="promotion",
        from_state="PAPER", to_state="LIVE_CANARY", spec=GATE_SPEC,
    )
    svc.transition_experiment(xos_session, exp, "PROBE", actor="operator")
    svc.transition_experiment(xos_session, exp, "PAPER", actor="operator")

    # No approval → refused by the state machine itself.
    with pytest.raises(OperatorApprovalRequired):
        svc.transition_experiment(xos_session, exp, "LIVE_CANARY", actor="operator")
    # Approval but no gate evidence → refused.
    with pytest.raises(svc.ExperimentOsError, match="PASS gate result"):
        svc.transition_experiment(
            xos_session, exp, "LIVE_CANARY", actor="operator", approved_by="operator"
        )
    # A FAIL verdict cannot be argued past.
    fail = svc.record_gate_result(xos_session, gate, verdict="FAIL")
    with pytest.raises(svc.ExperimentOsError, match="not PASS"):
        svc.transition_experiment(
            xos_session, exp, "LIVE_CANARY",
            actor="operator", approved_by="operator", gate_result=fail,
        )
    # A PASS result from a DIFFERENT experiment is refused.
    other = _experiment(xos_session, key="exp-other")
    other_ver = _frozen_version(xos_session, other)
    other_gate = svc.register_gate(
        xos_session, other_ver, gate_key="paper_gate", kind="promotion",
        from_state="PAPER", to_state="LIVE_CANARY", spec=GATE_SPEC,
    )
    foreign = svc.record_gate_result(xos_session, other_gate, verdict="PASS")
    with pytest.raises(svc.ExperimentOsError, match="different experiment"):
        svc.transition_experiment(
            xos_session, exp, "LIVE_CANARY",
            actor="operator", approved_by="operator", gate_result=foreign,
        )
    assert exp.state == "PAPER"  # nothing above moved it

    ok = svc.record_gate_result(xos_session, gate, verdict="PASS")
    svc.transition_experiment(
        xos_session, exp, "LIVE_CANARY",
        actor="operator", approved_by="operator", gate_result=ok,
    )
    assert exp.state == "LIVE_CANARY"


def test_illegal_transition_writes_nothing(xos_session, xos_platform):
    exp = _experiment(xos_session)
    before = len(read.transitions_for(xos_session, exp))
    with pytest.raises(IllegalTransition):
        svc.transition_experiment(xos_session, exp, "PAPER", actor="operator")  # skips PROBE
    assert exp.state == "IDEA"
    assert len(read.transitions_for(xos_session, exp)) == before


def test_pause_resume_bookkeeping(xos_session, xos_platform):
    exp = _experiment(xos_session)
    _frozen_version(xos_session, exp)
    svc.transition_experiment(xos_session, exp, "PROBE", actor="operator")
    svc.transition_experiment(xos_session, exp, "PAPER", actor="operator")
    svc.transition_experiment(
        xos_session, exp, "PAUSED", actor="system", reason="data health"
    )
    assert exp.state == "PAUSED"
    assert exp.paused_from_state == "PAPER"
    with pytest.raises(IllegalTransition, match="only to the state it paused from"):
        svc.transition_experiment(xos_session, exp, "PROBE", actor="operator")
    svc.transition_experiment(
        xos_session, exp, "PAPER", actor="operator", reason="collector repaired"
    )
    assert exp.state == "PAPER"
    assert exp.paused_from_state is None


def test_retired_is_terminal_and_succession_references_it(xos_session, xos_platform):
    exp = _experiment(xos_session)
    svc.transition_experiment(
        xos_session, exp, "RETIRED", actor="operator", occurred_at=_dt(2026, 8, 1)
    )
    assert exp.state == "RETIRED"
    assert exp.retired_at == _dt(2026, 8, 1)
    with pytest.raises(IllegalTransition, match="terminal"):
        svc.transition_experiment(xos_session, exp, "IDEA", actor="operator")
    with pytest.raises(svc.ExperimentOsError, match="successor"):
        svc.create_experiment_version(xos_session, exp)
    successor = svc.create_experiment(
        xos_session, key="exp-1-revival", origin="operator", predecessor=exp
    )
    assert successor.predecessor_experiment_id == exp.id


# ---------------------------------------------------------------------------
# Version vs epoch (spec §5.3 vs §5.5 — the distinction, tested)
# ---------------------------------------------------------------------------


def test_environment_change_is_a_new_epoch_same_version(xos_session, xos_platform):
    exp = _experiment(xos_session)
    ver = _frozen_version(xos_session, exp)
    e1 = svc.open_epoch(xos_session, ver, reason="initial", started_at=_dt(2026, 8, 1))
    assert (e1.epoch_number, e1.platform_snapshot_id) == (1, xos_platform.id)

    # Taxonomy expands (I2 sample boundary): same question, new world → new epoch.
    svc.register_platform_revision(
        xos_session, "MARKET_TAXONOMY", version="v2", activate=True,
        reason="46 series classified",
    )
    svc.close_epoch(xos_session, e1, ended_at=_dt(2026, 8, 13))
    e2 = svc.open_epoch(
        xos_session, ver, reason="taxonomy expansion", impact_class="I2",
        started_at=_dt(2026, 8, 13),
    )
    assert e2.epoch_number == 2
    assert e2.platform_snapshot_id != e1.platform_snapshot_id
    assert e2.impact_class == "I2"
    # Same version throughout: the scientific contract did not change.
    assert len(read.versions_for(xos_session, exp)) == 1


def test_scientific_change_is_a_new_version(xos_session, xos_platform):
    exp = _experiment(xos_session)
    v1 = _frozen_version(xos_session, exp)
    svc.open_epoch(xos_session, v1, reason="initial")

    # Changing the control definition is I3: a NEW version, never an epoch.
    with pytest.raises(svc.ExperimentOsError, match="change_reason"):
        svc.create_experiment_version(xos_session, exp)
    v2 = svc.create_experiment_version(
        xos_session, exp, hypothesis="h",
        independent_variable="the lever",
        change_reason="control redefined from open-window favorite to matched family",
    )
    assert v2.version == 2
    assert v2.predecessor_version_id == v1.id
    svc.add_arm(xos_session, v2, arm_key="treatment", role="treatment")
    svc.add_arm(xos_session, v2, arm_key="control", role="control")
    svc.freeze_version(xos_session, v2)
    # Epoch numbering is per-version: the new contract starts at epoch 1.
    e = svc.open_epoch(xos_session, v2, reason="initial for v2")
    assert e.epoch_number == 1
    # v1 remains queryable, immutable history.
    assert read.versions_for(xos_session, exp)[0].fingerprint == v1.fingerprint


def test_epoch_requires_frozen_version_and_single_open_interval(xos_session, xos_platform):
    exp = _experiment(xos_session)
    draft = svc.create_experiment_version(xos_session, exp, hypothesis="h")
    svc.add_arm(xos_session, draft, arm_key="t", role="treatment")
    svc.add_arm(xos_session, draft, arm_key="c", role="control")
    with pytest.raises(svc.ExperimentOsError, match="not frozen"):
        svc.open_epoch(xos_session, draft, reason="initial")
    svc.freeze_version(xos_session, draft)
    e1 = svc.open_epoch(xos_session, draft, reason="initial")
    with pytest.raises(svc.ExperimentOsError, match="still.*open|is still"):
        svc.open_epoch(xos_session, draft, reason="second")
    svc.close_epoch(xos_session, e1)
    svc.open_epoch(xos_session, draft, reason="after boundary")


def test_freeze_requires_a_control_or_exemption(xos_session, xos_platform):
    exp = _experiment(xos_session)
    ver = svc.create_experiment_version(xos_session, exp, hypothesis="h")
    svc.add_arm(xos_session, ver, arm_key="t", role="treatment")
    with pytest.raises(svc.ExperimentOsError, match="control"):
        svc.freeze_version(xos_session, ver)
    ver.control_exemption_reason = "single-arm calibration study; benchmark is the market"
    svc.freeze_version(xos_session, ver)
    assert ver.frozen_at is not None


# ---------------------------------------------------------------------------
# Immutability through the flush guard
# ---------------------------------------------------------------------------


def test_frozen_version_is_immutable(xos_session, xos_platform):
    exp = _experiment(xos_session)
    ver = _frozen_version(xos_session, exp)
    xos_session.commit()
    ver.entry_rule = "quietly widened band"
    with pytest.raises(svc.ImmutableRecord, match="frozen"):
        xos_session.flush()
    xos_session.rollback()
    with pytest.raises(svc.ImmutableRecord, match="frozen"):
        svc.add_arm(xos_session, ver, arm_key="late", role="secondary")


def test_gate_spec_is_immutable_from_registration(xos_session, xos_platform):
    exp = _experiment(xos_session)
    ver = _frozen_version(xos_session, exp)
    gate = svc.register_gate(
        xos_session, ver, gate_key="g", kind="kill", spec=GATE_SPEC
    )
    xos_session.commit()
    gate.spec_json = {**GATE_SPEC, "pass_all": []}
    with pytest.raises(svc.ImmutableRecord):
        xos_session.flush()
    xos_session.rollback()


def test_gate_edit_after_evidence_is_rejected(xos_session, xos_platform):
    """Spec §11.2: a pre-registered gate may not move its threshold once eligible
    observations begin — not even via fields that were mutable before."""
    exp = _experiment(xos_session)
    ver = _frozen_version(xos_session, exp)
    gate = svc.register_gate(xos_session, ver, gate_key="g", kind="kill", spec=GATE_SPEC)
    svc.mark_gate_evidence_started(xos_session, gate, at=_dt(2026, 8, 1))
    xos_session.commit()
    gate.evidence_started_at = None  # try to un-ring the bell
    with pytest.raises(svc.ImmutableRecord, match="accumulated evidence"):
        xos_session.flush()
    xos_session.rollback()
    xos_session.delete(xos_session.get(type(gate), gate.id))
    with pytest.raises(svc.ImmutableRecord, match="may not be deleted"):
        xos_session.flush()
    xos_session.rollback()
    with pytest.raises(svc.ExperimentOsError, match="already started"):
        svc.mark_gate_evidence_started(xos_session, gate)


def test_transitions_are_append_only(xos_session, xos_platform):
    exp = _experiment(xos_session)
    xos_session.commit()
    row = read.transitions_for(xos_session, exp)[0]
    row.reason = "rewritten history"
    with pytest.raises(svc.ImmutableRecord, match="append-only"):
        xos_session.flush()
    xos_session.rollback()
    xos_session.delete(
        xos_session.scalar(
            select(ExperimentStateTransition).where(
                ExperimentStateTransition.experiment_id == exp.id
            )
        )
    )
    with pytest.raises(svc.ImmutableRecord):
        xos_session.flush()
    xos_session.rollback()


def test_gate_results_are_immutable(xos_session, xos_platform):
    exp = _experiment(xos_session)
    ver = _frozen_version(xos_session, exp)
    gate = svc.register_gate(xos_session, ver, gate_key="g", kind="kill", spec=GATE_SPEC)
    res = svc.record_gate_result(xos_session, gate, verdict="FAIL")
    xos_session.commit()
    res.verdict = "PASS"
    with pytest.raises(svc.ImmutableRecord, match="append-only"):
        xos_session.flush()
    xos_session.rollback()


# ---------------------------------------------------------------------------
# Structured gate specs
# ---------------------------------------------------------------------------


def test_gate_spec_shape_is_validated(xos_session, xos_platform):
    exp = _experiment(xos_session)
    ver = _frozen_version(xos_session, exp)

    def reg(spec):
        return svc.register_gate(xos_session, ver, gate_key="g", kind="kill", spec=spec)

    with pytest.raises(svc.GateSpecError, match="mapping"):
        reg("freeze1 beats freeze3 by 3 cents")  # prose is not a gate
    with pytest.raises(svc.GateSpecError, match="decision list"):
        reg({"sample": {"t": {"metric": "n", "op": ">=", "value": 1}}})
    with pytest.raises(svc.GateSpecError, match="op"):
        reg({"pass_all": [{"metric": "pnl", "op": "beats", "value": 0}]})
    with pytest.raises(svc.GateSpecError, match="numeric"):
        reg({"pass_all": [{"metric": "pnl", "op": ">", "value": "positive"}]})
    with pytest.raises(svc.GateSpecError, match="metric"):
        reg({"pass_all": [{"op": ">", "value": 0}]})
    with pytest.raises(svc.GateSpecError, match="unknown"):
        reg({"pass_all": [{"metric": "pnl", "op": ">", "value": 0}], "vibe": "good"})


def test_gate_hash_is_deterministic(xos_session, xos_platform):
    exp = _experiment(xos_session)
    ver = _frozen_version(xos_session, exp)
    g = svc.register_gate(xos_session, ver, gate_key="g", kind="kill", spec=GATE_SPEC)
    assert g.spec_hash == svc.canonical_hash(GATE_SPEC)
    assert len(g.spec_hash) == 64


def test_promotion_gate_must_guard_a_legal_arrow(xos_session, xos_platform):
    exp = _experiment(xos_session)
    ver = _frozen_version(xos_session, exp)
    with pytest.raises(IllegalTransition, match="skips"):
        svc.register_gate(
            xos_session, ver, gate_key="g", kind="promotion",
            from_state="PAPER", to_state="PRODUCTION", spec=GATE_SPEC,
        )
    with pytest.raises(svc.ExperimentOsError, match="from_state and to_state"):
        svc.register_gate(xos_session, ver, gate_key="g2", kind="promotion", spec=GATE_SPEC)


# ---------------------------------------------------------------------------
# Deployments
# ---------------------------------------------------------------------------


def test_deployment_validation(xos_session, xos_platform):
    exp = _experiment(xos_session)
    ver = _frozen_version(xos_session, exp)
    epoch = svc.open_epoch(xos_session, ver, reason="initial")

    with pytest.raises(svc.ExperimentOsError, match="LIVE_CANARY or PRODUCTION"):
        svc.register_deployment(
            xos_session, epoch, deployment_key="d1", stage="PAPER", kind="live",
            arms={"treatment": "tag1"},
        )
    with pytest.raises(svc.ExperimentOsError, match="at least one arm"):
        svc.register_deployment(
            xos_session, epoch, deployment_key="d1", stage="PAPER", kind="paper", arms={}
        )
    with pytest.raises(svc.ExperimentOsError, match="not declared"):
        svc.register_deployment(
            xos_session, epoch, deployment_key="d1", stage="PAPER", kind="paper",
            arms={"mystery_arm": "tag1"},
        )
    with pytest.raises(svc.ExperimentOsError, match="reference its live deployment"):
        svc.register_deployment(
            xos_session, epoch, deployment_key="d1", stage="PAPER", kind="paper_twin",
            arms={"treatment": "tag1"},
        )
    paper = svc.register_deployment(
        xos_session, epoch, deployment_key="d-paper", stage="PAPER", kind="paper",
        arms={"treatment": "tag1", "control": "tag2"},
    )
    with pytest.raises(svc.ExperimentOsError, match="must be a LIVE deployment"):
        svc.register_deployment(
            xos_session, epoch, deployment_key="d-twin", stage="PAPER", kind="paper_twin",
            arms={"treatment": "tag1"}, twin_of=paper,
        )
    with pytest.raises(svc.ExperimentOsError, match="already exists"):
        svc.register_deployment(
            xos_session, epoch, deployment_key="d-paper", stage="PAPER", kind="paper",
            arms={"treatment": "t"},
        )


# ---------------------------------------------------------------------------
# Legacy import
# ---------------------------------------------------------------------------


def test_legacy_import_invents_nothing(xos_session):
    """No platform registry exists at all in this session — the import still works,
    because it records only what history supports (spec §22)."""
    exp = svc.import_legacy_experiment(
        xos_session,
        key="old-book",
        state="RETIRED",
        legacy_class="RETIRED_OR_KILLED",
        migration_integrity="C",
        verdict="killed at n=405",
        imported_at=_dt(2026, 8, 15),
    )
    assert exp.platform_snapshot_id is None
    assert exp.legacy_class == "RETIRED_OR_KILLED"
    assert exp.migration_integrity == "C"
    assert read.versions_for(xos_session, exp) == []
    # The unknown retirement date stays NULL — never defaulted to import time.
    assert exp.retired_at is None
    rows = read.transitions_for(xos_session, exp)
    assert [(r.from_state, r.to_state, r.actor) for r in rows] == [
        (None, "RETIRED", "import")
    ]
    assert rows[0].reason == "killed at n=405"
    # Terminal like any retired experiment.
    with pytest.raises(IllegalTransition, match="terminal"):
        svc.transition_experiment(xos_session, exp, "PAPER", actor="operator")


def test_legacy_import_validates_enums(xos_session):
    with pytest.raises(ValueError):
        svc.import_legacy_experiment(
            xos_session, key="x", state="RETIRED",
            legacy_class="MOSTLY_DEAD", migration_integrity="C",
        )
    with pytest.raises(ValueError):
        svc.import_legacy_experiment(
            xos_session, key="x", state="RETIRED",
            legacy_class="RETIRED_OR_KILLED", migration_integrity="E",
        )
    with pytest.raises(ValueError):
        svc.import_legacy_experiment(
            xos_session, key="x", state="DEAD",
            legacy_class="RETIRED_OR_KILLED", migration_integrity="C",
        )


def test_legacy_evidence_is_classed_and_append_only(xos_session):
    exp = svc.import_legacy_experiment(
        xos_session, key="old-book", state="RETIRED",
        legacy_class="RETIRED_OR_KILLED", migration_integrity="C",
    )
    with pytest.raises(ValueError):
        svc.attach_legacy_evidence(
            xos_session, exp, label="pnl", evidence_class="pretty_good_evidence"
        )
    ev = svc.attach_legacy_evidence(
        xos_session, exp, label="lifetime paper P&L", evidence_class="context_only",
        source="paper_trades.strategy='old-book'", summary={"n": 405},
    )
    xos_session.commit()
    ev.evidence_class = "certified_legacy_evidence"  # cannot upgrade in place
    with pytest.raises(svc.ImmutableRecord):
        xos_session.flush()
    xos_session.rollback()


def test_native_and_legacy_are_distinguishable(xos_session, xos_platform):
    _experiment(xos_session, key="native-1")
    svc.import_legacy_experiment(
        xos_session, key="legacy-1", state="PAPER",
        legacy_class="ACTIVE_PAPER", migration_integrity="B",
    )
    assert [e.key for e in read.list_experiments(xos_session, legacy=True)] == ["legacy-1"]
    assert [e.key for e in read.list_experiments(xos_session, legacy=False)] == ["native-1"]
    assert len(read.list_experiments(xos_session)) == 2
