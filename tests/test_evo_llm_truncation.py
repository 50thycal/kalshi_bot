"""A heartbeat that died because the model ran out of output budget must SAY so.

Live incident (2026-07-28): every tier-3 strategic heartbeat came back with
output_tokens landing on exactly the cap and a status_detail of "no JSON object
in output". That detail is indistinguishable from "the model wrote garbage",
which sent the diagnosis down the wrong path — the real cause was pure
truncation. Both backends already receive the provider's stop reason
(Anthropic `stop_reason`, OpenAI-compatible `finish_reason`); these tests pin
that it is carried on LlmResult and folded into the parse error so the ops SQL
channel shows the cause directly."""

from __future__ import annotations

import json

import respx
from httpx import Response
from sqlalchemy import select

import kalshi_bot.db as db
from kalshi_bot.evo import budgets, llm
from kalshi_bot.evo.cognition import alias_for_kind, max_output_tokens_for
from kalshi_bot.evo.cohorts import ensure_current_cohort
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.evolution import create_agent
from kalshi_bot.evo.models import EvoLlmUsage
from kalshi_bot.models import Base


def _init_sqlite_engine():
    db.init_engine("sqlite://")
    Base.metadata.create_all(db.get_engine())


def _agent(session, settings):
    cohort = ensure_current_cohort(session, settings)
    import random

    agent = create_agent(session, settings, cohort, random.Random(3),
                         origin="founder", slot_key="founder:0")
    budgets.ensure_budgets(session, agent.agent_uuid, cohort.id, settings)
    return cohort, agent


def test_strategic_tier_output_cap_clears_observed_truncation():
    """Tier 3 was capped at 8000 and EVERY live run hit it exactly. The cap must
    now sit meaningfully above that ceiling, and not below tier 2's."""
    s = EvoSettings(_env_file=None)
    assert s.strategic_max_output_tokens > 8000
    assert s.strategic_max_output_tokens >= s.reflection_max_output_tokens
    # the routing helper must actually hand tier 3 its own (larger) cap
    assert alias_for_kind("strategic") == "strategic"
    assert max_output_tokens_for(s, "strategic") == s.strategic_max_output_tokens
    # NOTE: routine's cap is no longer the smallest of the three — it was
    # separately raised to 22000 to clear a reasoning model's observed
    # max_tokens overshoot, not because tier 1 writes more content. Tiers 2/3
    # still share the same (smaller) content-driven ceiling.
    assert max_output_tokens_for(s, "strategic") == max_output_tokens_for(s, "reflection")


def test_input_caps_clear_observed_live_prompt_sizes_with_headroom():
    """Tier 1 was capped at 14000 and live routine prompts reached ~15.6K — 16 of
    17 routine beats in a 6-hour window degraded pre-flight. Every cap must clear
    the largest size actually observed on that tier, with real headroom, and must
    never invert (a tier carrying MORE context cannot have a SMALLER cap)."""
    s = EvoSettings(_env_file=None)
    observed_routine = 15614  # the live "input too large (~15614 tokens)" rejection
    assert s.heartbeat_max_input_tokens >= observed_routine * 1.25
    # tiers 2/3 carry the tier-1 base PLUS graveyard + peer roster
    assert s.reflection_max_input_tokens > s.heartbeat_max_input_tokens
    assert s.strategic_max_input_tokens >= s.reflection_max_input_tokens

    client = llm.LlmClient(s, api_key="")
    for alias in ("routine", "deep", "strategic"):
        assert client._max_input_tokens(alias) >= observed_routine * 1.25


@respx.mock
def test_anthropic_stop_reason_is_carried_on_result():
    _init_sqlite_engine()
    with db.session_scope() as session:
        settings = EvoSettings(_env_file=None)
        llm.seed_model_prices(session, settings)
        cohort, agent = _agent(session, settings)

        respx.post(llm.ANTHROPIC_URL).mock(return_value=Response(200, json={
            "content": [{"type": "text", "text": '{"journal": {"decision": "hi"'}],
            "usage": {"input_tokens": 18000, "output_tokens": 512},
            "stop_reason": "max_tokens",
        }))
        client = llm.LlmClient(settings, api_key="sk-ant-test")
        res = client.complete(
            session, agent_uuid=agent.agent_uuid, cohort_id=cohort.id, heartbeat_id=None,
            alias="strategic", system_blocks=[{"type": "text", "text": "sys"}],
            user_content="go", max_tokens=512,
        )
        assert res.error is None
        assert res.stop_reason == "max_tokens"
        assert res.truncated is True


