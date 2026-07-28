"""`max_active_agents` is the real population size, not a display filter.

Live bug this pins (observed 2026-07-28 at cap=3, 30 agents active): the cap
only filtered which agents ran each cycle, taking the LOWEST ids, while every
population decision still used population_size=30. Two things broke:

  1. Reproduction creates children with the HIGHEST ids, so a live set defined
     by "lowest N ids" could never contain an offspring. All 9 generation-2
     children sat at zero heartbeats and zero orders three days after birth
     while the same 3 founders ran forever — evolution was a no-op.
  2. Finalization ranked all 30 cohort members when only 3 had any recent
     activity, so 27 were scored on nothing and the bottom 30% retired were
     arbitrary.

The fix makes the cap the population itself, so retirement/reproduction
fractions scale off the real number: cap=3 -> retire 1, keep 2, clone 1.
"""

from __future__ import annotations

from datetime import timedelta, timezone

from sqlalchemy import select

import kalshi_bot.evo.models as em
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


# --- the sizing rule itself -------------------------------------------------


def test_effective_population_size_uses_the_cap_when_set():
    assert _capped(3).effective_population_size() == 3
    assert _capped(0).effective_population_size() == 30          # 0 = no cap
    # a cap above the population can never inflate it
    assert _capped(500).effective_population_size() == 30


def test_bootstrap_founders_targets_the_cap_not_population_size():
    """Before the fix this refilled to 30 every cycle, which is what kept the
    dead 27 alive."""
    from kalshi_bot.db import get_engine, init_engine, session_scope
    from kalshi_bot.models import Base

    init_engine("sqlite://")
    Base.metadata.create_all(get_engine())
    with session_scope() as session:
        settings = _capped(3)
        created = bootstrap_founders(session, settings)
        assert len(created) == 3
        assert len(active_agents(session)) == 3
        # idempotent: a second pass must not manufacture more
        assert bootstrap_founders(session, settings) == []
        assert len(active_agents(session)) == 3


# --- reconciliation ---------------------------------------------------------


def test_reconcile_suspends_the_excess_and_keeps_the_running_ones(evo_session):
    """A cap decrease sheds the surplus, keeping the lowest ids — the agents
    already live, so nothing mid-flight gets yanked out from under its book."""
    uncapped = _capped(0)
    bootstrap_founders(evo_session, uncapped)
    everyone = active_agents(evo_session)
    assert len(everyone) == 30
    keep = [a.agent_uuid for a in everyone[:3]]

    result = reconcile_population(evo_session, _capped(3))
    assert result == {"target": 3, "living": 3, "suspended": 27}

    still_active = active_agents(evo_session)
    assert [a.agent_uuid for a in still_active] == keep


def test_reconcile_is_idempotent_and_a_noop_at_or_under_target(evo_session):
    settings = _capped(3)
    bootstrap_founders(evo_session, settings)
    first = reconcile_population(evo_session, settings)
    assert first["suspended"] == 0
    second = reconcile_population(evo_session, settings)
    assert second["suspended"] == 0
    assert len(active_agents(evo_session)) == 3


def test_suspension_is_not_retirement(evo_session):
    """A capped-out agent did not fail. It must not get a retirement record, a
    graveyard entry, or a final rank — that would fabricate a performance story
    for a bot whose only problem is that the operator wanted a smaller fleet."""
    bootstrap_founders(evo_session, _capped(0))
    reconcile_population(evo_session, _capped(3))

    suspended = list(evo_session.scalars(
        select(em.EvoAgent).where(em.EvoAgent.status == "suspended")))
    assert len(suspended) == 27
    for agent in suspended:
        assert agent.retired_at is None
        assert "not a performance retirement" in (agent.status_reason or "")
        assert evo_session.scalar(select(em.EvoRetirement).where(
            em.EvoRetirement.agent_uuid == agent.agent_uuid)) is None
    # nothing was moved into the retired state
    assert evo_session.scalar(select(em.EvoAgent).where(
        em.EvoAgent.status == "retired")) is None


# --- the regression that actually matters -----------------------------------


