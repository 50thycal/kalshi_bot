"""MMSELL close-out: ending the live rows of seven stood-down canaries.

The safety argument is that every book is GENUINELY stopped, so these tests drive
the checks that establish it — the refusals — not just the happy path. Closing the
record of a book that is actually trading would be far worse than the state this
fixes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot.experiment_os import experiment_commands as cmds
from kalshi_bot.experiment_os import repair_mmsell_standdown_live_rows as repair
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.models import LiveOrder

UTC = timezone.utc
T0 = datetime(2026, 8, 1, tzinfo=UTC)


class _FakeSettings:
    def __init__(self, allowlist):
        self.live_strategy_list = list(allowlist)


def _arm(monkeypatch, *tags):
    monkeypatch.setattr("kalshi_bot.config.get_settings", lambda: _FakeSettings(tags))


@pytest.fixture(autouse=True)
def _production_allowlist(monkeypatch):
    """Default to the allowlist production actually runs: the successors only."""
    _arm(monkeypatch, "Xmmsell10", "Alimm1", "Ymmsell10")


def _live_experiment(s, key, deployment_key, tag):
    exp = svc.create_experiment(s, key=key, origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="lever", now=T0)
    svc.add_arm(s, ver, arm_key="arm0", role="treatment", strategy_tag=tag)
    ver.control_exemption_reason = "single-book repair fixture"
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    dep = svc.register_deployment(
        s, epoch, deployment_key=deployment_key, stage="LIVE_CANARY", kind="live",
        arms={"arm0": tag}, started_at=T0, grandfathered=True,
    )
    exp.state = "LIVE_CANARY"  # set as the importer/arming path left it
    return exp, ver, epoch, dep


def _all_targets(s, skip: int | None = None):
    out = {}
    for i, (key, dep_key, tag, _cutoff) in enumerate(repair.TARGETS):
        if i == skip:
            continue
        out[tag] = _live_experiment(s, key, dep_key, tag)
    s.commit()
    return out


# --- happy path ---------------------------------------------------------------


def test_ends_all_seven_live_rows_and_nothing_else(xos_session, xos_platform):
    s = xos_session
    books = _all_targets(s)

    out = repair.repair(s, actor="tester")

    assert out["already_repaired"] is False
    assert out["ended"] == sorted(d for _, d, _, _ in repair.TARGETS)
    for exp, _ver, epoch, dep in books.values():
        assert dep.ended_at is not None
        # A repair closes no epoch and moves no lifecycle state.
        assert epoch.ended_at is None
        assert exp.state == "LIVE_CANARY"


def test_is_idempotent(xos_session, xos_platform):
    s = xos_session
    books = _all_targets(s)
    repair.repair(s, actor="tester")
    first = {t: b[3].ended_at for t, b in books.items()}

    again = repair.repair(s, actor="tester")

    assert again["already_repaired"] is True
    assert again["ended"] == []
    assert {t: b[3].ended_at for t, b in books.items()} == first


def test_history_up_to_each_cutoff_is_the_expected_state(xos_session, xos_platform):
    s = xos_session
    books = _all_targets(s)
    for _, _, tag, cutoff in repair.TARGETS:
        s.add(LiveOrder(market_ticker=f"KX-{tag}", strategy=tag,
                        created_at=cutoff - timedelta(seconds=1)))
    s.commit()

    repair.repair(s, actor="tester")

    assert all(b[3].ended_at is not None for b in books.values())


def test_package_is_registered_as_a_repair(xos_session, xos_platform):
    pkg = cmds._packages()["mmsell-standdown-live-repair"]
    assert pkg.repair is repair.repair


def test_never_reaches_the_running_successors():
    tags = {t for _, _, t, _ in repair.TARGETS}
    assert not tags & {"Xmmsell10", "Ymmsell10", "Smmsell10", "Zmmsell10"}
    assert not any(t.endswith("_pt4") or t.endswith("_pt3") for t in tags)


# --- refusals ------------------------------------------------------------------


@pytest.mark.parametrize("armed", ["Kmmsell10", "Kmm"])  # exact and PREFIX
def test_refuses_when_the_allowlist_arms_any_tag(
    xos_session, xos_platform, monkeypatch, armed
):
    s = xos_session
    books = _all_targets(s)
    _arm(monkeypatch, "Xmmsell10", armed)

    with pytest.raises(svc.ExperimentOsError, match="armed by the runtime allowlist"):
        repair.repair(s, actor="tester")
    assert all(b[3].ended_at is None for b in books.values())


def test_refuses_when_one_tag_traded_after_ITS_OWN_cutoff(xos_session, xos_platform):
    """Per-tag cutoffs: an Nmmsell10 order dated before Kmmsell10's later cutoff is
    still a resumed Nmmsell10, and must refuse."""
    s = xos_session
    books = _all_targets(s)
    n_cutoff = next(c for _, _, t, c in repair.TARGETS if t == "Nmmsell10")
    k_cutoff = next(c for _, _, t, c in repair.TARGETS if t == "Kmmsell10")
    assert n_cutoff < k_cutoff
    s.add(LiveOrder(market_ticker="KX-RESUMED", strategy="Nmmsell10",
                    created_at=n_cutoff + timedelta(hours=1)))
    s.commit()

    with pytest.raises(svc.ExperimentOsError, match="after its reviewed last order"):
        repair.repair(s, actor="tester")
    assert all(b[3].ended_at is None for b in books.values())


def test_refuses_all_or_nothing_when_one_experiment_is_missing(
    xos_session, xos_platform
):
    s = xos_session
    books = _all_targets(s, skip=len(repair.TARGETS) - 1)

    with pytest.raises(svc.ExperimentOsError, match="is not registered"):
        repair.repair(s, actor="tester")
    # Nothing changed on the six that DID validate.
    assert all(b[3].ended_at is None for b in books.values())


def test_refuses_an_experiment_that_is_not_LIVE_CANARY(xos_session, xos_platform):
    s = xos_session
    books = _all_targets(s)
    exp = books["Dmmsell10"][0]
    svc.transition_experiment(s, exp, "PAUSED", actor="operator")
    s.commit()

    with pytest.raises(svc.ExperimentOsError, match="not LIVE_CANARY"):
        repair.repair(s, actor="tester")
    assert all(b[3].ended_at is None for b in books.values())


def test_refuses_when_the_epoch_is_already_closed(xos_session, xos_platform):
    s = xos_session
    books = _all_targets(s)
    books["Vmmsell10"][2].ended_at = T0 + timedelta(days=1)
    s.commit()

    with pytest.raises(svc.ExperimentOsError, match="already CLOSED"):
        repair.repair(s, actor="tester")
    assert books["Cmmsell10"][3].ended_at is None


def test_refuses_a_deployment_that_is_not_kind_live(xos_session, xos_platform):
    s = xos_session
    key, dep_key, tag, _ = repair.TARGETS[0]
    exp = svc.create_experiment(s, key=key, origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="lever", now=T0)
    svc.add_arm(s, ver, arm_key="arm0", role="treatment", strategy_tag=tag)
    ver.control_exemption_reason = "single-book repair fixture"
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    dep = svc.register_deployment(
        s, epoch, deployment_key=dep_key, stage="PAPER", kind="paper",
        arms={"arm0": tag}, started_at=T0,
    )
    exp.state = "LIVE_CANARY"
    for k, d, t, _ in repair.TARGETS[1:]:
        _live_experiment(s, k, d, t)
    s.commit()

    with pytest.raises(svc.ExperimentOsError, match="not 'live'"):
        repair.repair(s, actor="tester")
    assert dep.ended_at is None


# --- the XOS-000033 transport guard ---------------------------------------------


class _Runtime:
    def __init__(self, *, books=(), live=()):
        self.mmsell_variant_list = [{"tag": t} for t in books]
        self.live_paper_twin_pairs = []
        self.live_strategy_list = list(live)


def _envelope(cid):
    return {"command_id": cid, "action": "REPAIR_LINEAGE", "actor": "tester",
            "actor_role": "LIVE_OPS", "schema_version": 1,
            "payload": {"package": "mmsell-standdown-live-repair", "reason": "test"}}


def test_declares_exactly_the_seven_live_tags():
    pkg = cmds._packages()["mmsell-standdown-live-repair"]
    assert pkg.retires_tags == repair.RETIRES_TAGS == (
        "Cmmsell10", "Dmmsell10", "Fmmsell10", "Jmmsell10", "Kmmsell10",
        "Nmmsell10", "Vmmsell10")


def test_transport_succeeds_once_the_books_left_MMSELL_VARIANTS(
    xos_session, xos_platform, monkeypatch
):
    s = xos_session
    books = _all_targets(s)
    monkeypatch.setattr("kalshi_bot.config.get_settings", lambda: _Runtime(
        books=("mmsell9", "mmsell10", "Xmmsell10", "Ymmsell10"),
        live=("Xmmsell10", "Alimm1", "Ymmsell10")))

    out = cmds.execute_envelope(s, _envelope("mm-close-ok"))
    s.commit()

    assert out["status"] == "SUCCEEDED", out
    assert all(b[3].ended_at is not None for b in books.values())


def test_transport_rejects_while_a_book_is_still_constructed(
    xos_session, xos_platform, monkeypatch
):
    """Production on 2026-11-01: the entries were still in MMSELL_VARIANTS."""
    s = xos_session
    books = _all_targets(s)
    monkeypatch.setattr("kalshi_bot.config.get_settings", lambda: _Runtime(
        books=("Kmmsell10",), live=("Xmmsell10",)))

    out = cmds.execute_envelope(s, _envelope("mm-close-no"))
    s.commit()

    assert out["status"] == "REJECTED", out
    assert "Kmmsell10" in out["error"]
    s.expire_all()
    assert all(b[3].ended_at is None for b in books.values())
