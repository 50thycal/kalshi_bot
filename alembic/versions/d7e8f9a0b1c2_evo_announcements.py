"""Operator announcements table (evo_announcements).

The broadcast channel to the whole agent population: active announcements are
injected into every heartbeat's context so all agents learn a system change at
once (kalshi_bot/evo/announcements.py). Additive — nothing existing is touched.

Created from the ORM table object so the DDL matches the model's BigIntId/TS
variants exactly (same technique as the evo base migration, but scoped to the one
new table rather than the whole subsystem).

Revision ID: d7e8f9a0b1c2
Revises: a9e0c1d2f3b4
"""

from __future__ import annotations

from alembic import op
from kalshi_bot.evo.models import EvoAnnouncement

revision: str = "d7e8f9a0b1c2"
down_revision: str | None = "a9e0c1d2f3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    EvoAnnouncement.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    EvoAnnouncement.__table__.drop(op.get_bind(), checkfirst=True)
