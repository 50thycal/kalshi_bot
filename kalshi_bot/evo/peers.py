"""Peer observation views + the 6-hour-delayed leaderboard (spec §12).

Everything here is READ-ONLY summaries of other agents; the real-time fitness
table never crosses this API — only rows whose visible_after has passed are
served. There is no write path to another agent anywhere in the evo package."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from .genomes import current_genome
from .models import (
    EvoAgent,
    EvoFitness,
    EvoGenome,
    EvoInfluence,
    EvoListener,
    EvoRetirement,
    EvoStrategy,
    EvoTicket,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def roster(session, *, status: str | None = "active") -> list[dict]:
    q = select(EvoAgent).order_by(EvoAgent.id)
    if status:
        q = q.where(EvoAgent.status == status)
    return [
        {
            "uuid": a.agent_uuid,
            "name": a.display_name,
            "family": a.surname,
            "generation": a.generation,
            "origin": a.origin,
            "status": a.status,
        }
        for a in session.scalars(q)
    ]


def peer_summary(session, agent_uuid: str) -> dict | None:
    """High-level view of one peer: thesis, family, universe, recent revision
    metadata, listener categories — never private working state or beliefs."""
    agent = session.scalar(select(EvoAgent).where(EvoAgent.agent_uuid == agent_uuid))
    if agent is None:
        return None
    trading = current_genome(session, agent_uuid, "trading")
    doc = (trading.document_json if trading else {}) or {}
    revisions = list(
        session.scalars(
            select(EvoGenome)
            .where(EvoGenome.agent_uuid == agent_uuid)
            .order_by(EvoGenome.created_at.desc())
            .limit(5)
        )
    )
    listeners = list(
        session.scalars(
            select(EvoListener).where(
                EvoListener.owner_uuid == agent_uuid, EvoListener.status == "active"
            )
        )
    )
    strategies = list(
        session.scalars(
            select(EvoStrategy).where(
                EvoStrategy.agent_uuid == agent_uuid, EvoStrategy.status == "active"
            )
        )
    )
    retirement = session.scalar(
        select(EvoRetirement).where(EvoRetirement.agent_uuid == agent_uuid)
    )
    return {
        "uuid": agent.agent_uuid,
        "name": agent.display_name,
        "family": agent.surname,
        "generation": agent.generation,
        "status": agent.status,
        "strategy_family": doc.get("strategy_family"),
        "thesis": doc.get("thesis"),
        "markets": (doc.get("universe") or {}).get("series_prefixes", []),
        "data_dependencies": doc.get("data_dependencies", []),
        "active_strategies": [s.name for s in strategies],
        "recent_revisions": [
            {
                "kind": g.kind,
                "revision": g.revision,
                "at": g.created_at.isoformat(),
                "changed_fields": (g.changed_fields_json or [])[:10],
                "hypothesis": (g.hypothesis or "")[:200],
                "rollback": g.is_rollback,
            }
            for g in revisions
        ],
        "listener_categories": sorted(
            {(li.condition_json or {}).get("ticker", "portfolio") and li.name.split("_")[0]
             for li in listeners} - {None}
        )[:10],
        "listener_count": len(listeners),
        "retirement_reason": retirement.reason if retirement else None,
    }


def delayed_leaderboard(session, cohort_id: int, *, now: datetime | None = None) -> list[dict]:
    """Fitness rows past their visible_after gate ONLY (6h delay). Peer-facing."""
    now = now or _now()
    rows = list(
        session.scalars(
            select(EvoFitness)
            .where(EvoFitness.cohort_id == cohort_id, EvoFitness.visible_after <= now)
            .order_by(EvoFitness.computed_at.desc())
        )
    )
    newest: dict[str, EvoFitness] = {}
    for r in rows:
        newest.setdefault(r.agent_uuid, r)
    board = [
        {
            "agent_uuid": r.agent_uuid,
            "cohort_score": round(r.cohort_score, 2),
            "selection_score": round(r.selection_score, 2),
            "rank": r.rank,
            "as_of": r.computed_at.isoformat(),
        }
        for r in newest.values()
    ]
    board.sort(key=lambda r: -(r["selection_score"]))
    for i, row in enumerate(board, start=1):
        row["delayed_rank"] = i
    return board


def historical_scores(session, agent_uuid: str) -> list[dict]:
    rows = list(
        session.scalars(
            select(EvoFitness)
            .where(EvoFitness.agent_uuid == agent_uuid, EvoFitness.kind == "final")
            .order_by(EvoFitness.cohort_id)
        )
    )
    return [
        {"cohort_id": r.cohort_id, "cohort_score": r.cohort_score,
         "selection_score": r.selection_score, "rank": r.rank}
        for r in rows
    ]


def open_tickets_summary(session, limit: int = 20) -> list[dict]:
    from sqlalchemy import func

    from .models import EvoTicketSupporter

    rows = session.execute(
        select(EvoTicket, func.count(EvoTicketSupporter.id))
        .outerjoin(EvoTicketSupporter, EvoTicketSupporter.ticket_id == EvoTicket.id)
        .where(EvoTicket.status.in_(("open", "in_review")))
        .group_by(EvoTicket.id)
        .order_by(EvoTicket.id.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": t.id,
            "category": t.category,
            "capability": t.capability[:120],
            "supporters": int(n),
            "status": t.status,
            "age_days": round((_now() - (
                t.created_at if t.created_at.tzinfo else t.created_at.replace(
                    tzinfo=timezone.utc)
            )).total_seconds() / 86400, 1),
        }
        for t, n in rows
    ]


def influences_involving(session, agent_uuid: str, limit: int = 10) -> list[dict]:
    rows = list(
        session.scalars(
            select(EvoInfluence)
            .where(
                (EvoInfluence.source_uuid == agent_uuid)
                | (EvoInfluence.receiving_uuid == agent_uuid)
            )
            .order_by(EvoInfluence.id.desc())
            .limit(limit)
        )
    )
    return [
        {"source": r.source_uuid, "receiver": r.receiving_uuid, "concept": r.concept,
         "result": r.result}
        for r in rows
    ]
