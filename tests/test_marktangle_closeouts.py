"""Closing the MARKTANGLE line: both experiments retired with their verdicts recorded.

WHY THIS FILE EXISTS
--------------------
The MARKTANGLE line asked one question over two experiments — does measurable serial
dependence in a recurring Kalshi market survive contact with the price? — and got two
answers, neither of which authorizes anything:

  * MARKTANGLE-1 never reached its own 100-entry holdout floor. HOLD, by its own
    frozen rule, plus a directional finding AGAINST the thesis (daily crypto
    threshold families are momentum machines). It was never registered while it ran:
    three documents said "PAUSED at PROBE in Experiment OS" and it was not in
    Experiment OS at all.
  * MARKTANGLE-2 ran registered, and its run-2 holdout falsified Track A wherever the
    sample was adequate and found Track B unpriceable.

Two close-outs, two different shapes, and the shapes are the thing worth testing:
MARKTANGLE-1's REGISTERS then closes (the PERP-V1 pattern), MARKTANGLE-2's ADOPTS a
contract already in production and must refuse to author a second one beside it.

Both are only reachable at all because `close_out_retrospective`'s deployment guard
now asks about TAGS rather than about rows — every MARKTANGLE probe deployment is
deliberately tagless. `tests/test_xos_retrospective_closeout.py` owns that guard; this
file owns what the two packages do with it.
"""

from __future__ import annotations

import pathlib

import pytest
from sqlalchemy import select

from kalshi_bot.experiment_os import enforcement as enf
from kalshi_bot.experiment_os import experiment_commands as xc
from kalshi_bot.experiment_os import marktangle, marktangle2, read
from kalshi_bot.experiment_os.lifecycle import GateVerdict, LifecycleState
from kalshi_bot.experiment_os.models import (
    ExperimentDeploymentArm,
    ExperimentGateResult,
    ExperimentVersion,
)


@pytest.fixture(autouse=True)
def _fresh_resolver():
    enf.reset_for_tests()
    yield
    enf.reset_for_tests()


def _envelope(package: str, command_id: str) -> dict:
    return {
        "command_id": command_id,
        "action": "CLOSE_OUT_RETROSPECTIVE",
        "actor": "live-ops-test",
        "actor_role": "LIVE_OPS",
        "payload": {
            "package": package,
            "approved_by": "cal",
            "reason": f"{package} closed 2026-09-03.",
        },
        "schema_version": 1,
    }


def _results(session, key: str) -> dict[str, str]:
    experiment = read.get_experiment(session, key)
    version = read.latest_version(session, experiment)
    gates = {g.id: g.gate_key for g in read.gates_for(session, version)}
    rows = session.scalars(
        select(ExperimentGateResult).where(
            ExperimentGateResult.experiment_id == experiment.id
        )
    ).all()
    return {gates[r.gate_id]: r.verdict for r in rows}


# ===========================================================================
# MARKTANGLE-1 — register then close, having never been registered while it ran
# ===========================================================================


@pytest.fixture
def m1_closed(xos_session, xos_platform):
    return marktangle.close_out_retrospective(
        xos_session,
        actor="live-ops-test",
        approved_by="cal",
        reason="MARKTANGLE-1 closed 2026-09-03: HOLD on a holdout that never reached "
               "its 100-entry floor; closed at PROBE, never registered while it ran.",
    )


def test_marktangle_1_lands_retired_with_both_gates_judged(xos_session, m1_closed):
    experiment = read.get_experiment(xos_session, marktangle.EXPERIMENT_KEY)
    assert experiment.state == LifecycleState.RETIRED.value
    assert _results(xos_session, marktangle.EXPERIMENT_KEY) == {
        marktangle.PROMOTION_GATE_KEY: GateVerdict.HOLD.value,
        marktangle.KEEP_GATE_KEY: GateVerdict.HOLD.value,
    }


def test_marktangle_1_is_hold_not_fail(xos_session, m1_closed):
    """The contract's own frozen rule reserves HOLD for "no family reaches the
    100-entry holdout floor — thin sample is not a negative result, it is no result",
    and that is what run 8 returned. FAIL would claim a falsification the evidence
    never supported, and re-reading a frozen rule after results is the one thing the
    contract forbids."""
    assert {v for _, v, _ in marktangle.CLOSE_OUT_VERDICTS} == {"HOLD"}


def test_marktangle_1_creates_no_strategy_tag(xos_session, m1_closed):
    """No `mkt*` tag was ever created, which is why an unregistered MARKTANGLE-1
    could sit under NEW_ONLY with no risk of it trading. The close-out must not
    change that."""
    tags = xos_session.scalars(
        select(ExperimentDeploymentArm.strategy_tag).where(
            ExperimentDeploymentArm.strategy_tag.is_not(None)
        )
    ).all()
    assert tags == []


def test_marktangle_1_leaves_no_open_deployment(xos_session, m1_closed):
    experiment = read.get_experiment(xos_session, marktangle.EXPERIMENT_KEY)
    version = read.latest_version(xos_session, experiment)
    for epoch in read.epochs_for(xos_session, version):
        for deployment in read.deployments_for(xos_session, epoch):
            assert deployment.ended_at is not None, (
                f"{deployment.deployment_key} is still open on a RETIRED experiment"
            )


# ===========================================================================
# MARKTANGLE-2 — adopt the registered contract; never author a second one
# ===========================================================================


