"""Evolution mechanics: run a generation, evaluate it, decide, reproduce, retire.

The loop is deliberately explicit and boring:

    open → run every active candidate's current genome → evaluate → rank → decide
         → retire the bottom, continue the middle, reproduce from the top → close

Three invariants hold throughout, and the tests assert each of them:

* **A parent is never mutated in place.** Reproduction creates a child candidate with a
  new genome. The parent keeps its identity, its genome and its ledger history, and
  stays eligible in the next generation.
* **No decision is implicit.** Every retirement, continuation, reproduction, hold and
  escalation writes an `EvoDecision` carrying the evidence, the thresholds, the
  evaluator revision and the reason. Nothing changes a candidate's state without one.
* **A genome is frozen once evaluated.** Running a genome marks it evaluated; a
  material change after that must create a new version, which `mutation.admit_proposal`
  is the only way to do.
"""

from __future__ import annotations

import uuid as uuid_lib
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select

from ..config import EvoSettings
from . import diversity, mutation, replay
from . import fitness as fitness_mod
from . import genome as genome_mod
from .models import (
    EvoCandidate,
    EvoDecision,
    EvoFitness,
    EvoGeneration,
    EvoGenomeVersion,
    EvoJournalEntry,
    EvoProgram,
    EvoRun,
)

DECISION_CONTINUE = "continue"
DECISION_REPRODUCE = "reproduce"
DECISION_RETIRE = "retire"
DECISION_HOLD = "hold"
DECISION_ESCALATE = "escalate"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid_lib.uuid4())


# ---------------------------------------------------------------------------
# Candidates and genomes
# ---------------------------------------------------------------------------


def next_label(session, program_id: int) -> str:
    """`agent-001`, `agent-002`, … Stable and program-scoped, so a label in a Control
    Tower report identifies exactly one candidate forever."""
    used = session.execute(
        select(func.count()).select_from(EvoCandidate).where(
            EvoCandidate.program_id == program_id
        )
    ).scalar_one()
    return f"agent-{used + 1:03d}"


def create_candidate(
    session,
    *,
    program: EvoProgram,
    document: dict,
    generation_number: int,
    origin: str = "founder",
    parent: EvoCandidate | None = None,
    purpose: str | None = None,
    evidence_cutoff: str | None = None,
    genome: EvoGenomeVersion | None = None,
) -> tuple[EvoCandidate, EvoGenomeVersion]:
    """Create a candidate and its genome version 1.

    `genome` is passed when the caller already minted it through
    `mutation.admit_proposal` — reproduction goes that way so a child's genome is
    always the product of an evaluated proposal, never constructed here."""
    norm, err = genome_mod.validate(document)
    if err or norm is None:
        raise ValueError(f"invalid founder genome: {err}")

    candidate = EvoCandidate(
        uuid=_new_uuid(),
        program_id=program.id,
        label=next_label(session, program.id),
        birth_generation=generation_number,
        parent_uuid=parent.uuid if parent else None,
        origin=origin,
        generation_depth=(parent.generation_depth + 1) if parent else 0,
        state="active",
        family=str(norm.get("family") or "unassigned"),
        purpose=purpose or (genome.hypothesis if genome else None),
        lineage_json={
            "ancestors": ((parent.lineage_json or {}).get("ancestors", []) + [parent.uuid])
            if parent
            else [],
            "parent_label": parent.label if parent else None,
        },
    )
    session.add(candidate)
    session.flush()

    if genome is None:
        genome = EvoGenomeVersion(
            program_id=program.id,
            candidate_uuid=candidate.uuid,
            version=1,
            genome_hash=genome_mod.genome_hash(norm),
            document_json=norm,
            family=candidate.family,
            mutation_source="founder" if origin == "founder" else origin,
            mutation_kind="seed",
            hypothesis=purpose,
            rationale="seed genome",
            universe_json=norm.get("universe"),
            risk_json=norm.get("risk"),
            born_generation=generation_number,
            evidence_cutoff=evidence_cutoff,
            platform_snapshot=program.platform_snapshot,
        )
        session.add(genome)
        session.flush()
    else:
        # A genome minted by admit_proposal is created before its candidate exists, so
        # the ownership link is completed here.
        genome.candidate_uuid = candidate.uuid

    candidate.current_genome_id = genome.id
    session.flush()
    return candidate, genome


