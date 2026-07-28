"""The shared mark source for both legs of a paired run.

Both sides of a live-vs-paper comparison must be valued off the *same* price at the
*same* instant, or "the paper book is up more than live" can just mean the two were
marked differently. So the dashboard never uses either system's own stored
unrealized figure for the comparison: it derives both from one tick series and
reports each system's stored value alongside, so a mark-source disagreement shows
up as its own labelled divergence component instead of hiding inside the total.

The tick series is `mmsell_position_ticks` — the per-cycle orderbook capture the
mmsell tracker already writes for every market with an open mmsell paper position.
It is real, timestamped, executable market data that was recorded as the run
happened. Nothing here ever applies a current price to a past moment.

Coverage is a genuine limitation and is reported rather than smoothed over: a
ticker is taped only while some mmsell paper book holds it, so a live position on a
market no paper book held would have no marks. `coverage()` says exactly which
tickers were and were not covered.

A long NO position is marked at the **no-bid** — the price it could actually be
sold at — matching the conservative convention the paper engine uses for exits.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from .. import models as m

# Marks older than this are shown as stale rather than presented as current.
STALE_AFTER_MINUTES = 30


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Mark:
    ticker: str
    at: datetime
    price_cents: float
    yes_bid: int | None = None
    yes_ask: int | None = None
    no_bid: int | None = None
    no_ask: int | None = None

    def age_seconds(self, now: datetime) -> float:
        return max(0.0, (now - self.at).total_seconds())

    def is_stale(self, now: datetime, *, minutes: int = STALE_AFTER_MINUTES) -> bool:
        return self.age_seconds(now) > minutes * 60

    def to_dict(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        return {
            "ticker": self.ticker,
            "at": self.at.isoformat(),
            "price_cents": self.price_cents,
            "yes_bid": self.yes_bid, "yes_ask": self.yes_ask,
            "no_bid": self.no_bid, "no_ask": self.no_ask,
            "age_seconds": round(self.age_seconds(now)),
            "stale": self.is_stale(now),
        }


class MarkIndex:
    """Time-indexed executable marks for a set of tickers over one window."""

    def __init__(self, side: str = "no"):
        self.side = side
        self._by_ticker: dict[str, list[Mark]] = {}
        self._times: dict[str, list[datetime]] = {}
        self._requested: set[str] = set()

    # --- loading ---

    @classmethod
    def load(
        cls, session, tickers, since: datetime, until: datetime | None = None,
        *, side: str = "no",
    ) -> MarkIndex:
        index = cls(side=side)
        index._requested = set(tickers)
        if not index._requested:
            return index
        query = select(m.MmSellPositionTick).where(
            m.MmSellPositionTick.market_ticker.in_(sorted(index._requested)),
            m.MmSellPositionTick.captured_at >= since,
        )
        if until is not None:
            query = query.where(m.MmSellPositionTick.captured_at <= until)
        for row in session.scalars(query.order_by(m.MmSellPositionTick.captured_at)):
            price = row.no_bid if side == "no" else row.yes_bid
            if price is None:
                continue  # a one-sided book cannot be marked; skip rather than guess
            at = _aware(row.captured_at)
            if at is None:
                continue
            index._by_ticker.setdefault(row.market_ticker, []).append(Mark(
                ticker=row.market_ticker, at=at, price_cents=float(price),
                yes_bid=row.yes_bid, yes_ask=row.yes_ask,
                no_bid=row.no_bid, no_ask=row.no_ask,
            ))
        for ticker, marks in index._by_ticker.items():
            index._times[ticker] = [mk.at for mk in marks]
        return index

    # --- lookup ---

    def mark_for(self, ticker: str) -> Mark | None:
        """The newest mark for a ticker, or None when it was never taped."""
        marks = self._by_ticker.get(ticker)
        return marks[-1] if marks else None

    def mark_as_of(self, ticker: str, when: datetime) -> Mark | None:
        """The newest mark at or before `when` — never a later one. This is what keeps
        a historical P&L series honest: a point at 14:00 is priced with the book as it
        stood at 14:00, not with today's price applied backwards."""
        times = self._times.get(ticker)
        if not times:
            return None
        idx = bisect.bisect_right(times, when) - 1
        return self._by_ticker[ticker][idx] if idx >= 0 else None

    def timeline(self) -> list[datetime]:
        """Every distinct tick instant across all tickers, ascending."""
        stamps: set[datetime] = set()
        for times in self._times.values():
            stamps.update(times)
        return sorted(stamps)

    def latest_at(self) -> datetime | None:
        times = self.timeline()
        return times[-1] if times else None

    def coverage(self) -> dict:
        covered = sorted(t for t in self._requested if self._by_ticker.get(t))
        missing = sorted(t for t in self._requested if not self._by_ticker.get(t))
        return {
            "requested": len(self._requested),
            "covered": len(covered),
            "missing": missing[:25],
            "missing_count": len(missing),
            "ticks": sum(len(v) for v in self._by_ticker.values()),
            "source": "mmsell_position_ticks",
            "side": self.side,
        }
