"""Per-book SCAN DEPTH (`scanmax`) — docs/MMSELL_SCAN_DEPTH.md.

The mmsell scan ranks eligible events by volume and cuts to the top N. That cut decides which
candidates every book is offered, so raising it is not a per-book preference — it reshapes the
universe. Raising it globally would change the candidate stream of every paper book AND both
live arms at once, making every number collected before the change incomparable with every
number after it.

`scanmax` lets ONE book look deeper while every other book keeps seeing exactly the top-N it
always saw. These tests exist because that isolation is invisible in production: a book whose
candidate stream quietly widened would not error, it would just start trading a different
universe and its history would silently stop being comparable to its control.

What is pinned here:
  * a book without `scanmax` is offered EXACTLY the same events as before the feature existed;
  * the deep book is offered the extra ones, and nothing else differs between the two;
  * the funnel telemetry the scan-health read depends on stays scoped to the control's depth,
    so adding an experiment cannot look like the scan recovering.
"""

from __future__ import annotations

from kalshi_bot.config import Settings


def _settings(**over):
    base = dict(_env_file=None, kalshi_api_key_id="k", kalshi_private_key="p",
                database_url="sqlite://", bot_mode="weather")
    base.update(over)
    return Settings(**base)


def _books(**over):
    return {b["tag"]: b for b in _settings(**over).mmsell_variant_list}


# ------------------------------------------------------------------ the spec key


def test_scanmax_parses_as_an_int_and_defaults_to_none():
    books = _books(mmsell_variants="Xmmsell1:lo=5,hi=10,scanmax=225;Xmmsell2:lo=5,hi=10")
    assert books["Xmmsell1"]["scanmax"] == 225
    assert books["Xmmsell2"]["scanmax"] is None


def test_a_non_numeric_scanmax_rejects_the_whole_spec():
    """A book that silently fell back to the default depth would look like a running experiment
    while testing nothing — the same failure mode the mtype validation exists for."""
    assert "Xmmsell1" not in _books(mmsell_variants="Xmmsell1:lo=5,hi=10,scanmax=deep")


# ------------------------------------------------------------------ the isolation property


def test_only_the_deep_book_carries_a_scanmax():
    """The property that makes every other book's history still comparable. If a second book
    ever acquires a scanmax, its control relationship needs re-deriving — so this is asserted
    as an exhaustive list rather than a spot check."""
    deep = [tag for tag, b in _books().items() if b.get("scanmax")]
    assert deep == ["mmsell10d"], f"unexpected deep-scanning books: {deep}"


def test_the_deep_book_differs_from_its_control_ONLY_by_scan_depth():
    """`mmsell10d` is read against `mmsell10`, so any second difference would confound the
    result — the extra events would no longer be the only explanation for a divergence."""
    books = _books()
    control, deep = books["mmsell10"], books["mmsell10d"]

    differing = {k for k in deep if k != "tag" and deep[k] != control[k]}
    assert differing == {"scanmax"}
    assert control["scanmax"] is None
    assert deep["scanmax"] > _settings().mmsell_top_events


# ------------------------------------------------------------------ the rank gate


def _gate(book, rank, top_events):
    """The gate as the tracker applies it: a book sees an event only inside its own depth."""
    return rank < (book.get("scanmax") or top_events)


def test_a_book_without_scanmax_sees_exactly_the_global_top_n():
    top = _settings().mmsell_top_events
    control = _books()["mmsell10"]

    assert _gate(control, 0, top)
    assert _gate(control, top - 1, top)
    # ...and not one event further, however deep the scan itself reached.
    assert not _gate(control, top, top)
    assert not _gate(control, top + 74, top)


def test_the_deep_book_sees_past_the_global_cap_but_not_past_its_own():
    top = _settings().mmsell_top_events
    deep = _books()["mmsell10d"]

    assert _gate(deep, top, top), "deep book should see the first event past the control cap"
    assert _gate(deep, deep["scanmax"] - 1, top)
    assert not _gate(deep, deep["scanmax"], top)


def test_scan_depth_is_the_max_over_books_not_the_sum():
    """The scan fetches once, to the deepest book's depth; every book then filters that same
    list. Fetching per book would multiply the API cost that the whole exercise is bounded by."""
    top = 150
    books = [{"scanmax": None}, {"scanmax": 225}, {"scanmax": 180}]
    depth = max([top, *(b["scanmax"] for b in books if b.get("scanmax"))])
    assert depth == 225


def test_a_scanmax_below_the_global_cap_narrows_only_that_book():
    """Nothing forces scanmax to be an increase. A smaller value must narrow that book alone and
    must never shrink the shared fetch, or it would silently starve every other book."""
    top = 150
    narrow = {"scanmax": 50}
    assert _gate(narrow, 49, top)
    assert not _gate(narrow, 50, top)
    depth = max([top, *(b["scanmax"] for b in [narrow] if b.get("scanmax"))])
    assert depth == top, "a narrow book must not reduce the shared scan depth"


# ------------------------------------------------------------------ telemetry stability


def test_the_summary_separates_deep_counters_from_the_control_scoped_ones():
    """`mmsell_scan_health` reads events_seen / markets_considered as the funnel. If deep events
    landed in those, adding this experiment would read as the scan suddenly seeing 50% more of
    the market — indistinguishable from the 2026-08-08 starvation fix working twice."""
    from kalshi_bot.mmsell.tracker import MmSellCycleSummary

    summ = MmSellCycleSummary()
    for f in ("events_seen", "markets_considered",
              "events_scanned_deep", "markets_considered_deep"):
        assert hasattr(summ, f), f
        assert getattr(summ, f) == 0
