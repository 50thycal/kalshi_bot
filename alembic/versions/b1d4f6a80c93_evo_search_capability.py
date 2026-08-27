"""Evo historical search: run, candidates, trade tape.

Artifacts of the search capability the Evo agents invoke (`kalshi_bot/evo/search/`).
Every row is attributable to an existing `EvoAgent` and the trading-genome revision the
agent searched around — there is no second population here, and no lifecycle table.
`evo_agents`, `evo_cohorts`, `evo_genomes`, `evo_fitness`, `evo_births` and
`evo_retirements` remain the authoritative organism.

Created from the ORM table objects so the DDL matches the models' BigIntId/JSONType/TS
variants exactly — the same technique as the evo base and announcements migrations,
rather than a hand-written copy of the schema that can drift from `models.py`.

Revision ID: b1d4f6a80c93
Revises: e5a1c9d2f473
"""

from __future__ import annotations

from alembic import op
from kalshi_bot.evo.search.models import (
    EvoSearchCandidate,
    EvoSearchRun,
    EvoSearchTrade,
)

revision: str = "b1d4f6a80c93"
down_revision: str | None = "e5a1c9d2f473"
branch_labels = None
depends_on = None

# Foreign-key order: a referenced table is created before its referrers, dropped after.
TABLES = (EvoSearchRun, EvoSearchCandidate, EvoSearchTrade)


def upgrade() -> None:
    bind = op.get_bind()
    for model in TABLES:
        model.__table__.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for model in reversed(TABLES):
        model.__table__.drop(bind, checkfirst=True)
