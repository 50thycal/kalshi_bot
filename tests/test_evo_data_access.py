"""inspect_data: bounded, allowlisted, read-only access to ALL our collected data
(agents are not limited to weather). Proves the safety envelope + that a pulled read
resurfaces in the agent's next heartbeat."""

from __future__ import annotations

import random
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from kalshi_bot.evo import budgets, data_access, llm
from kalshi_bot.evo import models as em
from kalshi_bot.evo.cognition import ACTION_PROTOCOL, ScriptedCognition, build_user_prompt
from kalshi_bot.evo.cohorts import ensure_current_cohort
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.constitution import PERMITTED_ACTIONS
from kalshi_bot.evo.evolution import create_agent
from kalshi_bot.evo.heartbeats import run_heartbeat
from kalshi_bot.evo.marketdata import StaticMarketData
from kalshi_bot.models import (
    Base,
    CryptoLadderSnapshot,
    MmSellPositionTick,
    PaperTrade,
)

NOW = datetime.now(timezone.utc)


def _session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)()


def _seed(s):
    for i in range(3):
        s.add(PaperTrade(
            market_ticker=f"KXMMKT-{i}", strategy="mmsell", side="no", action="sell",
            assumed_price=60, quantity=10, status="settled",
            resolved_value=(0 if i else 100), pnl=1.5, created_at=NOW,
        ))
    s.add(PaperTrade(market_ticker="KXOTH-1", strategy="theta", side="yes",
                     action="buy", status="open", created_at=NOW))
    s.add(CryptoLadderSnapshot(
        captured_at=NOW, series="KXBTCD", event_ticker="KXBTCD-1",
        market_ticker="KXBTCD-1-T60000", strike_type="greater", floor_strike=60000,
        yes_bid_cents=40, yes_ask_cents=44, mid_cents=42, volume=100,
        minutes_to_close=30, spot=60500,
    ))
    s.add(MmSellPositionTick(market_ticker="KXMMKT-0", captured_at=NOW, yes_bid=40,
                             yes_ask=44, no_bid=56, no_ask=60, mid=42, volume=5))
    s.flush()


def test_inspect_returns_capped_allowlisted_rows():
    s = _session()
    _seed(s)
    res, err = data_access.inspect(s, "paper_trades", filters={"strategy": "mmsell"})
    assert err is None
    assert res["count"] == 3 and all(r["strategy"] == "mmsell" for r in res["rows"])
    # only whitelisted columns are ever returned
    assert set(res["rows"][0]) <= set(data_access.SOURCES["paper_trades"]["columns"])
    assert isinstance(res["rows"][0]["created_at"], str)  # datetime serialized


def test_inspect_rejects_unknown_source():
    res, err = data_access.inspect(_session(), "secrets")
    assert res is None and "unknown source" in err


def test_inspect_rejects_non_allowlisted_filter():
    s = _session()
    _seed(s)
    res, err = data_access.inspect(s, "paper_trades", filters={"pnl": 1.5})
    assert res is None and "cannot filter" in err


def test_inspect_caps_limit():
    s = _session()
    for i in range(80):
        s.add(PaperTrade(market_ticker=f"K-{i}", strategy="mmsell", created_at=NOW))
    s.flush()
    res, err = data_access.inspect(s, "paper_trades", limit=1000)
    assert err is None and res["count"] == data_access.MAX_LIMIT


def test_crypto_and_mmsell_sources_query():
    s = _session()
    _seed(s)
    c, err = data_access.inspect(s, "crypto_ladders")
    assert err is None and c["count"] == 1
    assert c["rows"][0]["market_ticker"] == "KXBTCD-1-T60000"
    m, err = data_access.inspect(s, "mmsell_ticks", filters={"market_ticker": "KXMMKT-0"})
    assert err is None and m["count"] == 1


def test_every_source_queryable_against_empty_db():
    # guards against a mistyped column name in any SOURCES entry
    s = _session()
    for name in data_access.SOURCES:
        res, err = data_access.inspect(s, name, limit=1)
        assert err is None, (name, err)
        assert res["count"] == 0


def test_inspect_data_permitted_and_documented():
    assert "inspect_data" in PERMITTED_ACTIONS
    assert "inspect_data" in ACTION_PROTOCOL
    for src in data_access.SOURCES:
        assert src in ACTION_PROTOCOL  # sources are listed for the agent


def _agent(s, settings):
    llm.seed_model_prices(s, settings)
    cohort = ensure_current_cohort(s, settings)
    agent = create_agent(s, settings, cohort, random.Random(1),
                         origin="founder", slot_key="founder:0")
    budgets.ensure_budgets(s, agent.agent_uuid, cohort.id, settings)
    return agent, cohort


def test_inspect_data_action_records_read_and_surfaces_next_heartbeat():
    s = _session()
    settings = EvoSettings(_env_file=None)
    agent, cohort = _agent(s, settings)
    _seed(s)
    cog = ScriptedCognition(lambda ctx: {
        "journal": {"decision": "look"},
        "actions": [{"type": "inspect_data", "source": "paper_trades",
                     "filters": {"strategy": "mmsell"}}],
    })
    hb = run_heartbeat(s, settings, agent=agent, cohort=cohort, kind="routine",
                       slot_id="d1", cognition=cog, md=StaticMarketData())
    assert hb.actions_json[0]["ok"] and hb.actions_json[0]["count"] == 3
    run = s.scalar(select(em.EvoSandboxRun).where(em.EvoSandboxRun.kind == "data_read"))
    assert run is not None and run.dataset == "paper_trades"
    assert budgets.remaining(s, agent.agent_uuid, cohort.id, "data_reads") == (
        settings.weekly_data_reads - 1
    )
    # the rows resurface in the NEXT heartbeat's prompt (outcomes aren't re-fed same turn)
    seen: dict = {}
    run_heartbeat(
        s, settings, agent=agent, cohort=cohort, kind="routine", slot_id="d2",
        cognition=ScriptedCognition(lambda ctx: seen.__setitem__("p", build_user_prompt(ctx))
                                    or {"journal": {"decision": "ok"}, "actions": []}),
        md=StaticMarketData(),
    )
    assert "YOUR RECENT DATA READS" in seen["p"] and "mmsell" in seen["p"]


def test_data_reads_do_not_pollute_recent_backtests_view():
    from kalshi_bot.evo import sandbox
    s = _session()
    settings = EvoSettings(_env_file=None)
    agent, cohort = _agent(s, settings)
    data_access.record_read(
        s, agent_uuid=agent.agent_uuid, heartbeat_id=None,
        result={"source": "paper_trades", "filters": {}, "count": 0, "rows": []},
    )
    assert sandbox.recent_runs(s, agent.agent_uuid) == []  # data_read kind excluded
