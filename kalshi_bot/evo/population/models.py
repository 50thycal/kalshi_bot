"""SQLAlchemy models for the Evo population layer (`evo_pop_*` tables).

Joins the same declarative Base as `kalshi_bot/models.py` and `kalshi_bot/evo/models.py`
so Alembic and the `create_all()` safety net see one metadata. Conventions match:
BigIntId autoincrement PKs, JSONType payloads, TS timestamps, integer-cent prices,
Numeric money.

Namespace note: the LLM-agent organism already owns `EvoAgent`, `EvoCohort` and
`EvoGenome` with different semantics. Nothing here reuses those names or those tables.
The population layer's durable identity is `EvoCandidate`; its evolving unit is
`EvoGenomeVersion`; its evaluation population is `EvoGeneration`.

Immutability is enforced by the service layer (nothing here exposes an update path for
an evaluated genome) and is checkable after the fact: `EvoGenomeVersion.genome_hash` is
a deterministic hash of the normalized document, so a mutated row stops matching it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...models import TS, Base, BigIntId, JSONType, utcnow

UUID_LEN = 36
HASH_LEN = 64


# ---------------------------------------------------------------------------
# Program — one evolutionary configuration
# ---------------------------------------------------------------------------


class EvoProgram(Base):
    """Top-level evolutionary run. Owns the policy every generation is evaluated
    under: cohort size, evaluation cadence, reproduction/retirement fractions, the
    allowed mutation surface, budgets, and the fitness weights.

    `platform_snapshot` is the Experiment OS snapshot fingerprint this program's
    evidence was produced under. It is recorded for provenance only — this layer never
    writes to Experiment OS (see the package docstring)."""

    __tablename__ = "evo_pop_programs"
    __table_args__ = (UniqueConstraint("key", name="uq_evo_pop_program_key"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active"
    )  # active | paused | ended
    objective: Mapped[str] = mapped_column(Text, nullable=False)

    # population policy
    cohort_target: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    reproduce_fraction: Mapped[float] = mapped_column(Float, nullable=False, default=0.30)
    continue_fraction: Mapped[float] = mapped_column(Float, nullable=False, default=0.40)
    retire_fraction: Mapped[float] = mapped_column(Float, nullable=False, default=0.30)

    # evaluation cadence: what one generation replays
    mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="replay"
    )  # replay | paper | shadow  (only replay is enabled in this phase)
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    window_days: Mapped[int | None] = mapped_column(Integer)

    # constraints
    starting_capital_usd: Mapped[float] = mapped_column(
        Numeric(14, 4), nullable=False, default=1000.0
    )
    min_trades_for_evidence: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    min_genome_distance: Mapped[float] = mapped_column(Float, nullable=False, default=0.02)
    max_runs_per_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=120)

    # allowlists / weights (JSON so the policy is data, not code)
    allowed_mutation_surface_json: Mapped[list | None] = mapped_column(JSONType)
    fitness_weights_json: Mapped[dict | None] = mapped_column(JSONType)
    policy_json: Mapped[dict | None] = mapped_column(JSONType)

    # provenance
    platform_snapshot: Mapped[str | None] = mapped_column(String(128))
    evaluator_revision: Mapped[str] = mapped_column(String(32), nullable=False)
    engine_revision: Mapped[str] = mapped_column(String(32), nullable=False)
    rng_seed: Mapped[int] = mapped_column(BigIntId, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(TS)
    ended_reason: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------
# Generation — one evaluation population over one environment/window
# ---------------------------------------------------------------------------


class EvoGeneration(Base):
    """One generation: a fixed set of candidates evaluated over one replay window
    under one policy snapshot.

    `data_cutoff` is the no-look-ahead boundary. Every run in this generation must
    replay data strictly at or before it, and a child born from this generation may
    never be evaluated on a window that ends at or before its own birth cutoff — that
    is what keeps a child from learning from evidence its parent's rank was set by."""

    __tablename__ = "evo_pop_generations"
    __table_args__ = (
        UniqueConstraint("program_id", "number", name="uq_evo_pop_generation_number"),
        Index("ix_evo_pop_generations_program", "program_id", "number"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    program_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("evo_pop_programs.id"), nullable=False
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open"
    )  # open | running | evaluated | closed
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="replay")

    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    window_start: Mapped[str | None] = mapped_column(String(10))  # ISO date
    window_end: Mapped[str | None] = mapped_column(String(10))  # ISO date
    data_cutoff: Mapped[str | None] = mapped_column(String(10))  # ISO date, inclusive

    rng_seed: Mapped[int] = mapped_column(BigIntId, nullable=False, default=0)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # cohort-level provenance + rollup (never a substitute for candidate ledgers)
    provenance_json: Mapped[dict | None] = mapped_column(JSONType)
    summary_json: Mapped[dict | None] = mapped_column(JSONType)
    diversity_json: Mapped[dict | None] = mapped_column(JSONType)

    started_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    evaluated_at: Mapped[datetime | None] = mapped_column(TS)
    closed_at: Mapped[datetime | None] = mapped_column(TS)


