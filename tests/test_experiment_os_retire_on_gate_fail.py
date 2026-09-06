"""Retiring an in-system book on its own recorded gate FAIL.

The gap: a book could fail its own pre-registered kill clause and go on trading
indefinitely, because nothing could retire it in production. The ops channel is
read-only against Postgres, the issue transport cannot transition an experiment
by construction, the platform transport has no retirement in its vocabulary, and
`close_out_retrospective` refuses a TAGGED deployment — correctly, since a tag
means the book really traded. So the decision lived in chat while the Control
Tower reported "kill/retire candidate" on every read.

Closing that gap safely means the authorization can never come from the caller.
These tests are about what the path REFUSES:

  * a stale FAIL the book has since recovered from;
  * a BLOCKED_* gate — unreadable is not failed, and retiring on a block would
    destroy the evidence needed to unblock it;
  * a gate belonging to some other book;
  * a LIVE_CANARY or PRODUCTION experiment, where standing money down is Live
    Ops' act under kill-switch semantics and never a research verdict's.

Plus the two positive properties: the deployments end atomically with the
transition (a retired experiment holding an open deployment is XOS-000011's
shape), and the retired book's tag stops being admissible to the write path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot.experiment_os import experiment_commands as xc
from kalshi_bot.experiment_os import read
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.service import ExperimentOsError

UTC = timezone.utc
T0 = datetime(2026, 8, 1, tzinfo=UTC)


def _book(s, key="doomed-book", *, tag="doomed1"):
    """A registered PAPER book with a frozen contract, an open epoch, a tagged
    paper deployment, and a promotion gate — the shape of a real failing book."""
    exp = svc.create_experiment(s, key=key, origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="lever", now=T0,
        control_exemption_reason="test shape",
    )
    svc.add_arm(s, ver, arm_key="treatment", role="treatment", strategy_tag=tag)
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    svc.register_deployment(
        s, epoch, deployment_key=f"{key}-paper", stage="PAPER", kind="paper",
        arms={"treatment": tag}, started_at=T0,
    )
    gate = svc.register_gate(
        s, ver, gate_key="paper_keep", kind="promotion",
        spec={"pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                            "op": ">", "value": 0}]},
        from_state="PAPER", to_state="LIVE_CANARY", registered_at=T0,
    )
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    s.commit()
    return exp, ver, epoch, gate


def _verdict(s, gate, verdict, *, at, epoch):
    return svc.record_gate_result(
        s, gate, verdict=verdict, computed_at=at, computed_by="system", epoch=epoch,
        explanation=f"test: {verdict}",
    )


def _retire(s, exp, gate, **kw):
    kw.setdefault("actor", "claude-research-lab")
    kw.setdefault("approved_by", "cal")
    kw.setdefault("reason", "the book failed its own pre-registered kill clause")
    return svc.retire_on_gate_fail(s, exp, gate=gate, **kw)


# ---------------------------------------------------------------------------
# The act
# ---------------------------------------------------------------------------


def test_a_current_FAIL_retires_the_book_and_ends_its_deployments(
    xos_session, xos_platform
):
    """The whole point: a clean failure is useful research and must be recordable.
    Ending the deployments is part of the same atomic act — a RETIRED experiment
    holding an OPEN deployment reads as running research and is the shape behind
    XOS-000011."""
    s = xos_session
    exp, ver, epoch, gate = _book(s)
    result = _verdict(s, gate, "FAIL", at=T0 + timedelta(days=5), epoch=epoch)
    s.commit()

    out = _retire(s, exp, gate)
    s.commit()

    assert exp.state == "RETIRED"
    assert exp.retired_at is not None
    assert out["authorizing_result_id"] == result.id
    assert out["authorizing_verdict"] == "FAIL"
    assert out["deployments_ended"] == ["doomed-book-paper"]
    assert all(d.ended_at is not None
               for e in read.epochs_for(s, ver)
               for d in read.deployments_for(s, e))


def test_the_retirement_names_the_operator_and_the_authorizing_result(
    xos_session, xos_platform
):
    """Retiring a book is an operator decision; the audit row must name a person
    rather than a process, and point at the verdict that justified it."""
    s = xos_session
    exp, ver, epoch, gate = _book(s)
    result = _verdict(s, gate, "FAIL", at=T0 + timedelta(days=5), epoch=epoch)
    s.commit()
    _retire(s, exp, gate, actor="claude-research-lab", approved_by="cal",
            reason="delta is negative at 19x the sample floor")
    s.commit()

    transitions = read.transitions_for(s, exp)
    last = transitions[-1]
    assert last.to_state == "RETIRED"
    assert last.actor == "claude-research-lab"
    assert last.approved_by == "cal"
    assert "19x the sample floor" in last.reason
    assert last.gate_result_id == result.id


def test_a_retired_books_tag_is_no_longer_admissible(xos_session, xos_platform):
    """Retirement is what actually stops the trading: the enforcement resolver
    excludes retired experiments, so the tag drops out of the admissible map."""
    from kalshi_bot.experiment_os import enforcement

    s = xos_session
    exp, ver, epoch, gate = _book(s)
    _verdict(s, gate, "FAIL", at=T0 + timedelta(days=5), epoch=epoch)
    s.commit()
    enforcement.refresh(s)
    assert "doomed1" in (enforcement._STATE.tag_map if enforcement._STATE else {})

    _retire(s, exp, gate)
    s.commit()
    enforcement.refresh(s)
    assert "doomed1" not in (enforcement._STATE.tag_map if enforcement._STATE else {})


# ---------------------------------------------------------------------------
# What it refuses — the authorization is never the caller's to assert
# ---------------------------------------------------------------------------


def test_a_STALE_fail_the_book_recovered_from_authorizes_nothing(
    xos_session, xos_platform
):
    """The check is the LATEST recorded result, not "any FAIL in history". A book
    that failed in August and has since recovered is not retirable on that."""
    s = xos_session
    exp, ver, epoch, gate = _book(s)
    _verdict(s, gate, "FAIL", at=T0 + timedelta(days=2), epoch=epoch)
    _verdict(s, gate, "HOLD", at=T0 + timedelta(days=9), epoch=epoch)
    s.commit()
    with pytest.raises(ExperimentOsError) as exc:
        _retire(s, exp, gate)
    assert "latest recorded result is HOLD" in str(exc.value)
    assert exp.state == "PAPER"


@pytest.mark.parametrize(
    "verdict", ["BLOCKED_PLATFORM", "BLOCKED_DATA", "BLOCKED_INTEGRITY"]
)
def test_a_BLOCKED_gate_is_UNREADABLE_not_failed(xos_session, xos_platform, verdict):
    """Retiring on a block would destroy exactly the evidence someone needs to
    unblock it — which is the failure mmsell-anchor-vol-entry spent two weeks in."""
    s = xos_session
    exp, ver, epoch, gate = _book(s)
    _verdict(s, gate, verdict, at=T0 + timedelta(days=5), epoch=epoch)
    s.commit()
    with pytest.raises(ExperimentOsError) as exc:
        _retire(s, exp, gate)
    assert "unreadable rather than failed" in str(exc.value)
    assert exp.state == "PAPER"


def test_a_gate_with_no_recorded_result_authorizes_nothing(xos_session, xos_platform):
    """A retirement needs the evaluator's own verdict, not an assertion."""
    s = xos_session
    exp, ver, epoch, gate = _book(s)
    with pytest.raises(ExperimentOsError) as exc:
        _retire(s, exp, gate)
    assert "no recorded result" in str(exc.value)
    assert exp.state == "PAPER"


