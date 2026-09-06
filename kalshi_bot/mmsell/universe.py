"""mmsell's view of the series registry, plus its real-money exposure pause.

TWO INDEPENDENT BARS live here, and keeping them distinct is the whole point of the module.

    THE REGISTRY TIER asks *do we know what this contract is*. It makes no claim about returns
    at all. The ladder it uses now lives in `kalshi_bot.registry`, because the same question is
    asked by all eight strategy families and a frozenset buried under `mmsell/` could only ever
    answer it for one. The decision ledger moved with it:
    `registry/series_manifest.json` holds the state, the reviewer and the review date for every
    series, so a graduation is a reviewable data change rather than an edit to a Python
    constant, and `series_observations` records when each series first appeared.

    THE EXPOSURE PAUSE (`exposure_paused`) asks *is this specific contract currently costing us
    money faster than we can prove it should stop*. A series can be fully GRADUATED and still be
    paused — `KXNFLSPREAD` is exactly that case, which is why the tier bar shipped in PR #338
    did not stop it.

Neither is an edge filter, and they fail in opposite directions if treated as one. The tier
makes no return claim, so selling it as one invents an edge that was never measured. The pause
*is* motivated by returns, so its danger is the reverse: a temporary pause hardening into a
permanent "we know this loses" that nobody re-tests.

Nothing here decides a tier; see `kalshi_bot/registry/__init__.py` for the states, the two-part
graduation bar, and why graduation is a claim about UNDERSTANDING a contract and never about
its P&L.

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


def exposure_paused(series: str, paused_prefixes) -> bool:
    """Whether REAL MONEY is paused on this series (`mmsell_live_skip_series`).

    A SECOND, INDEPENDENT bar beside the tier, and the distinction is the whole point of this
    module's opening warning. The tier asks *do we know what this contract is*; this asks *is
    this specific contract currently costing us money faster than we can prove it should stop*.
    A series can be fully GRADUATED and still be paused here — `KXNFLSPREAD` is exactly that
    case, which is why the tier bar shipped in PR #338 did not stop it.

    THIS IS NOT AN EDGE FILTER EITHER, and the reason differs from the tier's. The tier makes no
    claim about returns at all. This one *is* motivated by returns — so the danger is the
    opposite direction: that a temporary exposure pause, taken on evidence that explicitly could
    not clear the power bar, hardens into a permanent "we know this loses" that nobody re-tests.
    It is scoped against that by construction: PAPER IS UNTOUCHED, so the series keeps
    accumulating exactly the out-of-sample evidence that decides whether the pause becomes a
    real selection rule or gets lifted. Every entry here must name the ticket carrying that test.

    Longest-prefix match on an upper-cased ticker, same convention as the registry manifest and
    `SERIES_TYPES`, so `KXNFLSPREAD` covers `KXNFLSPREAD-26SEP07ATLDET-DET3` and does not
    accidentally cover `KXNFLSPREADX` — prefixes are series names, not substrings.

    Deliberately NOT a registry state. `barred` in the manifest is a governance refusal — we
    looked at this contract and will not trade it — and it binds paper too. A pause is a
    reversible, returns-motivated hold on real money only, taken on evidence too thin to
    graduate into a refusal. Collapsing the two would either make every pause bind paper (and
    so destroy the evidence that lifts it) or make `barred` reversible on P&L, which is exactly
    the governance-rule-becomes-strategy failure the registry exists to prevent.
    """
    s = (series or "").upper()
    return any(s.startswith(p) for p in paused_prefixes if p)


__all__ = ["GRADUATED", "IN_REVIEW", "UNCLASSIFIED", "TIER_ORDER",
           "tier_of", "admits", "state_of", "exposure_paused", "_IDENTIFIED"]
