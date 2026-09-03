"""Acceptance evidence for `kalshi_bot/experiment_os/marktangle2.py`.

Proved against the real service under NEW_ONLY: the contract is frozen at PROBE
with MARKTANGLE-1 as its predecessor and MARKTANGLE-1 untouched, every track has
a real mirror control, no sizing rule can remember a loss, the probe rule in the
frozen contract agrees with the instrument's constants, and nothing registered
here can reach the exchange.
"""

from __future__ import annotations

import pathlib
import sys

import pytest
from sqlalchemy import select

from kalshi_bot.experiment_os import enforcement as enf
from kalshi_bot.experiment_os import experiment_commands as cmds
from kalshi_bot.experiment_os import marktangle as m1
from kalshi_bot.experiment_os import marktangle2 as pkg
from kalshi_bot.experiment_os import read
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.lifecycle import ArmRole, DeploymentKind, LifecycleState
from kalshi_bot.experiment_os.models import ExperimentDeployment, ExperimentDeploymentArm

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import marktangle2_probe as probe  # noqa: E402


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
    m1.register(xos_session, actor="research-lab")
    xos_session.commit()
    out = pkg.register(xos_session, actor="research-lab")
    xos_session.commit()
    return out


def _version(session):
    exp = read.get_experiment(session, pkg.EXPERIMENT_KEY)
    return exp, read.latest_version(session, exp)


# ===========================================================================
# 1. A separate, frozen contract at PROBE with MARKTANGLE-1 as predecessor
# ===========================================================================


def test_registration_lands_at_probe_frozen_with_marktangle_1_as_predecessor(registered, xos_session):
    exp, ver = _version(xos_session)
    assert exp.state == LifecycleState.PROBE.value
    assert ver.version == 1 and ver.frozen_at is not None
    assert registered["predecessor"] == m1.EXPERIMENT_KEY
    parent = read.get_experiment(xos_session, m1.EXPERIMENT_KEY)
    assert exp.predecessor_experiment_id == parent.id
    assert registered["tags"] == []


def test_registers_without_a_predecessor_when_marktangle_1_is_absent(xos_session, xos_platform):
    out = pkg.register(xos_session, actor="research-lab")
    assert out["predecessor"] is None and out["state"] == LifecycleState.PROBE.value


def test_registering_twice_is_refused(registered, xos_session):
    with pytest.raises(svc.ExperimentOsError, match="already exists"):
        pkg.register(xos_session, actor="research-lab")


def test_marktangle_1_is_untouched_by_registration(registered, xos_session):
    """§2.4: no new version, no new arm, no state change on the parent."""
    parent = read.get_experiment(xos_session, m1.EXPERIMENT_KEY)
    ver = read.latest_version(xos_session, parent)
    assert parent.state == LifecycleState.PROBE.value and ver.version == 1
    assert {a.arm_key for a in read.arms_for(xos_session, ver)} == {
        "mktrev3", "mktrev5", "mktkelly", "mktcont", "mktnaive"}
    assert (ver.sample_json["probe"]["pass"][0]).startswith("at least one family with >= 100")


# ===========================================================================
# 2. Arms: two tracks, each with a baseline, treatments and a mirror control
# ===========================================================================


def test_each_track_has_a_baseline_three_treatments_and_a_mirror_control(registered, xos_session):
    _exp, ver = _version(xos_session)
    arms = {a.arm_key: a for a in read.arms_for(xos_session, ver)}
    assert set(arms) == {"m2a0", "m2a1", "m2a2", "m2a3", "m2amirror",
                         "m2b0", "m2b1", "m2b2", "m2b3", "m2bmirror"}
    for track, base, mirror in (("A", "m2a0", "m2amirror"), ("B", "m2b0", "m2bmirror")):
        assert arms[base].role == ArmRole.BENCHMARK.value
        assert arms[mirror].role == ArmRole.CONTROL.value
        assert arms[mirror].params_json["side"] == "opposite"
        treatments = {k for k, a in arms.items()
                      if a.role == ArmRole.TREATMENT.value and a.params_json["track"] == track}
        assert len(treatments) == 3 and pkg.PRIMARY[track] in treatments


def test_no_arm_sizes_off_prior_losses(registered, xos_session):
    _exp, ver = _version(xos_session)
    for arm in read.arms_for(xos_session, ver):
        assert (arm.params_json or {}).get("sizing") == "flat", arm.arm_key
    held = " ".join(ver.held_constant_json or [])
    assert "Martingale" in held and "zero effect on the next trade" in held
    with pytest.raises(svc.ImmutableRecord):
        ver.held_constant_json = ["anything"]
        xos_session.flush()


# ===========================================================================
# 3. The probe rule is frozen and agrees with the instrument
# ===========================================================================


