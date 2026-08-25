"""Evo findings — the Evo Ticket Workshop's durable work items.

A finding is what the population noticed and cannot fix by itself: a replay defect, an
invalid genome, a diversity collapse, a sample-starved cohort, an unexplained
performance gap, a research question worth a real probe.

Three boundaries, each of which exists because crossing it would break something:

* A finding is **not** an `evo_tickets` row. Those are the LLM fleet's capability
  requests ("I need access to X"), triaged by the `evo-ticket-triage` skill. A
  population finding is an observation about a *search*, and the two queues would rank
  against each other meaninglessly if merged.
* A finding is **not** an Experiment OS issue. XOS issues are anomalies in registered
  experiments and carry XOS's own ownership and workflow. A finding may *route to* one
  — `route_to='experiment_os_issue'`, with the XOS key recorded in `external_ref` once
  a session with that role opens it — but this layer never opens one itself.
* A finding **authorizes nothing**. It never changes a lifecycle state, a gate, a
  verdict, an epoch or an exposure, and it never retires a candidate. It routes work to
  the role that owns the problem; there is deliberately no fixer role.

Findings are deduplicated on a stable key so a condition that persists across
generations does not produce one row per generation.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from .models import EvoFinding

ROUTE_WORKSHOP = "evo_ticket_workshop"
ROUTE_RESEARCH_LAB = "research_lab"
ROUTE_XOS_ISSUE = "experiment_os_issue"
ROUTE_PLATFORM_REVIEW = "platform_change_review"
ROUTE_MUTATION = "mutation_candidate"

KIND_REPLAY_DEFECT = "replay_defect"
KIND_INVALID_GENOME = "invalid_genome"
KIND_DIVERSITY_COLLAPSE = "diversity_collapse"
KIND_SAMPLE_STARVED = "sample_starved"
KIND_CONCENTRATION = "concentration"
KIND_UNEXPLAINED_GAP = "unexplained_performance_gap"
KIND_MUTATION_BUG = "mutation_bug"
KIND_RISK_BREACH = "risk_breach"
KIND_RESEARCH_QUESTION = "research_question"
KIND_INERT_MUTATION = "inert_mutation"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record(
    session,
    *,
    program_id: int,
    kind: str,
    title: str,
    dedup_key: str,
    severity: str = "warn",
    route_to: str = ROUTE_WORKSHOP,
    generation_number: int | None = None,
    candidate_uuid: str | None = None,
    detail: str | None = None,
    evidence: dict | None = None,
) -> EvoFinding:
    """Open a finding, or refresh the one that already exists for this condition.

    Refreshing rather than reopening matters: a diversity collapse that persists for
    four generations is one problem seen four times, and four rows would make the queue
    look like four problems while burying the one that is new."""
    existing = session.execute(
        select(EvoFinding).where(
            EvoFinding.program_id == program_id, EvoFinding.dedup_key == dedup_key
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.updated_at = _now()
        if existing.status in ("resolved", "rejected"):
            # The condition recurred after someone closed it. That is worth seeing
            # again, so it reopens rather than staying quietly closed.
            existing.status = "open"
            existing.resolution = None
        existing.severity = severity
        existing.title = title[:200]
        existing.detail = detail
        existing.evidence_json = evidence
        if generation_number is not None:
            existing.generation_number = generation_number
        session.flush()
        return existing

    row = EvoFinding(
        program_id=program_id,
        generation_number=generation_number,
        candidate_uuid=candidate_uuid,
        kind=kind,
        severity=severity,
        dedup_key=dedup_key[:160],
        title=title[:200],
        detail=detail,
        evidence_json=evidence,
        route_to=route_to,
        status="open",
    )
    session.add(row)
    session.flush()
    return row


def open_findings(session, *, program_id: int, limit: int = 50) -> list[EvoFinding]:
    return list(
        session.execute(
            select(EvoFinding)
            .where(
                EvoFinding.program_id == program_id,
                EvoFinding.status.in_(("open", "acknowledged", "routed")),
            )
            .order_by(EvoFinding.severity.desc(), EvoFinding.id.desc())
            .limit(limit)
        ).scalars()
    )


def resolve(
    session, *, finding_id: int, resolution: str, external_ref: str | None = None
) -> EvoFinding | None:
    """Close a finding with a concrete result.

    A resolution string is required. "Closed for age" is exactly the failure the Evo
    Ticket Workshop playbook names, and an optional field would invite it."""
    row = session.get(EvoFinding, finding_id)
    if row is None:
        return None
    if not resolution.strip():
        raise ValueError("a finding needs a concrete resolution, not an empty close")
    row.status = "resolved"
    row.resolution = resolution
    if external_ref:
        row.external_ref = external_ref[:64]
    row.updated_at = _now()
    session.flush()
    return row


def route(
    session, *, finding_id: int, route_to: str, note: str | None = None
) -> EvoFinding | None:
    """Hand a finding to the role that owns the problem."""
    row = session.get(EvoFinding, finding_id)
    if row is None:
        return None
    row.route_to = route_to
    row.status = "routed"
    if note:
        row.detail = f"{row.detail or ''}\n\nROUTED: {note}".strip()
    row.updated_at = _now()
    session.flush()
    return row


# ---------------------------------------------------------------------------
# Automatic detection
# ---------------------------------------------------------------------------


def scan_generation(
    session,
    *,
    program,
    generation,
    fitness_rows: list,
    outcome,
) -> list[EvoFinding]:
    """Derive findings from a generation that has just been decided.

    Only conditions the population can detect about itself. Anything requiring judgement
    about whether an edge is real belongs to Research Lab, and anything about a
    registered experiment's standing belongs to Experiment OS."""
    found: list[EvoFinding] = []
    program_id = program.id
    gen = generation.number
    n = len(fitness_rows)

    invalid = [r for r in fitness_rows if r.evidence_class == "invalid"]
    for row in invalid:
        found.append(
            record(
                session,
                program_id=program_id,
                generation_number=gen,
                candidate_uuid=row.candidate_uuid,
                kind=KIND_INVALID_GENOME,
                severity="critical",
                route_to=ROUTE_WORKSHOP,
                dedup_key=f"invalid:{row.candidate_uuid}:{row.genome_hash}",
                title=f"Candidate {row.candidate_uuid[:8]} could not be evaluated",
                detail=(
                    f"{row.notes}. Not ranked and not retired: a candidate that could "
                    "not be evaluated is a defect to investigate, not a bad strategy."
                ),
                evidence={"run_id": row.run_id, "genome_hash": row.genome_hash},
            )
        )

    insufficient = [r for r in fitness_rows if r.evidence_class == "insufficient"]
    if n and len(insufficient) / n >= 0.25:
        found.append(
            record(
                session,
                program_id=program_id,
                generation_number=gen,
                kind=KIND_SAMPLE_STARVED,
                severity="warn",
                route_to=ROUTE_RESEARCH_LAB,
                dedup_key=f"sample-starved:{program_id}:{gen}",
                title=(
                    f"{len(insufficient)}/{n} candidates below the evidence minimum "
                    f"of {program.min_trades_for_evidence} trades"
                ),
                detail=(
                    "A cohort this thin cannot rank on performance. Either the window is "
                    "too short for the genomes' universes, or the universes are too "
                    "narrow for the window — both are design questions, not tuning."
                ),
                evidence={
                    "insufficient": len(insufficient),
                    "members": n,
                    "min_trades": int(program.min_trades_for_evidence),
                    "window": [generation.window_start, generation.window_end],
                },
            )
        )

    div = outcome.diversity or {}
    for warning in div.get("warnings") or []:
        if "diversity collapsing" in warning:
            kind, severity = KIND_DIVERSITY_COLLAPSE, "critical"
        elif "duplicates" in warning:
            kind, severity = KIND_MUTATION_BUG, "critical"
        else:
            kind, severity = KIND_CONCENTRATION, "warn"
        found.append(
            record(
                session,
                program_id=program_id,
                generation_number=gen,
                kind=kind,
                severity=severity,
                route_to=ROUTE_WORKSHOP,
                # Keyed on the condition, not the generation: a collapse that persists
                # is one finding that keeps being true.
                dedup_key=f"{kind}:{program_id}",
                title=warning[:200],
                detail=(
                    "Population diversity measured across the active cohort's current "
                    "genomes. A homogeneous cohort still produces a leaderboard, so this "
                    "is only visible if it is measured."
                ),
                evidence=div,
            )
        )

    breaches = [
        r for r in fitness_rows
        if ((r.components_json or {}).get("integrity", {}).get("detail") or "").startswith(
            "peak exposure"
        )
    ]
    if breaches:
        found.append(
            record(
                session,
                program_id=program_id,
                generation_number=gen,
                kind=KIND_RISK_BREACH,
                severity="warn",
                route_to=ROUTE_WORKSHOP,
                dedup_key=f"risk-breach:{program_id}:{gen}",
                title=f"{len(breaches)} candidates breached their virtual capital",
                detail=(
                    "Peak concurrent exposure exceeded the program's starting capital. "
                    "The replay engine visits markets sequentially and does not itself "
                    "enforce a concurrency cap, so this is measured after the fact rather "
                    "than prevented during the run."
                ),
                evidence={"candidates": [r.candidate_uuid for r in breaches]},
            )
        )

    for child in getattr(outcome, "inert_children", []) or []:
        found.append(
            record(
                session,
                program_id=program_id,
                generation_number=gen,
                candidate_uuid=child.get("candidate_uuid"),
                kind=KIND_INERT_MUTATION,
                severity="warn",
                route_to=ROUTE_RESEARCH_LAB,
                dedup_key=f"inert:{child.get('candidate_uuid')}:{gen}",
                title=(
                    f"{child.get('label')} produced an identical trade tape to its parent "
                    f"{child.get('parent_label')}"
                ),
                detail=(
                    "The mutation changed the genome but not the replay: same outcome "
                    "fingerprint over the same window. Either the engine cannot express "
                    "the gene that moved, or the value moved outside the range this "
                    "corpus can distinguish. Both spend a cohort slot on a hypothesis "
                    "that was not testable here, so the child's result is not evidence "
                    "about the mutation.\n\n"
                    f"genes changed: {child.get('changed')}"
                ),
                evidence=child,
            )
        )

    if outcome.refusals:
        found.append(
            record(
                session,
                program_id=program_id,
                generation_number=gen,
                kind=KIND_MUTATION_BUG,
                severity="info",
                route_to=ROUTE_WORKSHOP,
                dedup_key=f"proposal-refusals:{program_id}:{gen}",
                title=(
                    f"{len(outcome.refusals)} top-ranked candidates could not produce an "
                    "admissible child"
                ),
                detail=(
                    "Every proposal was refused by the admission gates. Usually the parent "
                    "is cornered against the risk envelope or surrounded by near-duplicates; "
                    "either way the population is not exploring from its best candidates."
                ),
                evidence={"refusals": outcome.refusals[:10]},
            )
        )

    return found


__all__ = [
    "KIND_CONCENTRATION",
    "KIND_DIVERSITY_COLLAPSE",
    "KIND_INVALID_GENOME",
    "KIND_MUTATION_BUG",
    "KIND_REPLAY_DEFECT",
    "KIND_RESEARCH_QUESTION",
    "KIND_RISK_BREACH",
    "KIND_SAMPLE_STARVED",
    "KIND_UNEXPLAINED_GAP",
    "ROUTE_MUTATION",
    "ROUTE_PLATFORM_REVIEW",
    "ROUTE_RESEARCH_LAB",
    "ROUTE_WORKSHOP",
    "ROUTE_XOS_ISSUE",
    "open_findings",
    "record",
    "resolve",
    "route",
    "scan_generation",
]
