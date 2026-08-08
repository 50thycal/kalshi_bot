"""Growing the fleet takes effect at a COHORT BOUNDARY, never mid-cohort.

`reconcile_population` already handles a cap *decrease* (suspend the excess).
This is the other direction, and it cannot simply be "create until we hit the
target on the next cycle", for two reasons:

  1. `bootstrap_founders` runs every cycle and topped up to the target
     unconditionally, so raising EVO_MAX_ACTIVE_AGENTS injected agents into the
     RUNNING cohort within a minute of the redeploy.
  2. That is also wrong on the merits. Fitness is windowed per cohort — n_eff,
     daily P&L, drawdown and the whole rubric assume a full cohort of exposure.
     An agent born on day 6 of a 7-day window is ranked against peers with
     seven times its evidence, and `bottom_fraction` retires on that ranking.

So growth is deferred to the boundary, where a new agent starts the cohort
alongside everyone else on equal footing.
"""

from __future__ import annotations

from datetime import timedelta, timezone

from sqlalchemy import select

import kalshi_bot.evo.models as em
from kalshi_bot.evo import paper
from kalshi_bot.evo.cognition import ScriptedCognition
from kalshi_bot.evo.cohorts import (
    active_agents,
    ensure_current_cohort,
    reconcile_population,
)
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.evolution import bootstrap_founders, maybe_finalize_cohort
from kalshi_bot.evo.marketdata import StaticMarketData


def _noop_cognition():
    return ScriptedCognition(lambda ctx: {"journal": {"decision": "hold"},
                                          "actions": [{"type": "no_action"}]})


def _capped(cap: int, **over):
    return EvoSettings(
        _env_file=None, population_size=30, max_active_agents=cap,
        fill_latency_ms=0, routine_heartbeats_per_day=1, **over,
    )


def _finalize(session, settings, *, cohort=None):
    cohort = cohort or ensure_current_cohort(session, settings)
    ends = cohort.ends_at if cohort.ends_at.tzinfo else cohort.ends_at.replace(
        tzinfo=timezone.utc)
    return maybe_finalize_cohort(
        session, settings, cohort, md=StaticMarketData(),
        cognition=_noop_cognition(), now=ends + timedelta(minutes=1),
    )


# --- the running cohort is never disturbed ----------------------------------


def test_raising_the_cap_does_not_add_agents_mid_cohort(evo_session):
    """THE requirement. The operator raises the cap while a cohort is running;
    nothing may change until that cohort ends."""
    bootstrap_founders(evo_session, _capped(3))
    reconcile_population(evo_session, _capped(3))
    assert len(active_agents(evo_session, _capped(3))) == 3

    grown = _capped(6)
    # every cycle calls both of these — neither may act mid-cohort
    for _ in range(3):
        reconcile_population(evo_session, grown)
        assert bootstrap_founders(evo_session, grown) == []
    assert len(active_agents(evo_session, grown)) == 3


def test_cold_start_still_builds_the_whole_fleet(evo_session):
    """The mid-cohort guard must not break a genuinely empty database — that is
    the one time bootstrap_founders is supposed to create the full population."""
    created = bootstrap_founders(evo_session, _capped(6))
    assert len(created) == 6
    assert len(active_agents(evo_session, _capped(6))) == 6


# --- growth lands at the boundary -------------------------------------------


def test_boundary_grows_the_fleet_to_the_new_target(evo_session):
    """3 running under cap=3, operator arms cap=6: the boundary retires 1,
    keeps 2, clones 1 (=3) and then tops up by 3 to open the next cohort at 6."""
    bootstrap_founders(evo_session, _capped(3))
    reconcile_population(evo_session, _capped(3))

    grown = _capped(6)
    outcome = _finalize(evo_session, grown)
    assert outcome is not None
    assert len(outcome["retired"]) == 1
    assert len(outcome["survivors"]) == 2
    assert len(outcome["children"]) == 1
    assert len(outcome["grown"]) == 3, "expected 3 new agents to reach the target of 6"
    assert len(active_agents(evo_session, grown)) == 6