# ---------------------------------------------------------------------------
# Candidate — durable identity across generations
# ---------------------------------------------------------------------------


class EvoCandidate(Base):
    """A durable bot identity. It does not change; its genome lineage does.

    A candidate is never mutated in place by reproduction: a parent stays a valid
    candidate and a child is a new row with `parent_uuid` set."""

    __tablename__ = "evo_pop_candidates"
    __table_args__ = (
        UniqueConstraint("uuid", name="uq_evo_pop_candidate_uuid"),
        UniqueConstraint("program_id", "label", name="uq_evo_pop_candidate_label"),
        Index("ix_evo_pop_candidates_program_state", "program_id", "state"),
        Index("ix_evo_pop_candidates_parent", "parent_uuid"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    program_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("evo_pop_programs.id"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(48), nullable=False)  # display: agent-017

    birth_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_uuid: Mapped[str | None] = mapped_column(String(UUID_LEN))
    second_parent_uuid: Mapped[str | None] = mapped_column(String(UUID_LEN))
    origin: Mapped[str] = mapped_column(
        String(24), nullable=False, default="founder"
    )  # founder | mutation | crossover | injected
    generation_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active"
    )  # active | retired | invalid
    family: Mapped[str] = mapped_column(String(48), nullable=False, default="unassigned")
    purpose: Mapped[str | None] = mapped_column(Text)  # thesis this identity carries

    current_genome_id: Mapped[int | None] = mapped_column(BigIntId)
    lineage_json: Mapped[dict | None] = mapped_column(JSONType)

    # cumulative summary across all generations — a cache, never the ledger of record
    cumulative_json: Mapped[dict | None] = mapped_column(JSONType)

    retired_generation: Mapped[int | None] = mapped_column(Integer)
    retirement_reason: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Genome — immutable versioned strategy state
# ---------------------------------------------------------------------------


class EvoGenomeVersion(Base):
    """One immutable genome version. Never updated once `evaluated` is true.

    `document_json` is the normalized StrategySpec document; `genome_hash` is the
    deterministic hash of that normalization, so identity is content-addressed and
    tampering is detectable. `mutation_diff_json` records exactly which genes changed
    from the parent genome and to what — a mutation that only exists in prose is a
    defect this column exists to prevent."""

    __tablename__ = "evo_pop_genomes"
    __table_args__ = (
        UniqueConstraint("candidate_uuid", "version", name="uq_evo_pop_genome_version"),
        Index("ix_evo_pop_genomes_program_hash", "program_id", "genome_hash"),
        Index("ix_evo_pop_genomes_candidate", "candidate_uuid", "version"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    program_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("evo_pop_programs.id"), nullable=False
    )
    candidate_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    genome_hash: Mapped[str] = mapped_column(String(HASH_LEN), nullable=False)
    document_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    family: Mapped[str] = mapped_column(String(48), nullable=False, default="unassigned")

    parent_genome_id: Mapped[int | None] = mapped_column(BigIntId)
    second_parent_genome_id: Mapped[int | None] = mapped_column(BigIntId)
    mutation_source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="founder"
    )  # founder | sweep | perturbation | research | llm | crossover | injected
    mutation_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="seed"
    )  # seed | exploit | explore
    mutation_diff_json: Mapped[list | None] = mapped_column(JSONType)
    proposal_id: Mapped[int | None] = mapped_column(BigIntId)

    hypothesis: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)

    # constraints carried by the genome itself
    universe_json: Mapped[dict | None] = mapped_column(JSONType)
    risk_json: Mapped[dict | None] = mapped_column(JSONType)

    # provenance
    born_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_cutoff: Mapped[str | None] = mapped_column(String(10))
    platform_snapshot: Mapped[str | None] = mapped_column(String(128))
    model_revision: Mapped[str | None] = mapped_column(String(64))

    evaluated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Mutation proposals — PROPOSE, distinct from ACCEPT
