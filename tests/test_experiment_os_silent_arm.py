"""`experiment.armed_but_silent` — registration is not configuration.

## The incident this file exists because of

2026-09-05, `mmsell-correlation-cap`. Every Experiment OS object was correct:
the experiment registered at PAPER, v1 frozen, two arms with tags, deployments
25 → 27, both arms armed, the keep gate registered. And both paper books
(`Gmmsell0`, `Gmmsell1`) then traded NOTHING for twelve hours — zero rows in
`paper_trades`, not zero trades — while `mmsell10` booked 166 trades in the same
window on the same worker.

The cause was that `MMSELL_VARIANTS` is a Railway environment variable that
OVERRIDES the code default in `config.py`, and the two books had been added to
the code default only. The worker never saw them. **Registration is not
configuration**, and nothing in the system knew the difference.

For twelve hours the Control Tower reported the experiment as PAPER and healthy.
No error, no warning, no blocked-gate reason: the gate was simply un-evaluable at
n=0, and n=0 is indistinguishable from "collecting, early days". A human asking
"is it working yet" is what found it.

## Why the existing detector was not enough

`experiment.zero_evidence` already fires the instant a book is registered with no
trades, rated LOW/P2, with the cause explicitly unknown. That is correct and it
stays — but it fires on day zero for every legitimately new experiment too, so on
its own it cannot say whether the silence is *early* or *broken*. XOS-000011 is
the same shape from the other side: the Tower saw only `experiment.zero_evidence`
LOW/P2, four days downstream of the cause.

`experiment.armed_but_silent` is the escalation: zero evidence that has lasted
past a settlement day, with no blocked gate to explain it, and with a REFERENCE
proving the silence is specific to this arm rather than to the day — a sibling
arm in the same epoch that traded, or the rest of the platform trading while this
arm booked nothing.

## What these tests are for

The positive case is one test. The rest are non-fire cases, and they matter more:
a detector that cries wolf is worse than no detector, because it is ignored
within a week and then the real one is ignored too.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot.experiment_os import control_tower as ct
from kalshi_bot.experiment_os import issue_policy as ipol
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.models import PaperTrade

UTC = timezone.utc

DETECTOR = "experiment.armed_but_silent"

#: A gate that is computable from real metrics, so nothing here is BLOCKED_DATA
#: by accident — the blocked-gate suppression must be tested deliberately, not
#: reached by a fixture that happens to name a provider nobody wrote.
KEEP_SPEC = {
    "sample": {"contest_capped": {"metric": "settled_trades", "op": ">=",
                                 "value": 60}},
    "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "contest_capped",
                  "op": ">", "value": 0}],
}
#: A gate whose metric has no provider — the BLOCKED_DATA shape.
UNPROVIDED_SPEC = {
    "pass_all": [{"metric": "candidate_rejection_rate_pct",
                  "arm": "contest_capped", "op": ">", "value": 0}],
}


def _now() -> datetime:
    return datetime.now(UTC)


def _trade(s, tag, at: datetime, *, n: int = 1) -> None:
    for i in range(n):
        s.add(PaperTrade(
            market_ticker=f"KX-{tag}-{i}-{at.timestamp()}", strategy=tag,
            status="settled", pnl=0.03, quantity=1,
            created_at=at + timedelta(seconds=i),
        ))


def _two_arm_paper_book(
    s, key, *, armed_hours_ago: float, spec=KEEP_SPEC,
    control_tag=None, treatment_tag=None, tagless=False,
):
    """The 2026-09-05 shape: PAPER, v1 frozen, a control and a treatment arm,
    one active paper deployment carrying both tags, armed `armed_hours_ago`."""
    armed = _now() - timedelta(hours=armed_hours_ago)
    control_tag = control_tag or f"{key}-c"
    treatment_tag = treatment_tag or f"{key}-t"
    exp = svc.create_experiment(s, key=key, origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="a cap on correlated exposure improves daily stability",
        independent_variable="contest cap", now=armed,
    )
    svc.add_arm(s, ver, arm_key="uncapped", role="control",
                strategy_tag=control_tag)
    svc.add_arm(s, ver, arm_key="contest_capped", role="treatment",
                strategy_tag=treatment_tag)
    svc.freeze_version(s, ver, now=armed)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=armed)
    if tagless:
        # The `correlation_cap.py` probe shape: arms declared, tags deliberately
        # absent, because a counterfactual replay places no order.
        dep = svc.register_deployment(
            s, epoch, deployment_key=f"{key}-probe-1", stage="PROBE",
            kind="probe", arms={"uncapped": None, "contest_capped": None},
            started_at=armed, notes="TAGLESS BY CONSTRUCTION",
        )
    else:
        dep = svc.register_deployment(
            s, epoch, deployment_key=f"{key}-paper-1", stage="PAPER",
            kind="paper",
            arms={"uncapped": control_tag, "contest_capped": treatment_tag},
            started_at=armed,
        )
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    gate = svc.register_gate(
        s, ver, gate_key="paper_keep", kind="monitoring", spec=spec,
        registered_at=armed,
    )
    svc.mark_gate_evidence_started(s, gate, at=armed)
    s.commit()
    return {"exp": exp, "ver": ver, "epoch": epoch, "deployment": dep,
            "gate": gate, "armed": armed,
            "control_tag": control_tag, "treatment_tag": treatment_tag}


def _fired(rep, key=None) -> list[dict]:
    return [c for c in rep.issue_candidates
            if c["detector"] == DETECTOR
            and (key is None or c["experiment"] == key)]


# ---------------------------------------------------------------------------
# 1. The real incident
# ---------------------------------------------------------------------------


def test_it_catches_the_2026_09_05_correlation_cap_incident(xos_session,
                                                            xos_platform):
    """THE REPRODUCTION. `mmsell-correlation-cap` armed twelve hours ago with
    both arms at zero rows, while `mmsell10` books trades on the same worker.

    Twelve hours is under the threshold on purpose in the sibling-less case — the
    point of the incident is that it ran 12h and then kept running. Here it has
    been armed for 26h, which is what the twelfth hour was on its way to."""
    s = xos_session
    book = _two_arm_paper_book(s, "mmsell-correlation-cap", armed_hours_ago=26)
    # The rest of the platform is demonstrably alive on the same worker.
    _trade(s, "mmsell10", _now() - timedelta(hours=20), n=166)
    s.commit()

    rep = ct.build_report(s, evaluate=True)
    fired = _fired(rep, "mmsell-correlation-cap")
    assert len(fired) == 2, (
        "both armed arms are silent, so both are findings — the shared cause is "
        "the diagnosis, not an assumption the detector may make"
    )
    arms = {c["anomaly_kind"] for c in fired}
    assert arms == {"uncapped:paper", "contest_capped:paper"}, (
        "one finding per (arm x deployment kind) — the kind is part of the scope"
    )
    detail = fired[0]["detail"] or ""
    assert "166" in detail, "the reference that localizes the silence is stated"
    assert book["control_tag"] in " ".join(c["detail"] or "" for c in fired)
    assert fired[0]["recommended_owner"] == "LIVE_OPS"
    assert fired[0]["recommended_classification"] == "UNCLASSIFIED", (
        "the cause is genuinely not established — guessing it produces a ticket "
        "the owning role cannot act on"
    )
    assert fired[0]["priority"] == "P1"


def test_giving_one_arm_trades_stops_that_arm_firing(xos_session, xos_platform):
    """The fix on 2026-09-06 was adding the books to `MMSELL_VARIANTS`; first
    trades landed three minutes later. The detector must go quiet for an arm the
    moment it produces evidence, and stay loud for one that still does not."""
    s = xos_session
    book = _two_arm_paper_book(s, "mmsell-correlation-cap", armed_hours_ago=26)
    _trade(s, "mmsell10", _now() - timedelta(hours=20), n=166)
    _trade(s, book["control_tag"], _now() - timedelta(hours=2), n=30)
    s.commit()

    fired = _fired(ct.build_report(s, evaluate=True), "mmsell-correlation-cap")
    assert {c["anomaly_kind"] for c in fired} == {"contest_capped:paper"}, (
        "the arm that started trading is no longer silent; its sibling still is"
    )
    assert "uncapped" in (fired[0]["detail"] or ""), (
        "and the still-silent arm is now indicted by its own sibling, which is "
        "strictly stronger evidence than the platform reference"
    )


def test_both_arms_trading_produces_nothing(xos_session, xos_platform):
    s = xos_session
    book = _two_arm_paper_book(s, "mmsell-correlation-cap", armed_hours_ago=26)
    _trade(s, book["control_tag"], _now() - timedelta(hours=2), n=30)
    _trade(s, book["treatment_tag"], _now() - timedelta(hours=2), n=25)
    s.commit()
    assert not _fired(ct.build_report(s, evaluate=True))


# ---------------------------------------------------------------------------
# 2. Non-fire cases — these matter more than the positive one
# ---------------------------------------------------------------------------


def test_an_experiment_armed_an_hour_ago_does_not_fire(xos_session, xos_platform):
    """MUST NOT FIRE: early is the ordinary state of a new experiment. A book
    registered this hour has not had a chance to find a candidate yet, and a
    detector that fires on every registration is one an operator learns to
    dismiss — at which point it is not a detector."""
    s = xos_session
    _two_arm_paper_book(s, "fresh-book", armed_hours_ago=1)
    _trade(s, "mmsell10", _now() - timedelta(minutes=50), n=166)
    s.commit()
    assert not _fired(ct.build_report(s, evaluate=True))


def test_the_threshold_boundary_is_pinned(xos_session, xos_platform):
    """The boundary is load-bearing, so it is pinned rather than left to whoever
    next edits the comparison. Two identical books, one an hour either side."""
    s = xos_session
    _two_arm_paper_book(s, "just-under", armed_hours_ago=ct.SILENT_ARM_HOURS - 1)
    _two_arm_paper_book(s, "just-over", armed_hours_ago=ct.SILENT_ARM_HOURS + 1)
    _trade(s, "mmsell10", _now() - timedelta(hours=1), n=166)
    s.commit()
    rep = ct.build_report(s, evaluate=True)
    assert not _fired(rep, "just-under")
    assert _fired(rep, "just-over")


def test_a_blocked_gate_already_explains_the_zero(xos_session, xos_platform):
    """MUST NOT FIRE: a BLOCKED_DATA / BLOCKED_PLATFORM gate is already reported,
    already routed to an owner, and already the reason this experiment's numbers
    cannot be interpreted. Firing again here would be a second ticket for a
    condition that already has one, aimed at a different role."""
    s = xos_session
    _two_arm_paper_book(s, "blocked-book", armed_hours_ago=48,
                        spec=UNPROVIDED_SPEC)
    _trade(s, "mmsell10", _now() - timedelta(hours=40), n=166)
    s.commit()
    rep = ct.build_report(s, evaluate=True)
    assert any(b["experiment"] == "blocked-book" for b in rep.blocked), (
        "the fixture really is blocked — otherwise this test proves nothing"
    )
    assert not _fired(rep, "blocked-book")


def test_a_tagless_probe_deployment_never_fires(xos_session, xos_platform):
    """MUST NOT FIRE: a PROBE deployment is TAGLESS BY CONSTRUCTION. The
    counterfactual replay that stands in for `mmsell-correlation-cap`'s probe is
    registered with no strategy tags at all (`correlation_cap.py`), so "this arm
    has booked nothing" is not a finding about it — there is nothing to book."""
    s = xos_session
    _two_arm_paper_book(s, "tagless-probe-book", armed_hours_ago=48, tagless=True)
    _trade(s, "mmsell10", _now() - timedelta(hours=40), n=166)
    s.commit()
    assert not _fired(ct.build_report(s, evaluate=True))


