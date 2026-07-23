"""Add mmsell_candidate_ticks: the per-cycle pre-entry price tape for EVERY in-band mmsell
candidate market (whether or not a position was opened).

mmsell_position_ticks captures only markets already HELD; the fill model still leans on a
live-*calibrated* estimate because it can't replay the resting-order fill of the wider candidate
universe. This table closes that gap — one orderbook snapshot per in-band candidate per cycle,
captured off the book the entry scan already fetches — so a direct per-ticker fill replay becomes
possible ('would a resting buy-NO at the no-bid have been lifted before close?'). It is the
step-0 collection for the next mmsell live re-test (docs/MMSELL_FILL_MODEL.md §5 #2).

Guarded with an inspector check because the schema is also materialised by create_all() at
startup (and in CI), so a plain create_table would collide on a from-scratch database — the same
pattern the mmsell_position_ticks / evo migrations use.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: str | None = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None

_TABLE = "mmsell_candidate_ticks"


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
        sa.Column("series", sa.String(32)),
        sa.Column("hours_to_close", sa.Float()),
        sa.Column("yes_bid", sa.Integer()),
        sa.Column("yes_ask", sa.Integer()),
        sa.Column("no_bid", sa.Integer()),
        sa.Column("no_ask", sa.Integer()),
        sa.Column("mid", sa.Float()),
        sa.Column("volume", sa.Integer()),
    )
    op.create_index("ix_mmsell_cand_ticks_ticker_time", _TABLE, ["market_ticker", "captured_at"])


def downgrade() -> None:
    op.drop_index("ix_mmsell_cand_ticks_ticker_time", table_name=_TABLE)
    op.drop_table(_TABLE)
