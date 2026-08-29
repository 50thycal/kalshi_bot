"""PERP-V1 registration package (docs/PERP_V1_THESIS.md).

These tests exist to pin the properties that make the registration honest rather
than merely successful: the horse-race shape the operator asked for, the control
that separates the mechanism from the crypto tape, the absence of any tag or
deployment that could reach the trading write path, and the one-way sample floor.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from kalshi_bot.experiment_os import perp_v1, read
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.experiment_commands import _packages, package_names
from kalshi_bot.experiment_os.lifecycle import ArmRole, LifecycleState
from kalshi_bot.experiment_os.metrics import REGISTRY, resolve_definition
from kalshi_bot.experiment_os.models import ExperimentDeployment, ExperimentStateTransition


@pytest.fixture
def registered(xos_session, xos_platform):
    return perp_v1.register(xos_session, actor="research-lab-test")


def test_the_horse_race_is_three_treatments_and_one_control(xos_session, registered):
    """The operator asked for one experiment and three arms, one per mechanism. The
    control is the fourth arm and is not a fourth strategy: without it
    `delta.perp_net_edge_bps_per_trade` has nothing to resolve against and every
    arm can be flattered by an accidental long-crypto tilt in a rising sample."""
    arms = {a.arm_key: a for a in read.arms_for(xos_session, registered["version"])}
    assert set(arms) == {"perprevert", "perpcarry", "perplead", "perpctl"}
    assert sorted(k for k, a in arms.items()
                  if a.role == ArmRole.TREATMENT.value) == [
        "perpcarry", "perplead", "perprevert",
    ]
    assert arms["perpctl"].role == ArmRole.CONTROL.value


def test_registration_creates_no_tag_and_no_deployment(xos_session, registered):
    """A probe is an instrument, not a deployment. Under NEW_ONLY an unregistered
    tag cannot trade, and registering one here would make a perp book admissible to
    the write path before a single byte of perp tape has been read."""
    arms = read.arms_for(xos_session, registered["version"])
    assert all(a.strategy_tag is None for a in arms)
    assert xos_session.scalars(select(ExperimentDeployment)).all() == []


def test_it_lands_in_probe_with_a_frozen_contract(xos_session, registered):
    exp = registered["experiment"]
    assert exp.state == LifecycleState.PROBE.value
    assert registered["version"].frozen_at is not None
    assert registered["version"].pre_registration_hash
    states = [
        t.to_state for t in xos_session.scalars(
            select(ExperimentStateTransition)
            .where(ExperimentStateTransition.experiment_id == exp.id)
            .order_by(ExperimentStateTransition.id)
        )
    ]
    assert states == [LifecycleState.IDEA.value, LifecycleState.PROBE.value]


def test_every_arm_has_its_own_probe_to_paper_gate(xos_session, registered):
    """One gate per arm is the horse race made structural: an arm that clears its
    own bar carries into paper without waiting for, or being rescued by, the other
    two."""
    gates = {g.gate_key: g for g in registered["gates"]}
    promos = {k: g for k, g in gates.items() if g.kind == "promotion"}
    assert set(promos) == {
        "probe_to_paper_perprevert",
        "probe_to_paper_perpcarry",
        "probe_to_paper_perplead",
    }
    for gate in promos.values():
        assert gate.from_state == LifecycleState.PROBE.value
        assert gate.to_state == LifecycleState.PAPER.value
        assert gate.evidence_started_at is not None
    assert gates["perp_probe_stop"].kind == "kill"


def test_every_promotion_gate_reads_net_edge_against_the_control(registered):
    """Gross convergence and gross funding income are diagnostics. The bar is the
    NET number, and it must beat the matched random-direction control on the same
    tape — otherwise the experiment measures the crypto tape."""
    for gate in registered["gates"]:
        if gate.kind != "promotion":
            continue
        metrics = {c["metric"] for c in gate.spec_json["pass_all"]}
        assert "perp_net_edge_bps_per_trade" in metrics
        assert "delta.perp_net_edge_bps_per_trade" in metrics
        assert "perp_data_coverage_pct" in metrics
        delta = next(c for c in gate.spec_json["pass_all"]
                     if c["metric"] == "delta.perp_net_edge_bps_per_trade")
        assert delta["control"] == "perpctl"


def test_arm_specific_clauses_are_the_arms_own_hypotheses(registered):
    """Arm B's claim is that the edge is not crypto beta; arm C's is that the
    overlay adds REALIZABLE cents over Theta. Each gate says so, and arm A's does
    not carry a second weaker restatement of the common bar."""
    by_key = {g.gate_key: g for g in registered["gates"]}

    def metrics(key):
        return {c["metric"] for c in by_key[key].spec_json["pass_all"]}

    assert "perp_beta_adjusted_net_edge_bps" in metrics("probe_to_paper_perpcarry")
    assert ("perp_incremental_cents_per_trade_vs_theta"
            in metrics("probe_to_paper_perplead"))
    assert metrics("probe_to_paper_perprevert") == {
        "perp_net_edge_bps_per_trade",
        "delta.perp_net_edge_bps_per_trade",
        "perp_data_coverage_pct",
    }


def test_every_gated_metric_is_in_the_canonical_registry(registered):
    """An unregistered metric key is always a typo or an undeclared quantity. The
    freeze already enforces this; the test states it so a later gate edit that
    invents a quantity fails here rather than at a production registration."""
    for gate in registered["gates"]:
        spec = gate.spec_json
        for clause in [*spec.get("pass_all", []), *spec.get("fail_any", []),
                       *spec.get("hold_if", []), *spec.get("sample", {}).values()]:
            assert resolve_definition(clause["metric"]) is not None, clause["metric"]


def test_perp_metrics_are_declared_unprovided(registered):
    """They are probe-instrument quantities: computed by the probe scripts and
    recorded against the gate, exactly like the FREEZE probe's. Declaring them
    `provided=True` would make the evaluator answer a question no provider can
    actually compute."""
    for key in ("perp_net_edge_bps_per_trade", "perp_beta_adjusted_net_edge_bps",
                "perp_incremental_cents_per_trade_vs_theta",
                "perp_probe_observations", "perp_data_coverage_pct"):
        assert REGISTRY[key].provided is False


def test_the_sample_floor_can_only_be_raised(xos_session, xos_platform):
    """`promotion_sample_floor` is the one knob the public envelope may turn. A
    lower floor would let an arm promote on a thinner sample than the reviewed
    contract asks for — the one direction an envelope must never move a
    pre-registered bar."""
    with pytest.raises(svc.ExperimentOsError, match="stricter, never weaker"):
        perp_v1.register(xos_session, actor="t",
                         promotion_sample_floor=perp_v1.SAMPLE_FLOOR - 1)


def test_a_raised_floor_reaches_every_promotion_gate(xos_session, xos_platform):
    produced = perp_v1.register(xos_session, actor="t", promotion_sample_floor=750)
    for gate in produced["gates"]:
        if gate.kind != "promotion":
            continue
        floor = next(iter(gate.spec_json["sample"].values()))
        assert floor["value"] == 750


def test_registering_twice_is_refused_not_silently_repeated(xos_session, registered):
    with pytest.raises(svc.ExperimentOsError, match="already exists"):
        perp_v1.register(xos_session, actor="t")


def test_the_package_is_registered_and_arms_nothing():
    """It has no `arm` function, so ARM_CANARY aimed at it has nothing to call —
    which is the structural reason this registration cannot expand real-money
    capability. Perps carry leverage and liquidation, semantics no risk envelope in
    this repository has ever modelled."""
    assert "perp-v1" in package_names()
    package = _packages()["perp-v1"]
    assert package.experiment_key == perp_v1.EXPERIMENT_KEY
    assert package.arm is None
    assert package.repair is None
    assert package.activation_vars == frozenset()


def test_the_thesis_document_the_contract_cites_exists():
    """A citation that does not resolve is an assertion. The version's docs point at
    the pre-registration; a test keeps the pointer honest."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    assert (root / perp_v1.THESIS_DOC).is_file()