# ---------------------------------------------------------------------------


class EvoMutationProposal(Base):
    """A proposed change to a parent genome, recorded whether or not it is admitted.

    Rejected proposals are kept deliberately: "we tried that and the validator refused
    it" is evidence, and without it the same invalid mutation is proposed forever."""

    __tablename__ = "evo_pop_mutation_proposals"
    __table_args__ = (
        Index("ix_evo_pop_proposals_program_gen", "program_id", "generation_number"),
        Index("ix_evo_pop_proposals_parent", "parent_genome_id"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    program_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("evo_pop_programs.id"), nullable=False
    )
    generation_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_candidate_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    parent_genome_id: Mapped[int] = mapped_column(BigIntId, nullable=False)

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # exploit | explore
    changes_json: Mapped[list] = mapped_column(JSONType, nullable=False)
    hypothesis: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="proposed"
    )  # proposed | accepted | rejected
    reject_stage: Mapped[str | None] = mapped_column(String(24))
    # schema | compatibility | risk | novelty | admission
    reject_reason: Mapped[str | None] = mapped_column(Text)
    resulting_genome_id: Mapped[int | None] = mapped_column(BigIntId)
    proposed_hash: Mapped[str | None] = mapped_column(String(HASH_LEN))
    nearest_distance: Mapped[float | None] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Runs — one genome × one environment/window
# ---------------------------------------------------------------------------


