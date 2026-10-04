"""The landing page: three answers an operator wants before anything else.

1. **Headline** — real money realized this calendar month (UTC) against the $100/month north
   star, per live book, with a health light per book: is it trading, has a loss stop tripped,
   is a cap refusing its orders.
2. **Order board** — every live order still working plus the last day's, labelled RESTING /
   PARTIAL / FILLED / EXPIRED, with its age next to the book's typical time-to-fill, so "eight
   orders never filled" can be read as "eight orders are 40 minutes into a 2-hour wait".
3. **Scorecard** — for a live book running a randomized size split (`sizes=` in its twin's
   parameter snapshot), the arms side by side and progress to the pre-registered readout floors
   (docs/MMSELL_SIZE_SPLIT_CANARY.md §5, docs/MMSELL_REPLAY_PROBES_20261003.md).

Why a separate module and a cache: the comparison page rebuilds both legs of a pair plus a
price series on every load, which is what made the old landing page slow. Everything here is a
handful of indexed SELECTs, and the server refreshes it on a background thread
(`OverviewCache`), so a page load reads a dict that is already built.

Attribution rules — the same chain as `legs.live_leg`, with one addition:

* a book's tickers come from `live_orders.strategy`, its contracts from `fills` joined on
  `kalshi_order_id`, and a ticker's outcome from its newest `positions` snapshot
  (`quantity=0` with `realized_pnl` = settled, net of the fees Kalshi charged);
* **a ticker two books both filled is split pro rata by filled contracts.** `positions` is
  account-wide per ticker, so without the split a draining book and its successor holding the
  same market would each claim the whole of it and the month total would double-count.

The month a settlement belongs to is the snapshot's `captured_at` — the reconcile loop writes
the settlement row once, the cycle it first sees it.

Nothing here writes. Nothing here hardcodes a strategy tag: the books are whichever placed
real orders recently or hold an open twin epoch, and the scorecard applies to any open pair
whose snapshot declares `sizes`.
"""

from __future__ import annotations

import logging
import statistics
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from .. import models as m
from ..live.sizing import ticker_size
from ..mmsell.market_types import classify as classify_series
from . import marks as marks_mod
from . import pairs as pairs_mod
from .legs import LIVE_WORKING
from .market_meta import classify as market_tag

logger = logging.getLogger("kalshi_bot.livedash")

#: The north star (CLAUDE.md): realized profit per month across all strategies.
MONTHLY_GOAL_USD = 100.0
#: A book that placed a real order inside this window is on the headline.
ACTIVE_BOOK_DAYS = 14
#: How far before the month (or the fill-time window) orders are read, so a position settled
#: this month but entered last month is still attributed. Nothing mmsell trades is held that long.
ENTRY_LOOKBACK_DAYS = 45
#: Health reads the parity tape over this window: the latest gate a book hit, not its history.
HEALTH_WINDOW_MINUTES = 60
#: A book with no order for this long, and no gate explaining why, is flagged as quiet.
QUIET_AFTER_MINUTES = pairs_mod.IDLE_AFTER_MINUTES
#: The order board shows working orders plus everything placed inside this window.
BOARD_WINDOW_HOURS = 24
BOARD_MAX_ROWS = 80
#: Typical time-to-fill is the median over this many days of a book's own filled orders.
FILL_TIME_DAYS = 7
#: An open position is priced off its newest tick inside this window; older is no price.
MARK_LOOKBACK_DAYS = 3

# Gate codes the live mirror returns (kalshi_bot/live/executor.py::mirror_mmsell_entry),
# recorded per candidate in `live_paper_parity_events.live_outcome`.
LOSS_STOP_GATES = {"gate:daily_loss": "daily loss stop tripped"}
CAP_GATES = {
    "gate:total_exposure": "total exposure cap",
    "gate:open_cap": "open-position cap",
    "gate:exposure": "per-market exposure cap",
}
OFF_GATE = "gate:switches"   # not on the live list, or a master switch is off

