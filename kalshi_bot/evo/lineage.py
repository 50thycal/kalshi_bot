"""Family lineage (vertical) and influence graph (horizontal) — spec §6, §13.

Lineage is derived from immutable parent/founder UUIDs on evo_agents; influence
edges live in evo_influences and never affect surname or parentage."""

from __future__ import annotations

from sqlalchemy import select

from .models import EvoAgent, EvoInfluence

INFLUENCE_CONCEPTS = (
    "copied_research_method", "copied_listener", "copied_market_filter",
    "copied_data_source", "copied_risk_rule", "forked_strategy", "combined_strategies",
    "rejected_peer_idea", "improved_peer_idea",
)


def get_agent(session, agent_uuid: str) -> EvoAgent | None:
    return session.scalar(select(EvoAgent).where(EvoAgent.agent_uuid == agent_uuid))


def children_of(session, agent_uuid: str) -> list[EvoAgent]:
    return list(
        session.scalars(
            select(EvoAgent).where(EvoAgent.parent_uuid == agent_uuid).order_by(EvoAgent.id)
        )
    )


def family_members(session, surname: str) -> list[EvoAgent]:
    return list(
        session.scalars(
            select(EvoAgent).where(EvoAgent.surname == surname).order_by(EvoAgent.generation,
                                                                         EvoAgent.id)
        )
    )


def family_tree(session) -> dict:
    """Nested {founder: {agent, children: [...]}} forest over ALL agents (active and
    retired), keyed by uuid. Rendered by scripts/evo_tree.py."""
    agents = list(session.scalars(select(EvoAgent).order_by(EvoAgent.id)))
    by_uuid = {a.agent_uuid: a for a in agents}
    nodes: dict[str, dict] = {
        a.agent_uuid: {
            "uuid": a.agent_uuid,
            "name": a.historical_name,
            "surname": a.surname,
            "generation": a.generation,
            "status": a.status,
            "origin": a.origin,
            "children": [],
        }
        for a in agents
    }
    roots: list[dict] = []
    for a in agents:
        node = nodes[a.agent_uuid]
        if a.parent_uuid and a.parent_uuid in nodes:
            nodes[a.parent_uuid]["children"].append(node)
        else:
            roots.append(node)
    return {"roots": roots, "count": len(agents), "families": len({a.surname for a in by_uuid.values()})}


def record_influence(
    session,
    *,
    source_uuid: str,
    receiving_uuid: str,
    concept: str,
    description: str | None = None,
    evidence: dict | None = None,
    interpretation: str | None = None,
    modifications: str | None = None,
    result: str = "pending",
    heartbeat_id: int | None = None,
) -> tuple[EvoInfluence | None, str | None]:
    if concept not in INFLUENCE_CONCEPTS:
        return None, f"unknown influence concept {concept!r} (valid: {INFLUENCE_CONCEPTS})"
    if source_uuid == receiving_uuid:
        return None, "cannot record self-influence"
    src, dst = get_agent(session, source_uuid), get_agent(session, receiving_uuid)
    if src is None or dst is None:
        return None, "unknown source or receiving agent"
    row = EvoInfluence(
        source_uuid=source_uuid,
        receiving_uuid=receiving_uuid,
        source_family=src.surname,
        receiving_family=dst.surname,
        concept=concept,
        description=description,
        evidence_json=evidence,
        interpretation=interpretation,
        modifications=modifications,
        result=result,
        heartbeat_id=heartbeat_id,
    )
    session.add(row)
    session.flush()
    return row, None


def influence_graph(session, *, limit: int = 500) -> dict:
    edges = list(
        session.scalars(select(EvoInfluence).order_by(EvoInfluence.id.desc()).limit(limit))
    )
    return {
        "edges": [
            {
                "source": e.source_uuid,
                "receiver": e.receiving_uuid,
                "source_family": e.source_family,
                "receiving_family": e.receiving_family,
                "concept": e.concept,
                "result": e.result,
                "at": e.created_at.isoformat(),
            }
            for e in edges
        ],
        "cross_family": sum(1 for e in edges if e.source_family != e.receiving_family),
        "count": len(edges),
    }
