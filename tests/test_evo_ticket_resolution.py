"""Tickets could only ever be OPENED. Nothing could close one.

`EvoTicket.status` has carried the full vocabulary since the schema was written —
`open | in_review | approved | rejected | implemented | duplicate` — alongside
`human_decision` and `implementation_result`. No code path ever set any of them. So the queue
was write-only, and it showed: the fleet filed ~25 tickets asking for an off switch between
2026-07-31 and 2026-08-08, `deactivate_strategy` SHIPPED on 2026-08-08 (commit 9d34158), and
every one of those tickets was still `open` five days later.

That is worse than untidy. The review queue is what the operator reads to decide what to build,
and what agents see when deciding whether to re-ask. A queue that only grows buries the live
requests — the four asking for a CPI backtest corpus — under a wall of already-delivered ones.

Two things are pinned here:

  RESOLUTION EXISTS AND RECORDS WHY — a closed ticket carries the reason, so the record says
  what was delivered rather than just going quiet.

  AUTO-CLOSE IS CONSERVATIVE AND EVIDENCE-BASED — shipped capabilities are declared in an
  explicit registry, never guessed from fuzzy text. A wrong auto-close silently discards an
  agent's request, so the matcher must refuse anything it is not sure about. Being too shy
  merely leaves a ticket for the operator; being too eager loses information.
"""

from __future__ import annotations

import pytest

from kalshi_bot.evo import tickets
from kalshi_bot.evo.models import EvoTicket

CAP = "deactivate_strategy so I can stop a negative-EV book"


def _ticket(session, capability: str, *, category: str = "shared_code_capability",
            agent="agent-1") -> EvoTicket:
    row, err, _dup = tickets.submit_ticket(
        session, agent_uuid=agent, category=category, capability=capability,
        problem="a deployed strategy is losing money and I cannot stop it",
    )
    assert err is None, err
    return row


# --- the primitive ----------------------------------------------------------


def test_a_ticket_can_be_resolved_and_says_why(evo_session):
    t = _ticket(evo_session, CAP)
    assert t.status == "open"
    row, err = tickets.resolve_ticket(
        evo_session, t.id, status="implemented",
        result="shipped as the deactivate_strategy action (2026-08-08)",
    )
    assert err is None
    assert row.status == "implemented"
    assert "deactivate_strategy" in row.implementation_result


def test_a_rejection_records_the_decision_not_the_result(evo_session):
    t = _ticket(evo_session, "give me live order placement with real money")
    row, err = tickets.resolve_ticket(
        evo_session, t.id, status="rejected", decision="PAP-4: paper only, permanently",
    )
    assert err is None
    assert row.status == "rejected"
    assert "PAP-4" in row.human_decision


def test_an_unknown_status_is_refused(evo_session):
    """The status vocabulary is a closed set; a typo must not invent a state that no
    query filters on, which would hide the ticket from every view at once."""
    t = _ticket(evo_session, CAP)
    row, err = tickets.resolve_ticket(evo_session, t.id, status="done")
    assert row is None and err and "done" in err
    assert evo_session.get(EvoTicket, t.id).status == "open"


def test_resolving_a_missing_ticket_is_an_error_not_a_crash(evo_session):
    row, err = tickets.resolve_ticket(evo_session, 999999, status="implemented")
    assert row is None and err


def test_a_resolved_ticket_leaves_the_review_queue(evo_session):
    """The queue is what the operator reads. That is the whole point of closing."""
    t = _ticket(evo_session, CAP)
    assert any(q["id"] == t.id for q in tickets.review_queue(evo_session))
    tickets.resolve_ticket(evo_session, t.id, status="implemented", result="shipped")
    assert not any(q["id"] == t.id for q in tickets.review_queue(evo_session))


def test_a_resolved_ticket_no_longer_absorbs_new_requests_as_duplicates(evo_session):
    """The safety valve on auto-close. `find_duplicate` only matches open/in_review/approved,
    so if we close something wrongly the fleet can raise it again and it will be a NEW
    ticket rather than being silently folded into the closed one."""
    t = _ticket(evo_session, CAP)
    tickets.resolve_ticket(evo_session, t.id, status="implemented", result="shipped")
    again, err, deduped = tickets.submit_ticket(
        evo_session, agent_uuid="agent-2", category="shared_code_capability", capability=CAP,
    )
    assert err is None and deduped is False
    assert again.id != t.id


# --- auto-close against the shipped registry --------------------------------


def test_the_off_switch_wave_closes_against_the_registry(evo_session):
    """The wave that motivated this: every phrasing the fleet actually used for the off
    switch, taken verbatim from the live queue, must resolve against one registry entry."""
    phrasings = [
        "deactivate_strategy",
        "deactivate_strategies",
        "strategy_deactivation",
        "deactivate_strategy / pause_strategy action",
        "strategy deactivation or self-cancel",
        "Ability to deactivate an active strategy",
        "deactivate_negative_ev_strategies",
    ]
    made = [_ticket(evo_session, p, agent=f"agent-{i}") for i, p in enumerate(phrasings)]
    n = tickets.auto_resolve_shipped(evo_session)
    assert n == len(made)
    for t in made:
        row = evo_session.get(EvoTicket, t.id)
        assert row.status == "implemented"
        assert "deactivate_strategy" in row.implementation_result


