"""Add positions.quantity_fp for fractional position tracking.

Fractional trading rolled out per-market: a position can be e.g. 3.12 contracts. The
integer `quantity` rounds it, which makes the close under-size (it leaves a fractional
remainder open). Store the fractional size so the close can size to the exact remainder.

Revision ID: a3f8c1d09b21
Revises: e1f2a3b4c5d6
Create Date: 2026-06-15
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = 'a3f8c1d09b21'
down_revision: str | None = 'e1f2a3b4c5d6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("positions", sa.Column("quantity_fp", sa.Numeric(18, 4), nullable=True))


def downgrade() -> None:
    op.drop_column("positions", "quantity_fp")
