"""Polymarket per-bucket implied-probability snapshots (cross-market signal).

Revision ID: a1b2c3d4e5f6
Revises: f7d2c91a44e0
Create Date: 2026-06-11
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = 'a1b2c3d4e5f6'
down_revision: str | None = 'f7d2c91a44e0'
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "polymarket_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("captured_at", TS, nullable=False),
        sa.Column("city", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("target_date", sa.String(16)),
        sa.Column("subtitle", sa.String(64)),
        sa.Column("low_f", sa.Float()),
        sa.Column("high_f", sa.Float()),
        sa.Column("yes_prob", sa.Float()),
        sa.Column("source", sa.String(20)),
    )
    op.create_index(
        "ix_polymarket_snapshots_city_time",
        "polymarket_snapshots",
        ["city", "kind", "target_date", "captured_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_polymarket_snapshots_city_time", table_name="polymarket_snapshots")
    op.drop_table("polymarket_snapshots")