def test_the_probe_rule_is_frozen_and_matches_the_instrument_constants(registered, xos_session):
    _exp, ver = _version(xos_session)
    rule = ver.sample_json["probe"]
    assert rule["split"].startswith("first 70%") and probe.TRAIN_FRAC == 0.70
    assert rule["floors"] == {"train_prediction_points": probe.FLOOR_TRAIN_POINTS,
                              "holdout_trades": probe.FLOOR_HOLDOUT_TRADES,
                              "holdout_price_coverage": probe.PRICE_COVERAGE_FLOOR}
    assert rule["primary_per_track"] == {"A": "m2a3", "B": "m2b3"}
    assert probe.PRIMARY == {"A": "A3", "B": "B3"}
    assert pkg.EDGE_BAR_CENTS == probe.EDGE_BAR_C
    assert pkg.MIRROR_DELTA_CENTS == probe.MIRROR_DELTA_C
    assert ver.costs_json["slippage_cents"] == probe.SLIPPAGE_C
    assert ver.costs_json["max_spread_cents"] == probe.MAX_SPREAD_C
    assert "no result" in rule["hold"]
    arms = {a.arm_key: a for a in read.arms_for(xos_session, ver)}
    assert arms["m2a3"].params_json["family_ridge"] == probe.RIDGE_FAMILY
    assert arms["m2b3"].params_json["vol_window_days"] == probe.VOL_WINDOW_DAYS
    assert arms["m2b3"].params_json["z_cap"] == probe.Z_CAP
    assert arms["m2a2"].params_json["max_k"] == probe.MAX_K_A
    buckets = [[lo, None if hi >= 10**9 else hi] for lo, hi in probe.DURATION_BUCKETS_B]
    assert arms["m2b2"].params_json["buckets"] == buckets


def test_gates_are_pre_registered_per_track_and_evidence_has_not_started(registered, xos_session):
    _exp, ver = _version(xos_session)
    gates = {g.gate_key: g for g in read.gates_for(xos_session, ver)}
    assert set(gates) == {"paper_to_live_canary_a", "paper_keep_a",
                          "paper_to_live_canary_b", "paper_keep_b"}
    for key in ("paper_to_live_canary_a", "paper_to_live_canary_b"):
        g = gates[key]
        assert g.from_state == LifecycleState.PAPER.value
        assert g.to_state == LifecycleState.LIVE_CANARY.value
        assert g.evidence_started_at is None
        fail = g.spec_json["fail_any"][0]
        assert fail["metric"] == "delta.pnl_cents_per_trade" and fail["op"] == "<="
    assert gates["paper_to_live_canary_a"].spec_json["pass_all"][1]["treatment"] == "m2a3"
    assert gates["paper_to_live_canary_b"].spec_json["pass_all"][1]["control"] == "m2bmirror"


# ===========================================================================
# 4. Nothing registered here can trade
# ===========================================================================


def test_the_probe_deployment_carries_no_tags_under_new_only(registered, xos_session):
    dep = xos_session.scalar(select(ExperimentDeployment).where(
        ExperimentDeployment.deployment_key == pkg.PROBE_DEPLOYMENT_KEY))
    assert dep is not None and dep.kind == DeploymentKind.PROBE.value
    arms = xos_session.scalars(select(ExperimentDeploymentArm).where(
        ExperimentDeploymentArm.deployment_id == dep.id)).all()
    assert len(arms) == 10 and all(a.strategy_tag is None for a in arms)
    enf.refresh(xos_session)
    for tag in ("m2a3", "m2b3", "m2amirror", "marktangle2"):
        assert enf.tag_admissible(xos_session, tag) is False


# ===========================================================================
# 5. Wired on the transport, and the documents it cites exist
# ===========================================================================


def test_the_package_is_registered_on_the_experiment_command_transport():
    packages = cmds._packages()
    assert "marktangle-2" in packages
    assert packages["marktangle-2"].experiment_key == pkg.EXPERIMENT_KEY
    assert packages["marktangle-2"].register is pkg.register


def test_cited_documents_and_instrument_exist():
    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in (pkg.SPEC_DOC, pkg.INSTRUMENT, pkg.WORKSTREAM, "scripts/marktangle2_package.py"):
        assert (root / rel).exists(), rel


# ===========================================================================
# 6. The transport's one knob: promotion_sample_floor, raise-only
# ===========================================================================


def test_every_package_register_accepts_the_transport_floor_keyword():
    """`_register_package` always passes `promotion_sample_floor=`; a package whose
    register() cannot take it fails at the worker with a TypeError, which is exactly
    how MARKTANGLE-2's first envelope (m2-register-1, 2026-09-02) died."""
    import inspect
    for name, package in cmds._packages().items():
        if package.register is cmds._no_contract:
            continue
        params = inspect.signature(package.register).parameters
        assert "promotion_sample_floor" in params or any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
        ), f"package {name!r}: register() cannot take promotion_sample_floor"


def test_the_floor_may_only_be_raised(xos_session, xos_platform):
    with pytest.raises(svc.ExperimentOsError, match="never weaker"):
        pkg.register(xos_session, actor="research-lab", promotion_sample_floor=100)
    pkg.register(xos_session, actor="research-lab", promotion_sample_floor=300)
    _exp, ver = _version(xos_session)
    gates = {g.gate_key: g for g in read.gates_for(xos_session, ver)}
    for key in ("paper_to_live_canary_a", "paper_to_live_canary_b"):
        assert all(c["value"] == 300 for c in gates[key].spec_json["sample"].values())
    assert set(ver.sample_json["paper_floor_settled_trades"].values()) == {300}


def test_marktangle_1_register_honours_the_same_contract(xos_session, xos_platform):
    with pytest.raises(svc.ExperimentOsError, match="never weaker"):
        m1.register(xos_session, actor="research-lab", promotion_sample_floor=50)
    m1.register(xos_session, actor="research-lab", promotion_sample_floor=250)
    exp = read.get_experiment(xos_session, m1.EXPERIMENT_KEY)
    ver = read.latest_version(xos_session, exp)
    gate = {g.gate_key: g for g in read.gates_for(xos_session, ver)}["paper_to_live_canary"]
    assert {c["value"] for c in gate.spec_json["sample"].values()} == {250}