@respx.mock
def test_openai_finish_reason_is_carried_on_result():
    _init_sqlite_engine()
    with db.session_scope() as session:
        settings = EvoSettings(
            _env_file=None, local_llm_enabled=True,
            local_llm_base_url="http://ollama.local:11434/v1",
            local_llm_model="qwen2.5:7b-instruct",
        )
        llm.seed_model_prices(session, settings)
        cohort, agent = _agent(session, settings)

        respx.post("http://ollama.local:11434/v1/chat/completions").mock(
            return_value=Response(200, json={
                "choices": [{"message": {"content": "{\"journal\":"},
                             "finish_reason": "length"}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 256},
            })
        )
        client = llm.LlmClient(settings, api_key="")
        res = client.complete(
            session, agent_uuid=agent.agent_uuid, cohort_id=cohort.id, heartbeat_id=None,
            alias="routine", system_blocks=[{"type": "text", "text": "sys"}],
            user_content="go", max_tokens=256,
        )
        assert res.error is None
        assert res.stop_reason == "length"
        assert res.truncated is True


@respx.mock
def test_truncated_strategic_heartbeat_status_detail_blames_the_token_cap(
    evo_session, evo_settings, evo_agent
):
    """The whole point, end to end through the real heartbeat: the row an
    operator reads over the ops SQL channel must name the cap, not just say the
    JSON was missing — that ambiguity is what misdirected the live diagnosis."""
    from kalshi_bot.evo.cognition import LlmCognition
    from kalshi_bot.evo.heartbeats import run_heartbeat
    from kalshi_bot.evo.marketdata import StaticMarketData

    agent, cohort = evo_agent
    llm.seed_model_prices(evo_session, evo_settings)

    # Prose that got cut off before the model ever opened its JSON object —
    # genuinely unrecoverable (recovery only ever CLOSES real structure).
    respx.post(llm.ANTHROPIC_URL).mock(return_value=Response(200, json={
        "content": [{"type": "text", "text": "Reviewing my lineage and the graveyard, I"}],
        "usage": {"input_tokens": 18023, "output_tokens": 16000},
        "stop_reason": "max_tokens",
    }))
    cognition = LlmCognition(llm.LlmClient(evo_settings, api_key="sk-ant-test"))
    hb = run_heartbeat(evo_session, evo_settings, agent=agent, cohort=cohort,
                       kind="strategic", slot_id="trunc-1", cognition=cognition,
                       md=StaticMarketData())

    assert hb.status == "degraded"
    detail = hb.status_detail or ""
    assert "no JSON object in output" in detail  # original cause preserved
    assert "16000" in detail        # names the cap that was hit
    assert "max_tokens" in detail   # names the provider's stop reason
    assert "truncated" in detail.lower()


def test_describe_parse_failure_is_a_noop_when_output_was_not_truncated():
    from kalshi_bot.evo.cognition import describe_parse_failure

    ok = llm.LlmResult(text="", model_id="m", alias="routine", output_tokens=120,
                       stop_reason="end_turn")
    assert describe_parse_failure("invalid JSON: boom", ok) == "invalid JSON: boom"
    assert describe_parse_failure(None, ok) is None


def test_json_dumps_roundtrip_of_stop_reason_defaults():
    """A backend that reports no stop reason must not synthesize one."""
    res = llm.LlmResult(text="{}", model_id="m", alias="routine")
    assert res.stop_reason == ""
    assert res.truncated is False
    json.dumps({"stop_reason": res.stop_reason})  # serializable, never None


@respx.mock
def test_openai_falls_back_to_native_finish_reason_when_normalized_is_null():
    """Live incident: OpenRouter's normalized `finish_reason` came back null on
    16 of 36 routine calls and 3 of 6 reflection calls in a 12h window — same
    model, same tier, no pattern by model — while the underlying provider always
    reports one via `native_finish_reason`. Without this fallback, cap
    enforcement was only checkable for ~60% of calls instead of ~100%."""
    _init_sqlite_engine()
    with db.session_scope() as session:
        settings = EvoSettings(
            _env_file=None, local_llm_enabled=True,
            local_llm_base_url="http://ollama.local:11434/v1",
            local_llm_model="qwen2.5:7b-instruct",
        )
        llm.seed_model_prices(session, settings)
        cohort, agent = _agent(session, settings)

        respx.post("http://ollama.local:11434/v1/chat/completions").mock(
            return_value=Response(200, json={
                "choices": [{"message": {"content": "{\"journal\":"},
                             "finish_reason": None,
                             "native_finish_reason": "length"}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 256},
            })
        )
        client = llm.LlmClient(settings, api_key="")
        res = client.complete(
            session, agent_uuid=agent.agent_uuid, cohort_id=cohort.id, heartbeat_id=None,
            alias="routine", system_blocks=[{"type": "text", "text": "sys"}],
            user_content="go", max_tokens=256,
        )
        assert res.stop_reason == "length"
        assert res.truncated is True


