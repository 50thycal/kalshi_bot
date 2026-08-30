"""PERP-V1 tape: read-only perpetual-futures market, order-book, funding and
collector-telemetry tables (docs/PERP_V1_THESIS.md, Probe 1).

Instrument tables, not book tables: no strategy tag, no position, no order.
One tape serves all three PERP-V1 arms, which is why the experiment is one
experiment rather than three (DEC-008).

Every table keeps `raw_json`. The survey established that `reference_price`,
`settlement_mark_price` and `liquidation_mark_price` arrive as nested objects
whose inner shape has never been read, and that the funding payload's shape is
unknown entirely — so extracted columns hold what was measured and the raw
payload holds everything else. `perp_collector_cycles` is the coverage
denominator: `perp_data_coverage_pct` is a pre-registered gate clause on every
arm, and rows that were never written are indistinguishable from a market that
did not exist unless the attempt is recorded.

Revision ID: a43d8c7e82f5
Revises: c8e1a2b3d4f5
Create Date: 2026-08-30
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a43d8c7e82f5"
down_revision: str | None = "c8e1a2b3d4f5"
branch_labels = None
depends_on = None


def _json():
    return postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "perp_market_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(64), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32)),
        sa.Column("bid", sa.Float()),
        sa.Column("ask", sa.Float()),
        sa.Column("price", sa.Float()),
        sa.Column("open_interest", sa.Float()),
        sa.Column("open_interest_notional_usd", sa.Float()),
        sa.Column("volume", sa.Float()),
        sa.Column("volume_24h", sa.Float()),
        sa.Column("reference_price", sa.Float()),
        sa.Column("settlement_mark_price", sa.Float()),
        sa.Column("premium_bps", sa.Float()),
        sa.Column("reference_price_json", _json()),
        sa.Column("settlement_mark_price_json", _json()),
        sa.Column("raw_json", _json()),
    )
    op.create_index(
        "ix_perp_market_snapshots_ticker_time",
        "perp_market_snapshots",
        ["ticker", "captured_at"],
    )

    op.create_table(
        "perp_orderbook_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(64), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("best_bid", sa.Float()),
        sa.Column("best_ask", sa.Float()),
        sa.Column("bid_depth", sa.Float()),
        sa.Column("ask_depth", sa.Float()),
        sa.Column("depth_imbalance", sa.Float()),
        sa.Column("raw_json", _json()),
    )
    op.create_index(
        "ix_perp_orderbook_snapshots_ticker_time",
        "perp_orderbook_snapshots",
        ["ticker", "captured_at"],
    )

    op.create_table(
        "perp_funding_observations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(64)),
        sa.Column("observed_at", sa.DateTime(timezone=True)),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("funding_rate", sa.Float()),
        sa.Column("source_key", sa.String(128), nullable=False),
        sa.Column("raw_json", _json()),
        # Overlapping fetches are deliberate — a re-fetch is cheap, a gap in
        # funding is not — so the uniqueness is what stops a funding period
        # being counted twice into arm B's carry.
        sa.UniqueConstraint(
            "ticker", "observed_at", "source_key", name="uq_perp_funding_obs"
        ),
    )
    op.create_index(
        "ix_perp_funding_obs_ticker_time",
        "perp_funding_observations",
        ["ticker", "observed_at"],
    )

    op.create_table(
        "perp_collector_cycles",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("markets_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("market_snapshots", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("orderbook_snapshots", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("funding_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes_json", _json()),
    )
    op.create_index(
        "ix_perp_collector_cycles_time", "perp_collector_cycles", ["started_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_perp_collector_cycles_time", table_name="perp_collector_cycles")
    op.drop_table("perp_collector_cycles")
    op.drop_index("ix_perp_funding_obs_ticker_time", table_name="perp_funding_observations")
    op.drop_table("perp_funding_observations")
    op.drop_index(
        "ix_perp_orderbook_snapshots_ticker_time", table_name="perp_orderbook_snapshots"
    )
    op.drop_table("perp_orderbook_snapshots")
    op.drop_index("ix_perp_market_snapshots_ticker_time", table_name="perp_market_snapshots")
    op.drop_table("perp_market_snapshots")