class EvoRun(Base):
    """One evaluation of one genome against one window.

    `reproducibility_json` carries everything needed to reproduce the number: engine
    revision, dataset provenance, fill-calibration version, spec hash, seed and row
    counts. `outcome` separates the three quantities the handoff insists stay distinct:
    theoretical opportunity, paper execution, and the fill-adjusted realizable
    estimate."""

    __tablename__ = "evo_pop_runs"
    __table_args__ = (
        UniqueConstraint(
            "generation_id", "candidate_uuid", name="uq_evo_pop_run_generation_candidate"
        ),
        Index("ix_evo_pop_runs_program_gen", "program_id", "generation_id"),
        Index("ix_evo_pop_runs_genome", "genome_id"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    program_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("evo_pop_programs.id"), nullable=False
    )
    generation_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("evo_pop_generations.id"), nullable=False
    )
    generation_number: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    genome_id: Mapped[int] = mapped_column(BigIntId, nullable=False)
    genome_hash: Mapped[str] = mapped_column(String(HASH_LEN), nullable=False)

    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="replay")
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    provenance: Mapped[str | None] = mapped_column(String(64))
    window_start: Mapped[str | None] = mapped_column(String(10))
    window_end: Mapped[str | None] = mapped_column(String(10))

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="completed"
    )  # completed | failed | refused
    error: Mapped[str | None] = mapped_column(Text)

    starting_capital_usd: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    rng_seed: Mapped[int] = mapped_column(BigIntId, nullable=False, default=0)

    outcome_json: Mapped[dict | None] = mapped_column(JSONType)
    reproducibility_json: Mapped[dict | None] = mapped_column(JSONType)
    integrity_json: Mapped[dict | None] = mapped_column(JSONType)

    rows_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    elapsed_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class EvoRunTrade(Base):
    """One closed virtual trade in a run's tape. The tape is what makes "why did
    candidate X outperform candidate Y?" answerable rather than only rankable."""

    __tablename__ = "evo_pop_run_trades"
    __table_args__ = (Index("ix_evo_pop_run_trades_run", "run_id", "id"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("evo_pop_runs.id"), nullable=False
    )
    market_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    event_root: Mapped[str | None] = mapped_column(String(64))
    month: Mapped[str | None] = mapped_column(String(7))
    side: Mapped[str | None] = mapped_column(String(4))
    style: Mapped[str | None] = mapped_column(String(8))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    entry_price_cents: Mapped[float | None] = mapped_column(Float)
    exit_price_cents: Mapped[float | None] = mapped_column(Float)
    entered_at: Mapped[datetime | None] = mapped_column(TS)
    exited_at: Mapped[datetime | None] = mapped_column(TS)
    fees_usd: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    pnl_usd: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    cents_per_contract: Mapped[float | None] = mapped_column(Float)
    maker_yes_c: Mapped[float | None] = mapped_column(Float)
    exit_reason: Mapped[str | None] = mapped_column(String(32))
    settled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    win: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class EvoCandidateLedger(Base):
    """One candidate's own virtual account for one generation.

    Every candidate has its own; a cohort number is an aggregate of these and never a
    substitute for them. Kept separate from `EvoRun.outcome_json` because the ledger is
    the accounting view (capital, exposure, turnover, concentration) while the outcome
    is the measurement view (edge, drawdown, realizable projection)."""

    __tablename__ = "evo_pop_ledgers"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_evo_pop_ledger_run"),
        Index("ix_evo_pop_ledgers_candidate", "candidate_uuid", "generation_number"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    program_id: Mapped[int] = mapped_column(BigIntId, nullable=False)
    generation_number: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    run_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("evo_pop_runs.id"), nullable=False
    )

    starting_capital_usd: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    realized_pnl_usd: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    unrealized_pnl_usd: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    fees_usd: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    ending_capital_usd: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)

    peak_exposure_usd: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    turnover_usd: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    max_drawdown_usd: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    max_concurrent_positions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    contracts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    markets: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trades_settled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trades_open: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    concentration_top_family: Mapped[float | None] = mapped_column(Float)
    concentration_hhi: Mapped[float | None] = mapped_column(Float)
    capital_breached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    detail_json: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Fitness — components persisted beside the derived score
# ---------------------------------------------------------------------------


