"""Series registry: the observation half — when each series was first seen listed.

The registry (`kalshi_bot/registry/__init__.py`) splits decisions from observations. Decisions
— which series is graduated, who reviewed it, when — live in `series_manifest.json` and move
only by PR. This table holds what the worker observes and no human should commit.

`first_seen_at` is the column that cannot be reconstructed after the fact. Without it a series
Kalshi listed this morning is indistinguishable from one traded for months, so "is this market
new?" — the question that triggers a review at all — has no answer. Everything else a reviewer
wants (settled counts, P&L, per-book exposure) is derivable from `paper_trades` and `markets`
and is deliberately not duplicated here.

Backfill is intentionally omitted. `markets.created_at` records when the SCANNER first wrote a
market row, which for series predating the collector is when the backfill ran, not when Kalshi
listed the series — seeding from it would manufacture arrival dates that look authoritative and
are wrong. Existing series therefore acquire a `first_seen_at` on the next scan that sees them,
and the review report labels rows first seen before this migration as unknown-arrival rather
than new. The column earns its meaning going forward, which is the only way it can be true.

Revision ID: b6c1d4e9f207
Revises: a43d8c7e82f5
Create Date: 2026-09-06
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "b6c1d4e9f207"
down_revision: str | None = "a43d8c7e82f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "series_observations",
        sa.Column("series", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("markets_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sample_ticker", sa.String(length=128), nullable=True),
        sa.Column("sample_title", sa.Text(), nullable=True),
        sa.Column("state_at_first_seen", sa.String(length=32), nullable=True),
    )
    # The arrivals queue is "ordered by when it showed up", which is the only scan this table
    # ever takes; at a few hundred rows it hardly matters, but the report reads it every run.
    op.create_index("ix_series_observations_first_seen",
                    "series_observations", ["first_seen_at"])


def downgrade() -> None:
    op.drop_index("ix_series_observations_first_seen", table_name="series_observations")
    op.drop_table("series_observations")
