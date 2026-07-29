"""Per-agent counterfactual paper execution: orders, fills, positions, and the
lifetime + cohort competition ledgers (spec §18, §19, §21).

Every agent trades an independent simulated book against the same market data —
one agent's fill never consumes liquidity from another's (fair comparison); the
fleet's aggregate consumption is reported separately (capacity_analysis).

Realism rules:
- Taker orders never fill at placement: they are evaluated by process_open_orders
  against a FRESH quote on a later cycle (structural latency), walking displayed
  depth level-by-level within the limit price (partial fills allowed).
- Maker (passive) orders fill only on TRADE-THROUGH — the market must price
  strictly through the limit, never merely touch it — and the filled quantity
  takes a configurable adverse-selection haircut.
- Fees: the exact Kalshi formula (ceil(0.07·C·P·(1−P))) on entries and early
  exits; settlement is free. Spread cost + slippage recorded per fill.
- Stale market data fails closed: no fill, a data-health event, order untouched.

Ledger model: the COHORT ledger (`cohort:<id>`) is the competitive book — the
$1,000 normalized capital, and the no-negative-cash / full-collateral constraints
are enforced against it. The LIFETIME ledger mirrors every fill unscaled and
preserves position continuity + cumulative P&L across cohorts; boundary capital
normalization applies only to the cohort book (adjustments are recorded in
evo_position_transfers), so the lifetime cash column is a derived trajectory,
not an independently constrained balance."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from ..paper.engine import kalshi_fee
from .audit import audit
from .config import EvoSettings
from .marketdata import MarketData, Quote
from .models import (
    EvoDataHealthEvent,
    EvoFill,
    EvoOrder,
    EvoPortfolio,
    EvoPortfolioSnapshot,
    EvoPosition,
    EvoPositionTransfer,
)

logger = logging.getLogger(__name__)

LIFETIME = "lifetime"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hours_since(ts: datetime, now: datetime | None = None) -> float:
    now = now or _now()
    aware = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return (now - aware).total_seconds() / 3600.0


def cohort_ledger(cohort_id: int) -> str:
    return f"cohort:{cohort_id}"


# ---------------------------------------------------------------------------
# Portfolio primitives
# ---------------------------------------------------------------------------


def get_portfolio(session, agent_uuid: str, ledger: str) -> EvoPortfolio | None:
    return session.scalar(
        select(EvoPortfolio).where(
            EvoPortfolio.agent_uuid == agent_uuid, EvoPortfolio.ledger == ledger
        )
    )


def ensure_portfolio(
    session, agent_uuid: str, ledger: str, starting_capital: float
) -> EvoPortfolio:
    pf = get_portfolio(session, agent_uuid, ledger)
    if pf is None:
        pf = EvoPortfolio(
            agent_uuid=agent_uuid,
            ledger=ledger,
            cash_usd=starting_capital,
            starting_capital_usd=starting_capital,
            peak_nav_usd=starting_capital,
        )
        session.add(pf)
        session.flush()
    return pf


def open_positions(session, agent_uuid: str, ledger: str) -> list[EvoPosition]:
    return list(
        session.scalars(
            select(EvoPosition).where(
                EvoPosition.agent_uuid == agent_uuid,
                EvoPosition.ledger == ledger,
                EvoPosition.status == "open",
            )
        )
    )


def all_open_positions(session, ledger_prefix: str | None = None) -> list[EvoPosition]:
    q = select(EvoPosition).where(EvoPosition.status == "open")
    if ledger_prefix:
        q = q.where(EvoPosition.ledger.like(f"{ledger_prefix}%"))
    return list(session.scalars(q))


def recent_orders(
    session, agent_uuid: str, *, hours: float = 48.0, limit: int = 8
) -> list[dict]:
    """Compact view of the agent's own recent orders for the heartbeat prompt: every
    still-open/partial order plus anything created in the window. Surfacing this makes
    a stuck ("open", 0 fills) or rejected order a VISIBLE fact the agent can act on and
    cite — instead of silently re-attempting the same mistake or narrating a stale one."""
    since = _now() - timedelta(hours=hours)
    rows = list(
        session.scalars(
            select(EvoOrder)
            .where(
                EvoOrder.agent_uuid == agent_uuid,
                or_(
                    EvoOrder.status.in_(("open", "partial")),
                    EvoOrder.created_at >= since,
                ),
            )
            .order_by(EvoOrder.created_at.desc())
            .limit(limit)
        )
    )
    out: list[dict] = []
    for o in rows:
        d: dict = {
            "order_id": o.id,
            "ticker": o.market_ticker,
            "side": o.side,
            "action": o.action,
            "qty": o.quantity,
            "filled": o.filled_quantity,
            "status": o.status,
            "age_h": round(_hours_since(o.created_at), 1),
        }
        if o.reject_reason:
            d["reject_reason"] = o.reject_reason[:180]
        out.append(d)
    return out


def _get_open_position(
    session, agent_uuid: str, ledger: str, ticker: str, side: str
) -> EvoPosition | None:
    return session.scalar(
        select(EvoPosition).where(
            EvoPosition.agent_uuid == agent_uuid,
            EvoPosition.ledger == ledger,
            EvoPosition.market_ticker == ticker,
            EvoPosition.side == side,
            EvoPosition.status == "open",
        )
    )


def _next_open_seq(session, agent_uuid: str, ledger: str, ticker: str, side: str) -> int:
    seqs = list(
        session.scalars(
            select(EvoPosition.open_seq).where(
                EvoPosition.agent_uuid == agent_uuid,
                EvoPosition.ledger == ledger,
                EvoPosition.market_ticker == ticker,
                EvoPosition.side == side,
            )
        )
    )
    return (max(seqs) + 1) if seqs else 1


def positions_value_usd(positions: list[EvoPosition]) -> float:
    total = 0.0
    for p in positions:
        mark = p.last_mark_cents if p.last_mark_cents is not None else float(p.avg_price_cents)
        total += float(p.quantity) * mark / 100.0
    return total


def nav(session, agent_uuid: str, ledger: str) -> float:
    pf = get_portfolio(session, agent_uuid, ledger)
    if pf is None:
        return 0.0
    return float(pf.cash_usd) + positions_value_usd(open_positions(session, agent_uuid, ledger))


def reserved_open_order_cost(session, agent_uuid: str, cohort_id: int) -> float:
    """Cost committed to still-open BUY orders (collateral reservation)."""
    total = 0.0
    for o in session.scalars(
        select(EvoOrder).where(
            EvoOrder.agent_uuid == agent_uuid,
            EvoOrder.cohort_id == cohort_id,
            EvoOrder.status.in_(("open", "partial")),
            EvoOrder.action == "buy",
        )
    ):
        price = o.limit_price_cents or 99
        remaining = o.quantity - o.filled_quantity
        total += remaining * price / 100.0
    return total


# ---------------------------------------------------------------------------
# Order placement (validation + idempotent insert)
# ---------------------------------------------------------------------------


def place_order(
    session,
    settings: EvoSettings,
    *,
    agent_uuid: str,
    cohort_id: int,
    idem_key: str,
    market_ticker: str,
    side: str,
    action: str,
    quantity: int,
    style: str = "taker",
    limit_price_cents: int | None = None,
    expires_at: datetime | None = None,
    heartbeat_id: int | None = None,
    strategy_id: int | None = None,
    opportunity_id: int | None = None,
    intent: dict | None = None,
    max_position_cost_usd: float | None = None,
) -> tuple[EvoOrder | None, str | None]:
    """Validate + insert a paper order. Idempotent on idem_key (a retry returns the
    existing row). Constitution constraints enforced here: sides/actions, positive
    integer size, full collateralization against COHORT cash, sells only against
    held quantity, per-market cost cap."""
    existing = session.scalar(select(EvoOrder).where(EvoOrder.idem_key == idem_key))
    if existing is not None:
        if existing.status == "rejected":
            return None, existing.reject_reason or "rejected"
        return existing, None

    def _reject(reason: str) -> tuple[EvoOrder | None, str | None]:
        row = EvoOrder(
            agent_uuid=agent_uuid, cohort_id=cohort_id, heartbeat_id=heartbeat_id,
            strategy_id=strategy_id, opportunity_id=opportunity_id, idem_key=idem_key,
            market_ticker=market_ticker, side=side, action=action, style=style,
            limit_price_cents=limit_price_cents, quantity=max(0, int(quantity)),
            status="rejected", reject_reason=reason, intent_json=intent,
        )
        session.add(row)
        session.flush()
        return None, reason

    if side not in ("yes", "no") or action not in ("buy", "sell"):
        return _reject(f"invalid side/action {side}/{action}")
    if not isinstance(quantity, int) or quantity <= 0:
        return _reject(f"invalid quantity {quantity!r}")
    if style not in ("taker", "maker"):
        return _reject(f"invalid style {style!r}")
    if style == "maker" and limit_price_cents is None:
        return _reject("maker orders require a limit price")
    if limit_price_cents is not None and not (1 <= limit_price_cents <= 99):
        # 0 is the mistake seen live (6 orders burned in a single heartbeat): the
        # agent means "no limit / trade at market" and writes 0 for it. Never
        # silently reinterpret that as a market order — a 0c bid is unfillable by
        # definition, so coercing it would turn "pay nothing" into "pay anything"
        # and could fill at 99c. Reject, but name the actual fix.
        hint = (
            " — to trade at the current market price OMIT limit_price_cents "
            "entirely (a taker order with no limit is marketable at the touch); "
            "0 is never a valid price"
            if limit_price_cents <= 0 else ""
        )
        return _reject(f"limit price {limit_price_cents} out of range 1..99{hint}")

    ledger = cohort_ledger(cohort_id)
    pf = get_portfolio(session, agent_uuid, ledger)
    if pf is None:
        return _reject("no cohort portfolio")

    if action == "buy":
        price_bound = limit_price_cents or 99
        est_cost = quantity * price_bound / 100.0 + kalshi_fee(price_bound, quantity)
        committed = reserved_open_order_cost(session, agent_uuid, cohort_id)
        if est_cost + committed > float(pf.cash_usd) + 1e-9:
            return _reject(
                f"insufficient collateral: cost {est_cost:.2f} + committed "
                f"{committed:.2f} > cash {float(pf.cash_usd):.2f}"
            )
        pos = _get_open_position(session, agent_uuid, ledger, market_ticker, side)
        pos_cost = (
            float(pos.quantity) * float(pos.avg_price_cents) / 100.0 if pos else 0.0
        )
        cap = max_position_cost_usd if max_position_cost_usd is not None else (
            settings.max_position_fraction * max(nav(session, agent_uuid, ledger), 1.0)
        )
        if pos_cost + quantity * price_bound / 100.0 > cap + 1e-9:
            return _reject(f"position cost cap {cap:.2f} exceeded")
    else:  # sell: only what is held
        pos = _get_open_position(session, agent_uuid, ledger, market_ticker, side)
        held = float(pos.quantity) if pos else 0.0
        if held < quantity:
            # Seen live: agents use sell to try to open a SHORT. There is no such
            # thing here (or on Kalshi) — a sell only closes what you already
            # own. The directional bet they want is a buy on the other side.
            other = "no" if side == "yes" else "yes"
            return _reject(
                f"cannot sell {quantity} {side}: you hold {held}. A sell only CLOSES "
                f"a position you already own — it cannot open a short. To bet "
                f"against {side}, BUY {other} instead."
            )

    row = EvoOrder(
        agent_uuid=agent_uuid,
        cohort_id=cohort_id,
        heartbeat_id=heartbeat_id,
        strategy_id=strategy_id,
        opportunity_id=opportunity_id,
        idem_key=idem_key,
        market_ticker=market_ticker,
        side=side,
        action=action,
        style=style,
        limit_price_cents=limit_price_cents,
        quantity=quantity,
        expires_at=expires_at,
        status="open",
        intent_json=intent,
    )
    session.add(row)
    session.flush()
    return row, None


def cancel_order(
    session, agent_uuid: str, order_id: int, *, reason: str = "canceled by agent"
) -> tuple[EvoOrder | None, str | None]:
    order = session.get(EvoOrder, order_id)
    if order is None:
        return None, f"order {order_id} not found"
    if order.agent_uuid != agent_uuid:
        return None, "cannot cancel another agent's order"
    if order.status not in ("open", "partial"):
        return None, f"order is {order.status}"
    order.status = "canceled"
    order.reject_reason = reason
    session.flush()
    return order, None


# ---------------------------------------------------------------------------
# Fill application (both ledgers)
# ---------------------------------------------------------------------------


def _apply_to_ledger(
    session,
    agent_uuid: str,
    ledger: str,
    *,
    ticker: str,
    side: str,
    action: str,
    price_cents: int,
    quantity: float,
    fee_usd: float,
) -> None:
    pf = get_portfolio(session, agent_uuid, ledger)
    if pf is None:  # lifetime should always exist; guard anyway
        pf = ensure_portfolio(session, agent_uuid, ledger, 0.0)
    pos = _get_open_position(session, agent_uuid, ledger, ticker, side)
    if action == "buy":
        cost = quantity * price_cents / 100.0
        if pos is None:
            pos = EvoPosition(
                agent_uuid=agent_uuid,
                ledger=ledger,
                market_ticker=ticker,
                side=side,
                open_seq=_next_open_seq(session, agent_uuid, ledger, ticker, side),
                quantity=quantity,
                avg_price_cents=price_cents,
                status="open",
                realized_pnl_usd=0,
                fees_usd=0,
            )
            session.add(pos)
        else:
            total = float(pos.quantity) + quantity
            pos.avg_price_cents = (
                float(pos.avg_price_cents) * float(pos.quantity) + price_cents * quantity
            ) / total
            pos.quantity = total
        pos.fees_usd = float(pos.fees_usd) + fee_usd
        pf.cash_usd = float(pf.cash_usd) - cost - fee_usd
        pf.fees_usd = float(pf.fees_usd) + fee_usd
    else:  # sell (close some or all of a held position)
        if pos is None:
            logger.warning("evo paper: sell with no open position", extra={
                "extra_fields": {"agent": agent_uuid, "ledger": ledger, "ticker": ticker}})
            return
        qty = min(quantity, float(pos.quantity))
        proceeds = qty * price_cents / 100.0
        realized = qty * (price_cents - float(pos.avg_price_cents)) / 100.0 - fee_usd
        pos.quantity = float(pos.quantity) - qty
        pos.realized_pnl_usd = float(pos.realized_pnl_usd) + realized
        pos.fees_usd = float(pos.fees_usd) + fee_usd
        if pos.quantity <= 1e-9:
            pos.status = "closed"
            pos.closed_at = _now()
            pos.unrealized_pnl_usd = 0
        pf.cash_usd = float(pf.cash_usd) + proceeds - fee_usd
        pf.realized_pnl_usd = float(pf.realized_pnl_usd) + realized
        pf.fees_usd = float(pf.fees_usd) + fee_usd
    session.flush()


def _record_fill(
    session,
    order: EvoOrder,
    *,
    price_cents: int,
    quantity: int,
    fee_usd: float,
    quote: Quote,
    partial: bool,
    liquidity: str,
    slippage_cents: float | None = None,
) -> EvoFill:
    spread_cost = None
    if quote.mid is not None:
        # cost vs mid, signed from the agent's perspective
        spread_cost = (price_cents - quote.mid) if order.action == "buy" else (
            quote.mid - price_cents
        )
    created = order.created_at if order.created_at.tzinfo else order.created_at.replace(
        tzinfo=timezone.utc
    )
    fill = EvoFill(
        order_id=order.id,
        agent_uuid=order.agent_uuid,
        market_ticker=order.market_ticker,
        side=order.side,
        action=order.action,
        price_cents=price_cents,
        quantity=quantity,
        fee_usd=fee_usd,
        spread_cost_cents=spread_cost,
        slippage_cents=slippage_cents,
        liquidity=liquidity,
        partial=partial,
        snapshot_ref=f"{quote.source}:{quote.captured_at.isoformat()}",
        latency_ms=int(max(0.0, (_now() - created).total_seconds() * 1000)),
    )
    session.add(fill)
    # both ledgers: cohort (competitive) + lifetime (continuity), same qty/price
    for ledger in (cohort_ledger(order.cohort_id) if order.cohort_id else None, LIFETIME):
        if ledger:
            _apply_to_ledger(
                session, order.agent_uuid, ledger,
                ticker=order.market_ticker, side=order.side, action=order.action,
                price_cents=price_cents, quantity=float(quantity), fee_usd=fee_usd,
            )
    order.filled_quantity += quantity
    order.status = "filled" if order.filled_quantity >= order.quantity else "partial"
    session.flush()
    return fill


# ---------------------------------------------------------------------------
# Order evaluation loop
# ---------------------------------------------------------------------------


def _record_stale(session, order: EvoOrder, quote: Quote, max_age: float) -> None:
    session.add(
        EvoDataHealthEvent(
            source_name=f"kalshi:{order.market_ticker}",
            level="warn",
            kind="stale",
            detail_json={"order_id": order.id, "age_seconds": quote.age_seconds(),
                         "max": max_age},
        )
    )
    session.flush()


def _record_no_quote(session, order: EvoOrder) -> None:
    """No quote at all for this ticker — distinct from _record_stale (a real
    market whose data went stale). Most likely an invalid/non-existent ticker
    (e.g. a series prefix like "KXHIGH" used where a specific tradable market
    ticker was required), which would otherwise leave the order open forever
    with zero operator-visible signal."""
    session.add(
        EvoDataHealthEvent(
            source_name=f"kalshi:{order.market_ticker}",
            level="warn",
            kind="no_quote",
            detail_json={"order_id": order.id, "ticker": order.market_ticker},
        )
    )
    session.flush()


def evaluate_order(
    session, settings: EvoSettings, order: EvoOrder, quote: Quote | None, *, fees: bool = True
) -> str:
    """Evaluate one open/partial order against a fresh quote. Returns the resulting
    status string (order row updated in place)."""
    now = _now()
    if quote is None:
        _record_no_quote(session, order)
        # Backstop: an order that never filled and can get NO quote at all for a
        # bounded window is unfillable (most likely an invalid/untradable ticker) —
        # reject it with a reason the agent will see, instead of leaving it "open"
        # forever with zero feedback. Fill-aware so a real, partially-filled market
        # that briefly loses its book is never auto-rejected here.
        if (
            order.filled_quantity == 0
            and _hours_since(order.created_at, now) >= settings.order_no_quote_reject_hours
        ):
            order.status = "rejected"
            order.reject_reason = (
                f"unfillable: no live quote for {order.market_ticker!r} in the "
                f"{_hours_since(order.created_at, now):.0f}h since submission (0 fills) "
                "— likely an invalid or untradable ticker"
            )
            session.flush()
            return order.status
        return order.status  # no data this cycle; leave untouched
    if quote.age_seconds(now) > settings.stale_data_seconds:
        _record_stale(session, order, quote, settings.stale_data_seconds)
        return order.status
    if quote.is_terminal():
        order.status = "expired"
        order.reject_reason = f"market terminal ({quote.result or quote.status})"
        session.flush()
        return order.status
    if order.expires_at is not None:
        exp = order.expires_at if order.expires_at.tzinfo else order.expires_at.replace(
            tzinfo=timezone.utc
        )
        if now >= exp:
            order.status = "expired"
            order.reject_reason = "order expired"
            session.flush()
            return order.status

    remaining = order.quantity - order.filled_quantity
    if remaining <= 0:
        order.status = "filled"
        session.flush()
        return order.status

    if order.style == "taker":
        levels = (
            quote.taker_levels(order.side) if order.action == "buy"
            else quote.exit_levels(order.side)
        )
        best = levels[0][0] if levels else None
        to_fill = remaining
        for price, depth in levels:
            if to_fill <= 0:
                break
            if order.limit_price_cents is not None:
                if order.action == "buy" and price > order.limit_price_cents:
                    break
                if order.action == "sell" and price < order.limit_price_cents:
                    break
            qty = min(to_fill, depth)
            fee = kalshi_fee(price, qty) if fees else 0.0
            slip = float(price - best) if (best is not None and order.action == "buy") else (
                float(best - price) if best is not None else None
            )
            _record_fill(
                session, order, price_cents=price, quantity=qty, fee_usd=fee,
                quote=quote, partial=(qty < order.quantity), liquidity="taker",
                slippage_cents=slip,
            )
            to_fill -= qty
        session.flush()
        return order.status

    # maker: fill ONLY on strict trade-through of the limit (touch never fills),
    # with an adverse-selection quantity haircut (conservative queue model).
    limit = order.limit_price_cents
    if order.action == "buy":
        # resting buy of `side` at limit fills when the taker cost of `side` drops
        # strictly BELOW the limit (someone sold through our level)
        market_price = quote.best_taker_price(order.side)
        through = market_price is not None and market_price < limit
    else:
        # resting sell of held `side` fills when that side's bid moves strictly
        # ABOVE the limit
        bid = quote.best_exit_bid(order.side)
        through = bid is not None and bid > limit
    if not through:
        return order.status
    haircut = max(0.0, min(0.9, settings.maker_adverse_selection_haircut))
    # Floor at 1 once the market has genuinely traded through: int() alone made a
    # 1-contract maker order unfillable BY CONSTRUCTION (int(1 * 0.75) == 0 -> the
    # qty <= 0 early return), so it rested forever no matter how far the market
    # moved past it. Agents place 1-lots routinely, so that silently swallowed a
    # whole class of orders. The haircut still scales every larger order (10 -> 7);
    # this only stops it rounding a real fill down to nothing.
    qty = max(1, int(remaining * (1.0 - haircut)))
    if qty <= 0:  # unreachable while remaining > 0; kept as a guard
        return order.status
    fee = kalshi_fee(limit, qty) if fees else 0.0
    _record_fill(
        session, order, price_cents=limit, quantity=qty, fee_usd=fee, quote=quote,
        partial=(qty < order.quantity), liquidity="maker", slippage_cents=0.0,
    )
    session.flush()
    return order.status


def process_open_orders(session, settings: EvoSettings, md: MarketData) -> dict[str, int]:
    """Evaluate every open/partial order against fresh quotes. One quote fetch per
    ticker per cycle; each agent's fill is computed independently against it."""
    counts = {"evaluated": 0, "filled": 0, "partial": 0, "expired": 0}
    orders = list(
        session.scalars(
            select(EvoOrder).where(EvoOrder.status.in_(("open", "partial")))
            .order_by(EvoOrder.id)
        )
    )
    quotes: dict[str, Quote | None] = {}
    for order in orders:
        if order.market_ticker not in quotes:
            quotes[order.market_ticker] = md.get_quote(order.market_ticker)
        counts["evaluated"] += 1
        status = evaluate_order(session, settings, order, quotes[order.market_ticker])
        if status == "filled":
            counts["filled"] += 1
        elif status == "partial":
            counts["partial"] += 1
        elif status == "expired":
            counts["expired"] += 1
    return counts