# Pre-registered readout floors (docs/MMSELL_SIZE_SPLIT_CANARY.md §5 S2,
# docs/MMSELL_REPLAY_PROBES_20261003.md P-SLOW / P-SPREAD). Restated, never re-derived.
S2_SETTLED_PER_ARM = 150
S1_FULL_FILL_PASS = 0.70
S0_INSTRUMENT_PASS = 0.98
SLOW_CELL_TYPES = frozenset({"event_stat", "mention", "exact_score", "outright", "game_prop"})
SLOW_CELL_FLOOR = 100
WIDE_SPREAD_CENTS = 3
WIDE_SPREAD_FLOOR = 80


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    aware = _aware(dt)
    return aware.isoformat() if aware else None


def _r(value: float | None, places: int = 2) -> float | None:
    return None if value is None else round(float(value), places)


def month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Shared reads
# ---------------------------------------------------------------------------


def _books(session, now: datetime) -> tuple[list[str], dict[str, pairs_mod.Pair]]:
    """Live tags on the headline: recent real orders, plus any book with an OPEN twin epoch
    (a freshly armed book is on the page before its first order)."""
    since = now - timedelta(days=ACTIVE_BOOK_DAYS)
    recent = set(session.scalars(
        select(m.LiveOrder.strategy).where(
            m.LiveOrder.strategy.is_not(None),
            m.LiveOrder.action == "buy",
            m.LiveOrder.created_at >= since,
        ).distinct()
    ))
    open_pairs: dict[str, pairs_mod.Pair] = {}
    for pair in pairs_mod.list_pairs(session, limit=200):
        if pair.is_open and pair.live_tag not in open_pairs:
            open_pairs[pair.live_tag] = pair
    return sorted(recent | set(open_pairs)), open_pairs


def _orders(session, books: list[str], since: datetime) -> list[m.LiveOrder]:
    if not books:
        return []
    return list(session.scalars(
        select(m.LiveOrder).where(
            m.LiveOrder.strategy.in_(books),
            m.LiveOrder.action == "buy",
            m.LiveOrder.created_at >= since,
        ).order_by(m.LiveOrder.created_at)
    ))


def _fills_by_order(session, orders) -> dict[str, list[m.Fill]]:
    ids = sorted({o.kalshi_order_id for o in orders if o.kalshi_order_id})
    out: dict[str, list[m.Fill]] = {}
    for start in range(0, len(ids), 500):
        for fill in session.scalars(
            select(m.Fill).where(m.Fill.kalshi_order_id.in_(ids[start:start + 500]))
            .order_by(m.Fill.id)
        ):
            out.setdefault(fill.kalshi_order_id or "", []).append(fill)
    return out


def _latest_snapshots(session, tickers) -> dict[str, m.Position]:
    from .legs import _latest_position_snapshots

    tickers = sorted(set(tickers))
    out: dict[str, m.Position] = {}
    for start in range(0, len(tickers), 500):
        out.update(_latest_position_snapshots(session, tickers[start:start + 500]))
    return out


def _settled(snap: m.Position | None) -> bool:
    return snap is not None and (snap.quantity or 0) == 0 and snap.realized_pnl is not None


def _open_marks(session, snaps, now) -> marks_mod.MarkIndex:
    """The newest orderbook tick for every ticker still held — one row per ticker, the same
    tape and the same "latest" read the comparison page values open positions with."""
    held = [t for t, snap in snaps.items() if snap is not None and not _settled(snap)
            and abs(float(snap.quantity_fp or snap.quantity or 0))]
    return marks_mod.MarkIndex.load_latest(
        session, held, now - timedelta(days=MARK_LOOKBACK_DAYS), None, side="no")


def _unrealized(snap: m.Position, mark) -> float | None:
    """Mark-to-bid P&L of one held position, whole account (callers apply the book's share).

    A long position is valued at the price it could be SOLD at now — the NO bid for a NO
    position, the YES bid for a YES one — against its cost basis. None when the tape has no
    price for the ticker or the snapshot has no cost basis: unmeasured, never zero."""
    qty = abs(float(snap.quantity_fp or snap.quantity or 0))
    if mark is None or not qty or snap.market_exposure is None:
        return None
    bid = mark.yes_bid if (snap.side or "").lower() == "yes" else mark.no_bid
    if bid is None:
        return None
    return qty * bid / 100.0 - abs(float(snap.market_exposure))


