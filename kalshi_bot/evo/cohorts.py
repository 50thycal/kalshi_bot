"""Cohort lifecycle: boundary math (America/Chicago Mondays), idempotent cohort
creation, membership, and boundary detection (spec §4, §22).

Finalization itself lives in evolution.py; this module owns the calendar."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .audit import audit
from .budgets import ensure_budgets
from .config import EvoSettings
from .constitution import ensure_config_version
from .models import EvoAgent, EvoCohort, EvoCohortMember, EvoPortfolio

logger = logging.getLogger(__name__)


def cohort_boundary_before(ts: datetime, settings: EvoSettings) -> datetime:
    """The most recent cohort boundary at or before ts (default: Monday 00:00
    America/Chicago), returned in UTC."""
    tz = ZoneInfo(settings.cohort_timezone)
    local = ts.astimezone(tz)
    days_back = (local.weekday() - settings.cohort_boundary_weekday) % 7
    boundary_local = (local - timedelta(days=days_back)).replace(
        hour=settings.cohort_boundary_hour, minute=0, second=0, microsecond=0
    )
    if boundary_local > local:
        boundary_local -= timedelta(days=7)
    return boundary_local.astimezone(timezone.utc)


def current_cohort(session) -> EvoCohort | None:
    return session.scalar(
        select(EvoCohort).where(EvoCohort.status == "open").order_by(EvoCohort.number.desc())
    )


def ensure_current_cohort(
    session, settings: EvoSettings, *, now: datetime | None = None
) -> EvoCohort:
    """Idempotently return the open cohort covering `now`, creating cohort 1 if none
    exists. Does NOT auto-roll a cohort whose window has passed — that is the
    evolution engine's finalization job (which then opens the next cohort)."""
    now = now or datetime.now(timezone.utc)
    open_cohort = current_cohort(session)
    if open_cohort is not None:
        return open_cohort
    prev = session.scalar(select(EvoCohort).order_by(EvoCohort.number.desc()).limit(1))
    number = (prev.number + 1) if prev else 1
    starts = cohort_boundary_before(now, settings)
    cfg = ensure_config_version(session, settings)
    wildcard = (
        settings.wildcard_every_n_cohorts > 0
        and number % settings.wildcard_every_n_cohorts == 0
    )
    cohort = EvoCohort(
        number=number,
        starts_at=starts,
        ends_at=starts + timedelta(days=settings.cohort_days),
        status="open",
        config_version_id=cfg.id,
        rng_seed=settings.bootstrap_rng_seed + number,
        wildcard_cohort=wildcard,
    )
    session.add(cohort)
    session.flush()
    audit(session, "cohort_opened", cohort_id=cohort.id, number=number,
          starts_at=starts.isoformat(), wildcard=wildcard)
    return cohort


def cohort_is_over(cohort: EvoCohort, *, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    ends = cohort.ends_at if cohort.ends_at.tzinfo else cohort.ends_at.replace(tzinfo=timezone.utc)
    return now >= ends


def join_cohort(
    session,
    agent: EvoAgent,
    cohort: EvoCohort,
    settings: EvoSettings,
    *,
    carried_scale: float = 1.0,
) -> EvoCohortMember:
    """Idempotently add an agent to a cohort: membership row, equal budgets, and the
    cohort competition ledger portfolio at exactly the normalized starting capital."""
    member = session.scalar(
        select(EvoCohortMember).where(
            EvoCohortMember.cohort_id == cohort.id,
            EvoCohortMember.agent_uuid == agent.agent_uuid,
        )
    )
    if member is None:
        member = EvoCohortMember(
            cohort_id=cohort.id,
            agent_uuid=agent.agent_uuid,
            starting_capital=settings.starting_capital_usd,
            carried_scale=carried_scale,
        )
        session.add(member)
        session.flush()
    ensure_budgets(session, agent.agent_uuid, cohort.id, settings)
    ledger = f"cohort:{cohort.id}"
    pf = session.scalar(
        select(EvoPortfolio).where(
            EvoPortfolio.agent_uuid == agent.agent_uuid, EvoPortfolio.ledger == ledger
        )
    )
    if pf is None:
        session.add(
            EvoPortfolio(
                agent_uuid=agent.agent_uuid,
                ledger=ledger,
                cash_usd=settings.starting_capital_usd,
                starting_capital_usd=settings.starting_capital_usd,
                peak_nav_usd=settings.starting_capital_usd,
            )
        )
        session.flush()
    return member


def cohort_members(session, cohort_id: int) -> list[EvoCohortMember]:
    return list(
        session.scalars(
            select(EvoCohortMember).where(EvoCohortMember.cohort_id == cohort_id)
        )
    )


def active_agents(session) -> list[EvoAgent]:
    return list(
        session.scalars(select(EvoAgent).where(EvoAgent.status == "active").order_by(EvoAgent.id))
    )
