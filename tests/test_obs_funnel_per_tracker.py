"""XOS-000004 — the first-zero capability, proved per tracker, from production output.

`test_obs_funnel_wiring.py` proves the capability end to end for FREEZE by
driving the real tracker. This file proves it for EVERY series-addressed tracker
by exercising the exact mapper each one's cycle message calls, because that is
the string an operator actually reads through the ops logs channel.

Driving all six trackers end to end would mean six sets of client, spot-feed,
model and database fakes, and would mostly test those fakes. The mappers are the
thing under review: they are the statement about each tracker's semantics, and
they are what turns a cycle summary into the operator-visible line. A separate
structural test asserts each cycle log call really uses its mapper, so this
cannot drift into testing a function production does not run.
"""

from __future__ import annotations

import inspect
import pathlib
import re

import pytest

from kalshi_bot import main as m
from kalshi_bot.obs.funnel import NOT_RUN, SUMMARY_MARKER
from kalshi_bot.obs.series_fetch import SeriesFetchResult

REPO = pathlib.Path(__file__).resolve().parents[1]


class Summary:
    """A stand-in cycle summary: whatever counters a mapper asks for."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


def _fetch(*, per_series=None, failed=(), markets=0) -> SeriesFetchResult:
    result = SeriesFetchResult()
    result.per_series = dict(per_series or {})
    result.failed = list(failed)
    result.markets = [{"ticker": f"T{i}"} for i in range(markets)]
    return result


OK_FETCH = _fetch(per_series={"KXA": 5}, markets=5)
EMPTY_FETCH = _fetch(per_series={"KXA": 0, "KXB": 0})
FAILED_FETCH = _fetch(per_series={"KXA": 0, "KXB": 0}, failed=["KXA", "KXB"])
PARTIAL_FETCH = _fetch(per_series={"KXA": 0, "KXB": 0}, failed=["KXB"])


# ---------------------------------------------------------------------------
# The six states, per tracker
#
# Each row is (mapper, summary-kwargs) for one of the six situations the review
# requires production output to distinguish. The counters are set to whatever
# that tracker's own semantics make them.
# ---------------------------------------------------------------------------

#: (tracker, kwargs-per-situation). `_c` is the tracker's candidate counter name
#: and `_e` its eligibility counter, which differ by tracker on purpose.
_SHAPES = {
    "freeze": dict(e="freeze_eligible", c="candidates", a="opened", f="markets_seen"),
    "pin15": dict(e="in_window", c="priced", a="opened", f="markets_seen"),
    "theta": dict(e="in_window", c="in_band", a="opened", f="markets_seen"),
    "tfav": dict(e="in_window", c="in_band", a="opened", f="markets_seen"),
}


def _summary(tracker: str, *, fetch, fetched, eligible, candidates, actions) -> Summary:
    shape = _SHAPES[tracker]
    return Summary(**{
        "fetch": fetch,
        shape["f"]: fetched,
        shape["e"]: eligible,
        shape["c"]: candidates,
        shape["a"]: actions,
    })


SIX_STATE_TRACKERS = sorted(_SHAPES)


@pytest.mark.parametrize("tracker", SIX_STATE_TRACKERS)
def test_the_six_situations_are_distinguishable_in_production_output(tracker):
    """The capability the ticket asks for, per tracker, from the real mapper."""
    mapper = m.FUNNEL_MAPPERS[tracker]
    lines = {
        "fetch empty": mapper(
            _summary(tracker, fetch=EMPTY_FETCH, fetched=0, eligible=0,
                     candidates=0, actions=0)),
        "fetch failed": mapper(
            _summary(tracker, fetch=FAILED_FETCH, fetched=0, eligible=0,
                     candidates=0, actions=0)),
        "fetch incomplete": mapper(
            _summary(tracker, fetch=PARTIAL_FETCH, fetched=0, eligible=0,
                     candidates=0, actions=0)),
        "none eligible": mapper(
            _summary(tracker, fetch=OK_FETCH, fetched=9, eligible=0,
                     candidates=0, actions=0)),
        "no candidates": mapper(
            _summary(tracker, fetch=OK_FETCH, fetched=9, eligible=4,
                     candidates=0, actions=0)),
        "no actions": mapper(
            _summary(tracker, fetch=OK_FETCH, fetched=9, eligible=4,
                     candidates=2, actions=0)),
        "actions": mapper(
            _summary(tracker, fetch=OK_FETCH, fetched=9, eligible=4,
                     candidates=2, actions=1)),
    }
    assert all(SUMMARY_MARKER in line for line in lines.values())
    # Every situation must be readable as a DIFFERENT line.
    assert len(set(lines.values())) == len(lines), lines

    assert "state=NO_MARKETS " in lines["fetch empty"] + " "
    assert "state=FETCH_FAILED" in lines["fetch failed"]
    assert "state=NO_MARKETS_INCOMPLETE" in lines["fetch incomplete"]
    assert "state=NO_ELIGIBLE" in lines["none eligible"]
    assert "state=NO_CANDIDATES" in lines["no candidates"]
    assert "state=NO_ACTIONS" in lines["no actions"]
    assert "state=ACTIONS" in lines["actions"]


@pytest.mark.parametrize("tracker", SIX_STATE_TRACKERS)
def test_a_failed_fetch_is_never_reported_as_an_empty_venue(tracker):
    mapper = m.FUNNEL_MAPPERS[tracker]
    line = mapper(_summary(tracker, fetch=FAILED_FETCH, fetched=0, eligible=0,
                           candidates=0, actions=0))
    assert "NO_MARKETS" not in line
    assert "failed_series=2/2" in line


# ---------------------------------------------------------------------------
# wcprop — the SIGNAL stage only runs while the settled-match trigger is open
# ---------------------------------------------------------------------------


def _wcprop(*, triggered, winner_rungs, quoted, moved, opened, fetch=OK_FETCH) -> Summary:
    return Summary(fetch=fetch, triggered=triggered, winner_rungs=winner_rungs,
                   quoted=quoted, moved=moved, opened=opened)


def test_wcprop_untriggered_cycle_reports_the_signal_stage_as_not_run():
    """The gate is closed, so `moved=0` would be a finding about code that never ran."""
    line = m._wcprop_funnel(
        _wcprop(triggered=False, winner_rungs=20, quoted=18, moved=0, opened=0)
    )
    assert "state=CANDIDATES_NOT_RUN" in line
    assert "candidates=NOT_RUN" in line and "actions=NOT_RUN" in line
    assert "not_run=candidates,actions" in line
    # ...and it must not be confused with a cycle that looked and found nothing.
    assert "NO_CANDIDATES" not in line
    assert "NO_MARKETS" not in line


def test_wcprop_triggered_cycle_reports_real_zeros():
    line = m._wcprop_funnel(
        _wcprop(triggered=True, winner_rungs=20, quoted=18, moved=0, opened=0)
    )
    assert "state=NO_CANDIDATES" in line
    assert "NOT_RUN" not in line


def test_wcprop_six_situations_are_distinguishable():
    lines = {
        "fetch empty": m._wcprop_funnel(
            _wcprop(triggered=True, winner_rungs=0, quoted=0, moved=0, opened=0,
                    fetch=EMPTY_FETCH)),
        "fetch failed": m._wcprop_funnel(
            _wcprop(triggered=True, winner_rungs=0, quoted=0, moved=0, opened=0,
                    fetch=FAILED_FETCH)),
        "none eligible": m._wcprop_funnel(
            _wcprop(triggered=True, winner_rungs=20, quoted=0, moved=0, opened=0)),
        "no candidates": m._wcprop_funnel(
            _wcprop(triggered=True, winner_rungs=20, quoted=18, moved=0, opened=0)),
        "no actions": m._wcprop_funnel(
            _wcprop(triggered=True, winner_rungs=20, quoted=18, moved=3, opened=0)),
        "actions": m._wcprop_funnel(
            _wcprop(triggered=True, winner_rungs=20, quoted=18, moved=3, opened=1)),
        "signal stage skipped": m._wcprop_funnel(
            _wcprop(triggered=False, winner_rungs=20, quoted=18, moved=0, opened=0)),
    }
    assert len(set(lines.values())) == len(lines), lines
    assert "state=NO_MARKETS" in lines["fetch empty"]
    assert "state=FETCH_FAILED" in lines["fetch failed"]
    assert "state=NO_ELIGIBLE" in lines["none eligible"]
    assert "state=NO_CANDIDATES" in lines["no candidates"]
    assert "state=NO_ACTIONS" in lines["no actions"]
    assert "state=ACTIONS" in lines["actions"]
    assert "state=CANDIDATES_NOT_RUN" in lines["signal stage skipped"]


# ---------------------------------------------------------------------------
# xgame — discovery is THROTTLED, so intake does not run on most cycles
# ---------------------------------------------------------------------------


def _xgame(*, discovered, kalshi_games, matches_active, polled, fetch=OK_FETCH) -> Summary:
    return Summary(fetch=fetch, discovered=discovered, kalshi_games=kalshi_games,
                   matches_active=matches_active, polled=polled)


def test_xgame_cycle_without_discovery_is_never_reported_as_no_markets():
    """The conditional-discovery case, stated as the negative that matters.

    Discovery is throttled to `xgame_discovery_minutes`, so the overwhelming
    majority of cycles never ask the venue. Zero fetched markets on those cycles
    says nothing about Kalshi, and must not read as though it did.
    """
    line = m._xgame_funnel(
        _xgame(discovered=False, kalshi_games=0, matches_active=12, polled=12,
               fetch=_fetch())
    )
    assert "NO_MARKETS" not in line
    assert "fetched=NOT_RUN" in line and "eligible=NOT_RUN" in line
    assert "not_run=fetched,eligible" in line


def test_xgame_poll_only_cycle_still_reports_that_it_polled():
    """A skipped intake stage must not mask a working downstream."""
    line = m._xgame_funnel(
        _xgame(discovered=False, kalshi_games=0, matches_active=12, polled=12,
               fetch=_fetch())
    )
    assert "state=ACTIONS" in line
    assert "candidates=12" in line and "actions=12" in line


def test_xgame_broken_polling_still_localises_even_with_discovery_skipped():
    """The other half: a NOT_RUN stage is skipped, not treated as the blocker, so
    a real downstream zero is still the diagnosis."""
    no_matches = m._xgame_funnel(
        _xgame(discovered=False, kalshi_games=0, matches_active=0, polled=0,
               fetch=_fetch()))
    assert "state=NO_CANDIDATES" in no_matches

    nothing_polled = m._xgame_funnel(
        _xgame(discovered=False, kalshi_games=0, matches_active=12, polled=0,
               fetch=_fetch()))
    assert "state=NO_ACTIONS" in nothing_polled


def test_xgame_discovery_cycle_reports_the_venue_normally():
    empty = m._xgame_funnel(
        _xgame(discovered=True, kalshi_games=0, matches_active=0, polled=0,
               fetch=EMPTY_FETCH))
    assert "state=NO_MARKETS" in empty
    assert "NOT_RUN" not in empty

    failed = m._xgame_funnel(
        _xgame(discovered=True, kalshi_games=0, matches_active=0, polled=0,
               fetch=FAILED_FETCH))
    assert "state=FETCH_FAILED" in failed

    ineligible = m._xgame_funnel(
        _xgame(discovered=True, kalshi_games=0, matches_active=0, polled=0,
               fetch=OK_FETCH))
    assert "state=NO_ELIGIBLE" in ineligible


# ---------------------------------------------------------------------------
# The structural invariant: mapper exists, and the cycle message uses it
# ---------------------------------------------------------------------------

#: Every series-addressed tracker, derived in the wiring test from the call
#: shape; restated here only to assert the mapper table matches it exactly.
EXPECTED_TRACKERS = {"freeze", "pin15", "theta", "tfav", "wcprop", "xgame"}


def test_every_series_addressed_tracker_has_a_funnel_mapper():
    assert set(m.FUNNEL_MAPPERS) == EXPECTED_TRACKERS


@pytest.mark.parametrize("tracker", sorted(EXPECTED_TRACKERS))
def test_each_cycle_message_embeds_its_own_mapper(tracker):
    """The mappers are only worth testing if production actually calls them.

    Asserts the tracker's cycle log call interpolates its mapper into the MESSAGE
    — the field the ops logs channel returns — rather than passing it as a
    structured field, which would be dropped before an operator saw it.
    """
    source = inspect.getsource(m)
    mapper = f"_{tracker}_funnel(summ)"
    assert mapper in source, f"{tracker}'s mapper is never called"
    # It must appear inside an f-string that is a log MESSAGE argument.
    pattern = re.compile(r'f"[^"]*\{' + re.escape(mapper) + r'\}[^"]*"')
    assert pattern.search(source), (
        f"{tracker}'s funnel must be interpolated into the log message, not a field"
    )


def test_every_mapper_survives_a_summary_it_does_not_understand():
    """Observability must never be the thing that stops a trading cycle."""
    for name, mapper in m.FUNNEL_MAPPERS.items():
        assert mapper(object()) == "", f"{name} raised instead of degrading"


def test_not_run_is_reachable_only_through_the_documented_sentinel():
    """Guard against a stray string sneaking into the closed vocabulary."""
    line = m._funnel_line(None, fetched=NOT_RUN, eligible=1, candidates=1, actions=1)
    assert "fetched=NOT_RUN" in line
    text = m._funnel_line(None, fetched="NOT_RUN", eligible=1, candidates=1, actions=1)
    assert "fetched=0" in text          # a mere string is coerced, not honoured
    assert "not_run=" not in text
