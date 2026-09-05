"""XOS-000004 — the empty-series warning, the funnel, and the disclosure bounds.

Every test here is about what an OPERATOR can see. The book's trading behaviour
is not the subject and must not change: the counters are observations, and a
test that had to alter an eligibility rule to pass would be testing the wrong
thing.
"""

from __future__ import annotations

import logging

import pytest

from kalshi_bot.kalshi.errors import AuthError
from kalshi_bot.obs.funnel import (
    FETCH_EMPTY_UNIVERSE,
    FETCH_FAILED,
    FETCH_NO_SERIES,
    FETCH_OK,
    FETCH_PARTIAL_FAILURE,
    FUNNEL_COUNTERS,
    FUNNEL_STAGES,
    MAX_SERIES_LISTED,
    MAX_SUMMARY_CHARS,
    SUMMARY_MARKER,
    TRUNCATION_MARKER,
    FunnelState,
    diagnose,
    first_zero_stage,
    funnel_summary,
    sanitize_series,
)
from kalshi_bot.obs.series_fetch import fetch_markets_by_series, warn_on_fetch_outcome


class FakeClient:
    """Kalshi's real shape for an unknown series: HTTP 200 with an empty list."""

    def __init__(self, pages: dict[str, list[dict]] | None = None, raise_for: set | None = None):
        self.pages = pages or {}
        self.raise_for = raise_for or set()
        self.calls: list[str] = []

    def get_markets(self, *, status="open", series_ticker=None, limit=200, cursor=None):
        self.calls.append(series_ticker)
        if series_ticker in self.raise_for:
            raise RuntimeError("boom")
        return {"markets": list(self.pages.get(series_ticker, [])), "cursor": None}


def _mkt(ticker: str) -> dict:
    return {"ticker": ticker, "title": ticker}


# --- the empty HTTP-200 case: the defect itself -------------------------------


def test_empty_http_200_series_is_detected_not_mistaken_for_a_healthy_fetch(caplog):
    """The whole ticket: 200 + [] is not an exception, so nothing used to fire."""
    client = FakeClient({"KXCORN": []})
    with caplog.at_level(logging.WARNING):
        result = fetch_markets_by_series(client, ["KXCORN"], book="freeze")

    assert result.markets == []
    assert result.per_series == {"KXCORN": 0}
    assert result.empty_series == ["KXCORN"]
    assert result.failed == []          # empty is NOT a failure
    assert "KXCORN" in caplog.text


def test_a_partially_empty_universe_names_only_the_empty_series(caplog):
    client = FakeClient({"KXCORN": [], "KXWHEAT": [_mkt("KXWHEAT-1")], "KXSUGAR": []})
    with caplog.at_level(logging.WARNING):
        result = fetch_markets_by_series(
            client, ["KXCORN", "KXWHEAT", "KXSUGAR"], book="freeze"
        )

    assert result.empty_series == ["KXCORN", "KXSUGAR"]
    assert result.universe_empty is False
    assert len(result.markets) == 1
    record = next(r for r in caplog.records if "zero open markets" in r.message)
    assert record.levelno == logging.WARNING          # not the louder one
    assert "KXCORN" in record.message and "KXSUGAR" in record.message
    assert "KXWHEAT" not in record.message


def test_an_entirely_empty_universe_is_louder_and_says_the_book_cannot_trade(caplog):
    """'Some series are quiet' and 'this book has nothing to look at' must not
    read the same at a glance — the second is why FREEZE ran blind for nine days."""
    series = ["KXCORN", "KXWHEAT", "KXSOYBEAN"]
    client = FakeClient({s: [] for s in series})
    with caplog.at_level(logging.WARNING):
        result = fetch_markets_by_series(client, series, book="freeze")

    assert result.universe_empty is True
    record = next(r for r in caplog.records if "ENTIRE" in r.message)
    assert record.levelno == logging.ERROR
    assert "cannot trade" in record.message
    assert "3/3" in record.message


