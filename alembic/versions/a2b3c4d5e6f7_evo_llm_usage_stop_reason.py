"""Add stop_reason to evo_llm_usage.

Additive-only: a new nullable-by-default column, no existing data touched, no
other table affected. Safe to apply while the evo worker is running.

Records the provider's own stop signal for each LLM call (Anthropic
`stop_reason` / OpenAI-compatible `finish_reason`), persisted so an operator
can directly verify whether a configured output-token cap is actually a hard
ceiling for a given model — this was previously only inferable from
output_tokens, which is unreliable for a reasoning model that can report
output_tokens near or over a nominal cap without ever having been cut off.

Uses `ADD COLUMN IF NOT EXISTS` rather than plain `op.add_column`: migration
a9e0c1d2f3b4 (the original evo-system migration) creates all evo_* tables from
LIVE ORM metadata at whatever moment it runs, not a frozen snapshot — so on a
database built from scratch (e.g. CI), it already includes stop_reason (added
to the EvoLlmUsage model above) by the time this migration runs, and a plain
ADD COLUMN collides with it (confirmed live: CI failed with
`DuplicateColumn: column "stop_reason" of relation "evo_llm_usage" already
exists`, exactly the case e9f0a1b2c3d4's docstring already describes for this
same repo pattern). On the existing production database (whose evo_llm_usage
table was created before this column existed in the model), the column is
genuinely missing and this correctly adds it. IF NOT EXISTS is correct for
both.

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
"""

from __future__ import annotations

from alembic import op

revision = "a2b3c4d5e6f7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE evo_llm_usage ADD COLUMN IF NOT EXISTS "
        "stop_reason VARCHAR(32) NOT NULL DEFAULT ''"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE evo_llm_usage DROP COLUMN IF EXISTS stop_reason")
