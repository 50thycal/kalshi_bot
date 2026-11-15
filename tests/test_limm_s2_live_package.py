"""`kalshi_bot/experiment_os/limm_s2_live.py`: registered under NEW_ONLY, the contract lands at
PROBE with a frozen envelope taken from `s2_live`, three gates and a tagless probe; `arm` walks
PROBE->PAPER only on a fresh probe-gate PASS and arms fresh live + twin tags at one instant;
`activation_env` appends and never drops a running tag."""

from __future__ import annotations

import pathlib

import pytest
from sqlalchemy import select

from kalshi_bot.experiment_os import enforcement as enf
from kalshi_bot.experiment_os import experiment_commands as cmds
from kalshi_bot.experiment_os import limm_s2_live as pkg
from kalshi_bot.experiment_os import read
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.lifecycle import LifecycleState
from kalshi_bot.experiment_os.metrics import resolve_definition
from kalshi_bot.experiment_os.models import ExperimentDeployment, ExperimentDeploymentArm
from kalshi_bot.liquidity_incentive import s2_live as s2

ROOT = pathlib.Path(__file__).resolve().parents[1]
GATE_SPECS = (pkg.PROBE_GATE_SPEC, pkg.PROMOTION_GATE_SPEC, pkg.KEEP_GATE_SPEC)


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


def test_registers_at_probe_frozen_with_envelope_and_three_gates(registered, xos_session):
    exp = read.get_experiment(xos_session, pkg.EXPERIMENT_KEY)
    ver = read.latest_version(xos_session, exp)
    assert exp.state == LifecycleState.PROBE.value and ver.frozen_at is not None
    assert ver.risk_json == pkg.RISK_ENVELOPE
    assert {g.gate_key for g in read.gates_for(xos_session, ver)} == {
        pkg.PROBE_GATE_KEY, pkg.PROMOTION_GATE_KEY, pkg.KEEP_GATE_KEY}
    arms = read.arms_for(xos_session, ver)
    assert [(a.arm_key, a.strategy_tag) for a in arms] == [(pkg.ARM_KEY, pkg.PAPER_TAG)]


def test_nothing_is_admissible_after_registration(registered, xos_session):
    dep = xos_session.scalar(select(ExperimentDeployment).where(
        ExperimentDeployment.deployment_key == pkg.PROBE_DEPLOYMENT_KEY))
    darms = xos_session.scalars(select(ExperimentDeploymentArm).where(
        ExperimentDeploymentArm.deployment_id == dep.id)).all()
    assert dep.kind == "probe" and all(a.strategy_tag is None for a in darms)
    enf.refresh(xos_session)
    for tag in (pkg.PAPER_TAG, pkg.LIVE_TAG, pkg.TWIN_TAG):
        assert enf.tag_admissible(xos_session, tag) is False


def test_registering_twice_is_refused(registered, xos_session):
    with pytest.raises(svc.ExperimentOsError, match="already exists"):
        pkg.register(xos_session)


def test_a_promotion_floor_can_only_tighten(xos_session, xos_platform):
    with pytest.raises(svc.ExperimentOsError, match="never loosen"):
        pkg.register(xos_session, promotion_sample_floor=10)


def test_the_envelope_is_the_rules_module():
    e, r = pkg.RISK_ENVELOPE, s2.DEFAULT_RULES
    assert (e["max_markets"], e["max_markets_per_event"], e["bid_usd"], e["max_resting_usd"]) == (
        r.max_markets, r.max_per_event, r.bid_usd, r.max_resting_usd)
    assert e["fill_loss_stop_usd"] == r.fill_loss_stop_usd
    assert e["worst_case_loss_usd"] == r.worst_case_loss_usd
    assert e["duration_days"] == r.duration_days
    assert (pkg.PAPER_TAG, pkg.LIVE_TAG, pkg.TWIN_TAG) == (s2.PAPER_TAG, s2.LIVE_TAG, s2.TWIN_TAG)


@pytest.mark.parametrize("spec", GATE_SPECS)
def test_gate_specs_are_valid_and_use_registered_metrics(spec):
    svc.validate_gate_spec(spec)
    for name in ("pass_all", "fail_any", "hold_if"):
        for clause in spec.get(name) or []:
            assert resolve_definition(clause["metric"]) is not None, clause
    for floor in (spec.get("sample") or {}).values():
        assert resolve_definition(floor["metric"]) is not None, floor


