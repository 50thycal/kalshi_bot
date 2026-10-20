"""The liquidity-incentive book's overall P&L over time, for the Charts page.

The paired-run chart (`series.build_pnl_series`) cannot show this book: it has no paper twin,
its marks come from a tape this book's markets are not on, and most of its money is the reward
for resting, which never appears as a fill. This is the book-level curve instead, built from the
same records the landing-page card reads (`incentive_book.build_incentive_block`), so the last
point of the net line IS the card's all-time net:

* **fill P&L (realized)** — per market, the newest `positions` snapshot at or before each
  instant, counted only when it is flat (the card's rule). A market contributes its realized
  P&L from the snapshot that first shows it flat, never before;
* **reward credits** — balance residuals that match a payout on Kalshi's Rewards page
  (`reward_ledger.confirmed_reward`), at the instant the ledger saw them. Unconfirmed residuals
  are reported as a total and never added to any line;
* **open positions, marked** — contracts this book holds from ITS fills, valued at the held
  side's best bid in the newest `incentive_market_snapshots` row at or before the instant and
  no older than `incentive_book.SNAPSHOT_FRESH`. A held market with no such row is left out of
  that point and the point is flagged `partial`: a gap reads as a gap, never as a move.

Nothing is applied backwards: every value at an instant comes from rows that existed then.

Alongside the curve: the fills and reward payouts as events on the time axis, the net per UTC
day, and a 7-day pace projected to a month. The pace is an ESTIMATE and is labelled as one.
"""

from __future__ import annotations

import threading
import time
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from .. import models as m
from ..liquidity_incentive import live as limm
from ..liquidity_incentive.reward_ledger import confirmed_reward
from . import incentive_book as ib

MAX_POINTS = 500
#: The page refreshes every minute; the reads behind one build (every positions snapshot of
#: every market the book traded) do not need to run more often than that.
CACHE_SECONDS = 60.0
#: The north star (CLAUDE.md): realized profit per month across ALL books. Shown beside this
#: book's pace for scale, never as this book's target.
GOAL_PER_MONTH_USD = 100.0
PACE_DAYS = 7


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _r(v: float | None, places: int = 2) -> float | None:
    return None if v is None else round(float(v), places)


@dataclass
class _Ticker:
    """What valuing one market's open position at an instant needs."""

    fill_at: list = field(default_factory=list)      # fill times, ascending
    state: list = field(default_factory=list)        # (net contracts, {side: entry cents}) after each
    pos_at: list = field(default_factory=list)       # positions snapshot times, ascending
    pos_flat: list = field(default_factory=list)     # was that snapshot flat
    snap_at: list = field(default_factory=list)      # collector snapshot times, ascending
    snap: list = field(default_factory=list)         # (best_yes_bid, best_no_bid, book_valid)


@dataclass
class _Base:
    book: str
    start: datetime | None
    realized_steps: list            # (at, delta usd), ascending
    reward_steps: list              # (at, usd), ascending
    unconfirmed_usd: float
    markers: list                   # fills and reward payouts, ascending
    tickers: dict                   # ticker -> _Ticker, only markets this book has filled on


def _book_fills(session, book: str) -> list[tuple]:
    """(fill, order) for every fill on this book's orders, oldest first."""
    rows = session.execute(
        select(m.Fill, m.LiveOrder)
        .join(m.LiveOrder, m.LiveOrder.kalshi_order_id == m.Fill.kalshi_order_id)
        .where(m.LiveOrder.strategy == book, m.Fill.filled_at.is_not(None))
        .order_by(m.Fill.filled_at, m.Fill.id))
    return [(f, o) for f, o in rows]


