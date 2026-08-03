"""mmsell regime settled-history capture tables.

Kalshi retains only a rolling ~70-day window of settled markets, so last season is
permanently unavailable and history must be captured forward as it settles. Same
provenance split as the backfill_weather_* tables (REST archive, not live capture).

Revision ID: c1d2e3f4a5b6
Revises: b8c9d0e1f2a3
Create Date: 2026-08-03
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = 'c1d2e3f4a5b6'
down_revision: str | None = 'b8c9d0e1f2a3'
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "backfill_regime_markets",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("fetched_at", TS, nullable=False),
        sa.Column("market_ticker", sa.String(128), nullable=False),
        sa.Column("event_ticker", sa.String(128)),
        sa.Column("series_ticker", sa.String(64)),
        sa.Column("regime", sa.String(16)),
        sa.Column("title", sa.String(256)),
        sa.Column("result", sa.String(8)),
        sa.Column("volume", sa.Float()),
        sa.Column("open_interest", sa.Float()),
        sa.Column("open_time", TS),
        sa.Column("close_time", TS),
        sa.Column("candles_fetched", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("candle_count", sa.Integer()),
        sa.Column("source", sa.String(16)),
        sa.UniqueConstraint("market_ticker", name="uq_backfill_regime_market_ticker"),
    )
    op.create_index("ix_backfill_regime_markets_close", "backfill_regime_markets", ["close_time"])
    op.create_index(
        "ix_backfill_regime_markets_pending",
        "backfill_regime_markets",
        ["candles_fetched", "close_time"],
    )
    op.create_index(
        "ix_backfill_regime_markets_regime",
        "backfill_regime_markets",
        ["regime", "close_time"],
    )
    op.create_index(
        "ix_backfill_regime_markets_event_ticker",
        "backfill_regime_markets",
        ["event_ticker"],
    )

    op.create_table(
        "backfill_regime_candles",
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
        sa.UniqueConstraint("market_ticker", "end_period_ts", name="uq_backfill_regime_candle"),
    )


def downgrade() -> None:
    op.drop_table("backfill_regime_candles")
    op.drop_index("ix_backfill_regime_markets_event_ticker", "backfill_regime_markets")
    op.drop_index("ix_backfill_regime_markets_regime", "backfill_regime_markets")
    op.drop_index("ix_backfill_regime_markets_pending", "backfill_regime_markets")
    op.drop_index("ix_backfill_regime_markets_close", "backfill_regime_markets")
    op.drop_table("backfill_regime_markets")
