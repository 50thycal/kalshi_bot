"""`package-preflight` is the mechanical half of "meets all the criteria".

`docs/STANDING_AUTHORIZATIONS.md` lets a paper-scope request run end to end
without per-step confirmation — but only when the criteria hold, and the
criteria therefore have to be computed rather than judged. These tests pin
what the read says in the three states that matter: a fresh registration,
a tag another experiment already carries, and a retired experiment.

It is a READ. The last test asserts the report never writes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from kalshi_bot.experiment_os import correlation_cap as cc
from kalshi_bot.experiment_os import preflight
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.models import (
    ExperimentDeployment,
    ExperimentStateTransition,
)

PKG = "mmsell-correlation-cap"
T0 = datetime.now(timezone.utc) - timedelta(days=3)


def _checks(report):
    return {c["check"]: c for c in report["checks"]}


def test_unknown_package_is_no_go_and_names_the_known_ones(xos_session):
    report = preflight.package_preflight(xos_session, "not-a-package")
    assert report["verdict"] == "NO-GO"
    assert report["failed"] == ["package_known"]
    assert PKG in _checks(report)["package_known"]["detail"]
    assert "envelope" not in report


def test_fresh_registration_is_go_and_prints_the_envelope(xos_session, xos_platform):
    report = preflight.package_preflight(xos_session, PKG)
    checks = _checks(report)
    assert report["verdict"] == "GO", report["failed"]
    assert checks["experiment"]["detail"].endswith("a fresh registration")
    assert checks["tag_collisions"]["ok"] is True
    assert checks["tag_collisions"]["tags"] == [cc.CONTROL_TAG, cc.CAPPED_TAG]
    env = report["envelope"]
    assert env["action"] == "REGISTER_PACKAGE"
    assert env["payload"]["package"] == PKG
    assert env["payload"]["approved_by"] == "<operator>"
    assert preflight._ID_RE.match(env["command_id"])
    # the ops-channel request wraps the envelope verbatim as one string
    assert report["env_request"]["type"] == "env"
    assert PKG in report["env_request"]["set"]["EXPERIMENT_OS_EXPERIMENT_COMMAND"]
    rendered = preflight.render(report)
    assert "VERDICT: GO" in rendered and "package-preflight" not in rendered


def test_no_platform_snapshot_is_no_go(xos_session):
    report = preflight.package_preflight(xos_session, PKG)
    assert report["verdict"] == "NO-GO"
    assert "platform_snapshot_complete" in report["failed"]


def _other_experiment_carrying(session, tag):
    exp = svc.create_experiment(session, key="someone-else", origin="operator")
    ver = svc.create_experiment_version(
        session, exp, hypothesis="h", control_required=False,
        control_exemption_reason="test", now=T0)
    svc.add_arm(session, ver, arm_key="a", role="secondary", strategy_tag=tag)
    svc.freeze_version(session, ver, now=T0)
    epoch = svc.open_epoch(session, ver, reason="paper", started_at=T0)
    svc.register_deployment(
        session, epoch, deployment_key="someone-else-paper", stage="PAPER",
        kind="paper", arms={"a": tag}, started_at=T0)
    session.commit()
    return exp


def test_a_tag_carried_by_another_experiment_is_a_collision(xos_session, xos_platform):
    _other_experiment_carrying(xos_session, cc.CONTROL_TAG)
    report = preflight.package_preflight(xos_session, PKG)
    assert report["verdict"] == "NO-GO"
    assert report["failed"] == ["tag_collisions"]
    assert "someone-else" in _checks(report)["tag_collisions"]["detail"]


def test_a_retired_experiment_is_terminal(xos_session, xos_platform):
    exp = svc.create_experiment(xos_session, key=cc.EXPERIMENT_KEY, origin="operator")
    svc.transition_experiment(xos_session, exp, "RETIRED", actor="operator",
                              reason="over")
    xos_session.commit()
    report = preflight.package_preflight(xos_session, PKG)
    assert report["verdict"] == "NO-GO"
    assert report["failed"] == ["experiment"]


def test_an_already_registered_experiment_is_informational_not_fatal(
    xos_session, xos_platform
):
    cc.register(xos_session, actor="operator")
    xos_session.commit()
    report = preflight.package_preflight(xos_session, PKG)
    exp_check = _checks(report)["experiment"]
    assert exp_check["ok"] is None and "SUCCESSOR" in exp_check["detail"]
    # its own tags are not a collision with itself
    assert _checks(report)["tag_collisions"]["ok"] is True
    assert report["verdict"] == "GO"


def test_preflight_writes_nothing(xos_session, xos_platform):
    before = (
        xos_session.scalar(select(func.count()).select_from(ExperimentDeployment)),
        xos_session.scalar(select(func.count()).select_from(ExperimentStateTransition)),
    )
    preflight.package_preflight(xos_session, PKG)
    xos_session.commit()
    after = (
        xos_session.scalar(select(func.count()).select_from(ExperimentDeployment)),
        xos_session.scalar(select(func.count()).select_from(ExperimentStateTransition)),
    )
    assert before == after == (0, 0)


def test_list_packages_declares_verbs_and_tags():
    rows = {r["package"]: r for r in preflight.list_packages()}
    assert rows[PKG]["verbs"] == ["register"]
    assert rows[PKG]["strategy_tags"] == [cc.CONTROL_TAG, cc.CAPPED_TAG]
    assert "arm" in rows["mmsell10-canary"]["verbs"]
