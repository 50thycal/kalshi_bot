"""The winner must always reproduce. A wildcard may not consume its only child.

Live at the 2026-08-09 boundary the cohort finalized with `children: 0`. Every
surviving bot read "Generation 1" on the roster and nothing inherited anything.

Why: `top_fraction=0.30` on a 3-agent fleet gives exactly ONE reproduction slot,
and the wildcard branch takes a slot rather than adding one —

    skipped_parent = parents[-1]   # the lowest-ranked top agent skips
    parents = parents[:-1]         # ...which was the ONLY parent

so the single best book produced no offspring and its genome died with the
cohort. On a 30-agent fleet the same line is harmless (9 top slots, 8 children +
1 wildcard); at small fleet sizes it silently switches evolution off.

The wildcard cannot simply become additive: births must equal retirements or the
population drifts past `effective_population_size()` and reconcile_population
suspends the newborn on the next cycle. So when there is only one slot to give,
inheritance wins and the wildcard defers to the next wildcard cohort.
"""

from __future__ import annotations

from datetime import timedelta, timezone

from sqlalchemy import select

import kalshi_bot.evo.models as em
from kalshi_bot.evo.cognition import ScriptedCognition
from kalshi_bot.evo.cohorts import active_agents, ensure_current_cohort, reconcile_population
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.evolution import bootstrap_founders, maybe_finalize_cohort
from kalshi_bot.evo.marketdata import StaticMarketData


def _noop_cognition():
    return ScriptedCognition(lambda ctx: {"journal": {"decision": "hold"},
                                          "actions": [{"type": "no_action"}]})


def _settings(cap: int, *, wildcard: bool):
    """`wildcard_every_n_cohorts=1` makes EVERY cohort a wildcard cohort, which is
    how the branch under test is forced on; 0 disables wildcards entirely."""
    return EvoSettings(_env_file=None, population_size=30, max_active_agents=cap,
                       fill_latency_ms=0, routine_heartbeats_per_day=1,
                       wildcard_every_n_cohorts=1 if wildcard else 0)


def _finalize(session, settings):
    bootstrap_founders(session, settings)
    reconcile_population(session, settings)
    cohort = ensure_current_cohort(session, settings)
    ends = cohort.ends_at if cohort.ends_at.tzinfo else cohort.ends_at.replace(
        tzinfo=timezone.utc)
    return maybe_finalize_cohort(
        session, settings, cohort, md=StaticMarketData(),
        cognition=_noop_cognition(), now=ends + timedelta(minutes=1),
    )


def test_a_single_top_slot_produces_a_child_not_a_wildcard(evo_session):
    """THE live bug: 3 agents, wildcard cohort, children==0 and nothing inherited."""
    settings = _settings(3, wildcard=True)
    outcome = _finalize(evo_session, settings)

    assert outcome is not None
    assert len(outcome["children"]) == 1, (
        f"the winner did not reproduce: children={outcome['children']}, "
        f"wildcard={outcome['wildcard']}"
    )
    assert outcome["wildcard"] is None, "the wildcard must defer, not take the slot"
    assert outcome["skipped_parent"] is None


def test_the_child_actually_inherits_the_winners_line(evo_session):
    """Inheritance is the point — surname carries and the generation advances."""
    settings = _settings(3, wildcard=True)
    outcome = _finalize(evo_session, settings)
    child_uuid = outcome["children"][0]
    child = evo_session.scalar(
        select(em.EvoAgent).where(em.EvoAgent.agent_uuid == child_uuid))
    parent = evo_session.scalar(
        select(em.EvoAgent).where(em.EvoAgent.agent_uuid == child.parent_uuid))

    assert child.origin == "child"
    assert parent is not None
    assert child.surname == parent.surname
    assert child.generation == parent.generation + 1


def test_population_still_lands_exactly_on_target(evo_session):
    """Deferring the wildcard must not change the headcount — births still equal
    retirements, so nothing gets suspended on the next reconcile."""
    settings = _settings(3, wildcard=True)
    _finalize(evo_session, settings)
    assert len(active_agents(evo_session, settings)) == 3
    reconcile_population(evo_session, settings)
    assert len(active_agents(evo_session, settings)) == 3


def test_with_room_for_both_the_wildcard_still_runs(evo_session):
    """The wildcard is not disabled — at a fleet size with more than one top slot
    it still displaces the lowest-ranked parent, as designed."""
    settings = _settings(6, wildcard=True)
    outcome = _finalize(evo_session, settings)

    assert outcome["wildcard"] is not None, "wildcard should still fire at 6 agents"
    assert outcome["skipped_parent"] is not None
    assert len(outcome["children"]) >= 1, "and the winner still reproduces"
    assert len(active_agents(evo_session, settings)) == 6


def test_a_non_wildcard_cohort_is_unaffected(evo_session):
    settings = _settings(3, wildcard=False)
    outcome = _finalize(evo_session, settings)
    assert outcome["wildcard"] is None
    assert len(outcome["children"]) == 1
    assert len(active_agents(evo_session, settings)) == 3
