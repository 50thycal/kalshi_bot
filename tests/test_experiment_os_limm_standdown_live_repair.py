"""LIMM close-out (DEC-046): end `limm-smoke-live-1` so liquidity-incentive-mm can retire.

The check that matters is that the book is GENUINELY stopped — so the refusals are
tested, not just the happy path — and that the retirement then goes through the
ordinary STAND_DOWN verb in the same command batch.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot.experiment_os import experiment_commands as cmds
from kalshi_bot.experiment_os import repair_limm_standdown_live_row as repair
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.models import LiveOrder

UTC = timezone.utc
T0 = datetime(2026, 9, 17, tzinfo=UTC)
KEY, LIVE_DEP, TAG, CUTOFF = repair.TARGETS[0]


class _Runtime:
    def __init__(self, *, books=(), live=(), twins=()):
        self.mmsell_variant_list = [{"tag": t} for t in books]
        self.live_paper_twin_pairs = list(twins)
        self.live_strategy_list = list(live)


def _run(monkeypatch, *, live=("Ummsell10", "Ommsell10"), twins=()):
    monkeypatch.setattr("kalshi_bot.config.get_settings",
                        lambda: _Runtime(books=("Ummsell10", "Ommsell10"), live=live,
                                         twins=twins))


def _limm(s):
    exp = svc.create_experiment(s, key=KEY, origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="lever", now=T0)
    svc.add_arm(s, ver, arm_key="limm1side", role="treatment", strategy_tag="limm1")
    ver.control_exemption_reason = "single-book repair fixture"
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    live = svc.register_deployment(
        s, epoch, deployment_key=LIVE_DEP, stage="LIVE_CANARY", kind="live",
        arms={"limm1side": TAG}, started_at=T0)
    twin = svc.register_deployment(
        s, epoch, deployment_key="limm-smoke-twin-1", stage="LIVE_CANARY",
        kind="paper_twin", arms={"limm1side": "Alimm1_pt3"}, started_at=T0,
        twin_of=live)
    exp.state = "LIVE_CANARY"
    s.commit()
    return exp, epoch, live, twin


def _env(cid, action, payload):
    return {"command_id": cid, "action": action, "actor": "tester",
            "actor_role": "LIVE_OPS", "schema_version": 1, "payload": payload}


REPAIR = ("REPAIR_LINEAGE", {"package": "limm-standdown-live-repair", "reason": "DEC-046"})
RETIRE = ("STAND_DOWN", {"experiment": KEY, "target": "RETIRED", "approved_by": "Calvin",
                         "reason": "DEC-046"})


def test_package_declares_only_alimm1():
    pkg = cmds._packages()["limm-standdown-live-repair"]
    assert pkg.retires_tags == ("Alimm1",)
    assert [t for _, _, t, _ in repair.TARGETS] == ["Alimm1"]
    assert not {"As2best1", "As2best1_pt1", "s2best1"} & set(pkg.retires_tags)


def test_ends_only_the_live_row(xos_session, xos_platform, monkeypatch):
    _run(monkeypatch)
    exp, epoch, live, twin = _limm(xos_session)
    out = repair.repair(xos_session, actor="tester")
    assert out["ended"] == [LIVE_DEP]
    assert live.ended_at is not None
    assert twin.ended_at is None and epoch.ended_at is None
    assert exp.state == "LIVE_CANARY"
    assert repair.repair(xos_session, actor="tester")["already_repaired"] is True


def test_refuses_while_alimm1_is_allowlisted(xos_session, xos_platform, monkeypatch):
    _run(monkeypatch, live=("Ummsell10", "Alimm1", "Ommsell10"))
    _exp, _epoch, live, _twin = _limm(xos_session)
    with pytest.raises(svc.ExperimentOsError, match="limm stand-down.*armed"):
        repair.repair(xos_session, actor="tester")
    assert live.ended_at is None


def test_refuses_after_a_newer_live_order(xos_session, xos_platform, monkeypatch):
    _run(monkeypatch)
    _exp, _epoch, live, _twin = _limm(xos_session)
    xos_session.add(LiveOrder(market_ticker="KXT", strategy=TAG,
                              created_at=CUTOFF + timedelta(minutes=1)))
    xos_session.commit()
    with pytest.raises(svc.ExperimentOsError, match="after its"):
        repair.repair(xos_session, actor="tester")
    assert live.ended_at is None


def test_stand_down_alone_is_refused_on_the_open_live_row(
    xos_session, xos_platform, monkeypatch
):
    _run(monkeypatch)
    _limm(xos_session)
    out = cmds.execute_envelope(xos_session, _env("limm-retire-only", *RETIRE))
    assert out["status"] == "REJECTED", out
    assert "open LIVE deployment" in str(out)


def test_repair_then_retire_closes_the_whole_record(xos_session, xos_platform, monkeypatch):
    _run(monkeypatch)
    exp, epoch, live, twin = _limm(xos_session)
    r1 = cmds.execute_envelope(xos_session, _env("limm-repair-1", *REPAIR))
    xos_session.commit()
    assert r1["status"] == "SUCCEEDED", r1
    r2 = cmds.execute_envelope(xos_session, _env("limm-retire-1", *RETIRE))
    xos_session.commit()
    assert r2["status"] == "SUCCEEDED", r2
    xos_session.expire_all()
    assert exp.state == "RETIRED"
    assert epoch.ended_at is not None
    assert live.ended_at is not None and twin.ended_at is not None


def test_transport_rejects_while_the_allowlist_still_arms_it(
    xos_session, xos_platform, monkeypatch
):
    _run(monkeypatch, live=("Alimm1",))
    _exp, _epoch, live, _twin = _limm(xos_session)
    out = cmds.execute_envelope(xos_session, _env("limm-repair-no", *REPAIR))
    xos_session.commit()
    assert out["status"] == "REJECTED", out
    xos_session.expire_all()
    assert live.ended_at is None
