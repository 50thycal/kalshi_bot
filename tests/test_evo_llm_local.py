"""Tests for the OpenAI-compatible routine LLM backend: routing, budget checks,
response parsing, and cost-recording discipline (own transaction). Covers both
shapes — a free self-hosted server (no auth, zero cost) and a paid hosted
provider like Groq (bearer token, real per-Mtok cost, dollar-ceiling enforced)."""

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
    base = dict(
        _env_file=None,
        local_llm_enabled=True,
        local_llm_base_url="http://ollama.local:11434/v1",
        local_llm_model="qwen2.5:7b-instruct",
    )
    base.update(overrides)  # let callers override any default (e.g. the model)
    return EvoSettings(**base)


def _paid_settings(**overrides):
    """A hosted provider (Groq-shaped): bearer key + per-Mtok cost rates."""
    return _local_settings(
        local_llm_model="llama-3.1-8b-instant",
        local_llm_api_key="gsk_test_key",
        local_llm_input_cost_per_mtok=0.05,
        local_llm_output_cost_per_mtok=0.08,
        **overrides,
    )


def _openrouter_settings(**overrides):
    """A hosted router (OpenRouter-shaped): same paid-provider mechanics as Groq,
    plus the base_url that triggers the optional identification headers."""
    return _local_settings(
        local_llm_base_url="https://openrouter.ai/api/v1",
        local_llm_model="meta-llama/llama-3.1-8b-instruct",
        local_llm_api_key="sk-or-test-key",
        local_llm_input_cost_per_mtok=0.05,
        local_llm_output_cost_per_mtok=0.08,
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


# --- paid hosted provider (Groq-shaped) --------------------------------------


@respx.mock
def test_paid_provider_sends_bearer_and_books_real_cost():
    _init_sqlite_engine()
    settings = _paid_settings()
    with db.session_scope() as session:
        agent_uuid, cohort_id = _make_agent(session, settings)

    route = respx.post("http://ollama.local:11434/v1/chat/completions").mock(
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
                heartbeat_id=None, alias="routine",
                system_blocks=[{"type": "text", "text": "sys"}],
                user_content="hi", max_tokens=100,
            )
        expected = llm.rate_cost_usd(800, 200, 0.05, 0.08)
        assert expected > 0
        assert result.error is None
        assert result.cost_usd == expected
        assert result.model_id == "llama-3.1-8b-instant"
        # bearer token was sent to the hosted provider
        assert route.calls.last.request.headers.get("authorization") == "Bearer gsk_test_key"

        with db.session_scope() as session:
            rows = list(session.scalars(select(EvoLlmUsage)))
            assert len(rows) == 1
            assert float(rows[0].cost_usd) == expected
            remaining = budgets.remaining(session, agent_uuid, cohort_id, "llm_cost_usd")
            assert abs(remaining - (settings.weekly_llm_ceiling_usd - expected)) < 1e-9, (
                "the real cost must be charged against the weekly dollar ceiling"
            )
    finally:
        client.close()


@respx.mock
def test_paid_provider_refuses_when_dollar_ceiling_reached():
    _init_sqlite_engine()
    # ceiling of $0 => any projected cost > 0 must refuse before any network call
    settings = _paid_settings(weekly_llm_ceiling_usd=0.0)
    with db.session_scope() as session:
        agent_uuid, cohort_id = _make_agent(session, settings)

    route = respx.post("http://ollama.local:11434/v1/chat/completions")
    client = llm.LlmClient(settings)
    try:
        with db.session_scope() as session:
            result = client.complete(
                session, agent_uuid=agent_uuid, cohort_id=cohort_id,
                heartbeat_id=None, alias="routine", system_blocks=[],
                user_content="hi", max_tokens=100,
            )
        assert result.error is not None and "ceiling" in result.error
        assert route.call_count == 0
        with db.session_scope() as session:
            assert list(session.scalars(select(EvoLlmUsage))) == []
    finally:
        client.close()


@respx.mock
def test_free_local_backend_sends_no_auth_header():
    _init_sqlite_engine()
    settings = _local_settings()  # no key, no rates => free self-host
    with db.session_scope() as session:
        agent_uuid, cohort_id = _make_agent(session, settings)

    route = respx.post("http://ollama.local:11434/v1/chat/completions").mock(
        return_value=Response(200, json={
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        })
    )
    client = llm.LlmClient(settings)
    try:
        with db.session_scope() as session:
            result = client.complete(
                session, agent_uuid=agent_uuid, cohort_id=cohort_id,
                heartbeat_id=None, alias="routine", system_blocks=[],
                user_content="hi", max_tokens=50,
            )
        assert result.error is None
        assert result.cost_usd == 0.0
        assert "authorization" not in route.calls.last.request.headers
    finally:
        client.close()


@respx.mock
def test_probe_local_sends_bearer_when_key_configured():
    settings = _paid_settings()
    route = respx.get("http://ollama.local:11434/v1/models").mock(
        return_value=Response(200, json={"data": [{"id": "llama-3.1-8b-instant"}]})
    )
    client = llm.LlmClient(settings)
    try:
        verdict = client.probe_local()
        assert verdict.startswith("OK")
        assert route.calls.last.request.headers.get("authorization") == "Bearer gsk_test_key"
    finally:
        client.close()


# --- OpenRouter (a second paid provider, same code path) ---------------------


@respx.mock
def test_openrouter_completes_with_bearer_and_identification_headers():
    """OpenRouter is just another OpenAI-compatible /chat/completions provider —
    no provider-specific branching beyond auth + its optional, harmless-elsewhere
    HTTP-Referer/X-Title headers. Confirms the whole existing paid-provider path
    (routing, cost, ceiling) works unmodified against a second real provider."""
    _init_sqlite_engine()
    settings = _openrouter_settings()
    with db.session_scope() as session:
        agent_uuid, cohort_id = _make_agent(session, settings)

    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=Response(200, json={
            "choices": [{"message": {"content": '{"journal": {}, "actions": []}'}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 150},
        })
    )
    client = llm.LlmClient(settings)
    try:
        with db.session_scope() as session:
            result = client.complete(
                session, agent_uuid=agent_uuid, cohort_id=cohort_id,
                heartbeat_id=None, alias="routine",
                system_blocks=[{"type": "text", "text": "sys"}],
                user_content="hi", max_tokens=100,
            )
        expected = llm.rate_cost_usd(900, 150, 0.05, 0.08)
        assert result.error is None
        assert result.cost_usd == expected
        assert result.model_id == "meta-llama/llama-3.1-8b-instruct"

        sent = route.calls.last.request.headers
        assert sent.get("authorization") == "Bearer sk-or-test-key"
        assert sent.get("http-referer") == "https://github.com/50thycal/kalshi_bot"
        assert sent.get("x-title") == "kalshi-evo-bot"

        with db.session_scope() as session:
            rows = list(session.scalars(select(EvoLlmUsage)))
            assert len(rows) == 1
            assert float(rows[0].cost_usd) == expected
    finally:
        client.close()


@respx.mock
def test_identification_headers_are_openrouter_only():
    """The HTTP-Referer/X-Title headers must NOT leak to Groq or a self-hosted
    server — they're gated strictly on the OpenRouter base_url."""
    _init_sqlite_engine()
    settings = _paid_settings()  # Groq-shaped, not OpenRouter
    with db.session_scope() as session:
        agent_uuid, cohort_id = _make_agent(session, settings)

    route = respx.post("http://ollama.local:11434/v1/chat/completions").mock(
        return_value=Response(200, json={
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        })
    )
    client = llm.LlmClient(settings)
    try:
        with db.session_scope() as session:
            client.complete(
                session, agent_uuid=agent_uuid, cohort_id=cohort_id,
                heartbeat_id=None, alias="routine", system_blocks=[],
                user_content="hi", max_tokens=50,
            )
        sent = route.calls.last.request.headers
        assert "http-referer" not in sent
        assert "x-title" not in sent
    finally:
        client.close()


@respx.mock
def test_openrouter_probe_sends_identification_headers():
    settings = _openrouter_settings()
    route = respx.get("https://openrouter.ai/api/v1/models").mock(
        return_value=Response(200, json={"data": [{"id": "meta-llama/llama-3.1-8b-instruct"}]})
    )
    client = llm.LlmClient(settings)
    try:
        verdict = client.probe_local()
        assert verdict.startswith("OK")
        sent = route.calls.last.request.headers
        assert sent.get("authorization") == "Bearer sk-or-test-key"
        assert sent.get("http-referer") == "https://github.com/50thycal/kalshi_bot"
        assert sent.get("x-title") == "kalshi-evo-bot"
    finally:
        client.close()