def test_a_stood_down_book_never_fires(xos_session, xos_platform):
    """MUST NOT FIRE: `EXPERIMENT_EXECUTION_STOOD_DOWN` is a RECORDED STATE, not
    a failure. The absence of evidence is already explained, so opening an
    investigation into it asks a question the system has already answered — the
    same rule that keeps INACTIVE collectors out of the action list."""
    s = xos_session
    book = _two_arm_paper_book(s, "stood-down-book", armed_hours_ago=48)
    svc.record_integrity_event(
        s, book["exp"], kind="EXPERIMENT_EXECUTION_STOOD_DOWN",
        description="deliberately stood down pending the capacity review",
    )
    _trade(s, "mmsell10", _now() - timedelta(hours=40), n=166)
    s.commit()
    rep = ct.build_report(s, evaluate=True)
    assert not _fired(rep, "stood-down-book")
    assert rep.recorded_state, "and it is still visible as recorded state"


def test_a_flow_constrained_book_that_trades_slowly_never_fires(xos_session,
                                                                xos_platform):
    """MUST NOT FIRE: `Tmmsell2` finds roughly one entry an hour. Slow is not
    silent. The detector's condition is ZERO evidence, never a rate — an absolute
    volume threshold would have to be tuned per book, and a detector that needs
    tuning does not get used."""
    s = xos_session
    book = _two_arm_paper_book(s, "flow-constrained-book", armed_hours_ago=48)
    # ~1/hour on the treatment arm, and a busy control beside it.
    for h in range(48):
        _trade(s, book["treatment_tag"], _now() - timedelta(hours=h + 1))
    _trade(s, book["control_tag"], _now() - timedelta(hours=2), n=400)
    s.commit()
    assert not _fired(ct.build_report(s, evaluate=True), "flow-constrained-book")


