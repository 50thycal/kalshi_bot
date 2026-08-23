"""Operator-facing observability shared across books (XOS-000004).

Two problems this package exists for, both recorded on that ticket:

* a configured Kalshi series that matches nothing returns HTTP 200 with an EMPTY
  list, which is not an exception — so the per-book `except` paths never fire and
  a book can fetch nothing, forever, while its log line looks healthy;
* the per-cycle funnel counters are emitted as structured log ATTRIBUTES, and the
  ops logs channel returns `message` text only, so an operator can read the
  message and none of the numbers.

`series_fetch` answers the first, `funnel` the second. Both are deliberately
book-agnostic: the defect is a property of the series-addressed fetch shape, not
of any one strategy, and neither module may make a trading decision.
"""

from .funnel import (
    FUNNEL_COUNTERS,
    FUNNEL_STAGES,
    SUMMARY_MARKER,
    FunnelState,
    diagnose,
    first_zero_stage,
    funnel_summary,
)
from .series_fetch import SeriesFetchResult, fetch_markets_by_series, warn_on_empty_series

__all__ = [
    "FUNNEL_COUNTERS",
    "FUNNEL_STAGES",
    "SUMMARY_MARKER",
    "FunnelState",
    "SeriesFetchResult",
    "diagnose",
    "fetch_markets_by_series",
    "first_zero_stage",
    "funnel_summary",
    "warn_on_empty_series",
]