def test_another_books_gate_cannot_retire_this_one(xos_session, xos_platform):
    """A retirement must be authorized by that book's OWN pre-registered gate."""
    s = xos_session
    exp, ver, epoch, gate = _book(s, key="doomed-book", tag="doomed1")
    other, _, other_epoch, other_gate = _book(s, key="other-book", tag="other1")
    _verdict(s, other_gate, "FAIL", at=T0 + timedelta(days=5), epoch=other_epoch)
    s.commit()
    with pytest.raises(ExperimentOsError) as exc:
        _retire(s, exp, other_gate)
    assert "does not belong to experiment" in str(exc.value)
    assert exp.state == "PAPER"


def test_a_LIVE_experiment_is_refused_outright(xos_session, xos_platform):
    """Standing REAL MONEY down is Live Ops' act under kill-switch semantics, with
    its own operator confirmation. Routing it through a research-verdict path would
    let a gate verdict move money, and no verdict may ever do that."""
    s = xos_session
    exp, ver, epoch, gate = _book(s)
    result = _verdict(s, gate, "PASS", at=T0 + timedelta(days=4), epoch=epoch)
    svc.transition_experiment(
        s, exp, "LIVE_CANARY", actor="operator", approved_by="cal",
        gate_result=result,
    )
    _verdict(s, gate, "FAIL", at=T0 + timedelta(days=6), epoch=epoch)
    s.commit()
    with pytest.raises(ExperimentOsError) as exc:
        _retire(s, exp, gate)
    assert "REAL MONEY" in str(exc.value)
    assert exp.state == "LIVE_CANARY"


