"""Backend aggregation for the evolution fleet dashboard.

Every financial number the dashboard shows is computed here, once, so the fleet
cards, the leaderboard and the per-bot detail can never disagree with each other
or with the underlying trade rows. The frontend formats; it does not calculate.

Accounting model (derived from evo/paper.py — read that file before changing any
of this):

* A **trade** is one `evo_positions` row: a position in one (market, side) on one
  ledger. `ledger` is either `cohort:<cohort_id>` (the competitive book that
  fitness and selection score) or `lifetime` (continuity across cohorts). The two
  hold the SAME fills, so they must never be summed together — every call here
  takes exactly one ledger.

* `position.realized_pnl_usd` is written by the paper engine and is net of the
  fees paid on the *exit* but not of the fees paid on the *entry* (entry fees come
  straight out of portfolio cash). Summing it over a bot's closed positions
  therefore reproduces `portfolio.realized_pnl_usd` exactly, and the residual
  against NAV is the entry-fee drag:

      NAV − starting_capital  ==  realized + unrealized − entry_fee_drag

  We compute that drag as an exact residual and show it, rather than papering over
  the gap. `total_pnl` is always the NAV-based bottom line.

* Missing data is never silently zero. A bot with no closed trades has
  `win_rate is None`, not 0.0; an open position that has never been marked is
  counted in `unmarked_open` and flips `unrealized_complete` to False.

Everything here is a SELECT. Nothing is added, updated or committed.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone

from sqlalchemy import func, select

from ..evo import paper
from ..evo.models import (
    EvoAgent,
    EvoCohort,
    EvoCohortMember,
    EvoFill,
    EvoHeartbeat,
    EvoOrder,
    EvoPortfolio,
    EvoPortfolioSnapshot,
    EvoPosition,
    EvoPositionTransfer,
    EvoStrategy,
)

# Position statuses that represent a finished trade. All four book realized P&L:
# `closed` (sold out), `settled` (market resolved), `liquidated` (retirement) and
# `transferred` (carried across a cohort boundary at the boundary mark).
CLOSED_STATUSES = ("closed", "settled", "liquidated", "transferred")
OPEN_STATUS = "open"

# Human-readable exit reason implied by the terminal status, used when no explicit
# exit order intent exists.
_EXIT_REASON_BY_STATUS = {
    "settled": "settled at expiry",
    "closed": "closed by the bot",
    "liquidated": "liquidated on retirement",
    "transferred": "carried into the next generation",
}
_ORDER_EXIT_REASONS = {
    "tp": "take-profit",
    "sl": "stop-loss",
    "timed": "max holding time",
}


def _f(value) -> float:
    """Numeric columns come back as Decimal on Postgres and float on SQLite."""
    return 0.0 if value is None else float(value)


def _r(value, places: int = 2) -> float:
    """Round to a float — never an int, so a JSON consumer sees one money type."""
    return round(float(value or 0.0), places)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hours_between(start: datetime | None, end: datetime | None) -> float | None:
    a, b = _aware(start), _aware(end)
    if a is None or b is None:
        return None
    return max(0.0, (b - a).total_seconds() / 3600.0)


def _mark_cents(pos: EvoPosition) -> float:
    """Value used to price an open position — the last executable mark, falling back
    to cost basis. Mirrors paper.positions_value_usd exactly so NAV agrees."""
    return _f(pos.last_mark_cents) if pos.last_mark_cents is not None else _f(pos.avg_price_cents)


def cohort_ledger(cohort) -> str | None:
    return paper.cohort_ledger(cohort.id) if cohort is not None else None


# ---------------------------------------------------------------------------
# Per-bot performance
# ---------------------------------------------------------------------------


def _empty_performance(starting_capital: float) -> dict:
    return {
        "trades_total": 0,
        "trades_closed": 0,
        "trades_open": 0,
        "wins": 0,
        "losses": 0,
        "breakeven": 0,
        "win_rate": None,
        "realized_pnl_usd": 0.0,
        "unrealized_pnl_usd": 0.0,
        "unrealized_complete": True,
        "unmarked_open": 0,
        "total_pnl_usd": 0.0,
        "fee_drag_usd": 0.0,
        "fees_usd": 0.0,
        "avg_trade_usd": None,
        "median_trade_usd": None,
        "best_trade": None,
        "worst_trade": None,
        "avg_hold_hours": None,
        "exposure_usd": 0.0,
        "equity_usd": starting_capital,
        "starting_capital_usd": starting_capital,
        "return_pct": 0.0,
        "last_trade_at": None,
    }


def _trade_ref(pos: EvoPosition) -> dict:
    """Minimal identifying handle for a trade, so the UI can jump from "best trade"
    straight to the row in the trade table."""
    return {
        "position_id": pos.id,
        "market": pos.market_ticker,
        "side": pos.side,
        "realized_pnl_usd": _r(_f(pos.realized_pnl_usd)),
        "closed_at": _aware(pos.closed_at).isoformat() if pos.closed_at else None,
    }


def _performance_from_positions(
    positions: list[EvoPosition], *, cash_usd: float, starting_capital: float
) -> dict:
    perf = _empty_performance(starting_capital)
    closed = [p for p in positions if p.status in CLOSED_STATUSES]
    open_pos = [p for p in positions if p.status == OPEN_STATUS]

    realized_values = [_f(p.realized_pnl_usd) for p in closed]
    realized = sum(realized_values)
    unrealized = sum(_f(p.unrealized_pnl_usd) for p in open_pos
                     if p.unrealized_pnl_usd is not None)
    unmarked = sum(1 for p in open_pos if p.unrealized_pnl_usd is None)
    exposure = sum(_f(p.quantity) * _mark_cents(p) / 100.0 for p in open_pos)
    equity = cash_usd + exposure
    total_pnl = equity - starting_capital

    perf.update({
        "trades_total": len(positions),
        "trades_closed": len(closed),
        "trades_open": len(open_pos),
        "wins": sum(1 for v in realized_values if v > 0),
        "losses": sum(1 for v in realized_values if v < 0),
        "breakeven": sum(1 for v in realized_values if v == 0),
        "realized_pnl_usd": _r(realized),
        "unrealized_pnl_usd": _r(unrealized),
        "unrealized_complete": unmarked == 0,
        "unmarked_open": unmarked,
        "fees_usd": _r(sum(_f(p.fees_usd) for p in positions)),
        "exposure_usd": _r(exposure),
        "equity_usd": _r(equity),
        "starting_capital_usd": _r(starting_capital),
        "total_pnl_usd": _r(total_pnl),
        # exact residual: what NAV lost to fees paid on entries (see module docstring)
        "fee_drag_usd": _r(realized + unrealized - total_pnl),
        "return_pct": _r(total_pnl / starting_capital * 100) if starting_capital else 0.0,
    })
    if closed:
        holds = [h for h in (_hours_between(p.opened_at, p.closed_at) for p in closed)
                 if h is not None]
        best = max(closed, key=lambda p: _f(p.realized_pnl_usd))
        worst = min(closed, key=lambda p: _f(p.realized_pnl_usd))
        closed_at = [_aware(p.closed_at) for p in closed if p.closed_at]
        perf.update({
            # win rate deliberately excludes open trades: an unsettled position has
            # no outcome yet, and counting it either way would misstate the record.
            "win_rate": round(perf["wins"] / len(closed) * 100, 1),
            "avg_trade_usd": _r(realized / len(closed)),
            "median_trade_usd": _r(statistics.median(realized_values)),
            "best_trade": _trade_ref(best),
            "worst_trade": _trade_ref(worst),
            "avg_hold_hours": round(sum(holds) / len(holds), 1) if holds else None,
            "last_trade_at": max(closed_at).isoformat() if closed_at else None,
        })
    return perf


def bot_performance(
    session, agent_uuids: list[str], ledger: str, *, default_capital: float
) -> dict[str, dict]:
    """Performance for many bots at once — two queries total, not two per bot."""
    if not agent_uuids:
        return {}
    positions_by_agent: dict[str, list[EvoPosition]] = {u: [] for u in agent_uuids}
    for pos in session.scalars(
        select(EvoPosition).where(
            EvoPosition.ledger == ledger,
            EvoPosition.agent_uuid.in_(agent_uuids),
        )
    ):
        positions_by_agent.setdefault(pos.agent_uuid, []).append(pos)

    portfolios = {
        pf.agent_uuid: pf
        for pf in session.scalars(
            select(EvoPortfolio).where(
                EvoPortfolio.ledger == ledger,
                EvoPortfolio.agent_uuid.in_(agent_uuids),
            )
        )
    }
    out: dict[str, dict] = {}
    for uuid in agent_uuids:
        pf = portfolios.get(uuid)
        # No portfolio row means the bot never joined this ledger — report the
        # starting baseline rather than inventing a $0 equity.
        starting = _f(pf.starting_capital_usd) if pf is not None else default_capital
        cash = _f(pf.cash_usd) if pf is not None else default_capital
        out[uuid] = _performance_from_positions(
            positions_by_agent.get(uuid, []),
            cash_usd=cash,
            starting_capital=starting or default_capital,
        )
    return out


# ---------------------------------------------------------------------------
# Fleet rollup
# ---------------------------------------------------------------------------


def fleet_summary(perf_by_uuid: dict[str, dict], *, names: dict[str, str]) -> dict:
    """Cohort-level totals. Sums of per-bot values that were themselves summed from
    trade rows, so the fleet card, the leaderboard column and the bot detail always
    reconcile."""
    rows = list(perf_by_uuid.values())
    if not rows:
        return {
            "bots": 0, "trades_total": 0, "trades_closed": 0, "trades_open": 0,
            "realized_pnl_usd": 0.0, "unrealized_pnl_usd": 0.0, "total_pnl_usd": 0.0,
            "fees_usd": 0.0, "fee_drag_usd": 0.0, "equity_usd": 0.0,
            "starting_capital_usd": 0.0, "win_rate": None, "wins": 0, "losses": 0,
            "breakeven": 0, "profitable_bots": 0, "unprofitable_bots": 0,
            "flat_bots": 0, "unrealized_complete": True, "best_bot": None,
            "worst_bot": None, "bots_with_closed_trades": 0,
        }
    wins = sum(r["wins"] for r in rows)
    losses = sum(r["losses"] for r in rows)
    breakeven = sum(r["breakeven"] for r in rows)
    closed = sum(r["trades_closed"] for r in rows)
    best_uuid = max(perf_by_uuid, key=lambda u: perf_by_uuid[u]["total_pnl_usd"])
    worst_uuid = min(perf_by_uuid, key=lambda u: perf_by_uuid[u]["total_pnl_usd"])

    def _bot_ref(uuid: str) -> dict:
        return {
            "uuid": uuid,
            "name": names.get(uuid, uuid[:8]),
            "total_pnl_usd": perf_by_uuid[uuid]["total_pnl_usd"],
        }

    return {
        "bots": len(rows),
        "trades_total": sum(r["trades_total"] for r in rows),
        "trades_closed": closed,
        "trades_open": sum(r["trades_open"] for r in rows),
        "realized_pnl_usd": _r(sum(r["realized_pnl_usd"] for r in rows)),
        "unrealized_pnl_usd": _r(sum(r["unrealized_pnl_usd"] for r in rows)),
        "total_pnl_usd": _r(sum(r["total_pnl_usd"] for r in rows)),
        "fees_usd": _r(sum(r["fees_usd"] for r in rows)),
        "fee_drag_usd": _r(sum(r["fee_drag_usd"] for r in rows)),
        "equity_usd": _r(sum(r["equity_usd"] for r in rows)),
        "starting_capital_usd": _r(sum(r["starting_capital_usd"] for r in rows)),
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        # None (not 0%) until at least one trade has actually finished.
        "win_rate": round(wins / closed * 100, 1) if closed else None,
        "bots_with_closed_trades": sum(1 for r in rows if r["trades_closed"]),
        "profitable_bots": sum(1 for r in rows if r["total_pnl_usd"] > 0),
        "unprofitable_bots": sum(1 for r in rows if r["total_pnl_usd"] < 0),
        "flat_bots": sum(1 for r in rows if r["total_pnl_usd"] == 0),
        "unrealized_complete": all(r["unrealized_complete"] for r in rows),
        "best_bot": _bot_ref(best_uuid),
        "worst_bot": _bot_ref(worst_uuid),
    }


# ---------------------------------------------------------------------------
# Generation (cohort) history
# ---------------------------------------------------------------------------


def generation_history(session, *, limit: int = 12) -> list[dict]:
    """Per-generation comparison built from what finalization already persists:
    `evo_cohort_members` (final rank/group per bot) and the `boundary:<n>` portfolio
    snapshots taken at each cohort close. Cohorts that have not finalized yet
    contribute a row with live counts and null P&L rather than being dropped."""
    cohorts = list(
        session.scalars(select(EvoCohort).order_by(EvoCohort.number.desc()).limit(limit))
    )
    if not cohorts:
        return []
    by_id = {c.id: c for c in cohorts}
    members_by_cohort: dict[int, list[EvoCohortMember]] = {c.id: [] for c in cohorts}
    for m in session.scalars(
        select(EvoCohortMember).where(EvoCohortMember.cohort_id.in_(list(by_id)))
    ):
        members_by_cohort.setdefault(m.cohort_id, []).append(m)

    # boundary snapshots are labelled by cohort NUMBER, on the cohort ledger
    labels = {f"boundary:{c.number}": c.id for c in cohorts}
    navs_by_cohort: dict[int, list[float]] = {c.id: [] for c in cohorts}
    if labels:
        for snap in session.scalars(
            select(EvoPortfolioSnapshot).where(
                EvoPortfolioSnapshot.label.in_(list(labels)),
                EvoPortfolioSnapshot.ledger.notin_([paper.LIFETIME]),
            )
        ):
            cohort_id = labels.get(snap.label)
            if cohort_id is not None and snap.ledger == paper.cohort_ledger(cohort_id):
                navs_by_cohort[cohort_id].append(_f(snap.nav_usd))

    trades_by_cohort = {
        c.id: int(
            session.scalar(
                select(func.count(EvoPosition.id)).where(
                    EvoPosition.ledger == paper.cohort_ledger(c.id),
                    EvoPosition.status.in_(CLOSED_STATUSES),
                )
            ) or 0
        )
        for c in cohorts
    }

    out: list[dict] = []
    for c in cohorts:
        members = members_by_cohort.get(c.id, [])
        navs = sorted(navs_by_cohort.get(c.id, []))
        starting = [_f(m.starting_capital) for m in members]
        base = sum(starting)
        row = {
            "cohort_id": c.id,
            "number": c.number,
            "status": c.status,
            "starts_at": _aware(c.starts_at).isoformat() if c.starts_at else None,
            "ends_at": _aware(c.ends_at).isoformat() if c.ends_at else None,
            "wildcard": bool(c.wildcard_cohort),
            "bots": len(members),
            "closed_trades": trades_by_cohort.get(c.id, 0),
            "promoted": sum(1 for m in members if m.final_group == "top"),
            "survived": sum(1 for m in members if m.final_group in ("top", "middle")),
            "eliminated": sum(1 for m in members if m.final_group == "bottom"),
            "finalized": any(m.final_group for m in members),
            # P&L series only exist once the boundary snapshots were taken.
            "fleet_pnl_usd": None,
            "median_bot_pnl_usd": None,
            "best_bot_pnl_usd": None,
            "worst_bot_pnl_usd": None,
            "snapshot_bots": len(navs),
        }
        if navs and base:
            per_bot_start = base / len(members) if members else 0.0
            row.update({
                "fleet_pnl_usd": _r(sum(navs) - per_bot_start * len(navs)),
                "median_bot_pnl_usd": _r(statistics.median(navs) - per_bot_start),
                "best_bot_pnl_usd": _r(navs[-1] - per_bot_start),
                "worst_bot_pnl_usd": _r(navs[0] - per_bot_start),
            })
        out.append(row)
    return list(reversed(out))


# ---------------------------------------------------------------------------
# Per-bot trade table
# ---------------------------------------------------------------------------


def _order_attribution(session, agent_uuid: str, tickers: set[str]) -> dict[tuple, list[EvoOrder]]:
    """Orders for this bot in the markets it traded, grouped by (ticker, side, action)
    and time-ordered. Positions carry no order FK, so entry strategy and exit reason
    are attributed by market/side/time — labelled "attributed" in the payload because
    it is a derivation, not a stored relationship."""
    if not tickers:
        return {}
    grouped: dict[tuple, list[EvoOrder]] = {}
    for order in session.scalars(
        select(EvoOrder)
        .where(EvoOrder.agent_uuid == agent_uuid, EvoOrder.market_ticker.in_(list(tickers)))
        .order_by(EvoOrder.created_at)
    ):
        grouped.setdefault((order.market_ticker, order.side, order.action), []).append(order)
    return grouped


def _pick_order(orders: list[EvoOrder], *, until: datetime | None):
    """The order most plausibly responsible for an event: the latest one created no
    later than `until`. Orders are always created before the fill they produce."""
    if not orders:
        return None
    bound = _aware(until)
    if bound is None:
        return orders[-1]
    eligible = [o for o in orders if (_aware(o.created_at) or bound) <= bound]
    return eligible[-1] if eligible else None


def _exit_prices(session, agent_uuid: str, positions: list[EvoPosition]) -> dict[int, float]:
    """Exit price in cents per closed position.

    * `settled` — `resolved_value_cents` is on the row (100/0, or None when voided).
    * `liquidated` / `transferred` — the boundary mark is recorded exactly in
      `evo_position_transfers.marked_price_cents`.
    * `closed` — the bot sold out, so the price is the quantity-weighted average of
      its sell fills inside the position's life. Only one position per
      (agent, ledger, market, side) is ever open at a time (paper.py opens a new
      `open_seq` only after the previous one closed), so that window is unambiguous.
    """
    out: dict[int, float] = {}
    transfer_ids = [p.id for p in positions if p.status in ("liquidated", "transferred")]
    if transfer_ids:
        for tr in session.scalars(
            select(EvoPositionTransfer).where(EvoPositionTransfer.position_id.in_(transfer_ids))
        ):
            out.setdefault(tr.position_id, _f(tr.marked_price_cents))

    sold = [p for p in positions if p.status == "closed"]
    if not sold:
        return out
    fills_by_key: dict[tuple[str, str], list[EvoFill]] = {}
    for fill in session.scalars(
        select(EvoFill).where(
            EvoFill.agent_uuid == agent_uuid,
            EvoFill.action == "sell",
            EvoFill.market_ticker.in_({p.market_ticker for p in sold}),
        ).order_by(EvoFill.created_at)
    ):
        fills_by_key.setdefault((fill.market_ticker, fill.side), []).append(fill)
    for pos in sold:
        opened, closed_at = _aware(pos.opened_at), _aware(pos.closed_at)
        if opened is None or closed_at is None:
            continue
        window = [
            f for f in fills_by_key.get((pos.market_ticker, pos.side), [])
            if opened <= (_aware(f.created_at) or opened) <= closed_at
        ]
        qty = sum(f.quantity for f in window)
        if qty:
            out[pos.id] = _r(sum(f.price_cents * f.quantity for f in window) / qty)
    return out


def trade_rows(
    session,
    agent_uuid: str,
    ledger: str,
    *,
    status: str = "all",
    sort: str = "recent",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """One bot's trades, one row per position, with entry/exit, P&L and the strategy
    and exit reason attributed from its orders."""
    query = select(EvoPosition).where(
        EvoPosition.agent_uuid == agent_uuid, EvoPosition.ledger == ledger
    )
    if status == "open":
        query = query.where(EvoPosition.status == OPEN_STATUS)
    elif status == "closed":
        query = query.where(EvoPosition.status.in_(CLOSED_STATUSES))
    positions = list(session.scalars(query))

    tickers = {p.market_ticker for p in positions}
    orders = _order_attribution(session, agent_uuid, tickers)
    strategy_names = {
        s.id: s.name
        for s in session.scalars(select(EvoStrategy).where(EvoStrategy.agent_uuid == agent_uuid))
    }

    exit_prices = _exit_prices(session, agent_uuid, positions)

    rows: list[dict] = []
    for pos in positions:
        opened, closed_at = _aware(pos.opened_at), _aware(pos.closed_at)
        entry_order = _pick_order(
            orders.get((pos.market_ticker, pos.side, "buy"), []), until=opened
        )
        exit_order = None
        if pos.status == "closed":
            exit_order = _pick_order(
                orders.get((pos.market_ticker, pos.side, "sell"), []), until=closed_at
            )
        intent = (exit_order.intent_json if exit_order else None) or {}
        reason_code = intent.get("exit_reason")
        exit_reason = _ORDER_EXIT_REASONS.get(
            str(reason_code), _EXIT_REASON_BY_STATUS.get(pos.status)
        ) if pos.status != OPEN_STATUS else None

        entry_intent = (entry_order.intent_json if entry_order else None) or {}
        strategy = None
        if entry_order is not None:
            strategy = strategy_names.get(entry_order.strategy_id) or entry_intent.get("strategy")

        if pos.status == "settled":
            exit_price = pos.resolved_value_cents  # 100/0, or None for a voided market
        else:
            exit_price = exit_prices.get(pos.id)
        rows.append({
            "position_id": pos.id,
            "market": pos.market_ticker,
            "side": pos.side,
            "status": pos.status,
            "is_open": pos.status == OPEN_STATUS,
            "quantity": _f(pos.quantity),
            "entry_price_cents": _r(_f(pos.avg_price_cents)),
            "entry_at": opened.isoformat() if opened else None,
            "exit_at": closed_at.isoformat() if closed_at else None,
            "exit_price_cents": exit_price,
            "mark_cents": _f(pos.last_mark_cents) if pos.last_mark_cents is not None else None,
            "realized_pnl_usd": _r(_f(pos.realized_pnl_usd))
            if pos.status != OPEN_STATUS else None,
            "unrealized_pnl_usd": _r(_f(pos.unrealized_pnl_usd))
            if (pos.status == OPEN_STATUS and pos.unrealized_pnl_usd is not None) else None,
            "fees_usd": _r(_f(pos.fees_usd)),
            "hold_hours": _hours_between(pos.opened_at, pos.closed_at)
            if closed_at else _hours_between(pos.opened_at, datetime.now(timezone.utc)),
            "exit_reason": exit_reason,
            "strategy": strategy,
            "strategy_attributed": entry_order is not None,
            "rationale": entry_intent.get("thesis") or entry_intent.get("rationale"),
        })

    def _pnl(row: dict) -> float:
        value = row["realized_pnl_usd"]
        if value is None:
            value = row["unrealized_pnl_usd"]
        return value if value is not None else 0.0

    sorters = {
        "recent": lambda r: (r["exit_at"] or r["entry_at"] or "", r["position_id"]),
        "pnl": _pnl,
        "size": lambda r: r["quantity"] * r["entry_price_cents"],
        "hold": lambda r: r["hold_hours"] or 0.0,
        "market": lambda r: r["market"],
    }
    key = sorters.get(sort, sorters["recent"])
    rows.sort(key=key, reverse=(sort != "market"))

    total = len(rows)
    window = rows[offset:offset + limit]
    return {
        "trades": window,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(window) < total,
    }


# ---------------------------------------------------------------------------
# Activity timestamps (batched — one query for the whole leaderboard)
# ---------------------------------------------------------------------------


def last_activity(session, agent_uuids: list[str]) -> dict[str, dict]:
    """Newest heartbeat and newest order per bot, in two grouped queries."""
    if not agent_uuids:
        return {}
    out = {u: {"heartbeat_at": None, "order_at": None} for u in agent_uuids}
    for uuid, ts in session.execute(
        select(EvoHeartbeat.agent_uuid, func.max(EvoHeartbeat.created_at))
        .where(EvoHeartbeat.agent_uuid.in_(agent_uuids))
        .group_by(EvoHeartbeat.agent_uuid)
    ):
        aware = _aware(ts)
        out.setdefault(uuid, {})["heartbeat_at"] = aware.isoformat() if aware else None
    for uuid, ts in session.execute(
        select(EvoOrder.agent_uuid, func.max(EvoOrder.created_at))
        .where(EvoOrder.agent_uuid.in_(agent_uuids))
        .group_by(EvoOrder.agent_uuid)
    ):
        aware = _aware(ts)
        out.setdefault(uuid, {})["order_at"] = aware.isoformat() if aware else None
    return out


def agent_names(session, agent_uuids: list[str] | None = None) -> dict[str, str]:
    """Short display name per bot (the part before the ` · ` honorific)."""
    query = select(EvoAgent)
    if agent_uuids:
        query = query.where(EvoAgent.agent_uuid.in_(agent_uuids))
    return {a.agent_uuid: a.display_name.split(" · ")[0] for a in session.scalars(query)}
