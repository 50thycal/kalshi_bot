"""HRRR forecast grading columns on weather_forecast_outcomes.

Adds hrrr_f / hrrr_abs_err_f / hrrr_divergence_f so the HRRR point forecast (Open-Meteo
ncep_hrrr_conus, collected into weather_forecasts with source='openmeteo_hrrr') is graded
head-to-head against NWS and the market. Nullable: already-materialized rows keep these
NULL; HRRR accumulates forward.

Revision ID: d4e5f6a7b8c9
Revises: c5d6e7f8a9b0
Create Date: 2026-06-17
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = 'd4e5f6a7b8c9'
down_revision: str | None = 'c5d6e7f8a9b0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("weather_forecast_outcomes", sa.Column("hrrr_f", sa.Float()))
    op.add_column("weather_forecast_outcomes", sa.Column("hrrr_abs_err_f", sa.Float()))
    op.add_column("weather_forecast_outcomes", sa.Column("hrrr_divergence_f", sa.Float()))


def downgrade() -> None:
    op.drop_column("weather_forecast_outcomes", "hrrr_divergence_f")
    op.drop_column("weather_forecast_outcomes", "hrrr_abs_err_f")
    op.drop_column("weather_forecast_outcomes", "hrrr_f")
