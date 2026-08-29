"""Canonical extraction of one side (a "leg") of a live/paper paired run.

This module is the single place that decides what a *position* is, what it cost,
what it is worth now and what it realized — separately for the LIVE leg (real
money on Kalshi) and the PAPER leg (the twin book). The comparison layer then
diffs two objects of the same shape, so a divergence can never be an artifact of
the two sides being measured differently.

Units and conventions, verified against the writers (kalshi_bot/live/executor.py,
kalshi_bot/paper/engine.py, kalshi_bot/twin/harness.py) — every one of these was a
place the two sides could have silently disagreed:

* **Prices are integer cents on the side actually traded.** mmsell rests a BUY-NO
  at the no-bid, so an entry price of 93 means 93¢ per NO contract (equivalently
  selling YES at 7¢). `fills.price` is already stored on the traded side, so it is
  directly comparable to `paper_trades.assumed_price`; there is no 100−x flip.
* **Quantity is contracts.** `positions.quantity` / `quantity_fp` are *signed*
  (negative = NO side), so every read here takes `abs()`.
* **P&L is US dollars.**
* **`positions.avg_price` is the cost basis in cents** on the held side, derived by
  the executor as `market_exposure_dollars / |position| * 100`.

The two realized-P&L figures do NOT come from the same arithmetic, and that is a
finding rather than a bug to paper over:

* LIVE realized is **reported by the exchange**: the reconcile loop writes a
  `quantity=0` position snapshot with `revenue/100 − cost − fee_cost` taken from
  `/portfolio/settlements`. It is net of the fees Kalshi actually charged.
* PAPER realized is **computed by our simulator**:
  `qty × (resolved_value − assumed_price) / 100 − entry_fee`, with settlement free.

Unrealized is not stored on either side (`positions.unrealized_pnl` is never
written by the live path; paper stores its own but against paper's own mark), so
this module derives it for BOTH legs from the *same* tick — see marks.py. That
removes "different mark source" as an explanation for a divergence by
construction, which is what makes the remaining gap meaningful.

Everything here is a SELECT.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import and_, func, select

from .. import models as m

LIVE = "live"
PAPER = "paper"

# paper_trades.status values that mean the trade is finished.
PAPER_OPEN = "open"
PAPER_TERMINAL = ("settled", "closed_void", "closed_tp", "closed_sl", "closed_timeout")

# live_orders.status values that mean the order can still fill.
LIVE_WORKING = ("submitted", "resting", "partial")
LIVE_DEAD = ("canceled", "rejected", "expired", "error", "not_landed")


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _f(value) -> float | None:
    """Numeric/Decimal -> float, preserving None (a missing value is NOT zero)."""
    return None if value is None else float(value)


def _f0(value) -> float:
    return 0.0 if value is None else float(value)


def _iso(dt: datetime | None) -> str | None:
    aware = _aware(dt)
    return aware.isoformat() if aware else None


@dataclass
class LegPosition:
    """One market's exposure on one side of the pair."""

    ticker: str
    environment: str
    side: str | None = None
    quantity: float | None = None
    entry_price_cents: float | None = None
    cost_basis_usd: float | None = None
    entry_at: datetime | None = None
    exit_at: datetime | None = None
    # open | settled | closed | unfilled — 'unfilled' is a live order that never
    # became a position, which is exactly the case paper cannot produce.
    status: str = "open"
    realized_pnl_usd: float | None = None
    resolved_value_cents: int | None = None
    entry_fees_usd: float | None = None
    mark_cents: float | None = None
    mark_at: datetime | None = None
    unrealized_pnl_usd: float | None = None
    stored_unrealized_pnl_usd: float | None = None  # what that system itself recorded
    order_count: int = 0
    fill_count: int = 0

    @property
    def is_open(self) -> bool:
        return self.status == "open"

    @property
    def is_closed(self) -> bool:
        return self.status in ("settled", "closed")

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "environment": self.environment,
            "side": self.side,
            "quantity": self.quantity,
            "entry_price_cents": self.entry_price_cents,
            "cost_basis_usd": _round(self.cost_basis_usd),
            "entry_at": _iso(self.entry_at),
            "exit_at": _iso(self.exit_at),
            "status": self.status,
            "realized_pnl_usd": _round(self.realized_pnl_usd),
            "resolved_value_cents": self.resolved_value_cents,
            "entry_fees_usd": _round(self.entry_fees_usd, 4),
            "mark_cents": self.mark_cents,
            "mark_at": _iso(self.mark_at),
            "unrealized_pnl_usd": _round(self.unrealized_pnl_usd),
            "stored_unrealized_pnl_usd": _round(self.stored_unrealized_pnl_usd),
            "hold_hours": self.hold_hours,
            "order_count": self.order_count,
            "fill_count": self.fill_count,
        }

    @property
    def hold_hours(self) -> float | None:
        start = _aware(self.entry_at)
        if start is None:
            return None
        end = _aware(self.exit_at) or datetime.now(timezone.utc)
        return round(max(0.0, (end - start).total_seconds() / 3600.0), 2)


