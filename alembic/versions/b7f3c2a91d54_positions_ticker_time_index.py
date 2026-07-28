"""Index positions by (market_ticker, captured_at) for the live/paper dashboard.

Index-only and additive: no column is added, changed or dropped, and no write path
is touched, so this is safe to apply while the live trading worker is running and
while a position is open. It cannot alter any trading behaviour.

Why it is justified: `positions` is an append-only snapshot table — the reconcile
loop writes a row per held market per cycle, so it is the largest table this
dashboard reads (tens of thousands of rows and growing every cycle). Every live-leg
read asks the same question, "the newest snapshot for each of these tickers":

    SELECT ... FROM positions
     WHERE market_ticker IN (...)
     ORDER BY captured_at DESC

The table has no index at all today, so that is a sequential scan on every page
load, worsening for as long as the bot runs.

`CREATE INDEX` (not CONCURRENTLY) is deliberate: alembic runs migrations inside a
transaction, and on a table of this size the build takes milliseconds. Should the
table grow by orders of magnitude, build it concurrently by hand instead.

Revision ID: b7f3c2a91d54
Revises: d5e6f7a8b9c0
"""

from __future__ import annotations

from alembic import op

revision: str = "b7f3c2a91d54"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_positions_ticker_time", "positions", ["market_ticker", "captured_at"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_positions_ticker_time", table_name="positions", if_exists=True)
