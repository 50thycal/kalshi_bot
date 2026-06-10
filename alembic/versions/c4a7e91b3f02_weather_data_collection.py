"""weather data collection: low markets (kind), station observations, ensembles,
bucket-ladder snapshots.

Revision ID: c4a7e91b3f02
Revises: 58e9542edff8
Create Date: 2026-06-10
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4a7e91b3f02'
down_revision: str | None = '58e9542edff8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # kind ('high' | 'low') on forecasts + settlements; legacy rows are all highs.
    op.add_column('weather_forecasts', sa.Column('kind', sa.String(length=8), nullable=True))
    op.add_column('weather_settlements', sa.Column('kind', sa.String(length=8), nullable=True))
    op.execute("UPDATE weather_forecasts SET kind = 'high' WHERE kind IS NULL")
    op.execute("UPDATE weather_settlements SET kind = 'high' WHERE kind IS NULL")

    op.create_table(
        'weather_observations',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('city', sa.String(length=32), nullable=False),
        sa.Column('station', sa.String(length=16), nullable=True),
        sa.Column('target_date', sa.String(length=16), nullable=True),
        sa.Column('running_max_f', sa.Float(), nullable=True),
        sa.Column('running_min_f', sa.Float(), nullable=True),
        sa.Column('obs_count', sa.Integer(), nullable=True),
        sa.Column('last_obs_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_weather_observations_city_time', 'weather_observations', ['city', 'captured_at']
    )

    op.create_table(
        'weather_ensembles',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('city', sa.String(length=32), nullable=False),
        sa.Column('target_date', sa.String(length=16), nullable=True),
        sa.Column('kind', sa.String(length=8), nullable=False),
        sa.Column('model', sa.String(length=32), nullable=True),
        sa.Column('member_count', sa.Integer(), nullable=True),
        sa.Column('mean_f', sa.Float(), nullable=True),
        sa.Column('std_f', sa.Float(), nullable=True),
        sa.Column('members_json', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_weather_ensembles_city_time', 'weather_ensembles', ['city', 'captured_at']
    )

    op.create_table(
        'weather_bucket_snapshots',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('event_ticker', sa.String(length=128), nullable=False),
        sa.Column('market_ticker', sa.String(length=128), nullable=True),
        sa.Column('city', sa.String(length=32), nullable=True),
        sa.Column('kind', sa.String(length=8), nullable=True),
        sa.Column('subtitle', sa.String(length=64), nullable=True),
        sa.Column('low_f', sa.Float(), nullable=True),
        sa.Column('high_f', sa.Float(), nullable=True),
        sa.Column('yes_bid_cents', sa.Float(), nullable=True),
        sa.Column('yes_ask_cents', sa.Float(), nullable=True),
        sa.Column('mid_cents', sa.Float(), nullable=True),
        sa.Column('volume', sa.Integer(), nullable=True),
        sa.Column('hours_to_close', sa.Float(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_weather_bucket_snapshots_event_time',
        'weather_bucket_snapshots',
        ['event_ticker', 'captured_at'],
    )


def downgrade() -> None:
    op.drop_index('ix_weather_bucket_snapshots_event_time', table_name='weather_bucket_snapshots')
    op.drop_table('weather_bucket_snapshots')
    op.drop_index('ix_weather_ensembles_city_time', table_name='weather_ensembles')
    op.drop_table('weather_ensembles')
    op.drop_index('ix_weather_observations_city_time', table_name='weather_observations')
    op.drop_table('weather_observations')
    op.drop_column('weather_settlements', 'kind')
    op.drop_column('weather_forecasts', 'kind')
