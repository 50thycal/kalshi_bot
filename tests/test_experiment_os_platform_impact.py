"""Platform Change Impact Engine (PR 5) — proving shapes on real repo history and
the adversarial set.

Pinned here, so none of these can silently regress:

  * activation with an unclassified affected experiment → refused, by name;
  * a forced activation → durable override (system event + unresolved integrity
    events that keep the skipped experiments gate-blocked);
  * I2 evidence never pools across the boundary; the new epoch starts at the
    MEASURED boundary and pins the new snapshot exactly;
  * an I3 cannot masquerade as a new epoch; its application requires a frozen,
    researcher-authored successor version — never fabricated;
  * I1 without a NAMED registered normalizer → refused
    (normalization_available=True is not permission);
  * an unestablished activation boundary refuses NEW_EPOCH application and keeps
    the evaluator at BLOCKED_PLATFORM;
  * a pre-change PASS cannot promote after the platform moved — even under a
    licensed I1, only a FRESH post-boundary evaluation authorizes;
  * retired experiments never block activation; unaffected experiments get an
    explicit NO_ACTION (and stay evaluable, licensed);
  * unresolved dispositions are visible in readiness and block evidence; STRICT
    blocks the tags of impact-unresolved experiments;
  * classification records are immutable once accepted, terminal once applied.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from kalshi_bot import repository as repo
from kalshi_bot.experiment_os import enforcement as enf
from kalshi_bot.experiment_os import evaluator, read
from kalshi_bot.experiment_os import platform_impact as pi
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.models import (
    ExperimentGateResult,
    ExperimentIntegrityEvent,
)
from kalshi_bot.models import PaperTrade, SystemEvent

UTC = timezone.utc
T0 = datetime(2026, 8, 1, tzinfo=UTC)
FEE_BOUNDARY = datetime(2026, 8, 11, 15, 0, tzinfo=UTC)       # measured (PR 2)
TAX_BOUNDARY = datetime(2026, 8, 13, 18, 9, 40, tzinfo=UTC)   # measured (PR 2)


def _dt(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _fresh_resolver():
    enf.reset_for_tests()
    yield
    enf.reset_for_tests()


def _trade(s, tag, *, pnl=0.05, status="settled", at=None):
    s.add(PaperTrade(
        market_ticker=f"T-{tag}-{id(object())}", strategy=tag, status=status,
        pnl=pnl, quantity=1, created_at=at or (T0 + timedelta(hours=1)),
    ))


def _experiment(s, key, tag=None):
    """PAPER experiment: frozen version, treatment+control arms with tags, open
    epoch, paper deployment. Returns (exp, ver, epoch)."""
    tag = tag or f"{key}_tag"
    exp = svc.create_experiment(s, key=key, origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="lever", now=T0
    )
    svc.add_arm(s, ver, arm_key="treatment", role="treatment", strategy_tag=tag)
    svc.add_arm(s, ver, arm_key="control", role="control", strategy_tag=f"{tag}_c")
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    svc.register_deployment(
        s, epoch, deployment_key=f"{key}-paper-1", stage="PAPER", kind="paper",
        arms={"treatment": tag, "control": f"{tag}_c"}, started_at=T0,
    )
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    return exp, ver, epoch


def _gate(s, ver, *, key="g"):
    gate = svc.register_gate(
        s, ver, gate_key=key, kind="promotion", from_state="PAPER",
        to_state="LIVE_CANARY", registered_at=T0,
        spec={"pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                            "op": ">", "value": 0}]},
    )
    svc.mark_gate_evidence_started(s, gate, at=T0)
    return gate


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discovery_categorizes_from_pinned_snapshots(xos_session, xos_platform):
    s = xos_session
    _experiment(s, "active-a")
    retired_exp, _, _ = _experiment(s, "old-one")
    svc.transition_experiment(s, retired_exp, "RETIRED", actor="operator",
                              reason="killed earlier")
    idea = svc.create_experiment(s, key="just-an-idea", origin="operator")
    rev = svc.register_platform_revision(s, "FEE_MODEL", version="fee-v2")
    affected = pi.affected_experiments(s, rev)
    assert [e["key"] for e in affected["active_on_superseded"]] == ["active-a"]
    assert [e["key"] for e in affected["retired"]] == ["old-one"]
    assert [e["key"] for e in affected["no_pinned_epoch"]] == ["just-an-idea"]
    assert affected["already_on_new"] == []
    assert idea.state == "IDEA"


# ---------------------------------------------------------------------------
# Activation gating
# ---------------------------------------------------------------------------


def test_activation_refused_while_affected_experiment_unclassified(
    xos_session, xos_platform
):
    s = xos_session
    exp, _, _ = _experiment(s, "exp-a")
    rev = svc.register_platform_revision(s, "FEE_MODEL", version="fee-v2")
    with pytest.raises(svc.ExperimentOsError, match="exp-a"):
        svc.activate_platform_revision(s, rev, activated_at=FEE_BOUNDARY)
    # A PROPOSED record is not accounting — acceptance is the review.
    pi.propose_impact(
        s, rev, exp, impact_class="I1", action="RECOMPUTE",
        rationale="exact per-row re-fee", decided_by="claude",
        normalization_ref="metric:pnl_cents_per_trade",
    )
    with pytest.raises(svc.ExperimentOsError, match="exp-a"):
        svc.activate_platform_revision(s, rev, activated_at=FEE_BOUNDARY)
    assert rev.status == "pending"  # nothing half-activated


def test_forced_activation_is_a_durable_override(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch = _experiment(s, "exp-forced")
    gate = _gate(s, ver)
    _trade(s, "exp-forced_tag")
    s.commit()
    rev = svc.register_platform_revision(s, "FEE_MODEL", version="fee-v2")
    with pytest.raises(svc.ExperimentOsError, match="force_reason"):
        svc.activate_platform_revision(s, rev, activated_at=FEE_BOUNDARY, force=True)
    svc.activate_platform_revision(
        s, rev, activated_at=FEE_BOUNDARY, force=True,
        force_reason="hotfix deployed before review", actor="operator",
    )
    s.commit()
    assert rev.status == "active"
    override = s.scalar(select(SystemEvent).where(
        SystemEvent.component == "experiment_os_platform",
        SystemEvent.message.like("%FORCED%"),
    ))
    assert override is not None and "exp-forced" not in (override.message or "")[:0]
    ev = s.scalar(select(ExperimentIntegrityEvent).where(
        ExperimentIntegrityEvent.kind == "PLATFORM_ACTIVATION_FORCED",
        ExperimentIntegrityEvent.experiment_id == exp.id,
    ))
    assert ev is not None and ev.resolved_at is None
    # The skipped experiment is blocked (stale platform fires first; the forced
    # integrity event backs it up) until someone actually classifies the impact.
    out = evaluator.evaluate_gate(s, gate, persist=False)
    assert out.verdict.startswith("BLOCKED")
    rdy = enf.production_readiness(s)
    assert rdy["checks"]["no_unresolved_integrity"]["ok"] is False


def test_retired_experiments_never_block_activation(xos_session, xos_platform):
    s = xos_session
    exp, _, _ = _experiment(s, "was-live-once")
    svc.transition_experiment(s, exp, "RETIRED", actor="operator", reason="killed")
    rev = svc.register_platform_revision(s, "FEE_MODEL", version="fee-v2")
    gate = pi.activation_gate(s, rev)
    assert gate["safe"] is True
    assert gate["retired_ignored"] == ["was-live-once"]
    svc.activate_platform_revision(s, rev, activated_at=FEE_BOUNDARY)
    assert rev.status == "active"


# ---------------------------------------------------------------------------
# Proving shape 1 — the maker-fee correction (FEE_MODEL, I1, named normalizer)
# ---------------------------------------------------------------------------


def test_fee_correction_i1_shape(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch = _experiment(s, "theta-like")
    gate = _gate(s, ver)
    for _ in range(3):
        _trade(s, "theta-like_tag", pnl=0.05)
    s.commit()
    assert evaluator.evaluate_gate(s, gate, persist=False).verdict == "PASS"
    # The PASS recorded BEFORE the fee boundary, under the old fee semantics.
    old_result = svc.record_gate_result(
        s, gate, verdict="PASS", computed_by="system", epoch=epoch,
        computed_at=_dt(2026, 8, 10),
    )

    rev = svc.register_platform_revision(
        s, "FEE_MODEL", version="fee-2026-08-11",
        reason="maker fee re-baseline — every historical row can be re-feed exactly",
        normalization_available=True,
    )
    # normalization_available=True is NOT permission: the normalizer must be named
    # and must exist.
    with pytest.raises(svc.ExperimentOsError, match="normalization_ref"):
        pi.propose_impact(s, rev, exp, impact_class="I1", action="RECOMPUTE",
                          rationale="exact re-fee", decided_by="claude")
    with pytest.raises(svc.ExperimentOsError, match="not in the canonical metric"):
        pi.propose_impact(s, rev, exp, impact_class="I1", action="RECOMPUTE",
                          rationale="exact re-fee", decided_by="claude",
                          normalization_ref="metric:no_such_metric")
    record = pi.propose_impact(
        s, rev, exp, impact_class="I1", action="RECOMPUTE",
        rationale="fee model corrected; per-row re-fee is exact on recorded fields",
        decided_by="claude", normalization_ref="metric:pnl_cents_per_trade",
    )
    pi.accept_impact(s, record, accepted_by="operator")
    s.commit()
    # Accepted-but-unapplied RECOMPUTE blocks evidence — and shows in readiness.
    assert enf.production_readiness(s)["checks"]["no_unresolved_platform_impact"]["ok"] is False
    svc.activate_platform_revision(s, rev, activated_at=FEE_BOUNDARY)
    mid = evaluator.evaluate_gate(s, gate, persist=False)
    assert mid.verdict == "BLOCKED_PLATFORM"
    assert any("not APPLIED" in r for r in mid.blocking_reasons)

    pi.apply_recompute(s, record, actor="operator")
    s.commit()
    assert record.status == "applied"
    # Re-evaluation, not sample reset: the SAME epoch stays interpretable under
    # the named normalizer...
    fresh = evaluator.evaluate_gate(s, gate)
    assert fresh.verdict == "PASS"
    assert fresh.clauses[0].n == 3  # history retained, not reset
    # ...but the PRE-change PASS is not reusable for money:
    with pytest.raises(svc.ExperimentOsError, match="predates the licensed"):
        svc.transition_experiment(
            s, exp, "LIVE_CANARY", actor="operator", approved_by="operator",
            gate_result=old_result,
        )
    fresh_result = s.get(ExperimentGateResult, fresh.result_id)
    svc.transition_experiment(
        s, exp, "LIVE_CANARY", actor="operator", approved_by="operator",
        gate_result=fresh_result,
    )
    assert exp.state == "LIVE_CANARY"


# ---------------------------------------------------------------------------
# Proving shape 2 — the taxonomy expansion (MARKET_TAXONOMY, I2 + NO_ACTION)
# ---------------------------------------------------------------------------


def test_taxonomy_expansion_i2_shape(xos_session, xos_platform):
    s = xos_session
    mm, mm_ver, mm_epoch = _experiment(s, "tmmsell-like", tag="mm_tag")
    mm_gate = _gate(s, mm_ver, key="mm_gate")
    weather, w_ver, w_epoch = _experiment(s, "weather-like", tag="w_tag")
    w_gate = _gate(s, w_ver, key="w_gate")
    for _ in range(2):
        _trade(s, "mm_tag", at=T0 + timedelta(days=2))   # pre-boundary
        _trade(s, "w_tag", at=T0 + timedelta(days=2))
    s.commit()

    rev = svc.register_platform_revision(
        s, "MARKET_TAXONOMY", version="tax-2026-08-13",
        reason="46 series classified into the sellable universe",
    )
    mm_rec = pi.propose_impact(
        s, rev, mm, impact_class="I2", action="NEW_EPOCH",
        rationale="candidate population changes — same question, new world",
        decided_by="claude",
    )
    w_rec = pi.propose_impact(
        s, rev, weather, impact_class="I0", action="NO_ACTION",
        rationale="weather books scan KXHIGH*/KXLOW* only; the taxonomy expansion "
                  "does not alter their candidate set",
        decided_by="claude",
    )
    pi.accept_impact(s, mm_rec, accepted_by="operator")
    pi.accept_impact(s, w_rec, accepted_by="operator")   # NO_ACTION settles here
    assert w_rec.status == "applied"
    svc.activate_platform_revision(s, rev, activated_at=TAX_BOUNDARY)
    s.commit()

    new_epoch = pi.apply_new_epoch(s, mm_rec, actor="operator")
    s.commit()
    old_epoch = s.get(type(new_epoch), mm_epoch.id)
    assert old_epoch.ended_at is not None                      # closed at boundary
    assert new_epoch.started_at.replace(tzinfo=UTC) == TAX_BOUNDARY
    assert new_epoch.impact_class == "I2"
    assert mm_rec.resulting_epoch_id == new_epoch.id           # forward lineage
    # The new snapshot pins EXACTLY the old set with only the taxonomy swapped.
    old_snap = read.snapshot_contents(s, s.get(
        type(xos_platform), old_epoch.platform_snapshot_id))
    new_snap = read.snapshot_contents(s, s.get(
        type(xos_platform), new_epoch.platform_snapshot_id))
    changed = set(new_snap) - set(old_snap)
    assert changed == {("MARKET_TAXONOMY", "tax-2026-08-13")}
    assert (set(old_snap) - set(new_snap)) == {("MARKET_TAXONOMY", "v1")}

    # Pre-boundary evidence stays OUT of the new epoch.
    svc.register_deployment(
        s, new_epoch, deployment_key="tmmsell-like-paper-2", stage="PAPER",
        kind="paper", arms={"treatment": "mm_tag", "control": "mm_tag_c"},
        started_at=TAX_BOUNDARY,
    )
    for _ in range(4):
        _trade(s, "mm_tag", pnl=0.07, at=TAX_BOUNDARY + timedelta(hours=3))
    s.commit()
    out = evaluator.evaluate_gate(s, mm_gate, epoch=new_epoch, persist=False)
    assert out.verdict == "PASS"
    assert out.clauses[0].n == 4  # the 2 pre-boundary trades are the OLD epoch's

    # The unaffected experiment keeps evaluating on its ORIGINAL epoch — the
    # applied NO_ACTION licenses the pinned-vs-active difference explicitly.
    w_out = evaluator.evaluate_gate(s, w_gate, persist=False)
    assert w_out.verdict == "PASS"
    # And with everything applied, the readiness surface is clean again.
    assert enf.production_readiness(s)["checks"]["no_unresolved_platform_impact"]["ok"]


# ---------------------------------------------------------------------------
# Proving shape 3 — a FILL_MODEL semantic change fixture (I3 discipline)
# ---------------------------------------------------------------------------


def test_i3_cannot_masquerade_as_a_new_epoch(xos_session, xos_platform):
    """FILL_MODEL fixture: a change that redefines what 'a fill' means for the
    experiment's thesis is I3 — the depth-proxy queue model measured and REJECTED
    in PR #218 is deliberately NOT canonized here; this revision is a fixture."""
    s = xos_session
    exp, ver, epoch = _experiment(s, "fill-thesis")
    rev = svc.register_platform_revision(
        s, "FILL_MODEL", version="fill-fixture-v2",
        reason="FIXTURE: fill semantics redefined (not the rejected depth proxy)",
    )
    with pytest.raises(svc.ExperimentOsError, match="masquerade"):
        pi.propose_impact(s, rev, exp, impact_class="I3", action="NEW_EPOCH",
                          rationale="thesis is ABOUT fills", decided_by="claude")
    record = pi.propose_impact(
        s, rev, exp, impact_class="I3", action="NEW_EXPERIMENT_VERSION",
        rationale="the fill claim under test is redefined — a new contract",
        decided_by="claude",
    )
    pi.accept_impact(s, record, accepted_by="operator")
    svc.activate_platform_revision(s, rev, activated_at=_dt(2026, 8, 14))
    s.commit()

    # The engine never fabricates the successor contract.
    draft = svc.create_experiment_version(
        s, exp, hypothesis="fills under the new semantics",
        independent_variable="lever",
        change_reason="fill semantics redefined by FILL_MODEL fill-fixture-v2",
    )
    with pytest.raises(svc.ExperimentOsError, match="FROZEN"):
        pi.apply_new_version(s, record, successor=draft, actor="operator")
    svc.add_arm(s, draft, arm_key="treatment", role="treatment",
                strategy_tag="fill2_tag")
    svc.add_arm(s, draft, arm_key="control", role="control",
                strategy_tag="fill2_tag_c")
    svc.freeze_version(s, draft, now=_dt(2026, 8, 14, 1))
    pi.apply_new_version(s, record, successor=draft, actor="operator")
    assert record.status == "applied"
    assert record.resulting_version_id == draft.id