def test_a_healthy_universe_warns_about_nothing(caplog):
    client = FakeClient({"KXCORN": [_mkt("A")], "KXWHEAT": [_mkt("B")]})
    with caplog.at_level(logging.WARNING):
        result = fetch_markets_by_series(client, ["KXCORN", "KXWHEAT"], book="freeze")

    assert result.empty_series == []
    assert [r for r in caplog.records if "zero open markets" in r.message] == []


def test_one_warning_per_cycle_not_one_per_series(caplog):
    """Signal-to-noise: a quiet configuration costs one line, not one per series."""
    series = [f"KX{i:02d}" for i in range(20)]
    client = FakeClient({s: [] for s in series})
    with caplog.at_level(logging.WARNING):
        fetch_markets_by_series(client, series, book="freeze")

    assert len([r for r in caplog.records if "zero open markets" in r.message]) == 1


# --- failure is not emptiness -------------------------------------------------


def test_a_failing_series_is_recorded_as_failed_rather_than_empty(caplog):
    """A transport problem and a venue answer have different remedies."""
    client = FakeClient({"KXCORN": [], "KXWHEAT": []}, raise_for={"KXWHEAT"})
    with caplog.at_level(logging.WARNING):
        result = fetch_markets_by_series(client, ["KXCORN", "KXWHEAT"], book="freeze")

    assert result.failed == ["KXWHEAT"]
    assert result.empty_series == ["KXCORN"]        # the failure is excluded


def test_auth_errors_still_propagate():
    """Preserved from the per-book loops: credentials are a cycle-level problem."""
    client = FakeClient({"KXCORN": []})
    client.get_markets = lambda **kw: (_ for _ in ()).throw(AuthError("nope"))
    with pytest.raises(AuthError):
        fetch_markets_by_series(client, ["KXCORN"], book="freeze")


def test_pagination_stops_on_a_missing_cursor_and_counts_every_page():
    pages = [
        {"markets": [_mkt("A")], "cursor": "c1"},
        {"markets": [_mkt("B")], "cursor": None},
    ]

    class Paged:
        def __init__(self):
            self.n = 0

        def get_markets(self, **kw):
            page = pages[self.n]
            self.n += 1
            return page

    result = fetch_markets_by_series(Paged(), ["KXCORN"], book="freeze", max_pages=5)
    assert result.per_series == {"KXCORN": 2}


# --- the four states an operator must be able to tell apart -------------------


@pytest.mark.parametrize(
    "counts,stage,state",
    [
        (dict(fetched=0, eligible=0, candidates=0, actions=0), "fetched", "NO_MARKETS"),
        (dict(fetched=9, eligible=0, candidates=0, actions=0), "eligible", "NO_ELIGIBLE"),
        (dict(fetched=9, eligible=4, candidates=0, actions=0), "candidates", "NO_CANDIDATES"),
        (dict(fetched=9, eligible=4, candidates=2, actions=0), "actions", "NO_ACTIONS"),
        (dict(fetched=9, eligible=4, candidates=2, actions=1), None, "ACTIONS"),
    ],
)
def test_first_zero_stage_localises_each_of_the_four_states(counts, stage, state):
    funnel = FunnelState.of(**counts)
    assert first_zero_stage(funnel) == stage
    assert diagnose(funnel) == state


def test_downstream_filter_zero_is_distinguishable_from_no_markets():
    """The pair the operator could not previously separate at all."""
    nothing_fetched = funnel_summary(FunnelState.of(fetched=0))
    rejected_downstream = funnel_summary(
        FunnelState.of(fetched=412, eligible=51, candidates=7, actions=0)
    )
    assert "state=NO_MARKETS" in nothing_fetched
    assert "state=NO_ACTIONS" in rejected_downstream
    assert nothing_fetched != rejected_downstream


def test_unknown_counter_names_are_refused_rather_than_silently_zero():
    """A typo that produced a zero stage would fabricate a diagnosis."""
    with pytest.raises(ValueError):
        FunnelState.of(fetched=1, elligible=2)


