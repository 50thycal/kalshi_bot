"""Add mmsell_position_ticks: the per-cycle intraday price tape for markets holding an open
mmsell paper position.

The mmsell (sports) tickers were never written to market_snapshots (that table is the main
scanner's different universe — 0 rows for them), so a per-ticker exit-rule / fill replay was
impossible. This table captures one price tick per open-mmsell ticker per management cycle
(off the orderbook the engine already fetches), feeding scripts/mmsell_exit_study.py.

Guarded with an inspector check because the schema is also materialised by create_all() at
startup (and in CI), so a plain create_table would collide on a from-scratch database — same
pattern the evo migrations use.

Revision ID: b1c2d3e4f5a6
Revises: e9f0a1b2c3d4
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "e9f0a1b2c3d4"
branch_labels = None
depends_on = None

_TABLE = "mmsell_position_ticks"


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE in sa.inspect(bind).get_table_names():
        return  # already created by create_all()
    op.create_table(
        _TABLE,
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  primary_key=True, autoincrement=True),
        sa.Column("market_ticker", sa.String(128), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yes_bid", sa.Integer()),
        sa.Column("yes_ask", sa.Integer()),
        sa.Column("no_bid", sa.Integer()),
        sa.Column("no_ask", sa.Integer()),
        sa.Column("mid", sa.Float()),
        sa.Column("volume", sa.Integer()),
    )
    op.create_index("ix_mmsell_ticks_ticker_time", _TABLE, ["market_ticker", "captured_at"])


def downgrade() -> None:
    op.drop_index("ix_mmsell_ticks_ticker_time", table_name=_TABLE)
    op.drop_table(_TABLE)
