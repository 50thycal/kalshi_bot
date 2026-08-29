"""Acceptance evidence for `kalshi_bot/experiment_os/marktangle.py`.

Everything asserted here is a property the contract is supposed to have, proved
against the real service under NEW_ONLY enforcement rather than described in a
docstring. In particular the two claims that make this a conditional-reversion
experiment and not a Martingale — no sizing rule may reference prior losses, and
nothing registered here can reach the exchange — are tests, not prose.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from kalshi_bot.experiment_os import enforcement as enf
from kalshi_bot.experiment_os import evaluator, read
from kalshi_bot.experiment_os import marktangle as pkg
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.lifecycle import ArmRole, DeploymentKind, LifecycleState
from kalshi_bot.experiment_os.models import ExperimentDeployment, ExperimentDeploymentArm


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
    out = pkg.register(xos_session, actor="research-lab")
    xos_session.commit()
    return out


def _version(session):
    exp = read.get_experiment(session, pkg.EXPERIMENT_KEY)
    return exp, read.latest_version(session, exp)


# ===========================================================================
# 1. The contract exists, frozen, at PROBE
# ===========================================================================


def test_registration_lands_at_probe_with_a_frozen_v1(registered, xos_session):
    exp, ver = _version(xos_session)
    assert exp.state == LifecycleState.PROBE.value
    assert ver.version == 1
    assert ver.frozen_at is not None, "the contract must be frozen to pre-register"
    assert registered["tags"] == []


def test_registering_twice_is_refused_not_silently_duplicated(registered, xos_session):
    with pytest.raises(svc.ExperimentOsError, match="already exists"):
        pkg.register(xos_session, actor="research-lab")


def test_the_five_arms_are_declared_with_a_real_control(registered, xos_session):
    _exp, ver = _version(xos_session)
    arms = {a.arm_key: a for a in read.arms_for(xos_session, ver)}
    assert set(arms) == {"mktrev3", "mktrev5", "mktkelly", "mktcont", "mktnaive"}
    assert arms["mktcont"].role == ArmRole.CONTROL.value
    assert arms["mktnaive"].role == ArmRole.BENCHMARK.value
    assert {k for k, a in arms.items() if a.role == ArmRole.TREATMENT.value} == {
        "mktrev3", "mktrev5", "mktkelly"
    }


def test_the_mirror_control_differs_from_the_treatment_only_in_side(
    registered, xos_session
):
    """The control has to be the same book pointed the other way, or the delta it
    anchors measures the universe rather than the signal."""
    _exp, ver = _version(xos_session)
    arms = {a.arm_key: a.params_json for a in read.arms_for(xos_session, ver)}
    assert arms["mktcont"]["min_run"] == arms["mktrev3"]["min_run"]
    assert arms["mktcont"]["sizing"] == arms["mktrev3"]["sizing"]
    assert arms["mktcont"]["side"] == "continuation"


# ===========================================================================
# 2. It is not a Martingale, and the record says so immutably
# ===========================================================================


def test_no_arm_sizes_off_prior_losses(registered, xos_session):
    _exp, ver = _version(xos_session)
    for arm in read.arms_for(xos_session, ver):
        sizing = (arm.params_json or {}).get("sizing")
        assert sizing in ("flat", "quarter_kelly"), (
            f"arm {arm.arm_key} declares sizing {sizing!r} — every sizing rule "
            "here must be a function of estimated edge, never of prior outcomes"
        )


def test_martingale_is_a_frozen_exclusion_not_an_untested_option(
    registered, xos_session
):
    _exp, ver = _version(xos_session)
    held = " ".join(ver.held_constant_json or [])
    assert "Martingale" in held and "recovery multiplier" in held
    with pytest.raises(svc.ImmutableRecord):
        # A frozen version cannot acquire the exclusion's opposite quietly.
        ver.held_constant_json = ["anything"]
        xos_session.flush()


def test_the_probe_rule_is_frozen_with_the_contract(registered, xos_session):
    """The bar has to exist before the data does, or 'pre-registration' means the
    afternoon the results arrived."""
    _exp, ver = _version(xos_session)
    probe = (ver.sample_json or {}).get("probe") or {}
    assert probe.get("split", "").startswith("first 70%")
    assert any("Wilson" in c for c in probe.get("pass", []))
    assert "no result" in probe.get("hold", "")


# ===========================================================================
# 3. Nothing registered here can trade
# ===========================================================================


def test_the_probe_deployment_carries_no_tags_under_new_only(
    registered, xos_session
):
    _exp, ver = _version(xos_session)
    epoch = read.open_epoch_for(xos_session, ver)
    assert epoch is not None and epoch.ended_at is None
    deployments = xos_session.scalars(select(ExperimentDeployment)).all()
    assert len(deployments) == 1
    dep = deployments[0]
    assert dep.kind == DeploymentKind.PROBE.value
    assert dep.stage == LifecycleState.PROBE.value
    tags = [da.strategy_tag
            for da in xos_session.scalars(select(ExperimentDeploymentArm)).all()]
    assert tags == [None] * 5, f"probe deployment must be tagless, got {tags}"


def test_an_unregistered_marktangle_tag_still_cannot_trade(registered, xos_session):
    """The point of tagless registration: registering the experiment grants no
    trading capability at all."""
    enf.refresh(xos_session)
    for arm_key in ("mktrev3", "mktrev5", "mktkelly", "mktcont", "mktnaive"):
        assert not enf.tag_admissible(xos_session, arm_key), (
            f"{arm_key} resolved to an active deployment arm — registering a "
            "PROBE contract must grant no trading capability"
        )


# ===========================================================================
# 4. The gates are pre-registered, resolvable, and gate the right thing
# ===========================================================================


def test_both_gates_resolve_against_the_frozen_arm_set(registered, xos_session):
    _exp, ver = _version(xos_session)
    for spec in (pkg.PROMOTION_GATE_SPEC, pkg.KEEP_GATE_SPEC):
        assert evaluator.validate_gate_scopes(xos_session, ver, spec) == []


def test_the_promotion_gate_turns_on_the_mirror_delta(registered, xos_session):
    """Absolute P&L alone must never promote this book: a profitable treatment
    whose mirror is equally profitable has demonstrated a mispriced family, not
    streak dependence."""
    deltas = [
        c for c in pkg.PROMOTION_GATE_SPEC["pass_all"]
        if c["metric"] == "delta.pnl_cents_per_trade"
    ]
    assert {c["control"] for c in deltas} == {"mktcont", "mktnaive"}
    kill = pkg.PROMOTION_GATE_SPEC["fail_any"][0]
    assert kill["control"] == "mktcont" and kill["op"] == "<=" and kill["value"] == 0


def test_only_the_named_treatment_can_promote(registered, xos_session):
    """A gate that promotes whichever of three arms looks best is a three-way
    search, and its winner's bar is not the bar that was pre-registered."""
    named = {c.get("treatment") or c.get("arm")
             for c in pkg.PROMOTION_GATE_SPEC["pass_all"]
             + pkg.PROMOTION_GATE_SPEC["fail_any"]}
    assert named == {"mktrev3"}


