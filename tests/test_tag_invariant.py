"""The transport's structural backstop for XOS-000033: no constructive action may
leave a tag with lineage and no active arm.

`select_handover_deployments` (#391) fixed the two packages that ended more of a
predecessor than they reused — but it protects only callers that use it. This
check lives in the transport instead, after the package has run and inside the
same savepoint, so a package that hand-rolls the old blanket end is REJECTED and
rolled back regardless of how it selected what to end.

The fake packages here are the smallest possible shapes of "wrong" and "right":
one ends a deployment and re-registers nothing (the 2026-09-02 defect), the other
ends it and re-registers the same tag at the same instant (the legitimate
handover). The guard must refuse the first and stay out of the way of the second.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from kalshi_bot.experiment_os import experiment_commands as xc
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.models import ExperimentDeployment

UTC = timezone.utc
T0 = datetime(2026, 8, 1, tzinfo=UTC)
TAG = "strandme"
OTHER = "bystander"


def _book(session, *, key: str, tag: str, dep_key: str):
    """One PAPER experiment with a frozen version, an open epoch and one open paper
    deployment carrying `tag`."""
    exp = svc.create_experiment(session, key=key, origin="operator")
    ver = svc.create_experiment_version(
        session, exp, hypothesis="h", independent_variable="lever", now=T0)
    svc.add_arm(session, ver, arm_key="a0", role="treatment", strategy_tag=tag)
    ver.control_exemption_reason = "fixture"
    svc.freeze_version(session, ver, now=T0)
    epoch = svc.open_epoch(session, ver, reason="initial", started_at=T0)
    dep = svc.register_deployment(
        session, epoch, deployment_key=dep_key, stage="PAPER", kind="paper",
        arms={"a0": tag}, started_at=T0,
    )
    session.flush()
    return exp, ver, epoch, dep


def _fake_package(name: str, experiment_key: str, register):
    return xc.ExperimentPackage(
        name=name, experiment_key=experiment_key,
        description="test-only package", register=register,
    )


def _envelope(package: str, command_id: str):
    return {
        "command_id": command_id, "action": "REGISTER_PACKAGE",
        "actor": "tester", "actor_role": "TASK_SPECIFIC", "schema_version": 1,
        "payload": {"package": package},
    }


def _dep(session, key):
    return session.scalar(
        select(ExperimentDeployment).where(ExperimentDeployment.deployment_key == key)
    )


# ---------------------------------------------------------------------------
# The snapshot itself
# ---------------------------------------------------------------------------


def test_active_tags_require_an_open_deployment_on_an_open_epoch(
    xos_session, xos_platform
):
    s = xos_session
    _exp, _ver, epoch, dep = _book(s, key="book-a", tag=TAG, dep_key="a-paper-1")

    assert TAG in svc.active_strategy_tags(s)

    svc.end_deployment(s, dep, ended_at=T0 + timedelta(days=1))
    assert TAG not in svc.active_strategy_tags(s), "an ended deployment is not active"

    # Put it back on a fresh deployment, then close the EPOCH: the deployment row
    # is open but its epoch is not, which the resolver treats as not admissible
    # (the XOS-000011 shape) — so must this.
    svc.register_deployment(
        s, epoch, deployment_key="a-paper-2", stage="PAPER", kind="paper",
        arms={"a0": TAG}, started_at=T0 + timedelta(days=1),
    )
    assert TAG in svc.active_strategy_tags(s)
    svc.close_epoch(s, epoch, ended_at=T0 + timedelta(days=2))
    assert TAG not in svc.active_strategy_tags(s)


# ---------------------------------------------------------------------------
# The guard in the transport
# ---------------------------------------------------------------------------


def test_a_package_that_ends_a_tag_without_replacing_it_is_rejected_and_rolled_back(
    xos_session, xos_platform, monkeypatch
):
    """The 2026-09-02 defect, reduced to its essence: end, re-register nothing."""
    s = xos_session
    _book(s, key="book-a", tag=TAG, dep_key="a-paper-1")
    s.commit()

    def register(session, *, actor, promotion_sample_floor=None):
        dep = _dep(session, "a-paper-1")
        svc.end_deployment(session, dep, ended_at=svc._now())
        return {"ended": [dep.deployment_key]}

    pkg = _fake_package("fake-strander", "book-a", register)
    monkeypatch.setattr(xc, "_packages", lambda: {pkg.name: pkg})

    out = xc.execute_envelope(s, _envelope(pkg.name, "inv-0001"))
    s.commit()

    assert out["status"] == "REJECTED"
    assert TAG in out["error"], "the receipt must name the stranded tag"
    assert "XOS-000033" in out["error"]
    # The package's write did not survive: the savepoint rolled it back, so the
    # record never carries "lineage, no arm, nothing says why".
    s.expire_all()
    assert _dep(s, "a-paper-1").ended_at is None
    assert TAG in svc.active_strategy_tags(s)


def test_a_legitimate_handover_that_re_registers_the_tag_is_not_blocked(
    xos_session, xos_platform, monkeypatch
):
    """End and re-register at the same instant — what every successor does for
    the tag it reuses. The guard compares AFTER the whole action, so this passes."""
    s = xos_session
    _exp, _ver, epoch, _dep0 = _book(s, key="book-a", tag=TAG, dep_key="a-paper-1")
    s.commit()

    def register(session, *, actor, promotion_sample_floor=None):
        at = svc._now()
        dep = _dep(session, "a-paper-1")
        svc.end_deployment(session, dep, ended_at=at)
        ep = session.get(type(epoch), epoch.id)
        svc.register_deployment(
            session, ep, deployment_key="a-paper-2", stage="PAPER", kind="paper",
            arms={"a0": TAG}, started_at=at,
        )
        return {"ended": ["a-paper-1"], "registered": ["a-paper-2"]}

    pkg = _fake_package("fake-handover", "book-a", register)
    monkeypatch.setattr(xc, "_packages", lambda: {pkg.name: pkg})

    out = xc.execute_envelope(s, _envelope(pkg.name, "inv-0002"))
    s.commit()

    assert out["status"] == "SUCCEEDED", out.get("error")
    s.expire_all()
    assert _dep(s, "a-paper-1").ended_at is not None
    assert _dep(s, "a-paper-2").ended_at is None
    assert TAG in svc.active_strategy_tags(s)


def test_the_guard_looks_at_every_tag_not_just_the_packages_own(
    xos_session, xos_platform, monkeypatch
):
    """mmsell9 was a BYSTANDER to the capacity registration — a tag the successor
    was not about, on a deployment it happened to sweep up. The snapshot is
    whole-system precisely so that a bystander counts."""
    s = xos_session
    _book(s, key="book-a", tag=TAG, dep_key="a-paper-1")
    _book(s, key="book-b", tag=OTHER, dep_key="b-paper-1")
    s.commit()

    def register(session, *, actor, promotion_sample_floor=None):
        # "Register" book-a's successor but sweep up book-b's deployment too.
        svc.end_deployment(session, _dep(session, "b-paper-1"), ended_at=svc._now())
        return {"ended": ["b-paper-1"]}

    pkg = _fake_package("fake-sweeper", "book-a", register)
    monkeypatch.setattr(xc, "_packages", lambda: {pkg.name: pkg})

    out = xc.execute_envelope(s, _envelope(pkg.name, "inv-0003"))
    s.commit()

    assert out["status"] == "REJECTED"
    assert OTHER in out["error"]
    s.expire_all()
    assert _dep(s, "b-paper-1").ended_at is None


def test_a_package_that_adds_a_tag_is_fine(xos_session, xos_platform, monkeypatch):
    """Gaining an arm is what registering is FOR; only losing one is refused."""
    s = xos_session
    _exp, _ver, epoch, _dep0 = _book(s, key="book-a", tag=TAG, dep_key="a-paper-1")
    s.commit()

    def register(session, *, actor, promotion_sample_floor=None):
        ep = session.get(type(epoch), epoch.id)
        svc.register_deployment(
            session, ep, deployment_key="a-paper-extra", stage="PAPER", kind="paper",
            arms={"a0": "newcomer"}, started_at=svc._now(),
        )
        return {"registered": ["a-paper-extra"]}

    pkg = _fake_package("fake-grower", "book-a", register)
    monkeypatch.setattr(xc, "_packages", lambda: {pkg.name: pkg})

    out = xc.execute_envelope(s, _envelope(pkg.name, "inv-0004"))
    s.commit()

    assert out["status"] == "SUCCEEDED", out.get("error")
    assert {TAG, "newcomer"} <= svc.active_strategy_tags(s)


# ---------------------------------------------------------------------------
# Which actions the guard covers — and, as importantly, which it does not
# ---------------------------------------------------------------------------


def test_only_constructive_actions_are_tag_preserving():
    """Retiring actions exist to take tags OUT of the active set. Guarding them
    would make every stand-down un-runnable, which is the XOS-000012 circle."""
    assert {"REGISTER_PACKAGE", "REPAIR_LINEAGE", "ARM_CANARY"} == set(
        xc._TAG_PRESERVING_ACTIONS
    )
    for retiring in ("STAND_DOWN", "RETIRE_ON_GATE_FAIL", "CLOSE_OUT_RETROSPECTIVE"):
        assert retiring in xc.ACTIONS, retiring
        assert retiring not in xc._TAG_PRESERVING_ACTIONS, retiring


def test_pytest_sees_the_refusal_as_a_refusal_not_a_failure():
    """A TAG_STRANDED refusal is a workflow outcome the transport is SUPPOSED to
    produce, so it must be in `_REFUSALS` (→ REJECTED), not fall through to
    FAILED, which means 'the executor no longer knows what it did'."""
    assert xc.ExperimentCommandRejected in xc._REFUSALS


# ---------------------------------------------------------------------------
# A DECLARED retirement the runtime has let go of (epoch re-cuts onto fresh tags)
# ---------------------------------------------------------------------------


class _Runtime:
    """The three things the guard reads off the running worker's Settings."""

    def __init__(self, *, books=(), twins=(), live=()):
        self.mmsell_variant_list = [{"tag": t} for t in books]
        self.live_paper_twin_pairs = [(f"live-of-{t}", t) for t in twins]
        self.live_strategy_list = list(live)