def test_auto_close_names_the_capability_and_when_it_shipped(evo_session):
    """A closed ticket has to be auditable later: which action, and as of when."""
    t = _ticket(evo_session, "strategy_deactivation")
    tickets.auto_resolve_shipped(evo_session)
    row = evo_session.get(EvoTicket, t.id)
    assert "deactivate_strategy" in row.implementation_result
    assert "2026-08-08" in row.implementation_result


def test_a_live_request_is_left_alone(evo_session):
    """THE property that makes auto-close safe. `view_strategy_spec` is a real ask sitting in
    the queue — an agent cannot read back its own strategy's universe filters and entry
    conditions, so it cannot diagnose a book that silently trades nothing. Nothing about it
    resembles a shipped capability, and closing it would destroy the request.

    (This test used to be pinned on the CPI corpus. That ask has since been delivered as
    `run_backtest dataset='econ'`, so it now belongs in the closes-against-the-registry test
    below — a live request has to be one that is actually still live.)"""
    t = _ticket(
        evo_session,
        "view_strategy_spec so I can inspect an active strategy's universe filters and "
        "entry conditions",
        category="research_tooling",
    )
    assert tickets.auto_resolve_shipped(evo_session) == 0
    assert evo_session.get(EvoTicket, t.id).status == "open"


def test_the_cpi_corpus_wave_closes_against_the_econ_entry(evo_session):
    """The fleet's own non-weather thesis: four tickets 2026-07-22..2026-08-05 asking for
    settled CPI history to backtest against. Delivered 2026-08-14 as `run_backtest`
    dataset='econ'. Phrasings are verbatim from the live queue (tickets 25, 27, 28)."""
    phrasings = [
        "CPI settled-data pipeline for backtesting",
        "Add KXCPI (CPI inflation) settled-market data to the run_backtest corpus, similar "
        "to backfill_weather, to enable backtesting CPI strategies.",
        "Add settled CPI market corpus + official CPI actuals as a run_backtest dataset "
        "(like 'crypto')",
    ]
    made = [
        _ticket(evo_session, p, category="data_collection", agent=f"cpi-{i}")
        for i, p in enumerate(phrasings)
    ]
    assert tickets.auto_resolve_shipped(evo_session) == len(made)
    for t in made:
        row = evo_session.get(EvoTicket, t.id)
        assert row.status == "implemented"
        assert "econ" in row.implementation_result
        assert "2026-08-14" in row.implementation_result


def test_an_econ_request_for_something_else_is_not_swept_up(evo_session):
    """What shipped is SETTLED economic history to replay. A live feed and a release
    schedule are different asks that happen to say "CPI" — closing them would tell an agent
    it has data it does not have, which is worse than leaving the ticket open."""
    for i, cap in enumerate((
        "live CPI quote feed so I can react to the print in realtime",
        "economic calendar of upcoming CPI release times and consensus forecast",
    )):
        _ticket(evo_session, cap, category="external_data_pipeline", agent=f"econ-{i}")
    assert tickets.auto_resolve_shipped(evo_session) == 0


def test_a_merely_adjacent_request_is_not_swept_up(evo_session):
    """`strategy_execution` and `strategy_management` sit in the same queue and share a word
    with the off-switch entry. Sharing a token is not evidence the thing shipped."""
    for cap in ("strategy_execution for automated live order placement",
                "strategy_management dashboard so I can see all my books at once"):
        _ticket(evo_session, cap, agent=f"a-{cap[:6]}")
    assert tickets.auto_resolve_shipped(evo_session) == 0


def test_auto_close_is_idempotent(evo_session):
    """It runs every cycle; a second pass must find nothing and rewrite nothing."""
    t = _ticket(evo_session, "deactivate_strategy")
    assert tickets.auto_resolve_shipped(evo_session) == 1
    first = evo_session.get(EvoTicket, t.id).implementation_result
    assert tickets.auto_resolve_shipped(evo_session) == 0
    assert evo_session.get(EvoTicket, t.id).implementation_result == first


def test_every_registry_entry_points_at_a_real_permitted_action(evo_session):
    """The registry claims a capability now EXISTS. If the named action is not actually in
    PERMITTED_ACTIONS, the entry is a lie and would close tickets for something agents still
    cannot do — the exact failure this whole file guards against, inverted."""
    from kalshi_bot.evo.constitution import PERMITTED_ACTIONS

    assert tickets.SHIPPED_CAPABILITIES
    for entry in tickets.SHIPPED_CAPABILITIES:
        assert entry.action in PERMITTED_ACTIONS, entry.action
        assert entry.shipped_on and entry.all_of


@pytest.mark.parametrize("cap", ["", "   ", "deactivate"])
def test_degenerate_capability_text_never_matches(evo_session, cap):
    assert tickets._shipped_match(cap) is None
