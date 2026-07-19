"""SQLAlchemy models for the evolutionary agent system (evo_* tables).

Joins the existing declarative Base/metadata (kalshi_bot.models) so Alembic and the
create_all() safety net see these tables alongside the legacy schema. Conventions
match models.py: BigIntId autoincrement PKs, JSONType payloads, TS timestamps,
integer-cent prices, Numeric money.

Identity rule: agents are identified by `agent_uuid` (String(36)) everywhere; names
are display-only and never used as keys. Immutability rules (genome versions, factual
memory, journals, transitions) are enforced by the API layer — nothing here exposes an
update path for them — and audited.
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

from ..models import TS, Base, BigIntId, JSONType, utcnow

UUID_LEN = 36


# ---------------------------------------------------------------------------
# System configuration / constitution
# ---------------------------------------------------------------------------


class EvoConfigVersion(Base):
    """Versioned snapshot of the effective system configuration: constitution hash,
    fitness weights, population parameters. Cohorts record which version they ran
    under, so every historical score is reproducible."""

    __tablename__ = "evo_config_versions"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    constitution_version: Mapped[str] = mapped_column(String(16), nullable=False)
    constitution_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    fitness_config_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    population_config_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    resource_config_json: Mapped[dict] = mapped_column(JSONType, nullable=False)


class EvoModelPrice(Base):
    """Configurable per-model pricing (USD per million tokens). No hardcoded prices:
    cost accounting joins against the newest active row per alias."""

    __tablename__ = "evo_model_prices"
    __table_args__ = (Index("ix_evo_model_prices_alias", "alias", "effective_at"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    alias: Mapped[str] = mapped_column(String(32), nullable=False)  # 'routine' | 'deep' | raw id
    model_id: Mapped[str] = mapped_column(String(64), nullable=False)
    input_usd_per_mtok: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    output_usd_per_mtok: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    cached_input_usd_per_mtok: Mapped[float | None] = mapped_column(Numeric(10, 4))
    effective_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# ---------------------------------------------------------------------------
# Agents, cohorts, lineage
# ---------------------------------------------------------------------------


class EvoAgent(Base):
    __tablename__ = "evo_agents"
    __table_args__ = (
        UniqueConstraint("agent_uuid", name="uq_evo_agent_uuid"),
        Index("ix_evo_agents_status", "status"),
        Index("ix_evo_agents_family", "surname"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    agent_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)

    first_name: Mapped[str] = mapped_column(String(32), nullable=False)
    surname: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(96), nullable=False)  # unique-while-active
    historical_name: Mapped[str] = mapped_column(String(96), nullable=False)  # permanent
    agent_code: Mapped[str] = mapped_column(String(24), nullable=False)  # e.g. KEP-G3-018

    founder_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    parent_uuid: Mapped[str | None] = mapped_column(String(UUID_LEN))
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)  # founder|child|wildcard
    birth_cohort_id: Mapped[int] = mapped_column(BigIntId, ForeignKey("evo_cohorts.id"))

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active"
    )  # active | retired | suspended
    status_reason: Mapped[str | None] = mapped_column(Text)
    retired_at: Mapped[datetime | None] = mapped_column(TS)

    cognitive_genome_rev: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    trading_genome_rev: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    working_state_json: Mapped[dict | None] = mapped_column(JSONType)  # spec §7.4


class EvoCohort(Base):
    __tablename__ = "evo_cohorts"
    __table_args__ = (UniqueConstraint("number", name="uq_evo_cohort_number"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(TS, nullable=False)  # birth (cohort creation)
    ends_at: Mapped[datetime] = mapped_column(TS, nullable=False)  # starts_at + cohort_days
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open"
    )  # open | finalizing | finalized
    config_version_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("evo_config_versions.id")
    )
    rng_seed: Mapped[int] = mapped_column(BigIntId, nullable=False, default=0)
    wildcard_cohort: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class EvoCohortMember(Base):
    __tablename__ = "evo_cohort_members"
    __table_args__ = (
        UniqueConstraint("cohort_id", "agent_uuid", name="uq_evo_cohort_member"),
        Index("ix_evo_cohort_members_agent", "agent_uuid"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    cohort_id: Mapped[int] = mapped_column(BigIntId, ForeignKey("evo_cohorts.id"), nullable=False)
    agent_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    starting_capital: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    carried_scale: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    # finalization outputs
    final_rank: Mapped[int | None] = mapped_column(Integer)
    final_group: Mapped[str | None] = mapped_column(String(8))  # top | middle | bottom
    cohort_score: Mapped[float | None] = mapped_column(Float)
    reliability_score: Mapped[float | None] = mapped_column(Float)
    selection_score: Mapped[float | None] = mapped_column(Float)


class EvoInfluence(Base):
    """Horizontal idea-transfer edge (peer learning); never affects surname/lineage."""

    __tablename__ = "evo_influences"
    __table_args__ = (
        Index("ix_evo_influences_receiver", "receiving_uuid", "created_at"),
        Index("ix_evo_influences_source", "source_uuid"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    source_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    receiving_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    source_family: Mapped[str | None] = mapped_column(String(32))
    receiving_family: Mapped[str | None] = mapped_column(String(32))
    concept: Mapped[str] = mapped_column(String(64), nullable=False)  # copied_listener, ...
    description: Mapped[str | None] = mapped_column(Text)
    evidence_json: Mapped[dict | None] = mapped_column(JSONType)
    interpretation: Mapped[str | None] = mapped_column(Text)
    modifications: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(String(16))  # adopted|rejected|improved|pending
    heartbeat_id: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("evo_heartbeats.id"))


# ---------------------------------------------------------------------------
# Genomes (immutable versions)
# ---------------------------------------------------------------------------


class EvoGenome(Base):
    """One immutable revision of an agent's cognitive or trading genome. Never
    updated in place: a material change inserts revision N+1; rollbacks insert a new
    revision copying an older document (history preserved)."""

    __tablename__ = "evo_genomes"
    __table_args__ = (
        UniqueConstraint("agent_uuid", "kind", "revision", name="uq_evo_genome_rev"),
        Index("ix_evo_genomes_agent_kind", "agent_uuid", "kind"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    agent_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)  # cognitive | trading
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_revision: Mapped[int | None] = mapped_column(Integer)
    document_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    changed_fields_json: Mapped[list | None] = mapped_column(JSONType)
    hypothesis: Mapped[str | None] = mapped_column(Text)
    evidence_for: Mapped[str | None] = mapped_column(Text)
    evidence_against: Mapped[str | None] = mapped_column(Text)
    expected_benefit: Mapped[str | None] = mapped_column(Text)
    potential_downside: Mapped[str | None] = mapped_column(Text)
    rollback_conditions: Mapped[str | None] = mapped_column(Text)
    is_rollback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rollback_of_revision: Mapped[int | None] = mapped_column(Integer)
    material: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    heartbeat_id: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("evo_heartbeats.id"))


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


class EvoMemory(Base):
    """One memory record. kind: fact (immutable) | belief (supersession chain) |
    episode | experiment | journal. The API layer refuses updates to immutable kinds;
    belief revision inserts a new row with supersedes_id (never mutates)."""

    __tablename__ = "evo_memories"
    __table_args__ = (
        UniqueConstraint("agent_uuid", "idem_key", name="uq_evo_memory_idem"),
        Index("ix_evo_memories_agent_kind_time", "agent_uuid", "kind", "created_at"),
        Index("ix_evo_memories_tags", "tag1", "tag2"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    agent_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    immutable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    idem_key: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    # retrieval features
    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)  # 0..1
    confidence: Mapped[float | None] = mapped_column(Float)  # 0..1 (beliefs/experiments)
    tag1: Mapped[str | None] = mapped_column(String(64))  # market/strategy scope
    tag2: Mapped[str | None] = mapped_column(String(64))  # regime/topic scope
    contradicts_id: Mapped[int | None] = mapped_column(BigIntId)
    # belief supersession chain (editable-by-supersession; old row untouched)
    supersedes_id: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("evo_memories.id"))
    superseded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    heartbeat_id: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("evo_heartbeats.id"))
    # inheritance: set on rows copied into a child at birth
    inherited_from_uuid: Mapped[str | None] = mapped_column(String(UUID_LEN))


class EvoExperiment(Base):
    """Pre-registered experiment. The hypothesis block is written at registration and
    never updated; concluding fills the result block once (API enforces write-once).
    A revised hypothesis is a NEW experiment row."""

    __tablename__ = "evo_experiments"
    __table_args__ = (
        UniqueConstraint("agent_uuid", "idem_key", name="uq_evo_experiment_idem"),
        Index("ix_evo_experiments_agent", "agent_uuid", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    agent_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    idem_key: Mapped[str | None] = mapped_column(String(128))
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    expected_result: Mapped[str | None] = mapped_column(Text)
    falsifiable_prediction: Mapped[str | None] = mapped_column(Text)
    dataset: Mapped[str | None] = mapped_column(String(128))
    provenance: Mapped[str | None] = mapped_column(String(64))
    date_range: Mapped[str | None] = mapped_column(String(64))
    no_lookahead_note: Mapped[str | None] = mapped_column(Text)
    strategy_revision: Mapped[int | None] = mapped_column(Integer)
    cognitive_revision: Mapped[int | None] = mapped_column(Integer)
    parameters_json: Mapped[dict | None] = mapped_column(JSONType)
    promotion_criteria: Mapped[str | None] = mapped_column(Text)
    kill_criteria: Mapped[str | None] = mapped_column(Text)
    heartbeat_id: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("evo_heartbeats.id"))
    # conclusion block (write-once)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    result_json: Mapped[dict | None] = mapped_column(JSONType)
    result_confidence: Mapped[float | None] = mapped_column(Float)
    interpretation: Mapped[str | None] = mapped_column(Text)
    follow_up: Mapped[str | None] = mapped_column(Text)
    concluded_at: Mapped[datetime | None] = mapped_column(TS)


# ---------------------------------------------------------------------------
# Heartbeats + listeners
# ---------------------------------------------------------------------------


class EvoHeartbeat(Base):
    __tablename__ = "evo_heartbeats"
    __table_args__ = (
        UniqueConstraint("idem_key", name="uq_evo_heartbeat_idem"),
        Index("ix_evo_heartbeats_agent_time", "agent_uuid", "created_at"),
        Index("ix_evo_heartbeats_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    agent_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    cohort_id: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("evo_cohorts.id"))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # routine | reflection | triggered | birth | cohort_end | retirement | recovery
    idem_key: Mapped[str] = mapped_column(String(128), nullable=False)  # agent:kind:slot
    scheduled_for: Mapped[datetime | None] = mapped_column(TS)
    trigger_reason: Mapped[str | None] = mapped_column(Text)
    listener_event_id: Mapped[int | None] = mapped_column(BigIntId)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    # running | completed | skipped | degraded | abandoned | error
    status_detail: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(TS)
    model_alias: Mapped[str | None] = mapped_column(String(32))
    model_id: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False, default=0)
    actions_json: Mapped[list | None] = mapped_column(JSONType)  # validated action summaries
    journal_memory_id: Mapped[int | None] = mapped_column(BigIntId)


class EvoListener(Base):
    __tablename__ = "evo_listeners"
    __table_args__ = (
        Index("ix_evo_listeners_owner", "owner_uuid", "status"),
        Index("ix_evo_listeners_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    owner_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    created_heartbeat_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("evo_heartbeats.id")
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str | None] = mapped_column(Text)
    condition_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    data_dependencies_json: Mapped[list | None] = mapped_column(JSONType)
    effect: Mapped[str] = mapped_column(
        String(24), nullable=False, default="event"
    )  # event | trigger_heartbeat | schedule_heartbeat | opportunity
    cooldown_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    expires_at: Mapped[datetime | None] = mapped_column(TS)
    expected_value_note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    # active | expired | removed
    last_triggered_at: Mapped[datetime | None] = mapped_column(TS)
    trigger_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    useful_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    useless_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class EvoListenerEvent(Base):
    __tablename__ = "evo_listener_events"
    __table_args__ = (
        UniqueConstraint("listener_id", "dedup_key", name="uq_evo_listener_event"),
        Index("ix_evo_listener_events_time", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    listener_id: Mapped[int] = mapped_column(BigIntId, ForeignKey("evo_listeners.id"))
    owner_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(160), nullable=False)
    payload_json: Mapped[dict | None] = mapped_column(JSONType)
    consumed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    consumed_by_heartbeat_id: Mapped[int | None] = mapped_column(BigIntId)


# ---------------------------------------------------------------------------
# Budgets + LLM usage
# ---------------------------------------------------------------------------


class EvoBudget(Base):
    __tablename__ = "evo_budgets"
    __table_args__ = (
        UniqueConstraint("agent_uuid", "cohort_id", "resource", name="uq_evo_budget"),
        Index("ix_evo_budgets_agent", "agent_uuid", "cohort_id"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    agent_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    cohort_id: Mapped[int] = mapped_column(BigIntId, ForeignKey("evo_cohorts.id"), nullable=False)
    resource: Mapped[str] = mapped_column(String(48), nullable=False)
    allocated: Mapped[float] = mapped_column(Numeric(14, 6), nullable=False)
    used: Mapped[float] = mapped_column(Numeric(14, 6), nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow, nullable=False)


class EvoLlmUsage(Base):
    __tablename__ = "evo_llm_usage"
    __table_args__ = (Index("ix_evo_llm_usage_agent_time", "agent_uuid", "created_at"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    agent_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    cohort_id: Mapped[int | None] = mapped_column(BigIntId)
    heartbeat_id: Mapped[int | None] = mapped_column(BigIntId)
    model_alias: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False, default=0)


# ---------------------------------------------------------------------------
# Data sources + health
# ---------------------------------------------------------------------------


class EvoDataSource(Base):
    __tablename__ = "evo_data_sources"
    __table_args__ = (UniqueConstraint("name", name="uq_evo_data_source_name"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    registered_by_uuid: Mapped[str | None] = mapped_column(String(UUID_LEN))
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64))
    retrieval_method: Mapped[str | None] = mapped_column(String(32))
    endpoint: Mapped[str | None] = mapped_column(Text)
    fields_json: Mapped[dict | None] = mapped_column(JSONType)
    provenance: Mapped[str | None] = mapped_column(String(64))
    update_frequency: Mapped[str | None] = mapped_column(String(32))
    observed_latency_seconds: Mapped[float | None] = mapped_column(Float)
    expected_latency_seconds: Mapped[float | None] = mapped_column(Float)
    cost_note: Mapped[str | None] = mapped_column(String(128))
    auth_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    usage_restrictions: Mapped[str | None] = mapped_column(Text)
    rate_limits: Mapped[str | None] = mapped_column(String(128))
    historical_availability: Mapped[str | None] = mapped_column(String(128))
    missing_data_behavior: Mapped[str | None] = mapped_column(String(128))
    validation_rules_json: Mapped[dict | None] = mapped_column(JSONType)
    reliability_score: Mapped[float | None] = mapped_column(Float)
    approved_usage: Mapped[str] = mapped_column(
        String(16), nullable=False, default="exploratory"
    )  # exploratory | operational | suspended
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")


class EvoDataHealthEvent(Base):
    __tablename__ = "evo_data_health_events"
    __table_args__ = (Index("ix_evo_data_health_time", "created_at"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(String(12), nullable=False)  # info|warn|error
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # stale|missing|invalid|outage
    detail_json: Mapped[dict | None] = mapped_column(JSONType)
    resolved_at: Mapped[datetime | None] = mapped_column(TS)


# ---------------------------------------------------------------------------
# Strategies (declarative artifacts)
# ---------------------------------------------------------------------------


class EvoStrategy(Base):
    """A versioned declarative strategy artifact (strategy_spec DSL). Immutable per
    revision; activation state lives on the row (deactivation writes status, never
    deletes)."""

    __tablename__ = "evo_strategies"
    __table_args__ = (
        UniqueConstraint("agent_uuid", "name", "revision", name="uq_evo_strategy_rev"),
        Index("ix_evo_strategies_agent", "agent_uuid", "status"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    agent_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    name: Mapped[str] = mapped_column(String(48), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    spec_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    validation_json: Mapped[dict | None] = mapped_column(JSONType)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft"
    )  # draft | validated | active | inactive | rejected
    heartbeat_id: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("evo_heartbeats.id"))
    forked_from_uuid: Mapped[str | None] = mapped_column(String(UUID_LEN))
    graveyard_check_json: Mapped[dict | None] = mapped_column(JSONType)  # spec §31 documentation


class EvoSandboxRun(Base):
    __tablename__ = "evo_sandbox_runs"
    __table_args__ = (Index("ix_evo_sandbox_runs_agent", "agent_uuid", "created_at"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    agent_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    heartbeat_id: Mapped[int | None] = mapped_column(BigIntId)
    strategy_id: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("evo_strategies.id"))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # probe|backtest|walkforward
    params_json: Mapped[dict | None] = mapped_column(JSONType)
    dataset: Mapped[str | None] = mapped_column(String(64))
    provenance: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="completed")
    result_json: Mapped[dict | None] = mapped_column(JSONType)
    rows_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    elapsed_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


# ---------------------------------------------------------------------------
# Paper trading: orders, fills, positions, ledgers
# ---------------------------------------------------------------------------


class EvoOrder(Base):
    """A paper-trade intent/order in an agent's independent counterfactual book."""

    __tablename__ = "evo_orders"
    __table_args__ = (
        UniqueConstraint("idem_key", name="uq_evo_order_idem"),
        Index("ix_evo_orders_agent_time", "agent_uuid", "created_at"),
        Index("ix_evo_orders_open", "status"),
        Index("ix_evo_orders_ticker", "market_ticker"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    agent_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    cohort_id: Mapped[int | None] = mapped_column(BigIntId)
    heartbeat_id: Mapped[int | None] = mapped_column(BigIntId)
    strategy_id: Mapped[int | None] = mapped_column(BigIntId)
    opportunity_id: Mapped[int | None] = mapped_column(BigIntId)
    idem_key: Mapped[str] = mapped_column(String(128), nullable=False)
    market_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)  # yes | no
    action: Mapped[str] = mapped_column(String(4), nullable=False)  # buy | sell
    style: Mapped[str] = mapped_column(String(8), nullable=False, default="taker")  # taker|maker
    limit_price_cents: Mapped[int | None] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    filled_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    # open | filled | partial | canceled | expired | rejected
    reject_reason: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(TS)
    intent_json: Mapped[dict | None] = mapped_column(JSONType)  # thesis/confidence/rationale


class EvoFill(Base):
    __tablename__ = "evo_fills"
    __table_args__ = (Index("ix_evo_fills_order", "order_id"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    order_id: Mapped[int] = mapped_column(BigIntId, ForeignKey("evo_orders.id"), nullable=False)
    agent_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    market_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    action: Mapped[str] = mapped_column(String(4), nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    fee_usd: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, default=0)
    spread_cost_cents: Mapped[float | None] = mapped_column(Float)
    slippage_cents: Mapped[float | None] = mapped_column(Float)
    liquidity: Mapped[str] = mapped_column(String(8), nullable=False, default="taker")
    partial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    snapshot_ref: Mapped[str | None] = mapped_column(String(64))  # market-data provenance
    latency_ms: Mapped[int | None] = mapped_column(Integer)


class EvoPosition(Base):
    """Open/closed position per (agent, ledger, market, side). ledger: 'lifetime' or
    'cohort:<id>'. The cohort ledger position may be scaled at carryover (spec §21)."""

    __tablename__ = "evo_positions"
    __table_args__ = (
        UniqueConstraint("agent_uuid", "ledger", "market_ticker", "side", "open_seq",
                         name="uq_evo_position"),
        Index("ix_evo_positions_agent", "agent_uuid", "ledger", "status"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    agent_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    ledger: Mapped[str] = mapped_column(String(24), nullable=False)
    market_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    open_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    quantity: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    avg_price_cents: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    # open | closed | settled | liquidated | transferred
    opened_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(TS)
    realized_pnl_usd: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    fees_usd: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    last_mark_cents: Mapped[float | None] = mapped_column(Float)
    last_marked_at: Mapped[datetime | None] = mapped_column(TS)
    unrealized_pnl_usd: Mapped[float | None] = mapped_column(Numeric(14, 4))
    resolved_value_cents: Mapped[int | None] = mapped_column(Integer)


class EvoPortfolio(Base):
    """Cash + reserved collateral per (agent, ledger). Fully collateralized: cash
    can never go negative (API-enforced); no leverage/borrowing/transfers."""

    __tablename__ = "evo_portfolios"
    __table_args__ = (UniqueConstraint("agent_uuid", "ledger", name="uq_evo_portfolio"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    agent_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    ledger: Mapped[str] = mapped_column(String(24), nullable=False)
    cash_usd: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    starting_capital_usd: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    realized_pnl_usd: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    fees_usd: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    peak_nav_usd: Mapped[float | None] = mapped_column(Numeric(14, 4))
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow, nullable=False)


class EvoPortfolioSnapshot(Base):
    __tablename__ = "evo_portfolio_snapshots"
    __table_args__ = (
        UniqueConstraint("agent_uuid", "ledger", "label", name="uq_evo_portfolio_snap"),
        Index("ix_evo_portfolio_snaps_agent", "agent_uuid", "captured_at"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    captured_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    agent_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    ledger: Mapped[str] = mapped_column(String(24), nullable=False)
    label: Mapped[str] = mapped_column(String(48), nullable=False)  # daily:YYYY-MM-DD, boundary:N
    cash_usd: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    positions_value_usd: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    nav_usd: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    detail_json: Mapped[dict | None] = mapped_column(JSONType)


class EvoPositionTransfer(Base):
    """Cohort-boundary carryover / retiree liquidation audit rows (spec §21)."""

    __tablename__ = "evo_position_transfers"
    __table_args__ = (
        UniqueConstraint("cohort_id", "agent_uuid", "position_id", name="uq_evo_pos_transfer"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    cohort_id: Mapped[int] = mapped_column(BigIntId, nullable=False)  # boundary being crossed
    agent_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    position_id: Mapped[int] = mapped_column(BigIntId, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # carryover | liquidation
    marked_price_cents: Mapped[float] = mapped_column(Float, nullable=False)
    marked_value_usd: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    scale_factor: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    detail_json: Mapped[dict | None] = mapped_column(JSONType)


class EvoOpportunity(Base):
    """Ex-ante opportunity record: declared by strategy/listener BEFORE the outcome is
    known — the basis for opportunity-use scoring that agents cannot game post hoc."""

    __tablename__ = "evo_opportunities"
    __table_args__ = (
        UniqueConstraint("agent_uuid", "dedup_key", name="uq_evo_opportunity"),
        Index("ix_evo_opportunities_agent", "agent_uuid", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    agent_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    cohort_id: Mapped[int | None] = mapped_column(BigIntId)
    dedup_key: Mapped[str] = mapped_column(String(160), nullable=False)
    market_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)  # strategy|listener|manual
    detail_json: Mapped[dict | None] = mapped_column(JSONType)
    acted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    no_trade_reason: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------
# Fitness + evolution
# ---------------------------------------------------------------------------


class EvoFitness(Base):
    """Per (agent, cohort) fitness evaluation. `visible_after` gates the peer-visible
    copy (6h delay); the real-time row is system-internal."""

    __tablename__ = "evo_fitness"
    __table_args__ = (
        UniqueConstraint("cohort_id", "agent_uuid", "kind", name="uq_evo_fitness"),
        Index("ix_evo_fitness_cohort", "cohort_id", "kind"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    cohort_id: Mapped[int] = mapped_column(BigIntId, nullable=False)
    agent_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False, default="interim")  # interim|final
    computed_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    visible_after: Mapped[datetime] = mapped_column(TS, nullable=False)
    components_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    penalties_json: Mapped[dict | None] = mapped_column(JSONType)
    cohort_score: Mapped[float] = mapped_column(Float, nullable=False)
    reliability_score: Mapped[float] = mapped_column(Float, nullable=False)
    selection_score: Mapped[float] = mapped_column(Float, nullable=False)
    rank: Mapped[int | None] = mapped_column(Integer)
    config_version_id: Mapped[int | None] = mapped_column(BigIntId)


class EvoTransition(Base):
    """Cohort finalization audit. UNIQUE on cohort_id: the insert is the finalization
    lock — a cohort can never finalize twice."""

    __tablename__ = "evo_transitions"
    __table_args__ = (UniqueConstraint("cohort_id", name="uq_evo_transition_cohort"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    cohort_id: Mapped[int] = mapped_column(BigIntId, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    # running | completed | failed
    finished_at: Mapped[datetime | None] = mapped_column(TS)
    rng_seed: Mapped[int] = mapped_column(BigIntId, nullable=False, default=0)
    outcome_json: Mapped[dict | None] = mapped_column(JSONType)  # groups, ranks, slots


class EvoInheritanceSnapshot(Base):
    __tablename__ = "evo_inheritance_snapshots"
    __table_args__ = (
        UniqueConstraint("cohort_id", "parent_uuid", name="uq_evo_inheritance"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    cohort_id: Mapped[int] = mapped_column(BigIntId, nullable=False)
    parent_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    cognitive_genome_id: Mapped[int | None] = mapped_column(BigIntId)
    trading_genome_id: Mapped[int | None] = mapped_column(BigIntId)
    bundle_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    # beliefs snapshot, strengths/weaknesses, listener specs, habits, reflection, refs


class EvoBirth(Base):
    """Child/wildcard creation record. Unique (cohort, slot_key) — a retry can never
    create the same child twice. slot_key = parent uuid for children, 'wildcard:<n>'
    for wildcard founders."""

    __tablename__ = "evo_births"
    __table_args__ = (UniqueConstraint("cohort_id", "slot_key", name="uq_evo_birth_slot"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    cohort_id: Mapped[int] = mapped_column(BigIntId, nullable=False)  # cohort being born INTO
    slot_key: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)  # child | wildcard | founder
    parent_uuid: Mapped[str | None] = mapped_column(String(UUID_LEN))
    child_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    inheritance_snapshot_id: Mapped[int | None] = mapped_column(BigIntId)
    divergence_plan_json: Mapped[dict | None] = mapped_column(JSONType)  # spec §28 / founder thesis


class EvoRetirement(Base):
    __tablename__ = "evo_retirements"
    __table_args__ = (UniqueConstraint("agent_uuid", name="uq_evo_retirement_agent"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    agent_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    cohort_id: Mapped[int] = mapped_column(BigIntId, nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)  # bottom_group|integrity|...
    final_rank: Mapped[int | None] = mapped_column(Integer)
    final_selection_score: Mapped[float | None] = mapped_column(Float)
    liquidation_value_usd: Mapped[float | None] = mapped_column(Numeric(14, 4))
    reflection_json: Mapped[dict | None] = mapped_column(JSONType)  # retirement heartbeat output


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------


class EvoTicket(Base):
    __tablename__ = "evo_tickets"
    __table_args__ = (Index("ix_evo_tickets_status", "status", "created_at"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    requesting_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    requesting_family: Mapped[str | None] = mapped_column(String(32))
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    capability: Mapped[str] = mapped_column(String(200), nullable=False)
    problem: Mapped[str | None] = mapped_column(Text)
    expected_strategy_benefit: Mapped[str | None] = mapped_column(Text)
    expected_portfolio_benefit: Mapped[str | None] = mapped_column(Text)
    existing_workaround: Mapped[str | None] = mapped_column(Text)
    required_json: Mapped[dict | None] = mapped_column(JSONType)  # data/code required
    expected_cost: Mapped[str | None] = mapped_column(String(128))
    expected_effort: Mapped[str | None] = mapped_column(String(128))
    urgency: Mapped[str] = mapped_column(String(12), nullable=False, default="normal")
    evidence_json: Mapped[dict | None] = mapped_column(JSONType)
    related_json: Mapped[dict | None] = mapped_column(JSONType)  # experiments/strategies/tickets
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    # open | in_review | approved | rejected | implemented | duplicate
    human_decision: Mapped[str | None] = mapped_column(Text)
    implementation_result: Mapped[str | None] = mapped_column(Text)
    duplicate_of_id: Mapped[int | None] = mapped_column(BigIntId)
    norm_signature: Mapped[str | None] = mapped_column(String(200))  # dedup token signature


class EvoTicketSupporter(Base):
    __tablename__ = "evo_ticket_supporters"
    __table_args__ = (UniqueConstraint("ticket_id", "agent_uuid", name="uq_evo_ticket_supporter"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    ticket_id: Mapped[int] = mapped_column(BigIntId, ForeignKey("evo_tickets.id"), nullable=False)
    agent_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    family: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------
# Graveyard + audit
# ---------------------------------------------------------------------------


class EvoGraveyardEntry(Base):
    """Searchable failed/retired strategy corpus. Seeded from the repo's research
    history (docs) and accreting from retired agents and killed experiments."""

    __tablename__ = "evo_graveyard"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_evo_graveyard_slug"),
        Index("ix_evo_graveyard_family", "strategy_family"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    strategy_family: Mapped[str | None] = mapped_column(String(48))
    market_categories: Mapped[str | None] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(24), nullable=False)  # docs_seed|agent|experiment
    source_ref: Mapped[str | None] = mapped_column(String(200))  # doc path / agent uuid / exp id
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)  # killed|shelved|retired|neg_ev
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    why_failed: Mapped[str | None] = mapped_column(Text)
    evidence_json: Mapped[dict | None] = mapped_column(JSONType)
    revival_conditions: Mapped[str | None] = mapped_column(Text)


class EvoAuditEvent(Base):
    __tablename__ = "evo_audit_events"
    __table_args__ = (
        Index("ix_evo_audit_agent_time", "agent_uuid", "created_at"),
        Index("ix_evo_audit_kind", "kind"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    agent_uuid: Mapped[str | None] = mapped_column(String(UUID_LEN))
    heartbeat_id: Mapped[int | None] = mapped_column(BigIntId)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    severity: Mapped[str] = mapped_column(String(12), nullable=False, default="info")
    # info | warn | integrity
    detail_json: Mapped[dict | None] = mapped_column(JSONType)


# ---------------------------------------------------------------------------
# Operator announcements (broadcast to every agent)
# ---------------------------------------------------------------------------


class EvoAnnouncement(Base):
    """An operator broadcast shown to every agent while active: how the operator
    tells the whole population about a system change (a config change, a new rule,
    a fixed bug). Active announcements are injected into every heartbeat's context,
    so all agents 'know' the same thing at once.

    Declared in code (kalshi_bot/evo/announcements.py) and seeded idempotently on
    the stable `key`, matching the model-price/graveyard seed pattern — there is no
    live write path, so announcing something is: add an entry and deploy."""

    __tablename__ = "evo_announcements"
    __table_args__ = (
        UniqueConstraint("key", name="uq_evo_announcement_key"),
        Index("ix_evo_announcements_active", "active", "effective_at"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)  # stable idempotency key
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="system_change")
    effective_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(TS)  # null = never expires
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