def test_retiring_twice_is_refused(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch, gate = _book(s)
    _verdict(s, gate, "FAIL", at=T0 + timedelta(days=5), epoch=epoch)
    s.commit()
    _retire(s, exp, gate)
    s.commit()
    with pytest.raises(ExperimentOsError) as exc:
        _retire(s, exp, gate)
    assert "already RETIRED" in str(exc.value)


# ---------------------------------------------------------------------------
# Through the transport
# ---------------------------------------------------------------------------


def _envelope(**payload):
    body = {"experiment": "doomed-book", "gate": "paper_keep",
            "approved_by": "cal", "reason": "failed its own kill clause"}
    body.update(payload)
    return {
        "command_id": "retire-0000001",
        "action": "RETIRE_ON_GATE_FAIL",
        "actor": "claude-research-lab",
        "actor_role": "RESEARCH_LAB",
        "payload": body,
        "schema_version": 1,
    }


def test_the_transport_retires_on_a_recorded_fail(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch, gate = _book(s)
    _verdict(s, gate, "FAIL", at=T0 + timedelta(days=5), epoch=epoch)
    s.commit()
    view = xc.execute_envelope(s, _envelope())
    assert view["status"] == "SUCCEEDED", view.get("error")
    assert view["result"]["state"] == "RETIRED"
    assert view["result"]["deployments_ended"] == ["doomed-book-paper"]


def test_the_transport_records_a_REJECTED_receipt_rather_than_retiring(
    xos_session, xos_platform
):
    """A refusal is the system working, so it is recorded, not raised — the
    operator reads back why nothing happened."""
    s = xos_session
    exp, ver, epoch, gate = _book(s)
    _verdict(s, gate, "HOLD", at=T0 + timedelta(days=5), epoch=epoch)
    s.commit()
    view = xc.execute_envelope(s, _envelope())
    assert view["status"] == "REJECTED"
    assert exp.state == "PAPER"


def test_the_transport_refuses_an_unknown_experiment_or_gate(
    xos_session, xos_platform
):
    s = xos_session
    _book(s)
    s.commit()
    missing_exp = xc.execute_envelope(
        s, _envelope(experiment="no-such-book") | {"command_id": "retire-0000002"}
    )
    assert missing_exp["status"] == "REJECTED"
    missing_gate = xc.execute_envelope(
        s, _envelope(gate="no_such_gate") | {"command_id": "retire-0000003"}
    )
    assert missing_gate["status"] == "REJECTED"


def test_the_action_cannot_be_authored_by_a_read_only_role(xos_session, xos_platform):
    """The Control Tower reports the kill candidate; it never performs it."""
    assert "EXPERIMENT_CONTROL_TOWER" not in xc.ACTION_ROLES["RETIRE_ON_GATE_FAIL"]
    assert "RESEARCH_LAB" in xc.ACTION_ROLES["RETIRE_ON_GATE_FAIL"]


def test_the_action_writes_no_verdict_and_arms_nothing(xos_session, xos_platform):
    """The narrow-door property: this action points at a verdict, it never writes
    one, and it cannot reach real-money capability."""
    s = xos_session
    exp, ver, epoch, gate = _book(s)
    _verdict(s, gate, "FAIL", at=T0 + timedelta(days=5), epoch=epoch)
    s.commit()
    before = len(read.gate_results_for(s, gate))
    xc.execute_envelope(s, _envelope())
    s.commit()
    assert len(read.gate_results_for(s, gate)) == before
    spec = xc.ACTIONS["RETIRE_ON_GATE_FAIL"]
    assert spec.required == {"experiment", "gate", "approved_by", "reason"}
    assert spec.optional == frozenset()