class EvoFitness(Base):
    """A candidate's evaluation for one generation.

    `components_json` holds every raw metric and its normalized sub-score, so the
    Control Tower can say *why* a candidate ranked where it did rather than showing an
    opaque number. `evidence_class` separates "bad" from "not enough data": an
    ADEQUATE candidate can win or lose on performance, an INSUFFICIENT one cannot."""

    __tablename__ = "evo_pop_fitness"
    __table_args__ = (
        UniqueConstraint(
            "generation_id", "candidate_uuid", name="uq_evo_pop_fitness_gen_candidate"
        ),
        Index("ix_evo_pop_fitness_generation_rank", "generation_id", "rank"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    program_id: Mapped[int] = mapped_column(BigIntId, nullable=False)
    generation_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("evo_pop_generations.id"), nullable=False
    )
    generation_number: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    run_id: Mapped[int | None] = mapped_column(BigIntId)
    genome_hash: Mapped[str | None] = mapped_column(String(HASH_LEN))

    evidence_class: Mapped[str] = mapped_column(
        String(16), nullable=False, default="adequate"
    )  # adequate | insufficient | invalid
    n_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    fitness: Mapped[float | None] = mapped_column(Float)
    rank: Mapped[int | None] = mapped_column(Integer)
    rank_group: Mapped[str | None] = mapped_column(String(16))  # top | middle | bottom | held

    components_json: Mapped[dict | None] = mapped_column(JSONType)
    weights_json: Mapped[dict | None] = mapped_column(JSONType)
    evaluator_revision: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Decisions — the evolutionary record
# ---------------------------------------------------------------------------


class EvoDecision(Base):
    """A durable evolutionary decision. Nothing in this layer may retire, reproduce or
    hold a candidate without writing one of these first — a decision that exists only
    in a log is exactly the failure mode this table prevents."""

    __tablename__ = "evo_pop_decisions"
    __table_args__ = (
        Index("ix_evo_pop_decisions_generation", "generation_id", "id"),
        Index("ix_evo_pop_decisions_candidate", "candidate_uuid"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    program_id: Mapped[int] = mapped_column(BigIntId, nullable=False)
    generation_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("evo_pop_generations.id"), nullable=False
    )
    generation_number: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)

    decision: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # continue | reproduce | mutate | retire | hold | escalate

    rank: Mapped[int | None] = mapped_column(Integer)
    rank_group: Mapped[str | None] = mapped_column(String(16))
    fitness: Mapped[float | None] = mapped_column(Float)
    evidence_class: Mapped[str | None] = mapped_column(String(16))

    evidence_json: Mapped[dict | None] = mapped_column(JSONType)
    thresholds_json: Mapped[dict | None] = mapped_column(JSONType)
    evaluator_revision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    # reproduction linkage, when the decision creates one
    child_candidate_uuid: Mapped[str | None] = mapped_column(String(UUID_LEN))
    child_genome_id: Mapped[int | None] = mapped_column(BigIntId)
    proposal_id: Mapped[int | None] = mapped_column(BigIntId)

    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Memory — evidence-based, not a transcript
# ---------------------------------------------------------------------------


class EvoJournalEntry(Base):
    """A candidate's durable memory. `kind` keeps the four registers apart on purpose:
    an observation is not an interpretation, and a hypothesis is not a decision. A
    child inherits entries whose `heritable` flag is set — so a parent's *lesson* can
    cross a generation boundary without its stale *conclusion* crossing with it."""

    __tablename__ = "evo_pop_journal"
    __table_args__ = (
        Index("ix_evo_pop_journal_candidate", "candidate_uuid", "id"),
        Index("ix_evo_pop_journal_program_gen", "program_id", "generation_number"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    program_id: Mapped[int] = mapped_column(BigIntId, nullable=False)
    candidate_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    generation_number: Mapped[int] = mapped_column(Integer, nullable=False)

    kind: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # observation | interpretation | hypothesis | decision | lesson | failure_mode
    topic: Mapped[str | None] = mapped_column(String(64))
    body: Mapped[str] = mapped_column(Text, nullable=False)

    genome_hash: Mapped[str | None] = mapped_column(String(HASH_LEN))
    run_id: Mapped[int | None] = mapped_column(BigIntId)
    evidence_json: Mapped[dict | None] = mapped_column(JSONType)

    heritable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    inherited_from: Mapped[str | None] = mapped_column(String(UUID_LEN))
    superseded_by: Mapped[int | None] = mapped_column(BigIntId)

    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Findings — the Evo Ticket Workshop's work items
# ---------------------------------------------------------------------------


class EvoFinding(Base):
    """A durable population-level work item: a replay defect, an invalid genome, a
    diversity collapse, an unexplained performance gap, a research question.

    Distinct from the fleet's `evo_tickets` (agent capability requests) and from
    Experiment OS issues (anomalies in *experiments*). `route_to` names the role that
    owns the problem — this layer has no fixer role of its own, and a finding never
    changes a lifecycle state, a gate or a verdict as a side effect."""

    __tablename__ = "evo_pop_findings"
    __table_args__ = (
        UniqueConstraint("program_id", "dedup_key", name="uq_evo_pop_finding_dedup"),
        Index("ix_evo_pop_findings_status", "program_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    program_id: Mapped[int] = mapped_column(BigIntId, nullable=False)
    generation_number: Mapped[int | None] = mapped_column(Integer)
    candidate_uuid: Mapped[str | None] = mapped_column(String(UUID_LEN))

    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(
        String(12), nullable=False, default="info"
    )  # info | warn | critical
    dedup_key: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    evidence_json: Mapped[dict | None] = mapped_column(JSONType)

    route_to: Mapped[str] = mapped_column(
        String(32), nullable=False, default="evo_ticket_workshop"
    )  # evo_ticket_workshop | research_lab | experiment_os_issue
    #   | platform_change_review | mutation_candidate
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open"
    )  # open | acknowledged | routed | resolved | rejected
    resolution: Mapped[str | None] = mapped_column(Text)
    external_ref: Mapped[str | None] = mapped_column(String(64))  # e.g. XOS-000123

    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
