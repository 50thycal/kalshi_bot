"""XOS-000033: restoring `mmsell9` on a fresh epoch of `mmsell-price-ceiling` v1.

The package's job is small — close one epoch, open the next, put one tag back on
it — and its whole safety argument is the set of states in which it REFUSES. A
package that opened an epoch on a version whose books were still running, or that
put a second active arm on a tag the resolver already resolves, would break more
than the silence it is fixing. So the refusals get the coverage, not the happy
path.

The boundary is the other load-bearing detail: e1 must close at the instant it
really stopped, not at "now", or the record grows a 9.6-day interval during which
the epoch was open and nothing was in it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from kalshi_bot.experiment_os import restore_mmsell9_epoch as restore
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.models import (
    ExperimentDeployment,
    ExperimentDeploymentArm,
    ExperimentEpoch,
    ExperimentStateTransition,
)

UTC = timezone.utc
T0 = datetime(2026, 8, 16, 14, 14, 43, 720928, tzinfo=UTC)
DARK = restore.DARK_SINCE


def _ceiling(session, *, prior_ended_at=DARK, state="LIVE_CANARY"):
    """`mmsell-price-ceiling` v1 in the shape production is actually in.

    v1 frozen, epoch 1 OPEN, and the mmsell9 carrier already ENDED at the instant
    the capacity successor ended it. The experiment is LIVE_CANARY because it has
    long since moved on to v2 — restoring a v1 arm beside that is the point, not
    an accident.
    """
    exp = svc.create_experiment(session, key=restore.EXPERIMENT_KEY, origin="operator")
    ver = svc.create_experiment_version(
        session, exp, hypothesis="cheap-cell price ceiling",
        independent_variable="entry price ceiling", now=T0,
    )
    svc.add_arm(
        session, ver, arm_key=restore.ARM_KEY, role="secondary",
        strategy_tag=restore.PAPER_TAG,
    )
    svc.add_arm(session, ver, arm_key="mmsell10", role="secondary",
                strategy_tag="mmsell10")
    ver.control_exemption_reason = "imported two-arm legacy contract"
    svc.freeze_version(session, ver, now=T0)
    epoch = svc.open_epoch(session, ver, reason="initial", started_at=T0)
    prior = svc.register_deployment(
        session, epoch, deployment_key=restore.PRIOR_DEPLOYMENT_KEY,
        stage="PAPER", kind="paper", arms={restore.ARM_KEY: restore.PAPER_TAG},
        started_at=T0,
    )
    if prior_ended_at is not None:
        prior.ended_at = prior_ended_at
    exp.state = state
    session.flush()
    return exp, ver, epoch, prior


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def _aware(value):
    """SQLite hands back naive datetimes; Postgres aware ones."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def test_closes_e1_at_the_measured_instant_and_opens_e2_now(
    xos_session, xos_platform
):
    s = xos_session
    _exp, ver, e1, _prior = _ceiling(s)

    out = restore.register(s, actor="tester")

    assert out["already_restored"] is False
    assert out["closed_epoch"] == 1
    assert out["epoch_number"] == 2
    # The boundary is MEASURED. Closing at "now" would record an epoch that was
    # open for 9.6 days with nothing running in it.
    assert _aware(e1.ended_at) == DARK
    e2 = s.scalar(
        select(ExperimentEpoch).where(
            ExperimentEpoch.version_id == ver.id,
            ExperimentEpoch.epoch_number == 2,
        )
    )
    assert e2 is not None and e2.ended_at is None
    # The gap between the epochs IS the outage, not something to paper over.
    assert _aware(e2.started_at) > DARK


def test_registers_mmsell9_alone_on_the_new_epoch(xos_session, xos_platform):
    s = xos_session
    _ceiling(s)

    restore.register(s, actor="tester")

    dep = s.scalar(
        select(ExperimentDeployment).where(
            ExperimentDeployment.deployment_key == restore.NEW_DEPLOYMENT_KEY
        )
    )
    assert dep is not None and dep.ended_at is None
    assert dep.kind == "paper"
    tags = sorted(
        link.strategy_tag
        for link in s.scalars(
            select(ExperimentDeploymentArm).where(
                ExperimentDeploymentArm.deployment_id == dep.id
            )
        )
    )
    assert tags == [restore.PAPER_TAG]