def test_a_quiet_platform_is_not_evidence_against_one_arm(xos_session,
                                                          xos_platform):
    """MUST NOT FIRE: with nothing trading anywhere, "this arm booked nothing" is
    equally consistent with a dead worker, a closed market and a broken arm. The
    detector's claim is that the silence is SPECIFIC to this arm; without a
    reference that claim is unsupported, and asserting it anyway is how a
    detector starts being wrong on the days it matters."""
    s = xos_session
    _two_arm_paper_book(s, "quiet-everywhere", armed_hours_ago=48)
    s.commit()
    assert not _fired(ct.build_report(s, evaluate=True))


def test_a_single_stray_trade_elsewhere_is_not_a_reference(xos_session,
                                                           xos_platform):
    """MUST NOT FIRE: one fill somewhere on the platform does not prove the
    worker was cycling and finding candidates. The reference floor exists so that
    a nearly-idle platform cannot indict a book."""
    s = xos_session
    _two_arm_paper_book(s, "near-idle-platform", armed_hours_ago=48)
    _trade(s, "mmsell10", _now() - timedelta(hours=3),
           n=ct.SILENT_ARM_REFERENCE_TRADES - 1)
    s.commit()
    assert not _fired(ct.build_report(s, evaluate=True))


def test_trades_before_the_arming_instant_are_not_a_reference(xos_session,
                                                              xos_platform):
    """MUST NOT FIRE: the reference has to cover the window this arm was silent
    IN. Platform activity from before the arm existed says nothing about whether
    the worker has been booking since."""
    s = xos_session
    book = _two_arm_paper_book(s, "armed-after-the-noise", armed_hours_ago=30)
    _trade(s, "mmsell10", book["armed"] - timedelta(hours=5), n=400)
    s.commit()
    assert not _fired(ct.build_report(s, evaluate=True))