def test_marktangle_2_refuses_to_close_an_unregistered_contract(
    xos_session, xos_platform
):
    """The defining difference from PERP-V1's and MARKTANGLE-1's close-outs. This
    contract is already in production, so the close-out records against it; asked to
    close one that does not exist it must say so, not quietly author one — a contract
    invented at close-out time is a contract nobody reviewed carrying verdicts nobody
    can check."""
    with pytest.raises(Exception, match="not registered"):
        marktangle2.close_out_retrospective(
            xos_session, actor="t", approved_by="cal", reason="r"
        )


@pytest.fixture
def m2_closed(xos_session, xos_platform):
    marktangle2.register(xos_session, actor="research-lab-test")
    return marktangle2.close_out_retrospective(
        xos_session,
        actor="live-ops-test",
        approved_by="cal",
        reason="MARKTANGLE-2 closed 2026-09-03: both tracks closed on run-2 holdout.",
    )


def test_marktangle_2_records_each_track_on_its_own_evidence(xos_session, m2_closed):
    """Track A failed; Track B could not be measured. Recording both as the same
    verdict would erase the difference between a refuted thesis and an absent
    market — and §11's track independence holds through the close-out."""
    assert _results(xos_session, marktangle2.EXPERIMENT_KEY) == {
        "paper_to_live_canary_a": GateVerdict.FAIL.value,
        "paper_keep_a": GateVerdict.FAIL.value,
        "paper_to_live_canary_b": GateVerdict.BLOCKED_DATA.value,
        "paper_keep_b": GateVerdict.BLOCKED_DATA.value,
    }


def test_marktangle_2_closes_without_authoring_a_second_version(xos_session, m2_closed):
    experiment = read.get_experiment(xos_session, marktangle2.EXPERIMENT_KEY)
    versions = xos_session.scalars(
        select(ExperimentVersion).where(
            ExperimentVersion.experiment_id == experiment.id
        )
    ).all()
    assert len(versions) == 1, "the close-out adopts the contract, it does not add one"
    assert experiment.state == LifecycleState.RETIRED.value


def test_marktangle_2_ends_its_tagless_probe_deployment(xos_session, m2_closed):
    experiment = read.get_experiment(xos_session, marktangle2.EXPERIMENT_KEY)
    version = read.latest_version(xos_session, experiment)
    deployments = [
        d
        for epoch in read.epochs_for(xos_session, version)
        for d in read.deployments_for(xos_session, epoch)
    ]
    assert [d.deployment_key for d in deployments] == [marktangle2.PROBE_DEPLOYMENT_KEY]
    assert deployments[0].ended_at is not None


def test_marktangle_2_verdicts_name_the_discrepancy_with_the_printed_track_rule(
    xos_session,
):
    """The instrument's frozen track rule printed `A HOLD` / `B HOLD` on run 2; these
    rows say FAIL and BLOCKED_DATA. That gap is an OPERATOR conclusion, and the one
    thing that makes it honest rather than post-hoc repricing is that it is written
    down where a reader will hit it. A silent relabel is the failure mode."""
    source = pathlib.Path(marktangle2.__file__).read_text()
    _, _, after = source.partition("CLOSE_OUT_VERDICTS")
    preamble = source[: source.index("CLOSE_OUT_VERDICTS")]
    assert after, "the verdict table moved; this test is anchored to it"
    assert "printed" in preamble and "HOLD" in preamble, (
        "the comment above CLOSE_OUT_VERDICTS must say that the instrument's frozen "
        "track rule printed HOLD and that these rows depart from it. A silent relabel "
        "of a frozen rule's output is exactly the post-hoc repricing §11 forbids; "
        "stating it is what makes an operator conclusion legible as one."
    )
    # And the explanations must carry the reason on each side: Track A's
    # adequately-powered classes, Track B's absent book.
    text = "\n".join(e for _, _, e in marktangle2.CLOSE_OUT_VERDICTS)
    assert "adequately-powered" in text
    assert "coverage" in text


# ===========================================================================
# The seam: both must survive the real transport, receipt and all
# ===========================================================================


@pytest.mark.parametrize(
    "package,setup",
    [("marktangle-reversion", None), ("marktangle-2", marktangle2.register)],
)
def test_the_transport_accepts_both_close_outs(
    package, setup, xos_session, xos_platform
):
    """`m2-register-1..4` cost four production attempts because package and transport
    were only ever tested apart. Every MARKTANGLE lifecycle act now goes through the
    real executor here first."""
    if setup is not None:
        setup(xos_session, actor="research-lab-test")
    out = xc.execute_envelope(xos_session, _envelope(package, f"xcmd-close-{package}"))
    xos_session.commit()

    assert out["status"] == xc.CommandStatus.SUCCEEDED, out.get("error")
    assert out["result"]["kind"] == "close_out"
    key = xc._packages()[package].experiment_key
    assert read.get_experiment(xos_session, key).state == LifecycleState.RETIRED.value


@pytest.mark.parametrize("package", ["marktangle-reversion", "marktangle-2"])
def test_no_close_out_verdict_can_authorize_anything(package):
    """PASS is unrepresentable through this path, and the verdict tables must not even
    contain the word — a promotion look tests `== PASS` and nothing here may ever
    satisfy it."""
    module = marktangle if package == "marktangle-reversion" else marktangle2
    assert all(v != "PASS" for _, v, _ in module.CLOSE_OUT_VERDICTS)
    assert all(
        e and e.strip() for _, _, e in module.CLOSE_OUT_VERDICTS
    ), "a retrospective verdict without an explanation is a number nobody can check"
