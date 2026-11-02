"""MMSELL close-out part 2: closing mmsell-price-ceiling's stale v1/e2 (mmsell9)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kalshi_bot.experiment_os import experiment_commands as cmds
from kalshi_bot.experiment_os import repair_mmsell9_stale_epoch as repair
from kalshi_bot.experiment_os import service as svc

T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)


class _Runtime:
    def __init__(self, books=()):
        self.mmsell_variant_list = [{"tag": t} for t in books]
        self.live_paper_twin_pairs = []
        self.live_strategy_list = ["Xmmsell10", "Ymmsell10"]


def _version(s, exp, n, tag, *, open_epoch=True):
    ver = svc.create_experiment_version(
        s, exp, hypothesis=f"h{n}", independent_variable="lever", now=T0,
        change_reason=None if n == 1 else "fixture successor")
    svc.add_arm(s, ver, arm_key="arm0", role="treatment", strategy_tag=tag)
    ver.control_exemption_reason = "fixture"
    svc.freeze_version(s, ver, now=T0)
    ep1 = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    svc.close_epoch(s, ep1, ended_at=T0)
    ep2 = svc.open_epoch(s, ver, reason="second", started_at=T0)
    return ver, ep2


def _world(s, *, extra_dep=False):
    exp = svc.create_experiment(s, key=repair.EXPERIMENT_KEY, origin="operator")
    v1, e2 = _version(s, exp, 1, repair.TAG)
    dep = svc.register_deployment(
        s, e2, deployment_key=repair.DEPLOYMENT_KEY, stage="PAPER", kind="paper",
        arms={"arm0": repair.TAG}, started_at=T0)
    if extra_dep:
        svc.register_deployment(
            s, e2, deployment_key="something-else", stage="PAPER", kind="paper",
            arms={"arm0": None}, started_at=T0)
    _version(s, exp, 2, "Cmmsell10")
    exp.state = "LIVE_CANARY"
    s.commit()
    return exp, e2, dep


def _env(cid):
    return {"command_id": cid, "action": "REPAIR_LINEAGE", "actor": "t",
            "actor_role": "LIVE_OPS", "schema_version": 1,
            "payload": {"package": "mmsell9-stale-epoch-repair", "reason": "test"}}


def test_closes_v1_e2_and_its_deployment_only(xos_session, xos_platform):
    s = xos_session
    exp, e2, dep = _world(s)
    out = repair.repair(s, actor="t")
    assert out["closed_epoch"] == "v1/e2"
    assert e2.ended_at is not None and dep.ended_at is not None
    assert exp.state == "LIVE_CANARY"
    assert repair.repair(s, actor="t")["already_repaired"] is True


def test_refuses_an_unexpected_deployment(xos_session, xos_platform):
    s = xos_session
    _exp, e2, _dep = _world(s, extra_dep=True)
    with pytest.raises(svc.ExperimentOsError, match="unexpected open deployments"):
        repair.repair(s, actor="t")
    assert e2.ended_at is None


def test_refuses_when_not_live_canary(xos_session, xos_platform):
    s = xos_session
    exp, e2, _ = _world(s)
    exp.state = "PAUSED"
    s.commit()
    with pytest.raises(svc.ExperimentOsError, match="not LIVE_CANARY"):
        repair.repair(s, actor="t")


def test_transport_needs_mmsell9_out_of_MMSELL_VARIANTS(
    xos_session, xos_platform, monkeypatch
):
    s = xos_session
    _exp, e2, _ = _world(s)
    monkeypatch.setattr("kalshi_bot.config.get_settings",
                        lambda: _Runtime(books=("mmsell9", "mmsell10")))
    out = cmds.execute_envelope(s, _env("mm9-close-no"))
    s.commit()
    assert out["status"] == "REJECTED" and "mmsell9" in out["error"]
    s.expire_all()
    assert e2.ended_at is None

    monkeypatch.setattr("kalshi_bot.config.get_settings",
                        lambda: _Runtime(books=("mmsell10",)))
    out = cmds.execute_envelope(s, _env("mm9-close-ok"))
    s.commit()
    assert out["status"] == "SUCCEEDED", out


def test_package_declares_only_mmsell9():
    pkg = cmds._packages()["mmsell9-stale-epoch-repair"]
    assert pkg.retires_tags == ("mmsell9",)
