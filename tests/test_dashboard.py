"""Read-only dashboard: payload shape + security (no secrets/traces leaked)."""

from __future__ import annotations

import json
import random

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kalshi_bot.dashboard import data as dash
from kalshi_bot.evo import budgets, llm, memory, tickets
from kalshi_bot.evo.cognition import ScriptedCognition
from kalshi_bot.evo.cohorts import ensure_current_cohort
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.evolution import create_agent
from kalshi_bot.evo.heartbeats import run_heartbeat
from kalshi_bot.evo.marketdata import Quote, StaticMarketData
from kalshi_bot.models import Base


def _session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)()


def _seed(session, settings, n=3):
    llm.seed_model_prices(session, settings)
    cohort = ensure_current_cohort(session, settings)
    agents = []
    for i in range(n):
        a = create_agent(session, settings, cohort, random.Random(i),
                         origin="founder", slot_key=f"founder:{i}")
        budgets.ensure_budgets(session, a.agent_uuid, cohort.id, settings)
        agents.append(a)
    return cohort, agents


def test_payload_shape_empty_population():
    session = _session()
    settings = EvoSettings(_env_file=None)
    d = dash.build_dashboard_data(session, settings)
    # no cohort yet
    assert d["cohort"] is None
    assert d["status"] == "between_cohorts"
    assert d["agents"] == []
    assert isinstance(d["components"], list) and len(d["components"]) == 10
    assert d["cards"]["active_agents"] == 0


def test_payload_shape_with_agents():
    session = _session()
    settings = EvoSettings(_env_file=None)
    cohort, agents = _seed(session, settings, n=3)
    d = dash.build_dashboard_data(session, settings)

    assert d["cohort"]["number"] == cohort.number
    assert d["cohort"]["seconds_remaining"] >= 0
    assert d["cards"]["active_agents"] == 3
    assert d["cards"]["starting_capital_usd"] == 3000.0
    assert d["cards"]["cohort_equity_usd"] == 3000.0  # untraded
    assert d["cards"]["cohort_profit_usd"] == 0.0
    assert d["cards"]["llm_budget_usd"] == 3 * settings.weekly_llm_ceiling_usd

    assert len(d["agents"]) == 3
    row = d["agents"][0]
    for key in ("uuid", "name", "family", "generation", "equity", "pnl",
                "return_pct", "status", "last_heartbeat_at", "group", "detail"):
        assert key in row
    det = row["detail"]
    for key in ("thesis", "positions", "fitness_components", "last_heartbeat_summary",
                "parent", "children", "llm_budget_remaining_usd"):
        assert key in det
    assert isinstance(det["positions"], list)
    # component table always lists the 10 systems
    assert {c["system"] for c in d["components"]} >= {
        "Cohort manager", "Heartbeat system", "Paper simulator", "Evolution engine"}


def test_activity_feed_reflects_a_completed_heartbeat():
    session = _session()
    settings = EvoSettings(_env_file=None)
    cohort, agents = _seed(session, settings, n=1)
    agent = agents[0]
    md = StaticMarketData()
    md.set_quote(Quote(ticker="T1", captured_at=__import__("datetime").datetime.now(
        __import__("datetime").timezone.utc), status="active", yes_bid=44, yes_ask=47,
        no_bid=53, no_ask=56, yes_levels=[(44, 10)], no_levels=[(53, 10)]))
    cog = ScriptedCognition(lambda ctx: {
        "journal": {"decision": "hold and research", "expected_outcome": "x"},
        "actions": [{"type": "note_episode", "title": "hi", "detail": "there"}],
    })
    run_heartbeat(session, settings, agent=agent, cohort=cohort, kind="routine",
                  slot_id="s1", cognition=cog, md=md)
    d = dash.build_dashboard_data(session, settings)
    hbs = [e for e in d["activity"] if e["category"] == "heartbeats"]
    assert hbs and hbs[0]["agent"] == agent.display_name.split(" · ")[0]
    # the detail summary surfaces the structured journal decision
    row = next(r for r in d["agents"] if r["uuid"] == agent.agent_uuid)
    assert row["detail"]["last_heartbeat_summary"] == "hold and research"


def test_requests_surface_open_tickets():
    session = _session()
    settings = EvoSettings(_env_file=None)
    cohort, agents = _seed(session, settings, n=1)
    tickets.submit_ticket(
        session, agent_uuid=agents[0].agent_uuid, category="paid_data_source",
        capability="access to the NOAA buoy feed for coastal weather",
        problem="need higher-frequency ocean temps",
    )
    d = dash.build_dashboard_data(session, settings)
    assert len(d["requests"]) == 1
    r = d["requests"][0]
    assert r["category"] == "paid_data_source" and r["status"] == "open"
    assert r["requested_by"] >= 1


def test_no_secrets_or_traces_in_payload():
    """The whole serialized payload must not contain credentials, prompts,
    raw genome dumps, DB URLs, or error tracebacks."""
    session = _session()
    settings = EvoSettings(_env_file=None)
    cohort, agents = _seed(session, settings, n=2)
    # write a fact that looks sensitive to make sure we don't dump raw memory
    memory.record_fact(session, agents[0].agent_uuid, "secret key sk-ABC123",
                       {"api_key": "sk-SHOULD-NOT-LEAK", "password": "hunter2"},
                       idem_key="x")
    blob = json.dumps(dash.build_dashboard_data(session, settings), default=str).lower()
    for needle in ("sk-should-not-leak", "hunter2", "api_key", "password",
                   "postgresql://", "traceback", "anthropic_api_key",
                   "x-api-key", "private_key"):
        assert needle not in blob, f"leaked: {needle}"


def test_agent_free_text_is_length_capped():
    session = _session()
    settings = EvoSettings(_env_file=None)
    cohort, agents = _seed(session, settings, n=1)
    # cap helper never returns anything longer than the cap
    long = "z" * 5000
    assert len(dash._cap(long)) <= dash.TEXT_CAP
    assert dash._cap(None) is None
