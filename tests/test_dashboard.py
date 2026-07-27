"""Read-only evolution dashboard: payload shapes + security (no secrets/traces leaked).

Financial aggregation, event filtering/pagination and strategy rendering have their
own suites in test_dashboard_metrics.py and test_dashboard_events.py.
"""

from __future__ import annotations

import datetime as dt
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


def _now_utc():
    return dt.datetime.now(dt.timezone.utc)


# ---------------------------------------------------------------------------
# Fleet overview
# ---------------------------------------------------------------------------


def test_fleet_payload_empty_population():
    session = _session()
    settings = EvoSettings(_env_file=None)
    d = dash.build_fleet(session, settings)
    assert d["generation"] is None
    assert d["status"] == "between_cohorts"
    assert d["summary"]["bots"] == 0
    # a fleet with no closed trades reports no win rate at all, never 0%
    assert d["summary"]["win_rate"] is None
    assert isinstance(d["components"], list) and len(d["components"]) == 10
    assert d["generations"] == []


def test_fleet_payload_with_bots():
    session = _session()
    settings = EvoSettings(_env_file=None)
    cohort, agents = _seed(session, settings, n=3)
    d = dash.build_fleet(session, settings)

    assert d["generation"]["number"] == cohort.number
    assert d["generation"]["seconds_remaining"] >= 0
    s = d["summary"]
    assert s["bots"] == 3
    assert s["starting_capital_usd"] == 3000.0
    assert s["equity_usd"] == 3000.0  # untraded
    assert s["total_pnl_usd"] == 0.0
    assert s["llm_budget_usd"] == 3 * settings.weekly_llm_ceiling_usd
    assert s["profitable_bots"] == 0 and s["flat_bots"] == 3
    assert {c["system"] for c in d["components"]} >= {
        "Cohort manager", "Heartbeat system", "Paper simulator", "Evolution engine"}
    # health condenses heartbeats rather than listing them
    assert d["health"]["total"] == 3 and d["health"]["checked_in"] == 0
    assert d["health"]["expected_interval_minutes"] > 0


def test_generation_history_lists_the_open_generation():
    session = _session()
    settings = EvoSettings(_env_file=None)
    cohort, _ = _seed(session, settings, n=2)
    gens = dash.build_fleet(session, settings)["generations"]
    assert [g["number"] for g in gens] == [cohort.number]
    row = gens[0]
    assert row["status"] == "open" and row["bots"] == 2
    # an unfinalized generation has no P&L series yet — null, not zero
    assert row["fleet_pnl_usd"] is None
    assert row["finalized"] is False


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------


def test_bot_leaderboard_shape_and_facets():
    session = _session()
    settings = EvoSettings(_env_file=None)
    cohort, agents = _seed(session, settings, n=3)
    d = dash.build_bots(session, settings)

    assert d["total"] == 3 and len(d["bots"]) == 3
    assert d["generation"] == cohort.number
    row = d["bots"][0]
    for key in ("uuid", "name", "family", "generation", "status", "strategy_name",
                "strategy_summary", "last_activity_at", "performance"):
        assert key in row
    for key in ("trades_total", "trades_closed", "trades_open", "realized_pnl_usd",
                "unrealized_pnl_usd", "total_pnl_usd", "win_rate", "best_trade",
                "worst_trade", "avg_trade_usd", "median_trade_usd", "fees_usd"):
        assert key in row["performance"]
    # facets drive the filter controls
    assert set(d["facets"]) == {
        "statuses", "families", "strategy_families", "strategies", "generations"}
    assert d["facets"]["generations"] == [1]


def test_bot_with_no_trades_reports_nulls_not_zeros():
    session = _session()
    settings = EvoSettings(_env_file=None)
    _seed(session, settings, n=1)
    p = dash.build_bots(session, settings)["bots"][0]["performance"]
    assert p["trades_total"] == 0
    assert p["win_rate"] is None
    assert p["avg_trade_usd"] is None
    assert p["median_trade_usd"] is None
    assert p["best_trade"] is None and p["worst_trade"] is None
    assert p["avg_hold_hours"] is None
    # money that genuinely IS zero stays zero
    assert p["realized_pnl_usd"] == 0.0 and p["total_pnl_usd"] == 0.0


