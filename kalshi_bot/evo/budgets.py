"""Per-agent per-cohort resource budgets + spend tracking (spec §11).

Equal allocations for every agent at cohort join; hard pre-spend checks; daily
material-revision allowance tracked as a dated resource key so it self-resets.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from .audit import audit
from .config import EvoSettings, resource_allocations
from .models import EvoBudget

REVISIONS_PREFIX = "material_revisions_day:"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_budgets(session, agent_uuid: str, cohort_id: int, settings: EvoSettings) -> None:
    """Create the standard equal allocation rows for (agent, cohort). Idempotent."""
    existing = {
        b.resource
        for b in session.scalars(
            select(EvoBudget).where(
                EvoBudget.agent_uuid == agent_uuid, EvoBudget.cohort_id == cohort_id
            )
        )
    }
    for resource, allocated in resource_allocations(settings).items():
        if resource not in existing:
            session.add(
                EvoBudget(
                    agent_uuid=agent_uuid,
                    cohort_id=cohort_id,
                    resource=resource,
                    allocated=allocated,
                    used=0,
                )
            )
    session.flush()


def _get(session, agent_uuid: str, cohort_id: int, resource: str) -> EvoBudget | None:
    return session.scalar(
        select(EvoBudget).where(
            EvoBudget.agent_uuid == agent_uuid,
            EvoBudget.cohort_id == cohort_id,
            EvoBudget.resource == resource,
        )
    )


def remaining(session, agent_uuid: str, cohort_id: int, resource: str) -> float:
    b = _get(session, agent_uuid, cohort_id, resource)
    if b is None:
        return 0.0
    return max(0.0, float(b.allocated) - float(b.used))


def can_spend(session, agent_uuid: str, cohort_id: int, resource: str, amount: float) -> bool:
    return remaining(session, agent_uuid, cohort_id, resource) >= amount


def spend(
    session, agent_uuid: str, cohort_id: int, resource: str, amount: float, *, force: bool = False
) -> bool:
    """Record spend. Returns False (and spends nothing) if the budget is missing or
    would overrun, unless force=True (used to book actual costs that already
    happened, e.g. an LLM call that returned more tokens than projected)."""
    b = _get(session, agent_uuid, cohort_id, resource)
    if b is None:
        return False
    if not force and float(b.used) + amount > float(b.allocated) + 1e-9:
        return False
    b.used = float(b.used) + amount
    b.updated_at = _now()
    session.flush()
    return True


def budget_summary(session, agent_uuid: str, cohort_id: int) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for b in session.scalars(
        select(EvoBudget).where(
            EvoBudget.agent_uuid == agent_uuid, EvoBudget.cohort_id == cohort_id
        )
    ):
        out[b.resource] = {
            "allocated": float(b.allocated),
            "used": round(float(b.used), 6),
            "remaining": round(max(0.0, float(b.allocated) - float(b.used)), 6),
        }
    return out


# --- daily material-revision allowance (spec §14: max 2/day, cooldown) -----


def _revision_resource(day: str) -> str:
    return f"{REVISIONS_PREFIX}{day}"


def try_use_material_revision(
    session, agent_uuid: str, cohort_id: int, settings: EvoSettings, *, now: datetime | None = None
) -> tuple[bool, str | None]:
    """Consume one of today's material-revision slots, enforcing the daily cap and
    the cooldown since the previous material revision."""
    now = now or _now()
    day = now.date().isoformat()
    resource = _revision_resource(day)
    b = _get(session, agent_uuid, cohort_id, resource)
    if b is None:
        b = EvoBudget(
            agent_uuid=agent_uuid,
            cohort_id=cohort_id,
            resource=resource,
            allocated=float(settings.material_revisions_per_day),
            used=0,
        )
        session.add(b)
        session.flush()
    if float(b.used) >= float(b.allocated):
        return False, "daily material-revision allowance exhausted"
    updated = b.updated_at if b.updated_at.tzinfo else b.updated_at.replace(tzinfo=timezone.utc)
    if float(b.used) > 0:
        minutes_since = (now - updated).total_seconds() / 60.0
        if minutes_since < settings.revision_cooldown_minutes:
            return False, (
                f"revision cooldown: {settings.revision_cooldown_minutes - minutes_since:.0f}"
                " minutes remaining"
            )
    b.used = float(b.used) + 1
    b.updated_at = now
    session.flush()
    audit(session, "material_revision_slot_used", agent_uuid=agent_uuid, day=day,
          used=float(b.used), allowed=float(b.allocated))
    return True, None
