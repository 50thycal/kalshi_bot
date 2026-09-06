"""Arrival detection: notice a series the first time Kalshi offers it.

The registry can only gate what it knows exists, and until now nothing recorded existence. A
series appeared, every book that selects by structure rather than by an explicit allowlist
started trading it that cycle, and the only way anyone found out was by running a census by
hand. The measured cost of that: 20.2% of the live canary's trades over 30 days were in series
no taxonomy covered, 68% of it a new season arriving.

So the scan reports every series it SEES — before any category, volume or liquidity filter,
because the registry's question is what Kalshi has offered us, not what some book took. A
series is observed whether or not any strategy could trade it; a filter is a decision and this
is the ledger of facts underneath the decisions.

Accumulate in memory during the cycle, write once at the end: one SELECT and a handful of
UPSERTs per scan rather than a query per market. Observation must never be able to slow or
break a scan, so `flush` swallows its own errors — a lost cycle of arrival data costs a delayed
review, while a raised exception costs trading.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

logger = logging.getLogger(__name__)


def series_of(market: dict, event: dict | None = None) -> str:
    """The series ticker for a market payload.

    Kalshi supplies `series_ticker` on the event, but not on every shape of response, so fall
    back to the ticker's first hyphen-delimited segment — the same derivation
    `mmsell/tracker.py` uses, kept identical so the two never disagree about what series a
    market belongs to."""
    if event:
        st = (event.get("series_ticker") or "").strip()
        if st:
            return st.upper()
    st = (market.get("series_ticker") or "").strip()
    if st:
        return st.upper()
    return (market.get("ticker") or "").split("-")[0].upper()


class SeriesObserver:
    """Per-cycle accumulator. One instance per scan; `flush` persists and resets it."""

    def __init__(self) -> None:
        #: series -> (distinct tickers this cycle, a sample ticker, a sample title)
        self._seen: dict[str, tuple[set[str], str, str]] = {}

    def observe(self, market: dict, event: dict | None = None) -> None:
        series = series_of(market, event)
        ticker = (market.get("ticker") or "").strip()
        if not series or not ticker:
            return
        title = (market.get("title") or (event or {}).get("title") or "").strip()
        tickers, sample_t, sample_title = self._seen.get(series, (set(), ticker, title))
        tickers.add(ticker)
        self._seen[series] = (tickers, sample_t, sample_title or title)

    @property
    def series_seen(self) -> int:
        return len(self._seen)

    def flush(self, session) -> tuple[int, int]:
        """Persist the cycle. Returns (series observed, series seen for the FIRST time ever).

        The second number is the one worth alerting on: it is how many markets became available
        to us this cycle that no review has ever looked at."""
        from kalshi_bot import models as m

        if not self._seen:
            return (0, 0)
        observed = dict(self._seen)
        self._seen = {}
        try:
            now = datetime.now(timezone.utc)
            rows = session.scalars(
                select(m.SeriesObservation).where(
                    m.SeriesObservation.series.in_(sorted(observed)))
            ).all()
            existing = {r.series: r for r in rows}
            new = 0
            for series, (tickers, sample_ticker, sample_title) in observed.items():
                row = existing.get(series)
                if row is None:
                    new += 1
                    row = m.SeriesObservation(
                        series=series, first_seen_at=now,
                        state_at_first_seen=_safe_state(series))
                    session.add(row)
                row.last_seen_at = now
                # Breadth, not a running total: the most distinct markets of this series we
                # have ever seen offered at once. A cumulative count would need every ticker
                # ever seen kept somewhere to stay honest, and would drift upward forever as
                # dated markets roll, saying nothing a reviewer can use.
                row.markets_seen = max(row.markets_seen or 0, len(tickers))
                row.sample_ticker = sample_ticker
                if sample_title:
                    row.sample_title = sample_title[:500]
            session.flush()
            if new:
                logger.info(
                    "series registry: new series observed",
                    extra={"extra_fields": {"new_series": new,
                                            "series_observed": len(observed)}})
            return (len(observed), new)
        except Exception:  # noqa: BLE001 — observation must never break a scan
            logger.exception("series registry: observation flush failed")
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                logger.exception("series registry: rollback after failed flush also failed")
            return (0, 0)


def _safe_state(series: str) -> str | None:
    from . import state_of
    try:
        return state_of(series)
    except Exception:  # noqa: BLE001
        logger.exception("series registry: state lookup failed for %s", series)
        return None
