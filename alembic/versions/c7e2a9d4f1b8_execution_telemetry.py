"""Execution telemetry: the raw record around every live MMSELL resting order (WS-019).

docs/MMSELL_QUEUE_FILL_TELEMETRY.md. Read-only measurement; no trading behaviour changes.

Six new tables and four new columns on `live_order_queue_ticks`:

* `execution_order_context` — 1:1 with `live_orders`: what the strategy knew at decision time,
  written BEFORE the order is sent, plus the submit/ack/cancel stamps (the only post-submit
  columns). Lets a fill model condition on entry-time state without leaking the future.
* `execution_book_events` — every `orderbook_snapshot`/`orderbook_delta` WebSocket message for
  a tracked market, with the exchange `seq`/`ts_ms` beside our `received_at`, on the YES price
  scale for both sides (`price_convention`), plus the reconstructed level quantity.
* `execution_trade_events` — every public trade on a tracked market (`trade_id` unique).
* `execution_fill_events` — our fills from the private `fill` channel with the exchange
  millisecond timestamp the REST poll loses; `rest_fill_id` once reconciled to `fills`.
* `execution_order_events` — `user_orders` messages: status / filled / remaining over time.
* `execution_market_events` — `market_lifecycle_v2`: pauses, reopens, close changes,
  determination, settlement.
* `execution_collector_events` — the collector's own record (connects, gaps, throttles,
  failed polls): missing telemetry is a row, never a silent zero.

`live_order_queue_ticks` gains `trigger` (why this sample was taken), `source` (which endpoint),
`remaining_count` (last known, never inferred) and `features_json` (DERIVED, recomputable).

Every payload keeps a verbatim `raw_json`, because the queue sampler's first deploy already
proved a Kalshi shape change is only recoverable from the raw payload.

Revision ID: c7e2a9d4f1b8
Revises: a0bd7f9c48de
Create Date: 2026-09-16
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "c7e2a9d4f1b8"
down_revision = "a0bd7f9c48de"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(JSONB(), "postgresql")
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("live_order_queue_ticks", sa.Column("trigger", sa.String(24), nullable=True))
    op.add_column("live_order_queue_ticks", sa.Column("source", sa.String(16), nullable=True))
    op.add_column("live_order_queue_ticks",
                  sa.Column("remaining_count", sa.Numeric(18, 2), nullable=True))
    op.add_column("live_order_queue_ticks", sa.Column("features_json", _JSON, nullable=True))

    op.create_table(
        "execution_order_context",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("live_order_id", sa.BigInteger(), nullable=True),
        sa.Column("kalshi_order_id", sa.String(128), nullable=True),
        sa.Column("client_order_id", sa.String(128), nullable=True),
        sa.Column("strategy", sa.String(32), nullable=True),
        sa.Column("twin_tag", sa.String(24), nullable=True),
        sa.Column("experiment_deployment_arm_id", sa.BigInteger(), nullable=True),
        sa.Column("market_ticker", sa.String(128), nullable=False),
        sa.Column("event_ticker", sa.String(128), nullable=True),
        sa.Column("series_ticker", sa.String(32), nullable=True),
        sa.Column("decided_at", _TS, nullable=False),
        sa.Column("no_price", sa.Integer(), nullable=True),
        sa.Column("yes_price", sa.Integer(), nullable=True),
        sa.Column("quantity", sa.Numeric(18, 2), nullable=True),
        sa.Column("offset_cents", sa.Integer(), nullable=True),
        sa.Column("hot_entry", sa.Boolean(), nullable=True),
        sa.Column("candidate_mid", sa.Float(), nullable=True),
        sa.Column("best_yes_bid", sa.Integer(), nullable=True),
        sa.Column("best_yes_ask", sa.Integer(), nullable=True),
        sa.Column("best_no_bid", sa.Integer(), nullable=True),
        sa.Column("best_no_ask", sa.Integer(), nullable=True),
        sa.Column("spread", sa.Integer(), nullable=True),
        sa.Column("depth_at_best_bid", sa.Integer(), nullable=True),
        sa.Column("depth_at_best_ask", sa.Integer(), nullable=True),
        sa.Column("top_depth", sa.Integer(), nullable=True),
        sa.Column("market_volume", sa.Integer(), nullable=True),
        sa.Column("open_interest", sa.Integer(), nullable=True),
        sa.Column("last_price", sa.Integer(), nullable=True),
        sa.Column("hours_to_close", sa.Float(), nullable=True),
        sa.Column("hours_to_expiration", sa.Float(), nullable=True),
        sa.Column("close_time", _TS, nullable=True),
        sa.Column("band_lo", sa.Float(), nullable=True),
        sa.Column("band_hi", sa.Float(), nullable=True),
        sa.Column("max_yes", sa.Integer(), nullable=True),
        sa.Column("market_type", sa.String(32), nullable=True),
        sa.Column("market_mode", sa.String(32), nullable=True),
        sa.Column("regime", sa.String(32), nullable=True),
        sa.Column("review_tier", sa.String(32), nullable=True),
        sa.Column("open_positions_for_tag", sa.Integer(), nullable=True),
        sa.Column("open_position_cap", sa.Integer(), nullable=True),
        sa.Column("context_json", _JSON, nullable=True),
        sa.Column("book_json", _JSON, nullable=True),
        sa.Column("submitted_at", _TS, nullable=True),
        sa.Column("acked_at", _TS, nullable=True),
        sa.Column("ack_ts_ms", sa.BigInteger(), nullable=True),
        sa.Column("cancel_requested_at", _TS, nullable=True),
        sa.Column("cancel_confirmed_at", _TS, nullable=True),
        sa.Column("terminal_reason", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(["live_order_id"], ["live_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_execution_order_context_live_order_id", "execution_order_context",
                    ["live_order_id"])
    op.create_index("ix_execution_order_context_kalshi_order_id", "execution_order_context",
                    ["kalshi_order_id"])
    op.create_index("ix_execution_order_context_client_order_id", "execution_order_context",
                    ["client_order_id"])
    op.create_index("ix_eoc_strategy_time", "execution_order_context",
                    ["strategy", "decided_at"])
    op.create_index("ix_eoc_ticker_time", "execution_order_context",
                    ["market_ticker", "decided_at"])

    op.create_table(
        "execution_book_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("market_ticker", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("sid", sa.Integer(), nullable=True),
        sa.Column("seq", sa.BigInteger(), nullable=True),
        sa.Column("ts_ms", sa.BigInteger(), nullable=True),
        sa.Column("received_at", _TS, nullable=False),
        sa.Column("side", sa.String(8), nullable=True),
        sa.Column("price_cents", sa.Integer(), nullable=True),
        sa.Column("price_convention", sa.String(8), nullable=True),
        sa.Column("delta_fp", sa.Numeric(18, 2), nullable=True),
        sa.Column("level_qty_after", sa.Numeric(18, 2), nullable=True),
        sa.Column("ours", sa.Boolean(), nullable=True),
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column("raw_json", _JSON, nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ebe_ticker_time", "execution_book_events",
                    ["market_ticker", "received_at"])
    op.create_index("ix_ebe_sid_seq", "execution_book_events", ["sid", "seq"])

    op.create_table(
        "execution_trade_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trade_id", sa.String(64), nullable=False),
        sa.Column("market_ticker", sa.String(128), nullable=False),
        sa.Column("ts_ms", sa.BigInteger(), nullable=True),
        sa.Column("received_at", _TS, nullable=False),
        sa.Column("yes_price_cents", sa.Integer(), nullable=True),
        sa.Column("no_price_cents", sa.Integer(), nullable=True),
        sa.Column("count_fp", sa.Numeric(18, 2), nullable=True),
        sa.Column("taker_outcome_side", sa.String(8), nullable=True),
        sa.Column("taker_book_side", sa.String(8), nullable=True),
        sa.Column("is_block_trade", sa.Boolean(), nullable=True),
        sa.Column("sid", sa.Integer(), nullable=True),
        sa.Column("seq", sa.BigInteger(), nullable=True),
        sa.Column("raw_json", _JSON, nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trade_id", name="uq_ete_trade_id"),
    )
    op.create_index("ix_ete_ticker_time", "execution_trade_events",
                    ["market_ticker", "received_at"])

    op.create_table(
        "execution_fill_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trade_id", sa.String(64), nullable=False),
        sa.Column("kalshi_order_id", sa.String(128), nullable=True),
        sa.Column("client_order_id", sa.String(128), nullable=True),
        sa.Column("market_ticker", sa.String(128), nullable=False),
        sa.Column("ts_ms", sa.BigInteger(), nullable=True),
        sa.Column("received_at", _TS, nullable=False),
        sa.Column("yes_price_cents", sa.Integer(), nullable=True),
        sa.Column("count_fp", sa.Numeric(18, 2), nullable=True),
        sa.Column("fee_cost", sa.Numeric(12, 6), nullable=True),
        sa.Column("is_taker", sa.Boolean(), nullable=True),
        sa.Column("outcome_side", sa.String(8), nullable=True),
        sa.Column("book_side", sa.String(8), nullable=True),
        sa.Column("post_position_fp", sa.Numeric(18, 2), nullable=True),
        sa.Column("exchange_index", sa.Integer(), nullable=True),
        sa.Column("rest_fill_id", sa.BigInteger(), nullable=True),
        sa.Column("rest_reconciled_at", _TS, nullable=True),
        sa.Column("raw_json", _JSON, nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trade_id", name="uq_efe_trade_id"),
    )
    op.create_index("ix_efe_order_time", "execution_fill_events",
                    ["kalshi_order_id", "received_at"])

    op.create_table(
        "execution_order_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("kalshi_order_id", sa.String(128), nullable=False),
        sa.Column("client_order_id", sa.String(128), nullable=True),
        sa.Column("market_ticker", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=True),
        sa.Column("fill_count_fp", sa.Numeric(18, 2), nullable=True),
        sa.Column("remaining_count_fp", sa.Numeric(18, 2), nullable=True),
        sa.Column("initial_count_fp", sa.Numeric(18, 2), nullable=True),
        sa.Column("maker_fill_cost_dollars", sa.Numeric(14, 6), nullable=True),
        sa.Column("maker_fees_dollars", sa.Numeric(14, 6), nullable=True),
        sa.Column("last_updated_ts_ms", sa.BigInteger(), nullable=True),
        sa.Column("received_at", _TS, nullable=False),
        sa.Column("raw_json", _JSON, nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_eoe_order_time", "execution_order_events",
                    ["kalshi_order_id", "received_at"])

    op.create_table(
        "execution_market_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("market_ticker", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(48), nullable=True),
        sa.Column("is_deactivated", sa.Boolean(), nullable=True),
        sa.Column("event_ts", sa.BigInteger(), nullable=True),
        sa.Column("received_at", _TS, nullable=False),
        sa.Column("sid", sa.Integer(), nullable=True),
        sa.Column("seq", sa.BigInteger(), nullable=True),
        sa.Column("raw_json", _JSON, nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_eme_ticker_time", "execution_market_events",
                    ["market_ticker", "received_at"])

    op.create_table(
        "execution_collector_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("at", _TS, nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("market_ticker", sa.String(128), nullable=True),
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("detail_json", _JSON, nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ece_kind_time", "execution_collector_events", ["kind", "at"])


def downgrade() -> None:
    for name in ("execution_collector_events", "execution_market_events",
                 "execution_order_events", "execution_fill_events",
                 "execution_trade_events", "execution_book_events",
                 "execution_order_context"):
        op.drop_table(name)
    for col in ("features_json", "remaining_count", "source", "trigger"):
        op.drop_column("live_order_queue_ticks", col)