def current_genome(session, candidate: EvoCandidate) -> EvoGenomeVersion | None:
    if candidate.current_genome_id is None:
        return None
    return session.get(EvoGenomeVersion, candidate.current_genome_id)


def active_candidates(session, program_id: int) -> list[EvoCandidate]:
    return list(
        session.execute(
            select(EvoCandidate)
            .where(EvoCandidate.program_id == program_id, EvoCandidate.state == "active")
            .order_by(EvoCandidate.id)
        ).scalars()
    )


def population_documents(session, program_id: int) -> list[dict]:
    """Every genome document ever admitted to this program.

    Novelty is checked against the whole history, not just the living population: a
    genome that was retired for being bad is not a good idea again just because nothing
    currently in the cohort resembles it."""
    rows = session.execute(
        select(EvoGenomeVersion.document_json).where(EvoGenomeVersion.program_id == program_id)
    ).scalars()
    return [r for r in rows if isinstance(r, dict)]


# ---------------------------------------------------------------------------
# Generations
# ---------------------------------------------------------------------------


def open_generation(
    session,
    *,
    program: EvoProgram,
    number: int,
    window_start: str | None,
    window_end: str | None,
    data_cutoff: str | None = None,
) -> EvoGeneration:
    """Open a generation over a window. The cutoff defaults to the window end, which is
    the tightest no-look-ahead boundary the window itself implies."""
    cutoff = data_cutoff or window_end
    replay.check_window(window_start, window_end, cutoff)
    generation = EvoGeneration(
        program_id=program.id,
        number=number,
        status="open",
        mode=program.mode,
        dataset=program.dataset,
        window_start=window_start,
        window_end=window_end,
        data_cutoff=cutoff,
        rng_seed=int(program.rng_seed) + number,
        provenance_json={
            "engine_revision": replay.ENGINE_REVISION,
            "evaluator_revision": fitness_mod.EVALUATOR_REVISION,
            "genome_schema_revision": genome_mod.GENOME_SCHEMA_REVISION,
            "mutation_engine_revision": mutation.MUTATION_ENGINE_REVISION,
            "platform_snapshot": program.platform_snapshot,
            "policy": {
                "cohort_target": program.cohort_target,
                "reproduce_fraction": program.reproduce_fraction,
                "continue_fraction": program.continue_fraction,
                "retire_fraction": program.retire_fraction,
                "min_trades_for_evidence": program.min_trades_for_evidence,
                "min_genome_distance": program.min_genome_distance,
            },
        },
    )
    session.add(generation)
    session.flush()
    return generation


@dataclass
class GenerationOutcome:
    generation: EvoGeneration
    runs: list[EvoRun] = field(default_factory=list)
    fitness_rows: list[EvoFitness] = field(default_factory=list)
    decisions: list[EvoDecision] = field(default_factory=list)
    children: list[EvoCandidate] = field(default_factory=list)
    refusals: list[dict] = field(default_factory=list)
    diversity: dict = field(default_factory=dict)
    inert_children: list[dict] = field(default_factory=list)


