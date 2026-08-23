"""XOS-000004 — the capability, end to end: can an operator localise the first zero?

The unit tests prove the renderer is correct and bounded. These prove the thing
the ticket actually asks for: that a book's PRODUCTION-VISIBLE output — the log
message, which is the only field the ops logs channel returns — distinguishes a
book that saw nothing from one that saw markets and rejected them.

The FREEZE book is the subject because it is the observed instance, but nothing
here asserts anything about its universe or its entry rules: those belong to
XOS-000003 and to Research Lab.
"""

from __future__ import annotations

import logging
import pathlib
import re

import pytest

from kalshi_bot.config import Settings
from kalshi_bot.freeze.tracker import FreezeTracker
from kalshi_bot.main import _freeze_funnel, _funnel_line
from kalshi_bot.obs.funnel import MAX_SUMMARY_CHARS, SUMMARY_MARKER


def _settings(**over) -> Settings:
    base = dict(
        kalshi_api_key_id="k", kalshi_private_key="p", database_url="sqlite://",
        freeze_series="KXCORN,KXWHEAT", freeze_enabled=True,
    )
    base.update(over)
    return Settings(**base)


class _Client:
    def __init__(self, by_series, book=None):
        self.by_series = by_series
        self.book = book

    def get_markets(self, *, status="open", series_ticker=None, limit=200, cursor=None):
        return {"markets": list(self.by_series.get(series_ticker, [])), "cursor": None}

    def get_orderbook(self, ticker, depth=None):
        if self.book is None:
            raise RuntimeError("no book")
        return self.book


class _Session:
    """Enough of a session for the tracker's repository reads; writes are refused."""

    def add(self, *a, **k):                     # pragma: no cover - guard
        raise AssertionError("a funnel test must not write")

    def flush(self):                            # pragma: no cover - guard
        raise AssertionError("a funnel test must not write")


@pytest.fixture(autouse=True)
def _no_db(monkeypatch):
    from kalshi_bot import repository as repo

    monkeypatch.setattr(repo, "count_open_paper_positions", lambda *a, **k: 0)
    monkeypatch.setattr(repo, "open_paper_position_tickers", lambda *a, **k: set())


def _corn(ticker="KXCORN-26DEC-T500"):
    return {"ticker": ticker, "title": "Corn price", "close_time": "2030-01-05T18:00:00Z"}


def test_state_1_no_markets_returned_is_visible_in_the_cycle_message(caplog):
    tracker = FreezeTracker(_Client({"KXCORN": [], "KXWHEAT": []}), _settings())
    with caplog.at_level(logging.WARNING):
        summ = tracker.run_once(_Session())

    line = _freeze_funnel(summ)
    assert SUMMARY_MARKER in line
    assert "state=NO_MARKETS" in line and "first_zero=fetched" in line
    assert "fetched=0" in line
    # ...and the operator is told WHICH configured series are empty.
    assert "empty_series=2/2" in line
    assert "KXCORN" in line and "KXWHEAT" in line
    assert any("ENTIRE" in r.message for r in caplog.records)


def test_state_2_markets_returned_but_rejected_by_eligibility():
    """A market the classifier does not admit: fetched > 0, eligible == 0."""
    not_a_commodity = {"ticker": "KXNOPE-1", "title": "Some election market",
                       "close_time": "2030-01-05T18:00:00Z"}
    tracker = FreezeTracker(_Client({"KXCORN": [not_a_commodity]}), _settings())
    summ = tracker.run_once(_Session())

    line = _freeze_funnel(summ)
    assert "state=NO_ELIGIBLE" in line and "first_zero=eligible" in line
    assert "fetched=1" in line and "eligible=0" in line


def test_state_3_eligible_markets_that_never_become_candidates():
    """Eligible, but no usable orderbook — the book never gets to decide."""
    tracker = FreezeTracker(_Client({"KXCORN": [_corn()]}, book=None), _settings())
    summ = tracker.run_once(_Session())

    line = _freeze_funnel(summ)
    assert "eligible=1" in line
    assert "candidates=0" in line
    assert "state=NO_CANDIDATES" in line


def test_the_three_states_produce_three_different_lines():
    """The capability check: these were indistinguishable before this ticket."""
    empty = FreezeTracker(_Client({"KXCORN": []}), _settings(freeze_series="KXCORN"))
    ineligible = FreezeTracker(
        _Client({"KXCORN": [{"ticker": "KXNOPE-1", "title": "election",
                             "close_time": "2030-01-05T18:00:00Z"}]}),
        _settings(freeze_series="KXCORN"),
    )
    no_candidate = FreezeTracker(
        _Client({"KXCORN": [_corn()]}, book=None), _settings(freeze_series="KXCORN")
    )
    lines = {
        _freeze_funnel(t.run_once(_Session()))
        for t in (empty, ineligible, no_candidate)
    }
    assert len(lines) == 3