def test_counters_are_coerced_to_non_negative_ints():
    funnel = FunnelState.of(fetched="12", eligible=-4, candidates=None, actions=3.9)
    assert funnel.as_dict() == {"fetched": 12, "eligible": 0, "candidates": 0, "actions": 3}


# --- bounded, publishable output ---------------------------------------------


def test_the_summary_carries_the_public_output_marker():
    line = funnel_summary(FunnelState.of(fetched=1, eligible=1, candidates=1, actions=1))
    assert line.startswith(SUMMARY_MARKER)


def test_the_summary_renders_only_allowlisted_field_names():
    """Nothing but the four counters and the two bounded series fields."""
    line = funnel_summary(
        FunnelState.of(fetched=5, eligible=2, candidates=1, actions=1),
        fetch=FETCH_PARTIAL_FAILURE,
        empty_series=["KXCORN"],
        failed_series=["KXWHEAT"],
        configured_series=3,
    )
    keys = {token.split("=", 1)[0] for token in line.split() if "=" in token}
    assert keys <= FUNNEL_COUNTERS | {
        "state", "first_zero", "fetch", "empty_series", "empty", "failed_series", "failed",
    }
    assert FUNNEL_COUNTERS == set(FUNNEL_STAGES)


def test_series_names_are_sanitized_before_they_reach_public_output():
    """Series tickers are the only non-numeric thing that reaches the output."""
    assert sanitize_series("kxcorn") == "KXCORN"
    assert sanitize_series("KX CORN; DROP TABLE markets--") == "KXCORNDROPTABLEMARKETS--"
    assert sanitize_series("") == "?"
    assert sanitize_series("\n\t ") == "?"
    assert len(sanitize_series("K" * 500)) <= 24


def test_output_is_bounded_and_marks_what_it_withheld():
    """A bounded list must never be mistakable for a complete one."""
    many = [f"KXSERIES{i:03d}" for i in range(200)]
    line = funnel_summary(FunnelState.of(fetched=0), empty_series=many, configured_series=200)

    assert len(line) <= MAX_SUMMARY_CHARS
    assert TRUNCATION_MARKER in line
    assert "empty_series=200/200" in line
    listed = [t for t in line.split() if t.startswith("KXSERIES")]
    assert len(listed) <= MAX_SERIES_LISTED


def test_a_hostile_configuration_cannot_produce_an_unbounded_line():
    line = funnel_summary(
        FunnelState.of(fetched=10**18),
        empty_series=["A" * 5000] * 50,
        configured_series=10**18,
    )
    assert len(line) <= MAX_SUMMARY_CHARS


def test_the_summary_never_contains_a_market_ticker_or_a_price():
    """The disclosure guarantee, stated as the negative an audit would ask for."""
    line = funnel_summary(
        FunnelState.of(fetched=3, eligible=3, candidates=3, actions=1),
        empty_series=[],
        configured_series=7,
    )
    for forbidden in ("KXCORN-26AUG", "price", "cents", "order", "payload", "@"):
        assert forbidden not in line


def test_warn_on_fetch_outcome_is_a_no_op_on_a_healthy_fetch(caplog):
    from kalshi_bot.obs.series_fetch import SeriesFetchResult

    result = SeriesFetchResult(per_series={"KXCORN": 4})
    with caplog.at_level(logging.WARNING):
        warn_on_fetch_outcome("freeze", result)
    assert caplog.records == []


# ---------------------------------------------------------------------------
# EMPTY vs FAILED: the cycle-level fetch diagnosis
#
# A zero is produced both by a venue with nothing for this book and by a fetch
# that never completed. Those have opposite remedies, so the cycle must never
# report the first when only the second was observed.
# ---------------------------------------------------------------------------


