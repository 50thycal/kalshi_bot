"""Where did this bot come from? Readable without a DB query.

The roster showed `Name · Surname · G1 · CODE-G1-044` and nothing else, so four
materially different kinds of bot were indistinguishable at a glance:

  - an original founder that has been running for weeks
  - a survivor carried over from the previous generation
  - a child cloned from last generation's winner
  - a brand-new bot born minutes ago (wildcard, or added to grow the fleet)

All six live bots read "G1" in the UI, which made it look like reproduction had
stopped working. It had — but you could not tell that from the roster, and the
operator's actual question ("I thought the winner's children keep the surname")
was unanswerable from what was on screen.

`origin` also could not separate the scheduled diversity wildcard from an agent
added by `grow_to_target`, because both are stored as origin='wildcard'. The
birth slot_key distinguishes them (`wildcard:N` vs `growth:<cohort>:N`), so the
roster reads it rather than guessing.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone

from sqlalchemy import select

import kalshi_bot.evo.models as em
from kalshi_bot.dashboard import data as dash
from kalshi_bot.evo.cohorts import ensure_current_cohort
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.evolution import bootstrap_founders, create_agent
from tests.test_evo_foundations import _mk_agent

NOW = datetime.now(timezone.utc)
S = EvoSettings(_env_file=None, population_size=3, max_active_agents=3)


def _lineage_of(session, agent_uuid: str) -> dict:
    rows = dash._lineage(session, [agent_uuid])
    return rows[agent_uuid]


def test_a_founder_says_so(evo_session):
    bootstrap_founders(evo_session, S)
    a = evo_session.scalars(select(em.EvoAgent)).first()
    lin = _lineage_of(evo_session, a.agent_uuid)
    assert lin["kind"] == "founder"
    assert "founder" in lin["label"].lower()
    assert lin["parent_name"] is None


def test_a_child_names_the_parent_it_was_cloned_from(evo_session):
    """The operator's question: do the winner's children keep the surname? Yes —
    and the roster should say whose child it is, not just show a shared word."""
    parent = _mk_agent(evo_session, surname="Blackwood")
    cohort = ensure_current_cohort(evo_session, S)
    child = create_agent(
        evo_session, S, cohort, random.Random(1),
        origin="child", parent=parent, slot_key=parent.agent_uuid,
    )
    assert child.surname == "Blackwood", "children inherit the parent surname"
    assert child.generation == parent.generation + 1

    lin = _lineage_of(evo_session, child.agent_uuid)
    assert lin["kind"] == "child"
    assert lin["parent_name"] == parent.display_name.split(" · ")[0]
    assert "child of" in lin["label"].lower()
    assert parent.surname in lin["label"]


def test_a_scheduled_wildcard_and_a_growth_hire_are_told_apart(evo_session):
    """Both are stored as origin='wildcard'; only the birth slot_key separates a
    diversity injection from a bot added because the operator grew the fleet."""
    cohort = ensure_current_cohort(evo_session, S)
    rng = random.Random(2)
    wc = create_agent(evo_session, S, cohort, rng, origin="wildcard",
                      slot_key="wildcard:1")
    grown = create_agent(evo_session, S, cohort, rng, origin="wildcard",
                         slot_key=f"growth:{cohort.id}:1")

    assert _lineage_of(evo_session, wc.agent_uuid)["kind"] == "wildcard"
    grown_lin = _lineage_of(evo_session, grown.agent_uuid)
    assert grown_lin["kind"] == "growth"
    assert "grow" in grown_lin["label"].lower()


def test_a_bot_born_this_generation_is_marked_new(evo_session):
    cohort = ensure_current_cohort(evo_session, S)
    newborn = create_agent(evo_session, S, cohort, random.Random(3),
                           origin="wildcard", slot_key="wildcard:1")
    lin = _lineage_of(evo_session, newborn.agent_uuid)
    assert lin["born_cohort_id"] == cohort.id
    assert lin["is_new"] is True


def test_a_carried_over_survivor_is_not_marked_new(evo_session):
    """A bot that survived into this generation must not read as a fresh hire —
    that is exactly the distinction the roster could not make."""
    veteran = _mk_agent(evo_session)
    veteran.birth_cohort_id = -1  # born in some earlier cohort
    evo_session.flush()
    lin = _lineage_of(evo_session, veteran.agent_uuid)
    assert lin["is_new"] is False


def test_the_roster_row_carries_lineage(evo_session, evo_settings):
    """It has to reach the payload the page renders, not just exist in a helper."""
    bootstrap_founders(evo_session, evo_settings)
    cohort = ensure_current_cohort(evo_session, evo_settings)
    rows = dash._bot_rows(evo_session, evo_settings, cohort, now=NOW)
    assert rows
    for r in rows:
        assert "lineage" in r
        assert r["lineage"]["label"]