# ---------------------------------------------------------------------------
# Proving shape 4 — safety change (EXECUTION_ENGINE, I4: protect FIRST)
# ---------------------------------------------------------------------------


def test_i4_safety_change_requires_protection_before_activation(
    xos_session, xos_platform
):
    s = xos_session
    exp, ver, epoch = _experiment(s, "live-book")
    rev = svc.register_platform_revision(
        s, "EXECUTION_ENGINE", version="exec-v2", safety_class="safety_critical",
        reason="order-path change alters kill-switch semantics",
    )
    record = pi.propose_impact(
        s, rev, exp, impact_class="I4", action="PAUSE",
        rationale="continued accumulation unsafe until the kill path is re-verified",
        decided_by="claude",
    )
    assert record.blocks_activation is True  # I4 default
    pi.accept_impact(s, record, accepted_by="operator")
    # Accepted is NOT enough for a safety blocker — the protection must be APPLIED.
    with pytest.raises(svc.ExperimentOsError, match="blocking dispositions"):
        svc.activate_platform_revision(s, rev, activated_at=_dt(2026, 8, 14))
    pi.apply_pause(s, record, actor="operator")
    assert exp.state == "PAUSED"
    assert exp.paused_from_state == "PAPER"
    svc.activate_platform_revision(s, rev, activated_at=_dt(2026, 8, 14))
    assert rev.status == "active"
    assert record.details_json["paused_from"] == "PAPER"


