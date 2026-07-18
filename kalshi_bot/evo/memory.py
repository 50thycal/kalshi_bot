"""Agent memory: immutable facts, supersession-chained beliefs, episodes,
experiments, decision journals, and bounded task-scoped retrieval (spec §8).

Mutability is enforced HERE, not by the caller: facts/journals are written with
immutable=True and there is no update function for them; belief revision inserts a
new row and flips only the `superseded` flag on the old one (its content is never
touched). Experiment hypotheses are write-once at registration; conclusions are
write-once on the same row (API refuses a second conclusion).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from .audit import audit, integrity_violation
from .models import EvoExperiment, EvoMemory

logger = logging.getLogger(__name__)

VALID_KINDS = ("fact", "belief", "episode", "journal")
IMMUTABLE_KINDS = ("fact", "journal")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def record_fact(
    session,
    agent_uuid: str,
    title: str,
    body: dict,
    *,
    idem_key: str | None = None,
    importance: float = 0.5,
    tag1: str | None = None,
    tag2: str | None = None,
    heartbeat_id: int | None = None,
) -> EvoMemory:
    """Immutable factual record (system-written). Idempotent on (agent, idem_key)."""
    if idem_key:
        existing = session.scalar(
            select(EvoMemory).where(
                EvoMemory.agent_uuid == agent_uuid, EvoMemory.idem_key == idem_key
            )
        )
        if existing is not None:
            return existing
    row = EvoMemory(
        agent_uuid=agent_uuid,
        kind="fact",
        immutable=True,
        idem_key=idem_key,
        title=title[:200],
        body_json=body,
        importance=max(0.0, min(1.0, importance)),
        tag1=tag1,
        tag2=tag2,
        heartbeat_id=heartbeat_id,
    )
    session.add(row)
    session.flush()
    return row


def write_journal(
    session,
    agent_uuid: str,
    heartbeat_id: int,
    journal: dict,
    *,
    tag1: str | None = None,
) -> EvoMemory:
    """The structured per-heartbeat decision record (spec §8.5). Immutable."""
    row = EvoMemory(
        agent_uuid=agent_uuid,
        kind="journal",
        immutable=True,
        idem_key=f"journal:{heartbeat_id}",
        title=str(journal.get("decision", "decision journal"))[:200],
        body_json=journal,
        importance=0.6,
        tag1=tag1,
        heartbeat_id=heartbeat_id,
    )
    session.add(row)
    session.flush()
    return row


def record_episode(
    session,
    agent_uuid: str,
    title: str,
    body: dict,
    *,
    importance: float = 0.7,
    tag1: str | None = None,
    tag2: str | None = None,
    heartbeat_id: int | None = None,
) -> EvoMemory:
    row = EvoMemory(
        agent_uuid=agent_uuid,
        kind="episode",
        immutable=False,
        title=title[:200],
        body_json=body,
        importance=max(0.0, min(1.0, importance)),
        tag1=tag1,
        tag2=tag2,
        heartbeat_id=heartbeat_id,
    )
    session.add(row)
    session.flush()
    return row


def revise_belief(
    session,
    agent_uuid: str,
    *,
    title: str,
    new_belief: str,
    evidence_for: str,
    evidence_against: str | None,
    confidence_after: float,
    heartbeat_id: int | None,
    supersedes_id: int | None = None,
    tag1: str | None = None,
    tag2: str | None = None,
) -> tuple[EvoMemory | None, str | None]:
    """Belief revision by supersession (spec §8.2). The old row keeps its content;
    only its `superseded` flag flips. Rejects supersession of another agent's row or
    a non-belief row (audited as integrity if cross-agent)."""
    prev: EvoMemory | None = None
    confidence_before: float | None = None
    previous_text: str | None = None
    if supersedes_id is not None:
        prev = session.get(EvoMemory, supersedes_id)
        if prev is None:
            return None, f"belief {supersedes_id} not found"
        if prev.agent_uuid != agent_uuid:
            integrity_violation(
                session, agent_uuid, "cross_agent_belief_revision",
                heartbeat_id=heartbeat_id, target_row=supersedes_id,
            )
            return None, "cannot revise another agent's belief"
        if prev.kind != "belief":
            return None, f"row {supersedes_id} is {prev.kind}, not a belief"
        if prev.superseded:
            return None, f"belief {supersedes_id} already superseded"
        confidence_before = prev.confidence
        previous_text = str(prev.body_json.get("belief", ""))[:2000]
    row = EvoMemory(
        agent_uuid=agent_uuid,
        kind="belief",
        immutable=False,
        title=title[:200],
        body_json={
            "belief": new_belief[:4000],
            "previous_belief": previous_text,
            "evidence_for": (evidence_for or "")[:2000],
            "evidence_against": (evidence_against or "")[:2000],
            "confidence_before": confidence_before,
            "confidence_after": confidence_after,
            "revised_at": _now().isoformat(),
        },
        importance=0.6,
        confidence=max(0.0, min(1.0, confidence_after)),
        tag1=tag1,
        tag2=tag2,
        supersedes_id=supersedes_id,
        heartbeat_id=heartbeat_id,
    )
    session.add(row)
    if prev is not None:
        prev.superseded = True  # flag only; content untouched
    session.flush()
    return row, None


def register_experiment(
    session,
    agent_uuid: str,
    *,
    hypothesis: str,
    heartbeat_id: int | None = None,
    idem_key: str | None = None,
    **fields: Any,
) -> EvoExperiment:
    """Pre-registered experiment (spec §8.4). Hypothesis is write-once."""
    if idem_key:
        existing = session.scalar(
            select(EvoExperiment).where(
                EvoExperiment.agent_uuid == agent_uuid, EvoExperiment.idem_key == idem_key
            )
        )
        if existing is not None:
            return existing
    allowed = {
        "reason", "expected_result", "falsifiable_prediction", "dataset", "provenance",
        "date_range", "no_lookahead_note", "strategy_revision", "cognitive_revision",
        "parameters_json", "promotion_criteria", "kill_criteria",
    }
    row = EvoExperiment(
        agent_uuid=agent_uuid,
        idem_key=idem_key,
        hypothesis=hypothesis[:4000],
        heartbeat_id=heartbeat_id,
        **{k: v for k, v in fields.items() if k in allowed},
    )
    session.add(row)
    session.flush()
    return row


def conclude_experiment(
    session,
    agent_uuid: str,
    experiment_id: int,
    *,
    result: dict,
    confidence: float,
    interpretation: str,
    follow_up: str | None = None,
    heartbeat_id: int | None = None,
) -> tuple[EvoExperiment | None, str | None]:
    """Write-once conclusion. The original hypothesis block is never touched; a
    changed hypothesis must be a new experiment."""
    row = session.get(EvoExperiment, experiment_id)
    if row is None:
        return None, f"experiment {experiment_id} not found"
    if row.agent_uuid != agent_uuid:
        integrity_violation(
            session, agent_uuid, "cross_agent_experiment_conclusion",
            heartbeat_id=heartbeat_id, target_row=experiment_id,
        )
        return None, "cannot conclude another agent's experiment"
    if row.status != "open":
        return None, f"experiment {experiment_id} already {row.status}"
    row.status = "concluded"
    row.result_json = result
    row.result_confidence = max(0.0, min(1.0, confidence))
    row.interpretation = interpretation[:4000]
    row.follow_up = (follow_up or None)
    row.concluded_at = _now()
    session.flush()
    audit(session, "experiment_concluded", agent_uuid=agent_uuid,
          heartbeat_id=heartbeat_id, experiment_id=experiment_id)
    return row, None


# ---------------------------------------------------------------------------
# Retrieval (bounded, task-scoped — spec §8.7)
# ---------------------------------------------------------------------------


def retrieve(
    session,
    agent_uuid: str,
    *,
    kinds: tuple[str, ...] = ("belief", "episode", "journal"),
    tags: tuple[str, ...] = (),
    limit: int = 20,
    include_superseded: bool = False,
    include_inherited: bool = True,
) -> list[EvoMemory]:
    """Scored retrieval: recency + importance + tag match (+ contradiction bonus),
    never the whole lifetime. Returns newest-first within score order."""
    q = select(EvoMemory).where(
        EvoMemory.agent_uuid == agent_uuid, EvoMemory.kind.in_(kinds)
    )
    if not include_superseded:
        q = q.where(EvoMemory.superseded.is_(False))
    if not include_inherited:
        q = q.where(EvoMemory.inherited_from_uuid.is_(None))
    # Over-fetch by recency, then score in Python (portable across sqlite/postgres).
    rows = list(session.scalars(q.order_by(EvoMemory.created_at.desc()).limit(limit * 5)))
    now = _now()

    def score(m: EvoMemory) -> float:
        created = m.created_at if m.created_at.tzinfo else m.created_at.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (now - created).total_seconds() / 86400.0)
        recency = 1.0 / (1.0 + age_days / 7.0)
        tag_bonus = 0.5 if tags and (m.tag1 in tags or m.tag2 in tags) else 0.0
        contradiction_bonus = 0.2 if m.contradicts_id is not None else 0.0
        inherit_bonus = 0.1 if m.inherited_from_uuid else 0.0
        return m.importance + recency + tag_bonus + contradiction_bonus + inherit_bonus

    rows.sort(key=score, reverse=True)
    return rows[:limit]


def summarize_for_prompt(rows: list[EvoMemory], *, max_chars: int = 6000) -> str:
    """Compact text rendering of retrieved memories for the heartbeat prompt."""
    out: list[str] = []
    used = 0
    for m in rows:
        body = m.body_json or {}
        if m.kind == "belief":
            text = f"[belief c={m.confidence:.2f}] {m.title}: {body.get('belief', '')}"
        elif m.kind == "journal":
            text = f"[journal] {m.title}"
        else:
            text = f"[{m.kind}] {m.title}: {str(body)[:200]}"
        text = text[:400]
        if used + len(text) > max_chars:
            break
        out.append(text)
        used += len(text)
    return "\n".join(out)


def open_experiments(session, agent_uuid: str, limit: int = 10) -> list[EvoExperiment]:
    return list(
        session.scalars(
            select(EvoExperiment)
            .where(EvoExperiment.agent_uuid == agent_uuid, EvoExperiment.status == "open")
            .order_by(EvoExperiment.created_at.desc())
            .limit(limit)
        )
    )
