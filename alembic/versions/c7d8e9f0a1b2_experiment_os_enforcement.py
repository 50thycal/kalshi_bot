"""Experiment OS enforcement + runtime lineage (PR 4 of the spec).

Adds:
  * experiment_os_enforcement — the append-only recorded enforcement state
    (mode / effective_at / actor / reason / cutover id / system version /
    readiness evidence). The enforcement cutover is an explicit recorded event,
    never inferred from a deploy or merge timestamp.
  * experiment_deployments.grandfathered — imported legacy deployments continue
    under enforcement; their evolution may not bypass Experiment OS.
  * experiment_deployments.config_fingerprint — the registered-config baseline
    the drift check compares runtime configuration against.
  * paper_trades.experiment_deployment_arm_id, live_orders.experiment_deployment_arm_id
    — nullable lineage back to deployment → epoch → version → experiment →
    platform snapshot. Legacy rows stay NULL; history is never rewritten.

Deploy-inert: no seed rows, enforcement defaults to OFF (empty table), nullable
column adds, and nothing reads the new columns until enforcement is wired on.
Guarded with inspector checks because create_all() also materialises the schema.

Revision ID: c7d8e9f0a1b2
Revises: b4c5d6e7f8a9
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: str | None = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None

_BIGINT = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "experiment_os_enforcement" not in existing_tables:
        op.create_table(
            "experiment_os_enforcement",
            sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
            sa.Column("cutover_id", sa.String(64), nullable=False, unique=True),
            sa.Column("mode", sa.String(12), nullable=False),
            sa.Column("preceding_mode", sa.String(12)),
            sa.Column("effective_at", _TS, nullable=False),
            sa.Column("actor", sa.String(64), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("system_version", sa.String(64), nullable=False),
            sa.Column("readiness_json", sa.JSON()),
            sa.Column("created_at", _TS, nullable=False),
        )

    def columns(table: str) -> set[str]:
        return {c["name"] for c in inspector.get_columns(table)}

    dep_cols = columns("experiment_deployments")
    if "grandfathered" not in dep_cols:
        op.add_column(
            "experiment_deployments",
            sa.Column(
                "grandfathered", sa.Boolean(), nullable=False, server_default="0"
            ),
        )
    if "config_fingerprint" not in dep_cols:
        op.add_column(
            "experiment_deployments", sa.Column("config_fingerprint", sa.String(64))
        )

    is_postgres = bind.dialect.name == "postgresql"
    for table in ("paper_trades", "live_orders"):
        cols = columns(table)
        if "experiment_deployment_arm_id" not in cols:
            op.add_column(
                table, sa.Column("experiment_deployment_arm_id", _BIGINT, nullable=True)
            )
            op.create_index(
                f"ix_{table}_experiment_deployment_arm_id",
                table,
                ["experiment_deployment_arm_id"],
            )
            # The real referential constraint (Postgres only; the ORM deliberately
            # declares a plain column so kalshi_bot.models stays importable without
            # the experiment_os package, and sqlite does not enforce FKs anyway).
            if is_postgres:
                op.create_foreign_key(
                    f"fk_{table}_experiment_deployment_arm",
                    table,
                    "experiment_deployment_arms",
                    ["experiment_deployment_arm_id"],
                    ["id"],
                )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    for table in ("live_orders", "paper_trades"):
        if is_postgres:
            op.drop_constraint(
                f"fk_{table}_experiment_deployment_arm", table, type_="foreignkey"
            )
        op.drop_index(f"ix_{table}_experiment_deployment_arm_id", table_name=table)
        op.drop_column(table, "experiment_deployment_arm_id")
    op.drop_column("experiment_deployments", "config_fingerprint")
    op.drop_column("experiment_deployments", "grandfathered")
    op.drop_table("experiment_os_enforcement")
