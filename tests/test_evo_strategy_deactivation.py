"""An agent must be able to turn OFF a strategy it has proven is losing money.

`PERMITTED_ACTIONS` shipped with `save_strategy` and `activate_strategy` and no
way back. The only deactivation anywhere was a side-effect of `activate_strategy`
demoting same-named siblings, so once a strategy was live the owner could never
stop it. `strategy_runner` then re-executed it every cycle, forever.

What that cost, live (cohort 4): agent 0bb6dd17 ran a backtest over mmsell's own
settled corpus — 4,339 trades, -$0.0476/trade — concluded its `mmsell_mid_no_taker`
strategies 49 and 50 were negative-EV, and filed EIGHTEEN capability tickets over
six days trying to switch them off, escalating urgency and cycling category
(performance -> bug_report -> infrastructure -> platform_deployment -> permissions)
looking for one that would land. Both strategies kept trading the whole time, and
0bb6dd17 finished the cohort as the worst book in the fleet at -$271.87. Agent
78c73b86 filed the same request for five more.

The fleet was doing exactly the right thing — measure, conclude, stop — and the
platform physically would not let it stop.

Second hole pinned here: a retired agent's strategies stayed `active`, so a bot
that no longer exists kept placing orders through `strategy_runner`.
"""

from __future__ import annotations

from datetime import timedelta, timezone

from sqlalchemy import select

import kalshi_bot.evo.models as em
from kalshi_bot.evo import sandbox
from kalshi_bot.evo.cognition import ACTION_PROTOCOL, ScriptedCognition
from kalshi_bot.evo.cohorts import ensure_current_cohort
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.constitution import PERMITTED_ACTIONS
from kalshi_bot.evo.evolution import bootstrap_founders, maybe_finalize_cohort
from kalshi_bot.evo.marketdata import StaticMarketData
from tests.test_evo_foundations import _mk_agent

SPEC = {
    "name": "losing_book_v1",
    "family": "testfam",
    "universe": {"series_prefixes": ["T"], "min_volume": 10, "max_spread_cents": 15,
                 "min_hours_to_close": 0.1, "max_hours_to_close": 500},
    "entry": {"side": "yes", "style": "taker", "min_price_cents": 20,
              "max_price_cents": 80, "size_contracts": 5},
    "exit": {"mode": "settlement"},
    "risk": {"max_concurrent_positions": 5, "max_per_event": 1,
             "max_cost_per_position_usd": 50},
}


def _noop_cognition():
    return ScriptedCognition(lambda ctx: {"journal": {"decision": "hold"},
                                          "actions": [{"type": "no_action"}]})


_S = EvoSettings(_env_file=None)


def _runner_sees(session):
    """Exactly what strategy_runner enumerates each cycle (strategy_runner.py:74)."""
    return list(session.scalars(
        select(em.EvoStrategy).where(em.EvoStrategy.status == "active")))


def _live_strategy(session, agent_uuid, name="losing_book_v1"):
    spec = dict(SPEC, name=name)
    row, err = sandbox.save_strategy(session, _S, agent_uuid=agent_uuid, spec_doc=spec)
    assert err is None, err
    active, err = sandbox.activate_strategy(session, agent_uuid, row.id)
    assert err is None, err
    assert active.status == "active"
    return active


# --- the action exists and is reachable -------------------------------------


def test_deactivate_strategy_is_a_permitted_action():
    assert "deactivate_strategy" in PERMITTED_ACTIONS


def test_protocol_tells_agents_the_off_switch_exists():
    """Eighteen tickets were filed by an agent that had measured the loss and could
    not find the lever. The protocol has to name it."""
    proto = " ".join(ACTION_PROTOCOL.split())
    assert "deactivate_strategy {strategy_id" in proto
    idx = proto.index("deactivate_strategy {strategy_id")
    assert "stop" in proto[idx:idx + 320].lower()


# --- it actually stops the strategy -----------------------------------------


