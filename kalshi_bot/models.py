"""SQLAlchemy 2.0 ORM models for the full bot schema.

The Scanner MVP only writes a subset (bot_runs, markets, market_snapshots,
orderbook_snapshots, signals, risk_events, account_snapshots, system_events),
but the full schema is defined so the later paper/approval/live phases slot in
without a migration scramble.

Conventions:
- Prices are integer cents (Kalshi range 1..99).
- Money / P&L / exposure use Numeric; probabilities and scores use Float.
- JSON columns are JSONB on Postgres and JSON on sqlite (tests).
- Primary/foreign keys are BigInteger on Postgres, Integer on sqlite (so sqlite
  autoincrement works correctly).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# JSONB on Postgres, plain JSON on sqlite.
JSONType = JSONB().with_variant(JSON(), "sqlite")
# BigInteger on Postgres, Integer on sqlite (sqlite only autoincrements INTEGER PKs).
BigIntId = BigInteger().with_variant(Integer(), "sqlite")
TS = DateTime(timezone=True)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class BotRun(Base):
    __tablename__ = "bot_runs"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(TS)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    error_message: Mapped[str | None] = mapped_column(Text)
    markets_scanned: Mapped[int] = mapped_column(Integer, default=0)
    candidates_found: Mapped[int] = mapped_column(Integer, default=0)


class Market(Base):
    __tablename__ = "markets"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(64))
    close_time: Mapped[datetime | None] = mapped_column(TS)
    expiration_time: Mapped[datetime | None] = mapped_column(TS)
    settlement_source: Mapped[str | None] = mapped_column(Text)
    rules_summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow, nullable=False)


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"
    __table_args__ = (Index("ix_market_snapshots_ticker_time", "market_ticker", "captured_at"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    market_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    yes_bid: Mapped[int | None] = mapped_column(Integer)
    yes_ask: Mapped[int | None] = mapped_column(Integer)
    no_bid: Mapped[int | None] = mapped_column(Integer)
    no_ask: Mapped[int | None] = mapped_column(Integer)
    last_price: Mapped[int | None] = mapped_column(Integer)
    volume: Mapped[int | None] = mapped_column(Integer)
    open_interest: Mapped[int | None] = mapped_column(Integer)
    spread: Mapped[int | None] = mapped_column(Integer)
    midpoint: Mapped[float | None] = mapped_column(Float)
    liquidity_score: Mapped[float | None] = mapped_column(Float)
    raw_json: Mapped[dict | None] = mapped_column(JSONType)


class OrderbookSnapshot(Base):
    __tablename__ = "orderbook_snapshots"
    __table_args__ = (Index("ix_orderbook_snapshots_ticker_time", "market_ticker", "captured_at"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    market_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    yes_levels_json: Mapped[list | None] = mapped_column(JSONType)
    no_levels_json: Mapped[list | None] = mapped_column(JSONType)
    best_yes_bid: Mapped[int | None] = mapped_column(Integer)
    best_yes_ask: Mapped[int | None] = mapped_column(Integer)
    best_no_bid: Mapped[int | None] = mapped_column(Integer)
    best_no_ask: Mapped[int | None] = mapped_column(Integer)
    top_depth: Mapped[int | None] = mapped_column(Integer)
    raw_json: Mapped[dict | None] = mapped_column(JSONType)


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (Index("ix_signals_ticker_time", "market_ticker", "created_at"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    market_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    signal_type: Mapped[str] = mapped_column(String(48), nullable=False)
    bot_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    implied_probability: Mapped[float | None] = mapped_column(Float)
    model_probability: Mapped[float | None] = mapped_column(Float)
    edge: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    label: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    input_snapshot_id: Mapped[int | None] = mapped_column(
        BigIntId, ForeignKey("market_snapshots.id")
    )


class RiskEvent(Base):
    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    signal_id: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("signals.id"))
    market_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason_codes_json: Mapped[list | None] = mapped_column(JSONType)
    max_allowed_quantity: Mapped[int | None] = mapped_column(Integer)
    max_allowed_price: Mapped[int | None] = mapped_column(Integer)


class PaperTrade(Base):
    __tablename__ = "paper_trades"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    signal_id: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("signals.id"))
    market_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy: Mapped[str | None] = mapped_column(String(24), index=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    side: Mapped[str | None] = mapped_column(String(8))
    action: Mapped[str | None] = mapped_column(String(8))
    assumed_price: Mapped[int | None] = mapped_column(Integer)
    quantity: Mapped[int | None] = mapped_column(Integer)
    model_probability: Mapped[float | None] = mapped_column(Float)
    edge: Mapped[float | None] = mapped_column(Float)
    fill_assumption: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str | None] = mapped_column(String(24))
    exit_price: Mapped[int | None] = mapped_column(Integer)
    resolved_value: Mapped[int | None] = mapped_column(Integer)
    pnl: Mapped[float | None] = mapped_column(Numeric(14, 4))
    fees: Mapped[float | None] = mapped_column(Numeric(14, 4))
    closed_at: Mapped[datetime | None] = mapped_column(TS)
    # Trades from retired entry windows/strategies (e.g. the day-1 h12 window): kept for
    # the record but excluded from the PnL report so it only reflects live strategies.
    legacy: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Experiment OS lineage (spec §14): the deployment-arm link this entry was written
    # under, from which the whole chain (deployment → epoch → version → experiment →
    # platform snapshot) is derivable losslessly. NULL on legacy/pre-enforcement rows —
    # history is never rewritten to populate it. Plain column (no ORM-level FK) so this
    # module stays importable without the experiment_os package; the real FK constraint
    # is added by the alembic migration on Postgres.
    experiment_deployment_arm_id: Mapped[int | None] = mapped_column(BigIntId, index=True)


class PaperPosition(Base):
    __tablename__ = "paper_positions"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    market_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy: Mapped[str | None] = mapped_column(String(24), index=True)
    side: Mapped[str | None] = mapped_column(String(8))
    quantity: Mapped[int | None] = mapped_column(Integer)
    avg_price: Mapped[float | None] = mapped_column(Numeric(8, 4))
    status: Mapped[str | None] = mapped_column(String(24))
    opened_at: Mapped[datetime | None] = mapped_column(TS, default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(TS)
    pnl: Mapped[float | None] = mapped_column(Numeric(14, 4))
    unrealized_pnl: Mapped[float | None] = mapped_column(Numeric(14, 4))


class MmSellPositionTick(Base):
    """Per-cycle price path for markets with an OPEN mmsell paper position — the intraday tape
    the mmsell books never recorded (0 rows in market_snapshots for these sports tickers, which
    is why a per-ticker fill/exit replay was impossible). Captured cheaply from the orderbook
    `manage_open_positions` already fetches each cycle, deduped to one row per ticker per cycle
    (the path is ticker-level; every book holding that ticker replays the same path from its own
    entry). Feeds the offline exit-rule study (scripts/mmsell_exit_study.py): confirmed
    catastrophic stop + volatility exit vs hold-to-settlement. Prices are integer cents."""

    __tablename__ = "mmsell_position_ticks"
    __table_args__ = (Index("ix_mmsell_ticks_ticker_time", "market_ticker", "captured_at"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    market_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    yes_bid: Mapped[int | None] = mapped_column(Integer)
    yes_ask: Mapped[int | None] = mapped_column(Integer)
    no_bid: Mapped[int | None] = mapped_column(Integer)
    no_ask: Mapped[int | None] = mapped_column(Integer)
    mid: Mapped[float | None] = mapped_column(Float)  # yes midpoint, cents
    volume: Mapped[int | None] = mapped_column(Integer)


class MmSellCandidateTick(Base):
    """Per-cycle orderbook snapshot for every IN-BAND mmsell CANDIDATE market — whether or not a
    position was opened that cycle. This is the pre-entry price path the fill model needs to replace
    its live-*calibrated* fill estimate (drawn from one 359-trade live window) with a DIRECT
    per-ticker replay: 'if I'd rested a buy-NO at the no-bid at cycle T, would it have been lifted
    before close, and at what realizable P&L?'. Complements `mmsell_position_ticks` (which captures
    only markets already HELD); captured cheaply off the orderbook the entry scan already fetches,
    config-gated + per-cycle capped so it never burdens the trading loop. Prices are integer cents."""

    __tablename__ = "mmsell_candidate_ticks"
    __table_args__ = (Index("ix_mmsell_cand_ticks_ticker_time", "market_ticker", "captured_at"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    market_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    series: Mapped[str | None] = mapped_column(String(32))
    hours_to_close: Mapped[float | None] = mapped_column(Float)
    # Hours to Kalshi's EXPECTED EXPIRATION, which for an in-play sports market is the only
    # forward-looking estimate of when the contest actually resolves. `hours_to_close` above is
    # derived from `close_time`, which Kalshi sets to a far-future fallback on sports: measured
    # 2026-08-05, KXUFCFIGHT reported 335h to close on a fight that resolved in 0.4h, so every
    # in-play trade buckets as "24-72h+" and a timing study on that column measures nothing
    # (docs/MMSELL_TIMING_STUDY.md). The timing study can score history on realized
    # closed_at - created_at, but a LIVE entry gate cannot — it needs this, known in advance.
    hours_to_expiration: Mapped[float | None] = mapped_column(Float)
    # Contract sub-structure, straight from the market payload. The market TYPE taxonomy
    # (kalshi_bot/mmsell/market_types.py) says a ticker is a `spread` or a `total`; these say
    # WHICH ONE — the run line, the over/under number, the strike. That is the difference
    # between "sell cheap tails on MLB spreads" and "sell them only at 3+ runs", and it cannot
    # be recovered later: the ticker suffix encodes it inconsistently across series and the
    # subtitle is truncated into fill_assumption.
    strike_type: Mapped[str | None] = mapped_column(String(16))   # greater | less | between | ...
    floor_strike: Mapped[float | None] = mapped_column(Float)
    cap_strike: Mapped[float | None] = mapped_column(Float)
    yes_sub_title: Mapped[str | None] = mapped_column(String(64))  # "Above 3.5%", "LAA by 3+"
    # Book DEPTH at the touch, the missing input to the taker-vs-maker question. mmsell sells the
    # YES tail by buying NO, so the two sides mean different things to it:
    #   depth_at_best_bid — contracts resting at the best YES bid. This is what a TAKER entry
    #       consumes (buy NO == sell YES into the bid), so it is the capacity ceiling: a book
    #       quoting 1 contract cannot fill a 20-lot no matter how good the edge looks.
    #   depth_at_best_ask — contracts resting at the best NO bid (== the YES-ask queue). This is
    #       what a MAKER entry joins, i.e. how many orders sit ahead of ours at our own price.
    # Without these, `taker = paper - spread` silently assumes infinite liquidity at the touch,
    # which is exactly the assumption that has to hold for the endgame result to be tradeable.
    depth_at_best_bid: Mapped[int | None] = mapped_column(Integer)
    depth_at_best_ask: Mapped[int | None] = mapped_column(Integer)
    yes_bid: Mapped[int | None] = mapped_column(Integer)
    yes_ask: Mapped[int | None] = mapped_column(Integer)
    no_bid: Mapped[int | None] = mapped_column(Integer)
    no_ask: Mapped[int | None] = mapped_column(Integer)
    mid: Mapped[float | None] = mapped_column(Float)  # yes midpoint, cents
    volume: Mapped[int | None] = mapped_column(Integer)


class MmSellSettlementMeta(Base):
    """Settlement-date metadata for a market the mmsell scan has considered — one row per
    ticker, written the first time it is seen. Exists ONLY to make the settlement-date
    concentration cap queryable: `paper_positions` records a strategy + status but not WHEN a
    market settles, so without this table there would be no way to ask "how many of this book's
    currently-open positions settle on the same date as this new candidate?"

    Deliberately NOT the regime — `series_ticker` is stored and `kalshi_bot.mmsell.regimes.
    regime_of` is applied at query time, so a later change to the regime map is reflected in a
    live risk check immediately (unlike `backfill_regime_markets`, where regime is stamped at
    capture time to keep a BACKTEST reproducible — the two tables want opposite behavior for
    the same fact, because one measures history and the other gates real risk right now)."""

    __tablename__ = "mmsell_settlement_meta"
    __table_args__ = (Index("ix_mmsell_settlement_meta_close", "close_time"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    market_ticker: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    event_ticker: Mapped[str | None] = mapped_column(String(128), index=True)
    series_ticker: Mapped[str | None] = mapped_column(String(64))
    close_time: Mapped[datetime] = mapped_column(TS, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class LiveOrder(Base):
    __tablename__ = "live_orders"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    signal_id: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("signals.id"))
    kalshi_order_id: Mapped[str | None] = mapped_column(String(128), index=True)
    client_order_id: Mapped[str | None] = mapped_column(String(128), index=True)
    market_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    event_ticker: Mapped[str | None] = mapped_column(String(128), index=True)
    strategy: Mapped[str | None] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    side: Mapped[str | None] = mapped_column(String(8))
    action: Mapped[str | None] = mapped_column(String(8))
    limit_price: Mapped[int | None] = mapped_column(Integer)
    quantity: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(24))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    raw_order_json: Mapped[dict | None] = mapped_column(JSONType)
    # Experiment OS lineage — same semantics as paper_trades.experiment_deployment_arm_id
    # (NULL = legacy/pre-enforcement; FK added by migration, not the ORM).
    experiment_deployment_arm_id: Mapped[int | None] = mapped_column(BigIntId, index=True)


class LiveOrderQueueTick(Base):
    """One sample of where a resting live order sat in Kalshi's queue.

    The measurement that turns maker adverse selection from an inference into an observation.
    `docs/MMSELL_FILL_MODEL.md` names it as the entire paper→live gap (~2¢/contract) and says we
    cannot replay it because the books throw away the data a fill model needs. This is that data,
    and it accrues on every resting order whether or not any experiment is armed.

    What it unblocks immediately: `docs/MMSELL_OFFSET_AB.md` pays real money to learn what 1¢ of
    queue priority buys, measured through downstream P&L — a comparison its own gate computes
    needs ~47,106 contracts/arm to resolve at +0.5¢, against the ~270/arm we have actually
    accrued. Queue rank answers the mechanism question directly: did the cent move us up, and
    past how many contracts?

    Append-only, one row per (order, cycle). `queue_position` is nullable because a sample can
    legitimately fail — but a null is never written silently: the sampler logs a parse failure
    and stores the raw payload so the shape can be inspected. `contracts_ahead` is captured only
    when Kalshi offers it and is deliberately not derived from the rank; rank 3 behind three
    1-lots is a different trade from rank 3 behind three 500-lots.
    """

    __tablename__ = "live_order_queue_ticks"
    # Every read is "this order's samples over time" (did we move up?) or "this book's samples in
    # a window" (does the offset arm rank better?).
    __table_args__ = (
        Index("ix_lqt_order_time", "live_order_id", "captured_at"),
        Index("ix_lqt_strategy_time", "strategy", "captured_at"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    live_order_id: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("live_orders.id"))
    kalshi_order_id: Mapped[str | None] = mapped_column(String(128), index=True)
    strategy: Mapped[str | None] = mapped_column(String(32))
    market_ticker: Mapped[str | None] = mapped_column(String(128))
    captured_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    queue_position: Mapped[int | None] = mapped_column(Integer)
    contracts_ahead: Mapped[int | None] = mapped_column(Integer)
    # The resting price and how long it has rested — the two covariates any P(fill) fit needs
    # alongside the rank, stored here so the analysis never has to re-join and re-derive them.
    limit_price: Mapped[int | None] = mapped_column(Integer)
    rest_seconds: Mapped[int | None] = mapped_column(Integer)
    raw_json: Mapped[dict | None] = mapped_column(JSONType)


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    kalshi_fill_id: Mapped[str | None] = mapped_column(String(128), index=True)
    kalshi_order_id: Mapped[str | None] = mapped_column(String(128), index=True)
    market_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    filled_at: Mapped[datetime | None] = mapped_column(TS)
    side: Mapped[str | None] = mapped_column(String(8))
    action: Mapped[str | None] = mapped_column(String(8))
    price: Mapped[int | None] = mapped_column(Integer)
    quantity: Mapped[int | None] = mapped_column(Integer)
    fee: Mapped[float | None] = mapped_column(Numeric(10, 4))
    raw_fill_json: Mapped[dict | None] = mapped_column(JSONType)


class Position(Base):
    __tablename__ = "positions"
    # Append-only snapshots: every read wants "the newest row for these tickers"
    # (repository.latest_position_snapshot, the live/paper dashboard's live leg).
    __table_args__ = (Index("ix_positions_ticker_time", "market_ticker", "captured_at"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    market_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    side: Mapped[str | None] = mapped_column(String(8))
    quantity: Mapped[int | None] = mapped_column(Integer)
    quantity_fp: Mapped[float | None] = mapped_column(Numeric(18, 4))  # fractional position size
    avg_price: Mapped[float | None] = mapped_column(Numeric(8, 4))
    market_exposure: Mapped[float | None] = mapped_column(Numeric(14, 4))
    realized_pnl: Mapped[float | None] = mapped_column(Numeric(14, 4))
    unrealized_pnl: Mapped[float | None] = mapped_column(Numeric(14, 4))
    raw_json: Mapped[dict | None] = mapped_column(JSONType)


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    captured_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    cash_balance: Mapped[float | None] = mapped_column(Numeric(14, 2))
    portfolio_value: Mapped[float | None] = mapped_column(Numeric(14, 2))
    total_exposure: Mapped[float | None] = mapped_column(Numeric(14, 2))
    raw_json: Mapped[dict | None] = mapped_column(JSONType)


class LivePaperTwin(Base):
    """Epoch record for one live/paper TWIN book — a fresh paper book started at the same moment
    as a live strategy, parameterized to the LIVE knobs (entry price rule, dollar sizing, open cap)
    so the only difference between the two is the fill assumption paper cannot test.

    Why an epoch row at all: the incumbent paper book (e.g. `mmsell10`) carries months of history
    that the live run does not, so paper-vs-live comparisons over it are confounded by sample and
    regime. The twin starts at zero, and `started_at` scopes BOTH sides of the comparison to the
    same window — that is what makes it one-to-one. `params_json` is the parameter snapshot taken
    at creation; if the live config later drifts away from it the parity read is no longer
    apples-to-apples, which the harness detects and flags (see kalshi_bot/twin/harness.py).
    """

    __tablename__ = "live_paper_twins"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    twin_tag: Mapped[str] = mapped_column(String(24), nullable=False, unique=True, index=True)
    live_tag: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(TS)
    params_json: Mapped[dict | None] = mapped_column(JSONType)
    notes: Mapped[str | None] = mapped_column(Text)


class LivePaperParityEvent(Base):
    """One row per candidate market per cycle per twin pair — the decision-alignment tape.

    Records what each of the THREE actors did with the same candidate at the same instant: the
    incumbent paper book (`parent_*`), the fresh twin paper book (`twin_*`), and the real live
    order attempt (`live_*`). Divergence is then attributable rather than guessed: a live book
    that trades less than its paper twin because of a gate is a different failure from one that
    trades the same markets but fills worse. Only written for markets that were in-band for the
    pair (real candidates), and capped per cycle."""

    __tablename__ = "live_paper_parity_events"
    __table_args__ = (
        Index("ix_lp_parity_twin_time", "twin_tag", "recorded_at"),
        Index("ix_lp_parity_ticker_time", "market_ticker", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    recorded_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    twin_tag: Mapped[str] = mapped_column(String(24), nullable=False)
    live_tag: Mapped[str] = mapped_column(String(32), nullable=False)
    market_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    series: Mapped[str | None] = mapped_column(String(32))
    hours_to_close: Mapped[float | None] = mapped_column(Float)
    # Incumbent (long-running) paper book on the live tag.
    parent_outcome: Mapped[str | None] = mapped_column(String(24))
    parent_price: Mapped[int | None] = mapped_column(Integer)
    # Fresh twin paper book (live-parameterized).
    twin_outcome: Mapped[str | None] = mapped_column(String(24))
    twin_price: Mapped[int | None] = mapped_column(Integer)
    twin_quantity: Mapped[int | None] = mapped_column(Integer)
    # The real live attempt, incl. the reason it placed nothing.
    live_outcome: Mapped[str | None] = mapped_column(String(32))
    live_price: Mapped[int | None] = mapped_column(Integer)
    live_quantity: Mapped[int | None] = mapped_column(Integer)
    # Market state at the decision (so a divergence can be read without a second table).
    yes_mid: Mapped[float | None] = mapped_column(Float)
    no_bid: Mapped[int | None] = mapped_column(Integer)
    no_ask: Mapped[int | None] = mapped_column(Integer)


class WeatherForecast(Base):
    __tablename__ = "weather_forecasts"
    __table_args__ = (Index("ix_weather_forecasts_event_time", "event_ticker", "captured_at"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    captured_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    city: Mapped[str] = mapped_column(String(32), nullable=False)
    series_ticker: Mapped[str | None] = mapped_column(String(64))
    event_ticker: Mapped[str | None] = mapped_column(String(128))
    target_date: Mapped[str | None] = mapped_column(String(16))
    station: Mapped[str | None] = mapped_column(String(16))
    # 'high' | 'low' (None = legacy rows, all high). For kind='low' rows,
    # forecast_high_f holds the forecast daily LOW.
    kind: Mapped[str | None] = mapped_column(String(8))
    forecast_high_f: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str | None] = mapped_column(String(32))
    raw_json: Mapped[dict | None] = mapped_column(JSONType)


class WeatherSettlement(Base):
    __tablename__ = "weather_settlements"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    event_ticker: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    city: Mapped[str | None] = mapped_column(String(32))
    series_ticker: Mapped[str | None] = mapped_column(String(64))
    target_date: Mapped[str | None] = mapped_column(String(16))
    kind: Mapped[str | None] = mapped_column(String(8))  # 'high' | 'low' (None = legacy high)
    winning_ticker: Mapped[str | None] = mapped_column(String(128))
    winning_subtitle: Mapped[str | None] = mapped_column(String(64))
    actual_low_f: Mapped[float | None] = mapped_column(Float)
    actual_high_f: Mapped[float | None] = mapped_column(Float)
    captured_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    raw_json: Mapped[dict | None] = mapped_column(JSONType)


class WeatherObservation(Base):
    """Running max/min observed at the settlement station so far in the local day —
    by mid-afternoon the daily high is often already locked in while the market lags."""

    __tablename__ = "weather_observations"
    __table_args__ = (Index("ix_weather_observations_city_time", "city", "captured_at"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    captured_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    city: Mapped[str] = mapped_column(String(32), nullable=False)
    station: Mapped[str | None] = mapped_column(String(16))
    target_date: Mapped[str | None] = mapped_column(String(16))
    running_max_f: Mapped[float | None] = mapped_column(Float)
    running_min_f: Mapped[float | None] = mapped_column(Float)
    obs_count: Mapped[int] = mapped_column(Integer, default=0)
    last_obs_at: Mapped[datetime | None] = mapped_column(TS)


class WeatherEnsemble(Base):
    """Per-member ensemble daily extremes (one row per model per kind per capture) —
    the empirical forecast distribution behind P(temperature lands in bucket)."""

    __tablename__ = "weather_ensembles"
    __table_args__ = (Index("ix_weather_ensembles_city_time", "city", "captured_at"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    captured_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    city: Mapped[str] = mapped_column(String(32), nullable=False)
    target_date: Mapped[str | None] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # 'high' | 'low'
    model: Mapped[str | None] = mapped_column(String(32))
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    mean_f: Mapped[float | None] = mapped_column(Float)
    std_f: Mapped[float | None] = mapped_column(Float)
    members_json: Mapped[list | None] = mapped_column(JSONType)  # per-member degF values


class WeatherBucketSnapshot(Base):
    """The full bucket ladder's prices over time — the market's own implied temperature
    distribution, captured per cycle (throttled) for later mispricing analysis."""

    __tablename__ = "weather_bucket_snapshots"
    __table_args__ = (
        Index("ix_weather_bucket_snapshots_event_time", "event_ticker", "captured_at"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    captured_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    event_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    market_ticker: Mapped[str | None] = mapped_column(String(128))
    city: Mapped[str | None] = mapped_column(String(32))
    kind: Mapped[str | None] = mapped_column(String(8))  # 'high' | 'low'
    subtitle: Mapped[str | None] = mapped_column(String(64))
    low_f: Mapped[float | None] = mapped_column(Float)
    high_f: Mapped[float | None] = mapped_column(Float)
    yes_bid_cents: Mapped[float | None] = mapped_column(Float)
    yes_ask_cents: Mapped[float | None] = mapped_column(Float)
    mid_cents: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(Integer)
    hours_to_close: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str | None] = mapped_column(String(32))


class BackfillWeatherMarket(Base):
    """Settled temperature markets pulled from the Kalshi REST API as HISTORY.

    Deliberately separate from the live-collected tables (weather_settlements /
    weather_bucket_snapshots): rows here come from a one-time backfill of Kalshi's
    archives, not from the bot observing markets in real time, so research can
    always distinguish (and weight) the two provenances.
    """

    __tablename__ = "backfill_weather_markets"
    __table_args__ = (
        Index("ix_backfill_weather_markets_close", "close_time"),
        Index("ix_backfill_weather_markets_pending", "candles_fetched", "close_time"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    fetched_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    market_ticker: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    event_ticker: Mapped[str | None] = mapped_column(String(128), index=True)
    series_ticker: Mapped[str | None] = mapped_column(String(64))
    city: Mapped[str | None] = mapped_column(String(32))
    kind: Mapped[str | None] = mapped_column(String(8))  # 'high' | 'low'
    target_date: Mapped[str | None] = mapped_column(String(16))
    subtitle: Mapped[str | None] = mapped_column(String(64))
    low_f: Mapped[float | None] = mapped_column(Float)
    high_f: Mapped[float | None] = mapped_column(Float)
    result: Mapped[str | None] = mapped_column(String(8))  # 'yes' | 'no'
    open_time: Mapped[datetime | None] = mapped_column(TS)
    close_time: Mapped[datetime | None] = mapped_column(TS)
    candles_fetched: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    candle_count: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str | None] = mapped_column(String(16), default="kalshi_rest")


class BackfillWeatherCandle(Base):
    """Hourly (configurable) candlesticks for backfilled markets — the historical
    price paths behind calibration / exit-replay studies at real sample sizes.
    Same provenance rule as backfill_weather_markets: REST archive, not live capture."""

    __tablename__ = "backfill_weather_candles"
    __table_args__ = (
        UniqueConstraint("market_ticker", "end_period_ts", name="uq_backfill_candle"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    market_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    end_period_ts: Mapped[datetime] = mapped_column(TS, nullable=False)
    period_minutes: Mapped[int | None] = mapped_column(Integer)
    price_open: Mapped[float | None] = mapped_column(Float)  # cents
    price_high: Mapped[float | None] = mapped_column(Float)
    price_low: Mapped[float | None] = mapped_column(Float)
    price_close: Mapped[float | None] = mapped_column(Float)
    yes_bid_close: Mapped[float | None] = mapped_column(Float)
    yes_ask_close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(Integer)
    open_interest: Mapped[int | None] = mapped_column(Integer)


class BackfillRegimeMarket(Base):
    """Settled markets for the mmsell REGIME series, captured from the Kalshi REST API.

    Why this table exists: Kalshi serves only a rolling **~70-day** window of settled markets
    (measured 2026-08-03 — paging any series to cursor exhaustion bottoms out on the same date,
    KXNFLGAME returns zero rows, and `status=finalized/closed` and `min_close_ts` cannot reach
    behind it; authentication does not help). So last season is permanently unavailable, and
    every day we do not capture is a day of history lost for good. See
    `docs/MMSELL_SEASONAL_FORECAST.md`.

    Unlike `backfill_weather_markets` — a one-time reach back into the archive — this table is
    filled FORWARD, forever: the capture re-enumerates on a schedule so markets are stored while
    they are still inside the retention window. Same provenance rule though (Kalshi REST, not the
    bot observing markets live), which is why it keeps the `backfill_` prefix: research must be
    able to tell captured-history rows from live-collected ones.
    """

    __tablename__ = "backfill_regime_markets"
    __table_args__ = (
        Index("ix_backfill_regime_markets_close", "close_time"),
        Index("ix_backfill_regime_markets_pending", "candles_fetched", "close_time"),
        Index("ix_backfill_regime_markets_regime", "regime", "close_time"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    fetched_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    market_ticker: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    event_ticker: Mapped[str | None] = mapped_column(String(128), index=True)
    series_ticker: Mapped[str | None] = mapped_column(String(64))
    # Trading regime (NFL / NBA / Elections / ...) as classified at capture time. Stored rather
    # than derived so a later change to the regime map cannot silently rewrite history.
    regime: Mapped[str | None] = mapped_column(String(16))
    title: Mapped[str | None] = mapped_column(String(256))
    result: Mapped[str | None] = mapped_column(String(8))  # 'yes' | 'no'
    volume: Mapped[float | None] = mapped_column(Float)
    open_interest: Mapped[float | None] = mapped_column(Float)
    open_time: Mapped[datetime | None] = mapped_column(TS)
    close_time: Mapped[datetime | None] = mapped_column(TS)
    candles_fetched: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    candle_count: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str | None] = mapped_column(String(16), default="kalshi_rest")


class BackfillRegimeCandle(Base):
    """Candlesticks for a captured regime market over its final `MMSELL_HISTORY_CAPTURE_HOURS`.

    This is the price path the mmsell entry filter is replayed against (yes-mid in the band, yes
    ASK at or under the cap), so bid and ask closes are the load-bearing columns — a mid alone
    cannot reproduce an entry. Same provenance rule as backfill_regime_markets."""

    __tablename__ = "backfill_regime_candles"
    __table_args__ = (
        UniqueConstraint("market_ticker", "end_period_ts", name="uq_backfill_regime_candle"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    market_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    end_period_ts: Mapped[datetime] = mapped_column(TS, nullable=False)
    period_minutes: Mapped[int | None] = mapped_column(Integer)
    price_open: Mapped[float | None] = mapped_column(Float)  # cents
    price_high: Mapped[float | None] = mapped_column(Float)
    price_low: Mapped[float | None] = mapped_column(Float)
    price_close: Mapped[float | None] = mapped_column(Float)
    yes_bid_close: Mapped[float | None] = mapped_column(Float)
    yes_ask_close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(Integer)
    open_interest: Mapped[int | None] = mapped_column(Integer)


class PolymarketSnapshot(Base):
    """Polymarket's per-bucket implied probability over time — a SEPARATE-provenance
    cross-market signal (public Gamma API, not Kalshi, not live-collected Kalshi data).
    Drives the `weather_pm` book and feeds the eventual Kalshi-vs-Polymarket lead-lag
    study. Only the station-matched cities (LAX/MIA/AUS) are collected."""

    __tablename__ = "polymarket_snapshots"
    __table_args__ = (
        Index("ix_polymarket_snapshots_city_time", "city", "kind", "target_date", "captured_at"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    captured_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    city: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # 'high' | 'low'
    target_date: Mapped[str | None] = mapped_column(String(16))
    subtitle: Mapped[str | None] = mapped_column(String(64))
    low_f: Mapped[float | None] = mapped_column(Float)
    high_f: Mapped[float | None] = mapped_column(Float)
    yes_prob: Mapped[float | None] = mapped_column(Float)  # 0..1 implied probability
    source: Mapped[str | None] = mapped_column(String(20), default="polymarket_gamma")


class WeatherForecastOutcome(Base):
    """Persisted forecast->settlement validation dataset: one row per (settled event,
    intraday market-state cycle).

    Materialized AT SETTLEMENT by replaying the live-collected raw tables
    (weather_forecasts / weather_ensembles / weather_bucket_snapshots /
    weather_observations / polymarket_snapshots) with NO lookahead and labeling each
    cycle with the actual outcome from weather_settlements. This is the clean join that
    scripts/weather_model_check.py reconstructs ad-hoc on every run; storing it lets
    cal/dist/pm calibration and forecast-vs-market skill accumulate over time (a forecast
    backfill). A sentinel row (all features NULL, hours_to_close NULL) is written for a
    settled event that has no usable bucket snapshots, so the backfill work-queue
    (settled events with no rows here) does not reprocess it forever."""

    __tablename__ = "weather_forecast_outcomes"
    __table_args__ = (
        UniqueConstraint("event_ticker", "captured_at", name="uq_wfo_event_capture"),
        Index("ix_wfo_event", "event_ticker"),
        Index("ix_wfo_city_kind_date", "city", "kind", "target_date"),
        Index("ix_wfo_htc", "hours_to_close"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)

    # --- identity / grain ---
    event_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    city: Mapped[str | None] = mapped_column(String(32))
    kind: Mapped[str | None] = mapped_column(String(8))  # 'high' | 'low'
    target_date: Mapped[str | None] = mapped_column(String(16))
    captured_at: Mapped[datetime] = mapped_column(TS, nullable=False)  # cycle snapshot time
    hours_to_close: Mapped[float | None] = mapped_column(Float)  # NULL => sentinel row

    # --- features at this cycle (no lookahead) ---
    forecast_f: Mapped[float | None] = mapped_column(Float)  # nearest NWS point fc <= captured_at
    forecast_source: Mapped[str | None] = mapped_column(String(16))
    # HRRR (Open-Meteo ncep_hrrr_conus) point forecast, graded alongside NWS (collect-only)
    hrrr_f: Mapped[float | None] = mapped_column(Float)  # nearest HRRR point fc <= captured_at
    hrrr_abs_err_f: Mapped[float | None] = mapped_column(Float)  # |hrrr_f - actual_extreme|
    hrrr_divergence_f: Mapped[float | None] = mapped_column(Float)  # hrrr_f - market_implied_mean_f
    ens_mean_f: Mapped[float | None] = mapped_column(Float)
    ens_std_f: Mapped[float | None] = mapped_column(Float)
    ens_models: Mapped[int | None] = mapped_column(Integer)
    market_implied_mean_f: Mapped[float | None] = mapped_column(Float)  # prob-wt bucket midpoint
    market_fav_low_f: Mapped[float | None] = mapped_column(Float)
    market_fav_high_f: Mapped[float | None] = mapped_column(Float)
    market_fav_mid_cents: Mapped[float | None] = mapped_column(Float)
    divergence_f: Mapped[float | None] = mapped_column(Float)  # forecast_f - market_implied_mean_f
    obs_running_max_f: Mapped[float | None] = mapped_column(Float)
    obs_running_min_f: Mapped[float | None] = mapped_column(Float)
    pm_implied_mean_f: Mapped[float | None] = mapped_column(Float)  # polymarket, nullable

    # --- outcome label (constant across an event's rows; from weather_settlements) ---
    actual_high_f: Mapped[float | None] = mapped_column(Float)
    actual_low_f: Mapped[float | None] = mapped_column(Float)
    winning_low_f: Mapped[float | None] = mapped_column(Float)
    winning_high_f: Mapped[float | None] = mapped_column(Float)
    winning_subtitle: Mapped[str | None] = mapped_column(String(64))

    # --- calibration / skill labels (per-cycle, vs this cycle's distribution) ---
    market_prob_winner: Mapped[float | None] = mapped_column(Float)  # market P on the winning bucket
    ens_prob_winner: Mapped[float | None] = mapped_column(Float)  # ensemble P on the winner (sigma)
    forecast_abs_err_f: Mapped[float | None] = mapped_column(Float)  # |forecast_f - actual_extreme|
    market_abs_err_f: Mapped[float | None] = mapped_column(Float)  # |market_implied_mean_f - actual|

    # --- provenance / debug ---
    n_buckets: Mapped[int | None] = mapped_column(Integer)
    materialized_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    raw_json: Mapped[dict | None] = mapped_column(JSONType)  # compact per-bucket distribution


class CryptoSpotCandle(Base):
    """1-minute spot closes (Coinbase Exchange public candles) — the underlying feed for
    the theta book's remaining-window return distribution. Rolling window only (pruned to
    the model's trailing days); provenance is the exchange feed, not Kalshi."""

    __tablename__ = "crypto_spot_candles"
    __table_args__ = (
        UniqueConstraint("product", "minute_ts", name="uq_spot_candle"),
        Index("ix_crypto_spot_candles_product_time", "product", "minute_ts"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    product: Mapped[str] = mapped_column(String(16), nullable=False)  # e.g. BTC-USD
    minute_ts: Mapped[datetime] = mapped_column(TS, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str | None] = mapped_column(String(16), default="coinbase")


class CryptoLadderSnapshot(Base):
    """Hourly crypto ladder quotes + the theta model's probability at capture — the
    research dataset behind the theta book (mirrors weather_bucket_snapshots). Only
    events near settlement are snapshotted (the strategy's active window)."""

    __tablename__ = "crypto_ladder_snapshots"
    __table_args__ = (
        Index("ix_crypto_ladder_snapshots_event_time", "event_ticker", "captured_at"),
        # theta's live hot-market check reads the newest quote for ONE market; the
        # event-leading index above cannot serve a market_ticker-leading predicate, and this
        # table grows by ~69k rows/day (see alembic b8c9d0e1f2a3).
        Index("ix_crypto_ladder_mkt_time", "market_ticker", "captured_at"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    captured_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    series: Mapped[str | None] = mapped_column(String(32))
    event_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    market_ticker: Mapped[str | None] = mapped_column(String(128))
    strike_type: Mapped[str | None] = mapped_column(String(16))  # greater | less | between
    floor_strike: Mapped[float | None] = mapped_column(Float)
    cap_strike: Mapped[float | None] = mapped_column(Float)
    yes_bid_cents: Mapped[float | None] = mapped_column(Float)
    yes_ask_cents: Mapped[float | None] = mapped_column(Float)
    mid_cents: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    minutes_to_close: Mapped[float | None] = mapped_column(Float)
    spot: Mapped[float | None] = mapped_column(Float)  # underlying at capture
    model_p: Mapped[float | None] = mapped_column(Float)  # theta model P(YES), 0..1
    model_excess_cents: Mapped[float | None] = mapped_column(Float)  # mid - 100*model_p


class GameMarketMatch(Base):
    """A matched in-play game-market pair across venues — Kalshi per-team moneyline
    (e.g. KXWCGAME 'Reg Time: Portugal') vs the Polymarket same-team, same-day market
    ('Will Portugal win on 2026-07-04?') — the unit the XGAME tape collector polls.

    Matching is by (day, normalized team), precision over recall: a (day, team) key that
    appears more than once on either venue is ambiguous and skipped. pm_token_id is the
    FULL clobTokenId of the team-YES outcome (never truncated)."""

    __tablename__ = "game_market_matches"
    __table_args__ = (
        UniqueConstraint("kalshi_ticker", "pm_token_id", name="uq_game_match_pair"),
        Index("ix_game_market_matches_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow, nullable=False)
    sport: Mapped[str | None] = mapped_column(String(24))  # config tag, e.g. 'soccer'
    day: Mapped[str | None] = mapped_column(String(16))    # game date YYYY-MM-DD
    team: Mapped[str] = mapped_column(String(48), nullable=False)  # normalized YES team
    kalshi_series: Mapped[str | None] = mapped_column(String(32))
    kalshi_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    kalshi_event_ticker: Mapped[str | None] = mapped_column(String(128))
    kalshi_title: Mapped[str | None] = mapped_column(Text)
    pm_condition_id: Mapped[str | None] = mapped_column(String(80))
    pm_token_id: Mapped[str] = mapped_column(String(128), nullable=False)
    pm_question: Mapped[str | None] = mapped_column(Text)
    close_time: Mapped[datetime | None] = mapped_column(TS)  # Kalshi close ~ game end
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    # tape polling high-water marks (newest stored trade per venue)
    kalshi_since_ts: Mapped[datetime | None] = mapped_column(TS)
    pm_since_ts: Mapped[datetime | None] = mapped_column(TS)
    kalshi_trades: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pm_trades: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_polled_at: Mapped[datetime | None] = mapped_column(TS)


class GameTapeSnapshot(Base):
    """Raw in-play trade-tape rows from BOTH venues for matched game markets — the
    XGAME research dataset (docs/IDEA_MODEL_20260704.md). SEPARATE provenance: rows come
    from the Kalshi public trades API and the Polymarket data-api, not from any
    live-collected weather/crypto table. Each row is one venue trade with the venue's
    own timestamp; the analysis (scripts/xgame_tape_study.py) builds ~10s bars from
    them, so poll cadence does not limit bar resolution.

    team_prob_cents normalizes both venues onto P(matched team wins) in cents: Kalshi
    trades are already the team's YES price; Polymarket trades on the complementary
    token are flipped (100 - price)."""

    __tablename__ = "game_tape_snapshots"
    __table_args__ = (
        UniqueConstraint("venue", "trade_id", name="uq_game_tape_trade"),
        Index("ix_game_tape_match_venue_time", "match_id", "venue", "traded_at"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    captured_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    match_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("game_market_matches.id"), nullable=False
    )
    venue: Mapped[str] = mapped_column(String(12), nullable=False)  # kalshi | polymarket
    market_id: Mapped[str | None] = mapped_column(String(128))  # ticker / pm asset id
    trade_id: Mapped[str] = mapped_column(String(160), nullable=False)
    traded_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    price_cents: Mapped[float | None] = mapped_column(Float)  # raw instrument price
    team_prob_cents: Mapped[float | None] = mapped_column(Float)  # normalized P(team)
    size: Mapped[float | None] = mapped_column(Float)
    taker_side: Mapped[str | None] = mapped_column(String(8))  # kalshi yes/no; pm buy/sell


class SystemEvent(Base):
    __tablename__ = "system_events"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    component: Mapped[str] = mapped_column(String(48), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    raw_json: Mapped[dict | None] = mapped_column(JSONType)
