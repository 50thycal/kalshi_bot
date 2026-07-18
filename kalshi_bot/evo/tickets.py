"""Capability-request queue (spec §16): submission with semantic dedup (a near-
duplicate converts the submitter into a supporter of the existing ticket),
unique supporter rows, and the human-review summary used by the evo digest."""

from __future__ import annotations

import re

from sqlalchemy import select

from .audit import audit
from .models import EvoAgent, EvoTicket, EvoTicketSupporter

CATEGORIES = (
    "external_data_pipeline", "paid_data_source", "api_credentials",
    "shared_code_capability", "sandbox_operator", "database_schema",
    "stored_information", "data_collection", "platform_deployment",
    "infrastructure", "dashboard", "research_tooling", "performance",
    "bug_report", "security", "permissions", "other",
)

_STOPWORDS = frozenset(
    "a an the for to of in on with and or we i need want would like please new add".split()
)
DEDUP_JACCARD = 0.6


def _signature(category: str, capability: str) -> tuple[str, frozenset[str]]:
    tokens = frozenset(
        t for t in re.findall(r"[a-z0-9]+", capability.lower()) if t not in _STOPWORDS
    )
    return f"{category}:{' '.join(sorted(tokens))[:180]}", tokens


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_duplicate(session, category: str, capability: str) -> EvoTicket | None:
    _, tokens = _signature(category, capability)
    candidates = list(
        session.scalars(
            select(EvoTicket).where(
                EvoTicket.category == category,
                EvoTicket.status.in_(("open", "in_review", "approved")),
            )
        )
    )
    best, best_score = None, 0.0
    for t in candidates:
        _, t_tokens = _signature(t.category, t.capability)
        score = _jaccard(tokens, t_tokens)
        if score > best_score:
            best, best_score = t, score
    return best if best_score >= DEDUP_JACCARD else None


def support_ticket(
    session, agent_uuid: str, ticket_id: int, *, note: str | None = None
) -> tuple[EvoTicketSupporter | None, str | None]:
    ticket = session.get(EvoTicket, ticket_id)
    if ticket is None:
        return None, f"ticket {ticket_id} not found"
    existing = session.scalar(
        select(EvoTicketSupporter).where(
            EvoTicketSupporter.ticket_id == ticket_id,
            EvoTicketSupporter.agent_uuid == agent_uuid,
        )
    )
    if existing is not None:
        return existing, None
    agent = session.scalar(select(EvoAgent).where(EvoAgent.agent_uuid == agent_uuid))
    row = EvoTicketSupporter(
        ticket_id=ticket_id,
        agent_uuid=agent_uuid,
        family=agent.surname if agent else None,
        note=note,
    )
    session.add(row)
    session.flush()
    return row, None


def submit_ticket(
    session,
    *,
    agent_uuid: str,
    category: str,
    capability: str,
    problem: str | None = None,
    expected_strategy_benefit: str | None = None,
    expected_portfolio_benefit: str | None = None,
    existing_workaround: str | None = None,
    required: dict | None = None,
    expected_cost: str | None = None,
    expected_effort: str | None = None,
    urgency: str = "normal",
    evidence: dict | None = None,
    related: dict | None = None,
) -> tuple[EvoTicket | None, str | None, bool]:
    """Returns (ticket, error, deduplicated). A semantic duplicate returns the
    EXISTING ticket with deduplicated=True and records the agent as a supporter."""
    if category not in CATEGORIES:
        return None, f"unknown category {category!r} (valid: {CATEGORIES})", False
    if not capability or len(capability.strip()) < 10:
        return None, "capability description too short", False
    if urgency not in ("low", "normal", "high"):
        urgency = "normal"
    dup = find_duplicate(session, category, capability)
    if dup is not None:
        support_ticket(session, agent_uuid, dup.id, note=f"dedup of: {capability[:120]}")
        audit(session, "ticket_deduplicated", agent_uuid=agent_uuid, ticket_id=dup.id)
        return dup, None, True
    agent = session.scalar(select(EvoAgent).where(EvoAgent.agent_uuid == agent_uuid))
    sig, _ = _signature(category, capability)
    row = EvoTicket(
        requesting_uuid=agent_uuid,
        requesting_family=agent.surname if agent else None,
        category=category,
        capability=capability[:200],
        problem=problem,
        expected_strategy_benefit=expected_strategy_benefit,
        expected_portfolio_benefit=expected_portfolio_benefit,
        existing_workaround=existing_workaround,
        required_json=required,
        expected_cost=expected_cost,
        expected_effort=expected_effort,
        urgency=urgency,
        evidence_json=evidence,
        related_json=related,
        norm_signature=sig[:200],
    )
    session.add(row)
    session.flush()
    audit(session, "ticket_submitted", agent_uuid=agent_uuid, ticket_id=row.id,
          category=category)
    return row, None, False


def review_queue(session) -> list[dict]:
    """Human-review ordering: supporters desc, then urgency, then age (spec §16)."""
    from sqlalchemy import func

    rows = session.execute(
        select(EvoTicket, func.count(EvoTicketSupporter.id),
               func.count(func.distinct(EvoTicketSupporter.family)))
        .outerjoin(EvoTicketSupporter, EvoTicketSupporter.ticket_id == EvoTicket.id)
        .where(EvoTicket.status.in_(("open", "in_review")))
        .group_by(EvoTicket.id)
    ).all()
    urgency_rank = {"high": 0, "normal": 1, "low": 2}
    out = [
        {
            "id": t.id,
            "category": t.category,
            "capability": t.capability,
            "problem": (t.problem or "")[:300],
            "supporters": int(n),
            "supporting_families": int(fams),
            "expected_strategy_benefit": (t.expected_strategy_benefit or "")[:200],
            "expected_cost": t.expected_cost,
            "urgency": t.urgency,
            "status": t.status,
            "created_at": t.created_at.isoformat(),
        }
        for t, n, fams in rows
    ]
    out.sort(key=lambda r: (-r["supporters"], urgency_rank.get(r["urgency"], 1), r["id"]))
    return out
