"""Add live_paper_twins + live_paper_parity_events: the live/paper parallel-run harness.

Any strategy promoted to live money now runs a FRESH paper twin beside it, parameterized to the
live knobs (entry price rule, dollar sizing, open cap) and started at the same instant. The twin
epoch row scopes both sides of the comparison to one window (the incumbent paper book carries
history the live run does not, which is what makes a naive paper-vs-live read a mirage), and the
parity events tape records, per candidate market per cycle, what the incumbent paper book, the
twin, and the real live attempt each did — so divergence is attributable (gate vs fill vs price)
instead of guessed. See docs/LIVE_PAPER_TWIN.md.

Guarded with an inspector check because the schema is also materialised by create_all() at
startup (and in CI), so a plain create_table would collide on a from-scratch database — the same
pattern the mmsell_position_ticks / mmsell_candidate_ticks migrations use.

Revision ID: d5e6f7a8b9c0
Revises: c2d3e4f5a6b7
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None

_TWINS = "live_paper_twins"
_EVENTS = "live_paper_parity_events"

_BIGINT = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if _TWINS not in existing:
        op.create_table(
            _TWINS,
            sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
            sa.Column("twin_tag", sa.String(24), nullable=False),
            sa.Column("live_tag", sa.String(32), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ended_at", sa.DateTime(timezone=True)),
            sa.Column("params_json", sa.JSON()),
            sa.Column("notes", sa.Text()),
        )
        op.create_index("ix_live_paper_twins_twin_tag", _TWINS, ["twin_tag"], unique=True)
        op.create_index("ix_live_paper_twins_live_tag", _TWINS, ["live_tag"])

    if _EVENTS not in existing:
        op.create_table(
            _EVENTS,
            sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
            sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("twin_tag", sa.String(24), nullable=False),
            sa.Column("live_tag", sa.String(32), nullable=False),
            sa.Column("market_ticker", sa.String(128), nullable=False),
            sa.Column("series", sa.String(32)),
            sa.Column("hours_to_close", sa.Float()),
            sa.Column("parent_outcome", sa.String(24)),
            sa.Column("parent_price", sa.Integer()),
            sa.Column("twin_outcome", sa.String(24)),
            sa.Column("twin_price", sa.Integer()),
            sa.Column("twin_quantity", sa.Integer()),
            sa.Column("live_outcome", sa.String(32)),
            sa.Column("live_price", sa.Integer()),
            sa.Column("live_quantity", sa.Integer()),
            sa.Column("yes_mid", sa.Float()),
            sa.Column("no_bid", sa.Integer()),
            sa.Column("no_ask", sa.Integer()),
        )
        op.create_index("ix_lp_parity_twin_time", _EVENTS, ["twin_tag", "recorded_at"])
        op.create_index("ix_lp_parity_ticker_time", _EVENTS, ["market_ticker", "recorded_at"])


def downgrade() -> None:
    op.drop_index("ix_lp_parity_ticker_time", table_name=_EVENTS)
    op.drop_index("ix_lp_parity_twin_time", table_name=_EVENTS)
    op.drop_table(_EVENTS)
    op.drop_index("ix_live_paper_twins_live_tag", table_name=_TWINS)
    op.drop_index("ix_live_paper_twins_twin_tag", table_name=_TWINS)
    op.drop_table(_TWINS)