def test_all_series_empty_is_an_empty_universe(caplog):
    client = FakeClient({"KXCORN": [], "KXWHEAT": []})
    with caplog.at_level(logging.WARNING):
        result = fetch_markets_by_series(client, ["KXCORN", "KXWHEAT"], book="freeze")

    assert result.diagnosis == FETCH_EMPTY_UNIVERSE
    assert result.universe_empty is True
    assert result.incomplete is False
    record = next(r for r in caplog.records if "ENTIRE" in r.message)
    assert record.levelno == logging.ERROR
    assert "none failed" in record.message


def test_all_series_failed_is_NOT_an_empty_universe(caplog):
    """The regression this review found: every request raised, total_markets is
    zero, and the old rule called that an empty venue."""
    series = ["KXCORN", "KXWHEAT"]
    client = FakeClient({s: [] for s in series}, raise_for=set(series))
    with caplog.at_level(logging.WARNING):
        result = fetch_markets_by_series(client, series, book="freeze")

    assert result.diagnosis == FETCH_FAILED
    assert result.universe_empty is False
    assert result.incomplete is True
    assert result.empty_series == []            # nothing was successfully asked
    record = next(r for r in caplog.records if "EVERY configured series FAILED" in r.message)
    assert record.levelno == logging.ERROR
    assert "UNKNOWN" in record.message
    assert "not a venue answer" in record.message
    # ...and it must NOT claim the universe is empty.
    assert not any("ENTIRE configured universe" in r.message for r in caplog.records)


def test_one_failed_plus_one_empty_is_a_partial_failure(caplog):
    client = FakeClient({"KXCORN": [], "KXWHEAT": []}, raise_for={"KXWHEAT"})
    with caplog.at_level(logging.WARNING):
        result = fetch_markets_by_series(client, ["KXCORN", "KXWHEAT"], book="freeze")

    assert result.diagnosis == FETCH_PARTIAL_FAILURE
    assert result.universe_empty is False       # the empty one is only half the story
    assert result.empty_series == ["KXCORN"]
    assert result.failed_series == ["KXWHEAT"]
    record = next(r for r in caplog.records if "INCOMPLETE fetch" in r.message)
    assert record.levelno == logging.WARNING
    assert "KXWHEAT" in record.message and "KXCORN" in record.message


def test_one_failed_plus_one_nonempty_is_a_partial_failure(caplog):
    client = FakeClient({"KXCORN": [_mkt("KXCORN-1")], "KXWHEAT": []}, raise_for={"KXWHEAT"})
    with caplog.at_level(logging.WARNING):
        result = fetch_markets_by_series(client, ["KXCORN", "KXWHEAT"], book="freeze")

    assert result.diagnosis == FETCH_PARTIAL_FAILURE
    assert result.incomplete is True
    assert result.empty_series == []
    assert result.failed_series == ["KXWHEAT"]
    assert len(result.markets) == 1
    # A partial failure warns even when the successful series had markets: the
    # cycle still saw less than the universe.
    assert any("INCOMPLETE fetch" in r.message for r in caplog.records)


def test_a_failure_after_an_earlier_page_returned_markets_is_partial_not_total():
    """One series, page 1 succeeds with markets, page 2 raises.

    Every configured series is in `failed`, so a naive rule would call this a
    total failure — but the markets page 1 returned are real and must be kept.
    """

    class FlakyPaging:
        def __init__(self):
            self.n = 0

        def get_markets(self, **kw):
            self.n += 1
            if self.n == 1:
                return {"markets": [_mkt("A"), _mkt("B")], "cursor": "c1"}
            raise RuntimeError("boom on page 2")

    result = fetch_markets_by_series(FlakyPaging(), ["KXCORN"], book="freeze", max_pages=4)
    assert result.per_series == {"KXCORN": 2}
    assert result.failed_series == ["KXCORN"]
    assert result.diagnosis == FETCH_PARTIAL_FAILURE     # not FETCH_FAILED
    assert result.universe_empty is False
    assert len(result.markets) == 2                      # page 1's markets survive


