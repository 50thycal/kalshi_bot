"""Tests for the local (CPU) OpenAI-compatible LLM backend: routing, budget
checks, response parsing, and the same cost-recording discipline as the
Anthropic path (own transaction, zero cost recorded)."""

from __future__ import annotations

import respx
from httpx import Response
from sqlalchemy import select

import kalshi_bot.db as db
from kalshi_bot.evo import budgets, llm
from kalshi_bot.evo.cohorts import ensure_current_cohort
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.evolution import create_agent
from kalshi_bot.evo.models import EvoLlmUsage
from kalshi_bot.models import Base


def _init_sqlite_engine():
    db.init_engine("sqlite://")
    Base.metadata.create_all(db.get_engine())


def _local_settings(**overrides):
    return EvoSettings(
        _env_file=None,
        local_llm_enabled=True,
        local_llm_base_url="http://ollama.local:11434/v1",
        local_llm_model="qwen2.5:7b-instruct",
        **overrides,
    )


def _make_agent(session, settings):
    import random

    cohort = ensure_current_cohort(session, settings)
    agent = create_agent(session, settings, cohort, random.Random(1),
                         origin="founder", slot_key="founder:0")
    budgets.ensure_budgets(session, agent.agent_uuid, cohort.id, settings)
    return agent.agent_uuid, cohort.id


def test_local_available_requires_all_three_fields():
    settings = EvoSettings(_env_file=None)
    client = llm.LlmClient(settings)
    assert client.local_available() is False

    settings2 = _local_settings()
    client2 = llm.LlmClient(settings2)
    assert client2.local_available() is True
    client2.close()
    client.close()


@respx.mock
def test_routine_routes_to_local_backend_when_enabled():
    _init_sqlite_engine()
    settings = _local_settings()
    with db.session_scope() as session:
        agent_uuid, cohort_id = _make_agent(session, settings)

    respx.post("http://ollama.local:11434/v1/chat/completions").mock(
        return_value=Response(200, json={
            "choices": [{"message": {"content": '{"journal": {}, "actions": []}'}}],
            "usage": {"prompt_tokens": 800, "completion_tokens": 200},
        })
    )
    client = llm.LlmClient(settings)
    try:
        with db.session_scope() as session:
            result = client.complete(
                session, agent_uuid=agent_uuid, cohort_id=cohort_id,
                heartbeat_id=None, alias="routine", system_blocks=[{"type": "text", "text": "sys"}],
                user_content="hi", max_tokens=100,
            )
        assert result.error is None
        assert result.cost_usd == 0.0
        assert result.model_id == "qwen2.5:7b-instruct"
        assert result.input_tokens == 800
        assert result.output_tokens == 200

        with db.session_scope() as session:
            rows = list(session.scalars(select(EvoLlmUsage)))
            assert len(rows) == 1
            assert rows[0].cost_usd == 0.0
            assert rows[0].model_alias == "routine"
            remaining = budgets.remaining(session, agent_uuid, cohort_id, "llm_cost_usd")
            assert remaining == settings.weekly_llm_ceiling_usd, (
                "the dollar ceiling must be untouched by a free local call"
            )
    finally:
        client.close()


@respx.mock
def test_deep_alias_never_routes_to_local():
    _init_sqlite_engine()
    settings = _local_settings()
    with db.session_scope() as session:
        agent_uuid, cohort_id = _make_agent(session, settings)
        llm.seed_model_prices(session, settings)

    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=Response(200, json={
            "content": [{"type": "text", "text": '{"journal": {}, "actions": []}'}],
            "usage": {"input_tokens": 100, "output_tokens": 50},
        })
    )
    client = llm.LlmClient(settings, api_key="test-key")
    try:
        with db.session_scope() as session:
            result = client.complete(
                session, agent_uuid=agent_uuid, cohort_id=cohort_id,
                heartbeat_id=None, alias="deep", system_blocks=[],
                user_content="hi", max_tokens=100,
            )
        assert result.error is None
        assert result.cost_usd > 0
        assert result.model_id == settings.model_deep
    finally:
        client.close()