@pytest.mark.parametrize("state", ["IDEA", "PROBE"])
def test_a_pre_paper_experiment_never_fires(xos_session, xos_platform, state):
    """MUST NOT FIRE: an idea that has traded nothing is not an anomaly. Evidence
    is expected from PAPER onward, which is the same rule `experiment.zero_
    evidence` already uses."""
    s = xos_session
    book = _two_arm_paper_book(s, f"pre-paper-{state.lower()}",
                               armed_hours_ago=48)
    # Walk back to the pre-PAPER state via the recorded machine.
    svc.transition_experiment(s, book["exp"], "PAUSED", actor="operator",
                              reason="not started")
    svc.transition_experiment(s, book["exp"], "RETIRED", actor="operator",
                              reason="never ran")
    _trade(s, "mmsell10", _now() - timedelta(hours=40), n=166)
    s.commit()
    assert not _fired(ct.build_report(s, evaluate=True))


# ---------------------------------------------------------------------------
# 3. It is a candidate like any other, and it writes nothing
# ---------------------------------------------------------------------------


def test_it_is_adoptable_and_the_report_stays_read_only(xos_session,
                                                        xos_platform):
    """A candidate is only useful if it can be adopted by fingerprint, and the
    Tower must not open the ticket itself."""
    from sqlalchemy import event

    s = xos_session
    _two_arm_paper_book(s, "adoptable-book", armed_hours_ago=48)
    _trade(s, "mmsell10", _now() - timedelta(hours=40), n=166)
    s.commit()

    written: list[str] = []

    def _spy(session, flush_context, instances):
        for obj in list(session.new) + list(session.dirty) + list(session.deleted):
            written.append(type(obj).__name__)

    event.listen(type(s), "before_flush", _spy)
    try:
        rep = ct.build_report(s, evaluate=True)
        out = ct.render(rep, session=s)
    finally:
        event.remove(type(s), "before_flush", _spy)

    assert written == [], f"the Control Tower wrote {written}"
    cand = _fired(rep, "adoptable-book")[0]
    assert cand["fingerprint"] == ipol.issue_fingerprint(
        detector=DETECTOR, experiment_id=cand["experiment_id"],
        version_id=cand["version_id"], epoch_id=cand["epoch_id"],
        anomaly_kind=cand["anomaly_kind"],
    )
    assert cand["fingerprint"] in out and DETECTOR in out


