"""Liquidity-incentive shadow market maker — Phase 0 research tables (WS-020).

docs/LIQUIDITY_INCENTIVE_THESIS.md. A read-only instrument: NO orders, no real money, no
shared state with MMSELL. Nine tables, all written by the shadow collector only:

* `incentive_programs` — one row per VERSION of a program's terms (reward, Target Size,
  Discount Factor, dates, type, cap). Changed terms supersede the old row rather than
  overwriting it; a program that stops being listed is stamped `disappeared_at`.
* `incentive_discovery_cycles` — one row per poll of GET /incentive_programs: the coverage
  denominator (a failed poll is a row with errors, never a missing day).
* `incentive_market_snapshots` — periodic book + scoring-model state per incentivized market.
* `incentive_shadow_quotes` — one hypothetical two-sided resting pair per (market, policy,
  capital tier); `ended_at`/`end_reason` stamped once.
* `incentive_shadow_events` — market events relevant to a resting pair (trade hits, ends).
* `incentive_shadow_fills` — simulated fill increments, ONE ROW PER FILL MODEL.
* `incentive_shadow_marks` — mark-to-market at 1 s/5 s/30 s/60 s/5 min after a leg's first fill.
* `incentive_shadow_outcomes` — the economics of one pair under one fill model, every
  component its own column; settlement fields stamped later, NULL until then.
* `incentive_book_events`, `incentive_trade_events` — the raw tape the fill models replay.
* `incentive_collector_events` — the collector's own record; missing data is a row.

Revision ID: d9f1c3a7b2e4
Revises: c7e2a9d4f1b8
Create Date: 2026-09-16
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "d9f1c3a7b2e4"
down_revision = "c7e2a9d4f1b8"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(JSONB(), "postgresql")
_TS = sa.DateTime(timezone=True)
_FP = sa.Numeric(18, 2)


def _id():
    return sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False)


def upgrade() -> None:
    op.create_table(
        "incentive_programs",
        _id(),
        sa.Column("program_id", sa.String(128), nullable=False),
        sa.Column("market_id", sa.String(128), nullable=True),
        sa.Column("market_ticker", sa.String(128), nullable=False),
        sa.Column("event_ticker", sa.String(128), nullable=True),
        sa.Column("series_ticker", sa.String(64), nullable=True),
        sa.Column("incentive_type", sa.String(32), nullable=True),
        sa.Column("incentive_description", sa.Text(), nullable=True),
        sa.Column("start_date", _TS, nullable=True),
        sa.Column("end_date", _TS, nullable=True),
        sa.Column("period_reward_raw", sa.BigInteger(), nullable=True),
        sa.Column("period_reward_unit", sa.String(16), nullable=True),
        sa.Column("period_reward_usd", sa.Numeric(14, 4), nullable=True),
        sa.Column("target_size", _FP, nullable=True),
        sa.Column("discount_factor_bps", sa.Integer(), nullable=True),
        sa.Column("paid_out", sa.Boolean(), nullable=True),
        sa.Column("status_observed", sa.String(16), nullable=True),
        sa.Column("extra_params_json", _JSON, nullable=True),
        sa.Column("market_title", sa.Text(), nullable=True),
        sa.Column("market_status", sa.String(32), nullable=True),
        sa.Column("close_time", _TS, nullable=True),
        sa.Column("fee_rule_json", _JSON, nullable=True),
        sa.Column("market_raw_json", _JSON, nullable=True),
        sa.Column("terms_hash", sa.String(64), nullable=False),
        sa.Column("raw_json", _JSON, nullable=True),
        sa.Column("first_seen_at", _TS, nullable=False),
        sa.Column("last_seen_at", _TS, nullable=False),
        sa.Column("superseded_at", _TS, nullable=True),
        sa.Column("disappeared_at", _TS, nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("program_id", "terms_hash", name="uq_incentive_program_terms"),
    )
    op.create_index("ix_incentive_programs_ticker", "incentive_programs", ["market_ticker"])
    op.create_index("ix_incentive_programs_current", "incentive_programs",
                    ["superseded_at", "disappeared_at"])

    op.create_table(
        "incentive_discovery_cycles",
        _id(),
        sa.Column("started_at", _TS, nullable=False),
        sa.Column("finished_at", _TS, nullable=True),
        sa.Column("programs_listed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("liquidity_programs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("volume_programs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_terms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("changed_terms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("disappeared", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_period_reward_usd", sa.Numeric(14, 4), nullable=True),
        sa.Column("pages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes_json", _JSON, nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_incentive_discovery_cycles_time", "incentive_discovery_cycles", ["started_at"])

    op.create_table(
        "incentive_market_snapshots",
        _id(),
        sa.Column("program_row_id", sa.BigInteger(), nullable=True),
        sa.Column("market_ticker", sa.String(128), nullable=False),
        sa.Column("at", _TS, nullable=False),
        sa.Column("book_valid", sa.Boolean(), nullable=True),
        sa.Column("best_yes_bid", sa.Integer(), nullable=True),
        sa.Column("best_no_bid", sa.Integer(), nullable=True),
        sa.Column("spread_cents", sa.Integer(), nullable=True),
        sa.Column("yes_depth_at_best", _FP, nullable=True),
        sa.Column("no_depth_at_best", _FP, nullable=True),
        sa.Column("yes_depth_total", _FP, nullable=True),
        sa.Column("no_depth_total", _FP, nullable=True),
        sa.Column("yes_levels_json", _JSON, nullable=True),
        sa.Column("no_levels_json", _JSON, nullable=True),
        sa.Column("trades_last_5m", sa.Integer(), nullable=True),
        sa.Column("volume_last_5m", _FP, nullable=True),
        sa.Column("price_range_5m_cents", sa.Integer(), nullable=True),
        sa.Column("last_trade_yes_price", sa.Integer(), nullable=True),
        sa.Column("est_reference_price", sa.Float(), nullable=True),
        sa.Column("est_yes_qualifying_depth", _FP, nullable=True),
        sa.Column("est_no_qualifying_depth", _FP, nullable=True),
        sa.Column("est_yes_meets_target", sa.Boolean(), nullable=True),
        sa.Column("est_no_meets_target", sa.Boolean(), nullable=True),
        sa.Column("est_yes_score_total", sa.Float(), nullable=True),
        sa.Column("est_no_score_total", sa.Float(), nullable=True),
        sa.Column("scoring_version", sa.String(32), nullable=True),
        sa.Column("seq", sa.BigInteger(), nullable=True),
        sa.Column("market_status", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_incentive_market_snapshots_program", "incentive_market_snapshots", ["program_row_id"])
    op.create_index("ix_incentive_market_snapshots_ticker_time", "incentive_market_snapshots",
                    ["market_ticker", "at"])

    op.create_table(
        "incentive_shadow_quotes",
        _id(),
        sa.Column("program_row_id", sa.BigInteger(), nullable=True),
        sa.Column("market_ticker", sa.String(128), nullable=False),
        sa.Column("policy", sa.String(32), nullable=False),
        sa.Column("capital_tier_usd", sa.Integer(), nullable=False),
        sa.Column("placed_at", _TS, nullable=False),
        sa.Column("yes_bid", sa.Integer(), nullable=False),
        sa.Column("no_bid", sa.Integer(), nullable=False),
        sa.Column("yes_bid_yes_scale", sa.Integer(), nullable=False),
        sa.Column("no_bid_yes_scale", sa.Integer(), nullable=False),
        sa.Column("qty_per_side", _FP, nullable=False),
        sa.Column("pair_cost_cents", sa.Integer(), nullable=False),
        sa.Column("maker_fee_cents_per_pair", sa.Float(), nullable=True),
        sa.Column("pair_edge_cents", sa.Float(), nullable=True),
        sa.Column("capital_required_usd", sa.Numeric(14, 2), nullable=True),
        sa.Column("capital_unused_usd", sa.Numeric(14, 2), nullable=True),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column("yes_joins_best", sa.Boolean(), nullable=True),
        sa.Column("no_joins_best", sa.Boolean(), nullable=True),
        sa.Column("yes_queue_ahead", _FP, nullable=True),
        sa.Column("no_queue_ahead", _FP, nullable=True),
        sa.Column("book_json", _JSON, nullable=True),
        sa.Column("est_reference_price", sa.Float(), nullable=True),
        sa.Column("est_yes_score", sa.Float(), nullable=True),
        sa.Column("est_no_score", sa.Float(), nullable=True),
        sa.Column("est_yes_share", sa.Float(), nullable=True),
        sa.Column("est_no_share", sa.Float(), nullable=True),
        sa.Column("est_reward_per_hour_usd", sa.Float(), nullable=True),
        sa.Column("scoring_version", sa.String(32), nullable=True),
        sa.Column("ended_at", _TS, nullable=True),
        sa.Column("end_reason", sa.String(48), nullable=True),
        sa.Column("rest_seconds", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_incentive_shadow_quotes_program", "incentive_shadow_quotes", ["program_row_id"])
    op.create_index("ix_incentive_shadow_quotes_ticker_time", "incentive_shadow_quotes",
                    ["market_ticker", "placed_at"])
    op.create_index("ix_incentive_shadow_quotes_open", "incentive_shadow_quotes", ["ended_at"])

    op.create_table(
        "incentive_shadow_events",
        _id(),
        sa.Column("quote_id", sa.BigInteger(), nullable=False),
        sa.Column("market_ticker", sa.String(128), nullable=False),
        sa.Column("at", _TS, nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("side", sa.String(8), nullable=True),
        sa.Column("yes_price_cents", sa.Integer(), nullable=True),
        sa.Column("count", _FP, nullable=True),
        sa.Column("taker_outcome_side", sa.String(8), nullable=True),
        sa.Column("trade_id", sa.String(64), nullable=True),
        sa.Column("detail_json", _JSON, nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_incentive_shadow_events_quote_time", "incentive_shadow_events", ["quote_id", "at"])

    op.create_table(
        "incentive_shadow_fills",
        _id(),
        sa.Column("quote_id", sa.BigInteger(), nullable=False),
        sa.Column("market_ticker", sa.String(128), nullable=False),
        sa.Column("fill_model", sa.String(16), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("at", _TS, nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("qty", _FP, nullable=False),
        sa.Column("cumulative_qty", _FP, nullable=False),
        sa.Column("is_full", sa.Boolean(), nullable=False),
        sa.Column("queue_ahead_before", _FP, nullable=True),
        sa.Column("seconds_since_placed", sa.Float(), nullable=True),
        sa.Column("trade_id", sa.String(64), nullable=True),
        sa.Column("mid_at_fill", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_incentive_shadow_fills_quote", "incentive_shadow_fills", ["quote_id", "fill_model"])

    op.create_table(
        "incentive_shadow_marks",
        _id(),
        sa.Column("quote_id", sa.BigInteger(), nullable=False),
        sa.Column("market_ticker", sa.String(128), nullable=False),
        sa.Column("fill_model", sa.String(16), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("horizon_seconds", sa.Integer(), nullable=False),
        sa.Column("at", _TS, nullable=False),
        sa.Column("fill_price_cents", sa.Integer(), nullable=False),
        sa.Column("filled_qty", _FP, nullable=False),
        sa.Column("mark_bid_cents", sa.Integer(), nullable=True),
        sa.Column("mark_mid_cents", sa.Float(), nullable=True),
        sa.Column("pnl_at_bid_usd", sa.Numeric(14, 4), nullable=True),
        sa.Column("pnl_at_mid_usd", sa.Numeric(14, 4), nullable=True),
        sa.Column("book_valid", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_incentive_shadow_marks_quote", "incentive_shadow_marks",
                    ["quote_id", "fill_model", "side"])

    op.create_table(
        "incentive_shadow_outcomes",
        _id(),
        sa.Column("quote_id", sa.BigInteger(), nullable=False),
        sa.Column("program_row_id", sa.BigInteger(), nullable=True),
        sa.Column("market_ticker", sa.String(128), nullable=False),
        sa.Column("policy", sa.String(32), nullable=False),
        sa.Column("capital_tier_usd", sa.Integer(), nullable=False),
        sa.Column("fill_model", sa.String(16), nullable=False),
        sa.Column("placed_at", _TS, nullable=False),
        sa.Column("ended_at", _TS, nullable=False),
        sa.Column("end_reason", sa.String(48), nullable=True),
        sa.Column("rest_seconds", sa.Float(), nullable=True),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("yes_filled_qty", _FP, nullable=False),
        sa.Column("no_filled_qty", _FP, nullable=False),
        sa.Column("matched_pairs", _FP, nullable=False),
        sa.Column("yes_first_fill_at", _TS, nullable=True),
        sa.Column("no_first_fill_at", _TS, nullable=True),
        sa.Column("seconds_between_legs", sa.Float(), nullable=True),
        sa.Column("capital_required_usd", sa.Numeric(14, 2), nullable=True),
        sa.Column("capital_hours", sa.Float(), nullable=True),
        sa.Column("est_reward_usd", sa.Numeric(14, 6), nullable=True),
        sa.Column("est_reward_yes_usd", sa.Numeric(14, 6), nullable=True),
        sa.Column("est_reward_no_usd", sa.Numeric(14, 6), nullable=True),
        sa.Column("paired_pnl_usd", sa.Numeric(14, 4), nullable=True),
        sa.Column("fees_usd", sa.Numeric(14, 4), nullable=True),
        sa.Column("single_leg_side", sa.String(8), nullable=True),
        sa.Column("single_leg_qty", _FP, nullable=True),
        sa.Column("single_leg_mtm_5m_usd", sa.Numeric(14, 4), nullable=True),
        sa.Column("single_leg_max_adverse_usd", sa.Numeric(14, 4), nullable=True),
        sa.Column("net_before_settlement_usd", sa.Numeric(14, 4), nullable=True),
        sa.Column("settled_at", _TS, nullable=True),
        sa.Column("settlement_result", sa.String(8), nullable=True),
        sa.Column("settlement_pnl_usd", sa.Numeric(14, 4), nullable=True),
        sa.Column("net_after_settlement_usd", sa.Numeric(14, 4), nullable=True),
        sa.Column("scoring_version", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("quote_id", "fill_model", name="uq_incentive_shadow_outcome"),
    )
    op.create_index("ix_incentive_shadow_outcomes_program", "incentive_shadow_outcomes", ["program_row_id"])
    op.create_index("ix_incentive_shadow_outcomes_ticker_time", "incentive_shadow_outcomes",
                    ["market_ticker", "ended_at"])

    op.create_table(
        "incentive_book_events",
        _id(),
        sa.Column("market_ticker", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("sid", sa.Integer(), nullable=True),
        sa.Column("seq", sa.BigInteger(), nullable=True),
        sa.Column("ts_ms", sa.BigInteger(), nullable=True),
        sa.Column("received_at", _TS, nullable=False),
        sa.Column("side", sa.String(8), nullable=True),
        sa.Column("price_cents", sa.Integer(), nullable=True),
        sa.Column("price_convention", sa.String(8), nullable=True),
        sa.Column("delta_fp", _FP, nullable=True),
        sa.Column("level_qty_after", _FP, nullable=True),
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column("raw_json", _JSON, nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_incentive_book_events_ticker_time", "incentive_book_events",
                    ["market_ticker", "received_at"])

    op.create_table(
        "incentive_trade_events",
        _id(),
        sa.Column("trade_id", sa.String(64), nullable=False),
        sa.Column("market_ticker", sa.String(128), nullable=False),
        sa.Column("ts_ms", sa.BigInteger(), nullable=True),
        sa.Column("received_at", _TS, nullable=False),
        sa.Column("yes_price_cents", sa.Integer(), nullable=True),
        sa.Column("no_price_cents", sa.Integer(), nullable=True),
        sa.Column("count_fp", _FP, nullable=True),
        sa.Column("taker_outcome_side", sa.String(8), nullable=True),
        sa.Column("taker_book_side", sa.String(8), nullable=True),
        sa.Column("is_block_trade", sa.Boolean(), nullable=True),
        sa.Column("sid", sa.Integer(), nullable=True),
        sa.Column("seq", sa.BigInteger(), nullable=True),
        sa.Column("raw_json", _JSON, nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trade_id", name="uq_incentive_trade_id"),
    )
    op.create_index("ix_incentive_trade_events_ticker_time", "incentive_trade_events",
                    ["market_ticker", "received_at"])

    op.create_table(
        "incentive_collector_events",
        _id(),
        sa.Column("at", _TS, nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("market_ticker", sa.String(128), nullable=True),
        sa.Column("connection_id", sa.Integer(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("detail_json", _JSON, nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_incentive_collector_events_kind_time", "incentive_collector_events", ["kind", "at"])


def downgrade() -> None:
    for name in ("incentive_collector_events", "incentive_trade_events", "incentive_book_events",
                 "incentive_shadow_outcomes", "incentive_shadow_marks", "incentive_shadow_fills",
                 "incentive_shadow_events", "incentive_shadow_quotes", "incentive_market_snapshots",
                 "incentive_discovery_cycles", "incentive_programs"):
        op.drop_table(name)
