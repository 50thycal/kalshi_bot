"""Series registry — which markets any strategy family is allowed to trade, and how well we
know them.

THE PROBLEM. Kalshi lists new series faster than anyone reviews them, and every book that
selects candidates by structure rather than by an explicit allowlist picks them up the moment
they appear. Measured 2026-09-05 across the mmsell family: 81 of 400 traded series were in no
taxonomy at all, and on the LIVE canary `Dmmsell10` 20.2% of 30 days of trades were in series
nobody had ever reviewed — a share that RISES with each new season, because it tracks listings.

WHAT THIS IS NOT. It is not an edge filter and must never be sold as one. The unclassified
slice has been PROFITABLE (+$45.18 all-time across the family), and a graduated series can be
catastrophic — `KXNFLSPREAD` is classified, carries 382 settled markets, and has lost $166.55.
Graduation says "we know what this contract is and we have history on it", never "this contract
makes money". Conflating the two turns a governance rule into an unvalidated strategy.

TWO LEDGERS, DELIBERATELY SPLIT.

    DECISION   `series_manifest.json` — the state of each series, who reviewed it and when.
               A decision should be reviewable, and in this project a PR is how decisions get
               reviewed, so the decision lives in git and moves only by diff.
    OBSERVATION `series_observations` (Postgres, `models.SeriesObservation`) — when we first saw
               a series listed, when we last saw it, how many of its markets we have seen. Facts
               the worker accumulates; nothing here is a decision and nothing here admits
               anything to trade.

`scripts/series_registry_review.py` joins the two and prints the queue: series observed but
absent from the manifest (arrivals), graduated series with no rules review (the backlog), and
in-review series with enough history to be graduation candidates.

WHY JSON rather than a Python table. The ops-channel analysis scripts are self-contained
(stdlib + psycopg only — they run on a GitHub Actions runner that never installs this package),
so historically a table the worker and the scripts both need was DUPLICATED with a test
asserting the copies match (see `market_types.SERIES_TYPES` / `scripts/mmsell_market_types.py`).
A JSON file both sides read from disk has no second copy to drift, which matters far more for a
ledger that changes weekly than for a taxonomy that changes rarely.

THE STATES. Ordered weakest to strongest; a book naming a minimum state admits that state and
everything above it, which is the whole semantics of `admits`.

    IDENTIFIED  seen, and nothing more. No taxonomy entry, or no manifest row. Paper may trade
                it (that is how it accumulates the history a review needs); no book naming a
                higher minimum will.
    IN_REVIEW   classified by the market-type taxonomy but not yet admitted by a reviewer.
    GRADUATED   admitted. Tradeable anywhere, live included.

    BARRED      not on the ladder at all: a veto that fails EVERY minimum, including the
                `None` that admits everything else. A series someone looked at and refused has
                to be un-tradeable by a book that opted into nothing, or the refusal is
                decorative. Nothing reaches this state by accumulation.

`unclassified` is accepted as a legacy spelling of `identified` everywhere a state name is
parsed from config, an env var or a book spec, so existing `MMSELL_VARIANTS` strings and
`mmsell_live_min_tier` values keep their exact meaning.

THE GRADUATION BAR is two independent claims, and neither implies the other:

    mechanism understood   someone read Kalshi's settlement rules for the series and recorded
                           what they found (`rules_reviewed_at`, `rules_reviewed_by`)
    history sufficient     enough of our own settled markets to know it settles the way we think

PR #338's seed proved only the second and inferred the first, which is why all 138 rows
grandfathered on 2026-09-06 carry `rules_reviewed_at: null`. They trade live today — revoking
that wholesale would empty the live universe overnight — and they are the audit backlog,
ranked by live exposure so the series actually risking money get read first.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

IDENTIFIED = "identified"
IN_REVIEW = "in_review"
GRADUATED = "graduated"
BARRED = "barred"

#: Ordered weakest-to-strongest. `barred` is deliberately absent: it is a veto, not a rung.
STATE_ORDER: tuple[str, ...] = (IDENTIFIED, IN_REVIEW, GRADUATED)

#: Every state a manifest row may declare.
MANIFEST_STATES: frozenset[str] = frozenset({*STATE_ORDER, BARRED})

#: Spellings accepted from config/env/book specs, mapped to the canonical state. `unclassified`
#: is what PR #338 called the bottom rung; keeping it parseable means no deployed spec string
#: or env var changes meaning when the registry replaces it.
_ALIASES: dict[str, str] = {"unclassified": IDENTIFIED}

MANIFEST_PATH = Path(__file__).resolve().parent / "series_manifest.json"

_lock = threading.Lock()
_manifest: dict[str, dict[str, Any]] | None = None
_reasons: dict[str, str] = {}


def _load() -> dict[str, dict[str, Any]]:
    global _manifest, _reasons
    with _lock:
        if _manifest is None:
            doc = json.loads(MANIFEST_PATH.read_text())
            rows: dict[str, dict[str, Any]] = {}
            for row in doc.get("series", ()):
                series = str(row.get("series", "")).upper()
                state = str(row.get("state", ""))
                if not series:
                    raise ValueError("series_manifest.json: a row has no series")
                if state not in MANIFEST_STATES:
                    raise ValueError(
                        f"series_manifest.json: {series} declares unknown state {state!r} "
                        f"(known: {sorted(MANIFEST_STATES)})")
                if series in rows:
                    raise ValueError(f"series_manifest.json: {series} appears twice")
                rows[series] = row
            _reasons = dict(doc.get("reasons") or {})
            _manifest = rows
    return _manifest


def canonical_state(name: str | None) -> str | None:
    """The canonical spelling of a state name, or None if it names no state.

    Returning None for an unknown name rather than raising keeps `admits` fail-open for a book
    that names nothing, which is every book that has not opted in."""
    if not name:
        return None
    s = str(name).strip().lower()
    s = _ALIASES.get(s, s)
    return s if s in MANIFEST_STATES else None


def entry_for(series: str) -> dict[str, Any] | None:
    """The manifest row governing this series, by LONGEST matching prefix, or None.

    Longest-prefix rather than first-match so a specific series can be barred underneath a
    graduated family: `KXNFL` graduated and `KXNFLSPREAD` barred is a decision the manifest has
    to be able to express, and it can only do so if the more specific row wins."""
    s = (series or "").upper()
    if not s:
        return None
    rows = _load()
    best: dict[str, Any] | None = None
    best_len = -1
    for prefix, row in rows.items():
        if s.startswith(prefix) and len(prefix) > best_len:
            best, best_len = row, len(prefix)
    return best


def state_of(series: str) -> str:
    """The registry state for a series ticker.

    A series the market-type taxonomy does not know is IDENTIFIED whatever the manifest says —
    it cannot be graduated by a stray prefix row, because we would still not know how it
    settles. The two tables have to agree before a series trades live, and that is enforced
    here. BARRED is the one exception: an explicit refusal outranks everything, since a series
    someone rejected must not be rescued by a taxonomy gap."""
    from kalshi_bot.mmsell.market_types import UNCLASSIFIED as UNCLASSIFIED_TYPE
    from kalshi_bot.mmsell.market_types import classify

    row = entry_for(series)
    if row is not None and row.get("state") == BARRED:
        return BARRED
    if classify((series or "").upper()) == UNCLASSIFIED_TYPE:
        return IDENTIFIED
    if row is None:
        return IN_REVIEW
    return str(row.get("state"))


def admits(series: str, min_state: str | None) -> bool:
    """Whether a book requiring `min_state` may trade this series.

    `None` (or an unparseable name) admits everything, so the registry is inert for every book
    that has not opted in rather than silently narrowing the existing cohort. BARRED is the
    exception and refuses even then."""
    state = state_of(series)
    if state == BARRED:
        return False
    floor = canonical_state(min_state)
    if floor is None or floor == BARRED:
        return True
    return STATE_ORDER.index(state) >= STATE_ORDER.index(floor)


def reason_text(code: str | None) -> str:
    """The prose behind a row's `reason` code, or the code itself if the manifest defines none."""
    if not code:
        return ""
    _load()
    return _reasons.get(code, code)


def rows() -> tuple[dict[str, Any], ...]:
    """Every manifest row, series-sorted. For reporting; nothing admits on this."""
    return tuple(sorted(_load().values(), key=lambda r: r["series"]))


def unreviewed_graduated() -> tuple[str, ...]:
    """Graduated series whose settlement rules nobody has recorded reading — the audit backlog.

    Not an error state and not a bar: these trade live today. It is the list the registry owes
    a reviewer, and `scripts/series_registry_review.py` ranks it by live exposure so the series
    actually risking money are read first."""
    return tuple(r["series"] for r in rows()
                 if r.get("state") == GRADUATED and not r.get("rules_reviewed_at"))
