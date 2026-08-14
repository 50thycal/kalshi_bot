"""live_order_queue_ticks: where each resting live order sat in Kalshi's queue.

The measurement that turns maker adverse selection from an inference into an observation.
`docs/MMSELL_FILL_MODEL.md` names it as the entire paper->live gap (~2c/contract) and says we
cannot replay it because the books throw away the data a fill model needs. This is that data.

What it unblocks immediately: `docs/MMSELL_OFFSET_AB.md` spends real money learning what 1c of
queue priority buys, measured through downstream P&L -- a comparison whose own gate computes it
needs ~47,106 contracts/arm to resolve at +0.5c, against the ~270/arm actually accrued. Queue rank
answers the mechanical question (did the cent move us up, past how many contracts?) on the ~35
orders resting at any moment.

Every column except the timestamp is nullable on purpose. A sample can legitimately fail -- the
order fills or cancels mid-cycle -- and a null is never written silently: the sampler logs the
parse failure and stores the raw payload alongside, so a Kalshi shape change is countable rather
than invisible. `contracts_ahead` is captured only when Kalshi offers it and is deliberately NOT
derived from the rank: rank 3 behind three 1-lots is a different trade from rank 3 behind three
500-lots.

Revision ID: a1c4e7b2d9f3
Revises: e3f4a5b6c7d8
Create Date: 2026-08-14
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "a1c4e7b2d9f3"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "live_order_queue_ticks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("live_order_id", sa.BigInteger(), nullable=True),
        sa.Column("kalshi_order_id", sa.String(length=128), nullable=True),
        sa.Column("strategy", sa.String(length=32), nullable=True),
        sa.Column("market_ticker", sa.String(length=128), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("queue_position", sa.Integer(), nullable=True),
        sa.Column("contracts_ahead", sa.Integer(), nullable=True),
        sa.Column("limit_price", sa.Integer(), nullable=True),
        sa.Column("rest_seconds", sa.Integer(), nullable=True),
        sa.Column("raw_json", JSONB(), nullable=True),
        sa.ForeignKeyConstraint(["live_order_id"], ["live_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    # The two readings this table exists for: one order's samples over time (did we move up?),
    # and one book's samples in a window (does the offset arm rank better?).
    op.create_index("ix_lqt_order_time", "live_order_queue_ticks",
                    ["live_order_id", "captured_at"])
    op.create_index("ix_lqt_strategy_time", "live_order_queue_ticks",
                    ["strategy", "captured_at"])
    op.create_index("ix_live_order_queue_ticks_kalshi_order_id", "live_order_queue_ticks",
                    ["kalshi_order_id"])


def downgrade() -> None:
    op.drop_index("ix_live_order_queue_ticks_kalshi_order_id",
                  table_name="live_order_queue_ticks")
    op.drop_index("ix_lqt_strategy_time", table_name="live_order_queue_ticks")
    op.drop_index("ix_lqt_order_time", table_name="live_order_queue_ticks")
    op.drop_table("live_order_queue_ticks")
