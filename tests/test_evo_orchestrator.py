"""Regression tests for two live production bugs found while verifying the
first deployed cohort: (1) one-time seeding must survive a later-phase
failure, and (2) a successful, already-billed LLM call must never lose its
cost record to a later rollback in the calling heartbeat's transaction.

Bug 1 — seeding and founder-bootstrap shared one DB transaction, and the
in-memory `_bootstrapped` flag was set to True before that transaction
committed. When founder-bootstrap failed later in the same cycle, the whole
transaction rolled back (including the seeded rows) but the flag stayed True
in the running process — so seeding was never retried and evo_model_prices
stayed empty for the process's lifetime, degrading every subsequent
heartbeat with "no price configured". Fixed by giving seeding its own
transaction and only flipping the flag after it commits.

Bug 2 — confirmed live via the Anthropic Console (usage filtered to the
evo service's own API key showed real billed Sonnet-5 tokens with NO
corresponding row in evo_llm_usage). Root cause: LlmClient.complete() made
the real (irreversible, already-billed) API call, then wrote its
EvoLlmUsage row and budget deduction into the CALLER's still-open session
(flush only). During founder bootstrap, a later founder's processing in the
same shared transaction failed and rolled back everything — including the
audit trail and budget deduction for tokens that had already been spent and
billed. Fixed by committing the cost record in its own independent
transaction immediately after a successful API response, in llm.py."""

from __future__ import annotations

import respx
from httpx import Response
from sqlalchemy import select

import kalshi_bot.db as db
from kalshi_bot.evo import budgets, llm, orchestrator
from kalshi_bot.evo.cohorts import ensure_current_cohort
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.evolution import create_agent
from kalshi_bot.evo.models import EvoLlmUsage, EvoModelPrice
from kalshi_bot.models import Base


def _init_sqlite_engine():
    db.init_engine("sqlite://")
    Base.metadata.create_all(db.get_engine())


def test_seeding_survives_a_later_bootstrap_failure(monkeypatch):
    _init_sqlite_engine()
    runtime = orchestrator.EvoRuntime(kalshi_client=object())

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated founder-bootstrap failure")

    monkeypatch.setattr(orchestrator, "bootstrap_founders", _boom)

    try:
        orchestrator.run_evo_cycle(runtime)
    except RuntimeError:
        pass  # the later phase is expected to still raise; that's fine

    with db.session_scope() as session:
        prices = list(session.scalars(select(EvoModelPrice)))
    assert len(prices) == 2, "seeding must commit even though bootstrap_founders raised"
    assert runtime._bootstrapped is True


def test_seeding_retries_if_its_own_transaction_fails(monkeypatch):
    _init_sqlite_engine()
    runtime = orchestrator.EvoRuntime(kalshi_client=object())

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated seeding failure")

    monkeypatch.setattr(orchestrator, "seed_graveyard", _boom)
    monkeypatch.setattr(orchestrator, "bootstrap_founders", lambda *a, **k: [])

    try:
        orchestrator.run_evo_cycle(runtime)
    except RuntimeError:
        pass

    # the flag must NOT be set when seeding itself failed, so the next cycle retries
    assert runtime._bootstrapped is False
    with db.session_scope() as session:
        prices = list(session.scalars(select(EvoModelPrice)))
    assert prices == []


@respx.mock
def test_llm_cost_survives_a_later_rollback_in_the_callers_transaction():
    _init_sqlite_engine()
    settings = EvoSettings(_env_file=None)
    with db.session_scope() as session:
        llm.seed_model_prices(session, settings)
        cohort = ensure_current_cohort(session, settings)
        import random

        agent = create_agent(session, settings, cohort, random.Random(1),
                             origin="founder", slot_key="founder:0")
        budgets.ensure_budgets(session, agent.agent_uuid, cohort.id, settings)
        agent_uuid, cohort_id = agent.agent_uuid, cohort.id

    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=Response(200, json={
            "content": [{"type": "text", "text": '{"journal": {}, "actions": []}'}],
            "usage": {"input_tokens": 1000, "output_tokens": 500},
        })
    )
    client = llm.LlmClient(settings, api_key="test-key")
    try:
        # Simulate the exact production scenario: the LLM call happens (and is
        # billed) as part of a heartbeat's transaction, and something LATER in
        # that same transaction fails, forcing a rollback.
        try:
            with db.session_scope() as caller_session:
                result = client.complete(
                    caller_session, agent_uuid=agent_uuid, cohort_id=cohort_id,
                    heartbeat_id=None, alias="routine", system_blocks=[],
                    user_content="hi", max_tokens=100,
                )
                assert result.error is None
                assert result.cost_usd > 0
                raise RuntimeError("simulated failure later in the same heartbeat")
        except RuntimeError:
            pass  # caller_session's transaction rolled back; that's the point

        with db.session_scope() as session:
            usage_rows = list(session.scalars(select(EvoLlmUsage)))
            assert len(usage_rows) == 1, (
                "the cost of an already-billed API call must survive a later "
                "rollback in the calling heartbeat's transaction"
            )
            assert usage_rows[0].input_tokens == 1000
            remaining = budgets.remaining(session, agent_uuid, cohort_id, "llm_cost_usd")
            assert remaining < settings.weekly_llm_ceiling_usd, (
                "the budget deduction for the already-billed call must also survive"
            )
    finally:
        client.close()