def test_auth_error_propagates_from_a_later_page_too():
    """AuthError is a cycle-level problem wherever in pagination it appears."""

    class AuthOnPageTwo:
        def __init__(self):
            self.n = 0

        def get_markets(self, **kw):
            self.n += 1
            if self.n == 1:
                return {"markets": [_mkt("A")], "cursor": "c1"}
            raise AuthError("credentials")

    with pytest.raises(AuthError):
        fetch_markets_by_series(AuthOnPageTwo(), ["KXCORN"], book="freeze", max_pages=4)


def test_nothing_configured_is_its_own_diagnosis(caplog):
    with caplog.at_level(logging.WARNING):
        result = fetch_markets_by_series(FakeClient(), [], book="freeze")
    assert result.diagnosis == FETCH_NO_SERIES
    assert result.universe_empty is False
    assert caplog.records == []


def test_a_healthy_fetch_is_OK():
    client = FakeClient({"KXCORN": [_mkt("A")]})
    result = fetch_markets_by_series(client, ["KXCORN"], book="freeze")
    assert result.diagnosis == FETCH_OK
    assert result.incomplete is False


# --- the funnel must not call an incomplete fetch a venue answer --------------


def test_the_funnel_does_not_report_no_markets_when_the_fetch_failed():
    state = FunnelState.of(fetched=0)
    assert diagnose(state, fetch=FETCH_FAILED) == "FETCH_FAILED"
    line = funnel_summary(state, fetch=FETCH_FAILED, failed_series=["KXCORN"], configured_series=1)
    assert "state=FETCH_FAILED" in line
    assert "state=NO_MARKETS" not in line
    assert f"fetch={FETCH_FAILED}" in line
    assert "failed_series=1/1" in line


def test_the_funnel_marks_a_partial_fetch_as_incomplete_rather_than_empty():
    state = FunnelState.of(fetched=0)
    assert diagnose(state, fetch=FETCH_PARTIAL_FAILURE) == "NO_MARKETS_INCOMPLETE"
    line = funnel_summary(state, fetch=FETCH_PARTIAL_FAILURE, failed_series=["KXWHEAT"],
                          configured_series=2)
    assert "state=NO_MARKETS_INCOMPLETE" in line


def test_no_markets_is_reserved_for_a_complete_fetch():
    """The venue claim is only made when every series was successfully asked."""
    state = FunnelState.of(fetched=0)
    assert diagnose(state, fetch=FETCH_EMPTY_UNIVERSE) == "NO_MARKETS"
    assert diagnose(state, fetch=FETCH_OK) == "NO_MARKETS"
    assert diagnose(state) == "NO_MARKETS"


def test_a_fetch_problem_never_overrides_a_later_stage():
    """Once markets came back, a downstream zero is the book's own filtering."""
    state = FunnelState.of(fetched=9, eligible=0)
    assert diagnose(state, fetch=FETCH_PARTIAL_FAILURE) == "NO_ELIGIBLE"
    assert diagnose(state, fetch=FETCH_FAILED) == "NO_ELIGIBLE"


def test_an_unrecognised_fetch_diagnosis_is_never_echoed():
    """The field is a closed vocabulary; echoing an unknown value would open it."""
    line = funnel_summary(FunnelState.of(fetched=0), fetch="'; DROP TABLE markets--")
    assert "DROP TABLE" not in line
    assert "fetch=" not in line


def test_the_summary_never_carries_exception_text():
    line = funnel_summary(
        FunnelState.of(fetched=0), fetch=FETCH_FAILED,
        failed_series=["KXCORN"], configured_series=1,
    )
    for forbidden in ("Traceback", "RuntimeError", "boom", "Connection refused"):
        assert forbidden not in line


def test_the_summary_stays_bounded_with_both_lists_populated():
    many = [f"KXSERIES{i:03d}" for i in range(200)]
    line = funnel_summary(
        FunnelState.of(fetched=0), fetch=FETCH_PARTIAL_FAILURE,
        empty_series=many, failed_series=many, configured_series=400,
    )
    assert len(line) <= MAX_SUMMARY_CHARS
    assert TRUNCATION_MARKER in line