def _trade_counts(session, books) -> dict[str, int]:
    """All-time trades per book: markets the book actually got filled on (one position = one
    trade, however many fills it took). Unbounded by the lookback, so it is a count, not a
    read of every row."""
    if not books:
        return {}
    rows = session.execute(
        select(m.LiveOrder.strategy, func.count(func.distinct(m.LiveOrder.market_ticker)))
        .join(m.Fill, m.Fill.kalshi_order_id == m.LiveOrder.kalshi_order_id)
        .where(m.LiveOrder.strategy.in_(books), m.LiveOrder.action == "buy")
        .group_by(m.LiveOrder.strategy)
    )
    return {str(k): int(v) for k, v in rows}


# ---------------------------------------------------------------------------
# 1. Headline
# ---------------------------------------------------------------------------


def _health(session, book: str, pair: pairs_mod.Pair | None, last_order_at, now) -> dict:
    """One light per book, worst first: a tripped loss stop, then a cap refusing orders, then
    a book that is not on the live list, then a quiet one.

    Read off the parity tape (what the live mirror returned for each candidate) rather than
    from settings: this service does not share the worker's environment, and the tape is what
    the worker actually decided."""
    gates: dict[str, int] = {}
    if pair is not None:
        rows = session.execute(
            select(m.LivePaperParityEvent.live_outcome, func.count())
            .where(
                m.LivePaperParityEvent.twin_tag == pair.twin_tag,
                m.LivePaperParityEvent.recorded_at >= now - timedelta(minutes=HEALTH_WINDOW_MINUTES),
            )
            .group_by(m.LivePaperParityEvent.live_outcome)
        )
        gates = {str(k): int(v) for k, v in rows if k}
    candidates = sum(gates.values())
    age_min = None
    if last_order_at is not None:
        age_min = round((now - _aware(last_order_at)).total_seconds() / 60)

    lights = []
    for code, label in LOSS_STOP_GATES.items():
        if gates.get(code):
            lights.append({"level": "bad", "text": label, "count": gates[code]})
    for code, label in CAP_GATES.items():
        if gates.get(code):
            lights.append({"level": "warn", "text": f"{label} blocking orders",
                           "count": gates[code]})
    placed = gates.get("placed", 0)
    if candidates and gates.get(OFF_GATE) == candidates:
        state, text = "off", "not trading — off the live list (new orders stopped)"
    elif age_min is not None and age_min <= QUIET_AFTER_MINUTES:
        state, text = "on", f"trading — last order {age_min} min ago"
    elif placed:
        state, text = "on", "trading"
    elif age_min is None:
        state, text = "quiet", "no orders yet"
    else:
        state, text = "quiet", f"quiet — last order {_ago(age_min)} ago"
    return {
        "state": state, "text": text, "lights": lights,
        "candidates_last_hour": candidates if pair is not None else None,
        "placed_last_hour": placed if pair is not None else None,
        "gates_last_hour": gates,
        "tape": pair is not None,
    }


def _ago(minutes: int) -> str:
    if minutes < 120:
        return f"{minutes} min"
    if minutes < 48 * 60:
        return f"{minutes // 60} h"
    return f"{minutes // 1440} d"


def _contracts_held(orders, fills) -> dict[tuple[str, str], float]:
    """Contracts filled per (book, ticker): the weights of the pro-rata split."""
    qty: dict[tuple[str, str], float] = {}
    for o in orders:
        for f in fills.get(o.kalshi_order_id or "", []):
            key = (o.strategy, o.market_ticker)
            qty[key] = qty.get(key, 0.0) + float(f.quantity or 0)
    return qty


