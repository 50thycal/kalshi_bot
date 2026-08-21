"""Experiment OS foundation tables (spec §31) — identity, state, dependencies, audit.

Joins the existing declarative Base/metadata (kalshi_bot.models) so Alembic and the
create_all() safety net see these tables alongside the legacy schema, exactly as the
evo package does. Conventions match models.py: BigIntId autoincrement PKs, JSONType
payloads, TS timestamps, no relationship() sugar (reads do explicit selects).

Design rules embodied here rather than left to convention:

  * The canonical lineage is a chain of NOT NULL foreign keys:
        deployment → epoch → version → experiment
    and every epoch pins a complete PlatformSnapshot (NOT NULL). A trade tagged with a
    deployment's strategy tag is therefore traceable to experiment/version/arm/epoch/
    snapshot with no archaeology.
  * Almost every *descriptive* column is nullable — deliberately. Legacy experiments
    are imported with exactly the metadata history actually recorded, never invented
    (spec §22); the difference between "unknown" and "empty" must survive.
    New-work completeness is enforced by the service layer, not by NOT NULL, so the
    same schema serves both cleanly.
  * Consequential records (state transitions, gate results, integrity events, legacy
    evidence) are append-only. The service layer installs a flush guard that rejects
    UPDATE/DELETE on them — history is never rewritten (spec §31).
  * Version ≠ epoch: a version is a new scientific contract (its own arms/gates);
    an epoch is the same contract under a changed environment (new snapshot, same
    version). The FK shape enforces the distinction: arms/gates hang off versions,
    snapshots hang off epochs.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..models import TS, Base, BigIntId, JSONType, utcnow

# ---------------------------------------------------------------------------
# Platform dependency registry (spec §16)
# ---------------------------------------------------------------------------

# The standard shared components every snapshot should cover (spec §16.1). Components
# are open-ended — rows, not an enum — but these are seeded by the service so a new
# experiment cannot accidentally miss e.g. the fee model.
STANDARD_PLATFORM_COMPONENTS: tuple[str, ...] = (
    "FEE_MODEL",
    "FILL_MODEL",
    "MARKET_TAXONOMY",
    "EXECUTION_ENGINE",
    "SETTLEMENT_ENGINE",
    "RISK_ENGINE",
    "DATA_PROVENANCE",
    "KALSHI_API_SCHEMA",
    "METRICS_ENGINE",
    "EXPERIMENT_ENGINE",
)


class PlatformComponent(Base):
    """A shared system component whose changes can affect many experiments."""

    __tablename__ = "platform_components"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class PlatformRevision(Base):
    """An immutable version of one platform component (spec §16.2).

    `activated_at` is the measured runtime boundary — preferred over any merge
    timestamp. While it is unknown and the change affects comparability, gate
    evaluation over the boundary must be BLOCKED_PLATFORM (enforced by the later
    evaluator; recorded here from day one)."""

    __tablename__ = "platform_revisions"
    __table_args__ = (
        UniqueConstraint("component_id", "version", name="uq_platform_revision"),
        Index("ix_platform_revisions_status", "component_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    component_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("platform_components.id"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    fingerprint: Mapped[str | None] = mapped_column(String(64))  # code/config hash
    description: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    backward_compatibility: Mapped[str | None] = mapped_column(Text)
    # Can affected historical observations be transformed exactly to the new
    # semantics (the I1-vs-I2 question)? Nullable: unknown is not "no".
    normalization_available: Mapped[bool | None] = mapped_column(Boolean)
    safety_class: Mapped[str | None] = mapped_column(String(16))
    pr_ref: Mapped[str | None] = mapped_column(String(120))  # PR/commit reference
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(TS)
    retired_at: Mapped[datetime | None] = mapped_column(TS)


class PlatformSnapshot(Base):
    """A complete bundle of active platform revisions (spec §16.3).

    Content-addressed: `fingerprint` hashes the sorted (component, revision) set, so an
    unchanged platform resolves to the same snapshot row instead of minting duplicates.
    Completeness (one item per registered component) is validated at creation."""

    __tablename__ = "platform_snapshots"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class PlatformSnapshotItem(Base):
    """One (component → revision) pin inside a snapshot."""

    __tablename__ = "platform_snapshot_items"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "component_id", name="uq_snapshot_component"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("platform_snapshots.id"), nullable=False
    )
    component_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("platform_components.id"), nullable=False
    )
    revision_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("platform_revisions.id"), nullable=False
    )


class PlatformImpactAction(Base):
    """Per-experiment disposition of one platform revision (spec §17–18) — the
    canonical impact record of the change-impact engine.

    One row answers, durably: which revision, superseding what, hit which
    experiment (at which version/epoch/snapshot), how materially (I0–I4), what
    must happen (ImpactAction), decided and accepted by whom, whether it has been
    APPLIED, what the application produced (new epoch/version ids), and — for I1 —
    the NAMED normalizer that licenses pooling. Status walks
    proposed → accepted → applied (or exempted, with rationale). Classification
    fields freeze at acceptance (flush-guard); only application/lifecycle fields
    may change after. `blocks_activation` marks dispositions (I4 by default) that
    must be APPLIED — not merely accepted — before the revision may activate."""

    __tablename__ = "platform_impact_actions"
    __table_args__ = (
        UniqueConstraint("revision_id", "experiment_id", name="uq_impact_per_experiment"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    revision_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("platform_revisions.id"), nullable=False
    )
    # Provenance of "before": the revision this one supersedes for the component,
    # and the snapshot the experiment's current epoch pinned at decision time.
    superseded_revision_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("platform_revisions.id")
    )
    prior_snapshot_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("platform_snapshots.id")
    )
    experiment_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("experiments.id"), nullable=False
    )
    version_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("experiment_versions.id")
    )
    epoch_id: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("experiment_epochs.id"))
    impact_class: Mapped[str] = mapped_column(String(4), nullable=False)  # I0..I4
    action: Mapped[str] = mapped_column(String(28), nullable=False)  # ImpactAction
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="proposed", server_default="proposed"
    )  # proposed | accepted | applied | exempted
    rationale: Mapped[str | None] = mapped_column(Text)
    # I1 only: the registered transformation that makes history exactly comparable
    # (e.g. "metric:pnl_cents_per_trade" or "metrics_engine:<revision>"). Presence
    # is VALIDATED against the metric registry — never a magic boolean.
    normalization_ref: Mapped[str | None] = mapped_column(String(200))
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    accepted_by: Mapped[str | None] = mapped_column(String(64))
    accepted_at: Mapped[datetime | None] = mapped_column(TS)
    applied_by: Mapped[str | None] = mapped_column(String(64))
    applied_at: Mapped[datetime | None] = mapped_column(TS)
    # What applying the action produced — auditable forward lineage.
    resulting_epoch_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("experiment_epochs.id")
    )
    resulting_version_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("experiment_versions.id")
    )
    # Safety blocker: activation refuses until this row reaches "applied".
    blocks_activation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    details_json: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Experiment identity + lifecycle (spec §5–8)
# ---------------------------------------------------------------------------


class Experiment(Base):
    """The durable research identity testing one hypothesis (spec §5.2).

    Survives across versions and epochs. `state` is the cached current lifecycle
    state; the append-only experiment_state_transitions table is the authority.
    `platform_snapshot_id` is the complete snapshot active when the experiment was
    created — the service requires it for every NEW experiment; a legacy import may
    leave it NULL because inventing a historical snapshot would be fabricated metadata
    (spec §22). `legacy_class` non-null marks an imported record."""

    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(200))
    origin: Mapped[str | None] = mapped_column(String(24))  # operator|idea_model|evo|...
    family: Mapped[str | None] = mapped_column(String(48))  # maker|observation_pin|...
    # Hypothesis headline fields. The version carries the frozen scientific contract;
    # these are the experiment-level summary (and all a legacy import may ever have).
    hypothesis: Mapped[str | None] = mapped_column(Text)
    mechanism: Mapped[str | None] = mapped_column(Text)
    counterparty: Mapped[str | None] = mapped_column(Text)
    falsification: Mapped[str | None] = mapped_column(Text)
    universe: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="IDEA", index=True)
    # Set while state == PAUSED: the state to which a resume may return (and no other).
    paused_from_state: Mapped[str | None] = mapped_column(String(16))
    platform_snapshot_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("platform_snapshots.id")
    )
    # Successor lineage: a revived retired concept points here at its predecessor.
    predecessor_experiment_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("experiments.id")
    )
    # Legacy migration fields (spec §22–23). NULL on native new-system experiments.
    legacy_class: Mapped[str | None] = mapped_column(String(24))
    migration_integrity: Mapped[str | None] = mapped_column(String(1))  # A|B|C|D
    imported_at: Mapped[datetime | None] = mapped_column(TS)
    docs_json: Mapped[dict | None] = mapped_column(JSONType)  # {"thesis": ..., "studies": [...]}
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TS, default=utcnow, onupdate=utcnow, nullable=False
    )
    retired_at: Mapped[datetime | None] = mapped_column(TS)


class ExperimentVersion(Base):
    """An immutable revision of the scientific/strategy contract (spec §5.3).

    A new version means we are no longer testing exactly the same question (changed
    hypothesis, treatment/control, independent variable, entry/exit semantics, gate,
    or metric meaning). Once `frozen_at` is set the row is immutable — the service's
    flush guard rejects edits; a changed contract is a NEW version row referencing its
    predecessor. Contract fields are normalized into headline columns plus
    `contract_json`, the whole machine-readable contract in one place (spec §9)."""

    __tablename__ = "experiment_versions"
    __table_args__ = (
        UniqueConstraint("experiment_id", "version", name="uq_experiment_version"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    experiment_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("experiments.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    # NULL = still being drafted; set = contract frozen and immutable.
    frozen_at: Mapped[datetime | None] = mapped_column(TS)
    # --- the scientific contract (spec §9) ---
    hypothesis: Mapped[str | None] = mapped_column(Text)
    mechanism: Mapped[str | None] = mapped_column(Text)
    counterparty: Mapped[str | None] = mapped_column(Text)
    falsification: Mapped[str | None] = mapped_column(Text)
    universe_selector: Mapped[str | None] = mapped_column(Text)
    universe_exclusions: Mapped[str | None] = mapped_column(Text)
    entry_rule: Mapped[str | None] = mapped_column(Text)
    exit_rule: Mapped[str | None] = mapped_column(Text)
    sizing_rule: Mapped[str | None] = mapped_column(Text)
    execution_style: Mapped[str | None] = mapped_column(String(16))  # maker|taker|mixed
    independent_variable: Mapped[str | None] = mapped_column(Text)
    held_constant_json: Mapped[list | None] = mapped_column(JSONType)  # list[str]
    control_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    control_exemption_reason: Mapped[str | None] = mapped_column(Text)
    metrics_json: Mapped[dict | None] = mapped_column(JSONType)
    sample_json: Mapped[dict | None] = mapped_column(JSONType)
    costs_json: Mapped[dict | None] = mapped_column(JSONType)
    risk_json: Mapped[dict | None] = mapped_column(JSONType)
    provenance_json: Mapped[dict | None] = mapped_column(JSONType)
    monitoring_json: Mapped[dict | None] = mapped_column(JSONType)
    docs_json: Mapped[dict | None] = mapped_column(JSONType)
    contract_json: Mapped[dict | None] = mapped_column(JSONType)
    # Reproducible hash of the material contract fields (spec §15).
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    # Immutable hash taken when a stage begins accumulating evidence under this
    # version — the exact pre-registration that governed the evidence.
    pre_registration_hash: Mapped[str | None] = mapped_column(String(64))
    predecessor_version_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("experiment_versions.id")
    )
    change_reason: Mapped[str | None] = mapped_column(Text)


class ExperimentArm(Base):
    """A pre-declared treatment/control/benchmark branch within a version (spec §5.4).

    `strategy_tag` here is the arm's default implementation tag; the concrete tag a
    given deployment uses lives on experiment_deployment_arms, because a tag may
    legitimately change between paper and live canary while the arm identity holds."""

    __tablename__ = "experiment_arms"
    __table_args__ = (UniqueConstraint("version_id", "arm_key", name="uq_experiment_arm"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("experiment_versions.id"), nullable=False
    )
    arm_key: Mapped[str] = mapped_column(String(48), nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False)  # ArmRole
    description: Mapped[str | None] = mapped_column(Text)
    params_json: Mapped[dict | None] = mapped_column(JSONType)
    strategy_tag: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class ExperimentEpoch(Base):
    """An immutable operating interval for ONE version under ONE environment (spec §5.5).

    New epoch when the question is unchanged but the world/machinery changed in a way
    that affects comparability (taxonomy, fee model, fill model, data provenance, ...).
    The canonical successor to the hand-written "cohort boundary" — the word cohort is
    deliberately not used here because evo_cohorts means something else.
    `platform_snapshot_id` is NOT NULL: every epoch pins a complete snapshot."""

    __tablename__ = "experiment_epochs"
    __table_args__ = (
        UniqueConstraint("version_id", "epoch_number", name="uq_experiment_epoch"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("experiment_versions.id"), nullable=False
    )
    epoch_number: Mapped[int] = mapped_column(Integer, nullable=False)
    platform_snapshot_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("platform_snapshots.id"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(TS)
    # Why this boundary exists ("initial", or the environment change), and the impact
    # class that forced it (I2 etc.) when one did.
    reason: Mapped[str | None] = mapped_column(Text)
    impact_class: Mapped[str | None] = mapped_column(String(4))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class ExperimentDeployment(Base):
    """A concrete running implementation of arms at a lifecycle stage (spec §5.6).

    `twin_of_deployment_id` makes the live/paper twin pair first-class: a PAPER_TWIN
    deployment points at the LIVE deployment it shadows. `platform_snapshot_id` is
    normally NULL (= the epoch's snapshot); set only if a deployment genuinely pins a
    different snapshot than its epoch, which the integrity machinery treats as a flag.

    `grandfathered` marks a deployment imported by the legacy migration: its runtime
    may continue under enforcement, but its research evolution (new arms, config
    changes, stage advances, new live deployments) must go through Experiment OS.
    `config_fingerprint` = canonical hash of `config_json` at registration — the
    baseline the drift check compares runtime configuration against."""

    __tablename__ = "experiment_deployments"
    __table_args__ = (Index("ix_experiment_deployments_epoch", "epoch_id"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    epoch_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("experiment_epochs.id"), nullable=False
    )
    deployment_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)  # PAPER|LIVE_CANARY|PRODUCTION
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # DeploymentKind
    twin_of_deployment_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("experiment_deployments.id")
    )
    platform_snapshot_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("platform_snapshots.id")
    )
    code_fingerprint: Mapped[str | None] = mapped_column(String(64))
    config_fingerprint: Mapped[str | None] = mapped_column(String(64))
    config_json: Mapped[dict | None] = mapped_column(JSONType)
    grandfathered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    started_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(TS)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class ExperimentDeploymentArm(Base):
    """Which arms a deployment runs, and under which concrete strategy tag.

    The strategy tag is the operational join key into paper_trades.strategy /
    live_orders.strategy. Keeping it here (per deployment × arm) preserves both the
    stable research identity and the concrete execution tag (spec §13)."""

    __tablename__ = "experiment_deployment_arms"
    __table_args__ = (
        UniqueConstraint("deployment_id", "arm_id", name="uq_deployment_arm"),
        Index("ix_experiment_deployment_arms_tag", "strategy_tag"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    deployment_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("experiment_deployments.id"), nullable=False
    )
    arm_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("experiment_arms.id"), nullable=False
    )
    strategy_tag: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Gates, results, audit (spec §11, §31)
# ---------------------------------------------------------------------------


class ExperimentGate(Base):
    """A pre-registered, executable decision rule (spec §5.9, §11).

    `spec_json` is structured data ({sample, pass_all, fail_any} clause lists), never
    prose — stored so a later evaluator can run it without parsing Markdown.
    `spec_hash` is the immutable pre-registration hash. Once `evidence_started_at` is
    set the gate may not be edited in place (flush-guarded); a revised decision rule
    is a new gate on a new version, with `superseded_by_gate_id` recording the chain."""

    __tablename__ = "experiment_gates"
    __table_args__ = (UniqueConstraint("version_id", "gate_key", name="uq_experiment_gate"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("experiment_versions.id"), nullable=False
    )
    gate_key: Mapped[str] = mapped_column(String(48), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # promotion|kill|monitoring
    from_state: Mapped[str | None] = mapped_column(String(16))
    to_state: Mapped[str | None] = mapped_column(String(16))
    spec_json: Mapped[dict] = mapped_column(JSONType, nullable=False)
    spec_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    evidence_started_at: Mapped[datetime | None] = mapped_column(TS)
    superseded_by_gate_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("experiment_gates.id")
    )
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class ExperimentGateResult(Base):
    """One immutable gate evaluation (spec §11.1).

    The foundation release records results manually (`computed_by="manual"`); the
    structured evaluator lands in the metrics/gate PR and writes the same rows with
    `computed_by="system"`. Every result names the exact version/epoch/snapshot/
    evidence window that produced it, so a verdict can never outlive its context."""

    __tablename__ = "experiment_gate_results"
    __table_args__ = (Index("ix_experiment_gate_results_gate", "gate_id", "computed_at"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    gate_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("experiment_gates.id"), nullable=False
    )
    experiment_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("experiments.id"), nullable=False
    )
    version_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("experiment_versions.id"), nullable=False
    )
    epoch_id: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("experiment_epochs.id"))
    verdict: Mapped[str] = mapped_column(String(20), nullable=False)  # GateVerdict
    evidence_start: Mapped[datetime | None] = mapped_column(TS)
    evidence_end: Mapped[datetime | None] = mapped_column(TS)
    sample_json: Mapped[dict | None] = mapped_column(JSONType)  # per-arm n
    metrics_json: Mapped[dict | None] = mapped_column(JSONType)
    platform_snapshot_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("platform_snapshots.id")
    )
    metric_revision: Mapped[str | None] = mapped_column(String(64))
    computed_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    computed_by: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    evidence_ref: Mapped[str | None] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class ExperimentStateTransition(Base):
    """Append-only lifecycle audit: one row per state change (spec §7, §31).

    `from_state` NULL marks the creation/import event. Approval-required transitions
    carry `approved_by`; promotions past PAPER also reference the PASS gate result
    that justified them. The cached experiments.state must always equal the newest
    row here — the service keeps them in lockstep."""

    __tablename__ = "experiment_state_transitions"
    __table_args__ = (
        Index("ix_experiment_state_transitions_exp", "experiment_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    experiment_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("experiments.id"), nullable=False
    )
    from_state: Mapped[str | None] = mapped_column(String(16))
    to_state: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    actor: Mapped[str] = mapped_column(String(24), nullable=False)  # operator|system|import
    approved_by: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(Text)
    gate_result_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("experiment_gate_results.id")
    )
    version_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("experiment_versions.id")
    )
    epoch_id: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("experiment_epochs.id"))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class ExperimentIntegrityEvent(Base):
    """A recorded contamination/violation flag (spec §10): a held-constant field
    changed mid-experiment, mixed snapshots in one epoch, an arming defect, etc.
    Append-only; `resolved_at`/`resolution` record the remedy without erasing the
    flag. Automatic detection is later work — the table is the durable place manual
    findings already go."""

    __tablename__ = "experiment_integrity_events"
    __table_args__ = (
        Index("ix_experiment_integrity_events_exp", "experiment_id", "detected_at"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    experiment_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("experiments.id"), nullable=False
    )
    version_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("experiment_versions.id")
    )
    epoch_id: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("experiment_epochs.id"))
    deployment_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("experiment_deployments.id")
    )
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="warning")
    detected_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    details_json: Mapped[dict | None] = mapped_column(JSONType)
    resolved_at: Mapped[datetime | None] = mapped_column(TS)
    resolution: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class ExperimentOsEnforcement(Base):
    """The recorded enforcement state (spec §4.2) — append-only; the newest row by
    effective_at is authoritative. The enforcement cutover is an EXPLICIT recorded
    event: mode, effective instant, actor, reason, system version, a unique cutover
    id, and the readiness evidence captured at the moment of the decision. Never
    inferred from a deploy, a merge, the first row, or the importer's run time."""

    __tablename__ = "experiment_os_enforcement"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    cutover_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    mode: Mapped[str] = mapped_column(String(12), nullable=False)  # EnforcementMode
    preceding_mode: Mapped[str | None] = mapped_column(String(12))
    effective_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    system_version: Mapped[str] = mapped_column(String(64), nullable=False)
    readiness_json: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class ExperimentLegacyEvidence(Base):
    """Pre-migration performance attached to an imported experiment (spec §22).

    `evidence_class` is the load-bearing column: CONTEXT_ONLY history is preserved and
    queryable but can never satisfy a clean new-system promotion gate; only
    CERTIFIED_LEGACY_EVIDENCE (an explicit migration certification) may inform one.
    `summary_json` preserves the numbers exactly as historically recorded — never
    recomputed to look consistent."""

    __tablename__ = "experiment_legacy_evidence"
    __table_args__ = (Index("ix_experiment_legacy_evidence_exp", "experiment_id"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    experiment_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("experiments.id"), nullable=False
    )
    version_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("experiment_versions.id")
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    evidence_class: Mapped[str] = mapped_column(String(32), nullable=False)  # EvidenceClass
    source: Mapped[str | None] = mapped_column(Text)  # doc path / table+filter / run ref
    window_start: Mapped[datetime | None] = mapped_column(TS)
    window_end: Mapped[datetime | None] = mapped_column(TS)
    summary_json: Mapped[dict | None] = mapped_column(JSONType)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Investigation / issue workflow (docs/EXPERIMENT_OS_ISSUES.md)
# ---------------------------------------------------------------------------
#
# The durable home for problems: anomalies, suspected defects, operational
# incidents, scientific questions and shared-platform problems. Before these
# tables the only record of an investigation was session prose and a small
# hand-written registry, so a problem survived exactly as long as the chat
# window did.
#
# These tables are workflow, NOT truth. Nothing here is an input to a gate, a
# verdict, a lifecycle transition, admission, enforcement or a platform
# revision — an issue records that one of those is required and links the
# canonical record once it exists. The distinction is the reason an issue can
# be opened on a hunch without endangering anything.


class ExperimentIssue(Base):
    """One durable investigation: a problem, its ownership, remedy and outcome.

    `status`, `current_owner_role`, `classification`, `occurrence_count` and
    `resolved_at` are CACHED current values for efficient reads; the append-only
    `experiment_issue_events` table is the authority for how they got there, in
    exactly the relationship `experiments.state` has to
    `experiment_state_transitions`.

    Every lineage column is nullable because an issue may legitimately be
    system-wide (a collector outage belongs to no experiment) — but present
    values are VALIDATED against each other by the service, so a version always
    belongs to its experiment and a gate result always belongs to its gate.
    Canonical identity lives in these foreign keys and never in `details_json`.

    The four `requires_*` flags are tri-state on purpose: NULL means "not yet
    determined", which is not the same as False. Recording "this does not need a
    new Version" is a finding; not having looked yet is not.
    """

    __tablename__ = "experiment_issues"
    __table_args__ = (
        Index("ix_experiment_issues_status", "status", "opened_at"),
        Index("ix_experiment_issues_owner", "current_owner_role", "status"),
        Index("ix_experiment_issues_classification", "classification", "status"),
        Index("ix_experiment_issues_experiment", "experiment_id", "status"),
        Index("ix_experiment_issues_version", "version_id"),
        Index("ix_experiment_issues_deployment", "deployment_id"),
        Index("ix_experiment_issues_fingerprint", "detector_fingerprint", "status"),
        Index("ix_experiment_issues_updated", "updated_at"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    #: Stable, unique, human-readable, CLI-safe: "XOS-000123". Never the title —
    #: a title is a description and descriptions get rewritten.
    issue_key: Mapped[str] = mapped_column(String(24), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    problem_statement: Mapped[str | None] = mapped_column(Text)
    classification: Mapped[str] = mapped_column(
        String(16), nullable=False, default="UNCLASSIFIED",
        server_default="UNCLASSIFIED",
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="OPEN", server_default="OPEN"
    )
    severity: Mapped[str] = mapped_column(
        String(8), nullable=False, default="MEDIUM", server_default="MEDIUM"
    )
    priority: Mapped[str] = mapped_column(
        String(2), nullable=False, default="P2", server_default="P2"
    )
    #: Who is working it now. Never EXPERIMENT_CONTROL_TOWER (read-only) and
    #: never a generic fixer role — an issue routes to an existing session role.
    current_owner_role: Mapped[str] = mapped_column(String(24), nullable=False)
    #: The detecting context, which MAY be the read-only Control Tower.
    opened_by_role: Mapped[str] = mapped_column(String(24), nullable=False)
    opened_by_actor: Mapped[str | None] = mapped_column(String(64))
    opened_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TS, default=utcnow, onupdate=utcnow, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(TS)

    # --- canonical scope (nullable, but validated together when present) ---
    experiment_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("experiments.id")
    )
    version_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("experiment_versions.id")
    )
    epoch_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("experiment_epochs.id")
    )
    deployment_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("experiment_deployments.id")
    )
    gate_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("experiment_gates.id")
    )
    gate_result_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("experiment_gate_results.id")
    )
    platform_revision_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("platform_revisions.id")
    )
    integrity_event_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("experiment_integrity_events.id")
    )

    # --- detection + recurrence (spec §8) ---
    detector: Mapped[str | None] = mapped_column(String(64))
    #: Deterministic hash of the problem SCOPE only. Volatile values never enter
    #: it, or the same recurring anomaly would hash differently every run.
    detector_fingerprint: Mapped[str | None] = mapped_column(String(64))
    first_observed_at: Mapped[datetime | None] = mapped_column(TS)
    last_observed_at: Mapped[datetime | None] = mapped_column(TS)
    #: Advanced ONLY by an explicit write. A Control Tower read must never
    #: increment it — rendering a report is not an observation event.
    occurrence_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    # --- investigation content ---
    evidence_summary: Mapped[str | None] = mapped_column(Text)
    proposed_fix: Mapped[str | None] = mapped_column(Text)
    disposition: Mapped[str | None] = mapped_column(String(24))
    validation_plan: Mapped[str | None] = mapped_column(Text)
    resolution_summary: Mapped[str | None] = mapped_column(Text)

    # --- what the remedy REQUIRES (recorded, never performed) ---
    requires_new_version: Mapped[bool | None] = mapped_column(Boolean)
    requires_new_epoch: Mapped[bool | None] = mapped_column(Boolean)
    requires_platform_revision: Mapped[bool | None] = mapped_column(Boolean)
    requires_pause_or_stand_down: Mapped[bool | None] = mapped_column(Boolean)

    # --- issue-to-issue lineage ---
    duplicate_of_issue_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("experiment_issues.id")
    )
    supersedes_issue_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("experiment_issues.id")
    )
    #: Set when investigating one issue uncovered this INDEPENDENTLY actionable
    #: problem (spec §6.1) — so unrelated remedies do not stay trapped together
    #: in one permanently ambiguous ticket.
    discovered_from_issue_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("experiment_issues.id")
    )
    details_json: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class ExperimentIssueEvent(Base):
    """Append-only workflow history: one row per consequential change (§4.1).

    Written in the SAME transaction as the change it describes, so the cached
    columns on the issue can never drift from the history that explains them.
    Guarded against UPDATE/DELETE by the service flush guard: an investigation
    that can be quietly edited afterwards is not a record of anything.

    The paired from_/to_ columns are populated only for the dimension that
    actually moved — a classification change leaves the status pair NULL — so
    "what changed here" is answerable without diffing adjacent rows.
    """

    __tablename__ = "experiment_issue_events"
    __table_args__ = (
        Index("ix_experiment_issue_events_issue", "issue_id", "occurred_at"),
        Index("ix_experiment_issue_events_type", "event_type", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    issue_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("experiment_issues.id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(64))
    actor_role: Mapped[str | None] = mapped_column(String(24))
    occurred_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str | None] = mapped_column(String(20))
    from_owner_role: Mapped[str | None] = mapped_column(String(24))
    to_owner_role: Mapped[str | None] = mapped_column(String(24))
    from_classification: Mapped[str | None] = mapped_column(String(16))
    to_classification: Mapped[str | None] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class ExperimentIssueEvidence(Base):
    """Append-only evidence cited by an issue (§4.2).

    Evidence is REFERENCED and summarized, not copied: `source_ref` points at the
    gate result, ops result id, document path, PR or bounded query that a reader
    can go and check, and `content_hash` optionally pins what was seen at the
    time. Inlining whole payloads would turn the issue into an unreviewable blob
    and would silently duplicate canonical records that already exist.
    """

    __tablename__ = "experiment_issue_evidence"
    __table_args__ = (
        Index("ix_experiment_issue_evidence_issue", "issue_id", "captured_at"),
        Index("ix_experiment_issue_evidence_type", "evidence_type"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    issue_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("experiment_issues.id"), nullable=False
    )
    evidence_type: Mapped[str] = mapped_column(String(24), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    source_ref: Mapped[str | None] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    captured_by: Mapped[str | None] = mapped_column(String(64))
    payload_json: Mapped[dict | None] = mapped_column(JSONType)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class ExperimentIssueLink(Base):
    """Append-only supporting references (§4.3).

    Foreign-key columns on the issue remain preferable for PRIMARY scope; this
    table exists for the second gate, the third PR, the successor issue and the
    external artifacts (GitHub issue, ops result) that have no column of their
    own. It is a pointer list, never a place to restate canonical identity.
    """

    __tablename__ = "experiment_issue_links"
    __table_args__ = (
        Index("ix_experiment_issue_links_issue", "issue_id", "link_type"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    issue_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("experiment_issues.id"), nullable=False
    )
    link_type: Mapped[str] = mapped_column(String(24), nullable=False)
    reference: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
