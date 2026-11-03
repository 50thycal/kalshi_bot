"""Acceptance evidence for `kalshi_bot/experiment_os/mentions_forward.py`.

Proved against the real service under NEW_ONLY enforcement:
  * it arms nothing — the only tagged deployment is PAPER;
  * the book spec is the retired base mmsell10 book's, verbatim, on a fresh prefix-safe tag;
  * only the new tag gains lineage (the retired `mmsell10` does not come back);
  * the mentions forward read is recorded as monitoring, never a verdict;
  * every metric the gate names has a provider.
"""

from __future__ import annotations

import pytest

from kalshi_bot.experiment_os import enforcement as enf
from kalshi_bot.experiment_os import mentions_forward as pkg
from kalshi_bot.experiment_os import read
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.lifecycle import ArmRole, DeploymentKind, LifecycleState
from kalshi_bot.experiment_os.metrics import REGISTRY


@pytest.fixture(autouse=True)
def _fresh_resolver():
    enf.reset_for_tests()
    yield
    enf.reset_for_tests()


def _new_only(session):
    enf.record_enforcement_change(
        session, mode="NEW_ONLY", actor="operator", reason="test",
        cutover_id="test-new-only", readiness={"ok": True, "checks": {}},
    )


@pytest.fixture
def registered(xos_session, xos_platform):
    _new_only(xos_session)
    out = pkg.register(xos_session, actor="research-lab")
    xos_session.commit()
    return out


def _version(session):
    exp = read.get_experiment(session, pkg.EXPERIMENT_KEY)
    return exp, read.latest_version(session, exp)


def test_registration_lands_at_paper_with_a_frozen_v1(registered, xos_session):
    exp, ver = _version(xos_session)
    assert exp.state == LifecycleState.PAPER.value
    assert ver.version == 1 and ver.frozen_at is not None


def test_one_control_arm(registered, xos_session):
    _exp, ver = _version(xos_session)
    arms = {a.arm_key: a for a in read.arms_for(xos_session, ver)}
    assert set(arms) == {pkg.BASE_ARM}
    assert arms[pkg.BASE_ARM].role == ArmRole.CONTROL.value


def test_nothing_registered_here_can_reach_real_money(registered, xos_session):
    _exp, ver = _version(xos_session)
    for epoch in read.epochs_for(xos_session, ver):
        for dep in read.deployments_for(xos_session, epoch):
            assert dep.kind != DeploymentKind.LIVE.value
            assert dep.stage != LifecycleState.LIVE_CANARY.value
    probe_tags = [t for _a, t in read.deployment_arms(xos_session, registered["probe"])]
    assert probe_tags and all(t is None for t in probe_tags)
    tags = {a.arm_key: t for a, t in read.deployment_arms(xos_session, registered["paper"])}
    assert tags == {pkg.BASE_ARM: pkg.BASE_TAG}


def test_only_the_new_tag_gains_lineage(registered, xos_session):
    lineage = {t for t in (pkg.BASE_TAG, pkg.PREDECESSOR_TAG, "Gmmsell0", "Tmmsell2")
               if read.strategy_tag_lineage(xos_session, t)}
    assert lineage == {pkg.BASE_TAG}


def test_tag_is_prefix_safe_for_live_strategies(settings):
    """LIVE_STRATEGIES matches by PREFIX: no configured tag may prefix the new one, nor it them."""
    tag = pkg.BASE_TAG
    assert "mmsell" in tag and len(tag) <= 24
    for other in [b["tag"] for b in settings.mmsell_variant_list] + ["Xmmsell10", "Ymmsell10",
                                                                     "Alimm1", "mmsell10"]:
        assert not tag.startswith(other) and not other.startswith(tag), other