def test_keep_gate_is_live_only_and_stops_at_the_registered_loss():
    spec = pkg.KEEP_GATE_SPEC
    for name in ("pass_all", "fail_any"):
        for clause in spec[name]:
            assert clause["deployment_kind"] == "live"
    pnl = next(c for c in spec["fail_any"] if c["metric"] == "live_realized_pnl_usd")
    assert pnl["value"] == -s2.DEFAULT_RULES.fill_loss_stop_usd


def test_wired_with_arm_and_activation_vars_the_env_channel_accepts():
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    import railway_env

    p = cmds._packages()["limm-s2-live"]
    assert p.register is pkg.register and p.arm is pkg.arm
    assert p.experiment_key == pkg.EXPERIMENT_KEY
    assert set(p.strategy_tags) == {pkg.PAPER_TAG, pkg.LIVE_TAG, pkg.TWIN_TAG}
    assert p.activation_vars == pkg.ACTIVATION_VARS
    for var in p.activation_vars:
        assert var in railway_env.ALLOWED_VARS, var
    # never the fleet-wide twin suffix, never Alimm1's switch, never a cap
    for var in ("LIVE_PAPER_TWIN_SUFFIX", "LIQUIDITY_INCENTIVE_LIVE_ENABLED", "MAX_DAILY_LOSS",
                "MAX_TOTAL_EXPOSURE", "LIVE_MAX_ORDER_DOLLARS"):
        assert var not in p.activation_vars


def test_activation_appends_last_and_never_drops_a_running_tag():
    env = pkg.activation_env(current_live_strategies="Fmmsell10,Gmmsell2",
                             current_live_paper_twins="Fmmsell10:Fmmsell10_pt4")
    assert env["LIVE_STRATEGIES"] == f"Fmmsell10,Gmmsell2,{pkg.LIVE_TAG}"
    assert env["LIVE_PAPER_TWINS"] == f"Fmmsell10:Fmmsell10_pt4,{pkg.LIVE_TAG}:{pkg.TWIN_TAG}"
    assert env["LIQUIDITY_INCENTIVE_S2_LIVE_ENABLED"] == "true"
    assert list(env)[-1] == "LIVE_STRATEGIES"
    again = pkg.activation_env(current_live_strategies=env["LIVE_STRATEGIES"],
                               current_live_paper_twins=env["LIVE_PAPER_TWINS"])
    assert again["LIVE_STRATEGIES"] == env["LIVE_STRATEGIES"]


def test_material_baseline_names_both_tags():
    from kalshi_bot.experiment_os import enforcement as e

    mat = e.live_material_or_none(pkg.material_config())
    assert mat["live_strategies_contains"] == [pkg.LIVE_TAG]
    assert mat["twin_pairs"][pkg.LIVE_TAG] == pkg.TWIN_TAG
    assert pkg.BOOK_PARAMS is None


def test_arm_refuses_on_thin_evidence(xos_session, xos_platform):
    from datetime import datetime, timedelta, timezone

    pkg.register(xos_session, now=datetime.now(timezone.utc) - timedelta(hours=2))
    with pytest.raises(svc.ExperimentOsError, match="probe gate"):
        pkg.arm(xos_session, approved_by="Calvin", actor="tester")


def test_arm_ends_the_probe_and_arms_live_and_twin_at_one_instant(xos_session, xos_platform):
    from datetime import datetime, timedelta, timezone

    from tests.test_liquidity_incentive_xos_package import _seed_shadow_evidence

    at = datetime.now(timezone.utc) - timedelta(hours=2)
    out = pkg.register(xos_session, now=at)
    _seed_shadow_evidence(xos_session, since=at)
    armed = pkg.arm(xos_session, approved_by="Calvin", actor="tester")
    xos_session.refresh(out["probe"])
    assert out["probe"].ended_at is not None
    live, twin = armed["live"], armed["twin"]
    assert live.kind == "live" and twin.kind == "paper_twin"
    assert live.started_at == twin.started_at and live.epoch_id == twin.epoch_id
    exp = read.get_experiment(xos_session, pkg.EXPERIMENT_KEY)
    assert exp.state == LifecycleState.LIVE_CANARY.value


def test_cited_documents_exist():
    for rel in (pkg.SPEC_DOC, pkg.HANDOFF_DOC, pkg.PAPER_SPEC_DOC, pkg.RULES_MODULE):
        assert (ROOT / rel).exists(), rel
