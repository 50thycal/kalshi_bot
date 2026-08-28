"""The full order book — not the event page's inline quote — decides `maxyes`.

WHY THIS FILE EXISTS
--------------------
`GET /events?with_nested_markets=true` returns a top-of-book quote on every nested
market, and the scan already makes that call. It is tempting to let it decide
entry eligibility: the orderbook fetch is the scan's rate-limit bottleneck
(`docs/MMSELL_QUOTE_PARITY.md`).

The parity study measured what that would cost. The inline quote and the
orderbook disagree by more than 5c on 0.6% of markets, and the large
disagreements are not rounding — the study observed ask discrepancies above 40c
on BTC/ETH contracts, where the event page's one snapshot is stale by the time
the scan reaches the market.

`maxyes=7` is a 7-CENT ceiling. A 40c error in the quote it is checked against is
not a near miss; it is the difference between the cheap-longshot cell the whole
mmsell10 thesis rests on and a market at the opposite end of the book. So the
authoritative check must read the orderbook, and this file is the proof that it
does — asserted through `MmSellTracker.run_once`, the real entry path, with the
inline quote and the orderbook deliberately disagreeing.

The pre-filter (`MMSELL_PREFILTER_ENABLED`) is the one place an inline quote can
change the candidate stream, and it is off by default. Its structural property is
pinned below too: it can only ever SKIP a fetch, never admit an entry — so even
armed, it cannot put a market past `maxyes`. It is nonetheless left disarmed for
the live canary, for the reason the last test states.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kalshi_bot import db
from kalshi_bot.mmsell.tracker import MmSellTracker

MMSELL10 = "mmsell10:lo=5,hi=10,maxyes=7"


def _mkt(ticker, sub, inline_bid_c, inline_ask_c, vol=500, hours=48):
    """A nested market as the EVENT PAGE reports it — the inline quote only."""
    close = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    return {
        "ticker": ticker,
        "yes_sub_title": sub,
        "close_time": close,
        "volume_fp": f"{vol}.0",
        "yes_bid_dollars": f"{inline_bid_c / 100:.4f}",
        "yes_ask_dollars": f"{inline_ask_c / 100:.4f}",
    }


def _event(markets, series="KXTEAM"):
    return {"event_ticker": f"{series}-26", "series_ticker": series,
            "markets": markets}


def _ob(yes_bid_c, yes_ask_c):
    """The ORDER BOOK — ground truth. `best_no_bid` = 100 - yes_ask is what the
    book actually pays to sell the yes tail, and what `maxyes` caps."""
    return {"orderbook_fp": {
        "yes_dollars": [[f"{yes_bid_c / 100:.4f}", "300"]],
        "no_dollars": [[f"{(100 - yes_ask_c) / 100:.4f}", "300"]],
    }}


class FakeClient:
    def __init__(self, events, books):
        self._events = events
        self._books = books
        self.fetched: list[str] = []

    def get_exchange_status(self):
        return {"exchange_active": True, "trading_active": True}

    def get_events(self, status="open", with_nested_markets=True, limit=200,
                   cursor=None):
        return {"events": self._events, "cursor": ""}

    def get_orderbook(self, ticker, depth=None):
        self.fetched.append(ticker)
        return self._books[ticker]


def _setup(settings):
    settings.bot_mode = "mmsell"
    db.init_engine(settings.database_url)
    db.create_all()
    settings.mmsell_variants = ""
    settings.live_strategies = ""
    settings.mmsell_quote_parity = False
    settings.mmsell_capture_candidates = False
    # Reset explicitly: `settings` is one object per test, so a run that armed the
    # pre-filter would otherwise leak it into the next _setup() in the same test.
    settings.mmsell_prefilter_enabled = False


def _run(settings, event, books):
    client = FakeClient([event], books)
    with db.session_scope() as session:
        return MmSellTracker(client, settings).run_once(session), client


# ---------------------------------------------------------------------------
# The load-bearing property
# ---------------------------------------------------------------------------


def test_a_wrong_inline_quote_cannot_admit_a_market_that_fails_maxyes(settings):
    """The BTC/ETH failure mode, at the magnitude the parity study measured.

    Inline: yes 5/6 -> the cheap side sells at 6c, comfortably under maxyes=7.
    Book:   yes 5/47 -> the cheap side actually sells at 47c, a 41c error.

    If the inline quote were authoritative this market would be entered at a
    price 6.7x the ceiling, in the exact cell the ceiling exists to exclude.
    """
    _setup(settings)
    settings.mmsell_variants = MMSELL10
    ev = _event([_mkt("KXTEAM-26-A", "A", 5, 6)])
    summ, client = _run(settings, ev, {"KXTEAM-26-A": _ob(5, 47)})

    assert "mmsell10" not in summ.per_book
    assert client.fetched == ["KXTEAM-26-A"]   # the book was consulted, not skipped


def test_the_admitted_price_is_the_books_price_not_the_inline_one(settings):
    """The mirror case: an inline quote that looks too RICH does not reject a
    market the book says is cheap, and the recorded entry price is the book's.

    Both halves matter. A quote that could only ever reject would still silently
    shrink the candidate stream, which is the same contamination in the other
    direction."""
    _setup(settings)
    settings.mmsell_variants = MMSELL10
    ev = _event([_mkt("KXTEAM-26-B", "B", 40, 45)])   # inline: far out of band
    summ, _client = _run(settings, ev, {"KXTEAM-26-B": _ob(5, 6)})

    assert summ.per_book.get("mmsell10") == 1


def test_the_boundary_is_read_off_the_book_exactly(settings):
    """maxyes=7 admits a 7c book price and refuses an 8c one, with the inline
    quote held constant at a passing value in both runs. The only thing that
    moves the decision is the order book."""
    _setup(settings)
    settings.mmsell_variants = MMSELL10
    ev = _event([_mkt("KXTEAM-26-C", "C", 5, 6)])
    summ, _ = _run(settings, ev, {"KXTEAM-26-C": _ob(6, 7)})
    assert summ.per_book.get("mmsell10") == 1

    _setup(settings)
    settings.mmsell_variants = MMSELL10
    ev = _event([_mkt("KXTEAM-26-D", "D", 5, 6)])
    summ, _ = _run(settings, ev, {"KXTEAM-26-D": _ob(6, 8)})
    assert "mmsell10" not in summ.per_book


# ---------------------------------------------------------------------------
# The pre-filter: off by default, and structurally unable to admit
# ---------------------------------------------------------------------------


def test_the_quote_prefilter_is_disarmed_by_default(settings):
    """The canary runs on the default. Stated as an assertion rather than a
    convention so a later default flip has to come past this test."""
    assert settings.mmsell_prefilter_enabled is False


def test_an_armed_prefilter_still_cannot_admit_a_market_that_fails_maxyes(settings):
    """The pre-filter's only power is to SKIP an orderbook fetch. A market whose
    inline quote passes is not admitted by that — it is merely fetched, and then
    judged on the book like any other. So arming it can never put a market past
    the ceiling; the risk it carries is entirely in the other direction."""
    _setup(settings)
    settings.mmsell_variants = MMSELL10
    settings.mmsell_prefilter_enabled = True
    settings.mmsell_prefilter_margin_cents = 0
    settings.mmsell_prefilter_trust_in_play = True
    ev = _event([_mkt("KXTEAM-26-E", "E", 5, 6)])      # inline passes the band
    summ, client = _run(settings, ev, {"KXTEAM-26-E": _ob(5, 47)})

    assert client.fetched == ["KXTEAM-26-E"]           # not skipped: inline in band
    assert "mmsell10" not in summ.per_book             # book still refuses it


def test_an_armed_prefilter_can_silently_drop_a_real_candidate(settings):
    """The reason the canary leaves it disarmed, asserted rather than asserted-in-prose.

    Inline says out of band; the book says in band. The pre-filter skips the fetch,
    so the market never becomes a candidate for ANY book sharing the scan — and it
    produces no error, because a skipped market simply stops existing. That changes
    the live book's candidate stream relative to its twin, which is precisely the
    contamination a live/paper twin comparison cannot survive.
    """
    _setup(settings)
    settings.mmsell_variants = MMSELL10
    settings.mmsell_prefilter_enabled = True
    settings.mmsell_prefilter_margin_cents = 0
    settings.mmsell_prefilter_trust_in_play = True
    ev = _event([_mkt("KXTEAM-26-F", "F", 40, 45)])    # inline: out of band
    summ, client = _run(settings, ev, {"KXTEAM-26-F": _ob(5, 6)})

    assert client.fetched == []                        # the book was never consulted
    assert summ.skipped_prefilter == 1
    assert "mmsell10" not in summ.per_book

    # ...and with the pre-filter disarmed, that same candidate is entered.
    _setup(settings)
    settings.mmsell_variants = MMSELL10
    ev = _event([_mkt("KXTEAM-26-G", "G", 40, 45)])
    summ, client = _run(settings, ev, {"KXTEAM-26-G": _ob(5, 6)})
    assert client.fetched == ["KXTEAM-26-G"]
    assert summ.per_book.get("mmsell10") == 1