def run_generation(
    session,
    settings: EvoSettings,
    *,
    program: EvoProgram,
    generation: EvoGeneration,
) -> list[EvoRun]:
    """Replay every active candidate's current genome over this generation's window.

    Each candidate is replayed independently against its own ledger. A candidate whose
    replay is refused gets a recorded run with `status='refused'` — the population needs
    to see that it could not be evaluated, and dropping it would quietly shrink the
    cohort."""
    generation.status = "running"
    session.flush()

    runs: list[EvoRun] = []
    for candidate in active_candidates(session, program.id):
        genome = current_genome(session, candidate)
        if genome is None:
            continue
        try:
            replayed = replay.replay(
                session,
                settings,
                document=genome.document_json,
                dataset=generation.dataset,
                window_start=generation.window_start,
                window_end=generation.window_end,
                data_cutoff=generation.data_cutoff,
                starting_capital_usd=float(program.starting_capital_usd),
            )
        except replay.ReplayRefused as exc:
            runs.append(
                replay.persist_run(
                    session, program=program, generation=generation,
                    candidate_uuid=candidate.uuid, genome_id=genome.id,
                    genome_hash=genome.genome_hash, replayed=None, error=str(exc),
                )
            )
            continue
        runs.append(
            replay.persist_run(
                session, program=program, generation=generation,
                candidate_uuid=candidate.uuid, genome_id=genome.id,
                genome_hash=genome.genome_hash, replayed=replayed,
            )
        )
        # A genome that has produced evidence is frozen from here on.
        genome.evaluated = True

    generation.member_count = len(runs)
    session.flush()
    return runs


def _trade_cents(session, run_id: int) -> list[float]:
    from .models import EvoRunTrade

    rows = session.execute(
        select(EvoRunTrade.cents_per_contract).where(EvoRunTrade.run_id == run_id)
    ).scalars()
    return [float(v) for v in rows if v is not None]


def evaluate_generation(
    session,
    *,
    program: EvoProgram,
    generation: EvoGeneration,
) -> list[EvoFitness]:
    """Score every run, then rank the ones that carry adequate evidence.

    Only `adequate` candidates are ranked against each other. `insufficient` and
    `invalid` get a fitness row (so the Tower can show why) but no rank — being unranked
    is what protects them from being retired for a number that does not mean anything
    yet."""
    weights = fitness_mod.resolve_weights((program.fitness_weights_json or {}).get("weights"))
    scales = fitness_mod.resolve_scales((program.fitness_weights_json or {}).get("scales"))

    runs = list(
        session.execute(
            select(EvoRun).where(EvoRun.generation_id == generation.id).order_by(EvoRun.id)
        ).scalars()
    )
    ledgers = _ledgers_by_run(session, [r.id for r in runs])

    rows: list[EvoFitness] = []
    for run in runs:
        outcome = run.outcome_json or {}
        integrity = run.integrity_json or {}
        n = int(outcome.get("n_trades") or 0)
        evidence_class, evidence_reason = fitness_mod.classify_evidence(
            run_status=run.status,
            integrity=integrity,
            n_trades=n,
            min_trades=int(program.min_trades_for_evidence),
        )
        row = EvoFitness(
            program_id=program.id,
            generation_id=generation.id,
            generation_number=generation.number,
            candidate_uuid=run.candidate_uuid,
            run_id=run.id,
            genome_hash=run.genome_hash,
            evidence_class=evidence_class,
            n_trades=n,
            weights_json={"weights": weights, "scales": scales},
            evaluator_revision=fitness_mod.EVALUATOR_REVISION,
            notes=evidence_reason,
        )
        if evidence_class != fitness_mod.EVIDENCE_INVALID:
            comps, score = fitness_mod.compute(
                outcome=outcome,
                ledger=ledgers.get(run.id, {}),
                integrity=integrity,
                trade_cents=_trade_cents(session, run.id),
                starting_capital_usd=float(program.starting_capital_usd),
                weights=weights,
                scales=scales,
            )
            row.components_json = fitness_mod.components_payload(comps)
            row.fitness = score
        session.add(row)
        rows.append(row)
    session.flush()

    adequate = [r for r in rows if r.evidence_class == fitness_mod.EVIDENCE_ADEQUATE]
    adequate.sort(key=lambda r: (-(r.fitness or 0.0), r.candidate_uuid))
    groups = fitness_mod.group_by_fractions(
        adequate,
        reproduce=float(program.reproduce_fraction),
        retire=float(program.retire_fraction),
    )
    for index, row in enumerate(adequate, start=1):
        row.rank = index
    for group, members in groups.items():
        for row in members:
            row.rank_group = group
    for row in rows:
        if row.rank_group is None:
            row.rank_group = "held"
    session.flush()

    generation.status = "evaluated"
    generation.evaluated_at = _now()
    session.flush()
    return rows


