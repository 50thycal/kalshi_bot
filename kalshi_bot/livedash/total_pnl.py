"""Every live book's realized P&L together, over time — the landing page's total P&L chart.

The headline (`overview.build_headline`) says where the month stands; this says how it got
there, and how every month before it went. One curve for all strategies, built from the same
records and the same rules as the headline, so the month view ends on the headline figure:

* **trading** — every market a live book got filled on (any book, active or not, all time),
  counted once at its realized P&L after fees from its newest `positions` snapshot, at the
  instant it was realized (`overview._realized_at`). A market two books shared is one market
  here: the per-book split only matters on the cards;
* **rewards** — confirmed liquidity reward credits (`incentive_book.reward_credits`, DEC-039)
  at the instant the balance moved. Unconfirmed credits never count;
* **total** — the two summed: the goal figure, over time.

Open positions have no history here (no marks are replayed); the page draws them as a dotted
tail from the last point to now, from the headline's own unrealized figure.

Points are cumulative from the first record and bucketed (an hour, or coarser when that would
exceed `MAX_POINTS`); each point carries the time of the last record in its bucket. Nothing
here writes.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from sqlalchemy import func, select

from .. import models as m
from . import incentive_book
from . import overview as ov

MAX_POINTS = 1500
BUCKETS_SECONDS = (3600, 6 * 3600, 86400)
#: The landing page refreshes every minute; the all-time read does not need to run more often
#: than the overview's own rebuild.
CACHE_SECONDS = 120.0
EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _filled_tickers(session) -> list[str]:
    """Every market a live book bought contracts on. A ticker whose fills sum to zero contracts
    is left out, as the headline leaves it out (no share to credit)."""
    rows = session.execute(
        select(m.LiveOrder.market_ticker)
        .join(m.Fill, m.Fill.kalshi_order_id == m.LiveOrder.kalshi_order_id)
        .where(m.LiveOrder.strategy.is_not(None), m.LiveOrder.action == "buy")
        .group_by(m.LiveOrder.market_ticker)
        .having(func.sum(m.Fill.quantity) > 0)
    )
    return sorted(t for (t,) in rows)


def _bucket(events: list[tuple[datetime, str, float]]) -> list[dict]:
    """Cumulative points, one per bucket that has a record, at its last record's time."""
    if not events:
        return []
    for size in BUCKETS_SECONDS:
        if len({int(at.timestamp()) // size for at, _, _ in events}) <= MAX_POINTS:
            break
    points: list[dict] = []
    totals = {"trading": 0.0, "rewards": 0.0}
    key = None
    for at, kind, usd in events:
        totals[kind] += usd
        k = int(at.timestamp()) // size
        if k != key:
            points.append({})
            key = k
        points[-1] = {"at": at.isoformat(), "trading": round(totals["trading"], 2),
                      "rewards": round(totals["rewards"], 2),
                      "total": round(totals["trading"] + totals["rewards"], 2)}
    return points


def build_total_pnl(session, *, now: datetime | None = None) -> dict:
    now = ov._aware(now) or datetime.now(timezone.utc)
    tickers = _filled_tickers(session)
    snaps = ov._latest_snapshots(session, tickers)
    settled = {t: s for t, s in snaps.items() if ov._settled(s)}
    realized_at = ov._realized_at(session, settled)
    rewards = incentive_book.reward_credits(session, EPOCH)
    events = [(realized_at[t], "trading", float(s.realized_pnl)) for t, s in settled.items()]
    events += [(at, "rewards", usd) for at, usd in rewards]
    events = [e for e in events if e[0] <= now]
    events.sort(key=lambda e: e[0])
    return {
        "generated_at": now.isoformat(),
        "month_start": ov.month_start(now).isoformat(),
        "goal_usd": ov.MONTHLY_GOAL_USD,
        "markets_settled": len(settled),
        "reward_payouts": len(rewards),
        "points": _bucket(events),
    }


class TotalPnlCache:
    """The built series, kept for `CACHE_SECONDS`."""

    def __init__(self, ttl: float = CACHE_SECONDS) -> None:
        self.ttl = ttl
        self._lock = threading.Lock()
        self._held: tuple[float, dict] | None = None

    def build(self, session) -> dict:
        with self._lock:
            if self._held is None or time.monotonic() - self._held[0] > self.ttl:
                self._held = (time.monotonic(), build_total_pnl(session))
            return self._held[1]
