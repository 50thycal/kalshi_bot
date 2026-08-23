"""The shared series-addressed market fetch, and the warning it owes an operator.

Four books address their universe the same way — a configured list of Kalshi
series tickers, each paginated through `get_markets(series_ticker=...)`. Every
one of them handled the FAILURE case (an exception → warn and move on) and none
of them handled the EMPTY case, because an unknown, renamed or delisted series
is not an error on Kalshi's side: it is HTTP 200 with `{"markets": []}`.

That is the whole defect recorded on XOS-000004. A book whose entire configured
universe has gone empty keeps logging a healthy-looking cycle line forever, and
the only signal anything is wrong arrives much later and from somewhere else —
the Control Tower noticing the experiment has no evidence, which reports the
symptom and cannot localise it.

This module does not decide anything. It fetches, counts, and says out loud what
came back. Selection rules, eligibility and configuration stay with each book.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..kalshi.errors import AuthError
from .funnel import (
    MAX_SERIES_LISTED,
    TRUNCATION_MARKER,
    sanitize_series,
)

logger = logging.getLogger(__name__)


@dataclass
class SeriesFetchResult:
    """What one cycle's series-addressed fetch actually returned."""

    markets: list[dict] = field(default_factory=list)
    #: sanitized series ticker -> open markets returned. Every CONFIGURED series
    #: appears, including the ones that returned nothing — that is the point.
    per_series: dict[str, int] = field(default_factory=dict)
    #: series whose fetch raised. Distinct from empty: a failure is a transport
    #: problem, an empty is a venue answer, and conflating them hides both.
    failed: list[str] = field(default_factory=list)

    @property
    def configured(self) -> int:
        return len(self.per_series)

    @property
    def empty_series(self) -> list[str]:
        """Configured series that returned zero markets WITHOUT failing."""
        return sorted(
            name for name, n in self.per_series.items() if n == 0 and name not in self.failed
        )

    @property
    def total_markets(self) -> int:
        return sum(self.per_series.values())

    @property
    def universe_empty(self) -> bool:
        """Every configured series returned nothing. The book cannot trade."""
        return self.configured > 0 and self.total_markets == 0


def _bounded_series_list(names: list[str]) -> str:
    shown = names[:MAX_SERIES_LISTED]
    hidden = len(names) - len(shown)
    listed = " ".join(shown)
    return f"{listed} {TRUNCATION_MARKER}{hidden}" if hidden > 0 else listed


def warn_on_empty_series(book: str, result: SeriesFetchResult, log: logging.Logger | None = None) -> None:
    """Say, in the log MESSAGE, which configured series came back empty.

    Message text rather than structured fields, because the ops logs channel
    returns `message` and drops attributes — a warning an operator cannot read is
    not a warning. Emitted at most once per cycle per book, so a legitimately
    quiet series costs one line and not one per page.

    The entire-universe case is louder (ERROR, distinct wording): "some series are
    quiet" is routine, "this book has nothing to look at" is the condition that
    makes it inert, and the two must not read the same at a glance.
    """
    log = log or logger
    empty = result.empty_series
    if not empty:
        return
    detail = _bounded_series_list(empty)
    if result.universe_empty:
        log.error(
            f"{book}: ENTIRE configured universe returned zero open markets "
            f"({len(empty)}/{result.configured} series empty) — this book cannot trade: "
            f"[{detail}]"
        )
        return
    log.warning(
        f"{book}: configured series returned zero open markets "
        f"({len(empty)}/{result.configured} empty): [{detail}]"
    )


def fetch_markets_by_series(
    client,
    series: list[str],
    *,
    book: str,
    status: str = "open",
    limit: int = 200,
    max_pages: int = 4,
    log: logging.Logger | None = None,
    warn: bool = True,
) -> SeriesFetchResult:
    """Paginate `get_markets` across `series`, counting what each one returned.

    Behaviour preserved from the per-book loops this replaces: `AuthError`
    propagates (credentials are a cycle-level problem), any other exception ends
    THAT series with a warning rather than the cycle, and pagination stops when
    Kalshi returns no cursor.

    What is new is only that the empty case is counted and reported.
    """
    log = log or logger
    result = SeriesFetchResult()
    for name in series:
        key = sanitize_series(name)
        result.per_series.setdefault(key, 0)
        cursor: str | None = None
        for _ in range(max_pages):
            try:
                page = client.get_markets(
                    status=status, series_ticker=name, limit=limit, cursor=cursor
                )
            except AuthError:
                raise
            except Exception as exc:  # noqa: BLE001 — one series must not kill the cycle
                if key not in result.failed:
                    result.failed.append(key)
                log.warning(
                    f"{book}: markets fetch failed for series {key}",
                    extra={"extra_fields": {"series": key, "error": str(exc)[:200]}},
                )
                break
            rows = (page or {}).get("markets") or []
            result.markets.extend(rows)
            result.per_series[key] += len(rows)
            cursor = (page or {}).get("cursor") or None
            if not cursor:
                break
    if warn:
        warn_on_empty_series(book, result, log)
    return result