def test_leaderboard_filters_and_sorting_are_server_side():
    session = _session()
    settings = EvoSettings(_env_file=None)
    _seed(session, settings, n=4)
    all_bots = dash.build_bots(session, settings)
    assert all_bots["sort"] == "total_pnl" and all_bots["order"] == "desc"

    by_name = dash.build_bots(session, settings, sort="name", order="asc")
    assert [b["name"] for b in by_name["bots"]] == sorted(b["name"] for b in all_bots["bots"])

    # every founder is flat, so the profitable filter must return nothing
    assert dash.build_bots(session, settings, profitability="profitable")["total"] == 0
    assert dash.build_bots(session, settings, profitability="flat")["total"] == 4
    assert dash.build_bots(session, settings, status="idle")["total"] == 4
    assert dash.build_bots(session, settings, status="eliminated")["total"] == 0
    # an unknown generation yields an empty roster rather than an error
    assert dash.build_bots(session, settings, generation=99)["total"] in (0, 4)


def test_bots_are_marked_dormant_when_the_worker_is_throttled():
    session = _session()
    settings = EvoSettings(_env_file=None, max_active_agents=3)
    _seed(session, settings, n=6)  # config snapshot records the cap
    d = dash.build_bots(session, settings)
    statuses = [b["status"] for b in d["bots"]]
    # all six stay visible (they are still in the cohort), but only 3 actually run
    assert len(d["bots"]) == 6
    assert statuses.count("dormant") == 3
    fleet = dash.build_fleet(session, settings)
    assert fleet["throttle"] == {"live": 3, "total": 6, "throttled": True}
    assert fleet["summary"]["live_bots"] == 3


def test_no_throttle_runs_the_full_population():
    session = _session()
    settings = EvoSettings(_env_file=None)  # max_active_agents=0
    _seed(session, settings, n=5)
    fleet = dash.build_fleet(session, settings)
    assert fleet["throttle"] == {"live": 5, "total": 5, "throttled": False}
    assert not any(b["status"] == "dormant" for b in dash.build_bots(session, settings)["bots"])


# ---------------------------------------------------------------------------
# Bot detail
# ---------------------------------------------------------------------------


def test_bot_detail_shape():
    session = _session()
    settings = EvoSettings(_env_file=None)
    cohort, agents = _seed(session, settings, n=1)
    d = dash.build_bot_detail(session, settings, agents[0].agent_uuid)
    for key in ("uuid", "name", "family", "generation", "performance",
                "lifetime_performance", "open_positions", "strategy", "fitness",
                "equity_curve", "children"):
        assert key in d
    strategy = d["strategy"]
    assert set(strategy) >= {"active_strategy", "spec", "genome", "lineage", "drafts"}
    # a bot with no armed strategy still gets a readable explanation, not a blank
    assert strategy["active_strategy"] is None
    assert "No declarative strategy" in strategy["spec"]["summary"]
    assert strategy["lineage"]["parent"] is None  # founder


def test_unknown_bot_detail_returns_none():
    session = _session()
    settings = EvoSettings(_env_file=None)
    _seed(session, settings, n=1)
    assert dash.build_bot_detail(session, settings, "not-a-real-uuid") is None
    assert dash.build_bot_trades(session, settings, "not-a-real-uuid") is None


def test_heartbeat_journal_surfaces_on_the_bot_detail():
    session = _session()
    settings = EvoSettings(_env_file=None)
    cohort, agents = _seed(session, settings, n=1)
    agent = agents[0]
    md = StaticMarketData()
    md.set_quote(Quote(ticker="T1", captured_at=_now_utc(), status="active", yes_bid=44,
                       yes_ask=47, no_bid=53, no_ask=56, yes_levels=[(44, 10)],
                       no_levels=[(53, 10)]))
    cog = ScriptedCognition(lambda ctx: {
        "journal": {"decision": "hold and research", "expected_outcome": "x"},
        "actions": [{"type": "note_episode", "title": "hi", "detail": "there"}],
    })
    run_heartbeat(session, settings, agent=agent, cohort=cohort, kind="routine",
                  slot_id="s1", cognition=cog, md=md)

    detail = dash.build_bot_detail(session, settings, agent.agent_uuid)
    assert detail["last_heartbeat_summary"] == "hold and research"
    # and the fleet health panel now counts it as checked in
    health = dash.build_fleet(session, settings)["health"]
    assert health["checked_in"] == 1
    assert health["last_24h"]["completed"] >= 1