def test_the_fingerprint_is_stable_as_the_silence_lengthens(xos_session,
                                                            xos_platform):
    """A fingerprint identifies a PROBLEM SCOPE. If it moved with the age of the
    silence, every run would look like a new anomaly and recurrence detection —
    the thing that tells "still happening" from "under investigation" — would be
    dead."""
    s = xos_session
    _two_arm_paper_book(s, "aging-book", armed_hours_ago=48)
    _trade(s, "mmsell10", _now() - timedelta(hours=40), n=166)
    s.commit()
    first = {c["fingerprint"] for c in _fired(ct.build_report(s, evaluate=False))}
    _trade(s, "mmsell10", _now() - timedelta(minutes=5), n=50)
    s.commit()
    second = {c["fingerprint"] for c in _fired(ct.build_report(s, evaluate=False))}
    assert first == second and first


def test_the_weaker_zero_evidence_candidate_is_not_suppressed(xos_session,
                                                              xos_platform):
    """Both fire, deliberately. `experiment.zero_evidence` may already have an
    open ticket against its own fingerprint; dropping it once the escalation
    fires would make that ticket read as no-longer-recurring — i.e. as fixed — on
    exactly the day the problem got worse."""
    s = xos_session
    _two_arm_paper_book(s, "double-reported", armed_hours_ago=48)
    _trade(s, "mmsell10", _now() - timedelta(hours=40), n=166)
    s.commit()
    rep = ct.build_report(s, evaluate=True)
    assert _fired(rep, "double-reported")
    assert [c for c in rep.issue_candidates
            if c["detector"] == ipol.DETECTOR_ZERO_EVIDENCE
            and c["experiment"] == "double-reported"]