def _round(value: float | None, places: int = 2) -> float | None:
    return None if value is None else round(float(value), places)


@dataclass
class Leg:
    """One whole side of the pair over the epoch window."""

    environment: str
    tag: str
    positions: list[LegPosition] = field(default_factory=list)
    # Explicit "we could not measure this" rather than a silent zero.
    realized_pnl_usd: float | None = None
    unrealized_pnl_usd: float | None = None
    entry_fees_usd: float | None = None
    last_data_at: datetime | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def open_positions(self) -> list[LegPosition]:
        return [p for p in self.positions if p.is_open]

    @property
    def closed_positions(self) -> list[LegPosition]:
        return [p for p in self.positions if p.is_closed]

    @property
    def unfilled(self) -> list[LegPosition]:
        return [p for p in self.positions if p.status == "unfilled"]

    def by_ticker(self) -> dict[str, LegPosition]:
        return {p.ticker: p for p in self.positions}

    def summary(self) -> dict:
        closed, opened = self.closed_positions, self.open_positions
        wins = sum(1 for p in closed if (p.realized_pnl_usd or 0) > 0)
        losses = sum(1 for p in closed if (p.realized_pnl_usd or 0) < 0)
        contracts_closed = sum(abs(p.quantity or 0) for p in closed)
        gross = None
        if self.realized_pnl_usd is not None and self.unrealized_pnl_usd is not None:
            gross = self.realized_pnl_usd + self.unrealized_pnl_usd
        # Capital deployed: the sum of entry cost basis across EVERY filled
        # position this leg ever opened, open or closed. This is a distinct
        # question from P&L or current exposure — "how many dollars did we
        # actually put to work" — and it is not netted against exits, so 20
        # trades at $2 each read as $40 deployed regardless of outcome. A
        # position with no cost basis (an unfilled live order) contributes 0,
        # since no capital was ever actually committed.
        filled = [p for p in self.positions if p.status != "unfilled"]
        capital_deployed = sum(p.cost_basis_usd or 0 for p in filled)
        return {
            "environment": self.environment,
            "tag": self.tag,
            "positions_total": len(self.positions),
            "positions_open": len(opened),
            "positions_closed": len(closed),
            "positions_unfilled": len(self.unfilled),
            "contracts_open": sum(abs(p.quantity or 0) for p in opened),
            "contracts_closed": contracts_closed,
            "wins": wins,
            "losses": losses,
            # None, not 0%, until something has actually closed.
            "win_rate": round(wins / len(closed) * 100, 1) if closed else None,
            "realized_pnl_usd": _round(self.realized_pnl_usd),
            "unrealized_pnl_usd": _round(self.unrealized_pnl_usd),
            "total_pnl_usd": _round(gross),
            "entry_fees_usd": _round(self.entry_fees_usd, 4),
            "cost_basis_open_usd": _round(sum(p.cost_basis_usd or 0 for p in opened)),
            "capital_deployed_usd": _round(capital_deployed),
            "avg_capital_per_trade_usd": (
                _round(capital_deployed / len(filled)) if filled else None
            ),
            "pnl_per_contract_cents": (
                round(self.realized_pnl_usd / contracts_closed * 100, 2)
                if (self.realized_pnl_usd is not None and contracts_closed) else None
            ),
            "last_data_at": _iso(self.last_data_at),
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# LIVE leg
# ---------------------------------------------------------------------------


def live_leg(session, live_tag: str, since: datetime, marks) -> Leg:
    """The real-money side, scoped to the epoch.

    Attribution chain (there is no strategy column on fills or positions, so this
    is the only sound path): `live_orders.strategy` gives the tickers this
    strategy touched -> `fills` join on `kalshi_order_id` gives the contracts and
    real cost basis -> the newest `positions` snapshot per ticker gives the
    exchange's own realized P&L and current size.
    """
    leg = Leg(environment=LIVE, tag=live_tag)
    orders = list(session.scalars(
        select(m.LiveOrder).where(
            m.LiveOrder.strategy == live_tag,
            m.LiveOrder.action == "buy",
            m.LiveOrder.created_at >= since,
        ).order_by(m.LiveOrder.created_at)
    ))
    if not orders:
        leg.notes.append("no live orders in this epoch")
        leg.realized_pnl_usd = 0.0
        leg.unrealized_pnl_usd = 0.0
        leg.entry_fees_usd = 0.0
        return leg

    order_ids = {o.kalshi_order_id for o in orders if o.kalshi_order_id}
    fills_by_ticker: dict[str, list[m.Fill]] = {}
    if order_ids:
        for fill in session.scalars(
            select(m.Fill).where(m.Fill.kalshi_order_id.in_(list(order_ids)))
            .order_by(m.Fill.id)
        ):
            fills_by_ticker.setdefault(fill.market_ticker, []).append(fill)

    tickers = sorted({o.market_ticker for o in orders})
    snapshots = _latest_position_snapshots(session, tickers)

    realized_total = 0.0
    unrealized_total = 0.0
    fees_total = 0.0
    unrealized_measurable = True

    for ticker in tickers:
        ticker_orders = [o for o in orders if o.market_ticker == ticker]
        fills = fills_by_ticker.get(ticker, [])
        pos = LegPosition(ticker=ticker, environment=LIVE, side="no",
                          order_count=len(ticker_orders), fill_count=len(fills))
        if not fills:
            # An order that never filled is NOT a position. This is precisely the
            # case the paper twin cannot produce, so it is labelled, not dropped.
            pos.status = "unfilled"
            pos.entry_at = _aware(ticker_orders[0].created_at)
            leg.positions.append(pos)
            continue

        qty = sum(f.quantity or 0 for f in fills)
        notional = sum((f.price or 0) * (f.quantity or 0) for f in fills)
        pos.quantity = float(qty)
        pos.entry_price_cents = round(notional / qty, 2) if qty else None
        pos.cost_basis_usd = notional / 100.0 if qty else None
        pos.entry_fees_usd = sum(_f0(f.fee) for f in fills)
        pos.side = fills[0].side or "no"
        fill_times = [_aware(f.filled_at) for f in fills if f.filled_at]
        pos.entry_at = min(fill_times) if fill_times else _aware(ticker_orders[0].created_at)
        fees_total += pos.entry_fees_usd

        snap = snapshots.get(ticker)
        if snap is None:
            pos.status = "open"
            leg.notes.append(f"{ticker}: filled but no position snapshot yet")
            unrealized_measurable = False
        elif (snap.quantity or 0) == 0 and snap.realized_pnl is not None:
            pos.status = "settled"
            pos.realized_pnl_usd = _f(snap.realized_pnl)
            pos.exit_at = _aware(snap.captured_at)
            realized_total += pos.realized_pnl_usd or 0.0
        else:
            pos.status = "open"
            live_qty = abs(_f0(snap.quantity_fp) or _f0(snap.quantity))
            if live_qty:
                pos.quantity = live_qty
            if snap.avg_price is not None:
                pos.entry_price_cents = _f(snap.avg_price)
            if snap.market_exposure is not None:
                pos.cost_basis_usd = abs(_f0(snap.market_exposure))
            mark = marks.mark_for(ticker)
            if mark is None or pos.entry_price_cents is None:
                unrealized_measurable = False
            else:
                pos.mark_cents, pos.mark_at = mark.price_cents, mark.at
                pos.unrealized_pnl_usd = (
                    (pos.quantity or 0) * (mark.price_cents - pos.entry_price_cents) / 100.0
                )
                unrealized_total += pos.unrealized_pnl_usd
            pos.stored_unrealized_pnl_usd = _f(snap.unrealized_pnl)
        leg.positions.append(pos)

    leg.realized_pnl_usd = realized_total
    leg.unrealized_pnl_usd = unrealized_total if unrealized_measurable else None
    leg.entry_fees_usd = fees_total
    if not unrealized_measurable:
        leg.notes.append("unrealized P&L incomplete: an open position has no usable mark")
    last_snap = max((s.captured_at for s in snapshots.values() if s.captured_at), default=None)
    leg.last_data_at = _aware(last_snap)
    return leg


def _latest_position_snapshots(session, tickers: list[str]) -> dict[str, m.Position]:
    """Newest `positions` row per ticker. Snapshots accumulate every reconcile cycle,
    so only the last one describes the position now.

    The newest-per-ticker cut is made in SQL rather than by reading every snapshot and
    dropping all but the first. `positions` grows by one row per held ticker per
    reconcile cycle, so a book that has run for weeks has thousands of rows per ticker,
    and reading them all to keep one was a large share of what made this page slow.
    Ties on `captured_at` are still broken by the highest id, as before.
    """
    if not tickers:
        return {}
    newest = (
        select(m.Position.market_ticker.label("ticker"),
               func.max(m.Position.captured_at).label("captured_at"))
        .where(m.Position.market_ticker.in_(tickers))
        .group_by(m.Position.market_ticker)
        .subquery()
    )
    out: dict[str, m.Position] = {}
    for row in session.scalars(
        select(m.Position).join(
            newest,
            and_(m.Position.market_ticker == newest.c.ticker,
                 m.Position.captured_at == newest.c.captured_at),
        ).order_by(m.Position.id.desc())
    ):
        out.setdefault(row.market_ticker, row)
    return out


# ---------------------------------------------------------------------------
# PAPER leg
# ---------------------------------------------------------------------------


def paper_leg(session, tag: str, since: datetime | None, marks) -> Leg:
    """The paper side. `since=None` reads the whole book (used to show what a naive
    all-time paper-vs-live comparison would have claimed); the twin always passes
    the epoch start so both legs cover the same window."""
    leg = Leg(environment=PAPER, tag=tag)
    query = select(m.PaperTrade).where(
        m.PaperTrade.strategy == tag, m.PaperTrade.legacy.is_(False)
    )
    if since is not None:
        query = query.where(m.PaperTrade.created_at >= since)
    trades = list(session.scalars(query.order_by(m.PaperTrade.created_at)))
    if not trades:
        leg.notes.append("no paper trades in this epoch")
        leg.realized_pnl_usd = 0.0
        leg.unrealized_pnl_usd = 0.0
        leg.entry_fees_usd = 0.0
        return leg

    stored = _paper_positions(session, tag)
    realized_total = 0.0
    unrealized_total = 0.0
    fees_total = 0.0
    unrealized_measurable = True

    for trade in trades:
        pos = LegPosition(
            ticker=trade.market_ticker, environment=PAPER, side=trade.side,
            quantity=float(trade.quantity or 0),
            entry_price_cents=_f(trade.assumed_price),
            entry_at=_aware(trade.created_at),
            entry_fees_usd=_f0(trade.fees),
            order_count=1, fill_count=1,  # paper assumes its resting order fills
        )
        if trade.assumed_price is not None and trade.quantity:
            pos.cost_basis_usd = trade.quantity * trade.assumed_price / 100.0
        fees_total += pos.entry_fees_usd or 0.0

        status = (trade.status or PAPER_OPEN).lower()
        if status in PAPER_TERMINAL:
            pos.status = "settled" if status == "settled" else "closed"
            pos.realized_pnl_usd = _f(trade.pnl)
            pos.resolved_value_cents = trade.resolved_value
            pos.exit_at = _aware(trade.closed_at)
            realized_total += pos.realized_pnl_usd or 0.0
        else:
            pos.status = "open"
            mark = marks.mark_for(trade.market_ticker)
            if mark is None or pos.entry_price_cents is None:
                unrealized_measurable = False
            else:
                pos.mark_cents, pos.mark_at = mark.price_cents, mark.at
                pos.unrealized_pnl_usd = (
                    (pos.quantity or 0) * (mark.price_cents - pos.entry_price_cents) / 100.0
                )
                unrealized_total += pos.unrealized_pnl_usd
            stored_pos = stored.get(trade.market_ticker)
            if stored_pos is not None:
                pos.stored_unrealized_pnl_usd = _f(stored_pos.unrealized_pnl)
        leg.positions.append(pos)

    leg.realized_pnl_usd = realized_total
    leg.unrealized_pnl_usd = unrealized_total if unrealized_measurable else None
    leg.entry_fees_usd = fees_total
    if not unrealized_measurable:
        leg.notes.append("unrealized P&L incomplete: an open position has no usable mark")
    leg.last_data_at = max(
        (p.mark_at for p in leg.positions if p.mark_at),
        default=max((_aware(t.created_at) for t in trades), default=None),
    )
    return leg


def _paper_positions(session, tag: str) -> dict[str, m.PaperPosition]:
    out: dict[str, m.PaperPosition] = {}
    for row in session.scalars(
        select(m.PaperPosition).where(m.PaperPosition.strategy == tag)
        .order_by(m.PaperPosition.id.desc())
    ):
        out.setdefault(row.market_ticker, row)
    return out


# ---------------------------------------------------------------------------
# Orders (both environments, one shape)
# ---------------------------------------------------------------------------


def live_orders(session, live_tag: str, since: datetime) -> list[dict]:
    """Live orders with their fills folded in. `cancel_reason` is a short internal
    label ("timeout"), never an exchange response body, so it is safe to publish."""
    orders = list(session.scalars(
        select(m.LiveOrder).where(
            m.LiveOrder.strategy == live_tag, m.LiveOrder.created_at >= since
        ).order_by(m.LiveOrder.created_at.desc())
    ))
    if not orders:
        return []
    ids = {o.kalshi_order_id for o in orders if o.kalshi_order_id}
    fills: dict[str, list[m.Fill]] = {}
    if ids:
        for fill in session.scalars(
            select(m.Fill).where(m.Fill.kalshi_order_id.in_(list(ids))).order_by(m.Fill.id)
        ):
            fills.setdefault(fill.kalshi_order_id or "", []).append(fill)

    out = []
    for order in orders:
        own = fills.get(order.kalshi_order_id or "", [])
        filled_qty = sum(f.quantity or 0 for f in own)
        notional = sum((f.price or 0) * (f.quantity or 0) for f in own)
        times = [_aware(f.filled_at) for f in own if f.filled_at]
        out.append({
            "environment": LIVE,
            "order_id": order.client_order_id or (str(order.id)),
            "market": order.market_ticker,
            "side": order.side,
            "action": order.action,
            "order_type": "maker" if order.status in ("resting", "canceled") else "limit",
            "requested_quantity": order.quantity,
            "filled_quantity": filled_qty or None,
            "limit_price_cents": order.limit_price,
            "avg_fill_price_cents": round(notional / filled_qty, 2) if filled_qty else None,
            "submitted_at": _iso(order.created_at),
            "first_fill_at": _iso(min(times)) if times else None,
            "last_fill_at": _iso(max(times)) if times else None,
            "status": order.status,
            "cancel_reason": (order.cancel_reason or "")[:120] or None,
            "fees_usd": round(sum(_f0(f.fee) for f in own), 4) if own else None,
        })
    return out


def paper_orders(session, tag: str, since: datetime) -> list[dict]:
    """Paper "orders". A paper trade IS its own fill — the twin assumes the resting
    order it placed was lifted — which is exactly the assumption under test, so the
    filled quantity is always the requested quantity here by construction."""
    trades = list(session.scalars(
        select(m.PaperTrade).where(
            m.PaperTrade.strategy == tag,
            m.PaperTrade.legacy.is_(False),
            m.PaperTrade.created_at >= since,
        ).order_by(m.PaperTrade.created_at.desc())
    ))
    return [
        {
            "environment": PAPER,
            "order_id": f"paper:{t.id}",
            "market": t.market_ticker,
            "side": t.side,
            "action": t.action,
            "order_type": "assumed maker fill",
            "requested_quantity": t.quantity,
            "filled_quantity": t.quantity,
            "limit_price_cents": t.assumed_price,
            "avg_fill_price_cents": _f(t.assumed_price),
            "submitted_at": _iso(t.created_at),
            "first_fill_at": _iso(t.created_at),
            "last_fill_at": _iso(t.created_at),
            "status": t.status or PAPER_OPEN,
            "cancel_reason": None,
            "fees_usd": _round(_f(t.fees), 4),
            "pnl_usd": _round(_f(t.pnl)),
        }
        for t in trades
    ]
