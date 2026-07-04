"""XGAME in-play tape collection: matched game-market pairs + raw cross-venue trades.

game_market_matches: Kalshi per-team moneyline <-> Polymarket same-team/day market,
matched by (day, normalized team). game_tape_snapshots: raw trade-tape rows from both
venues for matched games — the dataset behind the XGAME lead-lag thesis
(docs/IDEA_MODEL_20260704.md); analysis builds ~10s bars from the venue timestamps.

Revision ID: f3a4b5c6d7e8
Revises: e8a1b2c3d4f5
Create Date: 2026-07-04
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: str | None = "e8a1b2c3d4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_market_matches",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sport", sa.String(24)),
        sa.Column("day", sa.String(16)),
        sa.Column("team", sa.String(48), nullable=False),
        sa.Column("kalshi_series", sa.String(32)),
        sa.Column("kalshi_ticker", sa.String(128), nullable=False),
        sa.Column("kalshi_event_ticker", sa.String(128)),
        sa.Column("kalshi_title", sa.Text()),
        sa.Column("pm_condition_id", sa.String(80)),
        sa.Column("pm_token_id", sa.String(128), nullable=False),
        sa.Column("pm_question", sa.Text()),
        sa.Column("close_time", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("kalshi_since_ts", sa.DateTime(timezone=True)),
        sa.Column("pm_since_ts", sa.DateTime(timezone=True)),
        sa.Column("kalshi_trades", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pm_trades", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_polled_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("kalshi_ticker", "pm_token_id", name="uq_game_match_pair"),
    )
    op.create_index(
        "ix_game_market_matches_status", "game_market_matches", ["status"]
    )

    op.create_table(
        "game_tape_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "match_id",
            sa.BigInteger(),
            sa.ForeignKey("game_market_matches.id"),
            nullable=False,
        ),
        sa.Column("venue", sa.String(12), nullable=False),
        sa.Column("market_id", sa.String(128)),
        sa.Column("trade_id", sa.String(160), nullable=False),
        sa.Column("traded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price_cents", sa.Float()),
        sa.Column("team_prob_cents", sa.Float()),
        sa.Column("size", sa.Float()),
        sa.Column("taker_side", sa.String(8)),
        sa.UniqueConstraint("venue", "trade_id", name="uq_game_tape_trade"),
    )
    op.create_index(
        "ix_game_tape_match_venue_time",
        "game_tape_snapshots",
        ["match_id", "venue", "traded_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_game_tape_match_venue_time", table_name="game_tape_snapshots")
    op.drop_table("game_tape_snapshots")
    op.drop_index("ix_game_market_matches_status", table_name="game_market_matches")
    op.drop_table("game_market_matches")
