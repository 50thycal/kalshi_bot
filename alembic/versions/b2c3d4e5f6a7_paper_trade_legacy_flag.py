"""Flag legacy (retired-window) paper trades so the PnL report excludes them.

Adds paper_trades.legacy and marks the day-1 h12 window trades (favorite-only, before
the nws/cal books existed and before the window set moved to 20/14/8) as legacy.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-13
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = 'b2c3d4e5f6a7'
down_revision: str | None = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "paper_trades",
        sa.Column("legacy", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Retire the day-1 h12 entry window (no longer traded; nws/cal never ran it).
    op.execute("UPDATE paper_trades SET legacy = true WHERE strategy ~ '_h12$'")


def downgrade() -> None:
    op.drop_column("paper_trades", "legacy")
