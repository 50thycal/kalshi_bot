"""The contest cap must survive a contest whose legs settle either side of UTC midnight.

A contest is one result, but its markets do NOT share one UTC calendar date. An MLB F5 total
closes about an hour into a game and the full-game total about three; a 21:38 ET first pitch
puts the early legs before UTC midnight and the late ones after. Every other concentration cap
is settlement-DATE scoped by design — that is the risk they model — but the CONTEST cap is not
about a date at all, it is about one result. Scoping its read to the candidate's own UTC date
split a single game's legs across two days' counters and the cap simply never fired.

The failure was invisible: `skipped_contest_cap` stayed at 0, which reads as "the cap had
nothing to refuse" rather than "the cap is broken" — and it failed on exactly the late-evening
games the drawdown that motivated the cap came from (XOS-000020's 26SEP02 NYYLAA was a 21:38
start).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kalshi_bot import db
from kalshi_bot.mmsell.tracker import MmSellTracker

# One real MLB game, tickered by two series that close either side of UTC midnight.
GAME = "26SEP022138NYYLAA"
EV_F5 = f"KXMLBF5TOTAL-{GAME}"      # first five innings — closes late on day D
EV_TOTAL = f"KXMLBTOTAL-{GAME}"     # full game — closes early on day D+1


def _straddle() -> tuple[datetime, datetime]:
    """(23:30Z on day D, 01:30Z on day D+1) — two UTC dates, one nine innings."""
    early = (datetime.now(timezone.utc) + timedelta(days=3)).replace(
        hour=23, minute=30, second=0, microsecond=0)
    return early, early + timedelta(hours=2)


def _mkt(ticker, sub, close_dt, yes_bid_c=6, yes_ask_c=7, vol=500):
    return {"ticker": ticker, "yes_sub_title": sub, "close_time": close_dt.isoformat(),
            "volume_fp": f"{vol}.0",
            "yes_bid_dollars": f"{yes_bid_c / 100:.4f}",
            "yes_ask_dollars": f"{yes_ask_c / 100:.4f}"}


def _event(markets, event_ticker, series):
    return {"event_ticker": event_ticker, "series_ticker": series, "markets": markets}


def _ob(yes_bid_c=6, yes_ask_c=7):
    return {"orderbook_fp": {"yes_dollars": [[f"{yes_bid_c / 100:.4f}", "300"]],
                             "no_dollars": [[f"{(100 - yes_ask_c) / 100:.4f}", "300"]]}}


class FakeClient:
    def __init__(self, events, books):
        self._events, self._books = events, books

    def get_exchange_status(self):
        return {"exchange_active": True, "trading_active": True}

    def get_events(self, status="open", with_nested_markets=True, limit=200, cursor=None):
        return {"events": self._events, "cursor": ""}

    def get_orderbook(self, ticker, depth=None):
        return self._books[ticker]


def _setup(settings, variants, **over):
    settings.bot_mode = "mmsell"
    settings.mmsell_variants = variants
    for k, v in over.items():
        setattr(settings, k, v)
    db.init_engine(settings.database_url)
    db.create_all()


def _run(settings, events, books):
    with db.session_scope() as session:
        return MmSellTracker(FakeClient(events, books), settings).run_once(session)


def test_a_contest_straddling_utc_midnight_is_still_capped(settings):
    """The bug. The book already holds the F5 leg closing 23:30Z; the full-game leg closing
    01:30Z the next UTC day is the same bet and must be refused."""
    _setup(settings, "Gmmsell1:lo=5,hi=10,maxyes=7,contestcap=1",
           mmsell_contest_cap_enabled=False)
    early, late = _straddle()

    _run(settings,
         [_event([_mkt(f"{EV_F5}-5", "o4.5", early)], EV_F5, "KXMLBF5TOTAL")],
         {f"{EV_F5}-5": _ob()})
    summ = _run(settings,
                [_event([_mkt(f"{EV_TOTAL}-8", "o8.5", late)], EV_TOTAL, "KXMLBTOTAL")],
                {f"{EV_TOTAL}-8": _ob()})

    assert summ.per_book.get("Gmmsell1") is None, "the second leg of one game was taken"
    assert summ.skipped_contest_cap == 1


def test_both_straddling_legs_offered_in_one_cycle_still_cap(settings):
    """Same game, same scan: the control takes both legs, the capped arm takes one."""
    _setup(settings,
           "Gmmsell0:lo=5,hi=10,maxyes=7;Gmmsell1:lo=5,hi=10,maxyes=7,contestcap=1",
           mmsell_contest_cap_enabled=False)
    early, late = _straddle()
    events = [_event([_mkt(f"{EV_F5}-5", "o4.5", early)], EV_F5, "KXMLBF5TOTAL"),
              _event([_mkt(f"{EV_TOTAL}-8", "o8.5", late)], EV_TOTAL, "KXMLBTOTAL")]
    books = {f"{EV_F5}-5": _ob(), f"{EV_TOTAL}-8": _ob()}

    summ = _run(settings, events, books)

    assert summ.per_book.get("Gmmsell0") == 2
    assert summ.per_book.get("Gmmsell1") == 1
    assert summ.skipped_contest_cap == 1


def test_a_different_game_across_the_boundary_is_still_admitted(settings):
    """De-scoping the contest read from the settlement date must not make it grab unrelated
    games that happen to sit on the other side of midnight — over-grouping starves the book
    with no error and no log line."""
    _setup(settings, "Gmmsell1:lo=5,hi=10,maxyes=7,contestcap=1",
           mmsell_contest_cap_enabled=False)
    early, late = _straddle()
    other = "KXMLBTOTAL-26SEP021940MILCHC"

    _run(settings,
         [_event([_mkt(f"{EV_F5}-5", "o4.5", early)], EV_F5, "KXMLBF5TOTAL")],
         {f"{EV_F5}-5": _ob()})
    summ = _run(settings,
                [_event([_mkt(f"{other}-9", "o9.5", late)], other, "KXMLBTOTAL")],
                {f"{other}-9": _ob()})

    assert summ.per_book.get("Gmmsell1") == 1
    assert summ.skipped_contest_cap == 0


def test_a_book_without_the_override_still_takes_both_straddling_legs(settings):
    """The no-op case, on the shape the fix changes. The whole running cohort names no
    contest cap and must be byte-identical to before."""
    _setup(settings, "Tmmsell9:lo=5,hi=10,maxyes=7", mmsell_contest_cap_enabled=False)
    early, late = _straddle()
    events = [_event([_mkt(f"{EV_F5}-5", "o4.5", early)], EV_F5, "KXMLBF5TOTAL"),
              _event([_mkt(f"{EV_TOTAL}-8", "o8.5", late)], EV_TOTAL, "KXMLBTOTAL")]
    books = {f"{EV_F5}-5": _ob(), f"{EV_TOTAL}-8": _ob()}

    summ = _run(settings, events, books)

    assert summ.per_book.get("Tmmsell9") == 2
    assert summ.skipped_contest_cap == 0