def _fill_marker(fill, order) -> dict:
    coid = order.client_order_id or ""
    closing = order.action != "buy" or coid.startswith(
        (ib.EXIT_LEG_PREFIX, ib.EXIT_SELL_PREFIX, ib.RESTING_SELL_PREFIX))
    qty = int(fill.quantity or 0)
    price = int(order.limit_price) if order.limit_price is not None else None
    verb = "sold" if order.action != "buy" else "bought"
    return {
        "at": _aware(fill.filled_at).isoformat(),
        "kind": "exit" if closing else "fill",
        "market": fill.market_ticker,
        "side": order.side,
        "quantity": qty,
        "price_cents": price,
        "cost_usd": _r(price * qty / 100.0) if price is not None and not closing else None,
        "label": f"{fill.market_ticker}: {verb} {qty} {order.side or ''}"
                 + (f" @ {price}c" if price is not None else ""),
    }


def _load(session, book: str) -> _Base:
    orders = list(session.scalars(select(m.LiveOrder).where(m.LiveOrder.strategy == book)))
    tickers = sorted({o.market_ticker for o in orders if o.action == "buy"})
    start = min((_aware(o.created_at) for o in orders), default=None)

    # Fills: the open-position state per market, and the events on the time axis.
    held: dict[str, _Ticker] = {}
    markers = []
    run: dict[str, tuple[float, dict, dict]] = {}
    for fill, order in _book_fills(session, book):
        q = float(fill.quantity or 0)
        if not q:
            continue
        markers.append(_fill_marker(fill, order))
        net, cost, size = run.get(fill.market_ticker, (0.0, {}, {}))
        cost, size = dict(cost), dict(size)
        # The same reading as `incentive_book._own_position`: +YES / -NO, entry from the
        # fill-weighted limit price of the buys on each side.
        if order.action == "buy":
            net += q if order.side == limm.SIDE_YES else -q
            if order.limit_price is not None:
                cost[order.side] = cost.get(order.side, 0.0) + q * float(order.limit_price)
                size[order.side] = size.get(order.side, 0.0) + q
        else:
            net += -q if order.side == limm.SIDE_YES else q
        run[fill.market_ticker] = (net, cost, size)
        entry = {s: int(round(cost[s] / size[s])) for s in cost if size.get(s)}
        t = held.setdefault(fill.market_ticker, _Ticker())
        t.fill_at.append(_aware(fill.filled_at))
        t.state.append((net, entry))

    # Realized: per market, the change in "realized P&L if the newest snapshot is flat".
    realized_steps = []
    if tickers:
        rows = session.execute(
            select(m.Position.market_ticker, m.Position.captured_at, m.Position.quantity,
                   m.Position.realized_pnl)
            .where(m.Position.market_ticker.in_(tickers))
            .order_by(m.Position.market_ticker, m.Position.captured_at, m.Position.id))
        last_ticker, prev = None, 0.0
        for ticker, at, qty, realized in rows:
            if ticker != last_ticker:
                last_ticker, prev = ticker, 0.0
            at = _aware(at)
            flat = (qty or 0) == 0
            value = float(realized) if flat and realized is not None else 0.0
            if value != prev:
                realized_steps.append((at, value - prev))
                prev = value
            if ticker in held:
                held[ticker].pos_at.append(at)
                held[ticker].pos_flat.append(flat)
        realized_steps.sort(key=lambda s: s[0])

    # Marks: the collector's book for markets this book has held, from its first fill on.
    for ticker, t in held.items():
        for at, yes_bid, no_bid, valid in session.execute(
            select(m.IncentiveMarketSnapshot.at, m.IncentiveMarketSnapshot.best_yes_bid,
                   m.IncentiveMarketSnapshot.best_no_bid, m.IncentiveMarketSnapshot.book_valid)
            .where(m.IncentiveMarketSnapshot.market_ticker == ticker,
                   m.IncentiveMarketSnapshot.at >= t.fill_at[0] - ib.SNAPSHOT_FRESH)
            .order_by(m.IncentiveMarketSnapshot.at)):
            t.snap_at.append(_aware(at))
            t.snap.append((yes_bid, no_bid, valid))

    # Rewards: the card's rule (`incentive_book._rewards`) — confirmed payouts only.
    reward_steps, unconfirmed = [], 0.0
    for row in session.scalars(select(m.IncentiveBalanceObservation)
                               .order_by(m.IncentiveBalanceObservation.at)):
        if row.residual_cents is None or (row.notes_json or {}).get("residual_untrustworthy"):
            continue
        cents, at = int(row.residual_cents), _aware(row.at)
        if cents > 0 and confirmed_reward(cents, at):
            reward_steps.append((at, cents / 100.0))
            markers.append({"at": at.isoformat(), "kind": "reward", "amount_usd": _r(cents / 100.0),
                            "label": f"reward credit +${cents / 100.0:.2f}"})
    if tickers:
        _, unconfirmed, _, _ = ib._rewards(session, tickers)
    markers.sort(key=lambda mk: mk["at"])
    return _Base(book=book, start=start, realized_steps=realized_steps,
                 reward_steps=reward_steps, unconfirmed_usd=unconfirmed, markers=markers,
                 tickers=held)


