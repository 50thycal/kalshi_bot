"""DB writes for the shadow collector — the only module in the package that constructs rows.
Kept apart from `repository.py` on purpose: nothing in the trading path should import this,
and nothing here touches a trading table."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from .. import models as m


def _safe_json(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return {"unserializable": str(value)[:500]}


def record_event(session, *, kind: str, at: datetime, ticker: str | None = None,
                 connection_id: int | None = None, detail: str | None = None,
                 detail_json: Any | None = None) -> m.IncentiveCollectorEvent:
    row = m.IncentiveCollectorEvent(
        at=at, kind=kind, market_ticker=ticker, connection_id=connection_id,
        detail=detail[:2000] if detail else None,
        detail_json=_safe_json(detail_json) if detail_json is not None else None)
    session.add(row)
    return row


def insert_book_event(session, **fields) -> m.IncentiveBookEvent:
    if fields.get("raw_json") is not None:
        fields["raw_json"] = _safe_json(fields["raw_json"])
    row = m.IncentiveBookEvent(**fields)
    session.add(row)
    return row


def insert_trade_event(session, **fields) -> m.IncentiveTradeEvent | None:
    exists = session.scalar(select(func.count()).select_from(m.IncentiveTradeEvent).where(
        m.IncentiveTradeEvent.trade_id == fields["trade_id"]))
    if exists:
        return None
    if fields.get("raw_json") is not None:
        fields["raw_json"] = _safe_json(fields["raw_json"])
    row = m.IncentiveTradeEvent(**fields)
    session.add(row)
    return row


def insert_market_snapshot(session, **fields) -> m.IncentiveMarketSnapshot:
    for key in ("yes_levels_json", "no_levels_json"):
        if fields.get(key) is not None:
            fields[key] = _safe_json(fields[key])
    row = m.IncentiveMarketSnapshot(**fields)
    session.add(row)
    return row


def insert_quote(session, **fields) -> m.IncentiveShadowQuote:
    if fields.get("book_json") is not None:
        fields["book_json"] = _safe_json(fields["book_json"])
    row = m.IncentiveShadowQuote(**fields)
    session.add(row)
    session.flush()
    return row


def end_quote(session, quote_id: int, *, ended_at: datetime, reason: str, rest_seconds: float) -> None:
    row = session.get(m.IncentiveShadowQuote, quote_id)
    if row is not None and row.ended_at is None:
        row.ended_at = ended_at
        row.end_reason = reason[:48]
        row.rest_seconds = rest_seconds


def insert_shadow_event(session, **fields) -> m.IncentiveShadowEvent:
    if fields.get("detail_json") is not None:
        fields["detail_json"] = _safe_json(fields["detail_json"])
    row = m.IncentiveShadowEvent(**fields)
    session.add(row)
    return row


def insert_fill(session, **fields) -> m.IncentiveShadowFill:
    row = m.IncentiveShadowFill(**fields)
    session.add(row)
    return row


def insert_mark(session, **fields) -> m.IncentiveShadowMark:
    row = m.IncentiveShadowMark(**fields)
    session.add(row)
    return row


def insert_outcome(session, **fields) -> m.IncentiveShadowOutcome:
    row = m.IncentiveShadowOutcome(**fields)
    session.add(row)
    return row


def stamp_settlement(session, *, quote_id: int, settled_at: datetime, result: str,
                     pnl_by_model: dict[str, float | None]) -> int:
    """The one documented mutation on outcomes: settlement fields, once."""
    rows = session.scalars(select(m.IncentiveShadowOutcome).where(
        m.IncentiveShadowOutcome.quote_id == quote_id,
        m.IncentiveShadowOutcome.settled_at.is_(None))).all()
    n = 0
    for row in rows:
        pnl = pnl_by_model.get(row.fill_model)
        row.settled_at = settled_at
        row.settlement_result = result
        row.settlement_pnl_usd = pnl
        base = float(row.net_before_settlement_usd or 0.0)
        # net_before includes the single-leg MTM at the bid; settlement replaces that mark.
        mtm = float(row.single_leg_mtm_5m_usd or 0.0)
        row.net_after_settlement_usd = round(base - mtm + (pnl or 0.0), 4)
        n += 1
    return n


def unsettled_single_legs(session) -> list[m.IncentiveShadowOutcome]:
    return list(session.scalars(select(m.IncentiveShadowOutcome).where(
        m.IncentiveShadowOutcome.settled_at.is_(None),
        m.IncentiveShadowOutcome.single_leg_side.isnot(None))).all())


def open_quotes(session) -> list[m.IncentiveShadowQuote]:
    return list(session.scalars(select(m.IncentiveShadowQuote).where(
        m.IncentiveShadowQuote.ended_at.is_(None))).all())
