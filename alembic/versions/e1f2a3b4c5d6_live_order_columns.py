"""Add strategy / event_ticker / client_order_id to live_orders for the live executor.

The Phase-1 live_orders scaffold predates the per-strategy book model and idempotent
client_order_id keying. The live executor needs these to dedup entries per (event,strategy)
and to resolve indeterminate orders by client_order_id during reconciliation.

Revision ID: e1f2a3b4c5d6
Revises: b2c3d4e5f6a7
Create Date: 2026-06-14
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = 'e1f2a3b4c5d6'
down_revision: str | None = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("live_orders", sa.Column("client_order_id", sa.String(128), nullable=True))
    op.add_column("live_orders", sa.Column("event_ticker", sa.String(128), nullable=True))
    op.add_column("live_orders", sa.Column("strategy", sa.String(32), nullable=True))
    op.create_index("ix_live_orders_client_order_id", "live_orders", ["client_order_id"])
    op.create_index("ix_live_orders_event_ticker", "live_orders", ["event_ticker"])
    op.create_index("ix_live_orders_strategy", "live_orders", ["strategy"])


def downgrade() -> None:
    op.drop_index("ix_live_orders_strategy", table_name="live_orders")
    op.drop_index("ix_live_orders_event_ticker", table_name="live_orders")
    op.drop_index("ix_live_orders_client_order_id", table_name="live_orders")
    op.drop_column("live_orders", "strategy")
    op.drop_column("live_orders", "event_ticker")
    op.drop_column("live_orders", "client_order_id")