def _retiring_package(monkeypatch, retires):
    def register(session, *, actor, promotion_sample_floor=None):
        dep = _dep(session, "a-paper-1")
        svc.end_deployment(session, dep, ended_at=svc._now())
        return {"ended": [dep.deployment_key]}

    pkg = xc.ExperimentPackage(
        name="fake-retirer", experiment_key="book-a", description="test-only package",
        register=register, retires_tags=retires,
    )
    monkeypatch.setattr(xc, "_packages", lambda: {pkg.name: pkg})
    return pkg


def _with_runtime(monkeypatch, runtime):
    import kalshi_bot.config as config

    monkeypatch.setattr(config, "get_settings", lambda: runtime)


def test_a_declared_retirement_the_runtime_no_longer_constructs_is_allowed(
    xos_session, xos_platform, monkeypatch
):
    """The Hmmsell10 -> Jmmsell10 re-cut: the old tag is out of LIVE_STRATEGIES and
    MMSELL_VARIANTS, so nothing can be refused for lack of its arm."""
    s = xos_session
    _book(s, key="book-a", tag=TAG, dep_key="a-paper-1")
    s.commit()
    pkg = _retiring_package(monkeypatch, (TAG,))
    _with_runtime(monkeypatch, _Runtime(books=("other",), live=("Jmm",)))

    out = xc.execute_envelope(s, _envelope(pkg.name, "inv-ret-1"))
    s.commit()

    assert out["status"] == "SUCCEEDED", out
    assert TAG not in svc.active_strategy_tags(s)


