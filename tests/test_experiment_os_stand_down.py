"""`STAND_DOWN` — the verb that lets a line of research actually END in production.

## The gap this closes

An experiment could be registered, armed and promoted through the experiment
command transport and then never STOPPED through it. The CLI is read-only for
lifecycle, the ops channel is read-only against Postgres, and the transport's
vocabulary reached only REGISTER_PACKAGE, REPAIR_LINEAGE, CLOSE_OUT_RETROSPECTIVE
and ARM_CANARY.

`CLOSE_OUT_RETROSPECTIVE` looks like the answer and is not. It is for an
experiment that ran OUTSIDE Experiment OS, and it refuses any experiment holding a
tagged deployment — which is to say, exactly the experiments that actually ran
here. So a finished experiment stayed recorded as active indefinitely.

`freeze-dark-window-pin` is the worked case: four tagged paper books, zero
`paper_trades` rows in 24 days, still reported as an active PAPER deployment while
XOS-000003 held the diagnosis that no available Kalshi universe satisfies the
hypothesis. The record said the books were running; nothing was running. That is
the same class of untruth `close_epoch` exists to prevent.

## What these tests are for

The verb writes lifecycle state from a public envelope addressed by key, with no
reviewed package in between. Every guard that makes that safe is pinned here, and
the refusals matter more than the happy path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot.experiment_os import experiment_commands as xc
from kalshi_bot.experiment_os import read
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.lifecycle import LifecycleState

UTC = timezone.utc


def _now() -> datetime:
    return datetime.now(UTC)


def _paper_book(s, key="freeze-shaped", *, tags=("freeze1", "freeze2")):
    """A registered PAPER experiment with a tagged, open paper deployment —
    the shape `CLOSE_OUT_RETROSPECTIVE` refuses."""
    at = _now() - timedelta(days=24)
    exp = svc.create_experiment(s, key=key, origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="lever", now=at,
        control_exemption_reason="test shape: absolute threshold",
    )
    for i, tag in enumerate(tags):
        svc.add_arm(s, ver, arm_key=f"arm{i}",
                    role="control" if i == 0 else "treatment", strategy_tag=tag)
    svc.freeze_version(s, ver, now=at)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=at)
    dep = svc.register_deployment(
        s, epoch, deployment_key=f"{key}-paper-1", stage="PAPER", kind="paper",
        arms={f"arm{i}": t for i, t in enumerate(tags)}, started_at=at,
    )
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    s.commit()
    return {"exp": exp, "ver": ver, "epoch": epoch, "dep": dep}


def _env(*, command_id, target="RETIRED", experiment="freeze-shaped",
         actor_role="RESEARCH_LAB", **payload):
    body = {"experiment": experiment, "target": target,
            "approved_by": "cal", "reason": "no available universe satisfies "
                                            "the hypothesis"}
    body.update(payload)
    return {
        "command_id": command_id, "action": "STAND_DOWN", "actor": "claude-code",
        "actor_role": actor_role, "payload": body, "schema_version": 1,
    }


def _run(s, envelope):
    out = xc.execute_envelope(s, envelope)
    s.commit()
    return out


# ---------------------------------------------------------------------------
# It does the thing
# ---------------------------------------------------------------------------


def test_retiring_a_paper_book_also_ends_its_deployments(xos_session,
                                                         xos_platform):
    """RETIRED is terminal, so leaving deployments open would leave rows claiming
    to be running under an experiment that is over — the XOS-000011 shape, where
    the record and the admission resolver disagreed and four books went dark for
    four days without anything saying so."""
    s = xos_session
    book = _paper_book(s)
    out = _run(s, _env(command_id="xcmd-retire-1"))

    assert out["status"] == "SUCCEEDED", out
    assert book["exp"].state == LifecycleState.RETIRED.value
    assert book["epoch"].ended_at is not None, "the open epoch is closed"
    assert book["dep"].ended_at is not None, "and its deployment ended with it"
    assert out["result"]["ended_deployments"] == ["freeze-shaped-paper-1"]

    last = read.transitions_for(s, book["exp"])[-1]
    assert last.to_state == "RETIRED"
    assert last.approved_by == "cal", "the audit row names the person who decided"
    assert "universe" in (last.reason or "")


def test_pausing_leaves_the_epoch_open(xos_session, xos_platform):
    """A pause is meant to be resumable into the SAME operating interval, so it
    deliberately does not close the epoch. What stops a paused book trading is the
    runtime allowlist and its recorded stand-down, not a closed epoch."""
    s = xos_session
    book = _paper_book(s)
    out = _run(s, _env(command_id="xcmd-pause-1", target="PAUSED"))

    assert out["status"] == "SUCCEEDED", out
    assert book["exp"].state == LifecycleState.PAUSED.value
    assert book["epoch"].ended_at is None
    assert book["dep"].ended_at is None
    assert "closed_epoch" not in out["result"], (
        "no epoch was closed, so the receipt reports none rather than a null"
    )


def test_a_paused_experiment_can_still_be_retired(xos_session, xos_platform):
    """PAUSED → RETIRED is the path XOS-000003's disposition leaves open, so the
    verb has to be able to walk it rather than stranding a paused book."""
    s = xos_session
    book = _paper_book(s)
    _run(s, _env(command_id="xcmd-pause-2", target="PAUSED"))
    _run(s, _env(command_id="xcmd-retire-2", target="RETIRED"))
    assert book["exp"].state == LifecycleState.RETIRED.value
    assert book["epoch"].ended_at is not None


# ---------------------------------------------------------------------------
# The refusals, which are the point
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["PAPER", "LIVE_CANARY", "PRODUCTION",
                                    "PROBE", "IDEA", "retired", ""])
def test_it_can_never_advance_a_stage(xos_session, xos_platform, target):
    """MUST REFUSE: this verb holds or ends a line of research and can never
    advance one. Every other target is unrepresentable, so a promotion cannot be
    reached through it even by a typo — that still needs a reviewed package and a
    recorded evaluator PASS."""
    s = xos_session
    _paper_book(s)
    out = _run(s, _env(command_id=f"xcmd-bad-{abs(hash(target)) % 9999}",
                       target=target))
    assert out["status"] == "REJECTED"
    assert "target must be one of" in (out["error"] or "")
    assert read.get_experiment(s, "freeze-shaped").state == "PAPER"


def test_an_open_live_deployment_is_refused(xos_session, xos_platform):
    """MUST REFUSE: real-money exposure already has audited paths for stopping —
    the kill switch, the runtime allowlist, the recorded stand-down integrity
    event. A second one reachable by naming a key in a PUBLIC envelope is exactly
    the parallel mechanism Experiment OS exists to remove."""
    s = xos_session
    at = _now() - timedelta(days=3)
    exp = svc.create_experiment(s, key="live-book", origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="lever", now=at,
        control_exemption_reason="test shape",
    )
    svc.add_arm(s, ver, arm_key="a", role="treatment", strategy_tag="La")
    svc.freeze_version(s, ver, now=at)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=at)
    svc.register_deployment(
        s, epoch, deployment_key="live-book-live-1", stage="LIVE_CANARY",
        kind="live", arms={"a": "La"}, started_at=at, _sanctioned_canary=True,
    )
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    s.commit()

    out = _run(s, _env(command_id="xcmd-live-refuse", experiment="live-book"))
    assert out["status"] == "REJECTED"
    assert "open LIVE deployment" in (out["error"] or "")
    assert exp.state == "PAPER", "and nothing moved"
    assert epoch.ended_at is None, "and no epoch was closed on the way out"


def test_a_no_op_is_refused(xos_session, xos_platform):
    """MUST REFUSE: a transition row that records no change reads, a year later,
    as a decision that was taken. It was not."""
    s = xos_session
    _paper_book(s)
    _run(s, _env(command_id="xcmd-retire-3"))
    out = _run(s, _env(command_id="xcmd-retire-4"))
    assert out["status"] == "REJECTED"
    assert "is already" in (out["error"] or "")


def test_an_unknown_experiment_is_refused_by_name(xos_session, xos_platform):
    s = xos_session
    _paper_book(s)
    out = _run(s, _env(command_id="xcmd-unknown-exp", experiment="not-a-book"))
    assert out["status"] == "REJECTED"
    assert "does not exist" in (out["error"] or "")


@pytest.mark.parametrize("missing,needle", [
    ("approved_by", "approved_by must name"),
    ("reason", "reason is required"),
])
def test_the_decision_must_name_a_person_and_a_reason(xos_session, xos_platform,
                                                      missing, needle):
    """Ending a line of research is a decision somebody made. The audit row says
    who, and why — a process name in that column documents nothing."""
    s = xos_session
    _paper_book(s)
    out = _run(s, _env(command_id=f"xcmd-no-{missing[:6]}", **{missing: "   "}))
    assert out["status"] == "REJECTED"
    assert needle in (out["error"] or "")
    assert read.get_experiment(s, "freeze-shaped").state == "PAPER"


def test_platform_change_review_may_not_stand_an_experiment_down(xos_session,
                                                                 xos_platform):
    """Role is attribution, not authorization — but a receipt naming the wrong
    role misdescribes who decided, so the allowlist is still enforced."""
    s = xos_session
    _paper_book(s)
    with pytest.raises(xc.ExperimentCommandRejected):
        xc.execute_envelope(s, _env(command_id="xcmd-role",
                                    actor_role="PLATFORM_CHANGE_REVIEW"))
    assert read.get_experiment(s, "freeze-shaped").state == "PAPER"


def test_the_envelope_cannot_smuggle_extra_fields(xos_session, xos_platform):
    """Unknown payload fields are refused, not ignored — the same rule the rest of
    the vocabulary follows, so a typo cannot become a silent default."""
    s = xos_session
    _paper_book(s)
    out = _run(s, _env(command_id="xcmd-extra", gate_spec="{}"))
    assert out["status"] == "REJECTED"
    assert read.get_experiment(s, "freeze-shaped").state == "PAPER"


def test_it_records_no_pass_and_authorizes_nothing(xos_session, xos_platform):
    """A stand-down is not a verdict. It writes no gate result at all, so nothing
    it produces can ever be read as authorization for a promotion."""
    from kalshi_bot.experiment_os.models import ExperimentGateResult

    s = xos_session
    book = _paper_book(s)
    before = s.query(ExperimentGateResult).count()
    _run(s, _env(command_id="xcmd-noauth"))
    assert s.query(ExperimentGateResult).count() == before
    assert book["exp"].state == LifecycleState.RETIRED.value


def test_a_blank_approver_is_refused_on_every_verb_that_takes_one(xos_session,
                                                                  xos_platform):
    """`_ACTOR_RE` permits spaces — legitimately, since a person's name may
    contain one — which meant `approved_by="   "` satisfied every caller of it and
    wrote a BLANK approver onto the transition.

    Found while testing STAND_DOWN and fixed across all three verbs that take an
    approver, because the worst instance was not this one: on ARM_CANARY it meant
    a real-money arming whose audit row named nobody, which is that column's only
    job. Naming who approved is the whole reason the field is required."""
    s = xos_session
    _paper_book(s)
    for blank in ("   ", "", "\\t"):
        out = _run(s, _env(command_id=f"xcmd-blank-{len(blank)}-{ord(blank[0]) if blank else 0}",
                           approved_by=blank))
        assert out["status"] == "REJECTED"
        assert "approved_by must name" in (out["error"] or "")
    assert read.get_experiment(s, "freeze-shaped").state == "PAPER"


def test_an_approver_is_recorded_stripped(xos_session, xos_platform):
    """Surrounding whitespace is trimmed rather than stored, so the audit row
    reads as the name it is."""
    s = xos_session
    book = _paper_book(s)
    _run(s, _env(command_id="xcmd-strip-1", approved_by="  cal  "))
    assert read.transitions_for(s, book["exp"])[-1].approved_by == "cal"


# ---------------------------------------------------------------------------
# Precedence over RETIRE_ON_GATE_FAIL
# ---------------------------------------------------------------------------


def test_it_defers_to_retire_on_gate_fail_when_that_verb_applies(xos_session,
                                                                 xos_platform):
    """MUST REFUSE: `RETIRE_ON_GATE_FAIL` takes its authorization from a FAIL the
    EVALUATOR already computed — a caller can only point at a failure there, never
    assert one. Two paths to the same terminal state, one of which quietly
    discards that authority, is the parallel mechanism Experiment OS exists to
    remove. So where the stronger verb applies, this one stands aside and says so.
    """
    s = xos_session
    book = _paper_book(s)
    gate = svc.register_gate(
        s, book["ver"], gate_key="paper_keep", kind="monitoring",
        spec={"pass_all": [{"metric": "pnl_cents_per_trade", "arm": "arm0",
                            "op": ">", "value": 0}]},
        registered_at=_now() - timedelta(days=20),
    )
    svc.mark_gate_evidence_started(s, gate, at=_now() - timedelta(days=20))
    svc.record_gate_result(s, gate, verdict="FAIL",
                           computed_at=_now() - timedelta(days=1),
                           explanation="the kill clause fired")
    s.commit()

    out = _run(s, _env(command_id="xcmd-defer-1"))
    assert out["status"] == "REJECTED"
    assert "RETIRE_ON_GATE_FAIL" in (out["error"] or "")
    assert book["exp"].state == "PAPER", "and nothing moved"


def test_a_hold_is_not_a_fail_so_this_verb_is_the_one_that_applies(xos_session,
                                                                   xos_platform):
    """freeze-dark-window-pin's actual shape. Its gate reads HOLD ("sample floor
    not met: settled_trades=0 vs >= 150") and can never read anything else — the
    book has written zero rows and no Kalshi series exists to test the hypothesis
    on. A gate cannot FAIL on evidence that cannot exist, so waiting for the
    stronger verb means waiting forever while four tagged books stay recorded as
    an active PAPER deployment."""
    s = xos_session
    book = _paper_book(s)
    gate = svc.register_gate(
        s, book["ver"], gate_key="paper_to_live_canary", kind="promotion",
        spec={"sample": {"arm0": {"metric": "settled_trades", "op": ">=",
                                  "value": 150}},
              "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "arm0",
                            "op": ">", "value": 0}]},
        from_state="PAPER", to_state="LIVE_CANARY",
        registered_at=_now() - timedelta(days=24),
    )
    svc.mark_gate_evidence_started(s, gate, at=_now() - timedelta(days=24))
    svc.record_gate_result(s, gate, verdict="HOLD",
                           computed_at=_now() - timedelta(days=1),
                           explanation="sample floor not met: settled_trades=0")
    s.commit()

    out = _run(s, _env(command_id="xcmd-hold-1"))
    assert out["status"] == "SUCCEEDED", out
    assert book["exp"].state == LifecycleState.RETIRED.value
    assert book["epoch"].ended_at is not None


def test_a_superseded_fail_does_not_block_it(xos_session, xos_platform):
    """Only the LATEST recorded verdict decides which verb applies. A FAIL that a
    later evaluation replaced is history, not a live authorization."""
    s = xos_session
    book = _paper_book(s)
    gate = svc.register_gate(
        s, book["ver"], gate_key="paper_keep", kind="monitoring",
        spec={"pass_all": [{"metric": "pnl_cents_per_trade", "arm": "arm0",
                            "op": ">", "value": 0}]},
        registered_at=_now() - timedelta(days=20),
    )
    svc.mark_gate_evidence_started(s, gate, at=_now() - timedelta(days=20))
    svc.record_gate_result(s, gate, verdict="FAIL",
                           computed_at=_now() - timedelta(days=5))
    svc.record_gate_result(s, gate, verdict="HOLD",
                           computed_at=_now() - timedelta(days=1))
    s.commit()
    out = _run(s, _env(command_id="xcmd-superseded-1"))
    assert out["status"] == "SUCCEEDED", out
    assert book["exp"].state == LifecycleState.RETIRED.value


def test_pausing_is_allowed_even_with_a_failing_gate(xos_session, xos_platform):
    """The precedence rule guards the TERMINAL state only. Holding a failing book
    while someone decides what to do with it is a different act from ending it,
    and the stronger verb has no pause."""
    s = xos_session
    book = _paper_book(s)
    gate = svc.register_gate(
        s, book["ver"], gate_key="paper_keep", kind="monitoring",
        spec={"pass_all": [{"metric": "pnl_cents_per_trade", "arm": "arm0",
                            "op": ">", "value": 0}]},
        registered_at=_now() - timedelta(days=20),
    )
    svc.mark_gate_evidence_started(s, gate, at=_now() - timedelta(days=20))
    svc.record_gate_result(s, gate, verdict="FAIL",
                           computed_at=_now() - timedelta(days=1))
    s.commit()
    out = _run(s, _env(command_id="xcmd-pause-fail-1", target="PAUSED"))
    assert out["status"] == "SUCCEEDED", out
    assert book["exp"].state == LifecycleState.PAUSED.value