def test_capped_cohort_retires_one_keeps_two_and_clones_one(evo_session):
    """The operator's stated requirement, end to end: at cap=3 a cohort
    boundary must retire exactly 1, keep 2, and clone the best into 1 child —
    and the population must land back on exactly 3."""
    settings = _capped(3)
    bootstrap_founders(evo_session, settings)
    reconcile_population(evo_session, settings)
    cohort = ensure_current_cohort(evo_session, settings)
    ends = cohort.ends_at if cohort.ends_at.tzinfo else cohort.ends_at.replace(
        tzinfo=timezone.utc)

    outcome = maybe_finalize_cohort(
        evo_session, settings, cohort, md=StaticMarketData(),
        cognition=_noop_cognition(), now=ends + timedelta(minutes=1),
    )
    assert outcome is not None
    assert len(outcome["retired"]) == 1
    assert len(outcome["survivors"]) == 2
    assert len(outcome["children"]) == 1
    assert len(active_agents(evo_session)) == 3


def test_offspring_actually_enter_the_live_set(evo_session):
    """THE bug. Children get the highest ids; the old 'lowest N ids' live set
    could never include one, so every generation of offspring was born dead.
    After a finalization the child must be among the agents that actually run."""
    settings = _capped(3)
    bootstrap_founders(evo_session, settings)
    reconcile_population(evo_session, settings)
    cohort = ensure_current_cohort(evo_session, settings)
    ends = cohort.ends_at if cohort.ends_at.tzinfo else cohort.ends_at.replace(
        tzinfo=timezone.utc)

    outcome = maybe_finalize_cohort(
        evo_session, settings, cohort, md=StaticMarketData(),
        cognition=_noop_cognition(), now=ends + timedelta(minutes=1),
    )
    child_uuid = outcome["children"][0]

    # the live set is what the orchestrator/heartbeats/strategy runner all use
    live = {a.agent_uuid for a in active_agents(evo_session, settings)}
    assert child_uuid in live, "the newborn child is not in the live set — born dead"
    # and it survives the next reconcile rather than being immediately suspended
    reconcile_population(evo_session, settings)
    live_after = {a.agent_uuid for a in active_agents(evo_session, settings)}
    assert child_uuid in live_after
    assert len(live_after) == 3


def test_finalization_ignores_suspended_agents_entirely(evo_session):
    """Suspended agents must not be scored, ranked, retired, or cloned — the
    27 idle bots were polluting selection with rankings based on no activity."""
    bootstrap_founders(evo_session, _capped(0))
    settings = _capped(3)
    reconcile_population(evo_session, settings)
    suspended_uuids = {
        a.agent_uuid for a in evo_session.scalars(
            select(em.EvoAgent).where(em.EvoAgent.status == "suspended"))
    }
    cohort = ensure_current_cohort(evo_session, settings)
    ends = cohort.ends_at if cohort.ends_at.tzinfo else cohort.ends_at.replace(
        tzinfo=timezone.utc)

    outcome = maybe_finalize_cohort(
        evo_session, settings, cohort, md=StaticMarketData(),
        cognition=_noop_cognition(), now=ends + timedelta(minutes=1),
    )
    assert outcome is not None
    touched = set(outcome["retired"]) | set(outcome["survivors"])
    assert touched.isdisjoint(suspended_uuids)
    assert len(outcome["retired"]) == 1  # 30% of 3, not of 30
    # no child was cloned from a suspended parent
    for child_uuid in outcome["children"]:
        child = evo_session.scalar(select(em.EvoAgent).where(
            em.EvoAgent.agent_uuid == child_uuid))
        assert child.parent_uuid not in suspended_uuids


def test_fractions_scale_across_cap_sizes(evo_session):
    """'Scale appropriately based on the max bots allowed' — the population must
    come back to target for any cap, not just 3."""
    from kalshi_bot.evo.fitness import group_by_fractions

    class Row:
        pass

    s = EvoSettings(_env_file=None)
    for n in (2, 3, 4, 5, 10, 30):
        groups = group_by_fractions([Row() for _ in range(n)], s)
        n_bottom = len(groups["bottom"])
        n_top = len(groups["top"])
        survivors = n - n_bottom
        # one child per top-group parent, so the population returns to n
        assert survivors + n_top == n, f"cap {n} does not return to target"