def _ledgers_by_run(session, run_ids: list[int]) -> dict[int, dict]:
    from .models import EvoCandidateLedger

    if not run_ids:
        return {}
    out: dict[int, dict] = {}
    for led in session.execute(
        select(EvoCandidateLedger).where(EvoCandidateLedger.run_id.in_(run_ids))
    ).scalars():
        detail = led.detail_json or {}
        out[led.run_id] = {
            "realized_pnl_usd": float(led.realized_pnl_usd),
            "fees_usd": float(led.fees_usd),
            "turnover_usd": float(led.turnover_usd),
            "max_drawdown_usd": float(led.max_drawdown_usd),
            "peak_exposure_usd": float(led.peak_exposure_usd),
            "concentration_hhi": led.concentration_hhi,
            "concentration_top_family": led.concentration_top_family,
            "return_on_capital": detail.get("return_on_capital", 0.0),
        }
    return out


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def _record_decision(
    session,
    *,
    program: EvoProgram,
    generation: EvoGeneration,
    row: EvoFitness,
    decision: str,
    reason: str,
    child: EvoCandidate | None = None,
    child_genome: EvoGenomeVersion | None = None,
    proposal_id: int | None = None,
) -> EvoDecision:
    record = EvoDecision(
        program_id=program.id,
        generation_id=generation.id,
        generation_number=generation.number,
        candidate_uuid=row.candidate_uuid,
        decision=decision,
        rank=row.rank,
        rank_group=row.rank_group,
        fitness=row.fitness,
        evidence_class=row.evidence_class,
        evidence_json={
            "run_id": row.run_id,
            "n_trades": row.n_trades,
            "genome_hash": row.genome_hash,
            "components": row.components_json,
        },
        thresholds_json={
            "reproduce_fraction": float(program.reproduce_fraction),
            "retire_fraction": float(program.retire_fraction),
            "min_trades_for_evidence": int(program.min_trades_for_evidence),
            "min_genome_distance": float(program.min_genome_distance),
        },
        evaluator_revision=fitness_mod.EVALUATOR_REVISION,
        reason=reason,
        child_candidate_uuid=child.uuid if child else None,
        child_genome_id=child_genome.id if child_genome else None,
        proposal_id=proposal_id,
    )
    session.add(record)
    session.flush()
    return record


