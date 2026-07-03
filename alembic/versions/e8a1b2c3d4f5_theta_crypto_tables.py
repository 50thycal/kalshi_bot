"""Theta book data collection: crypto spot candles + ladder snapshots.

crypto_spot_candles: rolling 1-min spot closes (Coinbase) feeding the theta model's
remaining-window return distribution. crypto_ladder_snapshots: hourly crypto ladder
quotes with the model probability at capture — the theta research dataset.

Revision ID: e8a1b2c3d4f5
Revises: d4e5f6a7b8c9
Create Date: 2026-07-03
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "e8a1b2c3d4f5"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crypto_spot_candles",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("product", sa.String(16), nullable=False),
        sa.Column("minute_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("source", sa.String(16)),
        sa.UniqueConstraint("product", "minute_ts", name="uq_spot_candle"),
    )
    op.create_index(
        "ix_crypto_spot_candles_product_time",
        "crypto_spot_candles",
        ["product", "minute_ts"],
    )

    op.create_table(
        "crypto_ladder_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("series", sa.String(32)),
        sa.Column("event_ticker", sa.String(128), nullable=False),
        sa.Column("market_ticker", sa.String(128)),
        sa.Column("strike_type", sa.String(16)),
        sa.Column("floor_strike", sa.Float()),
        sa.Column("cap_strike", sa.Float()),
        sa.Column("yes_bid_cents", sa.Float()),
        sa.Column("yes_ask_cents", sa.Float()),
        sa.Column("mid_cents", sa.Float()),
        sa.Column("volume", sa.Float()),
        sa.Column("minutes_to_close", sa.Float()),
        sa.Column("spot", sa.Float()),
        sa.Column("model_p", sa.Float()),
        sa.Column("model_excess_cents", sa.Float()),
    )
    op.create_index(
        "ix_crypto_ladder_snapshots_event_time",
        "crypto_ladder_snapshots",
        ["event_ticker", "captured_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_crypto_ladder_snapshots_event_time", table_name="crypto_ladder_snapshots")
    op.drop_table("crypto_ladder_snapshots")
    op.drop_index("ix_crypto_spot_candles_product_time", table_name="crypto_spot_candles")
    op.drop_table("crypto_spot_candles")
