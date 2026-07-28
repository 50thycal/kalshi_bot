"""Add stop_reason to evo_llm_usage.

Additive-only: a new nullable-by-default column, no existing data touched, no
other table affected. Safe to apply while the evo worker is running.

Records the provider's own stop signal for each LLM call (Anthropic
`stop_reason` / OpenAI-compatible `finish_reason`), persisted so an operator
can directly verify whether a configured output-token cap is actually a hard
ceiling for a given model — this was previously only inferable from
output_tokens, which is unreliable for a reasoning model that can report
output_tokens near or over a nominal cap without ever having been cut off.

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "a2b3c4d5e6f7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "evo_llm_usage",
        sa.Column("stop_reason", sa.String(length=32), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("evo_llm_usage", "stop_reason")
