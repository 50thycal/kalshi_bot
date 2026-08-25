"""Scoped retrieval over the population's own history and the project's research.

The constraint that shapes this module is that a candidate must *not* be able to
ingest everything. A retrieval interface that returns all history returns mostly
irrelevant history, and a proposer conditioned on it learns the average of the corpus
rather than anything about its own lineage. So every entry point takes a scope —
strategy family, market family, gene dimension, failure mode, or lineage — and returns
a bounded slice.

Two sources, kept apart because they have different standing:

* **Population evidence** — this program's own runs, decisions, refused proposals and
  journal entries. First-hand, and the only thing that carries a genome hash.
* **Project research** — the durable thesis docs and research journal the operator and
  the LLM organism already maintain, read through `evo.knowledge`. Second-hand
  context, never evidence about a genome.

Nothing here writes. Nothing here reads Experiment OS state: an experiment's standing,
gate verdict or exposure is XOS's answer to give, and a copy retrieved here would be
stale the day after it was written.
"""

from __future__ import annotations

from sqlalchemy import select

from .. import knowledge as project_knowledge
from . import genome as genome_mod
from .models import (
    EvoCandidate,
    EvoDecision,
    EvoGenomeVersion,
    EvoJournalEntry,
    EvoMutationProposal,
    EvoRun,
)

DEFAULT_LIMIT = 12


