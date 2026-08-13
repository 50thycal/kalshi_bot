"""The inline-quote PRE-FILTER (docs/MMSELL_QUOTE_PARITY.md).

Skipping a market's orderbook fetch on the strength of the event page's own quote is the only
knob in this book that CANNOT be scoped to a single book: one orderbook call serves every book
that reaches the market, so a skipped fetch removes that candidate from every paper book and
both live arms simultaneously. There is no A/B form of it in production.

That makes the failure mode silent and global — a wrongly-skipped market produces no error, it
just stops existing as a candidate — so every test here is about a case where the filter must
REFUSE to skip. It ships default-off; these pin what happens when it is armed.
"""

from __future__ import annotations

from kalshi_bot.config import Settings
from kalshi_bot.mmsell.tracker import MmSellTracker

skips = MmSellTracker._prefilter_skips


def _settings(**over):
    base = dict(_env_file=None, kalshi_api_key_id="k", kalshi_private_key="p",
                database_url="sqlite://", bot_mode="weather", mmsell_prefilter_enabled=True)
    base.update(over)
    return Settings(**base)


def _mkt(bid=None, ask=None):
    """A nested market payload. Kalshi sends `_dollars` STRINGS and omits the integer keys, so
    the fixture uses the live shape — a filter written against the legacy keys would read every
    quote as absent and (correctly, but uselessly) never skip anything."""
    out = {}
    if bid is not None:
        out["yes_bid_dollars"] = f"{bid / 100:.4f}"
    if ask is not None:
        out["yes_ask_dollars"] = f"{ask / 100:.4f}"
    return out


def _book(lo=5.0, hi=10.0, maxyes=7.0, tag="b"):
    return {"tag": tag, "lo": lo, "hi": hi, "maxyes": maxyes}


SCHEDULED_SERIES = "KXBTCD"     # price_strike / scheduled
INPLAY_SERIES = "KXMLBGAME"     # h2h / in_play
UNKNOWN_SERIES = "KXNOSUCHSERIESANYWHERE"


# ------------------------------------------------------------------ it does skip, sometimes


def test_a_clearly_out_of_band_scheduled_market_is_skipped():
    """The whole point: a 60c market cannot be a 5-10c candidate for anyone, so spending an
    orderbook call to confirm that is the waste the pre-filter exists to remove."""
    s = _settings()
    assert skips(s, _mkt(59, 61), SCHEDULED_SERIES, [_book()]) is True


def test_a_market_inside_the_band_is_never_skipped():
    s = _settings()
    assert skips(s, _mkt(5, 7), SCHEDULED_SERIES, [_book()]) is False


def test_the_margin_widens_the_no_skip_zone():
    """A quote just outside the band is exactly where staleness bites, so the margin buys back
    the near misses. At 3c a 12c midpoint against a 5-10 band must still be fetched."""
    s = _settings(mmsell_prefilter_margin_cents=3.0)
    assert skips(s, _mkt(12, 12), SCHEDULED_SERIES, [_book()]) is False
    assert skips(s, _mkt(30, 30), SCHEDULED_SERIES, [_book()]) is True


# ------------------------------------------------------------------ it must NOT skip


def test_in_play_is_always_fetched():
    """The large disagreements concentrate in fast-moving contests, where the event page's one
    snapshot is already stale by the time the scan reaches this market."""
    s = _settings()
    assert skips(s, _mkt(59, 61), INPLAY_SERIES, [_book()]) is False
    # ...unless explicitly trusted, which is the knob that turns the carve-out off.
    assert skips(_settings(mmsell_prefilter_trust_in_play=True),
                 _mkt(59, 61), INPLAY_SERIES, [_book()]) is True


def test_an_unclassified_series_is_treated_as_in_play():
    """The conservative reading of "we do not know what this is" is to spend the call. Treating
    unknown as safe-to-skip would be most wrong exactly where we know least — and half of all
    candidate flow was unclassified as recently as 2026-08-13."""
    s = _settings()
    assert skips(s, _mkt(59, 61), UNKNOWN_SERIES, [_book()]) is False


def test_a_missing_or_half_missing_quote_never_skips():
    """No data is not evidence of being out of band. This is the case that would leak silently:
    a series whose inline quote stops being populated would otherwise vanish from every book at
    once, with no error anywhere."""
    s = _settings()
    assert skips(s, _mkt(None, None), SCHEDULED_SERIES, [_book()]) is False
    assert skips(s, _mkt(59, None), SCHEDULED_SERIES, [_book()]) is False
    assert skips(s, _mkt(None, 61), SCHEDULED_SERIES, [_book()]) is False
    assert skips(s, {}, SCHEDULED_SERIES, [_book()]) is False


def test_no_interested_books_means_no_decision_to_make():
    """Nothing is going to fetch this market anyway; returning True would be a saving that never
    existed and would inflate the skip counter."""
    assert skips(_settings(), _mkt(59, 61), SCHEDULED_SERIES, []) is False


# ------------------------------------------------------------------ the shared-fetch property


def test_the_test_is_the_UNION_of_interested_books_not_any_one_of_them():
    """The load-bearing property. One orderbook call serves every book that reaches this market,
    so a skip must be safe for ALL of them. A 25c market is far outside the tight book's 5-10
    band but sits inside the wide book's 5-40 — skipping on the tight book's opinion alone would
    silently starve the wide one."""
    s = _settings()
    tight, wide = _book(5, 10, 7, "tight"), _book(5, 40, None, "wide")

    assert skips(s, _mkt(24, 26), SCHEDULED_SERIES, [tight]) is True
    assert skips(s, _mkt(24, 26), SCHEDULED_SERIES, [tight, wide]) is False
    assert skips(s, _mkt(24, 26), SCHEDULED_SERIES, [wide, tight]) is False, "order must not matter"


def test_a_cheap_ask_keeps_a_ceiling_book_interested_even_above_the_band():
    """`maxyes` admits on the ACTUAL entry price, not the midpoint, so a market whose midpoint
    sits above the band can still be a candidate if its cheap side is cheap. Testing the
    midpoint alone would drop exactly the trades the tight book exists to take."""
    s = _settings(mmsell_prefilter_margin_cents=0.0)
    # midpoint 30 is far outside 5-10, but the ask is 6c — under the 7c ceiling.
    assert skips(s, _mkt(54, 6), SCHEDULED_SERIES, [_book(5, 10, 7)]) is False
    # ...and with no cheap ask, the same midpoint skips.
    assert skips(s, _mkt(29, 31), SCHEDULED_SERIES, [_book(5, 10, 7)]) is True


# ------------------------------------------------------------------ it is off by default


def test_the_prefilter_ships_disabled():
    """It cannot be scoped per book, so arming it changes every book at once — including live.
    It must never become enabled by a default change."""
    s = Settings(_env_file=None, kalshi_api_key_id="k", kalshi_private_key="p",
                 database_url="sqlite://", bot_mode="weather")
    assert s.mmsell_prefilter_enabled is False
    assert s.mmsell_prefilter_trust_in_play is False
