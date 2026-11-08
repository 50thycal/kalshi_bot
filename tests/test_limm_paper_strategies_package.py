"""Acceptance evidence for `kalshi_bot/experiment_os/limm_paper_strategies.py`: registered
under NEW_ONLY the contract is frozen at PROBE with seven arms (three controls), no gates, a
read rule that agrees with the read script, and a tagless probe nothing can trade through."""

from __future__ import annotations

import importlib.util
import pathlib

import pytest
from sqlalchemy import select

from kalshi_bot.experiment_os import enforcement as enf
from kalshi_bot.experiment_os import experiment_commands as cmds
from kalshi_bot.experiment_os import limm_paper_strategies as pkg
from kalshi_bot.experiment_os import read
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.lifecycle import ArmRole, DeploymentKind, LifecycleState
from kalshi_bot.experiment_os.models import ExperimentDeployment, ExperimentDeploymentArm
from kalshi_bot.liquidity_incentive import paper_arms as pa

ROOT = pathlib.Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("limm_paper_strategies_read",
                                               ROOT / "scripts" / "limm_paper_strategies_read.py")
rd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rd)  # type: ignore[union-attr]


@pytest.fixture(autouse=True)
def _fresh_resolver():
    enf.reset_for_tests()
    yield
    enf.reset_for_tests()


@pytest.fixture
def registered(xos_session, xos_platform):
    enf.record_enforcement_change(
        xos_session, mode="NEW_ONLY", actor="operator", reason="test",
        cutover_id="test-new-only", readiness={"ok": True, "checks": {}})
    out = pkg.register(xos_session, actor="research-lab")
    xos_session.commit()
    return out


def test_registration_lands_at_probe_frozen_with_seven_arms_and_no_gates(registered, xos_session):
    exp = read.get_experiment(xos_session, pkg.EXPERIMENT_KEY)
    ver = read.latest_version(xos_session, exp)
    assert exp.state == LifecycleState.PROBE.value and ver.frozen_at is not None
    arms = {a.arm_key: a for a in read.arms_for(xos_session, ver)}
    assert set(arms) == set(pa.ARMS)
    assert {k for k, a in arms.items() if a.role == ArmRole.CONTROL.value} == {
        pa.ARM_S2_BEHIND, pa.ARM_S3_CONTROL, pa.ARM_S4_FLAT}
    assert all(a.strategy_tag is None for a in arms.values())
    assert read.gates_for(xos_session, ver) == []


def test_registering_twice_is_refused(registered, xos_session):
    with pytest.raises(svc.ExperimentOsError, match="already exists"):
        pkg.register(xos_session, actor="research-lab")


def test_a_promotion_floor_is_refused_not_ignored(xos_session, xos_platform):
    with pytest.raises(svc.ExperimentOsError, match="no promotion gate"):
        pkg.register(xos_session, actor="research-lab", promotion_sample_floor=300)


def test_the_probe_carries_no_tags_and_nothing_is_admissible(registered, xos_session):
    dep = xos_session.scalar(select(ExperimentDeployment).where(
        ExperimentDeployment.deployment_key == pkg.PROBE_DEPLOYMENT_KEY))
    assert dep is not None and dep.kind == DeploymentKind.PROBE.value
    darms = xos_session.scalars(select(ExperimentDeploymentArm).where(
        ExperimentDeploymentArm.deployment_id == dep.id)).all()
    assert len(darms) == 7 and all(a.strategy_tag is None for a in darms)
    enf.refresh(xos_session)
    for tag in ("S1", "S2-best", "S4-burst", "Alimm1"):
        assert enf.tag_admissible(xos_session, tag) is False


def test_the_frozen_read_rule_agrees_with_the_read_script():
    r = pkg.PROBE_RULE
    assert r["read_not_before"] == rd.READ_NOT_BEFORE
    assert r["floors"] == {"bid_days_per_arm": rd.MIN_BID_DAYS, "events_per_arm": rd.MIN_EVENTS}
    assert (r["primary_fill_model"], r["stress_fill_model"]) == (rd.PRIMARY_MODEL, rd.STRESS_MODEL)
    assert "$0.15" in r["promising"] and rd.PROMISING_USD == 0.15
    assert "1.34" in r["reward"] and rd.CALIBRATION == pa.DEFAULT_RULES.calibration


def test_wired_on_the_transport_and_the_paper_switch_is_settable():
    """The package arms nothing, so it declares no activation_vars (the repo invariant);
    the paper switch is a separate env act, and the ops channel must be able to set it."""
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    import railway_env

    p = cmds._packages()["limm-paper-strategies"]
    assert p.register is pkg.register and p.experiment_key == pkg.EXPERIMENT_KEY
    assert p.arm is None and not p.activation_vars
    assert pkg.ACTIVATION_VAR in railway_env.ALLOWED_VARS


def test_cited_documents_exist():
    for rel in (pkg.SPEC_DOC, pkg.AGE_STUDY_DOC, pkg.HANDOFF_DOC, pkg.INSTRUMENT, pkg.READ_SCRIPT):
        assert (ROOT / rel).exists(), rel
