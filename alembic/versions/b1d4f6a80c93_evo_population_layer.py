"""Evo population layer: programs, generations, candidates, genomes, runs, decisions.

The `evo_pop_*` namespace for the evolutionary search layer
(`kalshi_bot/evo/population/`). Additive only: nothing existing is touched, including
the LLM-agent organism's `evo_*` tables, whose EvoAgent/EvoCohort/EvoGenome mean
something different and are deliberately left alone.

Created from the ORM table objects so the DDL matches the models' BigIntId/JSONType/TS
variants exactly — the same technique as the evo base and announcements migrations,
rather than a hand-written copy of the schema that can drift from `models.py`.

Revision ID: b1d4f6a80c93
Revises: e5a1c9d2f473
"""

from __future__ import annotations

from alembic import op
from kalshi_bot.evo.population.models import (
    EvoCandidate,
    EvoCandidateLedger,
    EvoDecision,
    EvoFinding,
    EvoFitness,
    EvoGeneration,
    EvoGenomeVersion,
    EvoJournalEntry,
    EvoMutationProposal,
    EvoProgram,
    EvoRun,
    EvoRunTrade,
)

revision: str = "b1d4f6a80c93"
down_revision: str | None = "e5a1c9d2f473"
branch_labels = None
depends_on = None

# Foreign-key order: a referenced table is always created before its referrers, and
# dropped after them.
TABLES = (
    EvoProgram,
    EvoGeneration,
    EvoCandidate,
    EvoGenomeVersion,
    EvoMutationProposal,
    EvoRun,
    EvoRunTrade,
    EvoCandidateLedger,
    EvoFitness,
    EvoDecision,
    EvoJournalEntry,
    EvoFinding,
)


def upgrade() -> None:
    bind = op.get_bind()
    for model in TABLES:
        model.__table__.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for model in reversed(TABLES):
        model.__table__.drop(bind, checkfirst=True)