# ---------------------------------------------------------------------------
# Marks + settlement
# ---------------------------------------------------------------------------


def mark_and_settle(session, md: MarketData, *, ledger_filter: str | None = None) -> dict[str, int]:
    """Mark open positions at conservative executable value (that side's best bid)
    and settle positions whose market is terminal (0/100, no fee; void returns
    cost). Applies to every ledger so lifetime + cohort stay in sync."""
    counts = {"marked": 0, "settled": 0, "voided": 0}
    positions = all_open_positions(session, ledger_filter)
    quotes: dict[str, Quote | None] = {}
    for pos in positions:
        t = pos.market_ticker
        if t not in quotes:
            quotes[t] = md.get_quote(t)
        quote = quotes[t]
        if quote is None:
            continue
        if quote.is_terminal():
            pf = get_portfolio(session, pos.agent_uuid, pos.ledger)
            if quote.result in ("yes", "no"):
                won = pos.side == quote.result
                value_cents = 100 if won else 0
                proceeds = float(pos.quantity) * value_cents / 100.0
                realized = float(pos.quantity) * (
                    value_cents - float(pos.avg_price_cents)
                ) / 100.0
                pos.status = "settled"
                pos.resolved_value_cents = value_cents
                counts["settled"] += 1
            else:  # void/scratch: returned at cost
                proceeds = float(pos.quantity) * float(pos.avg_price_cents) / 100.0
                realized = 0.0
                pos.status = "settled"
                pos.resolved_value_cents = None
                counts["voided"] += 1
            pos.closed_at = _now()
            pos.unrealized_pnl_usd = 0
            pos.realized_pnl_usd = float(pos.realized_pnl_usd) + realized
            if pf is not None:
                pf.cash_usd = float(pf.cash_usd) + proceeds
                pf.realized_pnl_usd = float(pf.realized_pnl_usd) + realized
            session.flush()
            continue
        bid = quote.best_exit_bid(pos.side)
        if bid is None:
            continue  # one-sided book: keep the last mark
        pos.last_mark_cents = float(bid)
        pos.last_marked_at = _now()
        pos.unrealized_pnl_usd = float(pos.quantity) * (
            bid - float(pos.avg_price_cents)
        ) / 100.0
        counts["marked"] += 1
    # refresh peak NAV for drawdown tracking
    seen: set[tuple[str, str]] = {(p.agent_uuid, p.ledger) for p in positions}
    for agent_uuid, ledger in seen:
        pf = get_portfolio(session, agent_uuid, ledger)
        if pf is not None:
            current = nav(session, agent_uuid, ledger)
            if pf.peak_nav_usd is None or current > float(pf.peak_nav_usd):
                pf.peak_nav_usd = current
    session.flush()
    return counts