@respx.mock
def test_openai_prefers_normalized_finish_reason_over_native_when_both_present():
    """finish_reason must win when populated — native_finish_reason is only a
    fallback for when the normalized field is null, not a replacement for it."""
    _init_sqlite_engine()
    with db.session_scope() as session:
        settings = EvoSettings(
            _env_file=None, local_llm_enabled=True,
            local_llm_base_url="http://ollama.local:11434/v1",
            local_llm_model="qwen2.5:7b-instruct",
        )
        llm.seed_model_prices(session, settings)
        cohort, agent = _agent(session, settings)

        respx.post("http://ollama.local:11434/v1/chat/completions").mock(
            return_value=Response(200, json={
                "choices": [{"message": {"content": "{}"},
                             "finish_reason": "stop",
                             "native_finish_reason": "end_turn"}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 10},
            })
        )
        client = llm.LlmClient(settings, api_key="")
        res = client.complete(
            session, agent_uuid=agent.agent_uuid, cohort_id=cohort.id, heartbeat_id=None,
            alias="routine", system_blocks=[{"type": "text", "text": "sys"}],
            user_content="go", max_tokens=256,
        )
        assert res.stop_reason == "stop"


def test_routine_output_cap_clears_the_reasoning_model_overshoot_with_headroom():
    """Live incident: routine heartbeats on deepseek/deepseek-v4-flash (a
    reasoning model) reported completion_tokens up to 15384 against a 6400 cap
    — 2.4x over — while the heartbeat still completed normally, meaning the old
    number was never actually a ceiling for this model. The new cap must clear
    that observed value with real headroom, and must equal the input cap (the
    operator's explicit sizing choice, not derived)."""
    s = EvoSettings(_env_file=None)
    observed_overshoot = 15384
    assert s.heartbeat_max_output_tokens >= observed_overshoot * 1.2
    assert s.heartbeat_max_output_tokens == s.heartbeat_max_input_tokens == 22000


@respx.mock
def test_anthropic_stop_reason_is_persisted_to_evo_llm_usage():
    """The whole point of persisting it: an operator must be able to query
    whether a cap actually stopped a call, not just see it on the in-memory
    result of the single call that happened to be under test."""
    _init_sqlite_engine()
    with db.session_scope() as session:
        settings = EvoSettings(_env_file=None)
        llm.seed_model_prices(session, settings)
        cohort, agent = _agent(session, settings)

        respx.post(llm.ANTHROPIC_URL).mock(return_value=Response(200, json={
            "content": [{"type": "text", "text": '{"journal": {}, "actions": []}'}],
            "usage": {"input_tokens": 1000, "output_tokens": 50},
            "stop_reason": "end_turn",
        }))
        client = llm.LlmClient(settings, api_key="sk-ant-test")
        client.complete(
            session, agent_uuid=agent.agent_uuid, cohort_id=cohort.id, heartbeat_id=None,
            alias="strategic", system_blocks=[{"type": "text", "text": "sys"}],
            user_content="go", max_tokens=512,
        )
        row = session.scalars(
            select(EvoLlmUsage).where(EvoLlmUsage.agent_uuid == agent.agent_uuid)
        ).one()
        assert row.stop_reason == "end_turn"


@respx.mock
def test_openai_finish_reason_is_persisted_to_evo_llm_usage():
    _init_sqlite_engine()
    with db.session_scope() as session:
        settings = EvoSettings(
            _env_file=None, local_llm_enabled=True,
            local_llm_base_url="http://ollama.local:11434/v1",
            local_llm_model="qwen2.5:7b-instruct",
        )
        llm.seed_model_prices(session, settings)
        cohort, agent = _agent(session, settings)

        respx.post("http://ollama.local:11434/v1/chat/completions").mock(
            return_value=Response(200, json={
                "choices": [{"message": {"content": '{"journal": {}, "actions": []}'},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 900, "completion_tokens": 40},
            })
        )
        client = llm.LlmClient(settings, api_key="")
        client.complete(
            session, agent_uuid=agent.agent_uuid, cohort_id=cohort.id, heartbeat_id=None,
            alias="routine", system_blocks=[{"type": "text", "text": "sys"}],
            user_content="go", max_tokens=256,
        )
        row = session.scalars(
            select(EvoLlmUsage).where(EvoLlmUsage.agent_uuid == agent.agent_uuid)
        ).one()
        assert row.stop_reason == "stop"
