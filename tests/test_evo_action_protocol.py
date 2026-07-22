"""Regression guard for the ACTION_PROTOCOL prompt text (kalshi_bot/evo/cognition.py).

Every run_backtest/save_strategy attempt across the live population failed
StrategySpec validation, 100% of the time, since the first attempt — because the
prompt only said `run_backtest {spec, ...}` without ever showing agents the actual
required schema, so each agent guessed its own field names (hypothesis_id,
strategy_name, commission_bps, ...), all rejected by StrategySpec's extra="forbid".
Same story for submit_ticket's category: rejected because the valid enum was never
shown. These tests prove the protocol text now documents the real schema/enums, and
that the worked example it gives agents actually validates."""

from __future__ import annotations

import random
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kalshi_bot.evo import budgets, llm
from kalshi_bot.evo.cognition import ACTION_PROTOCOL, ScriptedCognition, build_user_prompt
from kalshi_bot.evo.cohorts import ensure_current_cohort
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.evolution import create_agent
from kalshi_bot.evo.heartbeats import run_heartbeat
from kalshi_bot.evo.marketdata import Quote, StaticMarketData
from kalshi_bot.evo.strategy_spec import METRICS, OPS, validate_spec
from kalshi_bot.evo.tickets import CATEGORIES
from kalshi_bot.models import Base


def test_no_leftover_placeholder_tokens():
    for token in ("MAXN", "METRICS_LIST", "OPS_LIST", "TICKET_CATEGORIES"):
        assert token not in ACTION_PROTOCOL, f"unsubstituted placeholder {token!r} in prompt text"


def test_protocol_documents_real_metric_and_op_vocab():
    for metric in METRICS:
        assert metric in ACTION_PROTOCOL
    for op in OPS:
        assert op in ACTION_PROTOCOL


def test_protocol_documents_real_ticket_categories():
    for category in CATEGORIES:
        assert category in ACTION_PROTOCOL


def test_protocol_forbids_invented_field_names_the_population_actually_guessed():
    # Names live agents fabricated (per the DB record of failed attempts) that the
    # protocol should now explicitly warn against re-guessing.
    for guessed in ("hypothesis_id", "strategy_name", "commission_bps", "order_style"):
        assert guessed in ACTION_PROTOCOL  # named as counter-examples, not valid fields


def test_worked_example_spec_actually_validates():
    # The exact example given to agents in the prompt must itself be valid, or the
    # fix just teaches a new wrong shape.
    example = {
        "name": "weather_fade_v1",
        "universe": {"series_prefixes": ["KXHIGH"]},
        "entry": {"conditions": [{"metric": "spread", "op": "<=", "value": 6}]},
    }
    spec, err = validate_spec(example)
    assert err is None, err
    assert spec is not None
    assert spec.universe.series_prefixes == ["KXHIGH"]
    assert spec.entry.conditions[0].metric == "spread"


def test_protocol_encourages_batching_multiple_backtests_per_heartbeat():
    # Real usage showed agents batching 3-7 DIFFERENT actions per heartbeat
    # already, but never the SAME action (e.g. run_backtest) twice — nothing told
    # them they could. Structurally there was always room: 12 actions/heartbeat,
    # 50 sandbox runs/week.
    assert "run_backtest" in ACTION_PROTOCOL
    lower = ACTION_PROTOCOL.lower()
    assert "same heartbeat" in lower
    assert "cheap" in lower or "generous" in lower


def _run_scripted_heartbeat_and_capture_prompt() -> str:
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    settings = EvoSettings(_env_file=None)
    llm.seed_model_prices(s, settings)
    cohort = ensure_current_cohort(s, settings)
    agent = create_agent(s, settings, cohort, random.Random(1),
                         origin="founder", slot_key="founder:0")
    budgets.ensure_budgets(s, agent.agent_uuid, cohort.id, settings)

    seen: dict = {}

    def script(ctx):
        seen["prompt"] = build_user_prompt(ctx)
        return {"journal": {"decision": "ok"}, "actions": []}

    run_heartbeat(s, settings, agent=agent, cohort=cohort, kind="routine",
                  slot_id="s1", cognition=ScriptedCognition(script), md=StaticMarketData())
    return seen["prompt"]


def test_closing_instruction_reframes_economical_as_prose_not_action_count():
    # "Be economical" alone risked reading as "do less" — a real, if partial,
    # contributor to agents deferring one step per heartbeat instead of batching
    # everything ready. The closing instruction must now say so explicitly, in
    # the actual assembled prompt (not just the static ACTION_PROTOCOL block).
    prompt = _run_scripted_heartbeat_and_capture_prompt()
    assert "hours apart" in prompt
    assert "batch" in prompt.lower()
    assert "economical in prose" in prompt.lower()


def test_routine_output_token_cap_doubled():
    # Doubled (3200 -> 6400) to give headroom for batched multi-backtest output.
    assert EvoSettings(_env_file=None).heartbeat_max_output_tokens == 6400