def snapshot_portfolio(
    session, agent_uuid: str, ledger: str, label: str, *, detail: dict | None = None
) -> EvoPortfolioSnapshot | None:
    existing = session.scalar(
        select(EvoPortfolioSnapshot).where(
            EvoPortfolioSnapshot.agent_uuid == agent_uuid,
            EvoPortfolioSnapshot.ledger == ledger,
            EvoPortfolioSnapshot.label == label,
        )
    )
    if existing is not None:
        return existing
    pf = get_portfolio(session, agent_uuid, ledger)
    if pf is None:
        return None
    positions = open_positions(session, agent_uuid, ledger)
    snap = EvoPortfolioSnapshot(
        agent_uuid=agent_uuid,
        ledger=ledger,
        label=label,
        cash_usd=float(pf.cash_usd),
        positions_value_usd=positions_value_usd(positions),
        nav_usd=float(pf.cash_usd) + positions_value_usd(positions),
        detail_json=detail,
    )
    session.add(snap)
    session.flush()
    return snap


# ---------------------------------------------------------------------------
# Cohort boundary: carryover, normalization, liquidation (spec §21)
# ---------------------------------------------------------------------------


def _conservative_mark(pos: EvoPosition, quote: Quote | None) -> float:
    """Executable value in cents: current bid if available, else last mark, else 0
    (maximally conservative)."""
    if quote is not None:
        bid = quote.best_exit_bid(pos.side)
        if bid is not None:
            return float(bid)
    if pos.last_mark_cents is not None:
        return float(pos.last_mark_cents)
    return 0.0