def test_gate_evidence_has_not_started(registered, xos_session):
    """No arm has a tag, so starting the clock would floor every future evidence
    window at a boundary that predates the book."""
    _exp, ver = _version(xos_session)
    for gate in read.gates_for(xos_session, ver):
        assert gate.evidence_started_at is None


def test_the_gate_spec_is_immutable_once_registered(registered, xos_session):
    _exp, ver = _version(xos_session)
    gate = next(g for g in read.gates_for(xos_session, ver)
                if g.gate_key == pkg.PROMOTION_GATE_KEY)
    with pytest.raises(svc.ImmutableRecord):
        gate.spec_json = {"pass_all": [{"metric": "pnl_cents_per_trade",
                                        "arm": "mktrev3", "op": ">", "value": -99}]}
        xos_session.flush()


# ===========================================================================
# 5. The transport can run it, and only the roles that should
# ===========================================================================


def test_the_package_is_reachable_through_the_experiment_command_transport():
    from kalshi_bot.experiment_os import experiment_commands as ec

    assert "marktangle-reversion" in ec.package_names()
    package = ec._packages()["marktangle-reversion"]
    assert package.experiment_key == pkg.EXPERIMENT_KEY
    assert package.arm is None, "this package arms nothing"
    assert not package.activation_vars
