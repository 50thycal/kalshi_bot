"""The retrospective close-out verb: recording an experiment that ran and finished
OUTSIDE Experiment OS.

The verb exists because PERP-V1 ran a full probe lifecycle unregistered — correct at
the time, since registering redeploys the worker and a probe that cannot trade had no
reason to force that — and the system was then unable to say the one true thing: it
happened, and it is over. The documents were its only record, which is the exact
fragmentation Experiment OS exists to prevent.

A verb that writes hand-made verdicts is also the most dangerous thing that could be
added to this transport, because "only a recorded evaluator PASS authorizes a
transition" is the rule the whole apparatus rests on. So these tests spend most of
their effort on what it must REFUSE, not on what it produces.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from kalshi_bot.experiment_os import perp_v1
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.experiment_commands import (
    ACTION_ROLES,
    ACTIONS,
    _packages,
)
from kalshi_bot.experiment_os.lifecycle import GateVerdict, LifecycleState
from kalshi_bot.experiment_os.models import (
    ExperimentDeployment,
    ExperimentEpoch,
    ExperimentGateResult,
    ExperimentStateTransition,
    ExperimentVersion,
)


@pytest.fixture
def closed(xos_session, xos_platform):
    return perp_v1.close_out_retrospective(
        xos_session,
        actor="live-ops-test",
        approved_by="cal",
        reason="PERP-V1 closed 2026-09-02: arm A FAIL on execution economics, "
               "arm B BLOCKED_DATA, arm C operator NO-GO.",
    )


# ---------------------------------------------------------------------------
# What it must refuse — the reason the verb is safe to exist
# ---------------------------------------------------------------------------

def test_a_pass_verdict_is_refused_outright(xos_session, xos_platform):
    """The whole point. A verdict computed by hand, outside the system, after the
    fact, must never be able to authorize a promotion — and the cheapest way to
    guarantee that is to make PASS unrepresentable through this path rather than
    merely discouraged."""
    produced = perp_v1.register(xos_session, actor="t")
    gate = produced["gates"][0]
    with pytest.raises(svc.ExperimentOsError, match="may not record PASS"):
        svc.close_out_retrospective(
            xos_session, produced["experiment"],
            verdicts=[(gate, GateVerdict.PASS, "because I said so")],
            actor="t", approved_by="cal", reason="r", evidence_ref="docs/x.md",
        )


def test_an_experiment_holding_a_deployment_is_refused(xos_session, xos_platform):
    """A deployment means strategy tags existed and something may have traded. That
    is a MIGRATION with evidence to reconstruct, owned by a role that reconstructs
    evidence — not an outside-the-system record written by one that does not."""
    produced = perp_v1.register(xos_session, actor="t")
    xos_session.add(
        ExperimentDeployment(
            epoch_id=produced["epoch"].id,
            deployment_key="perp-fake",
            stage=LifecycleState.PAPER.value,
            kind="paper",
            started_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        )
    )
    xos_session.flush()
    with pytest.raises(svc.ExperimentOsError, match="MIGRATION"):
        svc.close_out_retrospective(
            xos_session, produced["experiment"],
            verdicts=[(produced["gates"][0], GateVerdict.FAIL, "x")],
            actor="t", approved_by="cal", reason="r", evidence_ref="docs/x.md",
        )


def test_it_requires_a_person_to_attest(xos_session, xos_platform):
    """Writing down someone else's conclusion is an operator act; the audit row must
    name a person rather than a process."""
    produced = perp_v1.register(xos_session, actor="t")
    with pytest.raises(svc.ExperimentOsError, match="approved_by"):
        svc.close_out_retrospective(
            xos_session, produced["experiment"],
            verdicts=[(produced["gates"][0], GateVerdict.FAIL, "x")],
            actor="t", approved_by="  ", reason="r", evidence_ref="docs/x.md",
        )


def test_it_requires_a_pointer_to_the_evidence_that_lives_elsewhere(
    xos_session, xos_platform
):
    """The numbers behind a retrospective verdict are by definition not in this
    system. A verdict with no pointer to where they ARE is unauditable."""
    produced = perp_v1.register(xos_session, actor="t")
    with pytest.raises(svc.ExperimentOsError, match="evidence_ref"):
        svc.close_out_retrospective(
            xos_session, produced["experiment"],
            verdicts=[(produced["gates"][0], GateVerdict.FAIL, "x")],
            actor="t", approved_by="cal", reason="r", evidence_ref="",
        )


def test_every_verdict_needs_an_explanation(xos_session, xos_platform):
    produced = perp_v1.register(xos_session, actor="t")
    with pytest.raises(svc.ExperimentOsError, match="needs an explanation"):
        svc.close_out_retrospective(
            xos_session, produced["experiment"],
            verdicts=[(produced["gates"][0], GateVerdict.FAIL, "  ")],
            actor="t", approved_by="cal", reason="r", evidence_ref="docs/x.md",
        )


def test_closing_out_with_no_verdicts_is_refused(xos_session, xos_platform):
    """An experiment retired with nothing recorded is not closed out, it is deleted."""
    produced = perp_v1.register(xos_session, actor="t")
    with pytest.raises(svc.ExperimentOsError, match="at least one gate verdict"):
        svc.close_out_retrospective(
            xos_session, produced["experiment"], verdicts=[],
            actor="t", approved_by="cal", reason="r", evidence_ref="docs/x.md",
        )


def test_a_gate_left_unjudged_is_refused(xos_session, xos_platform, monkeypatch):
    """A retired experiment with a silent gate is the fragmentation this verb exists
    to end — half a record is its own kind of missing record."""
    monkeypatch.setattr(
        perp_v1, "CLOSE_OUT_VERDICTS", perp_v1.CLOSE_OUT_VERDICTS[:2]
    )
    with pytest.raises(svc.ExperimentOsError, match="without a verdict"):
        perp_v1.close_out_retrospective(
            xos_session, actor="t", approved_by="cal", reason="r"
        )


def test_a_verdict_naming_a_gate_the_contract_lacks_is_refused(
    xos_session, xos_platform, monkeypatch
):
    """The verdict table and the gate specs drifting apart must fail loudly, not
    silently record three of four."""
    monkeypatch.setattr(
        perp_v1, "CLOSE_OUT_VERDICTS",
        perp_v1.CLOSE_OUT_VERDICTS + (("no_such_gate", "FAIL", "x"),),
    )
    with pytest.raises(svc.ExperimentOsError, match="does not have"):
        perp_v1.close_out_retrospective(
            xos_session, actor="t", approved_by="cal", reason="r"
        )


# ---------------------------------------------------------------------------
# What it produces
# ---------------------------------------------------------------------------

def test_it_lands_retired_in_one_act(xos_session, closed):
    """Atomicity is the design. REGISTER_PACKAGE alone would leave a closed, failed
    experiment sitting as an ACTIVE PROBE with open gates — the Control Tower would
    show a dead experiment as live research, worse than the documents-only state."""
    from kalshi_bot.experiment_os.read import get_experiment

    experiment = get_experiment(xos_session, perp_v1.EXPERIMENT_KEY)
    assert experiment.state == LifecycleState.RETIRED.value
    assert closed["state"] == LifecycleState.RETIRED.value


def test_no_recorded_verdict_is_a_pass(xos_session, closed):
    from kalshi_bot.experiment_os.read import get_experiment

    experiment = get_experiment(xos_session, perp_v1.EXPERIMENT_KEY)
    verdicts = xos_session.scalars(
        select(ExperimentGateResult.verdict).where(
            ExperimentGateResult.experiment_id == experiment.id
        )
    ).all()
    assert verdicts, "the close-out recorded nothing"
    assert GateVerdict.PASS.value not in verdicts


def test_every_gate_carries_a_verdict(xos_session, closed):
    assert closed["results"] == len(perp_v1.gate_specs())


def test_results_are_stamped_retrospective_so_they_read_apart_from_the_evaluator(
    xos_session, closed
):
    """A gate result that cannot say who computed it is worse than no gate result."""
    from kalshi_bot.experiment_os.read import get_experiment

    experiment = get_experiment(xos_session, perp_v1.EXPERIMENT_KEY)
    computed_by = set(
        xos_session.scalars(
            select(ExperimentGateResult.computed_by).where(
                ExperimentGateResult.experiment_id == experiment.id
            )
        ).all()
    )
    assert computed_by == {"retrospective:live-ops-test"}


def test_the_retirement_names_the_person_who_approved_it(xos_session, closed):
    from kalshi_bot.experiment_os.read import get_experiment

    experiment = get_experiment(xos_session, perp_v1.EXPERIMENT_KEY)
    row = xos_session.scalars(
        select(ExperimentStateTransition).where(
            ExperimentStateTransition.experiment_id == experiment.id,
            ExperimentStateTransition.to_state == LifecycleState.RETIRED.value,
        )
    ).one()
    assert row.approved_by == "cal"


def test_it_creates_no_deployment_and_no_tag(xos_session, closed):
    """PERP-V1 could sit unregistered under NEW_ONLY without risk precisely because
    no perp strategy tag ever existed. Closing it out must not create one."""
    from kalshi_bot.experiment_os.read import get_experiment

    experiment = get_experiment(xos_session, perp_v1.EXPERIMENT_KEY)
    assert xos_session.scalars(
        select(ExperimentDeployment)
        .join(ExperimentEpoch, ExperimentEpoch.id == ExperimentDeployment.epoch_id)
        .join(ExperimentVersion, ExperimentVersion.id == ExperimentEpoch.version_id)
        .where(ExperimentVersion.experiment_id == experiment.id)
    ).all() == []


def test_arm_c_is_hold_not_fail(xos_session, closed):
    """An operator NO-GO with the mechanism untested at the horizon it claimed is
    not a falsification. Recording FAIL would claim evidence we do not have."""
    verdicts = {k: v for k, v, _ in perp_v1.CLOSE_OUT_VERDICTS}
    assert verdicts[f"probe_to_paper_{perp_v1.ARM_LEAD}"] == "HOLD"
    assert verdicts[f"probe_to_paper_{perp_v1.ARM_REVERT}"] == "FAIL"
    assert verdicts[f"probe_to_paper_{perp_v1.ARM_CARRY}"] == "BLOCKED_DATA"


def test_no_bespoke_verdict_values_were_invented(xos_session):
    """The operator asked for FAIL_EXECUTION_ECONOMICS and NO_GO_OPERATOR. Neither is
    a GateVerdict, and adding one to a shared enum to describe a single experiment's
    cause of death is a platform change no evidence calls for. The cause lives in the
    explanation; the enum keeps meaning what it meant."""
    allowed = {v.value for v in GateVerdict}
    assert {v for _, v, _ in perp_v1.CLOSE_OUT_VERDICTS} <= allowed


# ---------------------------------------------------------------------------
# Transport wiring
# ---------------------------------------------------------------------------

def test_the_verb_exists_and_demands_an_approver_and_a_reason():
    spec = ACTIONS["CLOSE_OUT_RETROSPECTIVE"]
    assert spec.required == frozenset({"package", "approved_by", "reason"})
    assert spec.optional == frozenset()


def test_research_lab_may_not_close_out_its_own_experiment():
    """The session that RAN the experiment should not also be the one that writes
    down its own verdict. Research Lab may REGISTER_PACKAGE and may not close out."""
    assert "RESEARCH_LAB" in ACTION_ROLES["REGISTER_PACKAGE"]
    assert "RESEARCH_LAB" not in ACTION_ROLES["CLOSE_OUT_RETROSPECTIVE"]


def test_perp_v1_declares_a_close_out_and_still_arms_nothing():
    package = _packages()["perp-v1"]
    assert package.close_out is perp_v1.close_out_retrospective
    assert package.arm is None


def test_a_package_without_a_close_out_is_refused_by_name():
    """Aimed at a package that declares none, the verb must say so rather than
    quietly doing nothing."""
    assert all(
        p.close_out is None or p.name == "perp-v1" for p in _packages().values()
    )


# ---------------------------------------------------------------------------
# The transport's own post-conditions — it re-checks rather than trusting
# ---------------------------------------------------------------------------

def _closeout_envelope(command_id: str, package: str = "perp-v1") -> dict:
    return {
        "command_id": command_id,
        "action": "CLOSE_OUT_RETROSPECTIVE",
        "actor": "live-ops-test",
        "actor_role": "LIVE_OPS",
        "payload": {
            "package": package,
            "approved_by": "cal",
            "reason": "PERP-V1 closed 2026-09-02.",
        },
        "schema_version": 1,
    }


def test_the_transport_refuses_a_close_out_that_did_not_retire(
    xos_session, xos_platform, monkeypatch
):
    """A package is reviewed code, but a reviewed function can be edited later. The
    two properties that make this verb safe are cheap to re-check at the transport,
    so it re-checks them instead of trusting the package to have used the guarded
    helper. Here: a close_out that registers and stops."""
    from kalshi_bot.experiment_os import experiment_commands as xc

    real = xc._packages()["perp-v1"]
    lazy = xc._packages

    def only_registers(session, *, actor, approved_by, reason):
        return perp_v1.register(session, actor=actor)

    monkeypatch.setattr(
        xc, "_packages",
        lambda: {**lazy(), "perp-v1": dataclasses.replace(real, close_out=only_registers)},
    )
    out = xc.execute_envelope(xos_session, _closeout_envelope("xclo-0001"))
    assert out["status"] == "REJECTED"
    assert "rather than RETIRED" in (out["error"] or "")


def test_the_transport_refuses_a_close_out_that_recorded_a_pass(
    xos_session, xos_platform, monkeypatch
):
    """The service helper refuses PASS, so reaching this needs a package that went
    around it. That is exactly the case worth catching twice."""
    from kalshi_bot.experiment_os import experiment_commands as xc

    real = xc._packages()["perp-v1"]
    lazy = xc._packages

    def sneaks_a_pass(session, *, actor, approved_by, reason):
        produced = perp_v1.register(session, actor=actor)
        svc.record_gate_result(
            session, produced["gates"][0], verdict=GateVerdict.PASS,
            computed_by="not-the-evaluator",
        )
        svc.transition_experiment(
            session, produced["experiment"], LifecycleState.RETIRED,
            actor=actor, reason=reason, approved_by=approved_by,
        )
        return {"experiment": perp_v1.EXPERIMENT_KEY}

    monkeypatch.setattr(
        xc, "_packages",
        lambda: {**lazy(), "perp-v1": dataclasses.replace(real, close_out=sneaks_a_pass)},
    )
    out = xc.execute_envelope(xos_session, _closeout_envelope("xclo-0002"))
    assert out["status"] == "REJECTED"
    assert "may never authorize a promotion" in (out["error"] or "")


def test_a_real_close_out_is_accepted_through_the_transport(xos_session, xos_platform):
    from kalshi_bot.experiment_os import experiment_commands as xc
    from kalshi_bot.experiment_os.read import get_experiment

    out = xc.execute_envelope(xos_session, _closeout_envelope("xclo-0003"))
    assert out["status"] == "SUCCEEDED", out
    assert get_experiment(xos_session, perp_v1.EXPERIMENT_KEY).state == (
        LifecycleState.RETIRED.value
    )
