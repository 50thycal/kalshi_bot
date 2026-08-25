"""The public API: create a program, seed it, advance a generation.

Everything a caller should need is here. The modules underneath are deliberately
callable on their own for testing, but a session driving Evo goes through these
functions, because they are where the ordering invariants live:

    run → evaluate → decide → scan for findings → journal

Running before evaluating, or deciding before evaluating, would produce decisions
against stale fitness. The order is enforced here rather than documented and hoped for.

Two things this module will not do, and neither is an oversight:

* **It cannot arm anything.** There is no live mode, no order path, no deployment call.
  `EvoProgram.mode` accepts only `replay` in this phase; `paper` and `shadow` are
  reserved and refused.
* **It cannot promote into Experiment OS.** A candidate that earns advancement enters
  the normal XOS path through an XOS session with the authority to register it. This
  layer records the platform snapshot it ran under and stops there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select

from ..config import EvoSettings
from . import evolution, findings, mutation, replay
from . import fitness as fitness_mod
from . import genome as genome_mod
from .models import (
    EvoCandidate,
    EvoDecision,
    EvoFitness,
    EvoGeneration,
    EvoJournalEntry,
    EvoProgram,
    EvoRun,
)

SUPPORTED_MODES = ("replay",)
RESERVED_MODES = ("paper", "shadow")


class EvoPopulationError(Exception):
    """A refused operation. Never raised for a bad result — only a bad request."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Programs
# ---------------------------------------------------------------------------


def create_program(
    session,
    *,
    key: str,
    name: str,
    objective: str,
    dataset: str,
    cohort_target: int = 30,
    starting_capital_usd: float = 1000.0,
    min_trades_for_evidence: int = 30,
    min_genome_distance: float = 0.02,
    reproduce_fraction: float = 0.30,
    continue_fraction: float = 0.40,
    retire_fraction: float = 0.30,
    mode: str = "replay",
    allowed_mutation_surface: list[str] | None = None,
    fitness_weights: dict | None = None,
    policy: dict | None = None,
    platform_snapshot: str | None = None,
    rng_seed: int = 0,
) -> EvoProgram:
    """Create a program. Refuses a configuration that cannot mean what it says."""
    if mode in RESERVED_MODES:
        raise EvoPopulationError(
            f"mode {mode!r} is reserved and not enabled in this phase — only "
            f"{SUPPORTED_MODES} runs. Prospective paper cohorts need the scheduler and "
            "the time-boundary guarantees designed but not built here."
        )
    if mode not in SUPPORTED_MODES:
        raise EvoPopulationError(f"unknown mode {mode!r} (supported: {SUPPORTED_MODES})")

    total = reproduce_fraction + retire_fraction
    if total > 1.0:
        raise EvoPopulationError(
            f"reproduce ({reproduce_fraction}) + retire ({retire_fraction}) = {total} "
            "exceeds the whole population"
        )
    surface = list(allowed_mutation_surface or genome_mod.MUTABLE_PATHS)
    unknown = [p for p in surface if p not in genome_mod.GENES_BY_PATH]
    if unknown:
        raise EvoPopulationError(f"unknown genes in the allowed mutation surface: {unknown}")

    if session.execute(
        select(EvoProgram).where(EvoProgram.key == key)
    ).scalar_one_or_none() is not None:
        raise EvoPopulationError(f"program {key!r} already exists")

    program = EvoProgram(
        key=key,
        name=name,
        objective=objective,
        status="active",
        mode=mode,
        dataset=dataset,
        cohort_target=cohort_target,
        reproduce_fraction=reproduce_fraction,
        continue_fraction=continue_fraction,
        retire_fraction=retire_fraction,
        starting_capital_usd=starting_capital_usd,
        min_trades_for_evidence=min_trades_for_evidence,
        min_genome_distance=min_genome_distance,
        allowed_mutation_surface_json=surface,
        fitness_weights_json=fitness_weights or {},
        policy_json=policy or {},
        platform_snapshot=platform_snapshot,
        evaluator_revision=fitness_mod.EVALUATOR_REVISION,
        engine_revision=replay.ENGINE_REVISION,
        rng_seed=rng_seed,
    )
    session.add(program)
    session.flush()
    return program


def get_program(session, key: str) -> EvoProgram | None:
    return session.execute(
        select(EvoProgram).where(EvoProgram.key == key)
    ).scalar_one_or_none()


