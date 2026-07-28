"""Two read-path indexes for the evolution fleet dashboard.

Index-only and additive: no column is added, changed or dropped, and no write path
is touched, so this is safe to apply while the evo worker is running and it cannot
alter any bot's behavior.

Both back queries the dashboard introduced that the existing indexes cannot serve:

* ``evo_fills (agent_uuid, created_at)`` — the trade table recovers a bot-closed
  position's exit price from its sell fills. The only existing index on the table
  is on ``order_id``, so that lookup was a sequential scan that grows with every
  fill the fleet ever makes.

* ``evo_positions (ledger, status, closed_at)`` — the fleet rollup, the
  per-generation history and the closed-trade activity stream all filter by ledger
  and status and order by close time. The existing ``(agent_uuid, ledger, status)``
  index is agent-leading and cannot answer a ledger-leading predicate.

Revision ID: a1c4e70b92d3
Revises: d5e6f7a8b9c0
"""

from __future__ import annotations

from alembic import op

revision: str = "a1c4e70b92d3"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_evo_fills_agent_time", "evo_fills", ["agent_uuid", "created_at"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_evo_positions_ledger_status", "evo_positions", ["ledger", "status", "closed_at"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_evo_positions_ledger_status", table_name="evo_positions", if_exists=True)
    op.drop_index("ix_evo_fills_agent_time", table_name="evo_fills", if_exists=True)
