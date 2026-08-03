"""mmsell settlement-date metadata (feeds the settlement-date concentration cap).

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-08-03
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = 'd2e3f4a5b6c7'
down_revision: str | None = 'c1d2e3f4a5b6'
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "mmsell_settlement_meta",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("market_ticker", sa.String(128), nullable=False),
        sa.Column("event_ticker", sa.String(128)),
        sa.Column("series_ticker", sa.String(64)),
        sa.Column("close_time", TS, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("market_ticker", name="uq_mmsell_settlement_meta_ticker"),
    )
    op.create_index(
        "ix_mmsell_settlement_meta_close", "mmsell_settlement_meta", ["close_time"]
    )
    op.create_index(
        "ix_mmsell_settlement_meta_event_ticker", "mmsell_settlement_meta", ["event_ticker"]
    )


def downgrade() -> None:
    op.drop_index("ix_mmsell_settlement_meta_event_ticker", "mmsell_settlement_meta")
    op.drop_index("ix_mmsell_settlement_meta_close", "mmsell_settlement_meta")
    op.drop_table("mmsell_settlement_meta")