def test_an_open_zero_evidence_investigation_suppresses_the_escalation(
        xos_session, xos_platform):
    """MUST NOT FIRE as a candidate: the live shape is `freeze-dark-window-pin` —
    four arms, 580h armed, zero rows in `paper_trades` ever, while the platform
    booked tens of thousands. It is a true instance of the condition, and
    XOS-000003 has already diagnosed it (no available universe satisfies the
    hypothesis) and is working it.

    The escalation exists to make an UNDETECTED silence visible. A silence with
    an owned investigation is detected, and a second ticket under a different
    fingerprint would only split the history — so the finding is recorded with
    `covered_by` and stated in the recommendations, but no candidate is emitted.
    """
    from kalshi_bot.experiment_os import issues as ix

    s = xos_session
    book = _two_arm_paper_book(s, "freeze-shaped-book", armed_hours_ago=580)
    _trade(s, "mmsell10", _now() - timedelta(hours=40), n=4818)
    ix.create_issue(
        s, title="freeze-shaped-book is deployed but has produced zero evidence",
        opened_by_role="EXPERIMENT_CONTROL_TOWER", experiment=book["exp"],
        detector=ipol.DETECTOR_ZERO_EVIDENCE, detector_fingerprint="deadbeef",
    )
    s.commit()

    rep = ct.build_report(s, evaluate=True)
    assert not _fired(rep, "freeze-shaped-book"), "no duplicate candidate"
    assert len(rep.silent_arms) == 2, "the silence is still DETECTED, not dropped"
    assert all(f["covered_by"] for f in rep.silent_arms)
    assert any("already under investigation" in r for r in rep.recommendations), (
        "and the reader is told the silence is ongoing and who owns it"
    )


def test_closing_the_investigation_re_arms_the_escalation(xos_session,
                                                          xos_platform):
    """The suppression is recomputed from state every run and nothing about it is
    remembered, so a closed ticket over a continuing silence brings the candidate
    straight back — 'we ticketed that once' is not evidence that it is fixed."""
    from kalshi_bot.experiment_os import issues as ix

    s = xos_session
    book = _two_arm_paper_book(s, "reopening-book", armed_hours_ago=580)
    _trade(s, "mmsell10", _now() - timedelta(hours=40), n=4818)
    issue = ix.create_issue(
        s, title="reopening-book is deployed but has produced zero evidence",
        opened_by_role="EXPERIMENT_CONTROL_TOWER", experiment=book["exp"],
        detector=ipol.DETECTOR_ZERO_EVIDENCE, detector_fingerprint="deadbeef",
    )
    s.commit()
    assert not _fired(ct.build_report(s, evaluate=False), "reopening-book")

    ix.close_no_action(s, issue, actor="a", actor_role="LIVE_OPS",
                       reason="closed while the book is still silent")
    s.commit()
    assert len(_fired(ct.build_report(s, evaluate=False), "reopening-book")) == 2


# ---------------------------------------------------------------------------
# 4. live and paper_twin deployments
#
# The blind spot the first version of this detector shipped with, stated in its
# own PR: arm evidence came from `read.experiment_scoreboard`, whose metrics are
# scoped to `deployment_kind = "paper"`, so an experiment deployed only live
# and/or as a twin had no tags on its arms and could never fire however silent it
# went. Today that is `mmsell-price-ceiling`, `mmsell-scheduled-settle-live` and
# `theta4-fat-tail` — every live book in the portfolio.
# ---------------------------------------------------------------------------


def _live_canary_book(s, key, *, armed_hours_ago: float, arms=("mmsell10",)):
    """A LIVE_CANARY experiment with a live deployment and its registered paper
    twin, and NO paper deployment — the shape of every live book in production."""
    armed = _now() - timedelta(hours=armed_hours_ago)
    exp = svc.create_experiment(s, key=key, origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="the live book reproduces its paper edge",
        independent_variable="execution", now=armed,
        control_exemption_reason="test shape: absolute threshold",
    )
    for i, arm_key in enumerate(arms):
        svc.add_arm(s, ver, arm_key=arm_key,
                    role="treatment" if i == 0 else "control",
                    strategy_tag=f"L{arm_key}")
    svc.freeze_version(s, ver, now=armed)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=armed)
    live = svc.register_deployment(
        s, epoch, deployment_key=f"{key}-live-1", stage="LIVE_CANARY", kind="live",
        arms={a: f"L{a}" for a in arms}, started_at=armed,
        _sanctioned_canary=True,
    )
    twin = svc.register_deployment(
        s, epoch, deployment_key=f"{key}-twin-1", stage="LIVE_CANARY",
        kind="paper_twin", arms={a: f"L{a}_pt" for a in arms},
        twin_of=live, started_at=armed, _sanctioned_canary=True,
    )
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    # A promotion needs the PASS that justified it — the guard is real, so the
    # fixture satisfies it rather than routing around it.
    promo = svc.register_gate(
        s, ver, gate_key="paper_to_live_canary", kind="promotion",
        spec={"pass_all": [{"metric": "pnl_cents_per_trade", "arm": arms[0],
                            "op": ">", "value": 0}]},
        from_state="PAPER", to_state="LIVE_CANARY", registered_at=armed,
    )
    passed = svc.record_gate_result(
        s, promo, verdict="PASS", epoch=epoch, computed_at=armed,
        evidence_ref="test fixture", explanation="fixture promotion",
    )
    svc.transition_experiment(
        s, exp, "LIVE_CANARY", actor="operator", approved_by="operator",
        gate_result=passed, reason="fixture: arm the canary",
    )
    s.commit()
    return {"exp": exp, "ver": ver, "epoch": epoch, "live": live, "twin": twin,
            "armed": armed, "live_tags": [f"L{a}" for a in arms],
            "twin_tags": [f"L{a}_pt" for a in arms]}


