"""The shared series-addressed market fetch, and the warning it owes an operator.

SIX trackers address their universe the same way — a configured list of Kalshi
series tickers, each paginated through `get_markets(series_ticker=...)`: freeze,
pin15, wcprop, xgame, theta and tfav. Every one of them handled the FAILURE case
(an exception → warn and move on) and none handled the EMPTY case, because an
unknown, renamed or delisted series is not an error on Kalshi's side: it is HTTP
200 with `{"markets": []}`.

(The first draft of this module said "four". It was written from the books that
came to mind rather than from the source, and `theta` and `tfav` carry the same
loop. `tests/test_obs_funnel_wiring.py` now derives the set by scanning for the
call shape on every run, so the claim cannot drift from the code again.)

That is the whole defect recorded on XOS-000004. A book whose entire configured
universe has gone empty keeps logging a healthy-looking cycle line forever, and
the only signal anything is wrong arrives much later and from somewhere else —
the Control Tower noticing the experiment has no evidence, which reports the
symptom and cannot localise it.

This module does not decide anything. It fetches, counts, and says out loud what
came back. Selection rules, eligibility and configuration stay with each book.

Any NEW series-addressed book must fetch through here: the coverage test fails
the build otherwise, which is the durable form of "remember to add observability".
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from ..kalshi.errors import AuthError
from .funnel import (
    FETCH_EMPTY_UNIVERSE,
    FETCH_FAILED,
    FETCH_NO_SERIES,
    FETCH_OK,
    FETCH_PARTIAL_FAILURE,
    MAX_SERIES_LISTED,
    TRUNCATION_MARKER,
    sanitize_series,
)

logger = logging.getLogger(__name__)

#: While a book's fetch outcome is UNCHANGED, repeat its line at most this often.
#:
#: Not "log it once and go quiet": the ops log window is bounded, so a condition
#: that announced itself two days ago and has said nothing since is invisible to
#: an operator pulling logs today — which is XOS-000004 wearing a different hat.
#: Hourly keeps a standing problem present in any window somebody actually reads,
#: without paying one line per cycle. Observed 2026-09-05: the freeze book logged
#: `ENTIRE configured universe returned zero open markets` at ERROR on every
#: cycle across a weekend, because agricultural series are closed then — an
#: expected state, reported in the same voice and at the same volume as an
#: outage, which is how ERROR stops meaning anything.
REPEAT_AFTER_SECONDS = 3600.0

#: book -> (outcome signature, monotonic time it was last logged).
_LAST_OUTCOME: dict[str, tuple[tuple, float]] = {}


def reset_fetch_outcome_state() -> None:
    """Forget every book's last outcome.

    Process-global state is what makes the de-duplication work across cycles, and
    what would otherwise leak between tests: a suite that logs `freeze` twice
    would see the second call suppressed for reasons that have nothing to do with
    the case under test. `tests/conftest.py` calls this before every test."""
    _LAST_OUTCOME.clear()


def _outcome_signature(diagnosis: str, empty: list[str], failed: list[str]) -> tuple:
    """What has to change before the same book is worth another line.

    The series NAMES are part of it, not just the diagnosis: a universe that is
    empty for a different set of series is a different fact, and collapsing them
    would hide a book losing series one at a time behind an unchanged headline."""
    return (diagnosis, tuple(sorted(empty)), tuple(sorted(failed)))


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
    def failed_series(self) -> list[str]:
        return sorted(self.failed)

    @property
    def total_markets(self) -> int:
        return sum(self.per_series.values())

    @property
    def diagnosis(self) -> str:
        """How the fetch itself went, as a closed vocabulary.

        The distinction this makes is the whole point of the class. Zero markets
        is produced BOTH by a venue that has nothing for this book and by a fetch
        that never completed, and those have opposite remedies: the first is a
        question for whoever owns the book's universe, the second is an incident.
        Deriving "empty" from `total_markets == 0` alone conflates them, and would
        report an empty venue on a cycle where every request raised.

        `FETCH_FAILED` therefore requires BOTH that every configured series failed
        AND that nothing came back — a single series that failed on its second
        page after its first returned markets is a PARTIAL failure, not a total
        one, and the markets it did return are real.
        """
        if self.configured == 0:
            return FETCH_NO_SERIES
        failed = len([name for name in self.per_series if name in self.failed])
        if failed == 0:
            return FETCH_EMPTY_UNIVERSE if self.total_markets == 0 else FETCH_OK
        if failed == self.configured and self.total_markets == 0:
            return FETCH_FAILED
        return FETCH_PARTIAL_FAILURE

    @property
    def universe_empty(self) -> bool:
        """Every configured series was successfully asked, and all returned zero.

        Deliberately NOT `total_markets == 0`: that is true of a cycle in which
        every request raised, which is not a statement about the venue at all.
        """
        return self.diagnosis == FETCH_EMPTY_UNIVERSE

    @property
    def incomplete(self) -> bool:
        """Any configured series failed, so this cycle saw less than the universe."""
        return self.diagnosis in (FETCH_PARTIAL_FAILURE, FETCH_FAILED)


def _bounded_series_list(names: list[str]) -> str:
    shown = names[:MAX_SERIES_LISTED]
    hidden = len(names) - len(shown)
    listed = " ".join(shown)
    return f"{listed} {TRUNCATION_MARKER}{hidden}" if hidden > 0 else listed


def warn_on_fetch_outcome(
    book: str, result: SeriesFetchResult, log: logging.Logger | None = None,
    *, now: float | None = None,
) -> None:
    """Say, in the log MESSAGE, how this cycle's fetch actually went.

    Message text rather than structured fields, because the ops logs channel
    returns `message` and drops attributes — a warning an operator cannot read is
    not a warning. Emitted at most once per cycle per book, so a legitimately
    quiet series costs one line and not one per page.

    Four outcomes, deliberately worded so they cannot be confused at a glance:

    * every series succeeded and all returned zero -> ERROR, and it says the book
      cannot trade. This is the condition that makes a book inert.
    * every series FAILED -> ERROR, and it says the universe is UNKNOWN. It must
      not read as an empty venue: nothing was successfully asked.
    * some failed -> WARNING, naming the failed series and saying the cycle is
      incomplete, so a zero downstream is not mistaken for a venue answer.
    * some succeeded-but-empty, none failed -> WARNING naming them.

    No exception text ever reaches these messages. The per-series failure warning
    carries the error as a structured field, which the ops channel drops; the
    cycle line names only which series failed.
    """
    log = log or logger
    diagnosis = result.diagnosis
    empty, failed = result.empty_series, result.failed_series
    n = result.configured

    signature = _outcome_signature(diagnosis, empty, failed)
    stamp = time.monotonic() if now is None else now
    previous = _LAST_OUTCOME.get(book)

    if diagnosis in (FETCH_OK, FETCH_NO_SERIES) and not empty:
        # Recovery is worth exactly one line. Without it a book that went quiet
        # is indistinguishable from a book still broken and merely between
        # heartbeats, and the operator has to go and check.
        if previous is not None:
            _LAST_OUTCOME.pop(book, None)
            log.info(f"{book}: fetch RECOVERED — every configured series answered")
        return

    if previous is not None and previous[0] == signature:
        if stamp - previous[1] < REPEAT_AFTER_SECONDS:
            return                      # unchanged and recently said; stay quiet
        prefix = "STILL "               # unchanged but the window may have rolled
    else:
        prefix = ""
    _LAST_OUTCOME[book] = (signature, stamp)

    if diagnosis == FETCH_FAILED:
        log.error(
            f"{book}: {prefix}EVERY configured series FAILED to fetch ({len(failed)}/{n}) — "
            f"the universe is UNKNOWN this cycle, not empty; this is a transport "
            f"problem, not a venue answer: [{_bounded_series_list(failed)}]"
        )
        return

    if diagnosis == FETCH_EMPTY_UNIVERSE:
        log.error(
            f"{book}: {prefix}ENTIRE configured universe returned zero open markets "
            f"({len(empty)}/{n} series empty, none failed) — this book cannot trade: "
            f"[{_bounded_series_list(empty)}]"
        )
        return

    if diagnosis == FETCH_PARTIAL_FAILURE:
        detail = f"failed ({len(failed)}/{n}): [{_bounded_series_list(failed)}]"
        if empty:
            detail += f"; returned zero ({len(empty)}/{n}): [{_bounded_series_list(empty)}]"
        log.warning(
            f"{book}: {prefix}INCOMPLETE fetch — some configured series did not answer, so a "
            f"zero downstream is not a venue answer; {detail}"
        )
        return

    log.warning(
        f"{book}: {prefix}configured series returned zero open markets "
        f"({len(empty)}/{n} empty): [{_bounded_series_list(empty)}]"
    )


def fetch_markets_by_series(
    client,
    series: list[str],
    *,
    book: str,
    status: str = "open",
    limit: int = 200,
    max_pages: int = 4,
    min_close_ts: int | None = None,
    log: logging.Logger | None = None,
    warn: bool = True,
) -> SeriesFetchResult:
    """Paginate `get_markets` across `series`, counting what each one returned.

    Behaviour preserved from the per-book loops this replaces: `AuthError`
    propagates (credentials are a cycle-level problem), any other exception ends
    THAT series with a warning rather than the cycle, and pagination stops when
    Kalshi returns no cursor.

    What is new is only that the empty case is counted and reported.

    `warn=False` suppresses the CYCLE-level line for callers where an empty
    answer is the normal state (a settled-window scan, say). The per-series
    FAILURE warning still fires: a transport problem is never routine.
    """
    log = log or logger
    result = SeriesFetchResult()
    for name in series:
        key = sanitize_series(name)
        result.per_series.setdefault(key, 0)
        cursor: str | None = None
        for _ in range(max_pages):
            try:
                kwargs = dict(status=status, series_ticker=name, limit=limit, cursor=cursor)
                if min_close_ts is not None:
                    kwargs["min_close_ts"] = min_close_ts
                page = client.get_markets(**kwargs)
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
        warn_on_fetch_outcome(book, result, log)
    return result
