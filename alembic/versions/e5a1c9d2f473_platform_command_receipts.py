"""Receipt ledger for the Platform Change Review boot-command transport.

A SEPARATE table from experiment_os_issue_commands on purpose: an issue command
must never be able to mutate a Platform Revision, so the two vocabularies and the
two ledgers stay disjoint rather than sharing one widened surface.

Revision ID: e5a1c9d2f473
Revises: c4f7a2b8e1d9
"""

import sqlalchemy as sa

from alembic import op

revision = "e5a1c9d2f473"
down_revision = "c4f7a2b8e1d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "experiment_os_platform_commands",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("command_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("actor_role", sa.String(length=32), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column(
            "schema_version", sa.Integer(), server_default="1", nullable=False
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # The unique constraint IS the exactly-once mechanism: the claim is an
        # ON CONFLICT DO NOTHING insert against it.
        sa.UniqueConstraint("command_id"),
    )
    op.create_index(
        "ix_experiment_os_platform_commands_status",
        "experiment_os_platform_commands",
        ["status", "requested_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_experiment_os_platform_commands_status",
        table_name="experiment_os_platform_commands",
    )
    op.drop_table("experiment_os_platform_commands")
