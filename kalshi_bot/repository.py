"""Database write helpers used by the scanner.

Each helper takes an active session, adds/updates a row, flushes so the caller
gets a populated primary key, and returns the ORM object. Commit/rollback is
owned by the caller's `session_scope`.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from . import models as m
from .risk.manager import RiskDecision
from .scanner.metrics import MarketMetrics, orderbook_levels, parse_dt
from .scanner.signals import SignalResult

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_json(obj: Any) -> Any:
    """Return obj if it is JSON-serializable, else a stringified fallback."""
    try:
        json.dumps(obj, default=str)
        return obj
    except (TypeError, ValueError):
        return {"_unserializable": str(obj)[:500]}


def start_bot_run(session, mode: str) -> m.BotRun:
    run = m.BotRun(started_at=_now(), mode=mode, status="running")
    session.add(run)
    session.flush()
    return run


def finish_bot_run(
    session,
    run: m.BotRun,
    *,
    status: str,
    markets_scanned: int,
    candidates_found: int,
    error_message: str | None = None,
) -> None:
    run.finished_at = _now()
    run.status = status
    run.markets_scanned = markets_scanned
    run.candidates_found = candidates_found
    run.error_message = error_message
    session.add(run)
    session.flush()


def upsert_market(session, market: dict) -> m.Market:
    ticker = market["ticker"]
    obj = session.scalar(select(m.Market).where(m.Market.ticker == ticker))
    if obj is None:
        obj = m.Market(ticker=ticker, created_at=_now())
        session.add(obj)
    obj.title = market.get("title")
    obj.category = market.get("category")
    obj.close_time = parse_dt(market.get("close_time"))
    obj.expiration_time = parse_dt(market.get("expiration_time"))
    obj.settlement_source = market.get("settlement_source") or market.get(
        "settlement_sources_text"
    )
    obj.rules_summary = market.get("rules_primary")
    obj.status = market.get("status")
    obj.updated_at = _now()
    session.flush()
    return obj


def insert_market_snapshot(
    session, market: dict, metrics: MarketMetrics, *, captured_at: datetime | None = None
) -> m.MarketSnapshot:
    snap = m.MarketSnapshot(
        market_ticker=metrics.ticker,
        captured_at=captured_at or _now(),
        yes_bid=metrics.best_yes_bid,
        yes_ask=metrics.best_yes_ask,
        no_bid=metrics.best_no_bid,
        no_ask=metrics.best_no_ask,
        last_price=metrics.last_price,
        volume=metrics.volume,
        open_interest=metrics.open_interest,
        spread=metrics.spread,
        midpoint=metrics.midpoint,
        liquidity_score=metrics.liquidity_score,
        raw_json=_safe_json(market),
    )
    session.add(snap)
    session.flush()
    return snap


def insert_orderbook_snapshot(
    session,
    ticker: str,
    metrics: MarketMetrics,
    orderbook: dict,
    *,
    captured_at: datetime | None = None,
) -> m.OrderbookSnapshot:
    yes_raw, no_raw = orderbook_levels(orderbook)
    snap = m.OrderbookSnapshot(
        market_ticker=ticker,
        captured_at=captured_at or _now(),
        yes_levels_json=_safe_json(yes_raw),
        no_levels_json=_safe_json(no_raw),
        best_yes_bid=metrics.best_yes_bid,
        best_yes_ask=metrics.best_yes_ask,
        best_no_bid=metrics.best_no_bid,
        best_no_ask=metrics.best_no_ask,
        top_depth=metrics.top_depth,
        raw_json=_safe_json(orderbook),
    )
    session.add(snap)
    session.flush()
    return snap


def insert_signal(
    session, signal: SignalResult, metrics: MarketMetrics, *, snapshot_id: int, bot_mode: str
) -> m.Signal:
    row = m.Signal(
        market_ticker=signal.ticker,
        created_at=_now(),
        signal_type="deterministic_scan",
        bot_mode=bot_mode,
        implied_probability=signal.implied_probability,
        model_probability=None,
        edge=None,
        confidence=signal.confidence,
        label=signal.label,
        reason=signal.reason,
        input_snapshot_id=snapshot_id,
    )
    session.add(row)
    session.flush()
    return row


def insert_risk_event(
    session, signal_id: int | None, ticker: str, decision: RiskDecision
) -> m.RiskEvent:
    row = m.RiskEvent(
        signal_id=signal_id,
        market_ticker=ticker,
        created_at=_now(),
        approved=decision.approved,
        reason_codes_json=decision.reason_codes,
        max_allowed_quantity=decision.max_allowed_quantity,
        max_allowed_price=decision.max_allowed_price,
    )
    session.add(row)
    session.flush()
    return row


def insert_account_snapshot(
    session,
    *,
    cash_balance: float | None,
    portfolio_value: float | None = None,
    total_exposure: float | None = None,
    raw: dict | None = None,
) -> m.AccountSnapshot:
    row = m.AccountSnapshot(
        captured_at=_now(),
        cash_balance=cash_balance,
        portfolio_value=portfolio_value,
        total_exposure=total_exposure,
        raw_json=_safe_json(raw or {}),
    )
    session.add(row)
    session.flush()
    return row


# --- paper trading -----------------------------------------------------------


def create_paper_trade(
    session,
    *,
    signal_id: int | None,
    ticker: str,
    side: str,
    action: str,
    assumed_price: int,
    quantity: int,
    fill_assumption: str,
    entry_fee: float,
) -> m.PaperTrade:
    row = m.PaperTrade(
        signal_id=signal_id,
        market_ticker=ticker,
        created_at=_now(),
        side=side,
        action=action,
        assumed_price=assumed_price,
        quantity=quantity,
        fill_assumption=fill_assumption,
        status="open",
        fees=entry_fee,
    )
    session.add(row)
    session.flush()
    return row


def record_no_fill(
    session,
    *,
    signal_id: int | None,
    ticker: str,
    side: str,
    action: str,
    assumed_price: int | None,
    fill_assumption: str,
) -> m.PaperTrade:
    row = m.PaperTrade(
        signal_id=signal_id,
        market_ticker=ticker,
        created_at=_now(),
        side=side,
        action=action,
        assumed_price=assumed_price,
        quantity=0,
        fill_assumption=fill_assumption,
        status="no_fill",
        closed_at=_now(),
    )
    session.add(row)
    session.flush()
    return row


def get_open_paper_trades(session) -> list[m.PaperTrade]:
    return list(
        session.scalars(select(m.PaperTrade).where(m.PaperTrade.status == "open")).all()
    )


def get_open_paper_position(session, ticker: str) -> m.PaperPosition | None:
    return session.scalar(
        select(m.PaperPosition).where(
            m.PaperPosition.market_ticker == ticker, m.PaperPosition.status == "open"
        )
    )


def count_open_paper_positions(session) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(m.PaperPosition)
            .where(m.PaperPosition.status == "open")
        )
        or 0
    )


def open_paper_exposure(session, ticker: str | None = None) -> float:
    """Open paper exposure in dollars (sum of quantity * avg_price_cents / 100)."""
    stmt = select(m.PaperPosition).where(m.PaperPosition.status == "open")
    if ticker is not None:
        stmt = stmt.where(m.PaperPosition.market_ticker == ticker)
    total = 0.0
    for pos in session.scalars(stmt).all():
        total += (pos.quantity or 0) * float(pos.avg_price or 0) / 100.0
    return total


def open_paper_position_for_trade(
    session, *, ticker: str, side: str, quantity: int, avg_price: int
) -> m.PaperPosition:
    pos = m.PaperPosition(
        market_ticker=ticker,
        side=side,
        quantity=quantity,
        avg_price=avg_price,
        status="open",
        opened_at=_now(),
    )
    session.add(pos)
    session.flush()
    return pos


def close_paper_trade(
    session,
    trade: m.PaperTrade,
    *,
    status: str,
    pnl: float,
    exit_price: int | None = None,
    resolved_value: int | None = None,
    exit_fee: float = 0.0,
) -> None:
    trade.status = status
    trade.exit_price = exit_price
    trade.resolved_value = resolved_value
    trade.pnl = pnl
    trade.fees = float(trade.fees or 0.0) + exit_fee
    trade.closed_at = _now()
    session.add(trade)

    pos = get_open_paper_position(session, trade.market_ticker)
    if pos is not None:
        pos.status = status
        pos.pnl = pnl
        pos.unrealized_pnl = None
        pos.closed_at = _now()
        session.add(pos)
    session.flush()


def mark_paper_position(session, ticker: str, unrealized_pnl: float) -> None:
    pos = get_open_paper_position(session, ticker)
    if pos is not None:
        pos.unrealized_pnl = unrealized_pnl
        session.add(pos)
        session.flush()


def log_system_event(
    session, *, level: str, component: str, message: str, raw: dict | None = None
) -> m.SystemEvent:
    row = m.SystemEvent(
        created_at=_now(),
        level=level,
        component=component,
        message=message,
        raw_json=_safe_json(raw or {}),
    )
    session.add(row)
    session.flush()
    return row