def decide_generation(
    session,
    *,
    program: EvoProgram,
    generation: EvoGeneration,
    max_children: int | None = None,
) -> GenerationOutcome:
    """Turn the ranking into recorded decisions and act on them."""
    rows = list(
        session.execute(
            select(EvoFitness)
            .where(EvoFitness.generation_id == generation.id)
            .order_by(EvoFitness.rank.is_(None), EvoFitness.rank, EvoFitness.id)
        ).scalars()
    )
    outcome = GenerationOutcome(generation=generation)
    candidates = {c.uuid: c for c in active_candidates(session, program.id)}
    existing_docs = population_documents(session, program.id)

    for row in rows:
        candidate = candidates.get(row.candidate_uuid)
        if candidate is None:
            continue

        if row.evidence_class == fitness_mod.EVIDENCE_INVALID:
            outcome.decisions.append(
                _record_decision(
                    session, program=program, generation=generation, row=row,
                    decision=DECISION_ESCALATE,
                    reason=(
                        f"evidence invalid ({row.notes}); not ranked and not retired — a "
                        "candidate that could not be evaluated is a defect to investigate, "
                        "not a bad strategy"
                    ),
                )
            )
            continue

        if row.evidence_class == fitness_mod.EVIDENCE_INSUFFICIENT:
            outcome.decisions.append(
                _record_decision(
                    session, program=program, generation=generation, row=row,
                    decision=DECISION_HOLD,
                    reason=(
                        f"held on thin evidence ({row.notes}) — cannot win or lose on "
                        "performance until the sample reaches the program minimum"
                    ),
                )
            )
            continue

        if row.rank_group == "bottom":
            candidate.state = "retired"
            candidate.retired_generation = generation.number
            candidate.retirement_reason = (
                f"rank {row.rank}/{len([r for r in rows if r.rank])} in the bottom "
                f"{float(program.retire_fraction):.0%} at fitness {row.fitness:.4f}"
            )
            outcome.decisions.append(
                _record_decision(
                    session, program=program, generation=generation, row=row,
                    decision=DECISION_RETIRE,
                    reason=candidate.retirement_reason,
                )
            )
            continue

        if row.rank_group == "top":
            if max_children is not None and len(outcome.children) >= max_children:
                outcome.decisions.append(
                    _record_decision(
                        session, program=program, generation=generation, row=row,
                        decision=DECISION_CONTINUE,
                        reason=(
                            f"eligible to reproduce at rank {row.rank} but the generation's "
                            f"child budget of {max_children} was already spent"
                        ),
                    )
                )
                continue
            child, child_genome, proposal_row, refusal = reproduce(
                session,
                program=program,
                generation=generation,
                parent=candidate,
                existing_documents=existing_docs,
            )
            if child is None:
                outcome.refusals.append(refusal or {})
                outcome.decisions.append(
                    _record_decision(
                        session, program=program, generation=generation, row=row,
                        decision=DECISION_CONTINUE,
                        reason=(
                            "eligible to reproduce but every proposal was refused: "
                            + str((refusal or {}).get("reason", "unknown"))
                        ),
                        proposal_id=(refusal or {}).get("proposal_id"),
                    )
                )
                continue
            existing_docs.append(child_genome.document_json)
            outcome.children.append(child)
            outcome.decisions.append(
                _record_decision(
                    session, program=program, generation=generation, row=row,
                    decision=DECISION_REPRODUCE,
                    reason=(
                        f"rank {row.rank} in the top {float(program.reproduce_fraction):.0%} "
                        f"at fitness {row.fitness:.4f}; child {child.label} created with "
                        f"{'; '.join(c['label'] + ' ' + str(c['from']) + ' → ' + str(c['to']) for c in (child_genome.mutation_diff_json or []))}"
                    ),
                    child=child, child_genome=child_genome,
                    proposal_id=proposal_row.id if proposal_row else None,
                )
            )
            continue

        outcome.decisions.append(
            _record_decision(
                session, program=program, generation=generation, row=row,
                decision=DECISION_CONTINUE,
                reason=f"rank {row.rank} in the middle band at fitness {row.fitness:.4f}",
            )
        )

    outcome.fitness_rows = rows
    outcome.inert_children = _detect_inert_children(session, program=program, generation=generation)
    outcome.diversity = _measure_population(session, program).as_dict()
    generation.diversity_json = outcome.diversity
    generation.summary_json = _summarize(rows, outcome)
    generation.status = "closed"
    generation.closed_at = _now()
    session.flush()
    return outcome