def carry_over_positions(
    session,
    settings: EvoSettings,
    *,
    agent_uuid: str,
    old_cohort_id: int,
    new_cohort_id: int,
    md: MarketData,
) -> dict:
    """Transfer a survivor's open cohort positions into the next cohort ledger at
    conservative marked value, scaling proportionally if the marked value exceeds
    the $1,000 baseline, and set new-cohort cash so opening NAV == exactly $1,000.
    The lifetime ledger is untouched (continuity). Idempotent per position via
    evo_position_transfers uniqueness."""
    old_ledger, new_ledger = cohort_ledger(old_cohort_id), cohort_ledger(new_cohort_id)
    positions = open_positions(session, agent_uuid, old_ledger)
    marked: list[tuple[EvoPosition, float]] = []
    total_value = 0.0
    for pos in positions:
        mark = _conservative_mark(pos, md.get_quote(pos.market_ticker))
        value = float(pos.quantity) * mark / 100.0
        marked.append((pos, mark))
        total_value += value
    base = settings.starting_capital_usd
    scale = 1.0 if total_value <= base or total_value == 0 else base / total_value
    new_pf = ensure_portfolio(session, agent_uuid, new_ledger, base)
    carried_value = 0.0
    for pos, mark in marked:
        existing = session.scalar(
            select(EvoPositionTransfer).where(
                EvoPositionTransfer.cohort_id == new_cohort_id,
                EvoPositionTransfer.agent_uuid == agent_uuid,
                EvoPositionTransfer.position_id == pos.id,
            )
        )
        if existing is not None:
            carried_value += float(existing.marked_value_usd) * float(existing.scale_factor)
            continue
        qty = float(pos.quantity) * scale
        value = qty * mark / 100.0
        carried_value += value
        session.add(
            EvoPositionTransfer(
                cohort_id=new_cohort_id,
                agent_uuid=agent_uuid,
                position_id=pos.id,
                kind="carryover",
                marked_price_cents=mark,
                marked_value_usd=float(pos.quantity) * mark / 100.0,
                scale_factor=scale,
                detail_json={"from": old_ledger, "to": new_ledger},
            )
        )
        # old-cohort position closes as transferred (scored at the boundary mark)
        boundary_realized = float(pos.quantity) * (mark - float(pos.avg_price_cents)) / 100.0
        pos.status = "transferred"
        pos.closed_at = _now()
        pos.unrealized_pnl_usd = 0
        pos.realized_pnl_usd = float(pos.realized_pnl_usd) + boundary_realized
        old_pf = get_portfolio(session, agent_uuid, old_ledger)
        if old_pf is not None:
            old_pf.cash_usd = float(old_pf.cash_usd) + float(pos.quantity) * mark / 100.0
            old_pf.realized_pnl_usd = float(old_pf.realized_pnl_usd) + boundary_realized
        # new-cohort position opens at the boundary mark as its cost basis (spec:
        # the mark IS the next cohort's opening basis; prior unrealized never counts
        # as new-cohort profit)
        if qty > 1e-9 and mark > 0:
            session.add(
                EvoPosition(
                    agent_uuid=agent_uuid,
                    ledger=new_ledger,
                    market_ticker=pos.market_ticker,
                    side=pos.side,
                    open_seq=_next_open_seq(
                        session, agent_uuid, new_ledger, pos.market_ticker, pos.side
                    ),
                    quantity=qty,
                    avg_price_cents=mark,
                    status="open",
                    last_mark_cents=mark,
                    last_marked_at=_now(),
                    unrealized_pnl_usd=0,
                    realized_pnl_usd=0,
                    fees_usd=0,
                )
            )
    new_pf.cash_usd = base - carried_value  # opening NAV == exactly base
    session.flush()
    audit(
        session, "cohort_carryover", agent_uuid=agent_uuid,
        positions=len(marked), total_marked=round(total_value, 4),
        scale=round(scale, 6), opening_cash=round(float(new_pf.cash_usd), 4),
    )
    return {"positions": len(marked), "scale": scale, "carried_value": carried_value}