def _share(held, book: str, ticker: str) -> float:
    """This book's fraction of the account's position in `ticker` (see module docstring)."""
    total = sum(q for (_, t), q in held.items() if t == ticker)
    return held.get((book, ticker), 0.0) / total if total else 0.0


def build_headline(session, books, open_pairs, orders, fills, snaps, held, now) -> dict:
    start = month_start(now)
    marks = _open_marks(session, snaps, now)
    all_time = _trade_counts(session, books)
    # A trade belongs to the month its first fill landed in.
    first_fill: dict[tuple[str, str], datetime] = {}
    for o in orders:
        for f in fills.get(o.kalshi_order_id or "", []):
            at = _aware(f.filled_at) or _aware(o.created_at)
            key = (o.strategy, o.market_ticker)
            if key not in first_fill or at < first_fill[key]:
                first_fill[key] = at
    qty = held
    ticker_total: dict[str, float] = {}
    books_on: dict[str, int] = {}
    for (_, ticker), q in qty.items():
        if q:
            ticker_total[ticker] = ticker_total.get(ticker, 0.0) + q
            books_on[ticker] = books_on.get(ticker, 0) + 1
    shared = {t for t, n in books_on.items() if n > 1}

    last_order = {}
    for o in orders:
        last_order[o.strategy] = o.created_at
    rows = []
    month_total = unreal_total = 0.0
    unpriced_total = 0
    for book in books:
        month_pnl, settled_month, wins = 0.0, 0, 0
        open_n, open_cost, unreal, unpriced = 0, 0.0, 0.0, 0
        for (b, ticker), q in qty.items():
            if b != book or not q:
                continue
            share = q / ticker_total[ticker]
            snap = snaps.get(ticker)
            if _settled(snap):
                if _aware(snap.captured_at) >= start:
                    pnl = float(snap.realized_pnl) * share
                    month_pnl += pnl
                    settled_month += 1
                    wins += pnl > 0
            elif snap is not None and abs(float(snap.quantity_fp or snap.quantity or 0)):
                open_n += 1
                open_cost += abs(float(snap.market_exposure or 0)) * share
                u = _unrealized(snap, marks.mark_for(ticker))
                if u is None:
                    unpriced += 1
                else:
                    unreal += u * share
        resting = sum(
            1 for o in orders
            if o.strategy == book and (o.status or "").lower() in LIVE_WORKING
            and not fills.get(o.kalshi_order_id or "")
        )
        month_total += month_pnl
        unreal_total += unreal
        unpriced_total += unpriced
        trades_month = sum(1 for (b, _), at in first_fill.items() if b == book and at >= start)
        pair = open_pairs.get(book)
        rows.append({
            "live_tag": book,
            "twin_tag": pair.twin_tag if pair else None,
            "month_realized_usd": _r(month_pnl),
            "settled_this_month": settled_month,
            "wins_this_month": wins,
            "open_positions": open_n,
            "open_cost_usd": _r(open_cost),
            "unrealized_usd": _r(unreal),
            "open_unpriced": unpriced,
            "total_pnl_usd": _r(month_pnl + unreal),
            "trades_this_month": trades_month,
            "trades_all_time": all_time.get(book, 0),
            "resting_orders": resting,
            "last_order_at": _iso(last_order.get(book)),
            "health": _health(session, book, pair, last_order.get(book), now),
        })
    days_in = (now - start).total_seconds() / 86400
    next_month = (start + timedelta(days=32)).replace(day=1)
    days_total = (next_month - start).total_seconds() / 86400
    return {
        "month": start.strftime("%Y-%m"),
        "goal_usd": MONTHLY_GOAL_USD,
        "month_realized_usd": _r(month_total),
        # Open positions marked to the bid now. Not counted toward the goal: it is what the
        # book would book if it could sell everything at once, not money it has made.
        "unrealized_usd": _r(unreal_total),
        "open_unpriced": unpriced_total,
        "total_pnl_usd": _r(month_total + unreal_total),
        "trades_this_month": sum(r["trades_this_month"] for r in rows),
        "trades_all_time": sum(r["trades_all_time"] for r in rows),
        "progress_pct": _r(month_total / MONTHLY_GOAL_USD * 100, 1),
        # A straight-line pace, labelled as such on the page; early in a month it is noise.
        "pace_usd": _r(month_total / days_in * days_total) if days_in >= 1 else None,
        "day_of_month": int(days_in) + 1,
        "days_in_month": round(days_total),
        "books": rows,
        "shared_tickers": len(shared),
    }


