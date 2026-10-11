"""Partial index for the live incentive book's programme read (WS-024).

`programs.current_programs(now=...)` returns the running liquidity programmes: ~5k rows in a
~190k-row table (2026-10-11). The only index covering it was (superseded_at, disappeared_at),
which ~35k rows satisfy, so Postgres scanned the whole 287 MB heap several times a live cycle.
That read was ~80% of all Postgres disk reads and the main reason the database sat at its 2 GB
memory cap. This index makes it an index read of the matching rows. No data change.

Revision ID: c4e8a1f2b9d0
Revises: b3d7f1a409ce
Create Date: 2026-10-11
"""

import sqlalchemy as sa

from alembic import op

revision = "c4e8a1f2b9d0"
down_revision = "b3d7f1a409ce"
branch_labels = None
depends_on = None

_WHERE = "superseded_at IS NULL AND disappeared_at IS NULL AND incentive_type = 'liquidity'"


def upgrade() -> None:
    op.create_index(
        "ix_incentive_programs_live_end", "incentive_programs", ["end_date"],
        postgresql_where=sa.text(_WHERE), sqlite_where=sa.text(_WHERE))


def downgrade() -> None:
    op.drop_index("ix_incentive_programs_live_end", table_name="incentive_programs")