def _live_order(s, tag, at, *, n=1, action="buy"):
    from kalshi_bot.models import LiveOrder

    for i in range(n):
        s.add(LiveOrder(
            market_ticker=f"KX-{tag}-{i}-{at.timestamp()}", strategy=tag,
            action=action, side="yes", status="resting", quantity=1, limit_price=50,
            created_at=at + timedelta(seconds=i),
        ))


def test_a_silent_paper_twin_now_fires(xos_session, xos_platform):
    """THE BLIND SPOT, CLOSED. A live canary's registered twin is a paper book: it
    writes `paper_trades` under its own tags, and if it stops the live/paper
    comparison that detects adverse selection quietly stops with it. Before this,
    the arm carried no `paper` tags at all so nothing was ever measured."""
    s = xos_session
    book = _live_canary_book(s, "twin-went-dark", armed_hours_ago=48)
    # Live is placing orders, so the book itself is demonstrably running...
    _live_order(s, book["live_tags"][0], _now() - timedelta(hours=40), n=200)
    # ...and the paper engine is busy, but the TWIN has booked nothing.
    _trade(s, "mmsell10", _now() - timedelta(hours=40), n=4818)
    s.commit()

    fired = _fired(ct.build_report(s, evaluate=True), "twin-went-dark")
    assert {c["anomaly_kind"] for c in fired} == {"mmsell10:paper_twin"}, (
        "the twin is silent; the live deployment beside it is not"
    )
    assert "paper_twin" in (fired[0]["detail"] or "")


def test_a_silent_live_deployment_now_fires(xos_session, xos_platform):
    """The other half: live placed no entry order for 48h while other strategies
    placed 200. Measured on `live_entry_orders` (orders PLACED), never on
    settlement — settlement lags entry by hours to days, so a settled-contract
    count would call a healthy book silent for a whole window after it started."""
    s = xos_session
    book = _live_canary_book(s, "live-went-dark", armed_hours_ago=48)
    _trade(s, book["twin_tags"][0], _now() - timedelta(hours=40), n=300)
    _live_order(s, "Dmmsell10", _now() - timedelta(hours=40), n=200)
    s.commit()

    fired = _fired(ct.build_report(s, evaluate=True), "live-went-dark")
    assert {c["anomaly_kind"] for c in fired} == {"mmsell10:live"}
    assert "live entry orders" in (fired[0]["detail"] or ""), (
        "and the reference is live orders, not paper trades"
    )


def test_a_busy_paper_engine_never_indicts_a_silent_live_arm(xos_session,
                                                             xos_platform):
    """MUST NOT FIRE: this is the false positive that would hit EVERY live book at
    once. `paper_trades` being busy proves the paper engine is up; it is perfectly
    consistent with live trading being switched off platform-wide. The reference
    has to be read on the same substrate the silent arm is measured on."""
    s = xos_session
    book = _live_canary_book(s, "live-quiet-everywhere", armed_hours_ago=48)
    _trade(s, book["twin_tags"][0], _now() - timedelta(hours=40), n=300)
    _trade(s, "mmsell10", _now() - timedelta(hours=40), n=40000)
    # No live orders anywhere — not this book's, not anyone's.
    s.commit()
    assert not _fired(ct.build_report(s, evaluate=True), "live-quiet-everywhere")


