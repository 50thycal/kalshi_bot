"""Cohort lifecycle: birth-anchored windows, idempotent cohort creation,
membership, and boundary detection (spec §4, §22).

A cohort runs for exactly `cohort_days` from the moment it is created — every
population gets a full week, never a stub cut short by where a fixed calendar
boundary happened to fall. Finalization itself lives in evolution.py; this module
owns the calendar."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .audit import audit
from .budgets import ensure_budgets
from .config import EvoSettings
from .constitution import ensure_config_version
from .models import EvoAgent, EvoCohort, EvoCohortMember, EvoPortfolio

logger = logging.getLogger(__name__)

# A cohort window that begins more than this far before the cohort row was created
# is a legacy calendar-snapped window (see reanchor_open_cohort); a birth-anchored
# cohort's starts_at sits within a breath of its created_at.
_LEGACY_SNAP_TOLERANCE = timedelta(hours=1)


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
    starts = now  # birth-anchored: the cohort runs exactly cohort_days from creation
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


def reanchor_open_cohort(session, settings: EvoSettings) -> bool:
    """One-time healing for the legacy calendar-snapped cohort window.

    Earlier versions snapped a new cohort's starts_at back to the previous Monday
    00:00 America/Chicago, so a cohort created late in the week ran only a stub
    before the fixed Monday boundary. If the open cohort's window began well before
    the cohort row actually existed (created_at), re-anchor it to birth so the
    population gets a full `cohort_days` week. Idempotent and safe to call every
    cycle: birth-anchored cohorts (starts_at ≈ created_at) never match, so this is
    a no-op the moment there is nothing left to fix. Returns True if it re-anchored."""
    cohort = current_cohort(session)
    if cohort is None:
        return False
    created = cohort.created_at if cohort.created_at.tzinfo else cohort.created_at.replace(
        tzinfo=timezone.utc)
    starts = cohort.starts_at if cohort.starts_at.tzinfo else cohort.starts_at.replace(
        tzinfo=timezone.utc)
    if created - starts <= _LEGACY_SNAP_TOLERANCE:
        return False
    cohort.starts_at = created
    cohort.ends_at = created + timedelta(days=settings.cohort_days)
    session.flush()
    audit(session, "cohort_reanchored", cohort_id=cohort.id, number=cohort.number,
          old_starts_at=starts.isoformat(), new_starts_at=created.isoformat(),
          new_ends_at=cohort.ends_at.isoformat())
    logger.info("re-anchored cohort #%s to birth: now ends %s",
                cohort.number, cohort.ends_at.isoformat())
    return True


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


def active_agents(session, settings: EvoSettings | None = None) -> list[EvoAgent]:
    """Active agents, ordered by id (creation order).

    The LIMIT below is only a defensive backstop for the window before
    `reconcile_population` has run: that function keeps the number of `active`
    agents equal to the cap, so in steady state this limit matches everything
    and truncates nothing.

    It must NOT be load-bearing. Truncating "lowest id first" is precisely what
    broke evolution before — children are created with the highest ids, so a
    live set defined by this limit could never contain a single offspring.
    Passing no settings (dashboard, tests, simulation) returns the true,
    uncapped population."""
    q = select(EvoAgent).where(EvoAgent.status == "active").order_by(EvoAgent.id)
    cap = settings.max_active_agents if settings is not None else 0
    if cap and cap > 0:
        q = q.limit(cap)
    return list(session.scalars(q))


def reconcile_population(session, settings: EvoSettings) -> dict[str, int]:
    """Make the number of living agents equal `effective_population_size()`.

    Suspends the excess when the population is over target (only ever after a
    cap *decrease* — reproduction replaces exactly what retirement removes, so
    steady state is already at target and this is a no-op). Returns a summary.

    Suspension, not retirement, is deliberate: a capped-out agent did not fail
    and must not get a retirement record, a graveyard entry, or a final rank —
    that would fabricate a performance story for a bot whose only problem is
    that the operator wanted a smaller fleet. `suspended` keeps it out of the
    live set and out of scoring while leaving its history intact.

    Which ones are kept: the lowest ids, i.e. exactly the agents that were
    already running under the old limit. That maximizes continuity — the bots
    holding open positions and carrying real P&L stay live, and nothing that
    has been trading gets yanked out from under its own book.

    Open positions of a suspended agent are deliberately NOT liquidated:
    settlement runs off positions, not agents, so they resolve on their own
    schedule. Force-closing them would realize arbitrary P&L for a bookkeeping
    change."""
    target = settings.effective_population_size()
    living = list(
        session.scalars(
            select(EvoAgent).where(EvoAgent.status == "active").order_by(EvoAgent.id)
        )
    )
    if len(living) <= target:
        return {"target": target, "living": len(living), "suspended": 0}

    excess = living[target:]
    for agent in excess:
        agent.status = "suspended"
        agent.status_reason = (
            f"suspended: population capped at {target} "
            f"(EVO_MAX_ACTIVE_AGENTS); not a performance retirement"
        )
    session.flush()
    audit(
        session,
        "population_reconciled",
        target=target,
        was=len(living),
        suspended=len(excess),
        suspended_uuids=[a.agent_uuid for a in excess][:50],
    )
    logger.info(
        "evo population reconciled: %d active -> %d (suspended %d)",
        len(living), target, len(excess),
    )
    return {"target": target, "living": target, "suspended": len(excess)}
