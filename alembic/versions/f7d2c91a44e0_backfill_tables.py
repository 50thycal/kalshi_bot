"""Kalshi history backfill tables (separate provenance from live-collected data).

Revision ID: f7d2c91a44e0
Revises: c4a7e91b3f02
Create Date: 2026-06-11
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = 'f7d2c91a44e0'
down_revision: str | None = 'c4a7e91b3f02'
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "backfill_weather_markets",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("fetched_at", TS, nullable=False),
        sa.Column("market_ticker", sa.String(128), nullable=False),
        sa.Column("event_ticker", sa.String(128)),
        sa.Column("series_ticker", sa.String(64)),
        sa.Column("city", sa.String(32)),
        sa.Column("kind", sa.String(8)),
        sa.Column("target_date", sa.String(16)),
        sa.Column("subtitle", sa.String(64)),
        sa.Column("low_f", sa.Float()),
        sa.Column("high_f", sa.Float()),
        sa.Column("result", sa.String(8)),
        sa.Column("open_time", TS),
        sa.Column("close_time", TS),
        sa.Column("candles_fetched", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("candle_count", sa.Integer()),
        sa.Column("source", sa.String(16)),
        sa.UniqueConstraint("market_ticker", name="uq_backfill_weather_markets_ticker"),
    )
    op.create_index(
        "ix_backfill_weather_markets_event_ticker", "backfill_weather_markets", ["event_ticker"]
    )
    op.create_index(
        "ix_backfill_weather_markets_close", "backfill_weather_markets", ["close_time"]
    )
    op.create_index(
        "ix_backfill_weather_markets_pending",
        "backfill_weather_markets",
        ["candles_fetched", "close_time"],
    )

    op.create_table(
        "backfill_weather_candles",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("market_ticker", sa.String(128), nullable=False),
        sa.Column("end_period_ts", TS, nullable=False),
        sa.Column("period_minutes", sa.Integer()),
        sa.Column("price_open", sa.Float()),
        sa.Column("price_high", sa.Float()),
        sa.Column("price_low", sa.Float()),
        sa.Column("price_close", sa.Float()),
        sa.Column("yes_bid_close", sa.Float()),
        sa.Column("yes_ask_close", sa.Float()),
        sa.Column("volume", sa.Integer()),
        sa.Column("open_interest", sa.Integer()),
        sa.UniqueConstraint("market_ticker", "end_period_ts", name="uq_backfill_candle"),
    )


def downgrade() -> None:
    op.drop_table("backfill_weather_candles")
    op.drop_index("ix_backfill_weather_markets_pending", table_name="backfill_weather_markets")
    op.drop_index("ix_backfill_weather_markets_close", table_name="backfill_weather_markets")
    op.drop_index(
        "ix_backfill_weather_markets_event_ticker", table_name="backfill_weather_markets"
    )
    op.drop_table("backfill_weather_markets")