def lineage(session, *, candidate: EvoCandidate, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """A candidate's ancestors, nearest first.

    This is the highest-value scope and the cheapest: a child's parent already paid for
    the evidence about the region of the space the child was born into."""
    ancestors = list((candidate.lineage_json or {}).get("ancestors") or [])
    if not ancestors:
        return []
    rows = {
        c.uuid: c
        for c in session.execute(
            select(EvoCandidate).where(EvoCandidate.uuid.in_(ancestors))
        ).scalars()
    }
    out = []
    for uuid in reversed(ancestors[-limit:]):
        parent = rows.get(uuid)
        if parent is None:
            continue
        out.append(
            {
                "uuid": parent.uuid,
                "label": parent.label,
                "family": parent.family,
                "state": parent.state,
                "purpose": parent.purpose,
                "retirement_reason": parent.retirement_reason,
                "birth_generation": parent.birth_generation,
            }
        )
    return out


def prior_results(
    session,
    *,
    program_id: int,
    family: str | None = None,
    candidate_uuid: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    """Completed runs, newest first, scoped by strategy family or candidate."""
    stmt = select(EvoRun).where(
        EvoRun.program_id == program_id, EvoRun.status == "completed"
    )
    if candidate_uuid:
        stmt = stmt.where(EvoRun.candidate_uuid == candidate_uuid)
    if family:
        genome_ids = select(EvoGenomeVersion.id).where(
            EvoGenomeVersion.program_id == program_id,
            EvoGenomeVersion.family == family,
        )
        stmt = stmt.where(EvoRun.genome_id.in_(genome_ids))
    rows = session.execute(stmt.order_by(EvoRun.id.desc()).limit(limit)).scalars()
    return [
        {
            "run_id": r.id,
            "generation": r.generation_number,
            "candidate_uuid": r.candidate_uuid,
            "genome_hash": r.genome_hash,
            "window": [r.window_start, r.window_end],
            "n_trades": (r.outcome_json or {}).get("n_trades"),
            "net_pnl_usd": (r.outcome_json or {}).get("net_pnl_usd"),
            "per_trade_cents_per_contract": (r.outcome_json or {}).get(
                "per_trade_cents_per_contract"
            ),
            "realizable_cents_per_contract": (r.outcome_json or {}).get(
                "realizable_cents_per_contract"
            ),
            "max_drawdown_usd": (r.outcome_json or {}).get("max_drawdown_usd"),
        }
        for r in rows
    ]


def similar_genomes(
    session,
    *,
    program_id: int,
    document: dict,
    max_distance: float = 0.15,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    """Genomes near this one in the search space, nearest first.

    Scoped by *genome dimension* rather than by name: "what happened to strategies like
    this one" is the question a proposer needs answered before repeating an experiment
    the population has already run."""
    rows = list(
        session.execute(
            select(EvoGenomeVersion).where(
                EvoGenomeVersion.program_id == program_id,
                EvoGenomeVersion.evaluated.is_(True),
            )
        ).scalars()
    )
    scored = []
    for row in rows:
        doc = row.document_json or {}
        d = genome_mod.distance(document, doc)
        if d <= max_distance:
            scored.append((d, row))
    scored.sort(key=lambda pair: pair[0])
    return [
        {
            "distance": round(d, 4),
            "genome_hash": row.genome_hash,
            "candidate_uuid": row.candidate_uuid,
            "family": row.family,
            "summary": genome_mod.describe(row.document_json or {}),
            "hypothesis": row.hypothesis,
            "mutation_diff": row.mutation_diff_json,
        }
        for d, row in scored[:limit]
    ]


def refused_mutations(
    session,
    *,
    program_id: int,
    parent_candidate_uuid: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    """Proposals the gates refused, and why.

    Kept retrievable so the same invalid mutation is not reproposed forever — a
    rejection is a fact about the search space, not an error to discard."""
    stmt = select(EvoMutationProposal).where(
        EvoMutationProposal.program_id == program_id,
        EvoMutationProposal.status == "rejected",
    )
    if parent_candidate_uuid:
        stmt = stmt.where(
            EvoMutationProposal.parent_candidate_uuid == parent_candidate_uuid
        )
    rows = session.execute(
        stmt.order_by(EvoMutationProposal.id.desc()).limit(limit)
    ).scalars()
    return [
        {
            "proposal_id": r.id,
            "generation": r.generation_number,
            "changes": r.changes_json,
            "stage": r.reject_stage,
            "reason": r.reject_reason,
        }
        for r in rows
    ]


def failure_modes(
    session, *, program_id: int, limit: int = DEFAULT_LIMIT
) -> list[dict]:
    """Why candidates were retired or escalated in this program."""
    rows = session.execute(
        select(EvoDecision)
        .where(
            EvoDecision.program_id == program_id,
            EvoDecision.decision.in_(("retire", "escalate")),
        )
        .order_by(EvoDecision.id.desc())
        .limit(limit)
    ).scalars()
    return [
        {
            "candidate_uuid": r.candidate_uuid,
            "generation": r.generation_number,
            "decision": r.decision,
            "fitness": r.fitness,
            "evidence_class": r.evidence_class,
            "reason": r.reason,
        }
        for r in rows
    ]


def journal(
    session,
    *,
    candidate_uuid: str,
    kinds: tuple[str, ...] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    """A candidate's own memory, optionally filtered to particular registers."""
    stmt = select(EvoJournalEntry).where(
        EvoJournalEntry.candidate_uuid == candidate_uuid,
        EvoJournalEntry.superseded_by.is_(None),
    )
    if kinds:
        stmt = stmt.where(EvoJournalEntry.kind.in_(kinds))
    rows = session.execute(stmt.order_by(EvoJournalEntry.id.desc()).limit(limit)).scalars()
    return [
        {
            "id": r.id,
            "kind": r.kind,
            "topic": r.topic,
            "body": r.body,
            "generation": r.generation_number,
            "inherited_from": r.inherited_from,
            "heritable": r.heritable,
        }
        for r in rows
    ]


def research_docs(names: tuple[str, ...] | None = None) -> list[dict]:
    """The project's durable research library, as an index.

    Delegates to `evo.knowledge`, which is already the fleet's read path for thesis
    docs and the research journal — a second index would be a second answer to what the
    library contains."""
    index = project_knowledge.doc_index()
    if names:
        wanted = {n.lower() for n in names}
        index = [d for d in index if str(d.get("name", "")).lower() in wanted]
    return index


def read_research(name: str, *, chunk: int = 0) -> tuple[dict | None, str | None]:
    """Read one research document by name."""
    return project_knowledge.read_doc(name, chunk=chunk)


def context_for(
    session,
    *,
    program_id: int,
    candidate: EvoCandidate,
    document: dict,
    limit: int = 6,
) -> dict:
    """The bounded bundle a proposer gets: lineage, near neighbours, this candidate's own
    results, what it has already been refused, and how this program has been failing.

    Bounded on purpose. This is the whole retrieval budget, and every entry in it is
    scoped to *this* candidate's region of the space."""
    return {
        "lineage": lineage(session, candidate=candidate, limit=limit),
        "similar_genomes": similar_genomes(
            session, program_id=program_id, document=document, limit=limit
        ),
        "own_results": prior_results(
            session, program_id=program_id, candidate_uuid=candidate.uuid, limit=limit
        ),
        "refused_mutations": refused_mutations(
            session, program_id=program_id, parent_candidate_uuid=candidate.uuid,
            limit=limit,
        ),
        "failure_modes": failure_modes(session, program_id=program_id, limit=limit),
        "lessons": journal(
            session, candidate_uuid=candidate.uuid,
            kinds=("lesson", "failure_mode"), limit=limit,
        ),
    }


__all__ = [
    "DEFAULT_LIMIT",
    "context_for",
    "failure_modes",
    "journal",
    "lineage",
    "prior_results",
    "read_research",
    "refused_mutations",
    "research_docs",
    "similar_genomes",
]
