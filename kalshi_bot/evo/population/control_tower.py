"""Evo Control Tower — the read-oriented view of a population.

It explains; it never mutates. Nothing in this module writes, and that is a structural
property rather than a convention: it imports no service, opens no transaction, and its
only inputs are queries.

What it is for is the question a leaderboard cannot answer. A ranked list says agent-017
is first; this says *why* — which components carried it, what its parent was, what
changed between them, and what the cohort as a whole is failing at. The handoff's phrase
for the failure mode is "a single opaque magic number", and the antidote is that every
rank here can be unfolded into the components that produced it.

Performance of experiments that Evo eventually hands to Experiment OS is not shown here.
That is XOS's number to give, and a copy would be stale (`DEC-001`).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from . import fitness as fitness_mod
from . import genome as genome_mod
from .models import (
    EvoCandidate,
    EvoCandidateLedger,
    EvoDecision,
    EvoFinding,
    EvoFitness,
    EvoGeneration,
    EvoGenomeVersion,
    EvoProgram,
    EvoRun,
)


def _fmt_money(value) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_cents(value) -> str:
    try:
        return f"{float(value):+.2f}c/ct"
    except (TypeError, ValueError):
        return "—"


def collect(session, *, program: EvoProgram, generation_number: int | None = None) -> dict:
    """Everything the Tower renders, as data. Kept separate from the rendering so a
    dashboard or a test can consume the same structure without parsing text."""
    generation = _generation(session, program, generation_number)
    if generation is None:
        return {"program": _program_row(program), "generation": None}

    rows = list(
        session.execute(
            select(EvoFitness)
            .where(EvoFitness.generation_id == generation.id)
            .order_by(EvoFitness.rank.is_(None), EvoFitness.rank, EvoFitness.id)
        ).scalars()
    )
    candidates = {
        c.uuid: c
        for c in session.execute(
            select(EvoCandidate).where(EvoCandidate.program_id == program.id)
        ).scalars()
    }
    ledgers = {
        led.run_id: led
        for led in session.execute(
            select(EvoCandidateLedger).where(
                EvoCandidateLedger.program_id == program.id,
                EvoCandidateLedger.generation_number == generation.number,
            )
        ).scalars()
    }
    decisions = list(
        session.execute(
            select(EvoDecision)
            .where(EvoDecision.generation_id == generation.id)
            .order_by(EvoDecision.id)
        ).scalars()
    )

    entries = []
    for row in rows:
        candidate = candidates.get(row.candidate_uuid)
        led = ledgers.get(row.run_id) if row.run_id else None
        decision = next(
            (d for d in decisions if d.candidate_uuid == row.candidate_uuid), None
        )
        entries.append(
            {
                "label": candidate.label if candidate else row.candidate_uuid[:8],
                "uuid": row.candidate_uuid,
                "family": candidate.family if candidate else None,
                "parent_label": (candidate.lineage_json or {}).get("parent_label")
                if candidate
                else None,
                "rank": row.rank,
                "rank_group": row.rank_group,
                "fitness": row.fitness,
                "evidence_class": row.evidence_class,
                "n_trades": row.n_trades,
                "notes": row.notes,
                "genome_hash": row.genome_hash,
                "net_pnl_usd": float(led.realized_pnl_usd) if led else None,
                "max_drawdown_usd": float(led.max_drawdown_usd) if led else None,
                "cents_per_contract": _run_field(session, row.run_id, "per_trade_cents_per_contract"),
                "realizable_cents_per_contract": _run_field(
                    session, row.run_id, "realizable_cents_per_contract"
                ),
                "components": row.components_json,
                "explain": fitness_mod.explain(row.components_json),
                "decision": decision.decision if decision else None,
                "decision_reason": decision.reason if decision else None,
            }
        )

    children = [
        {
            "label": candidates[d.child_candidate_uuid].label
            if d.child_candidate_uuid in candidates
            else (d.child_candidate_uuid or "")[:8],
            "parent_label": candidates[d.candidate_uuid].label
            if d.candidate_uuid in candidates
            else d.candidate_uuid[:8],
            "mutation": _mutation_summary(session, d.child_genome_id),
            "hypothesis": _genome_field(session, d.child_genome_id, "hypothesis"),
            "kind": _genome_field(session, d.child_genome_id, "mutation_kind"),
        }
        for d in decisions
        if d.decision == "reproduce" and d.child_candidate_uuid
    ]

    findings = list(
        session.execute(
            select(EvoFinding)
            .where(
                EvoFinding.program_id == program.id,
                EvoFinding.status.in_(("open", "acknowledged", "routed")),
            )
            .order_by(EvoFinding.id.desc())
            .limit(20)
        ).scalars()
    )

    return {
        "program": _program_row(program),
        "generation": {
            "number": generation.number,
            "status": generation.status,
            "mode": generation.mode,
            "dataset": generation.dataset,
            "window": [generation.window_start, generation.window_end],
            "data_cutoff": generation.data_cutoff,
            "members": generation.member_count,
            "summary": generation.summary_json or {},
        },
        "entries": entries,
        "children": children,
        "diversity": generation.diversity_json or {},
        "findings": [
            {
                "id": f.id,
                "kind": f.kind,
                "severity": f.severity,
                "title": f.title,
                "route_to": f.route_to,
                "status": f.status,
            }
            for f in findings
        ],
        "population": _population_counts(session, program.id),
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


def _program_row(program: EvoProgram) -> dict:
    return {
        "key": program.key,
        "name": program.name,
        "status": program.status,
        "objective": program.objective,
        "mode": program.mode,
        "dataset": program.dataset,
        "cohort_target": program.cohort_target,
        "starting_capital_usd": float(program.starting_capital_usd),
        "min_trades_for_evidence": program.min_trades_for_evidence,
        "platform_snapshot": program.platform_snapshot,
        "evaluator_revision": program.evaluator_revision,
        "engine_revision": program.engine_revision,
    }


def _generation(
    session, program: EvoProgram, number: int | None
) -> EvoGeneration | None:
    stmt = select(EvoGeneration).where(EvoGeneration.program_id == program.id)
    if number is not None:
        stmt = stmt.where(EvoGeneration.number == number)
    else:
        stmt = stmt.order_by(EvoGeneration.number.desc())
    return session.execute(stmt.limit(1)).scalars().first()


def _population_counts(session, program_id: int) -> dict:
    rows = session.execute(
        select(EvoCandidate.state, func.count())
        .where(EvoCandidate.program_id == program_id)
        .group_by(EvoCandidate.state)
    ).all()
    return {str(state): int(count) for state, count in rows}


def _run_field(session, run_id: int | None, key: str):
    if run_id is None:
        return None
    run = session.get(EvoRun, run_id)
    return (run.outcome_json or {}).get(key) if run else None


def _genome_field(session, genome_id: int | None, key: str):
    if genome_id is None:
        return None
    genome = session.get(EvoGenomeVersion, genome_id)
    return getattr(genome, key, None) if genome else None


def _mutation_summary(session, genome_id: int | None) -> str:
    if genome_id is None:
        return "—"
    genome = session.get(EvoGenomeVersion, genome_id)
    if genome is None:
        return "—"
    diff = genome.mutation_diff_json or []
    if not diff:
        return "no gene change recorded"
    return ", ".join(f"{c.get('path')} {c.get('from')} → {c.get('to')}" for c in diff)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render(data: dict, *, top: int = 5, show: int = 5) -> str:
    """The operator-facing report."""
    program = data.get("program") or {}
    lines: list[str] = []
    lines.append("SESSION: Evo Control Tower")
    lines.append("MODE: READ ONLY")
    lines.append(
        f"PROGRAM: {program.get('key')} — {program.get('name')} [{program.get('status')}]"
    )
    lines.append(
        f"SUBSTRATE: {program.get('mode')} · {program.get('dataset')} · "
        f"engine {program.get('engine_revision')} · evaluator {program.get('evaluator_revision')}"
    )
    lines.append(f"PLATFORM SNAPSHOT: {program.get('platform_snapshot') or '—'}")
    lines.append(f"AS OF: {data.get('as_of')}")
    lines.append("")

    generation = data.get("generation")
    if not generation:
        lines.append("No generations have run yet.")
        return "\n".join(lines)

    pop = data.get("population") or {}
    summary = generation.get("summary") or {}
    lines.append(
        f"EVO PROGRAM · generation {generation['number']} · "
        f"{generation.get('members', 0)} agents · {generation.get('mode')}"
    )
    lines.append(
        f"window {generation['window'][0]}..{generation['window'][1]} "
        f"(cutoff {generation.get('data_cutoff')}) · "
        f"active {pop.get('active', 0)} · retired {pop.get('retired', 0)}"
    )
    lines.append(
        f"evidence: {summary.get('adequate', 0)} adequate · "
        f"{summary.get('insufficient', 0)} held · {summary.get('invalid', 0)} invalid"
    )
    lines.append("")

    entries = data.get("entries") or []
    ranked = [e for e in entries if e.get("rank")]
    held = [e for e in entries if e.get("evidence_class") == "insufficient"]
    invalid = [e for e in entries if e.get("evidence_class") == "invalid"]

    def _row(entry: dict) -> str:
        return (
            f"  {entry['label']:<10} fit {entry['fitness']:.4f}  "
            f"{_fmt_cents(entry.get('cents_per_contract')):>10}  "
            f"dd {_fmt_money(entry.get('max_drawdown_usd')):>10}  "
            f"n={entry.get('n_trades'):<4} "
            f"parent {entry.get('parent_label') or '—'}"
        )

    if ranked:
        lines.append("TOP")
        for entry in ranked[:top]:
            lines.append(_row(entry))
            lines.append(f"             why: {entry.get('explain')}")
        lines.append("")

        cont = [e for e in ranked if e.get("decision") == "continue"]
        if cont:
            lines.append("CONTINUE")
            for entry in cont[:show]:
                lines.append(_row(entry))
            if len(cont) > show:
                lines.append(f"  … and {len(cont) - show} more")
            lines.append("")

        retiring = [e for e in ranked if e.get("decision") == "retire"]
        if retiring:
            lines.append("RETIRE")
            for entry in retiring[:show]:
                lines.append(_row(entry))
                lines.append(f"             {entry.get('decision_reason')}")
            if len(retiring) > show:
                lines.append(f"  … and {len(retiring) - show} more")
            lines.append("")

    children = data.get("children") or []
    if children:
        lines.append("NEW CHILDREN")
        for child in children[:show]:
            lines.append(f"  {child['label']} ← {child['parent_label']}")
            lines.append(f"    mutation: {child['mutation']}  [{child.get('kind')}]")
            if child.get("hypothesis"):
                lines.append(f"    hypothesis: {child['hypothesis']}")
        if len(children) > show:
            lines.append(f"  … and {len(children) - show} more")
        lines.append("")

    warnings: list[str] = []
    if held:
        warnings.append(
            f"{len(held)} agents have n < the program minimum of "
            f"{program.get('min_trades_for_evidence')} — held, not ranked"
        )
    if invalid:
        warnings.append(
            f"{len(invalid)} agents could not be evaluated: "
            + "; ".join(f"{e['label']} ({e.get('notes')})" for e in invalid[:3])
        )
    div = data.get("diversity") or {}
    warnings.extend(div.get("warnings") or [])
    if div:
        top_family = div.get("top_family")
        if top_family and float(div.get("top_family_share") or 0) >= 0.5:
            warnings.append(
                f"{top_family} concentration {float(div['top_family_share']):.0%}"
            )
    if warnings:
        lines.append("WARNINGS")
        for warning in warnings:
            lines.append(f"  {warning}")
        lines.append("")

    findings = data.get("findings") or []
    if findings:
        lines.append("NEEDS HUMAN ATTENTION")
        for finding in findings[:show]:
            lines.append(
                f"  [{finding['severity']:>8}] #{finding['id']} {finding['title']}"
            )
            lines.append(f"             → {finding['route_to']} ({finding['status']})")
        if len(findings) > show:
            lines.append(f"  … and {len(findings) - show} more")
        lines.append("")

    lines.append("DIVERSITY")
    lines.append(
        f"  mean pairwise genome distance {div.get('mean_pairwise_distance', 0):.4f} · "
        f"{div.get('distinct_genomes', 0)} distinct genomes across {div.get('n', 0)} members"
    )
    families = div.get("family_shares") or {}
    if families:
        lines.append(
            "  families: "
            + ", ".join(f"{k} {v:.0%}" for k, v in sorted(
                families.items(), key=lambda kv: -kv[1]
            )[:5])
        )
    return "\n".join(lines)


def explain_candidate(session, *, program: EvoProgram, label_or_uuid: str) -> str:
    """The full story of one candidate: lineage, genome, every generation it ran, and
    the component breakdown behind each rank."""
    candidate = session.execute(
        select(EvoCandidate).where(
            EvoCandidate.program_id == program.id,
            (EvoCandidate.label == label_or_uuid) | (EvoCandidate.uuid == label_or_uuid),
        )
    ).scalars().first()
    if candidate is None:
        return f"no candidate {label_or_uuid!r} in program {program.key!r}"

    genome = (
        session.get(EvoGenomeVersion, candidate.current_genome_id)
        if candidate.current_genome_id
        else None
    )
    lines = [
        f"{candidate.label} [{candidate.state}] family={candidate.family}",
        f"born generation {candidate.birth_generation} · origin {candidate.origin} · "
        f"depth {candidate.generation_depth}",
    ]
    parent_label = (candidate.lineage_json or {}).get("parent_label")
    if parent_label:
        lines.append(f"parent: {parent_label}")
    if candidate.purpose:
        lines.append(f"purpose: {candidate.purpose}")
    if candidate.retirement_reason:
        lines.append(f"retired: {candidate.retirement_reason}")
    if genome is not None:
        lines.append("")
        lines.append(f"GENOME v{genome.version} {genome.genome_hash[:16]}")
        lines.append(f"  {genome_mod.describe(genome.document_json or {})}")
        lines.append(f"  source: {genome.mutation_source} / {genome.mutation_kind}")
        for change in genome.mutation_diff_json or []:
            lines.append(
                f"  changed: {change.get('path')} {change.get('from')} → {change.get('to')}"
            )
        if genome.hypothesis:
            lines.append(f"  hypothesis: {genome.hypothesis}")

    rows = list(
        session.execute(
            select(EvoFitness)
            .where(
                EvoFitness.program_id == program.id,
                EvoFitness.candidate_uuid == candidate.uuid,
            )
            .order_by(EvoFitness.generation_number)
        ).scalars()
    )
    if rows:
        lines.append("")
        lines.append("HISTORY")
        for row in rows:
            if row.fitness is None:
                lines.append(
                    f"  gen {row.generation_number}: {row.evidence_class} — {row.notes}"
                )
            else:
                lines.append(
                    f"  gen {row.generation_number}: rank {row.rank or '—'} "
                    f"({row.rank_group}) fitness {row.fitness:.4f}"
                )
            for key, comp in (row.components_json or {}).items():
                lines.append(
                    f"      {key:<20} score {float(comp.get('score', 0)):.3f} × "
                    f"w {float(comp.get('weight', 0)):.2f} = "
                    f"{float(comp.get('contribution', 0)):.4f}   {comp.get('detail')}"
                )
    return "\n".join(lines)


def lineage_tree(session, *, program: EvoProgram) -> str:
    """The family tree, parents above children."""
    candidates = list(
        session.execute(
            select(EvoCandidate)
            .where(EvoCandidate.program_id == program.id)
            .order_by(EvoCandidate.id)
        ).scalars()
    )
    by_parent: dict[str | None, list[EvoCandidate]] = {}
    for candidate in candidates:
        by_parent.setdefault(candidate.parent_uuid, []).append(candidate)

    lines: list[str] = []

    def walk(parent_uuid: str | None, depth: int) -> None:
        for candidate in by_parent.get(parent_uuid, []):
            mark = {"active": "•", "retired": "×", "invalid": "!"}.get(candidate.state, "?")
            lines.append(
                f"{'  ' * depth}{mark} {candidate.label} "
                f"[{candidate.family}] gen{candidate.birth_generation}"
            )
            walk(candidate.uuid, depth + 1)

    walk(None, 0)
    return "\n".join(lines) if lines else "no candidates"


__all__ = ["collect", "explain_candidate", "lineage_tree", "render"]