def _open_value(t: _Ticker, when: datetime) -> tuple[float, bool, bool]:
    """(unrealized usd, held, priced) for one market at `when`."""
    i = bisect_right(t.fill_at, when)
    if i == 0:
        return 0.0, False, True
    net, entry = t.state[i - 1]
    if abs(net) < 1:
        return 0.0, False, True
    # Settled: a flat snapshot taken after the last fill so far.
    j = bisect_right(t.pos_at, when)
    if j and t.pos_flat[j - 1] and t.pos_at[j - 1] >= t.fill_at[i - 1]:
        return 0.0, False, True
    side = limm.SIDE_YES if net > 0 else limm.SIDE_NO
    if side not in entry:
        return 0.0, False, True
    k = bisect_right(t.snap_at, when)
    if k == 0 or when - t.snap_at[k - 1] > ib.SNAPSHOT_FRESH:
        return 0.0, True, False
    yes_bid, no_bid, valid = t.snap[k - 1]
    mark = yes_bid if side == limm.SIDE_YES else no_bid
    if mark is None:
        if not valid:
            return 0.0, True, False
        mark = 0   # a live book with nobody bidding our side is worth 0 (the card's rule)
    return abs(net) * (int(mark) - entry[side]) / 100.0, True, True


def _grid(since: datetime, until: datetime, n: int, events: list[datetime]) -> list[datetime]:
    """An even grid across the window plus every event inside it, so each step is drawn at
    the instant it happened rather than at the next grid line."""
    n = max(2, n)
    span = (until - since).total_seconds()
    stamps = {since + timedelta(seconds=span * i / (n - 1)) for i in range(n)}
    stamps.update(e for e in events if since <= e <= until)
    return sorted(stamps)


def _daily(base: _Base, today: date) -> list[dict]:
    if base.start is None:
        return []
    days: dict[date, list[float]] = {}
    for at, usd in base.realized_steps:
        days.setdefault(at.date(), [0.0, 0.0])[0] += usd
    for at, usd in base.reward_steps:
        days.setdefault(at.date(), [0.0, 0.0])[1] += usd
    out, d = [], min([base.start.date(), *days])
    while d <= today:
        realized, rewards = days.get(d, [0.0, 0.0])
        out.append({"day": d.isoformat(), "realized_usd": _r(realized),
                    "rewards_usd": _r(rewards), "net_usd": _r(realized + rewards)})
        d += timedelta(days=1)
    return out


