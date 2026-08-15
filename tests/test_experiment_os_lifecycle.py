"""The Experiment OS lifecycle state machine, exhaustively.

The legal transition set is hand-enumerated here — independently of the validator's
own logic — and every one of the 49 ordered state pairs is asserted against it, so a
regression that quietly legalizes (or bans) a pair fails by name rather than slipping
through a happy-path test. Spec: docs/EXPERIMENT_OPERATING_SYSTEM_SPEC.md §7.
"""

from __future__ import annotations

import pytest

from kalshi_bot.experiment_os.lifecycle import (
    ACTIVE_STATES,
    FORWARD_PATH,
    IllegalTransition,
    LifecycleState,
    OperatorApprovalRequired,
    validate_transition,
)

S = LifecycleState
ALL = list(LifecycleState)

# The complete legal set for an experiment NOT currently paused, hand-enumerated.
LEGAL_NON_PAUSED: set[tuple[S, S]] = {
    # the forward path, one arrow at a time
    (S.IDEA, S.PROBE),
    (S.PROBE, S.PAPER),
    (S.PAPER, S.LIVE_CANARY),
    (S.LIVE_CANARY, S.PRODUCTION),
    # any active state may pause
    *{(s, S.PAUSED) for s in ACTIVE_STATES},
    # any non-retired state may retire (a clean kill is always recordable)
    *{(s, S.RETIRED) for s in ALL if s is not S.RETIRED},
}


@pytest.mark.parametrize("cur", ALL)
@pytest.mark.parametrize("tgt", ALL)
def test_full_transition_matrix(cur, tgt):
    """Every ordered pair behaves exactly as the hand-enumerated legal set says
    (PAUSED resumes are exercised separately — they need paused_from context)."""
    kwargs = {"approved_by": "operator"}
    if cur is S.PAUSED and tgt is not S.RETIRED:
        return  # resume semantics covered by the dedicated tests below
    if (cur, tgt) in LEGAL_NON_PAUSED:
        validate_transition(cur, tgt, **kwargs)  # must not raise
    else:
        with pytest.raises(IllegalTransition):
            validate_transition(cur, tgt, **kwargs)


def test_forward_path_shape():
    assert FORWARD_PATH == (S.IDEA, S.PROBE, S.PAPER, S.LIVE_CANARY, S.PRODUCTION)


# --- operator approval (spec §27) -------------------------------------------------


@pytest.mark.parametrize("pair", [(S.PAPER, S.LIVE_CANARY), (S.LIVE_CANARY, S.PRODUCTION)])
def test_money_promotions_require_operator_approval(pair):
    cur, tgt = pair
    with pytest.raises(OperatorApprovalRequired):
        validate_transition(cur, tgt)
    with pytest.raises(OperatorApprovalRequired):
        validate_transition(cur, tgt, approved_by="")
    validate_transition(cur, tgt, approved_by="operator")


def test_approval_error_is_an_illegal_transition():
    """Callers that catch IllegalTransition also catch the approval refusal."""
    assert issubclass(OperatorApprovalRequired, IllegalTransition)


@pytest.mark.parametrize("pair", [(S.IDEA, S.PROBE), (S.PROBE, S.PAPER)])
def test_pre_money_promotions_need_no_approval(pair):
    validate_transition(*pair)


# --- PAUSED semantics (spec §7.2) -------------------------------------------------


@pytest.mark.parametrize("origin", sorted(ACTIVE_STATES, key=lambda s: s.value))
def test_paused_resumes_only_to_its_origin(origin):
    validate_transition(S.PAUSED, origin, paused_from=origin)
    for other in ALL:
        if other in (origin, S.RETIRED, S.PAUSED):
            continue
        with pytest.raises(IllegalTransition, match="only to the state it paused from"):
            validate_transition(S.PAUSED, other, paused_from=origin)


def test_paused_without_recorded_origin_cannot_resume():
    with pytest.raises(IllegalTransition, match="paused_from is not recorded"):
        validate_transition(S.PAUSED, S.PAPER)


def test_paused_may_always_retire():
    validate_transition(S.PAUSED, S.RETIRED)  # no paused_from needed


def test_resume_needs_no_fresh_approval():
    """Resuming a paused live canary is a resume, not a promotion — approval was
    given when the stage was entered."""
    validate_transition(S.PAUSED, S.LIVE_CANARY, paused_from=S.LIVE_CANARY)


# --- terminality and rollback (spec §7.2–7.3) -------------------------------------


@pytest.mark.parametrize("tgt", ALL)
def test_retired_is_terminal(tgt):
    with pytest.raises(IllegalTransition, match="terminal"):
        validate_transition(S.RETIRED, tgt, approved_by="operator", paused_from=S.PAPER)


def test_no_silent_rollback_message():
    with pytest.raises(IllegalTransition, match="no silent rollback"):
        validate_transition(S.PAPER, S.PROBE)
    with pytest.raises(IllegalTransition, match="no silent rollback"):
        validate_transition(S.PRODUCTION, S.PAPER)


def test_stage_skips_name_the_legal_next_stage():
    with pytest.raises(IllegalTransition, match="legal next stage: PROBE"):
        validate_transition(S.IDEA, S.PAPER)
    with pytest.raises(IllegalTransition, match="legal next stage: LIVE_CANARY"):
        validate_transition(S.PAPER, S.PRODUCTION, approved_by="operator")


@pytest.mark.parametrize("state", ALL)
def test_self_transition_is_not_a_transition(state):
    with pytest.raises(IllegalTransition):
        validate_transition(state, state, paused_from=S.PAPER, approved_by="operator")


def test_unknown_states_are_rejected():
    with pytest.raises(IllegalTransition, match="not a lifecycle state"):
        validate_transition("PAPER", "SHADOW")
    with pytest.raises(IllegalTransition, match="not a lifecycle state"):
        validate_transition("HOLD", "PAPER")  # HOLD is a gate verdict, not a state


def test_string_states_are_accepted():
    validate_transition("IDEA", "PROBE")
    validate_transition("PAUSED", "PAPER", paused_from="PAPER")
