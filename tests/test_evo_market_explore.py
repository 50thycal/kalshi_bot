"""explore_markets: on-demand live Kalshi market discovery (read-only), so agents can
find NEW domains beyond weather. Proves formatting/caps, safe degradation, and that a
scan resurfaces in the agent's next heartbeat."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from kalshi_bot.evo import budgets, llm, market_explore
from kalshi_bot.evo import models as em
from kalshi_bot.evo.cognition import ACTION_PROTOCOL, ScriptedCognition, build_user_prompt
from kalshi_bot.evo.cohorts import ensure_current_cohort
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.constitution import PERMITTED_ACTIONS
from kalshi_bot.evo.evolution import create_agent
from kalshi_bot.evo.heartbeats import run_heartbeat
from kalshi_bot.evo.marketdata import StaticMarketData
from kalshi_bot.models import Base

NOW = datetime.now(timezone.utc)


class _FakeMD:
    """Market data with a list_markets like LiveMarketData; get_quote returns None so
    assemble_context's market_summaries stays empty."""

    def __init__(self):
        self.calls: list[dict] = []

    def get_quote(self, ticker):
        return None

    def list_markets(self, **params):
        self.calls.append(params)
        series = params.get("series_ticker") or "KXBTCD"
        return [
            {"ticker": f"{series}-26JUL2317-T60000", "event_ticker": f"{series}-26JUL2317",
             "category": "Crypto", "yes_bid": 40, "yes_ask": 44, "volume": 100,
             "close_time": NOW + timedelta(hours=1), "status": "active"},
            {"ticker": f"{series}-26JUL2317-T61000", "event_ticker": f"{series}-26JUL2317",
             "category": "Crypto", "yes_bid": 22, "yes_ask": 26, "volume": 30,
             "close_time": NOW + timedelta(hours=1), "status": "active"},
        ]


def _session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)()


def test_explore_formats_and_caps_whitelisted_fields():
    res = market_explore.explore(_FakeMD(), series="KXBTCD", status="open", limit=10)
    assert res["count"] == 2 and res["series"] == "KXBTCD"
    row = res["markets"][0]
    assert set(row) == set(market_explore._FIELDS)  # only whitelisted fields
    assert row["ticker"].startswith("KXBTCD-")
    assert isinstance(row["close_time"], str)  # datetime serialized


def test_explore_passes_series_and_caps_limit():
    md = _FakeMD()
    market_explore.explore(md, series="KXHIGHNY", limit=999)
    assert md.calls[0]["series_ticker"] == "KXHIGHNY"
    assert md.calls[0]["max_markets"] == market_explore.MAX_LIMIT


def test_explore_tolerates_backend_without_list_markets():
    # StaticMarketData has no list_markets — must degrade to empty, not raise
    res = market_explore.explore(StaticMarketData(), series="KXBTCD")
    assert res == {"series": "KXBTCD", "status": "open", "count": 0, "markets": []}


def test_explore_markets_permitted_and_documented():
    assert "explore_markets" in PERMITTED_ACTIONS
    assert "explore_markets" in ACTION_PROTOCOL
    assert "KXBTCD" in ACTION_PROTOCOL  # a concrete non-weather example is shown


def _agent(s, settings):
    llm.seed_model_prices(s, settings)
    cohort = ensure_current_cohort(s, settings)
    agent = create_agent(s, settings, cohort, random.Random(1),
                         origin="founder", slot_key="founder:0")
    budgets.ensure_budgets(s, agent.agent_uuid, cohort.id, settings)
    return agent, cohort


def test_explore_markets_action_records_scan_and_surfaces_next_heartbeat():
    s = _session()
    settings = EvoSettings(_env_file=None)
    agent, cohort = _agent(s, settings)
    cog = ScriptedCognition(lambda ctx: {
        "journal": {"decision": "discover"},
        "actions": [{"type": "explore_markets", "series": "KXBTCD"}],
    })
    hb = run_heartbeat(s, settings, agent=agent, cohort=cohort, kind="routine",
                       slot_id="x1", cognition=cog, md=_FakeMD())
    assert hb.actions_json[0]["ok"] and hb.actions_json[0]["count"] == 2
    run = s.scalar(select(em.EvoSandboxRun).where(em.EvoSandboxRun.kind == "market_scan"))
    assert run is not None and run.dataset == "KXBTCD"
    assert budgets.remaining(s, agent.agent_uuid, cohort.id, "market_scans") == (
        settings.weekly_market_scans - 1
    )
    seen: dict = {}
    run_heartbeat(
        s, settings, agent=agent, cohort=cohort, kind="routine", slot_id="x2",
        cognition=ScriptedCognition(lambda ctx: seen.__setitem__("p", build_user_prompt(ctx))
                                    or {"journal": {"decision": "ok"}, "actions": []}),
        md=_FakeMD(),
    )
    assert "YOUR RECENT MARKET SCANS" in seen["p"] and "KXBTCD-" in seen["p"]


def test_explore_markets_rejects_bad_status():
    s = _session()
    settings = EvoSettings(_env_file=None)
    agent, cohort = _agent(s, settings)
    cog = ScriptedCognition(lambda ctx: {
        "journal": {"decision": "x"},
        "actions": [{"type": "explore_markets", "series": "KXBTCD", "status": "bogus"}],
    })
    hb = run_heartbeat(s, settings, agent=agent, cohort=cohort, kind="routine",
                       slot_id="xbad", cognition=cog, md=_FakeMD())
    assert "rejected" in hb.actions_json[0] and "status must be" in hb.actions_json[0]["rejected"]
