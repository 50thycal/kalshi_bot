"""Experiment OS foundation tables (PR 1 of docs/EXPERIMENT_OPERATING_SYSTEM_SPEC.md).

Adds the canonical experiment-lifecycle schema: experiments / versions / arms /
epochs / deployments (+ per-deployment arm→strategy-tag links), structured gates and
immutable gate results, the append-only state-transition audit, integrity events,
legacy evidence, and the platform dependency registry (components / revisions /
snapshots / snapshot items / impact actions). Models: kalshi_bot/experiment_os/.

Deploy-inert by design: no seed rows, no existing table touched, and nothing in the
trading runtime reads or writes these tables yet (enforcement mode OFF). The tables
exist so the read path, fixtures, and the later importer/enforcement PRs have a
stable schema to build on.

Guarded with an inspector check because the schema is also materialised by
create_all() at startup (and in CI), so a plain create_table would collide on a
from-scratch database — the same pattern the live_paper_twins migration uses.

Revision ID: b4c5d6e7f8a9
Revises: a1c4e7b2d9f3
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "b4c5d6e7f8a9"
down_revision: str | None = "a1c4e7b2d9f3"
branch_labels = None
depends_on = None

_BIGINT = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if "platform_components" not in existing:
        op.create_table(
            "platform_components",
            sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
            sa.Column("key", sa.String(48), nullable=False, unique=True),
            sa.Column("description", sa.Text()),
            sa.Column("created_at", _TS, nullable=False),
        )

    if "platform_revisions" not in existing:
        op.create_table(
            "platform_revisions",
            sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
            sa.Column(
                "component_id", _BIGINT, sa.ForeignKey("platform_components.id"), nullable=False
            ),
            sa.Column("version", sa.String(64), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("fingerprint", sa.String(64)),
            sa.Column("description", sa.Text()),
            sa.Column("reason", sa.Text()),
            sa.Column("backward_compatibility", sa.Text()),
            sa.Column("normalization_available", sa.Boolean()),
            sa.Column("safety_class", sa.String(16)),
            sa.Column("pr_ref", sa.String(120)),
            sa.Column("created_at", _TS, nullable=False),
            sa.Column("activated_at", _TS),
            sa.Column("retired_at", _TS),
            sa.UniqueConstraint("component_id", "version", name="uq_platform_revision"),
        )
        op.create_index(
            "ix_platform_revisions_status", "platform_revisions", ["component_id", "status"]
        )

    if "platform_snapshots" not in existing:
        op.create_table(
            "platform_snapshots",
            sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
            sa.Column("fingerprint", sa.String(64), nullable=False, unique=True),
            sa.Column("label", sa.String(64)),
            sa.Column("description", sa.Text()),
            sa.Column("created_at", _TS, nullable=False),
        )

    if "platform_snapshot_items" not in existing:
        op.create_table(
            "platform_snapshot_items",
            sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
            sa.Column(
                "snapshot_id", _BIGINT, sa.ForeignKey("platform_snapshots.id"), nullable=False
            ),
            sa.Column(
                "component_id", _BIGINT, sa.ForeignKey("platform_components.id"), nullable=False
            ),
            sa.Column(
                "revision_id", _BIGINT, sa.ForeignKey("platform_revisions.id"), nullable=False
            ),
            sa.UniqueConstraint("snapshot_id", "component_id", name="uq_snapshot_component"),
        )

    if "experiments" not in existing:
        op.create_table(
            "experiments",
            sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
            sa.Column("key", sa.String(64), nullable=False),
            sa.Column("title", sa.String(200)),
            sa.Column("origin", sa.String(24)),
            sa.Column("family", sa.String(48)),
            sa.Column("hypothesis", sa.Text()),
            sa.Column("mechanism", sa.Text()),
            sa.Column("counterparty", sa.Text()),
            sa.Column("falsification", sa.Text()),
            sa.Column("universe", sa.Text()),
            sa.Column("state", sa.String(16), nullable=False),
            sa.Column("paused_from_state", sa.String(16)),
            sa.Column(
                "platform_snapshot_id", _BIGINT, sa.ForeignKey("platform_snapshots.id")
            ),
            sa.Column(
                "predecessor_experiment_id", _BIGINT, sa.ForeignKey("experiments.id")
            ),
            sa.Column("legacy_class", sa.String(24)),
            sa.Column("migration_integrity", sa.String(1)),
            sa.Column("imported_at", _TS),
            sa.Column("docs_json", sa.JSON()),
            sa.Column("notes", sa.Text()),
            sa.Column("created_at", _TS, nullable=False),
            sa.Column("updated_at", _TS, nullable=False),
            sa.Column("retired_at", _TS),
        )
        op.create_index("ix_experiments_key", "experiments", ["key"], unique=True)
        op.create_index("ix_experiments_state", "experiments", ["state"])

    if "experiment_versions" not in existing:
        op.create_table(
            "experiment_versions",
            sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
            sa.Column(
                "experiment_id", _BIGINT, sa.ForeignKey("experiments.id"), nullable=False
            ),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("created_at", _TS, nullable=False),
            sa.Column("frozen_at", _TS),
            sa.Column("hypothesis", sa.Text()),
            sa.Column("mechanism", sa.Text()),
            sa.Column("counterparty", sa.Text()),
            sa.Column("falsification", sa.Text()),
            sa.Column("universe_selector", sa.Text()),
            sa.Column("universe_exclusions", sa.Text()),
            sa.Column("entry_rule", sa.Text()),
            sa.Column("exit_rule", sa.Text()),
            sa.Column("sizing_rule", sa.Text()),
            sa.Column("execution_style", sa.String(16)),
            sa.Column("independent_variable", sa.Text()),
            sa.Column("held_constant_json", sa.JSON()),
            sa.Column("control_required", sa.Boolean(), nullable=False),
            sa.Column("control_exemption_reason", sa.Text()),
            sa.Column("metrics_json", sa.JSON()),
            sa.Column("sample_json", sa.JSON()),
            sa.Column("costs_json", sa.JSON()),
            sa.Column("risk_json", sa.JSON()),
            sa.Column("provenance_json", sa.JSON()),
            sa.Column("monitoring_json", sa.JSON()),
            sa.Column("docs_json", sa.JSON()),
            sa.Column("contract_json", sa.JSON()),
            sa.Column("fingerprint", sa.String(64)),
            sa.Column("pre_registration_hash", sa.String(64)),
            sa.Column(
                "predecessor_version_id", _BIGINT, sa.ForeignKey("experiment_versions.id")
            ),
            sa.Column("change_reason", sa.Text()),
            sa.UniqueConstraint("experiment_id", "version", name="uq_experiment_version"),
        )

    if "experiment_arms" not in existing:
        op.create_table(
            "experiment_arms",
            sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
            sa.Column(
                "version_id", _BIGINT, sa.ForeignKey("experiment_versions.id"), nullable=False
            ),
            sa.Column("arm_key", sa.String(48), nullable=False),
            sa.Column("role", sa.String(24), nullable=False),
            sa.Column("description", sa.Text()),
            sa.Column("params_json", sa.JSON()),
            sa.Column("strategy_tag", sa.String(32)),
            sa.Column("created_at", _TS, nullable=False),
            sa.UniqueConstraint("version_id", "arm_key", name="uq_experiment_arm"),
        )

    if "experiment_epochs" not in existing:
        op.create_table(
            "experiment_epochs",
            sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
            sa.Column(
                "version_id", _BIGINT, sa.ForeignKey("experiment_versions.id"), nullable=False
            ),
            sa.Column("epoch_number", sa.Integer(), nullable=False),
            sa.Column(
                "platform_snapshot_id",
                _BIGINT,
                sa.ForeignKey("platform_snapshots.id"),
                nullable=False,
            ),
            sa.Column("started_at", _TS, nullable=False),
            sa.Column("ended_at", _TS),
            sa.Column("reason", sa.Text()),
            sa.Column("impact_class", sa.String(4)),
            sa.Column("notes", sa.Text()),
            sa.Column("created_at", _TS, nullable=False),
            sa.UniqueConstraint("version_id", "epoch_number", name="uq_experiment_epoch"),
        )

    if "experiment_deployments" not in existing:
        op.create_table(
            "experiment_deployments",
            sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
            sa.Column(
                "epoch_id", _BIGINT, sa.ForeignKey("experiment_epochs.id"), nullable=False
            ),
            sa.Column("deployment_key", sa.String(64), nullable=False, unique=True),
            sa.Column("stage", sa.String(16), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column(
                "twin_of_deployment_id", _BIGINT, sa.ForeignKey("experiment_deployments.id")
            ),
            sa.Column(
                "platform_snapshot_id", _BIGINT, sa.ForeignKey("platform_snapshots.id")
            ),
            sa.Column("code_fingerprint", sa.String(64)),
            sa.Column("config_json", sa.JSON()),
            sa.Column("started_at", _TS, nullable=False),
            sa.Column("ended_at", _TS),
            sa.Column("notes", sa.Text()),
            sa.Column("created_at", _TS, nullable=False),
        )
        op.create_index(
            "ix_experiment_deployments_epoch", "experiment_deployments", ["epoch_id"]
        )

    if "experiment_deployment_arms" not in existing:
        op.create_table(
            "experiment_deployment_arms",
            sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
            sa.Column(
                "deployment_id",
                _BIGINT,
                sa.ForeignKey("experiment_deployments.id"),
                nullable=False,
            ),
            sa.Column("arm_id", _BIGINT, sa.ForeignKey("experiment_arms.id"), nullable=False),
            sa.Column("strategy_tag", sa.String(32)),
            sa.Column("created_at", _TS, nullable=False),
            sa.UniqueConstraint("deployment_id", "arm_id", name="uq_deployment_arm"),
        )
        op.create_index(
            "ix_experiment_deployment_arms_tag",
            "experiment_deployment_arms",
            ["strategy_tag"],
        )

    if "experiment_gates" not in existing:
        op.create_table(
            "experiment_gates",
            sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
            sa.Column(
                "version_id", _BIGINT, sa.ForeignKey("experiment_versions.id"), nullable=False
            ),
            sa.Column("gate_key", sa.String(48), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("from_state", sa.String(16)),
            sa.Column("to_state", sa.String(16)),
            sa.Column("spec_json", sa.JSON(), nullable=False),
            sa.Column("spec_hash", sa.String(64), nullable=False),
            sa.Column("registered_at", _TS, nullable=False),
            sa.Column("evidence_started_at", _TS),
            sa.Column(
                "superseded_by_gate_id", _BIGINT, sa.ForeignKey("experiment_gates.id")
            ),
            sa.Column("notes", sa.Text()),
            sa.Column("created_at", _TS, nullable=False),
            sa.UniqueConstraint("version_id", "gate_key", name="uq_experiment_gate"),
        )

    if "experiment_gate_results" not in existing:
        op.create_table(
            "experiment_gate_results",
            sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
            sa.Column("gate_id", _BIGINT, sa.ForeignKey("experiment_gates.id"), nullable=False),
            sa.Column(
                "experiment_id", _BIGINT, sa.ForeignKey("experiments.id"), nullable=False
            ),
            sa.Column(
                "version_id", _BIGINT, sa.ForeignKey("experiment_versions.id"), nullable=False
            ),
            sa.Column("epoch_id", _BIGINT, sa.ForeignKey("experiment_epochs.id")),
            sa.Column("verdict", sa.String(20), nullable=False),
            sa.Column("evidence_start", _TS),
            sa.Column("evidence_end", _TS),
            sa.Column("sample_json", sa.JSON()),
            sa.Column("metrics_json", sa.JSON()),
            sa.Column(
                "platform_snapshot_id", _BIGINT, sa.ForeignKey("platform_snapshots.id")
            ),
            sa.Column("metric_revision", sa.String(64)),
            sa.Column("computed_at", _TS, nullable=False),
            sa.Column("computed_by", sa.String(32), nullable=False),
            sa.Column("evidence_ref", sa.Text()),
            sa.Column("explanation", sa.Text()),
            sa.Column("created_at", _TS, nullable=False),
        )
        op.create_index(
            "ix_experiment_gate_results_gate",
            "experiment_gate_results",
            ["gate_id", "computed_at"],
        )

    if "experiment_state_transitions" not in existing:
        op.create_table(
            "experiment_state_transitions",
            sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
            sa.Column(
                "experiment_id", _BIGINT, sa.ForeignKey("experiments.id"), nullable=False
            ),
            sa.Column("from_state", sa.String(16)),
            sa.Column("to_state", sa.String(16), nullable=False),
            sa.Column("occurred_at", _TS, nullable=False),
            sa.Column("actor", sa.String(24), nullable=False),
            sa.Column("approved_by", sa.String(64)),
            sa.Column("reason", sa.Text()),
            sa.Column(
                "gate_result_id", _BIGINT, sa.ForeignKey("experiment_gate_results.id")
            ),
            sa.Column("version_id", _BIGINT, sa.ForeignKey("experiment_versions.id")),
            sa.Column("epoch_id", _BIGINT, sa.ForeignKey("experiment_epochs.id")),
            sa.Column("created_at", _TS, nullable=False),
        )
        op.create_index(
            "ix_experiment_state_transitions_exp",
            "experiment_state_transitions",
            ["experiment_id", "occurred_at"],
        )

    if "experiment_integrity_events" not in existing:
        op.create_table(
            "experiment_integrity_events",
            sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
            sa.Column(
                "experiment_id", _BIGINT, sa.ForeignKey("experiments.id"), nullable=False
            ),
            sa.Column("version_id", _BIGINT, sa.ForeignKey("experiment_versions.id")),
            sa.Column("epoch_id", _BIGINT, sa.ForeignKey("experiment_epochs.id")),
            sa.Column(
                "deployment_id", _BIGINT, sa.ForeignKey("experiment_deployments.id")
            ),
            sa.Column("kind", sa.String(48), nullable=False),
            sa.Column("severity", sa.String(16), nullable=False),
            sa.Column("detected_at", _TS, nullable=False),
            sa.Column("description", sa.Text()),
            sa.Column("details_json", sa.JSON()),
            sa.Column("resolved_at", _TS),
            sa.Column("resolution", sa.Text()),
            sa.Column("created_at", _TS, nullable=False),
        )
        op.create_index(
            "ix_experiment_integrity_events_exp",
            "experiment_integrity_events",
            ["experiment_id", "detected_at"],
        )

    if "experiment_legacy_evidence" not in existing:
        op.create_table(
            "experiment_legacy_evidence",
            sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
            sa.Column(
                "experiment_id", _BIGINT, sa.ForeignKey("experiments.id"), nullable=False
            ),
            sa.Column("version_id", _BIGINT, sa.ForeignKey("experiment_versions.id")),
            sa.Column("label", sa.String(120), nullable=False),
            sa.Column("evidence_class", sa.String(32), nullable=False),
            sa.Column("source", sa.Text()),
            sa.Column("window_start", _TS),
            sa.Column("window_end", _TS),
            sa.Column("summary_json", sa.JSON()),
            sa.Column("notes", sa.Text()),
            sa.Column("created_at", _TS, nullable=False),
        )
        op.create_index(
            "ix_experiment_legacy_evidence_exp",
            "experiment_legacy_evidence",
            ["experiment_id"],
        )

    if "platform_impact_actions" not in existing:
        op.create_table(
            "platform_impact_actions",
            sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
            sa.Column(
                "revision_id", _BIGINT, sa.ForeignKey("platform_revisions.id"), nullable=False
            ),
            sa.Column(
                "experiment_id", _BIGINT, sa.ForeignKey("experiments.id"), nullable=False
            ),
            sa.Column("epoch_id", _BIGINT, sa.ForeignKey("experiment_epochs.id")),
            sa.Column("impact_class", sa.String(4), nullable=False),
            sa.Column("action", sa.String(28), nullable=False),
            sa.Column("rationale", sa.Text()),
            sa.Column("decided_by", sa.String(64)),
            sa.Column("decided_at", _TS, nullable=False),
            sa.Column("created_at", _TS, nullable=False),
            sa.UniqueConstraint("revision_id", "experiment_id", name="uq_impact_per_experiment"),
        )


def downgrade() -> None:
    op.drop_table("platform_impact_actions")
    op.drop_index(
        "ix_experiment_legacy_evidence_exp", table_name="experiment_legacy_evidence"
    )
    op.drop_table("experiment_legacy_evidence")
    op.drop_index(
        "ix_experiment_integrity_events_exp", table_name="experiment_integrity_events"
    )
    op.drop_table("experiment_integrity_events")
    op.drop_index(
        "ix_experiment_state_transitions_exp", table_name="experiment_state_transitions"
    )
    op.drop_table("experiment_state_transitions")
    op.drop_index("ix_experiment_gate_results_gate", table_name="experiment_gate_results")
    op.drop_table("experiment_gate_results")
    op.drop_table("experiment_gates")
    op.drop_index(
        "ix_experiment_deployment_arms_tag", table_name="experiment_deployment_arms"
    )
    op.drop_table("experiment_deployment_arms")
    op.drop_index("ix_experiment_deployments_epoch", table_name="experiment_deployments")
    op.drop_table("experiment_deployments")
    op.drop_table("experiment_epochs")
    op.drop_table("experiment_arms")
    op.drop_table("experiment_versions")
    op.drop_index("ix_experiments_state", table_name="experiments")
    op.drop_index("ix_experiments_key", table_name="experiments")
    op.drop_table("experiments")
    op.drop_table("platform_snapshot_items")
    op.drop_table("platform_snapshots")
    op.drop_index("ix_platform_revisions_status", table_name="platform_revisions")
    op.drop_table("platform_revisions")
    op.drop_table("platform_components")
