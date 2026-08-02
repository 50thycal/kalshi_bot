"""Index crypto_ladder_snapshots by (market_ticker, captured_at) for theta's hot-market check.

Index-only and additive: no column is added, changed or dropped, and no write path is
touched, so this is safe to apply while the live trading worker is running and while a
position is open. It cannot alter any trading behaviour.

Why it is justified: theta's live entry now asks, per candidate per cycle, "what was this
market's last quote before now?" (kalshi_bot/live/sizing.py's is_hot_entry, reading the
ladder tape via repository.latest_theta_no_bid_before):

    SELECT yes_ask_cents, captured_at FROM crypto_ladder_snapshots
     WHERE market_ticker = :t AND captured_at < :before
     ORDER BY captured_at DESC LIMIT 1

The table's only index is `ix_crypto_ladder_snapshots_event_time` on
(event_ticker, captured_at), which cannot serve a market_ticker-leading predicate. And this
table grows fast: the theta tracker writes up to `theta_snapshot_rows_cap` (240) rows per
cycle at a 5-minute cadence, i.e. up to ~69k rows/day. Without this index the lookup
degrades to a sequential scan **inside the single-threaded live trading loop**, which is a
latency hazard for order placement, not just a slow read.

`CREATE INDEX` (not CONCURRENTLY) is deliberate and matches ix_positions_ticker_time: alembic
runs migrations inside a transaction. Should the table reach a size where the build blocks
meaningfully, build it concurrently by hand instead.

Revision ID: b8c9d0e1f2a3
Revises: a2b3c4d5e6f7
"""

from __future__ import annotations

from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | None = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_crypto_ladder_mkt_time", "crypto_ladder_snapshots",
        ["market_ticker", "captured_at"], if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_crypto_ladder_mkt_time", table_name="crypto_ladder_snapshots", if_exists=True)