# ---------------------------------------------------------------------------
# Repetition. XOS-000004 made this module loud on purpose; being loud EVERY
# CYCLE is how that signal stopped being read.
#
# Observed in production 2026-09-05: the freeze book logged "ENTIRE configured
# universe returned zero open markets" at ERROR on every cycle across a weekend,
# because agricultural series are closed at weekends. An expected state, in the
# same voice and at the same volume as an outage.
#
# So: log on CHANGE, heartbeat hourly while it persists, and say when it clears.
# ---------------------------------------------------------------------------


def _empty_universe(*series):
    from kalshi_bot.obs.series_fetch import SeriesFetchResult

    return SeriesFetchResult(per_series={s: 0 for s in series})


def test_an_unchanged_outcome_is_logged_once_not_every_cycle(caplog):
    with caplog.at_level(logging.INFO):
        for _ in range(20):
            warn_on_fetch_outcome("freeze", _empty_universe("KXCORN", "KXSOYB"), now=0.0)
    assert len(caplog.records) == 1
    assert "ENTIRE configured universe" in caplog.records[0].getMessage()
    assert "STILL" not in caplog.records[0].getMessage()


def test_a_persisting_outcome_still_heartbeats_so_it_survives_a_log_window(caplog):
    """Silence is not the goal — a bounded log window that has rolled past the
    single announcement would show a broken book as perfectly quiet."""
    from kalshi_bot.obs.series_fetch import REPEAT_AFTER_SECONDS

    with caplog.at_level(logging.INFO):
        warn_on_fetch_outcome("freeze", _empty_universe("KXCORN"), now=0.0)
        warn_on_fetch_outcome("freeze", _empty_universe("KXCORN"), now=REPEAT_AFTER_SECONDS - 1)
        warn_on_fetch_outcome("freeze", _empty_universe("KXCORN"), now=REPEAT_AFTER_SECONDS + 1)
    msgs = [r.getMessage() for r in caplog.records]
    assert len(msgs) == 2, msgs
    assert "STILL" not in msgs[0] and "STILL" in msgs[1]


def test_a_different_series_set_is_a_different_fact_and_logs_immediately(caplog):
    """A book losing series one at a time must not hide behind an unchanged
    headline — the names are part of the signature, not just the diagnosis."""
    with caplog.at_level(logging.INFO):
        warn_on_fetch_outcome("freeze", _empty_universe("KXCORN"), now=0.0)
        warn_on_fetch_outcome("freeze", _empty_universe("KXCORN", "KXSOYB"), now=1.0)
    msgs = [r.getMessage() for r in caplog.records]
    assert len(msgs) == 2, msgs
    assert all("STILL" not in m for m in msgs)


def test_recovery_is_reported_exactly_once(caplog):
    """Without this, a book that went quiet is indistinguishable from one still
    broken and merely between heartbeats."""
    from kalshi_bot.obs.series_fetch import SeriesFetchResult

    healthy = SeriesFetchResult(per_series={"KXCORN": 4})
    with caplog.at_level(logging.INFO):
        warn_on_fetch_outcome("freeze", _empty_universe("KXCORN"), now=0.0)
        warn_on_fetch_outcome("freeze", healthy, now=1.0)
        warn_on_fetch_outcome("freeze", healthy, now=2.0)
    msgs = [r.getMessage() for r in caplog.records]
    assert len(msgs) == 2, msgs
    assert "RECOVERED" in msgs[1]


def test_two_books_do_not_suppress_each_other(caplog):
    with caplog.at_level(logging.INFO):
        warn_on_fetch_outcome("freeze", _empty_universe("KXCORN"), now=0.0)
        warn_on_fetch_outcome("theta", _empty_universe("KXCORN"), now=0.0)
    assert len(caplog.records) == 2