def test_a_healthy_live_canary_and_twin_fire_nothing(xos_session, xos_platform):
    s = xos_session
    book = _live_canary_book(s, "healthy-canary", armed_hours_ago=48)
    _live_order(s, book["live_tags"][0], _now() - timedelta(hours=40), n=50)
    _trade(s, book["twin_tags"][0], _now() - timedelta(hours=40), n=50)
    _trade(s, "mmsell10", _now() - timedelta(hours=40), n=4818)
    _live_order(s, "Dmmsell10", _now() - timedelta(hours=40), n=200)
    s.commit()
    assert not _fired(ct.build_report(s, evaluate=True), "healthy-canary")


def test_the_two_kinds_of_one_arm_are_separate_findings(xos_session, xos_platform):
    """A live canary and its twin are the SAME arm on two deployments and they
    fail independently — an emptied live allowlist stops one, a mis-registered
    twin stops the other. So the unit of a finding is (arm x kind), and each
    carries its own fingerprint so one can be ticketed without hiding the other."""
    s = xos_session
    _live_canary_book(s, "both-dark", armed_hours_ago=48)
    _trade(s, "mmsell10", _now() - timedelta(hours=40), n=4818)
    _live_order(s, "Dmmsell10", _now() - timedelta(hours=40), n=200)
    s.commit()

    fired = _fired(ct.build_report(s, evaluate=True), "both-dark")
    assert {c["anomaly_kind"] for c in fired} == {"mmsell10:live",
                                                  "mmsell10:paper_twin"}
    assert len({c["fingerprint"] for c in fired}) == 2, (
        "distinct fingerprints, so adopting one does not silence the other"
    )


def test_a_sibling_reference_must_be_the_same_deployment_kind(xos_session,
                                                              xos_platform):
    """A busy PAPER-TWIN sibling arm says nothing about whether a LIVE arm could
    have traded. Only a sibling on the same kind is a controlled comparison."""
    s = xos_session
    _live_canary_book(s, "kind-crossed-sibling", armed_hours_ago=48,
                      arms=("treatment", "control"))
    # The control arm is busy on its TWIN only; both live arms are silent, and
    # nothing else on the platform placed a live order.
    _trade(s, "Lcontrol_pt", _now() - timedelta(hours=40), n=300)
    _trade(s, "Ltreatment_pt", _now() - timedelta(hours=40), n=300)
    s.commit()
    assert not _fired(ct.build_report(s, evaluate=True), "kind-crossed-sibling"), (
        "a twin sibling is not a reference for a live arm"
    )


def test_an_unanswerable_metric_is_not_a_zero(xos_session, xos_platform):
    """MUST NOT FIRE: `collected is None` means the provider could not answer, and
    "we could not compute this" is not "this collected nothing". Firing on it
    would indict a book for a gap in the metrics layer."""
    s = xos_session
    _live_canary_book(s, "unanswerable", armed_hours_ago=48)
    _live_order(s, "Dmmsell10", _now() - timedelta(hours=40), n=200)
    _trade(s, "mmsell10", _now() - timedelta(hours=40), n=4818)
    s.commit()

    import kalshi_bot.experiment_os.metrics as mx
    from kalshi_bot.experiment_os.metrics import MetricValue

    real = mx.compute_metric

    def _blind(session, key, scope):
        if scope.deployment_kind in ("live", "paper_twin"):
            return MetricValue(metric=key, value=None, n=0, unit="?",
                               missing=True, reason="no provider in this test")
        return real(session, key, scope)

    mx.compute_metric = _blind
    try:
        rep = ct.build_report(s, evaluate=True)
    finally:
        mx.compute_metric = real
    assert not _fired(rep, "unanswerable")