def test_the_funnel_line_is_bounded_and_carries_no_ticker():
    tracker = FreezeTracker(
        _Client({"KXCORN": [_corn(f"KXCORN-26DEC-T{i}") for i in range(50)]}), _settings()
    )
    line = _freeze_funnel(tracker.run_once(_Session()))
    assert len(line) <= MAX_SUMMARY_CHARS
    assert "26DEC" not in line


def test_the_funnel_line_never_breaks_a_cycle():
    """Observability is not allowed to be the thing that stops trading."""

    class Boom:
        @property
        def diagnosis(self):
            raise RuntimeError("boom")

    assert _funnel_line(Boom(), fetched=1, eligible=1, candidates=1, actions=1) == ""

    class NoFields:
        pass

    # A mapper handed a summary missing its counters degrades to an empty string
    # rather than taking the cycle down with it.
    assert _freeze_funnel(NoFields()) == ""


#: Every tracker that addresses its universe with `get_markets(series_ticker=...)`.
#: Six, not the four the first draft of this PR claimed — `theta` and `tfav`
#: carry the identical loop and were missed until this list was derived from
#: the source instead of from memory.
EVERY_SERIES_ADDRESSED_BOOK = ("freeze", "pin15", "wcprop", "xgame", "theta", "tfav")


@pytest.mark.parametrize("book", EVERY_SERIES_ADDRESSED_BOOK)
def test_every_series_addressed_book_uses_the_shared_fetch(book):
    """The coverage claim, asserted rather than described.

    The defect is a property of the series-addressed fetch SHAPE, so a fix that
    reached only some of the books that have that shape would leave the rest
    failing silently — and would make "cross-book" a claim the code does not
    support. This enumerates them and checks each one.
    """
    import importlib
    import inspect

    module = importlib.import_module(f"kalshi_bot.{book}.tracker")
    source = inspect.getsource(module)
    assert "fetch_markets_by_series" in source, f"{book} still fetches series on its own"
    # ...and no book keeps a hand-rolled series loop, which is where the blind
    # spot lived: an empty HTTP 200 that no `except` clause can see.
    assert "series_ticker=series" not in source, f"{book} still has a raw series loop"
    assert "series_ticker=s." not in source, f"{book} still has a raw series loop"


#: `get_markets(... series_ticker=...)` — the exact call shape whose empty
#: HTTP 200 is invisible. Deliberately narrow: `get_events(series_ticker=...)`
#: returns a different object and needs its own helper (see the test below).
_GET_MARKETS_BY_SERIES = re.compile(
    r"get_markets\((?:[^()]|\([^()]*\))*series_ticker\s*=", re.S
)


def test_no_tracker_anywhere_fetches_by_series_outside_the_shared_helper():
    """The durable form: catches a SEVENTH book added later without the helper.

    The enumeration above can go stale the moment someone writes a new tracker.
    This derives the set from the source every run, so the coverage claim cannot
    quietly become false again — which is how it became false the first time.
    """
    repo = pathlib.Path(__file__).resolve().parents[1]
    # The client module DEFINES get_markets; it is the layer the helper calls, not
    # a caller that could adopt it.
    defines_the_call = {"kalshi_bot/kalshi/client.py"}
    offenders = []
    for path in sorted((repo / "kalshi_bot").rglob("*.py")):
        rel = path.relative_to(repo).as_posix()
        if rel in defines_the_call:
            continue
        text = path.read_text()
        if _GET_MARKETS_BY_SERIES.search(text) and "fetch_markets_by_series" not in text:
            offenders.append(rel)
    assert not offenders, f"series-addressed fetches bypassing the shared helper: {offenders}"


