"""Strategy graveyard interface (spec §31): search dead ideas, require documented
material differences before reviving, and accrete entries from retired agents and
killed experiments."""

from __future__ import annotations

from sqlalchemy import or_, select

from .models import EvoGraveyardEntry


def search(
    session,
    *,
    query: str | None = None,
    strategy_family: str | None = None,
    limit: int = 10,
) -> list[EvoGraveyardEntry]:
    q = select(EvoGraveyardEntry)
    if strategy_family:
        q = q.where(EvoGraveyardEntry.strategy_family == strategy_family)
    if query:
        like = f"%{query}%"
        q = q.where(
            or_(
                EvoGraveyardEntry.title.ilike(like),
                EvoGraveyardEntry.summary.ilike(like),
                EvoGraveyardEntry.why_failed.ilike(like),
                EvoGraveyardEntry.slug.ilike(like),
            )
        )
    return list(session.scalars(q.order_by(EvoGraveyardEntry.id).limit(limit)))


def conflicts_for_strategy(
    session, *, strategy_family: str, keywords: list[str]
) -> list[EvoGraveyardEntry]:
    """Entries a new strategy must confront: same family plus keyword hits."""
    hits: dict[int, EvoGraveyardEntry] = {}
    for row in search(session, strategy_family=strategy_family, limit=10):
        hits[row.id] = row
    for kw in keywords[:6]:
        for row in search(session, query=kw, limit=5):
            hits[row.id] = row
    return list(hits.values())


def add_entry(
    session,
    *,
    slug: str,
    title: str,
    summary: str,
    outcome: str,
    source: str,
    source_ref: str | None = None,
    strategy_family: str | None = None,
    market_categories: str | None = None,
    why_failed: str | None = None,
    revival_conditions: str | None = None,
    evidence: dict | None = None,
) -> EvoGraveyardEntry:
    existing = session.scalar(
        select(EvoGraveyardEntry).where(EvoGraveyardEntry.slug == slug)
    )
    if existing is not None:
        return existing
    row = EvoGraveyardEntry(
        slug=slug,
        title=title[:200],
        summary=summary,
        outcome=outcome,
        source=source,
        source_ref=source_ref,
        strategy_family=strategy_family,
        market_categories=market_categories,
        why_failed=why_failed,
        revival_conditions=revival_conditions,
        evidence_json=evidence,
    )
    session.add(row)
    session.flush()
    return row


def summarize_for_prompt(rows: list[EvoGraveyardEntry], *, max_chars: int = 4000) -> str:
    out: list[str] = []
    used = 0
    for r in rows:
        text = f"[{r.outcome}] {r.slug}: {r.summary[:300]} WHY: {(r.why_failed or '')[:150]}"
        if used + len(text) > max_chars:
            break
        out.append(text)
        used += len(text)
    return "\n".join(out)
