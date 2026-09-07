"""Acceptance evidence for `kalshi_bot/experiment_os/queue_aware_cancel.py`.

Every property here is proved against the real service under NEW_ONLY enforcement:
the contract registers frozen at PROBE with a shadow probe deployment, the gates
resolve against the registered arms and the canonical metric registry, the running
rule equals the registered one, the shadow metrics compute from real decision rows
through the canonical evaluator, and — the part NEW_ONLY exists for — a tag this
package did not register is refused at the write path, and the package cannot reach
real money at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from kalshi_bot import models as m
from kalshi_bot import repository as repo
from kalshi_bot.experiment_os import enforcement as enf
from kalshi_bot.experiment_os import evaluator, metrics, read
from kalshi_bot.experiment_os import experiment_commands as ec
from kalshi_bot.experiment_os import queue_aware_cancel as pkg
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.lifecycle import ArmRole, DeploymentKind, LifecycleState
from kalshi_bot.live import queue_cancel as qc


@pytest.fixture(autouse=True)
def _fresh_resolver():
    enf.reset_for_tests()
    yield
    enf.reset_for_tests()


@pytest.fixture
def registered(xos_session, xos_platform):
    enf.record_enforcement_change(
        xos_session, mode="NEW_ONLY", actor="operator", reason="test",
        cutover_id="test-new-only", readiness={"ok": True, "checks": {}},
    )
    out = pkg.register(xos_session, actor="task-specific")
    xos_session.commit()
    enf.refresh(xos_session)
    return out


def _version(session):
    exp = read.get_experiment(session, pkg.EXPERIMENT_KEY)
    return exp, read.latest_version(session, exp)


# ===========================================================================
# 1. The contract: frozen, at PROBE, two arms, two gates, one shadow probe
# ===========================================================================


def test_registration_lands_at_probe_with_a_frozen_v1_and_a_shadow_probe(registered, xos_session):
    exp, ver = _version(xos_session)
    assert exp.state == LifecycleState.PROBE.value
    assert ver.version == 1 and ver.frozen_at is not None
    assert ver.pre_registration_hash
    epoch = read.open_epoch_for(xos_session, ver)
    assert epoch is not None and epoch.epoch_number == 1
    (dep,) = read.deployments_for(xos_session, epoch)
    assert dep.kind == DeploymentKind.PROBE.value
    assert dep.deployment_key == pkg.SHADOW_DEPLOYMENT_KEY
    arms = {a.arm_key: tag for a, tag in read.deployment_arms(xos_session, dep)}
    assert arms == {pkg.ARM_CONTROL: None, pkg.ARM_TREATMENT: pkg.SHADOW_TAG}
    assert dep.config_json["material"]["rule"] == pkg.FROZEN_RULE_INPUTS


def test_registering_twice_is_refused(registered, xos_session):
    with pytest.raises(svc.ExperimentOsError, match="already exists"):
        pkg.register(xos_session, actor="task-specific")


def test_the_two_arms_are_a_real_control_and_one_treatment(registered, xos_session):
    _exp, ver = _version(xos_session)
    arms = {a.arm_key: a for a in read.arms_for(xos_session, ver)}
    assert arms[pkg.ARM_CONTROL].role == ArmRole.CONTROL.value
    assert arms[pkg.ARM_TREATMENT].role == ArmRole.TREATMENT.value
    assert arms[pkg.ARM_CONTROL].params_json["queue_cancel"] is False
    assert arms[pkg.ARM_TREATMENT].params_json["rule_version"] == qc.FROZEN_RULE.rule_version
    assert ver.control_required is True


def test_the_registered_rule_is_the_running_rule(settings):
    """Three copies of the threshold exist by necessity (module constant, Settings default,
    registered contract); this pins them to one value so a re-tune cannot be quiet."""
    assert pkg.FROZEN_RULE_INPUTS == qc.FROZEN_RULE.inputs()
    assert qc.rule_from_settings(settings).inputs() == pkg.FROZEN_RULE_INPUTS
    assert pkg.RISK_ENVELOPE["rule"] == pkg.FROZEN_RULE_INPUTS


def test_gates_are_pre_registered_and_every_metric_is_canonical(registered, xos_session):
    _exp, ver = _version(xos_session)
    gates = {g.gate_key: g for g in read.gates_for(xos_session, ver)}
    assert set(gates) == {pkg.PROMOTION_GATE_KEY, pkg.KILL_GATE_KEY}
    promo = gates[pkg.PROMOTION_GATE_KEY]
    assert promo.kind == "promotion"
    assert promo.from_state == "PROBE" and promo.to_state == "PAPER"
    assert promo.evidence_started_at is not None and promo.spec_hash
    for spec in (pkg.PROMOTION_GATE_SPEC, pkg.KILL_GATE_SPEC):
        for key in ("pass_all", "fail_any"):
            for clause in spec.get(key) or []:
                assert metrics.resolve_definition(clause["metric"]) is not None, clause
                assert metrics.REGISTRY[clause["metric"]].provided, clause["metric"]
                assert clause["deployment_kind"] == "probe"
        for clause in (spec.get("sample") or {}).values():
            assert metrics.resolve_definition(clause["metric"]) is not None
    assert evaluator.validate_gate_scopes(xos_session, ver, pkg.PROMOTION_GATE_SPEC) == []
    assert evaluator.validate_gate_scopes(xos_session, ver, pkg.KILL_GATE_SPEC) == []


def test_a_weaker_sample_floor_is_refused_and_a_stricter_one_is_recorded(xos_session, xos_platform):
    with pytest.raises(svc.ExperimentOsError, match="stricter, never weaker"):
        pkg.register(xos_session, actor="x", promotion_sample_floor=10)
    out = pkg.register(xos_session, actor="x", promotion_sample_floor=80)
    spec = out["promotion_gate"].spec_json
    assert spec["sample"][pkg.ARM_TREATMENT]["value"] == 80
    assert "Floored by the registering envelope at 80" in spec["description"]


# ===========================================================================
# 2. NEW_ONLY: what the package admits and what it refuses
# ===========================================================================


def test_an_unregistered_tag_is_still_refused_at_the_write_path(registered, xos_session):
    """Registering this experiment does not loosen admission for anyone else."""
    with pytest.raises(enf.LineageBlocked, match="not registered"):
        repo.create_live_order(
            xos_session, signal_id=None, ticker="KXT-1", event_ticker="KXT",
            strategy="rogue_queue_book", side="no", action="buy", limit_price=93,
            quantity=1, status="pending", client_order_id="c-1",
        )
    assert xos_session.scalar(
        select(m.LiveOrder).where(m.LiveOrder.strategy == "rogue_queue_book")) is None


def test_the_package_cannot_arm_and_activates_nothing():
    """No `arm` function, no activation variables: ARM_CANARY aimed here has nothing to
    call, and the CI allowlist test treats it as a package that arms nothing."""
    p = ec._packages()["mmsell10-queue-aware-cancel"]
    assert p.arm is None and not p.activation_vars
    assert p.experiment_key == pkg.EXPERIMENT_KEY
    assert "mmsell10-queue-aware-cancel" in ec.package_names()


def test_register_through_the_transport_produces_a_receipt(xos_session, xos_platform):
    enf.record_enforcement_change(
        xos_session, mode="NEW_ONLY", actor="operator", reason="test",
        cutover_id="test-new-only", readiness={"ok": True, "checks": {}},
    )
    receipt = ec.execute_envelope(xos_session, {
        "schema_version": 1, "command_id": "qac-register-test-1",
        "action": "REGISTER_PACKAGE", "actor": "task-specific",
        "actor_role": "TASK_SPECIFIC",
        "payload": {"package": "mmsell10-queue-aware-cancel"},
    })
    assert receipt["status"] == ec.CommandStatus.SUCCEEDED
    res = receipt["result"]
    assert res["version"] == 1 and res["probe_deployment"] == pkg.SHADOW_DEPLOYMENT_KEY
    assert res["promotion_gate"] == pkg.PROMOTION_GATE_KEY


def test_active_lineage_names_the_probe_arm_and_no_live_arm(registered, xos_session):
    lineage = pkg.active_lineage(xos_session)
    assert set(lineage) == {"probe"}
    probe = lineage["probe"]
    assert probe["deployment_key"] == pkg.SHADOW_DEPLOYMENT_KEY
    assert probe["by_tag"] == {pkg.SHADOW_TAG: probe["arm_link_id"]}
    assert "live" not in lineage, "live cancellation has no lineage to stamp against"


def test_active_lineage_is_empty_when_unregistered(xos_session, xos_platform):
    assert pkg.active_lineage(xos_session) == {}


# ===========================================================================
# 3. The shadow metrics, computed through the canonical evaluator
# ===========================================================================


def _seed_shadow(session, arm_link_id, *, at):
    """Three would-cancel orders (one later filled and settled +7c, one later filled and
    unsettled, one timed out), one kept order, and one row with missing telemetry."""
    def order(koid, status, strategy="Dmmsell10"):
        row = m.LiveOrder(
            market_ticker=f"KXT-{koid}", event_ticker="KXT", strategy=strategy, side="no",
            action="buy", limit_price=93, quantity=1, status=status, kalshi_order_id=koid,
            client_order_id=f"c-{koid}", created_at=at - timedelta(hours=2),
        )
        session.add(row)
        session.flush()
        return row

    def decision(row, code, *, when, tel="observed", cap_bound=True):
        session.add(m.LiveOrderQueueDecision(
            decided_at=when, live_order_id=row.id, kalshi_order_id=row.kalshi_order_id,
            strategy=row.strategy, market_ticker=row.market_ticker,
            experiment_deployment_arm_id=arm_link_id, mode="shadow",
            limit_price=93, quantity=1, telemetry_status=tel, contracts_ahead=9000,
            decision=code, acted=False, rule_version="qac-v1-2026-09-07",
            cap_bound=cap_bound, book_open_count=40, book_open_cap=40,
        ))

    a = order("K-A", "filled")       # would-cancel, later filled, settled +$0.07
    b = order("K-B", "filled")       # would-cancel, later filled, still open
    c = order("K-C", "canceled")     # would-cancel, timed out
    d = order("K-D", "canceled")     # kept
    e = order("K-E", "canceled")     # telemetry missing
    for row in (a, b, c):
        decision(row, "queue_cancel", when=at)
        decision(row, "queue_cancel", when=at + timedelta(seconds=2), cap_bound=False)
    decision(d, "keep", when=at)
    decision(e, "telemetry_keep", when=at, tel="missing")
    session.add(m.Position(market_ticker="KXT-K-A", captured_at=at + timedelta(hours=5),
                           side="no", quantity=0, quantity_fp=0.0, realized_pnl=0.07))
    session.add(m.Position(market_ticker="KXT-K-B", captured_at=at + timedelta(hours=5),
                           side="no", quantity=1, quantity_fp=1.0, realized_pnl=None))
    session.flush()


def test_shadow_metrics_compute_from_decision_rows(registered, xos_session):
    at = registered["registered_at"] + timedelta(seconds=1)
    arm_link_id = pkg.active_lineage(xos_session)["probe"]["arm_link_id"]
    _seed_shadow(xos_session, arm_link_id, at=at)
    exp, ver = _version(xos_session)
    epoch = read.open_epoch_for(xos_session, ver)
    from kalshi_bot.experiment_os.models import PlatformSnapshot
    snapshot = xos_session.get(PlatformSnapshot, epoch.platform_snapshot_id)
    scope = evaluator._arm_scope(
        xos_session, exp, ver, epoch, pkg.ARM_TREATMENT, "probe",
        (epoch.started_at, at + timedelta(days=1)), snapshot.fingerprint,
    )
    assert scope.deployment_keys == (pkg.SHADOW_DEPLOYMENT_KEY,)

    def v(key):
        return metrics.compute_metric(xos_session, key, scope)

    assert v("qac_decisions").value == 8
    assert v("qac_telemetry_coverage_pct").value == 87.5          # 7 of 8 observed
    assert v("qac_would_cancel_orders").value == 3
    later = v("qac_would_cancel_later_fill_pct")
    assert later.value == pytest.approx(66.67, abs=0.01) and later.n == 3
    forgone = v("qac_forgone_cents_per_would_cancel")
    assert forgone.value == pytest.approx(7.0 / 3, abs=0.01)     # 7c over 3 would-cancels
    assert forgone.provenance["settled_later_fills"] == 1
    assert v("qac_would_cancel_cap_bound_pct").value == 100.0     # first decision was bound
    # $0.93 x 2s x 3 orders — the seed's two decisions are two seconds apart
    assert v("qac_capital_hours_released").value == pytest.approx(3 * 0.93 * 2 / 3600, abs=1e-4)


def test_shadow_metrics_refuse_a_non_probe_addressing(registered, xos_session):
    exp, ver = _version(xos_session)
    epoch = read.open_epoch_for(xos_session, ver)
    from kalshi_bot.experiment_os.models import PlatformSnapshot
    snapshot = xos_session.get(PlatformSnapshot, epoch.platform_snapshot_id)
    now = datetime.now(timezone.utc)
    paper_scope = evaluator._arm_scope(
        xos_session, exp, ver, epoch, pkg.ARM_TREATMENT, "paper",
        (epoch.started_at, now), snapshot.fingerprint,
    )
    val = metrics.compute_metric(xos_session, "qac_would_cancel_orders", paper_scope)
    assert val.missing and val.provenance.get("addressing_error")


def test_the_gates_evaluate_to_hold_on_no_evidence_not_blocked(registered, xos_session):
    """A freshly registered shadow with zero rows is HOLD (waiting), which proves the
    scopes and metrics resolve; BLOCKED_* would mean the contract is malformed."""
    _exp, ver = _version(xos_session)
    gates = {g.gate_key: g for g in read.gates_for(xos_session, ver)}
    promo = evaluator.evaluate_gate(xos_session, gates[pkg.PROMOTION_GATE_KEY])
    assert promo.verdict == "HOLD", promo
    kill = evaluator.evaluate_gate(xos_session, gates[pkg.KILL_GATE_KEY])
    assert kill.verdict in ("HOLD", "PASS"), kill


def test_the_kill_gate_fails_when_forgone_profit_is_large(registered, xos_session, monkeypatch):
    """Pin the FAIL path of the pre-registered kill: 40+ would-cancel orders whose later
    fills earned >= 3c each on average."""
    at = registered["registered_at"] + timedelta(seconds=1)
    arm_link_id = pkg.active_lineage(xos_session)["probe"]["arm_link_id"]
    for i in range(45):
        row = m.LiveOrder(
            market_ticker=f"KXT-{i}", event_ticker="KXT", strategy="Dmmsell10", side="no",
            action="buy", limit_price=93, quantity=1, status="filled", kalshi_order_id=f"K-{i}",
            client_order_id=f"c-{i}", created_at=at - timedelta(hours=2),
        )
        xos_session.add(row)
        xos_session.flush()
        xos_session.add(m.LiveOrderQueueDecision(
            decided_at=at, live_order_id=row.id, kalshi_order_id=row.kalshi_order_id,
            strategy="Dmmsell10", market_ticker=row.market_ticker,
            experiment_deployment_arm_id=arm_link_id, mode="shadow", limit_price=93,
            quantity=1, telemetry_status="observed", contracts_ahead=9000,
            decision="queue_cancel", acted=False, cap_bound=True,
        ))
        xos_session.add(m.Position(market_ticker=f"KXT-{i}", captured_at=at + timedelta(hours=5),
                                   side="no", quantity=0, quantity_fp=0.0, realized_pnl=0.07))
    xos_session.flush()
    _exp, ver = _version(xos_session)
    gates = {g.gate_key: g for g in read.gates_for(xos_session, ver)}
    end = at + timedelta(days=1)
    kill = evaluator.evaluate_gate(xos_session, gates[pkg.KILL_GATE_KEY], window_end=end)
    assert kill.verdict == "FAIL", kill
    # A promotion gate never FAILs on an unmet pass clause — it HOLDs; the kill is the FAIL.
    promo = evaluator.evaluate_gate(xos_session, gates[pkg.PROMOTION_GATE_KEY], window_end=end)
    assert promo.verdict == "HOLD" and "qac_forgone_cents_per_would_cancel" in promo.explanation