# ---------------------------------------------------------------------------
# 2. Order board
# ---------------------------------------------------------------------------

RESTING, PARTIAL, FILLED, EXPIRED = "resting", "partial", "filled", "expired"


def board_status(order: m.LiveOrder, filled_qty: float) -> str:
    """Four words an operator can act on. EXPIRED covers every terminal order that got
    nothing — the 4 h timeout, a drain, an exchange cancel — because to the book they are
    the same outcome; the raw status and cancel reason travel alongside it."""
    working = (order.status or "").lower() in LIVE_WORKING
    requested = float(order.quantity or 0)
    if filled_qty and requested and filled_qty >= requested:
        return FILLED
    if filled_qty:
        return PARTIAL if working else FILLED
    return RESTING if working else EXPIRED


def build_board(orders, fills, now) -> dict:
    board_since = now - timedelta(hours=BOARD_WINDOW_HOURS)
    fill_since = now - timedelta(days=FILL_TIME_DAYS)
    waits: dict[str, list[float]] = {}
    rows = []
    for o in orders:
        own = fills.get(o.kalshi_order_id or "", [])
        filled = float(sum(f.quantity or 0 for f in own))
        created = _aware(o.created_at)
        times = [_aware(f.filled_at) for f in own if f.filled_at]
        first_fill = min(times) if times else None
        if first_fill is not None and created >= fill_since:
            waits.setdefault(o.strategy, []).append((first_fill - created).total_seconds() / 60)
        status = board_status(o, filled)
        if not (status in (RESTING, PARTIAL) or created >= board_since):
            continue
        notional = sum((f.price or 0) * (f.quantity or 0) for f in own)
        rows.append({
            "live_tag": o.strategy,
            "market": o.market_ticker,
            "label": market_tag(o.market_ticker).label,
            "status": status,
            "raw_status": o.status,
            "cancel_reason": (o.cancel_reason or "")[:60] or None,
            "requested": o.quantity,
            "filled": filled or None,
            "limit_cents": o.limit_price,
            "avg_fill_cents": _r(notional / filled) if filled else None,
            "submitted_at": _iso(created),
            "age_minutes": round((now - created).total_seconds() / 60),
            "wait_minutes": (round((first_fill - created).total_seconds() / 60)
                             if first_fill else None),
        })
    order = {RESTING: 0, PARTIAL: 1, FILLED: 2, EXPIRED: 3}
    # Working orders oldest first (the one nearest its timeout leads); finished ones newest first.
    rows.sort(key=lambda r: (order[r["status"]],
                             -r["age_minutes"] if r["status"] in (RESTING, PARTIAL)
                             else r["age_minutes"]))
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in order}
    typical = {
        book: {"median_minutes": round(statistics.median(w)), "n": len(w)}
        for book, w in waits.items() if w
    }
    return {
        "window_hours": BOARD_WINDOW_HOURS,
        "counts": counts,
        "typical_fill": typical,
        "rows": rows[:BOARD_MAX_ROWS],
        "truncated": max(0, len(rows) - BOARD_MAX_ROWS),
    }


# ---------------------------------------------------------------------------
# 3. Scorecard
# ---------------------------------------------------------------------------


def _size_split(pair: pairs_mod.Pair) -> tuple[tuple[int, ...], str] | None:
    params = pair.params or {}
    sizes, salt = params.get("sizes"), params.get("live_size_salt")
    if not isinstance(sizes, (list, tuple)) or len(sizes) < 2 or not salt:
        return None
    try:
        return tuple(int(s) for s in sizes), str(salt)
    except (TypeError, ValueError):
        return None


