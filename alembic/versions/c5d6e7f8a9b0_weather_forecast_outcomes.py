"""Persisted forecast->settlement validation dataset (weather_forecast_outcomes).

One row per (settled event, intraday market-state cycle), materialized at settlement
by replaying the live-collected raw tables and labeling with the actual outcome.

Revision ID: c5d6e7f8a9b0
Revises: a3f8c1d09b21
Create Date: 2026-06-17
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'c5d6e7f8a9b0'
down_revision: str | None = 'a3f8c1d09b21'
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "weather_forecast_outcomes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        # identity / grain
        sa.Column("event_ticker", sa.String(128), nullable=False),
        sa.Column("city", sa.String(32)),
        sa.Column("kind", sa.String(8)),
        sa.Column("target_date", sa.String(16)),
        sa.Column("captured_at", TS, nullable=False),
        sa.Column("hours_to_close", sa.Float()),
        # features at this cycle (no lookahead)
        sa.Column("forecast_f", sa.Float()),
        sa.Column("forecast_source", sa.String(16)),
        sa.Column("ens_mean_f", sa.Float()),
        sa.Column("ens_std_f", sa.Float()),
        sa.Column("ens_models", sa.Integer()),
        sa.Column("market_implied_mean_f", sa.Float()),
        sa.Column("market_fav_low_f", sa.Float()),
        sa.Column("market_fav_high_f", sa.Float()),
        sa.Column("market_fav_mid_cents", sa.Float()),
        sa.Column("divergence_f", sa.Float()),
        sa.Column("obs_running_max_f", sa.Float()),
        sa.Column("obs_running_min_f", sa.Float()),
        sa.Column("pm_implied_mean_f", sa.Float()),
        # outcome label
        sa.Column("actual_high_f", sa.Float()),
        sa.Column("actual_low_f", sa.Float()),
        sa.Column("winning_low_f", sa.Float()),
        sa.Column("winning_high_f", sa.Float()),
        sa.Column("winning_subtitle", sa.String(64)),
        # calibration / skill labels
        sa.Column("market_prob_winner", sa.Float()),
        sa.Column("ens_prob_winner", sa.Float()),
        sa.Column("forecast_abs_err_f", sa.Float()),
        sa.Column("market_abs_err_f", sa.Float()),
        # provenance / debug
        sa.Column("n_buckets", sa.Integer()),
        sa.Column("materialized_at", TS, nullable=False),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()).with_variant(
            sa.JSON(), "sqlite")),
        sa.UniqueConstraint("event_ticker", "captured_at", name="uq_wfo_event_capture"),
    )
    op.create_index("ix_wfo_event", "weather_forecast_outcomes", ["event_ticker"])
    op.create_index(
        "ix_wfo_city_kind_date", "weather_forecast_outcomes", ["city", "kind", "target_date"]
    )
    op.create_index("ix_wfo_htc", "weather_forecast_outcomes", ["hours_to_close"])


def downgrade() -> None:
    op.drop_index("ix_wfo_htc", table_name="weather_forecast_outcomes")
    op.drop_index("ix_wfo_city_kind_date", table_name="weather_forecast_outcomes")
    op.drop_index("ix_wfo_event", table_name="weather_forecast_outcomes")
    op.drop_table("weather_forecast_outcomes")
