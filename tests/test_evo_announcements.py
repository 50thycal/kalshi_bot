"""Operator announcements: idempotent seeding, active-window filtering, and that
an active announcement actually reaches every agent's heartbeat prompt."""

from __future__ import annotations

import datetime as dt
import random

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kalshi_bot.evo import announcements as announce
from kalshi_bot.evo import budgets, llm
from kalshi_bot.evo.cognition import ScriptedCognition, build_user_prompt
from kalshi_bot.evo.cohorts import ensure_current_cohort
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.evolution import create_agent
from kalshi_bot.evo.heartbeats import run_heartbeat
from kalshi_bot.evo.marketdata import StaticMarketData
from kalshi_bot.evo.models import EvoAnnouncement
from kalshi_bot.models import Base


def _session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)()


def test_seed_announcements_idempotent():
    s = _session()
    n1 = announce.seed_announcements(s)
    n2 = announce.seed_announcements(s)
    assert n1 >= 1 and n2 == 0
    keys = [a.key for a in announce.active_announcements(s)]
    assert "2026-07-cohort-full-week" in keys


def test_inspect_data_capability_is_announced():
    s = _session()
    announce.seed_announcements(s)
    live = {a.key: a for a in announce.active_announcements(s)}
    ann = live.get("2026-07-inspect-data-capability")
    assert ann is not None and "inspect_data" in ann.body
    assert len(ann.body) <= 700  # fits summarize_for_prompt's body cap uncut


def test_active_announcements_respects_window():
    s = _session()
    now = dt.datetime(2026, 7, 19, 12, 0, tzinfo=dt.timezone.utc)
    s.add_all([
        EvoAnnouncement(key="past", title="expired", body="x", category="system_change",
                        effective_at=now - dt.timedelta(days=10),
                        expires_at=now - dt.timedelta(days=1), active=True),
        EvoAnnouncement(key="future", title="not yet", body="x", category="system_change",
                        effective_at=now + dt.timedelta(days=1), active=True),
        EvoAnnouncement(key="inactive", title="off", body="x", category="system_change",
                        effective_at=now - dt.timedelta(days=1), active=False),
        EvoAnnouncement(key="live", title="live one", body="x", category="system_change",
                        effective_at=now - dt.timedelta(hours=1), active=True),
    ])
    s.flush()
    keys = {a.key for a in announce.active_announcements(s, now=now)}
    assert keys == {"live"}


def test_active_announcements_breaks_effective_at_ties_deterministically():
    """Regression: every announcement seeded in the SAME deploy shares the exact
    same effective_at. Once the roster grows past `limit`, ordering by
    effective_at alone leaves ties broken arbitrarily — a still-valid
    announcement can silently vanish from every agent's prompt for no visible
    reason, and which one vanishes can even change between calls. id DESC as a
    secondary sort makes the truncation deterministic and repeatable."""
    s = _session()
    now = dt.datetime(2026, 7, 19, 12, 0, tzinfo=dt.timezone.utc)
    s.add_all([
        EvoAnnouncement(key=f"tied-{i}", title=f"tied {i}", body="x",
                        category="system_change", effective_at=now, active=True)
        for i in range(6)
    ])
    s.flush()
    first = [a.key for a in announce.active_announcements(s, now=now, limit=3)]
    second = [a.key for a in announce.active_announcements(s, now=now, limit=3)]
    assert first == second  # stable across repeated calls, not arbitrary
    assert first == ["tied-5", "tied-4", "tied-3"]  # most-recently-inserted wins ties


def test_announcement_reaches_agent_heartbeat_prompt():
    s = _session()
    settings = EvoSettings(_env_file=None)
    llm.seed_model_prices(s, settings)
    announce.seed_announcements(s)
    cohort = ensure_current_cohort(s, settings)
    agent = create_agent(s, settings, cohort, random.Random(1),
                         origin="founder", slot_key="founder:0")
    budgets.ensure_budgets(s, agent.agent_uuid, cohort.id, settings)

    seen: dict = {}

    def script(ctx):
        seen["ann"] = ctx.announcements
        seen["prompt"] = build_user_prompt(ctx)
        return {"journal": {"decision": "ok"}, "actions": []}

    run_heartbeat(s, settings, agent=agent, cohort=cohort, kind="routine",
                  slot_id="s1", cognition=ScriptedCognition(script), md=StaticMarketData())

    assert any(a["key"] == "2026-07-cohort-full-week" for a in seen["ann"])
    assert "OPERATOR ANNOUNCEMENTS" in seen["prompt"]
    assert "full 7 days" in seen["prompt"]