# ---------------------------------------------------------------------------
# Boundary discipline
# ---------------------------------------------------------------------------


def test_unknown_boundary_blocks_new_epoch_until_established(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch = _experiment(s, "boundary-exp")
    gate = _gate(s, ver)
    _trade(s, "boundary-exp_tag")
    s.commit()
    rev = svc.register_platform_revision(s, "DATA_PROVENANCE", version="dp-v2")
    record = pi.propose_impact(
        s, rev, exp, impact_class="I2", action="NEW_EPOCH",
        rationale="provenance change alters the candidate labeling",
        decided_by="claude",
    )
    pi.accept_impact(s, record, accepted_by="operator")
    svc.activate_platform_revision(s, rev, boundary_unknown=True)
    s.commit()
    # Unknown means unknown: the epoch split cannot happen at a made-up instant,
    # and the evaluator keeps the evidence blocked meanwhile.
    with pytest.raises(svc.ExperimentOsError, match="UNESTABLISHED"):
        pi.apply_new_epoch(s, record, actor="operator")
    out = evaluator.evaluate_gate(s, gate, persist=False)
    assert out.verdict == "BLOCKED_PLATFORM"
    svc.establish_activation_boundary(s, rev, activated_at=_dt(2026, 8, 14, 12))
    new_epoch = pi.apply_new_epoch(s, record, actor="operator")
    assert new_epoch.started_at.replace(tzinfo=UTC) == _dt(2026, 8, 14, 12)


def test_explicit_boundary_must_match_measured(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch = _experiment(s, "boundary-exp2")
    rev = svc.register_platform_revision(s, "RISK_ENGINE", version="risk-v2")
    record = pi.propose_impact(
        s, rev, exp, impact_class="I2", action="NEW_EPOCH",
        rationale="sizing floor changes the sample", decided_by="claude",
    )
    pi.accept_impact(s, record, accepted_by="operator")
    svc.activate_platform_revision(s, rev, activated_at=_dt(2026, 8, 14))
    with pytest.raises(svc.ExperimentOsError, match="contradicts"):
        pi.apply_new_epoch(s, record, actor="operator", boundary=_dt(2026, 8, 13))


# ---------------------------------------------------------------------------
# Enforcement + ledger integrity
# ---------------------------------------------------------------------------


def test_strict_blocks_tags_of_impact_unresolved_experiments(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch = _experiment(s, "unresolved-exp", tag="ur_tag")
    rev = svc.register_platform_revision(s, "FEE_MODEL", version="fee-v2")
    record = pi.propose_impact(
        s, rev, exp, impact_class="I2", action="NEW_EPOCH",
        rationale="fee change alters the P&L sample", decided_by="claude",
    )
    pi.accept_impact(s, record, accepted_by="operator")
    s.commit()
    enf.record_enforcement_change(
        s, mode="NEW_ONLY", actor="operator", reason="test", cutover_id="pi-new-only",
        readiness={"ok": True, "checks": {}},
    )
    enf.refresh(s)
    # NEW_ONLY: continuity — the known tag keeps trading (evidence is blocked by
    # the evaluator, not the entry path).
    t = repo.create_paper_trade(
        s, signal_id=None, ticker="T-1", strategy="ur_tag", side="no", action="buy",
        assumed_price=93, quantity=1, fill_assumption="test", entry_fee=0.0,
    )
    assert t.experiment_deployment_arm_id is not None
    # STRICT: integrity outranks continuity.
    enf.record_enforcement_change(
        s, mode="STRICT", actor="operator", reason="test", cutover_id="pi-strict",
        readiness={"ok": True, "checks": {}},
    )
    enf.refresh(s)
    with pytest.raises(enf.LineageBlocked, match="platform-impact"):
        repo.create_paper_trade(
            s, signal_id=None, ticker="T-2", strategy="ur_tag", side="no",
            action="buy", assumed_price=93, quantity=1, fill_assumption="test",
            entry_fee=0.0,
        )


def test_impact_records_freeze_at_acceptance_and_settle_terminally(
    xos_session, xos_platform
):
    s = xos_session
    exp, _, _ = _experiment(s, "immutable-exp")
    rev = svc.register_platform_revision(s, "FEE_MODEL", version="fee-v2")
    record = pi.propose_impact(
        s, rev, exp, impact_class="I2", action="NEW_EPOCH",
        rationale="r", decided_by="claude",
    )
    record.rationale = "refined while proposed"  # a proposal may be edited
    s.flush()
    pi.accept_impact(s, record, accepted_by="operator")
    s.commit()
    record.impact_class = "I0"  # rewriting an accepted classification
    with pytest.raises(svc.ImmutableRecord, match="classification is frozen"):
        s.flush()
    s.rollback()
    with pytest.raises(svc.ImmutableRecord, match="may not be deleted"):
        s.delete(record)
        s.flush()
    s.rollback()
    pi.exempt_impact(s, record, actor="operator", reason="superseded by fee-v3 plan")
    s.commit()
    record.status = "accepted"  # a settled disposition is history
    with pytest.raises(svc.ImmutableRecord, match="settled disposition"):
        s.flush()
    s.rollback()


def test_duplicate_and_retired_proposals_refused(xos_session, xos_platform):
    s = xos_session
    exp, _, _ = _experiment(s, "dup-exp")
    rev = svc.register_platform_revision(s, "FEE_MODEL", version="fee-v2")
    pi.propose_impact(s, rev, exp, impact_class="I0", action="NO_ACTION",
                      rationale="not fee-sensitive", decided_by="claude")
    with pytest.raises(svc.ExperimentOsError, match="already has an impact record"):
        pi.propose_impact(s, rev, exp, impact_class="I2", action="NEW_EPOCH",
                          rationale="changed my mind", decided_by="claude")
    gone, _, _ = _experiment(s, "gone-exp")
    svc.transition_experiment(s, gone, "RETIRED", actor="operator", reason="dead")
    with pytest.raises(svc.ExperimentOsError, match="RETIRED"):
        pi.propose_impact(s, rev, gone, impact_class="I0", action="NO_ACTION",
                          rationale="r", decided_by="claude")


def test_classify_drift_into_the_platform_workflow(xos_session, xos_platform):
    s = xos_session
    exp, _, _ = _experiment(s, "drift-exp")
    ev = svc.record_integrity_event(
        s, exp, kind="EXPERIMENT_CONFIG_DRIFT",
        description="runtime band differs from registered deployment",
    )
    other = svc.record_integrity_event(
        s, exp, kind="HELD_CONSTANT_CHANGED", description="not a drift event",
    )
    rev = svc.register_platform_revision(
        s, "EXECUTION_ENGINE", version="exec-band-v2",
        reason="the drift was a deliberate band change — a platform-level semantic",
    )
    with pytest.raises(svc.ExperimentOsError, match="EXPERIMENT_CONFIG_DRIFT"):
        pi.classify_drift(s, other, revision=rev, actor="operator")
    pi.classify_drift(s, ev, revision=rev, actor="operator", note="band widened")
    assert ev.resolved_at is not None
    assert "exec-band-v2" in ev.resolution and "operator" in ev.resolution


# ---------------------------------------------------------------------------
# The review surface
# ---------------------------------------------------------------------------


def test_revision_review_is_one_canonical_read(xos_session, xos_platform):
    s = xos_session
    exp, _, _ = _experiment(s, "review-exp")
    rev = svc.register_platform_revision(
        s, "FEE_MODEL", version="fee-v2", reason="re-baseline", pr_ref="PR #226",
    )
    record = pi.propose_impact(
        s, rev, exp, impact_class="I1", action="RECOMPUTE",
        rationale="exact re-fee", decided_by="claude",
        normalization_ref="metric:pnl_cents_per_trade",
    )
    review = pi.revision_review(s, rev)
    assert review["component"] == "FEE_MODEL"
    assert review["revision"]["version"] == "fee-v2"
    assert review["revision"]["boundary_established"] is False
    assert review["supersedes"]["version"] == "v1"
    assert review["affected"]["active_on_superseded"][0]["key"] == "review-exp"
    assert review["impact_records"][0]["status"] == "proposed"
    assert review["activation"]["safe"] is False
    assert review["activation"]["unaccounted"] == ["review-exp"]

    assert pi.get_revision(s, "FEE_MODEL:fee-v2").id == rev.id
    assert pi.get_revision(s, str(rev.id)).id == rev.id
    assert pi.get_revision(s, "NOPE:v9") is None

    pending = pi.pending_revisions(s)
    assert pending and pending[0]["activation_safe"] is False
    open_rows = pi.unresolved_impacts(s)
    assert open_rows[0]["experiment"] == "review-exp"

    pi.accept_impact(s, record, accepted_by="operator")
    review2 = pi.revision_review(s, rev)
    assert review2["activation"]["safe"] is True