def test_touches_no_gate_and_records_no_transition(xos_session, xos_platform):
    s = xos_session
    exp, _ver, _e1, _prior = _ceiling(s)
    before = s.scalar(
        select(func.count()).select_from(ExperimentStateTransition).where(
            ExperimentStateTransition.experiment_id == exp.id
        )
    )

    restore.register(s, actor="tester")

    after = s.scalar(
        select(func.count()).select_from(ExperimentStateTransition).where(
            ExperimentStateTransition.experiment_id == exp.id
        )
    )
    assert after == before
    assert exp.state == "LIVE_CANARY"


def test_is_idempotent_and_does_not_open_a_third_epoch(xos_session, xos_platform):
    s = xos_session
    _exp, ver, _e1, _prior = _ceiling(s)

    restore.register(s, actor="tester")
    epochs_after_first = s.scalar(
        select(func.count()).select_from(ExperimentEpoch).where(
            ExperimentEpoch.version_id == ver.id
        )
    )

    again = restore.register(s, actor="tester")

    assert again["already_restored"] is True
    assert again["epoch_number"] == 2
    assert s.scalar(
        select(func.count()).select_from(ExperimentEpoch).where(
            ExperimentEpoch.version_id == ver.id
        )
    ) == epochs_after_first


# ---------------------------------------------------------------------------
# The refusals — the whole safety argument
# ---------------------------------------------------------------------------


def test_refuses_when_the_carrier_is_still_open(xos_session, xos_platform):
    s = xos_session
    _ceiling(s, prior_ended_at=None)

    with pytest.raises(svc.ExperimentOsError, match="still OPEN"):
        restore.register(s, actor="tester")


def test_refuses_when_the_carrier_ended_at_a_different_instant(
    xos_session, xos_platform
):
    """If production did not end it when we believe, the belief is what is wrong."""
    s = xos_session
    _ceiling(s, prior_ended_at=DARK + timedelta(seconds=1))

    with pytest.raises(svc.ExperimentOsError, match="not the reviewed"):
        restore.register(s, actor="tester")


def test_refuses_when_something_is_still_running_in_the_epoch(
    xos_session, xos_platform
):
    """The premise is that the book is dark. If e1 still runs anything, closing it
    would stop that too — `close_epoch` cascades."""
    s = xos_session
    _exp, _ver, e1, _prior = _ceiling(s)
    svc.register_deployment(
        s, e1, deployment_key="mmsell-ceiling-paper-something-else",
        stage="PAPER", kind="paper", arms={"mmsell10": "mmsell10"}, started_at=T0,
    )

    with pytest.raises(svc.ExperimentOsError, match="still runs"):
        restore.register(s, actor="tester")


def test_refuses_when_the_tag_already_resolves_somewhere_active(
    xos_session, xos_platform
):
    """Two active arms on one tag is the ambiguity the resolver refuses outright —
    restoring into it would stop the book a second way."""
    s = xos_session
    exp = svc.create_experiment(s, key="other-book", origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="lever", now=T0)
    svc.add_arm(s, ver, arm_key="a0", role="treatment", strategy_tag=restore.PAPER_TAG)
    ver.control_exemption_reason = "fixture"
    svc.freeze_version(s, ver, now=T0)
    other_epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    svc.register_deployment(
        s, other_epoch, deployment_key="other-paper-1", stage="PAPER", kind="paper",
        arms={"a0": restore.PAPER_TAG}, started_at=T0,
    )
    _ceiling(s)

    with pytest.raises(svc.ExperimentOsError, match="already resolves"):
        restore.register(s, actor="tester")


def test_refuses_a_retired_experiment(xos_session, xos_platform):
    s = xos_session
    _ceiling(s, state="RETIRED")

    with pytest.raises(svc.ExperimentOsError, match="RETIRED"):
        restore.register(s, actor="tester")


def test_refuses_a_sample_floor_because_it_registers_no_gate(
    xos_session, xos_platform
):
    s = xos_session
    _ceiling(s)

    with pytest.raises(svc.ExperimentOsError, match="promotion_sample_floor"):
        restore.register(s, actor="tester", promotion_sample_floor=50)


def test_refuses_when_the_experiment_is_absent(xos_session, xos_platform):
    with pytest.raises(svc.ExperimentOsError, match="not registered"):
        restore.register(xos_session, actor="tester")


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_package_is_registered_and_declares_no_repair():
    from kalshi_bot.experiment_os import experiment_commands as xc

    pkg = xc._packages()["mmsell9-restore-v1-e2"]
    assert pkg.experiment_key == restore.EXPERIMENT_KEY
    assert pkg.register is restore.register
    # Not a REPAIR_LINEAGE: that action may reach no epoch, and this cuts one.
    assert pkg.repair is None
