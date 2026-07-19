"""Add evo_heartbeats.raw_output_text: bounded raw model output captured ONLY on
a failed (degraded, parse/validation error) heartbeat.

Private operator diagnostic data — queried via the read-only ops SQL channel to
root-cause LLM output failures (e.g. malformed JSON) that the previous design
made undiagnosable (no raw text was ever persisted). Never surfaced by the
dashboard and never shown to any agent; nothing reads it except direct SQL.
Additive, nullable column on an existing table.

Revision ID: e9f0a1b2c3d4
Revises: d7e8f9a0b1c2
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "e9f0a1b2c3d4"
down_revision: str | None = "d7e8f9a0b1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evo_heartbeats", sa.Column("raw_output_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("evo_heartbeats", "raw_output_text")