def build_scorecard(session, pair, orders, fills, snaps, held, now) -> dict | None:
    split = _size_split(pair)
    if split is None:
        return None
    sizes, salt = split
    started = _aware(pair.started_at)
    mine = [o for o in orders if o.strategy == pair.live_tag and _aware(o.created_at) >= started]

    spreads: dict[str, int | None] = {}
    ids = [o.kalshi_order_id for o in mine if o.kalshi_order_id]
    for start in range(0, len(ids), 500):
        for koid, spread in session.execute(
            select(m.ExecutionOrderContext.kalshi_order_id, m.ExecutionOrderContext.spread)
            .where(m.ExecutionOrderContext.kalshi_order_id.in_(ids[start:start + 500]))
        ):
            spreads[koid] = spread

    arms = {i: {"contracts": c, "orders": 0, "orders_on_size": 0, "with_fill": 0,
                "fully_filled": 0, "decided": 0, "contracts_filled": 0.0, "settled": 0,
                "settled_contracts": 0.0, "realized_usd": 0.0, "wins": 0}
            for i, c in enumerate(sizes)}
    slow_settled = wide_settled = 0
    seen_settled: set[str] = set()
    for o in mine:
        idx, contracts = ticker_size(o.market_ticker, sizes, salt=salt)
        a = arms[idx]
        a["orders"] += 1
        a["orders_on_size"] += int((o.quantity or 0) == contracts)
        own = fills.get(o.kalshi_order_id or "", [])
        filled = float(sum(f.quantity or 0 for f in own))
        working = (o.status or "").lower() in LIVE_WORKING
        # Fill rate counts only orders whose fate is known: finished, or already filled.
        a["decided"] += int(filled > 0 or not working)
        if filled:
            a["with_fill"] += 1
            a["fully_filled"] += int(filled >= float(o.quantity or 0))
            a["contracts_filled"] += filled
        snap = snaps.get(o.market_ticker)
        if filled and _settled(snap) and o.market_ticker not in seen_settled:
            seen_settled.add(o.market_ticker)
            # The snapshot covers every order this book (and any other) put on the ticker.
            book_qty = held.get((pair.live_tag, o.market_ticker), 0.0)
            pnl = float(snap.realized_pnl) * _share(held, pair.live_tag, o.market_ticker)
            a["settled"] += 1
            a["settled_contracts"] += book_qty
            a["realized_usd"] += pnl
            a["wins"] += pnl > 0
            mtype, _ = classify_series(o.market_ticker.split("-", 1)[0])
            slow_settled += mtype in SLOW_CELL_TYPES
            spread = spreads.get(o.kalshi_order_id)
            wide_settled += spread is not None and spread >= WIDE_SPREAD_CENTS

    out_arms = []
    for idx in sorted(arms):
        a = arms[idx]
        out_arms.append({
            "arm": idx,
            "contracts": a["contracts"],
            "orders": a["orders"],
            "fill_rate_pct": _r(a["with_fill"] / a["decided"] * 100, 1) if a["decided"] else None,
            "orders_with_fill": a["with_fill"],
            "full_fill_pct": _r(a["fully_filled"] / a["with_fill"] * 100, 1)
            if a["with_fill"] else None,
            "contracts_filled": a["contracts_filled"],
            "settled_markets": a["settled"],
            "wins": a["wins"],
            "realized_usd": _r(a["realized_usd"]),
            "net_cents_per_contract": _r(a["realized_usd"] / a["settled_contracts"] * 100)
            if a["settled_contracts"] else None,
            "progress_pct": _r(min(100.0, a["settled"] / S2_SETTLED_PER_ARM * 100), 1),
        })
    total_orders = sum(a["orders"] for a in arms.values())
    on_size = sum(a["orders_on_size"] for a in arms.values())
    big = max(arms, key=lambda i: arms[i]["contracts"])
    big_fill = arms[big]["with_fill"]
    return {
        "live_tag": pair.live_tag,
        "twin_tag": pair.twin_tag,
        "started_at": _iso(started),
        "days_running": _r((now - started).total_seconds() / 86400, 1),
        "sizes": list(sizes),
        "arms": out_arms,
        "checks": [
            {"id": "S0", "label": "orders went out at their assigned size",
             "value_pct": _r(on_size / total_orders * 100, 1) if total_orders else None,
             "pass_pct": S0_INSTRUMENT_PASS * 100, "n": total_orders},
            {"id": "S1", "label": f"{arms[big]['contracts']}-lot orders that filled completely",
             "value_pct": _r(arms[big]["fully_filled"] / big_fill * 100, 1) if big_fill else None,
             "pass_pct": S1_FULL_FILL_PASS * 100, "n": big_fill},
        ],
        "progress": [
            {"id": "S2", "label": "settled markets per arm (smaller arm)",
             "value": min(a["settled"] for a in arms.values()), "floor": S2_SETTLED_PER_ARM},
            {"id": "P-SLOW", "label": "slow-information cell, settled fills (this book)",
             "value": slow_settled, "floor": SLOW_CELL_FLOOR},
            {"id": "P-SPREAD", "label": f"spread ≥ {WIDE_SPREAD_CENTS}¢ at entry, settled fills "
                                        "(this book)",
             "value": wide_settled, "floor": WIDE_SPREAD_FLOOR},
        ],
    }