def seed_founders(
    session,
    *,
    program: EvoProgram,
    documents: list[dict],
    generation_number: int = 0,
    purposes: list[str] | None = None,
) -> list[EvoCandidate]:
    """Create the founder population.

    Founders are admitted through the same novelty floor as children: seeding thirty
    near-identical genomes would start the program already collapsed, and the duplicate
    check is the only thing that catches a seed generator with a narrow imagination."""
    created: list[EvoCandidate] = []
    existing = evolution.population_documents(session, program.id)
    for index, doc in enumerate(documents):
        norm, err = genome_mod.validate(doc)
        if err or norm is None:
            raise EvoPopulationError(f"founder #{index} is invalid: {err}")
        from . import diversity

        ok, _, reason = diversity.novelty_check(
            norm, existing, min_distance=float(program.min_genome_distance)
        )
        if not ok:
            raise EvoPopulationError(f"founder #{index} refused: {reason}")
        purpose = (purposes or [None] * len(documents))[index] if purposes else None
        candidate, _genome = evolution.create_candidate(
            session,
            program=program,
            document=norm,
            generation_number=generation_number,
            origin="founder",
            purpose=purpose,
        )
        existing.append(norm)
        created.append(candidate)
    return created


# ---------------------------------------------------------------------------
# Generations
# ---------------------------------------------------------------------------


@dataclass
class AdvanceResult:
    generation: EvoGeneration
    runs: list[EvoRun] = field(default_factory=list)
    fitness_rows: list[EvoFitness] = field(default_factory=list)
    decisions: list[EvoDecision] = field(default_factory=list)
    children: list[EvoCandidate] = field(default_factory=list)
    findings: list = field(default_factory=list)
    diversity: dict = field(default_factory=dict)
    diversity_inert: list = field(default_factory=list)

    def summary(self) -> dict:
        return dict(self.generation.summary_json or {})


def advance(
    session,
    settings: EvoSettings,
    *,
    program: EvoProgram,
    window_start: str | None,
    window_end: str | None,
    data_cutoff: str | None = None,
    number: int | None = None,
    max_children: int | None = None,
    write_journal: bool = True,
) -> AdvanceResult:
    """Run one whole generation, start to finish.

    The ordering here is the contract: nothing is decided before everything is
    evaluated, and nothing is evaluated before every run has completed. A generation
    that decided as it went would let an early retirement change the denominator the
    later ranks were computed against."""
    if program.status != "active":
        raise EvoPopulationError(f"program {program.key!r} is {program.status}, not active")

    next_number = number if number is not None else _next_generation_number(session, program.id)
    active = evolution.active_candidates(session, program.id)
    if not active:
        raise EvoPopulationError(
            f"program {program.key!r} has no active candidates — seed founders first"
        )

    generation = evolution.open_generation(
        session,
        program=program,
        number=next_number,
        window_start=window_start,
        window_end=window_end,
        data_cutoff=data_cutoff,
    )
    runs = evolution.run_generation(
        session, settings, program=program, generation=generation
    )
    fitness_rows = evolution.evaluate_generation(
        session, program=program, generation=generation
    )
    outcome = evolution.decide_generation(
        session, program=program, generation=generation, max_children=max_children
    )
    detected = findings.scan_generation(
        session,
        program=program,
        generation=generation,
        fitness_rows=fitness_rows,
        outcome=outcome,
    )
    if write_journal:
        _write_journal(session, program=program, generation=generation, rows=fitness_rows)

    return AdvanceResult(
        generation=generation,
        runs=runs,
        fitness_rows=fitness_rows,
        decisions=outcome.decisions,
        children=outcome.children,
        findings=detected,
        diversity=outcome.diversity,
        diversity_inert=outcome.inert_children,
    )


def _next_generation_number(session, program_id: int) -> int:
    last = session.execute(
        select(EvoGeneration.number)
        .where(EvoGeneration.program_id == program_id)
        .order_by(EvoGeneration.number.desc())
        .limit(1)
    ).scalar_one_or_none()
    return 0 if last is None else int(last) + 1


