"""Raw model output is captured ONLY on a failed heartbeat (for operator
diagnosis via the read-only ops SQL channel), never on success, and never
surfaced anywhere an agent or the public dashboard can read it."""

from __future__ import annotations

import random

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kalshi_bot.evo import budgets, llm
from kalshi_bot.evo.cognition import Cognition, CognitionResult, ScriptedCognition, parse_output
from kalshi_bot.evo.cohorts import ensure_current_cohort
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.evolution import create_agent
from kalshi_bot.evo.heartbeats import RAW_OUTPUT_CAP, run_heartbeat
from kalshi_bot.evo.marketdata import StaticMarketData
from kalshi_bot.models import Base


class _RawTextCognition(Cognition):
    """Mirrors exactly what LlmCognition.decide does: parse_output() on raw model
    text, with raw_text always passed through on CognitionResult regardless of
    success — the real path a malformed LLM response takes."""

    def __init__(self, raw_text: str) -> None:
        self.raw_text = raw_text

    def decide(self, session, settings, ctx):
        journal, actions, err = parse_output(self.raw_text)
        return CognitionResult(journal=journal, actions=actions, raw_text=self.raw_text,
                               error=err, model_alias="test", model_id="test")


def _setup():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    session = sessionmaker(bind=eng, expire_on_commit=False)()
    settings = EvoSettings(_env_file=None)
    llm.seed_model_prices(session, settings)
    cohort = ensure_current_cohort(session, settings)
    agent = create_agent(session, settings, cohort, random.Random(1),
                         origin="founder", slot_key="founder:0")
    budgets.ensure_budgets(session, agent.agent_uuid, cohort.id, settings)
    return session, settings, cohort, agent


def test_malformed_json_output_is_captured_for_diagnosis():
    session, settings, cohort, agent = _setup()
    marker = "TOTALLY-MALFORMED-MARKER"
    # missing comma between two journal-like entries — the real-world failure
    # signature seen live ("Expecting ',' delimiter")
    raw = f'{{"journal": {{"decision": "{marker}"}} "actions": []}}'
    hb = run_heartbeat(session, settings, agent=agent, cohort=cohort, kind="routine",
                       slot_id="s1", cognition=_RawTextCognition(raw), md=StaticMarketData())

    assert hb.status == "degraded"
    assert "invalid JSON" in hb.status_detail
    assert hb.raw_output_text is not None
    assert marker in hb.raw_output_text
    assert len(hb.raw_output_text) <= RAW_OUTPUT_CAP


def test_raw_output_capped_at_bound():
    session, settings, cohort, agent = _setup()
    huge = "{" + "x" * (RAW_OUTPUT_CAP * 3)  # no closing brace -> parse failure
    hb = run_heartbeat(session, settings, agent=agent, cohort=cohort, kind="routine",
                       slot_id="s1", cognition=_RawTextCognition(huge), md=StaticMarketData())

    assert hb.status == "degraded"
    assert len(hb.raw_output_text) == RAW_OUTPUT_CAP


def test_successful_heartbeat_never_captures_raw_output():
    session, settings, cohort, agent = _setup()
    cog = ScriptedCognition(lambda ctx: {
        "journal": {"decision": "all good"}, "actions": [],
    })

    hb = run_heartbeat(session, settings, agent=agent, cohort=cohort, kind="routine",
                       slot_id="s1", cognition=cog, md=StaticMarketData())

    assert hb.status == "completed"
    assert hb.raw_output_text is None


def test_non_parse_degradation_has_no_raw_output():
    """e.g. budget/config-gated failures never reach the LLM, so there is no raw
    text to capture — raw_output_text stays null, not an empty string."""
    session, settings, cohort, agent = _setup()
    cog = ScriptedCognition(lambda ctx: (_ for _ in ()).throw(RuntimeError("boom")))

    hb = run_heartbeat(session, settings, agent=agent, cohort=cohort, kind="routine",
                       slot_id="s1", cognition=cog, md=StaticMarketData())

    assert hb.status == "degraded"
    assert hb.raw_output_text is None


def test_raw_output_never_reaches_dashboard_payload():
    from kalshi_bot.dashboard import data as dash

    session, settings, cohort, agent = _setup()
    marker = "sk-DEFINITELY-SHOULD-NOT-LEAK-TO-DASHBOARD"
    raw = f'{{"journal": {{"decision": "{marker}"}} "actions": []}}'
    hb = run_heartbeat(session, settings, agent=agent, cohort=cohort, kind="routine",
                       slot_id="s1", cognition=_RawTextCognition(raw), md=StaticMarketData())
    assert hb.status == "degraded" and marker in hb.raw_output_text  # sanity: it was captured

    import json
    blob = json.dumps(dash.build_dashboard_data(session, settings), default=str)
    assert marker not in blob