def test_submit_trade_intent_warns_against_series_prefixes():
    # The exact live bug: an agent used "KXHIGH" (a universe.series_prefixes
    # value, correct for a strategy spec) as a market_ticker for a direct order —
    # not a real, specific, tradable market, so the order got a quote for nothing
    # and sat open forever with zero fills and no error.
    assert "submit_trade_intent" in ACTION_PROTOCOL
    assert "series prefix" in ACTION_PROTOCOL.lower()
    assert "KXHIGH" in ACTION_PROTOCOL


def test_protocol_requires_backtests_to_close_the_loop():
    # Agents were re-running identical losing backtests without ever concluding.
    # The protocol must now demand a decision after each run and forbid re-running a
    # known (already-fingerprinted) spec.
    lower = ACTION_PROTOCOL.lower()
    assert "close the loop" in lower
    assert "times_run_recently" in ACTION_PROTOCOL  # references the recent-backtests view
    assert "conclude_experiment" in ACTION_PROTOCOL


def test_protocol_requires_cited_evidence_for_tickets():
    # A live ticket asserted a stale/hallucinated diagnosis ("error for 10+ heartbeats
    # since cohort start") that the DB flatly contradicted. The protocol must require
    # claims to be grounded in visible evidence, not narrated from memory.
    lower = ACTION_PROTOCOL.lower()
    assert "ground every factual claim" in lower
    assert "do not narrate" in lower
    assert "cite the order_id" in lower


def test_recent_orders_and_backtests_render_in_prompt():
    from kalshi_bot.evo import models as em
    from kalshi_bot.evo import paper

    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    settings = EvoSettings(_env_file=None)
    llm.seed_model_prices(s, settings)
    cohort = ensure_current_cohort(s, settings)
    agent = create_agent(s, settings, cohort, random.Random(1),
                         origin="founder", slot_key="founder:0")
    budgets.ensure_budgets(s, agent.agent_uuid, cohort.id, settings)
    paper.ensure_portfolio(s, agent.agent_uuid, paper.cohort_ledger(cohort.id), 1000.0)

    # a stuck open order + the SAME backtest already run twice, on the record
    s.add(em.EvoOrder(
        agent_uuid=agent.agent_uuid, cohort_id=cohort.id, idem_key="stuck1",
        market_ticker="KXHIGH", side="yes", action="buy", quantity=5, status="open",
    ))
    spec = {"name": "wx", "universe": {"series_prefixes": ["KXHIGH"]}}
    for _ in range(2):
        s.add(em.EvoSandboxRun(
            agent_uuid=agent.agent_uuid, kind="backtest", dataset="backfill_weather",
            params_json={"spec": spec},
            result_json={"n_trades": 100, "win_rate": 0.17, "total_pnl_usd": -22.0},
        ))
    s.flush()

    seen: dict = {}

    def script(ctx):
        seen["prompt"] = build_user_prompt(ctx)
        return {"journal": {"decision": "ok"}, "actions": []}

    run_heartbeat(s, settings, agent=agent, cohort=cohort, kind="routine",
                  slot_id="s1", cognition=ScriptedCognition(script), md=StaticMarketData())
    prompt = seen["prompt"]
    assert "YOUR RECENT ORDERS" in prompt and "KXHIGH" in prompt
    assert "YOUR RECENT BACKTESTS" in prompt and "times_run_recently" in prompt


def test_candidate_tickers_extend_market_summaries_without_leaking_to_extra():
    """candidate_tickers (the orchestrator's universe scan) must reach an agent
    with zero open positions — otherwise market_summaries builds only from
    existing positions (necessarily empty before an agent's first trade), and it
    has no legitimate ticker to reference for submit_trade_intent at all. This is
    exactly how the live "KXHIGH" bug happened."""
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    settings = EvoSettings(_env_file=None)
    llm.seed_model_prices(s, settings)
    cohort = ensure_current_cohort(s, settings)
    agent = create_agent(s, settings, cohort, random.Random(1),
                         origin="founder", slot_key="founder:0")
    budgets.ensure_budgets(s, agent.agent_uuid, cohort.id, settings)

    md = StaticMarketData()
    real_ticker = "KXHIGHNY-26JUL21-B85"
    md.set_quote(Quote(ticker=real_ticker, captured_at=datetime.now(timezone.utc),
                       status="active", yes_bid=45, yes_ask=55))

    seen: dict = {}

    def script(ctx):
        seen["prompt"] = build_user_prompt(ctx)
        seen["market_summaries"] = ctx.market_summaries
        seen["extra"] = ctx.extra
        return {"journal": {"decision": "ok"}, "actions": []}

    run_heartbeat(s, settings, agent=agent, cohort=cohort, kind="routine",
                  slot_id="s1", cognition=ScriptedCognition(script), md=md,
                  extra_context={"candidate_tickers": [real_ticker]})

    assert any(m["ticker"] == real_ticker for m in seen["market_summaries"])
    assert real_ticker in seen["prompt"]
    # consumed to build market_summaries, not dumped raw into ctx.extra
    assert "candidate_tickers" not in seen["extra"]