def test_book_spec_is_the_retired_base_book_verbatim(settings):
    assert pkg.BASE_PARAMS == "lo=5,hi=10,maxyes=7"
    settings.mmsell_variants = ";".join(f"{t}:{b}" for t, b in pkg.BOOK_SPECS.items())
    books = {v["tag"]: v for v in settings.mmsell_variant_list}
    assert set(books) == {pkg.BASE_TAG}
    cell = books[pkg.BASE_TAG]
    assert (cell["lo"], cell["hi"], cell["maxyes"]) == (5.0, 10.0, 7.0)
    # Same parse as the predecessor's spec, so the copy selects exactly what mmsell10 did.
    settings.mmsell_variants = f"{pkg.PREDECESSOR_TAG}:{pkg.BASE_PARAMS}"
    old = settings.mmsell_variant_list[0]
    assert {k: v for k, v in cell.items() if k != "tag"} == \
        {k: v for k, v in old.items() if k != "tag"}


def test_gate_is_a_kill_gate_with_no_promotion(registered, xos_session):
    _exp, ver = _version(xos_session)
    gates = {g.gate_key: g for g in read.gates_for(xos_session, ver)}
    assert set(gates) == {pkg.BASE_GATE_KEY}
    gate = gates[pkg.BASE_GATE_KEY]
    assert gate.to_state != LifecycleState.LIVE_CANARY.value, "no promotion is pre-authorized"
    spec = gate.spec_json
    assert spec["sample"] == {pkg.BASE_ARM: {"metric": "settled_days", "op": ">=",
                                             "value": pkg.SAMPLE_FLOOR_DAYS}}
    assert spec["max_evidence_horizon"]["value"] == pkg.HORIZON_DAYS
    assert all(c["arm"] == pkg.BASE_ARM for c in spec["pass_all"] + spec["fail_any"])
    assert pkg.EARLY_FAIL_FLOOR_DAYS < pkg.SAMPLE_FLOOR_DAYS < pkg.HORIZON_DAYS
    assert pkg.PASS_CENTS > pkg.FAIL_CENTS


def test_the_mentions_read_is_monitoring_not_a_verdict(registered, xos_session):
    _exp, ver = _version(xos_session)
    text = ver.monitoring_json["mentions_forward_read"]
    assert "never a verdict" in text and "DEC-041" in text
    assert pkg.PREDECESSOR_TAG in text and pkg.BASE_TAG in text
    assert f"{pkg.MENTIONS_FIRST_LOOK} settled" in text


def test_every_metric_the_gate_names_has_a_provider(registered):
    for gate in registered["gates"].values():
        spec = gate.spec_json
        named = {c["metric"] for c in spec["pass_all"] + spec["fail_any"]}
        named |= {c["metric"] for c in spec["sample"].values()}
        named |= {c["min_evidence"]["metric"] for c in spec["fail_any"] if "min_evidence" in c}
        for metric in named:
            assert metric in REGISTRY and REGISTRY[metric].provided, metric


def test_an_envelope_may_raise_the_floor_but_never_lower_it(xos_session, xos_platform):
    _new_only(xos_session)
    with pytest.raises(svc.ExperimentOsError, match="stricter, never weaker"):
        pkg.register(xos_session, actor="research-lab",
                     promotion_sample_floor=pkg.SAMPLE_FLOOR_DAYS - 1)
    with pytest.raises(svc.ExperimentOsError, match="horizon"):
        pkg.register(xos_session, actor="research-lab",
                     promotion_sample_floor=pkg.HORIZON_DAYS)


def test_registering_twice_is_refused(registered, xos_session):
    with pytest.raises(svc.ExperimentOsError, match="already exists"):
        pkg.register(xos_session, actor="research-lab")


def test_package_is_registered_with_its_tag_and_arms_nothing():
    from kalshi_bot.experiment_os import experiment_commands as ec

    p = ec._packages()["mmsell-mentions-forward"]
    assert p.experiment_key == pkg.EXPERIMENT_KEY
    assert set(p.strategy_tags) == {pkg.BASE_TAG}
    assert not p.activation_vars, "a package that arms nothing declares no activation vars"
    assert p.arm is None