def test_every_series_addressed_tracker_publishes_its_diagnosis_in_message_text():
    """The second half of coverage, derived from the source on every run.

    Using the shared fetch buys INTAKE observability: a book can say its series
    came back empty or failed. It does not buy the first-zero capability — a
    successful fetch whose first zero lands at eligibility, candidate generation
    or action generation is still unlocalisable unless the cycle MESSAGE carries
    the funnel. This asserts both properties for every tracker that has the
    series-addressed shape, so the two can never drift apart again.
    """
    from kalshi_bot.main import FUNNEL_MAPPERS

    repo = pathlib.Path(__file__).resolve().parents[1]
    main_source = (repo / "kalshi_bot/main.py").read_text()

    discovered = set()
    for path in sorted((repo / "kalshi_bot").rglob("tracker.py")):
        if "fetch_markets_by_series" in path.read_text():
            discovered.add(path.parent.name)

    assert discovered == set(EVERY_SERIES_ADDRESSED_BOOK), (
        f"the series-addressed set moved: {sorted(discovered)}"
    )
    for tracker in sorted(discovered):
        assert tracker in FUNNEL_MAPPERS, (
            f"{tracker} fetches through the shared helper but has no funnel mapper — "
            "that is intake observability without first-zero capability"
        )
        call = f"_{tracker}_funnel(summ)"
        assert re.search(r'f"[^"]*\{' + re.escape(call) + r'\}[^"]*"', main_source), (
            f"{tracker}'s funnel is not interpolated into its cycle log MESSAGE, so the "
            "ops logs channel (which returns message text only) would never show it"
        )


def test_the_two_remaining_uncovered_shapes_are_recorded_not_omitted():
    """Two known gaps, asserted rather than left as a silence.

    Both address a universe by series and neither fits the `get_markets` helper:

    * `weather` uses `get_events(series_ticker=...)`, which returns
      events-with-nested-markets rather than a market list;
    * the evo fleet's `_scan_universe` goes through its own market-data adapter
      (`list_markets`) on a different worker, and swallows a failing series to
      `[]` — the same blind spot in a different abstraction.

    They are named here so the coverage claim stays honest and so neither can be
    quietly forgotten. When an events-shaped helper lands, each moves into
    EVERY_SERIES_ADDRESSED_BOOK and its clause here is deleted.
    """
    repo = pathlib.Path(__file__).resolve().parents[1]

    weather = (repo / "kalshi_bot/weather/tracker.py").read_text()
    assert "get_events(" in weather
    assert not _GET_MARKETS_BY_SERIES.search(weather), (
        "weather now uses get_markets by series — it must adopt the shared helper "
        "and move into EVERY_SERIES_ADDRESSED_BOOK"
    )

    evo = (repo / "kalshi_bot/evo/orchestrator.py").read_text()
    assert "list_markets(" in evo
    assert not _GET_MARKETS_BY_SERIES.search(evo), (
        "evo now uses get_markets by series — it must adopt the shared helper"
    )


def test_the_books_do_not_change_their_universe_or_eligibility_rules():
    """This ticket is observability. The scientific contract is XOS-000003's."""
    defaults = Settings(
        kalshi_api_key_id="k", kalshi_private_key="p", database_url="sqlite://"
    )
    assert defaults.freeze_series == "KXCORN,KXWHEAT,KXSOYBEAN,KXCOFFEE,KXSUGAR,KXCOCOA,KXCOTTON"
    assert defaults.freeze_min_discount_cents == 3.0
    assert defaults.freeze_books.startswith("freeze1:dark=1;")


def test_a_failed_fetch_is_not_reported_as_an_empty_venue(caplog):
    """End to end: every configured series raises, so the cycle must not claim
    the venue returned nothing (the review's blocker)."""

    class Broken:
        def get_markets(self, **kw):
            raise RuntimeError("connection reset")

    tracker = FreezeTracker(Broken(), _settings())
    with caplog.at_level(logging.WARNING):
        summ = tracker.run_once(_Session())

    line = _freeze_funnel(summ)
    assert "state=FETCH_FAILED" in line
    assert "NO_MARKETS" not in line
    assert "fetch=FETCH_FAILED" in line
    assert "failed_series=2/2" in line
    assert any("EVERY configured series FAILED" in r.message for r in caplog.records)
    assert not any("ENTIRE configured universe" in r.message for r in caplog.records)
    # No exception text reaches the operator-visible cycle line.
    assert "connection reset" not in line


def test_a_partial_fetch_failure_is_visible_in_the_cycle_line(caplog):
    class OneBroken:
        def get_markets(self, *, series_ticker=None, **kw):
            if series_ticker == "KXWHEAT":
                raise RuntimeError("boom")
            return {"markets": [], "cursor": None}

    tracker = FreezeTracker(OneBroken(), _settings())
    with caplog.at_level(logging.WARNING):
        summ = tracker.run_once(_Session())

    line = _freeze_funnel(summ)
    assert "state=NO_MARKETS_INCOMPLETE" in line
    assert "empty_series=1/2" in line and "failed_series=1/2" in line
    assert any("INCOMPLETE fetch" in r.message for r in caplog.records)