# ---------------------------------------------------------------------------
# Assembly + cache
# ---------------------------------------------------------------------------


def build_overview(session, *, now: datetime | None = None) -> dict:
    now = _aware(now) or datetime.now(timezone.utc)
    t0 = time.perf_counter()
    books, open_pairs = _books(session, now)
    since = min(month_start(now), now - timedelta(days=FILL_TIME_DAYS)) - timedelta(
        days=ENTRY_LOOKBACK_DAYS)
    orders = _orders(session, books, since)
    fills = _fills_by_order(session, orders)
    snaps = _latest_snapshots(session, {o.market_ticker for o in orders})
    held = _contracts_held(orders, fills)
    scorecards = [
        card for pair in open_pairs.values()
        if (card := build_scorecard(session, pair, orders, fills, snaps, held, now)) is not None
    ]
    return {
        "generated_at": now.isoformat(),
        "build_ms": round((time.perf_counter() - t0) * 1000),
        "headline": build_headline(session, books, open_pairs, orders, fills, snaps, held, now),
        "board": build_board(orders, fills, now),
        "scorecards": scorecards,
    }


class OverviewCache:
    """The landing page's payload, rebuilt on a background thread.

    A request never waits for a rebuild that is already fresh enough; the first request
    after start (or after the refresher dies) builds inline, so the page can never be empty
    because a thread did not start. A failed rebuild keeps serving the last good payload and
    says how old it is."""

    def __init__(self, builder, *, refresh_seconds: int = 120, stale_seconds: int = 600):
        self._builder = builder
        self.refresh_seconds = refresh_seconds
        self.stale_seconds = stale_seconds
        self._lock = threading.Lock()
        self._payload: dict | None = None
        self._built_at = 0.0
        self._error: str | None = None
        self._thread: threading.Thread | None = None

    def refresh(self) -> None:
        try:
            payload = self._builder()
        except Exception:  # noqa: BLE001 — keep the last good payload
            logger.exception("livedash overview refresh failed")
            self._error = "last refresh failed; showing the previous build"
            return
        with self._lock:
            self._payload, self._built_at, self._error = payload, time.monotonic(), None

    def get(self) -> dict:
        if self._payload is None or time.monotonic() - self._built_at > self.stale_seconds:
            self.refresh()
        with self._lock:
            if self._payload is None:
                raise RuntimeError(self._error or "overview unavailable")
            return {**self._payload,
                    "cache_age_seconds": round(time.monotonic() - self._built_at),
                    "cache_error": self._error}

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        def loop():
            while True:
                self.refresh()
                time.sleep(self.refresh_seconds)

        self._thread = threading.Thread(target=loop, name="livedash-overview", daemon=True)
        self._thread.start()
