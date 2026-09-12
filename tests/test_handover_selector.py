"""XOS-000033's cause: a successor ending MORE of its predecessor than it reuses.

`select_handover_deployments` replaces the blanket `kind == "paper"` selection in
the two successor packages. The selector is pure over already-fetched rows, so
the first half of this module tests it directly with plain objects. The second
half proves the two packages actually route through it — the regression is the
exact production shape of 2026-09-02: a predecessor holding the `mmsell10`
carrier AND a separate `mmsell9`-only carrier, where the old code ended both and
re-registered one.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os import successor_mmsell10_capacity as cap
from kalshi_bot.experiment_os import successor_mmsell10_contest_cap as cc
from kalshi_bot.experiment_os.lifecycle import ArmRole, LifecycleState
from kalshi_bot.experiment_os.read import get_experiment

UTC = timezone.utc
T0 = datetime(2026, 8, 1, tzinfo=UTC)
T1 = datetime(2026, 9, 2, tzinfo=UTC)


class _Dep:
    """Just enough of an ExperimentDeployment for the pure selector."""

    def __init__(self, key: str, kind: str = "paper"):
        self.deployment_key = key
        self.kind = kind
        self.ended_at = None

    def __repr__(self):  # pragma: no cover - diagnostics only
        return f"_Dep({self.deployment_key!r}, {self.kind!r})"


def _select(deps, tags: dict[str, list[str]], taking_over):
    return svc.select_handover_deployments(
        deps, taking_over=taking_over, tags_of=lambda d: tags[d.deployment_key],
    )


# ---------------------------------------------------------------------------
# The selector on its own
# ---------------------------------------------------------------------------


def test_narrows_to_deployments_carrying_a_taken_over_tag():
    """The 2026-09-02 shape: two open paper deployments, one of them ours."""
    carrier, other = _Dep("pred-paper-mmsell10"), _Dep("pred-paper-mmsell9")

    ending, left_open = _select(
        [carrier, other],
        {"pred-paper-mmsell10": ["mmsell10"], "pred-paper-mmsell9": ["mmsell9"]},
        taking_over={"mmsell10"},
    )

    assert ending == [carrier]
    assert left_open == [other], "a deployment we take nothing from must be LEFT ALONE"


def test_other_kinds_are_neither_ended_nor_reported_left_open():
    """Live and twin deployments are the caller's to drain; the selector must not
    even mention them, or a package might end one on the selector's say-so."""
    paper, live, twin = _Dep("p"), _Dep("l", "live"), _Dep("t", "paper_twin")

    ending, left_open = _select(
        [paper, live, twin],
        {"p": ["mmsell10"], "l": ["Cmmsell10"], "t": ["Cmmsell10_pt3"]},
        taking_over={"mmsell10"},
    )

    assert ending == [paper]
    assert left_open == []


def test_refuses_a_partial_handover_and_names_the_stranded_tag():
    """A deployment carrying our tag AND another cannot simply end: we re-register
    only what we take, so the other tag would be left with no active arm."""
    legacy = _Dep("pred-paper-legacy-1")

    with pytest.raises(svc.ExperimentOsError) as exc:
        _select([legacy], {"pred-paper-legacy-1": ["mmsell9", "mmsell10"]},
                taking_over={"mmsell10"})

    msg = str(exc.value)
    assert "pred-paper-legacy-1" in msg
    assert "['mmsell9']" in msg, "the stranded tag must be named, not implied"
    assert "XOS-000033" in msg


def test_a_tagless_paper_deployment_is_left_open_not_ended():
    """A PROBE-style deployment has nothing to hand over. `tags_of` returning
    nothing must read as 'not ours', never as 'nothing to strand, so end it'."""
    probe = _Dep("pred-probe-1")

    ending, left_open = _select([probe], {"pred-probe-1": []}, taking_over={"mmsell10"})

    assert ending == []
    assert left_open == [probe]


def test_refuses_an_empty_handover():
    with pytest.raises(svc.ExperimentOsError, match="at least one tag"):
        _select([_Dep("p")], {"p": ["mmsell10"]}, taking_over=set())


# ---------------------------------------------------------------------------
# The capacity package, against a real database, in the production shape
# ---------------------------------------------------------------------------


