"""The execution research view — read-only (docs/MMSELL_QUEUE_FILL_TELEMETRY.md §Dashboard).

Two payloads: an aggregate coverage/funnel summary, and one order's full trace (queue
history, fills, trades at our price, status events, lifecycle). Diagnostics only: no fill
probability, no EV, no score — a predictive number does not belong on the production
screen until its validation exists.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from .. import models as m

MAX_TICKS = 2000
MAX_TRADES = 500


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _iso(dt) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _f(x):
    return float(x) if x is not None else None


def build_summary(session, *, hours: int = 72, now: datetime | None = None) -> dict:
    now = _now(now)
    since = now - timedelta(hours=max(1, min(int(hours or 72), 24 * 30)))

    ev_counts = dict(session.execute(
        select(m.ExecutionCollectorEvent.kind, func.count())
        .where(m.ExecutionCollectorEvent.at >= since)
        .group_by(m.ExecutionCollectorEvent.kind)).all())
    last_event = session.execute(
        select(m.ExecutionCollectorEvent.kind, m.ExecutionCollectorEvent.at)
        .order_by(m.ExecutionCollectorEvent.at.desc()).limit(1)).first()

    orders = session.scalars(select(m.LiveOrder).where(
        m.LiveOrder.created_at >= since, m.LiveOrder.kalshi_order_id.isnot(None))).all()
    koids = [o.kalshi_order_id for o in orders]
    ctx_ids = set(session.scalars(select(m.ExecutionOrderContext.live_order_id).where(
        m.ExecutionOrderContext.live_order_id.in_([o.id for o in orders]))).all()) if orders else set()
    tick_rows = session.execute(
        select(m.LiveOrderQueueTick.kalshi_order_id, m.LiveOrderQueueTick.trigger,
               m.LiveOrderQueueTick.contracts_ahead, m.LiveOrderQueueTick.captured_at)
        .where(m.LiveOrderQueueTick.kalshi_order_id.in_(koids))
        .order_by(m.LiveOrderQueueTick.captured_at)).all() if koids else []
    ws_fill_counts = dict(session.execute(
        select(m.ExecutionFillEvent.kalshi_order_id, func.count())
        .where(m.ExecutionFillEvent.kalshi_order_id.in_(koids))
        .group_by(m.ExecutionFillEvent.kalshi_order_id)).all()) if koids else {}

    by_trigger: dict[str, int] = {}
    first_ahead: dict[str, int] = {}
    ticked: set[str] = set()
    at_rest: set[str] = set()
    terminal: set[str] = set()
    for koid, trigger, ahead, _at in tick_rows:
        ticked.add(koid)
        by_trigger[trigger or "(none)"] = by_trigger.get(trigger or "(none)", 0) + 1
        if trigger == "at_rest":
            at_rest.add(koid)
        if trigger == "terminal":
            terminal.add(koid)
        if ahead is not None and koid not in first_ahead:
            first_ahead[koid] = int(ahead)

    funnel: dict[str, dict[str, int]] = {}
    for o in orders:
        row = funnel.setdefault(o.strategy or "?", {
            "placed": 0, "filled": 0, "canceled": 0, "open": 0, "refused": 0, "multi_fill": 0})
        row["placed"] += 1
        if o.status == "filled":
            row["filled"] += 1
        elif o.status == "canceled":
            row["canceled"] += 1
        elif o.status in ("resting", "partial", "submitted", "pending", "unknown"):
            row["open"] += 1
        else:
            row["refused"] += 1
        if ws_fill_counts.get(o.kalshi_order_id, 0) > 1:
            row["multi_fill"] += 1

    ahead_values = sorted(first_ahead.values())

    def pct(p):
        if not ahead_values:
            return None
        return ahead_values[min(len(ahead_values) - 1, int(p * len(ahead_values)))]

    n_orders = len(orders)
    book_events = session.scalar(select(func.count()).select_from(m.ExecutionBookEvent).where(
        m.ExecutionBookEvent.received_at >= since)) or 0
    trade_events = session.scalar(select(func.count()).select_from(m.ExecutionTradeEvent).where(
        m.ExecutionTradeEvent.received_at >= since)) or 0
    ws_fills = session.scalar(select(func.count()).select_from(m.ExecutionFillEvent).where(
        m.ExecutionFillEvent.received_at >= since)) or 0
    ws_fills_matched = session.scalar(select(func.count()).select_from(m.ExecutionFillEvent).where(
        m.ExecutionFillEvent.received_at >= since,
        m.ExecutionFillEvent.rest_fill_id.isnot(None))) or 0

    return {
        "generated_at": now.isoformat(),
        "hours": int(hours or 72),
        "collector": {
            "events": ev_counts,
            "last_event": {"kind": last_event[0], "at": _iso(last_event[1])} if last_event else None,
            "alive": bool(last_event) and last_event[0] != "thread_stopped",
        },
        "coverage": {
            "orders": n_orders,
            "with_context": len(ctx_ids),
            "with_at_rest_tick": len(at_rest),
            "with_any_tick": len(ticked),
            "with_terminal_tick": len(terminal),
            "ticks_by_trigger": by_trigger,
            "book_events": int(book_events),
            "trade_events": int(trade_events),
            "ws_fills": int(ws_fills),
            "ws_fills_matched_to_rest": int(ws_fills_matched),
        },
        "funnel": funnel,
        "queue_at_rest": {
            "orders": len(ahead_values),
            "front_share": (sum(1 for v in ahead_values if v == 0) / len(ahead_values)
                            if ahead_values else None),
            "p25": pct(0.25), "p50": pct(0.5), "p75": pct(0.75), "p90": pct(0.9),
        },
        "recent_orders": [
            {"kalshi_order_id": o.kalshi_order_id, "strategy": o.strategy,
             "market": o.market_ticker, "no_price": o.limit_price, "quantity": o.quantity,
             "status": o.status, "created_at": _iso(o.created_at),
             "first_ahead": first_ahead.get(o.kalshi_order_id),
             "ticks": sum(1 for r in tick_rows if r[0] == o.kalshi_order_id)}
            for o in sorted(orders, key=lambda x: x.created_at or now, reverse=True)[:100]
        ],
    }


def build_order_trace(session, kalshi_order_id: str, *, now: datetime | None = None) -> dict | None:
    now = _now(now)
    order = session.scalars(select(m.LiveOrder).where(
        m.LiveOrder.kalshi_order_id == kalshi_order_id)).first()
    if order is None:
        return None
    ctx = session.scalars(select(m.ExecutionOrderContext).where(
        m.ExecutionOrderContext.live_order_id == order.id)
        .order_by(m.ExecutionOrderContext.id.desc())).first()
    ticks = session.scalars(select(m.LiveOrderQueueTick).where(
        m.LiveOrderQueueTick.kalshi_order_id == kalshi_order_id)
        .order_by(m.LiveOrderQueueTick.captured_at).limit(MAX_TICKS)).all()
    fills = session.scalars(select(m.ExecutionFillEvent).where(
        m.ExecutionFillEvent.kalshi_order_id == kalshi_order_id)
        .order_by(m.ExecutionFillEvent.received_at)).all()
    rest_fills = session.scalars(select(m.Fill).where(
        m.Fill.kalshi_order_id == kalshi_order_id).order_by(m.Fill.id)).all()
    order_events = session.scalars(select(m.ExecutionOrderEvent).where(
        m.ExecutionOrderEvent.kalshi_order_id == kalshi_order_id)
        .order_by(m.ExecutionOrderEvent.received_at)).all()
    our_yes = (100 - order.limit_price) if order.side == "no" and order.limit_price is not None \
        else order.limit_price
    created = order.created_at if order.created_at and order.created_at.tzinfo else (
        order.created_at.replace(tzinfo=timezone.utc) if order.created_at else now)
    trades = session.scalars(select(m.ExecutionTradeEvent).where(
        m.ExecutionTradeEvent.market_ticker == order.market_ticker,
        m.ExecutionTradeEvent.received_at >= created)
        .order_by(m.ExecutionTradeEvent.received_at).limit(MAX_TRADES)).all()
    lifecycle = session.scalars(select(m.ExecutionMarketEvent).where(
        m.ExecutionMarketEvent.market_ticker == order.market_ticker)
        .order_by(m.ExecutionMarketEvent.received_at)).all()
    book_count = session.scalar(select(func.count()).select_from(m.ExecutionBookEvent).where(
        m.ExecutionBookEvent.market_ticker == order.market_ticker)) or 0

    def feat(t, key):
        return (t.features_json or {}).get(key) if isinstance(t.features_json, dict) else None

    return {
        "generated_at": now.isoformat(),
        "order": {
            "kalshi_order_id": order.kalshi_order_id, "client_order_id": order.client_order_id,
            "strategy": order.strategy, "market": order.market_ticker,
            "event": order.event_ticker, "side": order.side, "no_price": order.limit_price,
            "our_yes_price": our_yes, "quantity": order.quantity, "status": order.status,
            "cancel_reason": order.cancel_reason, "created_at": _iso(order.created_at),
        },
        "context": None if ctx is None else {
            "decided_at": _iso(ctx.decided_at), "acked_at": _iso(ctx.acked_at),
            "ack_ts_ms": ctx.ack_ts_ms, "cancel_requested_at": _iso(ctx.cancel_requested_at),
            "cancel_confirmed_at": _iso(ctx.cancel_confirmed_at),
            "terminal_reason": ctx.terminal_reason, "twin_tag": ctx.twin_tag,
            "candidate_mid": ctx.candidate_mid, "best_yes_bid": ctx.best_yes_bid,
            "best_yes_ask": ctx.best_yes_ask, "best_no_bid": ctx.best_no_bid,
            "spread": ctx.spread, "depth_at_best_ask": ctx.depth_at_best_ask,
            "hours_to_close": ctx.hours_to_close, "band_lo": ctx.band_lo, "band_hi": ctx.band_hi,
            "max_yes": ctx.max_yes, "review_tier": ctx.review_tier, "regime": ctx.regime,
            "market_type": ctx.market_type, "market_mode": ctx.market_mode,
            "open_positions_for_tag": ctx.open_positions_for_tag,
            "open_position_cap": ctx.open_position_cap, "hot_entry": ctx.hot_entry,
            "offset_cents": ctx.offset_cents,
        },
        "queue": [
            {"at": _iso(t.captured_at), "trigger": t.trigger, "ahead": t.contracts_ahead,
             "remaining": _f(t.remaining_count), "rest_seconds": t.rest_seconds,
             "best_yes_bid": feat(t, "best_yes_bid"), "best_yes_ask": feat(t, "best_yes_ask"),
             "qty_at_our_price": feat(t, "qty_at_our_price"), "qty_better": feat(t, "qty_better"),
             "trades_since_placement": feat(t, "trades_since_placement"),
             "book_valid": feat(t, "book_valid")}
            for t in ticks
        ],
        "fills": [
            {"trade_id": f.trade_id, "ts_ms": f.ts_ms, "count": _f(f.count_fp),
             "yes_price": f.yes_price_cents, "fee": _f(f.fee_cost), "is_taker": f.is_taker,
             "rest_matched": f.rest_fill_id is not None}
            for f in fills
        ],
        "rest_fills": [
            {"fill_id": f.kalshi_fill_id, "filled_at": _iso(f.filled_at), "price": f.price,
             "quantity": f.quantity, "fee": _f(f.fee)} for f in rest_fills
        ],
        "order_events": [
            {"at": _iso(e.received_at), "status": e.status, "filled": _f(e.fill_count_fp),
             "remaining": _f(e.remaining_count_fp)} for e in order_events
        ],
        "trades": [
            {"ts_ms": t.ts_ms, "yes_price": t.yes_price_cents, "count": _f(t.count_fp),
             "taker": t.taker_outcome_side, "at_our_price": t.yes_price_cents == our_yes,
             "block": t.is_block_trade}
            for t in trades
        ],
        "lifecycle": [
            {"at": _iso(e.received_at), "event_type": e.event_type,
             "is_deactivated": e.is_deactivated} for e in lifecycle
        ],
        "book_events": int(book_count),
    }
