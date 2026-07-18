"""Evolutionary agent system: all evo_* tables.

One additive migration for the entire evo subsystem (docs/EVOLUTIONARY_AGENT_SYSTEM.md):
agents/cohorts/lineage, immutable genome + memory versions, heartbeats + listeners,
budgets + LLM ledger, data registry, declarative strategies + sandbox runs, per-agent
paper portfolios (orders/fills/positions/ledgers/transfers), fitness + transitions +
births + retirements, tickets, graveyard, audit. Nothing existing is touched.

Tables are created from the ORM metadata (kalshi_bot/evo/models.py) rather than
duplicated by hand: this is the FIRST evo migration, the models are brand-new, and a
hand-copied 30-table DDL block is a larger correctness risk than the coupling. Any
future evo schema change must be a normal hand-written diff migration on top.

Revision ID: a9e0c1d2f3b4
Revises: f3a4b5c6d7e8
"""

from __future__ import annotations

from alembic import op

from kalshi_bot.evo import models as evo_models  # noqa: F401 — registers evo_* tables
from kalshi_bot.models import Base

revision: str = "a9e0c1d2f3b4"
down_revision: str | None = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def _evo_tables():
    return [t for t in Base.metadata.sorted_tables if t.name.startswith("evo_")]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _evo_tables():
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_evo_tables()):
        table.drop(bind, checkfirst=True)