def _predecessor(session, *, split: bool):
    """`mmsell-price-ceiling` v1 with `mmsell9` beside `mmsell10`.

    `split=True` is what production looked like on 2026-09-02: the `mmsell10`
    carrier and a SEPARATE `mmsell9`-only carrier, both open. `split=False` is the
    older two-arm legacy deployment carrying both tags on ONE row — the shape the
    selector must refuse rather than strand.
    """
    exp = svc.create_experiment(
        session, key=cap.PREDECESSOR_KEY, origin="operator",
        title="predecessor", family="maker",
        hypothesis="h", mechanism="m", falsification="f", actor="t", now=T0,
    )
    version = svc.create_experiment_version(
        session, exp, independent_variable="entry-price ceiling",
        risk={"max_open_positions": 20}, control_required=False,
        control_exemption_reason="the execution control is the paper twin",
        now=T0,
    )
    svc.add_arm(
        session, version, arm_key=cap.ARM_KEY, role=ArmRole.TREATMENT,
        description="d", params={"lo": 5, "hi": 10, "maxyes": 7},
        strategy_tag=cap.PAPER_TAG,
    )
    svc.add_arm(
        session, version, arm_key="mmsell9", role="secondary",
        description="sweet-spot cell", params={"lo": 5, "hi": 12, "maxyes": 7},
        strategy_tag="mmsell9",
    )
    svc.freeze_version(session, version, now=T0)
    for state in (LifecycleState.PROBE, LifecycleState.PAPER):
        svc.transition_experiment(session, exp, state, actor="t",
                                  reason="r", occurred_at=T0)
    epoch = svc.open_epoch(session, version, reason="e", started_at=T0)
    if split:
        svc.register_deployment(
            session, epoch, deployment_key="pred-paper-1",
            stage=LifecycleState.PAPER, kind="paper",
            arms={cap.ARM_KEY: cap.PAPER_TAG}, started_at=T0,
        )
        carrier = svc.register_deployment(
            session, epoch, deployment_key="pred-paper-mmsell9-1",
            stage=LifecycleState.PAPER, kind="paper",
            arms={"mmsell9": "mmsell9"}, started_at=T0,
        )
    else:
        carrier = svc.register_deployment(
            session, epoch, deployment_key="pred-paper-legacy-1",
            stage=LifecycleState.PAPER, kind="paper",
            arms={cap.ARM_KEY: cap.PAPER_TAG, "mmsell9": "mmsell9"}, started_at=T0,
        )
    return exp, carrier


def test_capacity_register_leaves_the_mmsell9_carrier_open(xos_session, xos_platform):
    """THE regression. Before the fix this ended both deployments and re-registered
    only mmsell10; mmsell9 then went dark for 9.6 days."""
    _exp, mmsell9_carrier = _predecessor(xos_session, split=True)

    out = cap.register(xos_session, actor="claude-code", now=T1)

    assert out["ended_deployments"] == ["pred-paper-1"]
    assert out["left_open_deployments"] == ["pred-paper-mmsell9-1"]
    assert mmsell9_carrier.ended_at is None, "mmsell9's carrier must not be touched"
    # And the successor still got its own mmsell10 book — narrowing the end did
    # not break the handover it exists for.
    assert out["paper_deployment"].deployment_key == cap.PAPER_DEPLOYMENT_KEY


def test_capacity_register_refuses_to_strand_mmsell9_on_a_shared_deployment(
    xos_session, xos_platform
):
    """The legacy two-arm shape. Ending it would re-register mmsell10 and lose
    mmsell9, so the package must refuse BEFORE writing anything."""
    _exp, legacy = _predecessor(xos_session, split=False)

    with pytest.raises(svc.ExperimentOsError) as exc:
        cap.register(xos_session, actor="claude-code", now=T1)

    assert "['mmsell9']" in str(exc.value)
    assert legacy.ended_at is None, "must refuse before ending anything"
    assert get_experiment(xos_session, cap.SUCCESSOR_KEY) is None, (
        "a refused handover must not leave a half-registered successor behind"
    )


# ---------------------------------------------------------------------------
# The contest-cap package routes through the same selector
# ---------------------------------------------------------------------------


def test_contest_cap_register_refuses_to_strand_a_tag_it_is_not_taking_over(
    monkeypatch,
):
    """Same stub style as the package's own tests, so this pins the WIRING: the
    selector's refusal reaches `register` before `end_deployment` is called."""
    class _Pred:
        id = 7

    class _Paper:
        id, kind, ended_at = 2, "paper", None
        deployment_key = "mmsell-capacity-paper-1"

    class _Session:
        def scalars(self, *a, **k):
            return type("R", (), {"all": staticmethod(lambda: [_Paper()])})()

    monkeypatch.setattr(cc, "get_experiment",
                        lambda s, key: _Pred() if key == cc.PREDECESSOR_KEY else None)
    monkeypatch.setattr(cc, "_epoch_experiment_id", lambda s, d: _Pred.id)
    monkeypatch.setattr(cc, "_tags_of", lambda s, d: ["mmsell10", "mmsell9"])

    ended = []
    monkeypatch.setattr(svc, "end_deployment", lambda *a, **k: ended.append(a))

    with pytest.raises(svc.ExperimentOsError) as exc:
        cc.register(_Session(), actor="cal", now=T0)

    assert "['mmsell9']" in str(exc.value)
    assert not ended, "must refuse BEFORE ending anything"