def build_book_pnl(session, book: str = limm.LIVE_TAG, *, now: datetime | None = None,
                   since: datetime | None = None, max_points: int = MAX_POINTS,
                   base: _Base | None = None) -> dict:
    """The book's P&L curve over [since, now], plus all-time daily net, events and pace."""
    now = now or datetime.now(timezone.utc)
    base = base or _load(session, book)
    note = ("Net = realized fill P&L + reward credits Kalshi confirmed on its Rewards page — the "
            "landing-page card's all-time net. Open positions are marked to the held side's best "
            "bid from the collector's 10-minute book; a held market with no fresh book is left "
            "out of that point and shaded as a data gap. Nothing is applied backwards.")
    if base.start is None:
        return {"book": book, "points": [], "markers": [], "daily": [], "stats": None,
                "note": "this book has placed no orders yet"}
    lo = max(base.start, since) if since else base.start
    if lo >= now:
        lo = now - timedelta(minutes=1)
    events = ([a for a, _ in base.realized_steps] + [a for a, _ in base.reward_steps]
              + [datetime.fromisoformat(mk["at"]) for mk in base.markers])
    stamps = _grid(lo, now, max_points, events)

    points = []
    ri = wi = 0
    realized = rewards = 0.0
    for when in stamps:
        while ri < len(base.realized_steps) and base.realized_steps[ri][0] <= when:
            realized += base.realized_steps[ri][1]
            ri += 1
        while wi < len(base.reward_steps) and base.reward_steps[wi][0] <= when:
            rewards += base.reward_steps[wi][1]
            wi += 1
        unrealized, held, complete = 0.0, 0, True
        for t in base.tickers.values():
            value, is_held, priced = _open_value(t, when)
            held += is_held
            complete = complete and priced
            unrealized += value
        net = realized + rewards
        points.append({
            "at": when.isoformat(),
            "realized": round(realized, 4),
            "rewards": round(rewards, 4),
            "net": round(net, 4),
            "unrealized": round(unrealized, 4),
            "total": round(net + unrealized, 4),
            "held_markets": held,
            "partial": not complete,
        })

    last = points[-1]
    pace_from = now - timedelta(days=PACE_DAYS)
    week = (sum(u for a, u in base.realized_steps if a > pace_from)
            + sum(u for a, u in base.reward_steps if a > pace_from))
    running_days = (now - base.start).total_seconds() / 86400.0
    window_first = points[0]
    stats = {
        "net_usd": _r(last["net"]),
        "realized_usd": _r(last["realized"]),
        "rewards_usd": _r(last["rewards"]),
        "reward_payouts": len(base.reward_steps),
        "unconfirmed_rewards_usd": _r(base.unconfirmed_usd),
        "unrealized_usd": _r(last["unrealized"]),
        "total_usd": _r(last["total"]),
        "held_markets": last["held_markets"],
        "fills": sum(1 for mk in base.markers if mk["kind"] == "fill"),
        "net_change_in_window_usd": _r(last["net"] - window_first["net"]),
        "running_days": _r(running_days, 1),
        # ESTIMATE: the last week's net, scaled to 30 days. It is a run rate, not a forecast.
        "pace_days": PACE_DAYS,
        "pace_week_net_usd": _r(week),
        "est_pace_per_month_usd": _r(week * 30.0 / PACE_DAYS) if running_days >= 1 else None,
        "goal_per_month_usd": GOAL_PER_MONTH_USD,
    }
    window_markers = [mk for mk in base.markers if datetime.fromisoformat(mk["at"]) >= lo]
    return {
        "book": book,
        "since": lo.isoformat(),
        "until": now.isoformat(),
        "points": points,
        "markers": window_markers,
        "daily": _daily(base, now.date()),
        "stats": stats,
        "note": note,
    }


class BookPnlCache:
    """The loaded records, kept for `CACHE_SECONDS`; each request still cuts its own window."""

    def __init__(self, ttl: float = CACHE_SECONDS) -> None:
        self.ttl = ttl
        self._lock = threading.Lock()
        self._held: dict[str, tuple[float, _Base]] = {}

    def build(self, session, book: str = limm.LIVE_TAG, **kw) -> dict:
        with self._lock:
            hit = self._held.get(book)
            if hit is None or time.monotonic() - hit[0] > self.ttl:
                hit = (time.monotonic(), _load(session, book))
                self._held[book] = hit
        return build_book_pnl(session, book, base=hit[1], **kw)
