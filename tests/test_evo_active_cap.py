"""The max_active_agents ops throttle: cap how many active agents run live work
(heartbeats + strategy) without retiring anyone, and lift it cleanly."""

from __future__ import annotations

import random

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kalshi_bot.evo import budgets, llm
from kalshi_bot.evo.cognition import ScriptedCognition
from kalshi_bot.evo.cohorts import active_agents, ensure_current_cohort
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.evolution import create_agent
from kalshi_bot.evo.heartbeats import run_due_heartbeats
from kalshi_bot.evo.marketdata import StaticMarketData
from kalshi_bot.models import Base


def _seed(n, settings):
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    session = sessionmaker(bind=eng, expire_on_commit=False)()
    llm.seed_model_prices(session, settings)
    cohort = ensure_current_cohort(session, settings)
    for i in range(n):
        a = create_agent(session, settings, cohort, random.Random(i),
                         origin="founder", slot_key=f"founder:{i}")
        budgets.ensure_budgets(session, a.agent_uuid, cohort.id, settings)
    return session, cohort


def test_active_agents_uncapped_by_default():
    settings = EvoSettings(_env_file=None)
    session, _ = _seed(10, settings)
    assert len(active_agents(session)) == 10
    assert len(active_agents(session, settings)) == 10  # cap 0 == no cap


def test_active_agents_respects_cap():
    settings = EvoSettings(_env_file=None, max_active_agents=3)
    session, _ = _seed(10, settings)
    capped = active_agents(session, settings)
    assert len(capped) == 3
    # deterministic: the lowest-id (earliest-created) agents
    all_by_id = active_agents(session)  # uncapped, ordered by id
    assert [a.agent_uuid for a in capped] == [a.agent_uuid for a in all_by_id[:3]]
    # nobody was retired — the population is intact, just throttled
    assert len(all_by_id) == 10


def test_cap_limits_which_agents_get_heartbeats():
    settings = EvoSettings(_env_file=None, max_active_agents=2)
    session, cohort = _seed(6, settings)
    ran = set()
    cog = ScriptedCognition(lambda ctx: (ran.add(ctx.agent.agent_uuid) or
                                         {"journal": {"decision": "ok"}, "actions": []}))
    run_due_heartbeats(session, settings, cohort=cohort, cognition=cog,
                       md=StaticMarketData(), max_per_cycle=50)
    # only the 2 capped agents ever ran a heartbeat this cycle
    assert len(ran) <= 2
    live = {a.agent_uuid for a in active_agents(session, settings)}
    assert ran <= live


def test_lifting_the_cap_restores_the_full_population():
    capped = EvoSettings(_env_file=None, max_active_agents=3)
    session, _ = _seed(10, capped)
    assert len(active_agents(session, capped)) == 3
    uncapped = EvoSettings(_env_file=None, max_active_agents=0)
    assert len(active_agents(session, uncapped)) == 10  # same DB, cap lifted
