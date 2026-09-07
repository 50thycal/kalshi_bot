"""live_order_queue_decisions: the audit trail for queue-aware cancellation.

One row per (resting live order, cycle) while `LIVE_QUEUE_CANCEL_MODE` is `shadow` or `live`
(docs/MMSELL_QUEUE_AWARE_CANCEL.md). Each row carries the queue telemetry the frozen rule saw,
the rule inputs it applied, what it decided, and whether a cancel was actually sent. In shadow
mode nothing is ever sent; the rows are the treatment's counterfactual, joined later to
`live_orders` by `kalshi_order_id` to learn what the order really did.

Missing telemetry is a named `telemetry_status`, never a queue position of zero, and the
final order state is deliberately NOT copied here: `live_orders` stays the single record.

Revision ID: a0bd7f9c48de
Revises: b6c1d4e9f207
Create Date: 2026-09-07
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "a0bd7f9c48de"
down_revision = "b6c1d4e9f207"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "live_order_queue_decisions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("live_order_id", sa.BigInteger(), nullable=True),
        sa.Column("kalshi_order_id", sa.String(length=128), nullable=True),
        sa.Column("strategy", sa.String(length=32), nullable=True),
        sa.Column("market_ticker", sa.String(length=128), nullable=True),
        sa.Column("event_ticker", sa.String(length=128), nullable=True),
        sa.Column("experiment_deployment_arm_id", sa.BigInteger(), nullable=True),
        sa.Column("mode", sa.String(length=16), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("side", sa.String(length=8), nullable=True),
        sa.Column("limit_price", sa.Integer(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("filled_quantity_before", sa.Integer(), nullable=True),
        sa.Column("telemetry_status", sa.String(length=24), nullable=True),
        sa.Column("queue_position", sa.Integer(), nullable=True),
        sa.Column("contracts_ahead", sa.Integer(), nullable=True),
        sa.Column("queue_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("order_age_seconds", sa.Integer(), nullable=True),
        sa.Column("remaining_timeout_seconds", sa.Integer(), nullable=True),
        sa.Column("cap_bound", sa.Boolean(), nullable=True),
        sa.Column("book_open_count", sa.Integer(), nullable=True),
        sa.Column("book_open_cap", sa.Integer(), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=True),
        sa.Column("acted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cancel_result", sa.Text(), nullable=True),
        sa.Column("rule_version", sa.String(length=48), nullable=True),
        sa.Column("estimated_fill_probability_pct", sa.Float(), nullable=True),
        sa.Column("rule_inputs_json", JSONB(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["live_order_id"], ["live_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lqd_order_time", "live_order_queue_decisions",
                    ["live_order_id", "decided_at"])
    op.create_index("ix_lqd_strategy_time", "live_order_queue_decisions",
                    ["strategy", "decided_at"])
    op.create_index("ix_lqd_arm_time", "live_order_queue_decisions",
                    ["experiment_deployment_arm_id", "decided_at"])
    op.create_index("ix_live_order_queue_decisions_kalshi_order_id",
                    "live_order_queue_decisions", ["kalshi_order_id"])


def downgrade() -> None:
    op.drop_index("ix_live_order_queue_decisions_kalshi_order_id",
                  table_name="live_order_queue_decisions")
    op.drop_index("ix_lqd_arm_time", table_name="live_order_queue_decisions")
    op.drop_index("ix_lqd_strategy_time", table_name="live_order_queue_decisions")
    op.drop_index("ix_lqd_order_time", table_name="live_order_queue_decisions")
    op.drop_table("live_order_queue_decisions")
