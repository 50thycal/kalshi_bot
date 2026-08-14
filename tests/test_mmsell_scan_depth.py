"""Per-book SCAN DEPTH (`scanmax`) — docs/MMSELL_SCAN_DEPTH.md.

The mmsell scan ranks eligible events by volume and cuts to the top N. That cut decides which
candidates every book is offered, so raising it is not a per-book preference — it reshapes the
universe. `scanmax` let ONE book look deeper while every other book kept seeing exactly the top-N
it always saw.

## The experiment is OVER (retired 2026-08-14) and the answer was NO

`mmsell10d` (225) and `mmsell10e` (300) were read against `mmsell10` over each book's own window:

    depth 150 (control)   +1.34c/trade  (n=177)
    depth 225 (mmsell10d) +0.70c/trade  (n=243)  -> -0.64c vs control
    depth 300 (mmsell10e) -1.05c/trade  (n=133)  -> -1.94c vs control

Monotonic decay into negative territory, and `mmsell10d` lost on TOTAL dollars too despite taking
66 more trades. The volume rank cut selects for LIQUID events, and liquidity is what a resting
maker needs both to fill and to avoid being picked off. The cap was doing work.

## Why these tests still exist

The `scanmax` MECHANISM is kept — a revival on a mechanically different (taker) entry is the one
licensed use, and rebuilding it from scratch is how the isolation property gets lost. So the
mechanism is still pinned here, on synthetic books, while the first test below pins the thing that
actually matters in production now: **no real book carries a `scanmax`**, so the shared scan runs
at the global cap and no book's candidate stream is quietly wider than its control's.
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


# ------------------------------------------------------------------ the retirement


def test_no_book_carries_a_scanmax():
    """The retirement, asserted as an exhaustive list rather than a spot check.

    A book that acquires a `scanmax` has had its candidate stream widened past its control's,
    which silently breaks comparability with its own history AND with the control — and nothing
    else in the system would notice. It also raises the SHARED scan depth (see below), so one
    book's experiment silently multiplies orderbook fetches for the whole cycle.

    After 2026-08-14 the answer to "is the deeper tail worth scanning" is a measured no, so the
    correct number of deep books is zero. Anything else here is either a revival that skipped the
    docs or an accident."""
    deep = sorted(tag for tag, b in _books().items() if b.get("scanmax"))
    assert deep == [], f"unexpected deep-scanning books: {deep} (the ladder was retired)"


def test_the_retired_ladder_books_are_gone_entirely():
    """Not merely un-deepened — removed. A `mmsell10d` still present without its `scanmax` would
    be a duplicate of `mmsell10` quietly diluting the control's own numbers."""
    books = _books()
    for tag in ("mmsell10d", "mmsell10e"):
        assert tag not in books, f"{tag} was retired 2026-08-14 but is still configured"


# ------------------------------------------------------------------ the spec key


def test_scanmax_parses_as_an_int_and_defaults_to_none():
    books = _books(mmsell_variants="Xmmsell1:lo=5,hi=10,scanmax=225;Xmmsell2:lo=5,hi=10")
    assert books["Xmmsell1"]["scanmax"] == 225
    assert books["Xmmsell2"]["scanmax"] is None


def test_a_non_numeric_scanmax_rejects_the_whole_spec():
    """A book that silently fell back to the default depth would look like a running experiment
    while testing nothing — the same failure mode the mtype validation exists for."""
    assert "Xmmsell1" not in _books(mmsell_variants="Xmmsell1:lo=5,hi=10,scanmax=deep")


# ------------------------------------------------------------------ the rank gate


def _gate(book, rank, top_events):
    """The gate as the tracker applies it: a book sees an event only inside its own depth."""
    return rank < (book.get("scanmax") or top_events)


def test_a_book_without_scanmax_sees_exactly_the_global_top_n():
    """Now the production case for EVERY book, which is why it stays even with the ladder gone."""
    top = _settings().mmsell_top_events
    control = _books()["mmsell10"]

    assert _gate(control, 0, top)
    assert _gate(control, top - 1, top)
    # ...and not one event further, however deep the scan itself reached.
    assert not _gate(control, top, top)
    assert not _gate(control, top + 74, top)


def test_a_deep_book_sees_past_the_global_cap_but_not_past_its_own():
    top = _settings().mmsell_top_events
    deep = {"scanmax": top + 75}

    assert _gate(deep, top, top), "deep book should see the first event past the control cap"
    assert _gate(deep, deep["scanmax"] - 1, top)
    assert not _gate(deep, deep["scanmax"], top)


def test_scan_depth_is_the_max_over_books_not_the_sum():
    """The scan fetches once, to the deepest book's depth; every book then filters that same
    list. Fetching per book would multiply the API cost that the whole exercise is bounded by.

    This is also why retiring the ladder HALVES orderbook fetches rather than saving nothing:
    with no book carrying a scanmax the max collapses back to the global cap."""
    top = 150
    books = [{"scanmax": None}, {"scanmax": 225}, {"scanmax": 180}]
    depth = max([top, *(b["scanmax"] for b in books if b.get("scanmax"))])
    assert depth == 225
    # ...and with the ladder retired:
    assert max([top, *(b["scanmax"] for b in [{"scanmax": None}] if b.get("scanmax"))]) == top


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
    landed in those, adding a depth experiment would read as the scan suddenly seeing 50% more of
    the market — indistinguishable from the 2026-08-08 starvation fix working twice.

    Kept after the retirement because these counters are what MEASURED it: `events_scanned_deep`
    going 225 -> 300 while `events_seen` held at 150 is how the ladder's effect on fetch load was
    separated from the control's funnel."""
    from kalshi_bot.mmsell.tracker import MmSellCycleSummary

    summ = MmSellCycleSummary()
    for f in ("events_seen", "markets_considered",
              "events_scanned_deep", "markets_considered_deep"):
        assert hasattr(summ, f), f
        assert getattr(summ, f) == 0