def liquidate_agent(
    session,
    *,
    agent_uuid: str,
    cohort_id: int,
    md: MarketData,
) -> float:
    """Conservatively liquidate a retired agent's open positions (cohort AND
    lifetime ledgers) at executable value. Returns total liquidation value (cohort
    ledger). Idempotent via evo_position_transfers uniqueness."""
    total = 0.0
    for ledger in (cohort_ledger(cohort_id), LIFETIME):
        for pos in open_positions(session, agent_uuid, ledger):
            existing = session.scalar(
                select(EvoPositionTransfer).where(
                    EvoPositionTransfer.cohort_id == cohort_id,
                    EvoPositionTransfer.agent_uuid == agent_uuid,
                    EvoPositionTransfer.position_id == pos.id,
                )
            )
            if existing is not None:
                continue
            mark = _conservative_mark(pos, md.get_quote(pos.market_ticker))
            value = float(pos.quantity) * mark / 100.0
            realized = float(pos.quantity) * (mark - float(pos.avg_price_cents)) / 100.0
            session.add(
                EvoPositionTransfer(
                    cohort_id=cohort_id,
                    agent_uuid=agent_uuid,
                    position_id=pos.id,
                    kind="liquidation",
                    marked_price_cents=mark,
                    marked_value_usd=value,
                    scale_factor=1.0,
                    detail_json={"ledger": pos.ledger},
                )
            )
            pos.status = "liquidated"
            pos.closed_at = _now()
            pos.unrealized_pnl_usd = 0
            pos.realized_pnl_usd = float(pos.realized_pnl_usd) + realized
            pf = get_portfolio(session, agent_uuid, pos.ledger)
            if pf is not None:
                pf.cash_usd = float(pf.cash_usd) + value
                pf.realized_pnl_usd = float(pf.realized_pnl_usd) + realized
            if ledger != LIFETIME:
                total += value
    session.flush()
    return total