def _write_journal(
    session, *, program: EvoProgram, generation: EvoGeneration, rows: list[EvoFitness]
) -> int:
    """Record what each candidate observed this generation, in the four registers.

    An observation is what the run measured; an interpretation is what the evaluator
    made of it. They are separate rows because conflating them is how a child inherits
    "longshots are toxic" as a fact when what actually happened was one cohort, one
    window, one measurement."""
    written = 0
    for row in rows:
        outcome = _run_outcome(session, row.run_id)
        session.add(
            EvoJournalEntry(
                program_id=program.id,
                candidate_uuid=row.candidate_uuid,
                generation_number=generation.number,
                kind="observation",
                topic="generation_result",
                body=(
                    f"generation {generation.number} on {generation.dataset} "
                    f"[{generation.window_start}..{generation.window_end}]: "
                    f"n={row.n_trades}, net ${outcome.get('net_pnl_usd', 0):,.2f}, "
                    f"per-contract {outcome.get('per_trade_cents_per_contract')}c, "
                    f"max drawdown ${outcome.get('max_drawdown_usd', 0):,.2f}"
                ),
                genome_hash=row.genome_hash,
                run_id=row.run_id,
                evidence_json={"outcome": outcome},
            )
        )
        written += 1
        if row.fitness is not None:
            session.add(
                EvoJournalEntry(
                    program_id=program.id,
                    candidate_uuid=row.candidate_uuid,
                    generation_number=generation.number,
                    kind="interpretation",
                    topic="fitness",
                    body=(
                        f"ranked {row.rank or '—'} ({row.rank_group}) at fitness "
                        f"{row.fitness:.4f}. "
                        + fitness_mod.explain(row.components_json)
                    ),
                    genome_hash=row.genome_hash,
                    run_id=row.run_id,
                    evidence_json={"components": row.components_json},
                )
            )
            written += 1
        elif row.evidence_class != fitness_mod.EVIDENCE_ADEQUATE:
            session.add(
                EvoJournalEntry(
                    program_id=program.id,
                    candidate_uuid=row.candidate_uuid,
                    generation_number=generation.number,
                    kind="failure_mode",
                    topic=row.evidence_class,
                    body=f"not ranked: {row.notes}",
                    genome_hash=row.genome_hash,
                    run_id=row.run_id,
                    heritable=True,
                )
            )
            written += 1
    session.flush()
    return written


def _run_outcome(session, run_id: int | None) -> dict:
    if run_id is None:
        return {}
    run = session.get(EvoRun, run_id)
    return (run.outcome_json or {}) if run else {}


# ---------------------------------------------------------------------------
# Manual proposals — the seam an LLM proposer plugs into
# ---------------------------------------------------------------------------


def propose_and_admit(
    session,
    *,
    program: EvoProgram,
    generation_number: int,
    parent: EvoCandidate,
    path: str,
    value,
    hypothesis: str,
    rationale: str,
    source: str = mutation.SOURCE_RESEARCH,
) -> tuple[EvoCandidate | None, str | None]:
    """Admit an externally-sourced mutation — a research finding, or an LLM proposal.

    Identical gating to the automatic path. That is the whole point: an external
    proposer supplies a `(gene, value)` pair and a hypothesis, and inherits every check.
    It cannot reach `admit_proposal` on its own, and it cannot express a change outside
    the gene surface."""
    parent_genome = evolution.current_genome(session, parent)
    if parent_genome is None:
        return None, "parent has no current genome"

    proposal = mutation.propose_sweep(
        parent_candidate_uuid=parent.uuid,
        parent_genome_id=parent_genome.id,
        document=parent_genome.document_json,
        path=path,
        value=value,
        hypothesis=hypothesis,
        rationale=rationale,
    )
    if proposal is None:
        return None, f"no change: {path!r} is unknown or already {value!r}"
    proposal.source = source

    allowed = list(program.allowed_mutation_surface_json or genome_mod.MUTABLE_PATHS)
    admission = mutation.evaluate_proposal(
        proposal,
        parent_document=parent_genome.document_json,
        program=program,
        existing_documents=evolution.population_documents(session, program.id),
        allowed_paths=allowed,
    )
    proposal_row = mutation.record_proposal(
        session, program=program, generation_number=generation_number,
        proposal=proposal, admission=admission,
    )
    if not admission.ok:
        return None, f"{admission.stage}: {admission.reason}"

    child_genome = mutation.admit_proposal(
        session,
        program=program,
        generation_number=generation_number,
        child_candidate_uuid="pending",
        parent_genome=parent_genome,
        proposal=proposal,
        admission=admission,
        proposal_row=proposal_row,
        evidence_cutoff=None,
    )
    child, _ = evolution.create_candidate(
        session,
        program=program,
        document=admission.document or {},
        generation_number=generation_number + 1,
        origin="mutation",
        parent=parent,
        purpose=hypothesis,
        genome=child_genome,
    )
    return child, None


__all__ = [
    "AdvanceResult",
    "EvoPopulationError",
    "RESERVED_MODES",
    "SUPPORTED_MODES",
    "advance",
    "create_program",
    "get_program",
    "propose_and_admit",
    "seed_founders",
]
