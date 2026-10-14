"""Acceptance evidence for `kalshi_bot/experiment_os/rain_season.py`.

Proved against the real service under NEW_ONLY enforcement:
  * it arms nothing — the only tagged deployment is PAPER;
  * the two bands are DISJOINT and KXRAIN-only, and the specs parse as the books they claim;
  * the floor is in settlement days and cannot be reached before winter is in the sample;
  * no promotion gate and no size arm exist;
  * every metric a gate names has a provider.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot.experiment_os import enforcement as enf
from kalshi_bot.experiment_os import rain_season as pkg
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


def test_two_arms_one_control(registered, xos_session):
    _exp, ver = _version(xos_session)
    arms = {a.arm_key: a for a in read.arms_for(xos_session, ver)}
    assert set(arms) == {pkg.CELL_ARM, pkg.ADJACENT_ARM}
    assert arms[pkg.CELL_ARM].role == ArmRole.CONTROL.value
    assert arms[pkg.ADJACENT_ARM].role == ArmRole.TREATMENT.value


def test_nothing_registered_here_can_reach_real_money(registered, xos_session):
    _exp, ver = _version(xos_session)
    for epoch in read.epochs_for(xos_session, ver):
        for dep in read.deployments_for(xos_session, epoch):
            assert dep.kind != DeploymentKind.LIVE.value
            assert dep.stage != LifecycleState.LIVE_CANARY.value
    probe_tags = [t for _a, t in read.deployment_arms(xos_session, registered["probe"])]
    assert probe_tags and all(t is None for t in probe_tags)
    tags = {a.arm_key: t for a, t in read.deployment_arms(xos_session, registered["paper"])}
    assert tags == {pkg.CELL_ARM: pkg.CELL_TAG, pkg.ADJACENT_ARM: pkg.ADJACENT_TAG}


def test_only_these_two_tags_gain_lineage(registered, xos_session):
    lineage = {t for t in (pkg.CELL_TAG, pkg.ADJACENT_TAG, "mmsell10", "Rmmsell1")
               if read.strategy_tag_lineage(xos_session, t)}
    assert lineage == {pkg.CELL_TAG, pkg.ADJACENT_TAG}


def test_tags_are_prefix_safe_for_live_strategies():
    """LIVE_STRATEGIES matches by PREFIX: neither tag may prefix the other."""
    a, b = pkg.CELL_TAG, pkg.ADJACENT_TAG
    assert not a.startswith(b) and not b.startswith(a)
    assert all("mmsell" in t and len(t) <= 24 for t in (a, b))


def test_book_specs_parse_as_kxrain_only_disjoint_bands(settings):
    settings.mmsell_variants = ";".join(f"{t}:{b}" for t, b in pkg.BOOK_SPECS.items())
    books = {v["tag"]: v for v in settings.mmsell_variant_list}
    assert set(books) == {pkg.CELL_TAG, pkg.ADJACENT_TAG}
    cell, adj = books[pkg.CELL_TAG], books[pkg.ADJACENT_TAG]
    assert cell["onlyx"] == ["KXRAIN"] and adj["onlyx"] == ["KXRAIN"]
    assert (cell["lo"], cell["hi"], cell["maxyes"]) == (5.0, 10.0, 7.0)
    assert (adj["lo"], adj["hi"], adj["maxyes"]) == (8.0, 14.0, 12.0)
    # Disjoint: the cell sells YES at <= 7c, so its mid is <= 7 (mid <= ask); the adjacent
    # arm needs mid >= 8.
    assert cell["maxyes"] < adj["lo"]


def test_gates_are_kill_gates_with_a_winter_spanning_day_floor(registered, xos_session):
    _exp, ver = _version(xos_session)
    gates = {g.gate_key: g for g in read.gates_for(xos_session, ver)}
    assert set(gates) == {pkg.CELL_GATE_KEY, pkg.ADJACENT_GATE_KEY}
    assert all(g.to_state != LifecycleState.LIVE_CANARY.value for g in gates.values()), \
        "no promotion is pre-authorized here"
    for key, arm in ((pkg.CELL_GATE_KEY, pkg.CELL_ARM), (pkg.ADJACENT_GATE_KEY, pkg.ADJACENT_ARM)):
        spec = gates[key].spec_json
        assert spec["sample"] == {arm: {"metric": "settled_days", "op": ">=",
                                        "value": pkg.SAMPLE_FLOOR_DAYS}}
        assert spec["max_evidence_horizon"]["value"] == pkg.HORIZON_DAYS
        assert all(c["arm"] == arm for c in spec["pass_all"] + spec["fail_any"])


def test_the_floor_cannot_be_reached_before_mid_february():
    """Arms open no earlier than the merge (Oct 2026). One settled day per calendar day at
    most, so the first permitted look is >= 120 days later and at least ~73 counted days fall
    in December-February."""
    opened = datetime(2026, 10, 14, tzinfo=timezone.utc)
    earliest_look = opened + timedelta(days=pkg.SAMPLE_FLOOR_DAYS)
    assert earliest_look >= datetime(2027, 2, 10, tzinfo=timezone.utc)
    winter_days = (earliest_look - datetime(2026, 12, 1, tzinfo=timezone.utc)).days
    assert winter_days >= 70
    assert pkg.EARLY_FAIL_FLOOR_DAYS < pkg.SAMPLE_FLOOR_DAYS < pkg.HORIZON_DAYS


def test_every_metric_the_gates_name_has_a_provider(registered):
    for gate in registered["gates"].values():
        spec = gate.spec_json
        named = {c["metric"] for c in spec["pass_all"] + spec["fail_any"]}
        named |= {c["metric"] for c in spec["sample"].values()}
        named |= {c["min_evidence"]["metric"] for c in spec["fail_any"] if "min_evidence" in c}
        for metric in named:
            assert metric in REGISTRY and REGISTRY[metric].provided, metric


def test_pass_bar_is_above_the_fail_bar():
    assert pkg.PASS_CENTS > pkg.FAIL_CENTS_AT_FLOOR > 0


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


def test_package_is_registered_with_its_tags_and_arms_nothing():
    from kalshi_bot.experiment_os import experiment_commands as ec

    p = ec._packages()["mmsell-rain-season"]
    assert p.experiment_key == pkg.EXPERIMENT_KEY
    assert set(p.strategy_tags) == {pkg.CELL_TAG, pkg.ADJACENT_TAG}
    assert not p.activation_vars, "a package that arms nothing declares no activation vars"
    assert p.arm is None