def test_owner_can_deactivate_a_live_strategy(evo_session):
    agent = _mk_agent(evo_session)
    live = _live_strategy(evo_session, agent.agent_uuid)

    row, err = sandbox.deactivate_strategy(
        evo_session, agent.agent_uuid, live.id, reason="backtest 1855: -$0.0476/trade",
    )
    assert err is None
    assert row.status == "inactive"

    audits = [a.kind for a in evo_session.scalars(
        select(em.EvoAuditEvent).where(em.EvoAuditEvent.agent_uuid == agent.agent_uuid))]
    assert "strategy_deactivated" in audits


def test_a_deactivated_strategy_is_no_longer_picked_up_for_execution(evo_session):
    """The point of the action: strategy_runner must stop seeing it."""
    agent = _mk_agent(evo_session)
    live = _live_strategy(evo_session, agent.agent_uuid)
    assert live.id in {s.id for s in _runner_sees(evo_session)}

    sandbox.deactivate_strategy(evo_session, agent.agent_uuid, live.id, reason="-EV")
    assert live.id not in {s.id for s in _runner_sees(evo_session)}


def test_deactivation_is_reversible(evo_session):
    """'inactive' is already an activatable state — turning a book off must not
    be a one-way door, so an agent can re-enable after a spec fix."""
    agent = _mk_agent(evo_session)
    live = _live_strategy(evo_session, agent.agent_uuid)
    sandbox.deactivate_strategy(evo_session, agent.agent_uuid, live.id, reason="-EV")
    again, err = sandbox.activate_strategy(evo_session, agent.agent_uuid, live.id)
    assert err is None and again.status == "active"


# --- it is bounded ----------------------------------------------------------


def test_cannot_deactivate_another_agents_strategy(evo_session):
    a, b = _mk_agent(evo_session), _mk_agent(evo_session, surname="Voss")
    live = _live_strategy(evo_session, a.agent_uuid)
    row, err = sandbox.deactivate_strategy(evo_session, b.agent_uuid, live.id, reason="x")
    assert row is None and "another agent" in err
    assert live.status == "active", "a peer must never be able to stop your book"


def test_deactivating_an_unknown_or_already_off_strategy_is_a_clean_rejection(evo_session):
    agent = _mk_agent(evo_session)
    row, err = sandbox.deactivate_strategy(evo_session, agent.agent_uuid, 999999,
                                           reason="x")
    assert row is None and "not found" in err

    live = _live_strategy(evo_session, agent.agent_uuid)
    sandbox.deactivate_strategy(evo_session, agent.agent_uuid, live.id, reason="x")
    row, err = sandbox.deactivate_strategy(evo_session, agent.agent_uuid, live.id,
                                           reason="x")
    assert row is None and "not active" in err


# --- retired agents stop trading --------------------------------------------


def test_retiring_an_agent_deactivates_its_strategies(evo_session):
    """A retired bot's strategies kept executing every cycle — a dead agent with a
    live book, and nobody left who could file a ticket about it."""
    settings = EvoSettings(_env_file=None, population_size=30, max_active_agents=3,
                           fill_latency_ms=0, routine_heartbeats_per_day=1)
    bootstrap_founders(evo_session, settings)
    cohort = ensure_current_cohort(evo_session, settings)
    for agent in evo_session.scalars(
        select(em.EvoAgent).where(em.EvoAgent.status == "active")
    ):
        _live_strategy(evo_session, agent.agent_uuid, name=f"book_{agent.agent_uuid[:4]}")

    ends = cohort.ends_at if cohort.ends_at.tzinfo else cohort.ends_at.replace(
        tzinfo=timezone.utc)
    outcome = maybe_finalize_cohort(
        evo_session, settings, cohort, md=StaticMarketData(),
        cognition=_noop_cognition(), now=ends + timedelta(minutes=1),
    )
    assert outcome["retired"], "precondition: someone must have been retired"

    live_owners = {s.agent_uuid for s in _runner_sees(evo_session)}
    for au in outcome["retired"]:
        assert au not in live_owners, (
            f"retired agent {au[:8]} still has an active strategy placing orders"
        )
    # survivors are untouched
    for au in outcome["survivors"]:
        assert au in live_owners
