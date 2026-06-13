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


class LiveOrder(Base):
    __tablename__ = "live_orders"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    signal_id: Mapped[int | None] = mapped_column(BigIntId, ForeignKey("signals.id"))
    kalshi_order_id: Mapped[str | None] = mapped_column(String(128), index=True)
    market_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    side: Mapped[str | None] = mapped_column(String(8))
    action: Mapped[str | None] = mapped_column(String(8))
    limit_price: Mapped[int | None] = mapped_column(Integer)
    quantity: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(24))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    raw_order_json: Mapped[dict | None] = mapped_column(JSONType)


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

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    market_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    side: Mapped[str | None] = mapped_column(String(8))
    quantity: Mapped[int | None] = mapped_column(Integer)
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


class SystemEvent(Base):
    __tablename__ = "system_events"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    component: Mapped[str] = mapped_column(String(48), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    raw_json: Mapped[dict | None] = mapped_column(JSONType)
