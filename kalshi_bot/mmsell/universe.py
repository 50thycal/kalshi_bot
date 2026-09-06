"""mmsell's view of the series registry.

The tier ladder this module used to own now lives in `kalshi_bot.registry`, because the same
question — may a book trade this series? — is asked by all eight strategy families, and a
frozenset buried under `mmsell/` could only ever answer it for one. The decision ledger moved
with it: `registry/series_manifest.json` holds the state, the reviewer and the review date for
every series, so a graduation is a reviewable data change rather than an edit to a Python
constant, and `series_observations` records when each series first appeared.

This module stays as mmsell's import surface. Nothing here decides anything; see
`kalshi_bot/registry/__init__.py` for the states, the two-part graduation bar, and why
graduation is a claim about UNDERSTANDING a contract and never about its P&L.

The tier names mmsell already had (`unclassified`, `in_review`, `graduated`) keep working
exactly as before, in book specs and in `mmsell_live_min_tier` alike: `unclassified` is parsed
as the registry's `identified`. Verified series-by-series against the pre-registry
implementation in `tests/test_series_registry.py`.
"""

from __future__ import annotations

from kalshi_bot.registry import GRADUATED, IN_REVIEW, STATE_ORDER, admits, state_of
from kalshi_bot.registry import IDENTIFIED as _IDENTIFIED

#: Legacy spelling of the bottom rung, kept because deployed book specs and env vars use it.
UNCLASSIFIED = "unclassified"

#: Ordered weakest-to-strongest, as before. Aliased to the registry's ladder so there is one
#: ordering, not two that can disagree.
TIER_ORDER: tuple[str, ...] = STATE_ORDER


def tier_of(series: str) -> str:
    """The registry state for a series ticker. See `kalshi_bot.registry.state_of`."""
    return state_of(series)


__all__ = ["GRADUATED", "IN_REVIEW", "UNCLASSIFIED", "TIER_ORDER",
           "tier_of", "admits", "state_of", "_IDENTIFIED"]