@respx.mock
def test_local_backend_input_too_large_is_rejected_without_network_call():
    _init_sqlite_engine()
    settings = _local_settings(heartbeat_max_input_tokens=10)
    with db.session_scope() as session:
        agent_uuid, cohort_id = _make_agent(session, settings)

    route = respx.post("http://ollama.local:11434/v1/chat/completions")
    client = llm.LlmClient(settings)
    try:
        with db.session_scope() as session:
            result = client.complete(
                session, agent_uuid=agent_uuid, cohort_id=cohort_id,
                heartbeat_id=None, alias="routine", system_blocks=[],
                user_content="this prompt is way too long for the tiny cap",
                max_tokens=100,
            )
        assert result.error is not None
        assert "too large" in result.error
        assert route.call_count == 0
    finally:
        client.close()


@respx.mock
def test_local_backend_http_error_is_surfaced_as_error():
    _init_sqlite_engine()
    settings = _local_settings()
    with db.session_scope() as session:
        agent_uuid, cohort_id = _make_agent(session, settings)

    respx.post("http://ollama.local:11434/v1/chat/completions").mock(
        return_value=Response(500, text="model not loaded")
    )
    client = llm.LlmClient(settings)
    try:
        with db.session_scope() as session:
            result = client.complete(
                session, agent_uuid=agent_uuid, cohort_id=cohort_id,
                heartbeat_id=None, alias="routine", system_blocks=[],
                user_content="hi", max_tokens=100,
            )
        assert result.error is not None
        assert "http 500" in result.error

        with db.session_scope() as session:
            assert list(session.scalars(select(EvoLlmUsage))) == []
    finally:
        client.close()


@respx.mock
def test_local_backend_connection_error_is_logged_and_surfaced(caplog):
    # A connection-level failure (unreachable host / connect timeout / refused) must
    # surface as an error AND be logged with the real reason — otherwise every routine
    # heartbeat degrades to a silent no-op with no clue why.
    import logging as _logging

    import httpx

    _init_sqlite_engine()
    settings = _local_settings()
    with db.session_scope() as session:
        agent_uuid, cohort_id = _make_agent(session, settings)

    respx.post("http://ollama.local:11434/v1/chat/completions").mock(
        side_effect=httpx.ConnectError("nodename nor servname provided")
    )
    client = llm.LlmClient(settings)
    try:
        with caplog.at_level(_logging.WARNING), db.session_scope() as session:
            result = client.complete(
                session, agent_uuid=agent_uuid, cohort_id=cohort_id,
                heartbeat_id=None, alias="routine", system_blocks=[],
                user_content="hi", max_tokens=100,
            )
        assert result.error is not None and "ConnectError" in result.error
        assert any("local llm connection error" in r.message for r in caplog.records)
    finally:
        client.close()


def test_probe_local_not_configured():
    client = llm.LlmClient(EvoSettings(_env_file=None))
    try:
        assert "not configured" in client.probe_local()
    finally:
        client.close()


@respx.mock
def test_probe_local_reports_ok_when_model_present():
    settings = _local_settings()
    respx.get("http://ollama.local:11434/v1/models").mock(
        return_value=Response(200, json={"data": [
            {"id": "qwen2.5:7b-instruct"}, {"id": "llama3.2:3b"}]})
    )
    client = llm.LlmClient(settings)
    try:
        verdict = client.probe_local()
        assert verdict.startswith("OK") and "qwen2.5:7b-instruct" in verdict
    finally:
        client.close()


@respx.mock
def test_probe_local_reports_missing_model():
    settings = _local_settings()
    respx.get("http://ollama.local:11434/v1/models").mock(
        return_value=Response(200, json={"data": [{"id": "some-other-model"}]})
    )
    client = llm.LlmClient(settings)
    try:
        verdict = client.probe_local()
        assert "MISSING" in verdict and "some-other-model" in verdict
    finally:
        client.close()


@respx.mock
def test_probe_local_reports_unreachable_with_exception_type():
    import httpx

    settings = _local_settings()
    respx.get("http://ollama.local:11434/v1/models").mock(
        side_effect=httpx.ConnectTimeout("timed out")
    )
    client = llm.LlmClient(settings)
    try:
        verdict = client.probe_local()
        assert "UNREACHABLE" in verdict and "ConnectTimeout" in verdict
    finally:
        client.close()
