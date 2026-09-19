"""Incentive reward ledger: append-only balance observations and their residual.

The liquidity reward Kalshi credits us has no endpoint — the programme object carries terms,
never our share of them. This table is the arithmetic route: each row differences the account
balance against the previous reading and attributes the change to fills and settlements, so the
unexplained remainder (`residual_cents`) can be read as a candidate reward.

Revision ID: b3d7f1a409ce
Revises: d9f1c3a7b2e4
Create Date: 2026-09-19
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b3d7f1a409ce"
down_revision = "d9f1c3a7b2e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incentive_balance_observations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("balance_cents", sa.BigInteger(), nullable=False),
        sa.Column("prev_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prev_balance_cents", sa.BigInteger(), nullable=True),
        sa.Column("delta_cents", sa.BigInteger(), nullable=True),
        sa.Column("buy_cost_cents", sa.BigInteger(), nullable=True),
        sa.Column("sell_proceeds_cents", sa.BigInteger(), nullable=True),
        sa.Column("fees_cents", sa.BigInteger(), nullable=True),
        sa.Column("settlement_cents", sa.BigInteger(), nullable=True),
        sa.Column("fills_counted", sa.Integer(), nullable=True),
        sa.Column("settlements_counted", sa.Integer(), nullable=True),
        sa.Column("residual_cents", sa.BigInteger(), nullable=True),
        sa.Column("presumed_transfer", sa.Boolean(), nullable=True),
        sa.Column("notes_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_incentive_balance_obs_time", "incentive_balance_observations", ["at"])


def downgrade() -> None:
    op.drop_index("ix_incentive_balance_obs_time",
                  table_name="incentive_balance_observations")
    op.drop_table("incentive_balance_observations")