def expire_cohort_orders(session, cohort_id: int) -> int:
    """Cancel all open orders at the cohort boundary (no new scored intents)."""
    n = 0
    for order in session.scalars(
        select(EvoOrder).where(
            EvoOrder.cohort_id == cohort_id, EvoOrder.status.in_(("open", "partial"))
        )
    ):
        order.status = "expired"
        order.reject_reason = "cohort boundary"
        n += 1
    session.flush()
    return n


# ---------------------------------------------------------------------------
# Aggregate capacity analysis (spec §19)
# ---------------------------------------------------------------------------


def capacity_analysis(session, cohort_id: int, md: MarketData) -> dict:
    """How much displayed liquidity would the fleet collectively have consumed?
    Reported per market: total filled quantity vs currently displayed depth.
    A report for fitness context + deployment realism, never a fill constraint."""
    from sqlalchemy import func

    rows = session.execute(
        select(
            EvoFill.market_ticker,
            func.count(EvoFill.id),
            func.sum(EvoFill.quantity),
            func.count(func.distinct(EvoFill.agent_uuid)),
        )
        .join(EvoOrder, EvoOrder.id == EvoFill.order_id)
        .where(EvoOrder.cohort_id == cohort_id)
        .group_by(EvoFill.market_ticker)
    ).all()
    out = []
    for ticker, fills, qty, agents in rows:
        quote = md.get_quote(ticker)
        depth = None
        if quote is not None:
            depth = sum(q for _, q in quote.yes_levels) + sum(q for _, q in quote.no_levels)
        out.append(
            {
                "ticker": ticker,
                "fills": int(fills),
                "quantity": int(qty or 0),
                "agents": int(agents),
                "displayed_depth": depth,
                "consumption_ratio": (
                    round(float(qty) / depth, 3) if depth else None
                ),
            }
        )
    out.sort(key=lambda r: -(r["quantity"] or 0))
    crowded = [r for r in out if (r["consumption_ratio"] or 0) > 1.0]
    return {"markets": out[:100], "crowded": crowded[:25], "total_markets": len(out)}
