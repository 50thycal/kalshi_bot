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

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kalshi_bot.evo import budgets, llm
from kalshi_bot.evo.cognition import ACTION_PROTOCOL, ScriptedCognition, build_user_prompt
from kalshi_bot.evo.cohorts import ensure_current_cohort
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.evolution import create_agent
from kalshi_bot.evo.heartbeats import run_heartbeat
from kalshi_bot.evo.marketdata import StaticMarketData
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