def test_new_agents_join_the_next_cohort_funded_and_live(evo_session):
    """A grown agent is only real if it is in the NEXT cohort, in the live set,
    and holding starting capital — otherwise it is born dead like the old
    offspring bug."""
    bootstrap_founders(evo_session, _capped(3))
    reconcile_population(evo_session, _capped(3))
    grown = _capped(6)
    outcome = _finalize(evo_session, grown)

    next_cohort = ensure_current_cohort(evo_session, grown)
    live = {a.agent_uuid for a in active_agents(evo_session, grown)}
    for au in outcome["grown"]:
        assert au in live, f"{au[:8]} is not in the live set — born dead"
        member = evo_session.scalar(
            select(em.EvoCohortMember).where(
                em.EvoCohortMember.cohort_id == next_cohort.id,
                em.EvoCohortMember.agent_uuid == au,
            )
        )
        assert member is not None, f"{au[:8]} never joined the next cohort"
        pf = paper.get_portfolio(evo_session, au, paper.LIFETIME)
        assert pf is not None and float(pf.cash_usd) == grown.starting_capital_usd


def test_grown_agents_are_parentless_wildcards_not_clones(evo_session):
    """Adding fleet capacity should widen the search, not duplicate the
    incumbent — a grown agent carries no parent and its own surname."""
    bootstrap_founders(evo_session, _capped(3))
    reconcile_population(evo_session, _capped(3))
    grown = _capped(6)
    outcome = _finalize(evo_session, grown)

    for au in outcome["grown"]:
        agent = evo_session.scalar(
            select(em.EvoAgent).where(em.EvoAgent.agent_uuid == au))
        assert agent.origin == "wildcard"
        assert agent.parent_uuid is None
        assert agent.founder_uuid == agent.agent_uuid
        assert agent.generation == 1


def test_steady_state_boundary_grows_nobody(evo_session):
    """With the fleet already at target, reproduction replaces retirement 1-for-1
    and the top-up must be a no-op — not a slow leak of extra agents."""
    settings = _capped(3)
    bootstrap_founders(evo_session, settings)
    reconcile_population(evo_session, settings)
    outcome = _finalize(evo_session, settings)
    assert outcome["grown"] == []
    assert len(active_agents(evo_session, settings)) == 3


def test_growth_is_idempotent_across_a_retried_finalization(evo_session):
    """The transition row is the lock; a second call must return None and must
    not mint a second batch of agents."""
    bootstrap_founders(evo_session, _capped(3))
    reconcile_population(evo_session, _capped(3))
    grown = _capped(6)
    cohort = ensure_current_cohort(evo_session, grown)
    ends = cohort.ends_at if cohort.ends_at.tzinfo else cohort.ends_at.replace(
        tzinfo=timezone.utc)

    first = _finalize(evo_session, grown, cohort=cohort)
    assert len(first["grown"]) == 3
    again = maybe_finalize_cohort(
        evo_session, grown, cohort, md=StaticMarketData(),
        cognition=_noop_cognition(), now=ends + timedelta(minutes=2),
    )
    assert again is None
    assert len(active_agents(evo_session, grown)) == 6


def test_a_runaway_cap_cannot_mint_an_unbounded_fleet_in_one_boundary(evo_session):
    """A fat-fingered EVO_MAX_ACTIVE_AGENTS must not spawn dozens of agents at
    once — each birth is a real LLM heartbeat against the weekly budget. Growth
    is clamped per boundary and converges over several instead."""
    bootstrap_founders(evo_session, _capped(3))
    reconcile_population(evo_session, _capped(3))
    huge = _capped(30)
    outcome = _finalize(evo_session, huge)
    assert len(outcome["grown"]) == huge.max_growth_per_boundary
    assert len(active_agents(evo_session, huge)) == 3 + huge.max_growth_per_boundary
