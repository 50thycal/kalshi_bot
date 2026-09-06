"""XOS-000012: ending the record of a live book that the runtime already stopped.

`theta4-fat-tail` and `mmsell-scheduled-settle-live` stopped trading on 2026-08-19
when `LIVE_STRATEGIES` was emptied, but their live deployments were never closed in
the record. That makes them unretirable: `_stand_down` refuses any experiment holding
an open LIVE deployment, `RETIRE_ON_GATE_FAIL` needs a recorded FAIL and both gates
read BLOCKED_DATA, and nothing else in the vocabulary ends a live deployment.

`repair_dark_live_canaries` breaks that circle. Its whole safety argument is that the
books are GENUINELY stopped, so these tests exercise the checks that establish it —
not the happy path, which is the easy half. A repair that closes a live deployment on
a book that is actually trading would be far worse than the state it fixes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot.experiment_os import repair_dark_live_canaries as repair
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.models import LiveOrder

UTC = timezone.utc
T0 = datetime(2026, 8, 1, tzinfo=UTC)
#: Comfortably before `LAST_LIVE_ORDER`, so it never trips the history check.
QUIET = repair.LAST_LIVE_ORDER - timedelta(days=2)

EXPERIMENT_KEY, DEPLOYMENT_KEY, TAGS = repair.TARGETS[0]
SECOND_KEY, SECOND_DEPLOYMENT, SECOND_TAGS = repair.TARGETS[1]


class _FakeSettings:
    def __init__(self, allowlist):
        self.live_strategy_list = list(allowlist)


def _arm(monkeypatch, *tags):
    """Point the repair's settings accessor at a given runtime allowlist."""
    monkeypatch.setattr(
        "kalshi_bot.config.get_settings", lambda: _FakeSettings(tags)
    )


@pytest.fixture(autouse=True)
def _allowlist_is_empty(monkeypatch):
    """Default every test to the state production is actually in: no tag armed."""
    _arm(monkeypatch)


def _live_experiment(s, key, deployment_key, tag):
    """A LIVE_CANARY experiment holding one OPEN live deployment."""
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
    # Both real targets are GRANDFATHERED legacy imports: they were recorded as
    # already-live rather than promoted through PAPER → LIVE_CANARY, which demands
    # an operator approval AND the PASS gate result that justified it. The fixture
    # sets the state the same way the importer does, because the transition path is
    # not what is under test here.
    exp.state = "LIVE_CANARY"
    return exp, ver, epoch, dep


def _both_targets(s):
    a = _live_experiment(s, EXPERIMENT_KEY, DEPLOYMENT_KEY, TAGS[0])
    b = _live_experiment(s, SECOND_KEY, SECOND_DEPLOYMENT, SECOND_TAGS[0])
    s.commit()
    return a, b


# ---------------------------------------------------------------------------
# The happy path — the record is made to agree with the runtime
# ---------------------------------------------------------------------------


def test_ends_both_live_deployments_and_nothing_else(xos_session, xos_platform):
    s = xos_session
    (_ea, _va, epoch_a, dep_a), (_eb, _vb, _pb, dep_b) = _both_targets(s)

    out = repair.repair(s, actor="tester")

    assert out["already_repaired"] is False
    assert out["ended"] == sorted([DEPLOYMENT_KEY, SECOND_DEPLOYMENT])
    assert dep_a.ended_at is not None
    assert dep_b.ended_at is not None
    # A repair moves no lifecycle state and closes no epoch — retiring is a
    # separate act an operator attests to.
    assert epoch_a.ended_at is None


def test_is_idempotent_and_does_not_move_an_existing_ended_at(
    xos_session, xos_platform
):
    s = xos_session
    (_ea, _va, _pa, dep_a), _b = _both_targets(s)

    repair.repair(s, actor="tester")
    first_ended = dep_a.ended_at

    again = repair.repair(s, actor="tester")

    assert again["already_repaired"] is True
    assert again["ended"] == []
    assert dep_a.ended_at == first_ended


# ---------------------------------------------------------------------------
# The refusals — the entire safety argument
# ---------------------------------------------------------------------------


def test_refuses_when_the_runtime_allowlist_arms_the_tag(
    xos_session, xos_platform, monkeypatch
):
    """An armed book may be trading, so its OPEN record is CORRECT and closing it
    would be the dangerous direction of this repair's only real mistake."""
    s = xos_session
    (_ea, _va, _pa, dep_a), _b = _both_targets(s)
    _arm(monkeypatch, TAGS[0])

    with pytest.raises(svc.ExperimentOsError, match="armed by the runtime allowlist"):
        repair.repair(s, actor="tester")
    assert dep_a.ended_at is None


