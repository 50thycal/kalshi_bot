"""mmsell candidate ticks: expected-expiration clock + contract sub-structure.

Two capture gaps this closes, both on the same row the entry scan already writes:

  * `hours_to_expiration` — the only FORWARD-LOOKING estimate of when an in-play contest
    resolves. `hours_to_close` comes from Kalshi's `close_time`, which on sports is a
    far-future fallback (KXUFCFIGHT reported 335h on a fight that resolved in 0.4h, measured
    2026-08-05). The timing study can score HISTORY on realized closed_at - created_at, but a
    live entry gate cannot — see docs/MMSELL_TIMING_STUDY.md.
  * `strike_type` / `floor_strike` / `cap_strike` / `yes_sub_title` — the contract's
    sub-structure. The type taxonomy says a ticker is a `spread` or a `total`; these say which
    LINE, so a book can be cut at "MLB spreads of 3+ runs" rather than all spreads.
  * `depth_at_best_bid` / `depth_at_best_ask` — book depth at the touch. A TAKER entry (buy NO
    == sell YES into the bid) consumes the first; a MAKER entry queues behind the second. The
    endgame taker result is `paper - spread`, which silently assumes liquidity at the touch is
    unlimited — these are what turn that into a measurable capacity ceiling.

All are nullable and backfill-free by design: rows already captured keep NULL, and the
analysis scripts report coverage rather than assuming it.

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = 'e3f4a5b6c7d8'
down_revision: str | None = 'd2e3f4a5b6c7'
branch_labels = None
depends_on = None

_COLUMNS = (
    ("hours_to_expiration", sa.Float()),
    ("strike_type", sa.String(16)),
    ("floor_strike", sa.Float()),
    ("cap_strike", sa.Float()),
    ("yes_sub_title", sa.String(64)),
    ("depth_at_best_bid", sa.Integer()),
    ("depth_at_best_ask", sa.Integer()),
)


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        op.add_column("mmsell_candidate_ticks", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _type in reversed(_COLUMNS):
        op.drop_column("mmsell_candidate_ticks", name)
