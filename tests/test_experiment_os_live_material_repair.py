"""XOS-000036: backfilling the drift-check baseline two live canaries never had.

The defect is in `docs/EXPERIMENT_OS_PLATFORM_IMPACT.md` and in
`repair_live_material_baseline`'s own docstring. What these tests exercise is the
repair's safety argument, which is narrow on purpose: it writes ONE key, derived
from the package's reviewed literals, and only after every one of them agrees
with what the row already stores. A baseline that did not describe the row would
be worse than no baseline — it would look like coverage while comparing the
wrong book.

Deliberately NOT tested here because they are not this module's to make true:
the stand-down, the drift verdict, and the resolution of any open
EXPERIMENT_CONFIG_UNVERIFIABLE event. All three are recorded by the LIVE
worker's next `runtime_config_check`, through the audited path, and are proven
in `tests/test_experiment_os_enforcement.py`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from kalshi_bot.experiment_os import enforcement as enf
from kalshi_bot.experiment_os import repair_live_material_baseline as repair
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.models import ExperimentDeploymentArm

UTC = timezone.utc
T0 = datetime(2026, 9, 2, tzinfo=UTC)


def _armed_without_baseline(s, package, *, config=None):
    """The production shape this repair exists for: a LIVE_CANARY experiment
    holding an OPEN live deployment whose config_json carries the package's flat
    keys and no `material` block."""
    exp = svc.create_experiment(s, key=f"x-{package.LIVE_DEPLOYMENT_KEY}",
                                origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="lever", now=T0)
    svc.add_arm(s, ver, arm_key="arm0", role="treatment",
                strategy_tag=package.LIVE_TAG)
    ver.control_exemption_reason = "single-book repair fixture"
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="live", started_at=T0)
    registered = package.material_config()
    if config is None:
        config = {k: v for k, v in registered.items() if k != "material"}
    dep = svc.register_deployment(
        s, epoch, deployment_key=package.LIVE_DEPLOYMENT_KEY,
        stage="LIVE_CANARY", kind="live", arms={"arm0": package.LIVE_TAG},
        started_at=T0, grandfathered=True,
    )
    dep.config_json = config
    exp.state = "LIVE_CANARY"
    s.commit()
    return exp, epoch, dep


def _both(s):
    return [_armed_without_baseline(s, pkg) for _key, pkg in repair.TARGETS]


def test_every_target_is_an_open_live_deployment_of_a_reviewed_package():
    """The repair names its rows as literals so an envelope cannot point it at a
    different book. Two, matching what production carries."""
    keys = [key for key, _pkg in repair.TARGETS]
    assert keys == ["mmsell-capacity-live-1", "mmsell-contestcap-live-2"]
    for key, package in repair.TARGETS:
        assert package.LIVE_DEPLOYMENT_KEY == key


def test_writes_the_packages_own_baseline_and_nothing_else(
    xos_session, xos_platform
):
    s = xos_session
    (_ea, epoch_a, dep_a), (_eb, _pb, dep_b) = _both(s)
    before = dict(dep_a.config_json)

    out = repair.repair(s, actor="tester")

    assert [r["deployment"] for r in out["repaired"]] == [
        k for k, _ in repair.TARGETS
    ]
    for (_key, package), dep in ((repair.TARGETS[0], dep_a),
                                 (repair.TARGETS[1], dep_b)):
        assert enf.live_material_or_none(dep.config_json) == (
            package.material_config()["material"]
        )
        assert dep.config_json["material"]["live_strategies_contains"] == [
            package.LIVE_TAG
        ]
    # Everything the row already carried survives, and nothing else moves: no
    # lifecycle state, no epoch, no deployment lifetime.
    assert {k: dep_a.config_json[k] for k in before} == before
    assert dep_a.ended_at is None
    assert epoch_a.ended_at is None


def test_is_idempotent_and_never_overwrites_an_existing_baseline(
    xos_session, xos_platform
):
    """A second run must not overwrite what a first run — or a later arming —
    established. The repair reports it rather than re-deriving it."""
    s = xos_session
    (_ea, _pa, dep_a), _b = _both(s)
    repair.repair(s, actor="tester")
    written = dict(dep_a.config_json["material"])

    again = repair.repair(s, actor="tester")

    assert again["repaired"] == []
    assert again["already_repaired"] == [k for k, _ in repair.TARGETS]
    assert dep_a.config_json["material"] == written


def test_refuses_when_the_rows_stored_facts_disagree_with_the_package(
    xos_session, xos_platform
):
    """The literals must describe THIS row. A book spec that has moved since
    arming means the reviewed shape is not the shape production is in, and
    writing a baseline anyway would assert a registration that never happened."""
    s = xos_session
    _key, package = repair.TARGETS[0]
    config = {k: v for k, v in package.material_config().items()
              if k != "material"}
    _armed_without_baseline(s, package, config={**config, "book_spec": "other:lo=9"})

    with pytest.raises(svc.ExperimentOsError, match="not the one this repair"):
        repair.repair(s, actor="tester")


def test_refuses_when_the_deployment_runs_a_different_tag(
    xos_session, xos_platform
):
    s = xos_session
    _key, package = repair.TARGETS[0]
    _exp, _epoch, dep = _armed_without_baseline(s, package)
    for link in s.scalars(select(ExperimentDeploymentArm).where(
        ExperimentDeploymentArm.deployment_id == dep.id
    )):
        link.strategy_tag = "SomeOtherTag"
    s.commit()

    with pytest.raises(svc.ExperimentOsError, match="carries arm tags"):
        repair.repair(s, actor="tester")


def test_refuses_an_ended_deployment(xos_session, xos_platform):
    """A closed deployment's registered config is history. Backfilling it would
    be inventing a baseline for evidence that already finished accruing."""
    s = xos_session
    (_ea, _pa, dep_a), _b = _both(s)
    dep_a.ended_at = datetime(2026, 9, 10, tzinfo=UTC)
    s.commit()

    with pytest.raises(svc.ExperimentOsError, match="has ended"):
        repair.repair(s, actor="tester")


def test_refuses_a_deployment_stranded_on_a_closed_epoch(
    xos_session, xos_platform
):
    s = xos_session
    (_ea, epoch_a, _da), _b = _both(s)
    epoch_a.ended_at = datetime(2026, 9, 10, tzinfo=UTC)
    s.commit()

    with pytest.raises(svc.ExperimentOsError, match="XOS-000011"):
        repair.repair(s, actor="tester")