def test_refuses_on_a_PREFIX_match_not_only_an_exact_tag(
    xos_session, xos_platform, monkeypatch
):
    """`LIVE_STRATEGIES` matches by prefix (config.py), so a prefix arms the book
    just as surely as the full tag. Missing that is the permissive failure — the
    repair would close a deployment the runtime is still authorising."""
    s = xos_session
    (_ea, _va, _pa, dep_a), _b = _both_targets(s)
    _arm(monkeypatch, TAGS[0][:3])

    with pytest.raises(svc.ExperimentOsError, match="armed by the runtime allowlist"):
        repair.repair(s, actor="tester")
    assert dep_a.ended_at is None


def test_refuses_when_a_live_order_exists_after_the_reviewed_cutoff(
    xos_session, xos_platform
):
    """Config says what is armed now; history says what actually traded. A newer
    order means the book came back and this repair is out of date."""
    s = xos_session
    (_ea, _va, _pa, dep_a), _b = _both_targets(s)
    s.add(LiveOrder(
        market_ticker="KX-RESUMED", strategy=TAGS[0],
        created_at=repair.LAST_LIVE_ORDER + timedelta(hours=1),
    ))
    s.commit()

    with pytest.raises(svc.ExperimentOsError, match="after the reviewed"):
        repair.repair(s, actor="tester")
    assert dep_a.ended_at is None


def test_an_order_before_the_cutoff_does_not_block_the_repair(
    xos_session, xos_platform
):
    """The books DID trade — 628 orders up to 2026-08-19. History before the cutoff
    is the expected state, not a reason to refuse."""
    s = xos_session
    (_ea, _va, _pa, dep_a), _b = _both_targets(s)
    s.add(LiveOrder(
        market_ticker="KX-HISTORIC", strategy=TAGS[0], created_at=QUIET,
    ))
    s.commit()

    repair.repair(s, actor="tester")

    assert dep_a.ended_at is not None


def test_refuses_an_experiment_that_is_not_LIVE_CANARY(xos_session, xos_platform):
    s = xos_session
    (exp_a, _va, _pa, dep_a), _b = _both_targets(s)
    svc.transition_experiment(s, exp_a, "PAUSED", actor="operator")
    s.commit()

    with pytest.raises(svc.ExperimentOsError, match="not LIVE_CANARY"):
        repair.repair(s, actor="tester")
    assert dep_a.ended_at is None


def test_refuses_when_the_epoch_is_already_closed(xos_session, xos_platform):
    """A live deployment open on a CLOSED epoch is the XOS-000011 shape and a
    different repair; this one must not quietly absorb it."""
    s = xos_session
    (_ea, _va, epoch_a, dep_a), _b = _both_targets(s)
    epoch_a.ended_at = T0 + timedelta(days=1)
    s.commit()

    with pytest.raises(svc.ExperimentOsError, match="already CLOSED"):
        repair.repair(s, actor="tester")
    assert dep_a.ended_at is None


def test_refuses_when_a_named_experiment_is_not_registered(
    xos_session, xos_platform
):
    s = xos_session
    # Only the SECOND target exists; the first is absent.
    _live_experiment(s, SECOND_KEY, SECOND_DEPLOYMENT, SECOND_TAGS[0])
    s.commit()

    with pytest.raises(svc.ExperimentOsError, match="is not registered"):
        repair.repair(s, actor="tester")


def test_refuses_a_deployment_that_is_not_kind_live(xos_session, xos_platform):
    """The targets are literals, but the ROW they name is production state. If it
    is not the live deployment this repair describes, it must not be touched."""
    s = xos_session
    exp = svc.create_experiment(s, key=EXPERIMENT_KEY, origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="lever", now=T0)
    svc.add_arm(s, ver, arm_key="arm0", role="treatment", strategy_tag=TAGS[0])
    ver.control_exemption_reason = "single-book repair fixture"
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    dep = svc.register_deployment(
        s, epoch, deployment_key=DEPLOYMENT_KEY, stage="PAPER", kind="paper",
        arms={"arm0": TAGS[0]}, started_at=T0,
    )
    exp.state = "LIVE_CANARY"
    s.commit()

    with pytest.raises(svc.ExperimentOsError, match="not 'live'"):
        repair.repair(s, actor="tester")
    assert dep.ended_at is None
