"""Acceptance evidence for `kalshi_bot/experiment_os/temperature_season.py`.

Proved against the real service under NEW_ONLY enforcement:
  * it arms nothing — the only tagged deployment is PAPER;
  * the book spec parses as the rain cell restricted to temperature, opted in through `optin=`;
  * the floor is in settlement days and cannot be reached before winter is in the sample;
  * no promotion gate and no size arm exist;
  * every metric the gate names has a provider.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot.experiment_os import enforcement as enf
from kalshi_bot.experiment_os import read
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os import temperature_season as pkg
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
    assert set(arms) == {pkg.CELL_ARM}
    assert arms[pkg.CELL_ARM].role == ArmRole.CONTROL.value


def test_nothing_registered_here_can_reach_real_money(registered, xos_session):
    _exp, ver = _version(xos_session)
    for epoch in read.epochs_for(xos_session, ver):
        for dep in read.deployments_for(xos_session, epoch):
            assert dep.kind != DeploymentKind.LIVE.value
            assert dep.stage != LifecycleState.LIVE_CANARY.value
    probe_tags = [t for _a, t in read.deployment_arms(xos_session, registered["probe"])]
    assert probe_tags and all(t is None for t in probe_tags)
    tags = {a.arm_key: t for a, t in read.deployment_arms(xos_session, registered["paper"])}
    assert tags == {pkg.CELL_ARM: pkg.CELL_TAG}


def test_only_this_tag_gains_lineage(registered, xos_session):
    lineage = {t for t in (pkg.CELL_TAG, "mmsell10", "Rainmmsell1", "Tmmsell6")
               if read.strategy_tag_lineage(xos_session, t)}
    assert lineage == {pkg.CELL_TAG}


def test_tag_is_prefix_safe_for_live_strategies(settings):
    """LIVE_STRATEGIES matches by PREFIX: no configured tag may prefix the new one, nor it them."""
    tag = pkg.CELL_TAG
    assert "mmsell" in tag and len(tag) <= 24
    for other in (b["tag"] for b in settings.mmsell_variant_list):
        assert not tag.startswith(other) and not other.startswith(tag), other


def test_book_spec_parses_as_the_rain_cell_on_temperature_only(settings):
    settings.mmsell_variants = ";".join(f"{t}:{b}" for t, b in pkg.BOOK_SPECS.items())
    books = {v["tag"]: v for v in settings.mmsell_variant_list}
    assert set(books) == {pkg.CELL_TAG}
    cell = books[pkg.CELL_TAG]
    assert (cell["lo"], cell["hi"], cell["maxyes"]) == (5.0, 10.0, 7.0)
    assert cell["htcmax"] == 48.0
    assert cell["optin"] == ["KXHIGH", "KXLOW"] and cell["only"] == ["KXHIGH", "KXLOW"]
    # The opt-in names only prefixes the global skip list drops; the skip list itself is
    # untouched, which is what keeps every other book off temperature.
    assert set(cell["optin"]) <= set(settings.mmsell_skip_series_list)
    assert {"KXHIGH", "KXLOW"} <= set(settings.mmsell_skip_series_list)


def test_gate_is_a_kill_gate_with_a_winter_spanning_day_floor(registered, xos_session):
    _exp, ver = _version(xos_session)
    gates = {g.gate_key: g for g in read.gates_for(xos_session, ver)}
    assert set(gates) == {pkg.CELL_GATE_KEY}
    gate = gates[pkg.CELL_GATE_KEY]
    assert gate.to_state != LifecycleState.LIVE_CANARY.value, "no promotion is pre-authorized"
    spec = gate.spec_json
    assert spec["sample"] == {pkg.CELL_ARM: {"metric": "settled_days", "op": ">=",
                                             "value": pkg.SAMPLE_FLOOR_DAYS}}
    assert spec["max_evidence_horizon"]["value"] == pkg.HORIZON_DAYS
    assert all(c["arm"] == pkg.CELL_ARM for c in spec["pass_all"] + spec["fail_any"])


def test_the_floor_cannot_be_reached_before_mid_february():
    """The arm opens no earlier than the merge (mid-October 2026); one settled day per calendar
    day at most, so the first permitted look holds well over the pre-registered 30 winter days."""
    opened = datetime(2026, 10, 15, tzinfo=timezone.utc)
    earliest_look = opened + timedelta(days=pkg.SAMPLE_FLOOR_DAYS)
    assert earliest_look >= datetime(2027, 2, 10, tzinfo=timezone.utc)
    winter_days = (earliest_look - datetime(2026, 12, 1, tzinfo=timezone.utc)).days
    assert winter_days >= pkg.WINTER_MIN_DAYS
    assert pkg.EARLY_FAIL_FLOOR_DAYS < pkg.SAMPLE_FLOOR_DAYS < pkg.HORIZON_DAYS


def test_the_winter_stop_is_recorded_as_monitoring_not_a_verdict(registered, xos_session):
    _exp, ver = _version(xos_session)
    winter = ver.monitoring_json["winter_stop"]
    assert "never a verdict" in winter and f"{pkg.WINTER_STOP_CENTS:+.0f}c" in winter


def test_every_metric_the_gate_names_has_a_provider(registered):
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


def test_package_is_registered_with_its_tag_and_arms_nothing():
    from kalshi_bot.experiment_os import experiment_commands as ec

    p = ec._packages()["mmsell-temperature-season"]
    assert p.experiment_key == pkg.EXPERIMENT_KEY
    assert set(p.strategy_tags) == {pkg.CELL_TAG}
    assert not p.activation_vars, "a package that arms nothing declares no activation vars"
    assert p.arm is None