def test_a_declared_retirement_the_runtime_still_constructs_is_rejected(
    xos_session, xos_platform, monkeypatch
):
    """The declaration is not trusted on its own: each way the worker can still build the
    tag — a variants book, a twin, a LIVE_STRATEGIES prefix — keeps the guard closed."""
    for i, runtime in enumerate((_Runtime(books=(TAG,)), _Runtime(twins=(TAG,)),
                                 _Runtime(live=(TAG[:5],)))):
        s = xos_session
        if i == 0:
            _book(s, key="book-a", tag=TAG, dep_key="a-paper-1")
            s.commit()
        pkg = _retiring_package(monkeypatch, (TAG,))
        _with_runtime(monkeypatch, runtime)

        out = xc.execute_envelope(s, _envelope(pkg.name, f"inv-ret-c{i}"))
        s.commit()

        assert out["status"] == "REJECTED", (i, out)
        assert TAG in out["error"]
        s.expire_all()
        assert TAG in svc.active_strategy_tags(s)


def test_unreadable_settings_fail_closed(xos_session, xos_platform, monkeypatch):
    s = xos_session
    _book(s, key="book-a", tag=TAG, dep_key="a-paper-1")
    s.commit()
    pkg = _retiring_package(monkeypatch, (TAG,))
    import kalshi_bot.config as config

    def boom():
        raise RuntimeError("no settings")

    monkeypatch.setattr(config, "get_settings", boom)
    out = xc.execute_envelope(s, _envelope(pkg.name, "inv-ret-2"))
    assert out["status"] == "REJECTED"


def test_an_undeclared_tag_is_still_guarded_beside_a_declared_one(
    xos_session, xos_platform, monkeypatch
):
    """Declaring one retirement does not open the guard for anything else."""
    s = xos_session
    _book(s, key="book-a", tag=TAG, dep_key="a-paper-1")
    s.commit()
    pkg = _retiring_package(monkeypatch, ("somethingelse",))
    _with_runtime(monkeypatch, _Runtime())

    out = xc.execute_envelope(s, _envelope(pkg.name, "inv-ret-3"))
    assert out["status"] == "REJECTED"
    assert TAG in out["error"]


def test_the_sizesplit_epoch3_package_declares_exactly_the_tags_it_retires():
    from kalshi_bot.experiment_os import recut_mmsell10_size_split as r

    pkg = xc._packages()["mmsell-sizesplit-epoch3"]
    assert pkg.retires_tags == (r.PRIOR_LIVE_TAG, r.PRIOR_TWIN_TAG) == ("Hmmsell10",
                                                                         "Hmmsell10_pt4")