# ---------------------------------------------------------------------------
# Requests + announcements
# ---------------------------------------------------------------------------


def test_requests_surface_open_tickets():
    session = _session()
    settings = EvoSettings(_env_file=None)
    cohort, agents = _seed(session, settings, n=1)
    tickets.submit_ticket(
        session, agent_uuid=agents[0].agent_uuid, category="paid_data_source",
        capability="access to the NOAA buoy feed for coastal weather",
        problem="need higher-frequency ocean temps",
    )
    requests = dash.build_fleet(session, settings)["requests"]
    assert len(requests) == 1
    r = requests[0]
    assert r["category"] == "paid_data_source" and r["status"] == "open"
    assert r["requested_by"] >= 1


def test_announcements_are_a_separate_paginated_resource():
    session = _session()
    settings = EvoSettings(_env_file=None)
    _seed(session, settings, n=1)
    from kalshi_bot.evo import announcements as announce

    announce.seed_announcements(session)
    page = dash.build_announcements(session, limit=2)
    assert page["total"] >= 1
    # the fleet payload carries only the COUNT, so announcements can never take
    # over the top of the page the way the v0.1 card stack did
    fleet = dash.build_fleet(session, settings)
    assert fleet["announcement_count"] == page["total"]
    assert "announcements" not in fleet
    assert len(page["announcements"]) <= 2
    if page["total"] > 2:
        assert page["has_more"] is True
        rest = dash.build_announcements(session, limit=2, offset=2)
        assert rest["offset"] == 2 and rest["announcements"]


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


def _whole_surface(session, settings) -> str:
    """Everything every endpoint could return, serialized — what a public visitor
    could possibly see."""
    payloads = [
        dash.build_fleet(session, settings),
        dash.build_bots(session, settings),
        dash.build_events(session, category="all", limit=200),
        dash.build_announcements(session, limit=100),
    ]
    for row in payloads[1]["bots"]:
        payloads.append(dash.build_bot_detail(session, settings, row["uuid"]))
        payloads.append(dash.build_bot_trades(session, settings, row["uuid"]))
    return json.dumps(payloads, default=str).lower()


def test_no_secrets_or_traces_in_any_payload():
    """No endpoint may return credentials, prompts, raw genome dumps, DB URLs or
    error tracebacks."""
    session = _session()
    settings = EvoSettings(_env_file=None)
    cohort, agents = _seed(session, settings, n=2)
    memory.record_fact(session, agents[0].agent_uuid, "secret key sk-ABC123",
                       {"api_key": "sk-SHOULD-NOT-LEAK", "password": "hunter2"},
                       idem_key="x")
    blob = _whole_surface(session, settings)
    for needle in ("sk-should-not-leak", "hunter2", "api_key", "password",
                   "postgresql://", "traceback", "anthropic_api_key",
                   "x-api-key", "private_key"):
        assert needle not in blob, f"leaked: {needle}"


def test_heartbeat_error_detail_is_never_exposed():
    """Heartbeat status_detail can carry internal tracebacks; the feed must reduce a
    failure to its status label only."""
    from kalshi_bot.evo import models as em

    session = _session()
    settings = EvoSettings(_env_file=None)
    cohort, agents = _seed(session, settings, n=1)
    session.add(em.EvoHeartbeat(
        agent_uuid=agents[0].agent_uuid, cohort_id=cohort.id, kind="routine",
        idem_key="hb-err", status="error",
        status_detail="Traceback: SECRETINTERNALDETAIL at /app/kalshi_bot/evo/llm.py:1",
        raw_output_text="RAWMODELOUTPUTSHOULDNOTLEAK",
    ))
    session.flush()
    blob = _whole_surface(session, settings)
    assert "secretinternaldetail" not in blob
    assert "rawmodeloutputshouldnotleak" not in blob
    # but the failure itself IS reported
    events = dash.build_events(session, category="important", limit=50)["events"]
    assert any(e["kind"] == "heartbeat_error" for e in events)


def test_agent_free_text_is_length_capped():
    long = "z" * 5000
    assert len(dash._cap(long)) <= dash.TEXT_CAP
    assert dash._cap(None) is None
