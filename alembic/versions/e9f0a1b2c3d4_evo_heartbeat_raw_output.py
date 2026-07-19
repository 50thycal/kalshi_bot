"""Add evo_heartbeats.raw_output_text: bounded raw model output captured ONLY on
a failed (degraded, parse/validation error) heartbeat.

Private operator diagnostic data — queried via the read-only ops SQL channel to
root-cause LLM output failures (e.g. malformed JSON) that the previous design
made undiagnosable (no raw text was ever persisted). Never surfaced by the
dashboard and never shown to any agent; nothing reads it except direct SQL.
Additive, nullable column on an existing table.

Uses `ADD COLUMN IF NOT EXISTS` rather than plain `op.add_column`: migration
a9e0c1d2f3b4 (the original evo-system migration) creates all evo_* tables from
LIVE ORM metadata at whatever moment it runs, not a frozen snapshot — so on a
database built from scratch (e.g. CI), it already includes raw_output_text
(added to the EvoHeartbeat model above) by the time this migration runs, and a
plain ADD COLUMN collides with it. On the existing production database (whose
evo_heartbeats table was created before this column existed in the model), the
column is genuinely missing and this correctly adds it. IF NOT EXISTS is
correct for both.

Revision ID: e9f0a1b2c3d4
Revises: d7e8f9a0b1c2
"""

from __future__ import annotations

from alembic import op

revision: str = "e9f0a1b2c3d4"
down_revision: str | None = "d7e8f9a0b1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE evo_heartbeats ADD COLUMN IF NOT EXISTS raw_output_text TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE evo_heartbeats DROP COLUMN IF EXISTS raw_output_text")