def _summarize(rows: list[EvoFitness], outcome: GenerationOutcome) -> dict:
    scored = [r for r in rows if r.fitness is not None]
    by_decision: dict[str, int] = {}
    for d in outcome.decisions:
        by_decision[d.decision] = by_decision.get(d.decision, 0) + 1
    return {
        "members": len(rows),
        "adequate": sum(1 for r in rows if r.evidence_class == fitness_mod.EVIDENCE_ADEQUATE),
        "insufficient": sum(
            1 for r in rows if r.evidence_class == fitness_mod.EVIDENCE_INSUFFICIENT
        ),
        "invalid": sum(1 for r in rows if r.evidence_class == fitness_mod.EVIDENCE_INVALID),
        "fitness_max": max((r.fitness for r in scored), default=None),
        "fitness_median": (
            sorted(r.fitness for r in scored)[len(scored) // 2] if scored else None
        ),
        "decisions": by_decision,
        "children": len(outcome.children),
        "proposal_refusals": len(outcome.refusals),
        "inert_children": len(outcome.inert_children),
    }


def _detect_inert_children(session, *, program: EvoProgram, generation: EvoGeneration) -> list[dict]:
    """Children whose run produced the same trade tape as their parent's, this generation.

    A mutation that changes the genome but not the replay is not a failed experiment —
    it is a non-experiment. The child occupies a cohort slot and returns a number that
    is its parent's number, so ranking it says nothing about the gene that moved. Two
    causes, and the distinction matters to whoever picks this up: the engine may be
    unable to express the gene at all (a defect in the surface), or the value may have
    moved outside the range this corpus can resolve (a fact about the data)."""
    runs = {
        r.candidate_uuid: r
        for r in session.execute(
            select(EvoRun).where(
                EvoRun.generation_id == generation.id, EvoRun.status == "completed"
            )
        ).scalars()
    }
    out: list[dict] = []
    for candidate in session.execute(
        select(EvoCandidate).where(
            EvoCandidate.program_id == program.id,
            EvoCandidate.origin == "mutation",
            EvoCandidate.parent_uuid.is_not(None),
        )
    ).scalars():
        run, parent_run = runs.get(candidate.uuid), runs.get(candidate.parent_uuid)
        if run is None or parent_run is None:
            continue
        mine = (run.reproducibility_json or {}).get("outcome_fingerprint")
        theirs = (parent_run.reproducibility_json or {}).get("outcome_fingerprint")
        if not mine or mine != theirs:
            continue
        genome = current_genome(session, candidate)
        changed = [str(c.get("path")) for c in (genome.mutation_diff_json or [])] if genome else []
        out.append(
            {
                "candidate_uuid": candidate.uuid,
                "label": candidate.label,
                "parent_label": (candidate.lineage_json or {}).get("parent_label"),
                "changed": changed,
                "generation": generation.number,
                "fingerprint": mine,
            }
        )
    return out


def _measure_population(session, program: EvoProgram) -> diversity.DiversityReport:
    members = []
    for candidate in active_candidates(session, program.id):
        genome = current_genome(session, candidate)
        if genome is None:
            continue
        members.append(
            {
                "document": genome.document_json,
                "family": candidate.family,
                "parent_uuid": candidate.parent_uuid,
                "hash": genome.genome_hash,
            }
        )
    return diversity.measure(members)


# ---------------------------------------------------------------------------
# Reproduction
# ---------------------------------------------------------------------------

#: How many proposals to try before giving up on a parent. Small on purpose: a parent
#: that cannot produce an admissible child in this many tries is usually cornered
#: against the risk envelope or surrounded by near-duplicates, and that is worth
#: surfacing as a refusal rather than grinding through the space.
MAX_PROPOSAL_ATTEMPTS = 8


def reproduce(
    session,
    *,
    program: EvoProgram,
    generation: EvoGeneration,
    parent: EvoCandidate,
    existing_documents: list[dict],
) -> tuple[EvoCandidate | None, EvoGenomeVersion | None, object | None, dict | None]:
    """Propose, gate, and admit a child from a parent. The parent is untouched.

    Attempts escalate from exploit to explore: if small steps around the parent are all
    refused as near-duplicates, a larger step is the right response, and it is recorded
    as `explore` so the Tower can tell the two apart later."""
    parent_genome = current_genome(session, parent)
    if parent_genome is None:
        return None, None, None, {"reason": "parent has no current genome"}

    allowed = list(
        program.allowed_mutation_surface_json or genome_mod.MUTABLE_PATHS
    )
    last_refusal: dict | None = None

    for attempt in range(MAX_PROPOSAL_ATTEMPTS):
        kind = mutation.KIND_EXPLOIT if attempt < MAX_PROPOSAL_ATTEMPTS // 2 else mutation.KIND_EXPLORE
        proposal = mutation.propose_perturbation(
            parent_candidate_uuid=parent.uuid,
            parent_genome_id=parent_genome.id,
            document=parent_genome.document_json,
            allowed_paths=allowed,
            kind=kind,
            seed=int(generation.rng_seed),
            index=attempt,
            max_genes=2,
        )
        if proposal is None:
            last_refusal = {"reason": "no mutable gene applies to this genome"}
            continue

        admission = mutation.evaluate_proposal(
            proposal,
            parent_document=parent_genome.document_json,
            program=program,
            existing_documents=existing_documents,
            allowed_paths=allowed,
        )
        proposal_row = mutation.record_proposal(
            session, program=program, generation_number=generation.number,
            proposal=proposal, admission=admission,
        )
        if not admission.ok:
            last_refusal = {
                "reason": admission.reason,
                "stage": admission.stage,
                "proposal_id": proposal_row.id,
            }
            continue

        child_uuid_placeholder = _new_uuid()
        child_genome = mutation.admit_proposal(
            session,
            program=program,
            # The proposal was made in this generation, but the genome is born into the
            # next one — that is when it first runs. Stamping it with the current
            # generation would make it look like an unevaluated genome from a generation
            # that has already closed.
            generation_number=generation.number + 1,
            child_candidate_uuid=child_uuid_placeholder,
            parent_genome=parent_genome,
            proposal=proposal,
            proposal_row=proposal_row,
            # The writer re-runs the gates against these, rather than trusting the
            # advisory `admission` computed above.
            existing_documents=existing_documents,
            allowed_paths=allowed,
            # A child born from this generation carries its parent's evidence boundary:
            # it may never be evaluated on data earlier than what set the parent's rank.
            evidence_cutoff=generation.data_cutoff,
        )
        child, child_genome = create_candidate(
            session,
            program=program,
            document=admission.document or {},
            generation_number=generation.number + 1,
            origin="mutation",
            parent=parent,
            purpose=proposal.hypothesis,
            evidence_cutoff=generation.data_cutoff,
            genome=child_genome,
        )
        _inherit_lessons(session, program=program, parent=parent, child=child,
                         generation_number=generation.number + 1)
        return child, child_genome, proposal_row, None

    return None, None, None, last_refusal or {"reason": "no admissible proposal"}


def _inherit_lessons(
    session,
    *,
    program: EvoProgram,
    parent: EvoCandidate,
    child: EvoCandidate,
    generation_number: int,
) -> int:
    """Carry the parent's heritable lessons to the child, marked as inherited.

    Only entries flagged heritable cross — a parent's *conclusion* about a market
    ("longshots under 10c are toxic") is stale the moment the regime changes, while its
    *lesson* ("my stop fired on single prints; confirmation mattered") stays useful.
    Everything inherited is stamped with its origin so a child can tell the difference
    between what it learned and what it was told."""
    rows = list(
        session.execute(
            select(EvoJournalEntry).where(
                EvoJournalEntry.candidate_uuid == parent.uuid,
                EvoJournalEntry.heritable.is_(True),
                EvoJournalEntry.superseded_by.is_(None),
            )
        ).scalars()
    )
    for row in rows:
        session.add(
            EvoJournalEntry(
                program_id=program.id,
                candidate_uuid=child.uuid,
                generation_number=generation_number,
                kind=row.kind,
                topic=row.topic,
                body=row.body,
                evidence_json=row.evidence_json,
                heritable=True,
                inherited_from=parent.uuid,
            )
        )
    session.flush()
    return len(rows)


__all__ = [
    "DECISION_CONTINUE",
    "DECISION_ESCALATE",
    "DECISION_HOLD",
    "DECISION_REPRODUCE",
    "DECISION_RETIRE",
    "GenerationOutcome",
    "MAX_PROPOSAL_ATTEMPTS",
    "active_candidates",
    "create_candidate",
    "current_genome",
    "decide_generation",
    "evaluate_generation",
    "next_label",
    "open_generation",
    "population_documents",
    "reproduce",
    "run_generation",
]
