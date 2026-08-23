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

import pytest

from kalshi_bot.config import Settings
from kalshi_bot.freeze.tracker import FreezeTracker
from kalshi_bot.main import _funnel_line
from kalshi_bot.obs.funnel import SUMMARY_MARKER


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

    line = _funnel_line(summ)
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

    line = _funnel_line(summ)
    assert "state=NO_ELIGIBLE" in line and "first_zero=eligible" in line
    assert "fetched=1" in line and "eligible=0" in line


def test_state_3_eligible_markets_that_never_become_candidates():
    """Eligible, but no usable orderbook — the book never gets to decide."""
    tracker = FreezeTracker(_Client({"KXCORN": [_corn()]}, book=None), _settings())
    summ = tracker.run_once(_Session())

    line = _funnel_line(summ)
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
        _funnel_line(t.run_once(_Session()))
        for t in (empty, ineligible, no_candidate)
    }
    assert len(lines) == 3


def test_the_funnel_line_is_bounded_and_carries_no_ticker():
    tracker = FreezeTracker(
        _Client({"KXCORN": [_corn(f"KXCORN-26DEC-T{i}") for i in range(50)]}), _settings()
    )
    line = _funnel_line(tracker.run_once(_Session()))
    assert len(line) <= 400
    assert "26DEC" not in line


def test_the_funnel_line_never_breaks_a_cycle():
    """Observability is not allowed to be the thing that stops trading."""

    class Broken:
        markets_seen = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))

    assert _funnel_line(Broken()) == ""


def test_pin15_uses_the_same_shared_helper_so_the_fix_is_cross_book():
    """The defect is a property of the series-addressed shape, not of one book."""
    import inspect

    from kalshi_bot.pin15 import tracker as pin15

    source = inspect.getsource(pin15)
    assert "fetch_markets_by_series" in source
    assert "warn_on_empty_series" in source
    # ...and the old blind loop is gone from both books.
    from kalshi_bot.freeze import tracker as freeze

    assert "series_ticker=series" not in inspect.getsource(freeze)


def test_the_books_do_not_change_their_universe_or_eligibility_rules():
    """This ticket is observability. The scientific contract is XOS-000003's."""
    defaults = Settings(
        kalshi_api_key_id="k", kalshi_private_key="p", database_url="sqlite://"
    )
    assert defaults.freeze_series == "KXCORN,KXWHEAT,KXSOYBEAN,KXCOFFEE,KXSUGAR,KXCOCOA,KXCOTTON"
    assert defaults.freeze_min_discount_cents == 3.0
    assert defaults.freeze_books.startswith("freeze1:dark=1;")
