"""The PER-BOOK contest cap override — what makes the merged contest cap measurable.

The mechanism itself (`contest_key_of`, `mmsell_contest_cap`) is merged and tested in
`tests/test_mmsell_settlement_cap.py`. It ships DEFAULT OFF because `tracker.py` is shared by
every mmsell book, so the global flag caps all of them at once — which is exactly why it cannot
be measured as it stands: there is no window in which a capped book and an uncapped control run
side by side, and without one the only comparison available is before-versus-after across two
different market regimes.

`contestcap=` in a book's variant spec is the missing half (docs/MMSELL_CORRELATION_CAP.md), and
these tests pin the properties that make it a valid experiment rather than a second global
switch:

  * a book with `contestcap` is capped even though the GLOBAL flag is off — otherwise the
    experiment cannot run at all;
  * a book WITHOUT it is completely untouched, which is the entire existing cohort. If this ever
    breaks, every running book's candidate stream changes silently and every number collected
    before the change becomes incomparable with every number after it;
  * the two arms differ from each other ONLY by the cap, on the same candidate;
  * a malformed cap drops the book rather than running it uncapped — a book that reads as
    capped and trades uncapped looks like a null result for the cap instead of a typo.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kalshi_bot import db
from kalshi_bot.mmsell.tracker import MmSellTracker

# One real MLB game as Kalshi tickers it, priced by two different series. This is the shape that
# defeated every pre-existing cap: two event tickers, one result.
GAME = "26SEP022138NYYLAA"
EV_TOTAL, EV_SPREAD = f"KXMLBTOTAL-{GAME}", f"KXMLBSPREAD-{GAME}"


def _anchor() -> datetime:
    return (datetime.now(timezone.utc) + timedelta(days=3)).replace(
        hour=12, minute=0, second=0, microsecond=0)


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


def _one_game_two_series(day):
    events = [_event([_mkt(f"{EV_TOTAL}-8", "o8.5", day)], EV_TOTAL, "KXMLBTOTAL"),
              _event([_mkt(f"{EV_SPREAD}-NYY3", "-1.5", day)], EV_SPREAD, "KXMLBSPREAD")]
    books = {f"{EV_TOTAL}-8": _ob(), f"{EV_SPREAD}-NYY3": _ob()}
    return events, books


def test_a_book_opts_in_while_the_global_flag_stays_off(settings):
    """The whole point of the override. `Gmmsell1` is capped and `Gmmsell0` — its control, on the
    same two candidates in the same cycle — is not."""
    _setup(settings,
           "Gmmsell0:lo=5,hi=10,maxyes=7;Gmmsell1:lo=5,hi=10,maxyes=7,contestcap=1",
           mmsell_contest_cap_enabled=False)
    events, books = _one_game_two_series(_anchor())
    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient(events, books), settings).run_once(session)

    assert summ.per_book.get("Gmmsell1") == 1     # one bet on this game
    assert summ.per_book.get("Gmmsell0") == 2     # the control takes both legs
    assert summ.skipped_contest_cap == 1


def test_a_book_without_the_override_is_untouched(settings):
    """The entire running cohort. If this breaks, every book's candidate stream moves silently."""
    _setup(settings, "Tmmsell9:lo=5,hi=10,maxyes=7", mmsell_contest_cap_enabled=False)
    events, books = _one_game_two_series(_anchor())
    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient(events, books), settings).run_once(session)

    assert summ.per_book.get("Tmmsell9") == 2
    assert summ.skipped_contest_cap == 0


def test_the_override_still_caps_when_the_global_is_on(settings):
    """A book naming its own cap uses THAT number, not the global one, so an arm cannot be
    silently re-sized by a later change to the global setting."""
    _setup(settings, "Gmmsell1:lo=5,hi=10,maxyes=7,contestcap=2",
           mmsell_contest_cap_enabled=True, mmsell_contest_cap=1)
    events, books = _one_game_two_series(_anchor())
    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient(events, books), settings).run_once(session)

    assert summ.per_book.get("Gmmsell1") == 2     # its own cap of 2 admits both legs
    # ...while the control book, which names no cap, follows the global 1 and IS capped. The
    # skip counter is cycle-wide across books, so that one refusal is what it records.
    assert summ.per_book.get("mmsell") == 1
    assert summ.skipped_contest_cap == 1


def test_a_different_game_is_still_admitted(settings):
    """The cap bounds one bet, not the book's flow."""
    _setup(settings, "Gmmsell1:lo=5,hi=10,maxyes=7,contestcap=1",
           mmsell_contest_cap_enabled=False)
    day = _anchor()
    other = "KXMLBTOTAL-26SEP021940MILCHC"
    events = [_event([_mkt(f"{EV_TOTAL}-8", "o8.5", day)], EV_TOTAL, "KXMLBTOTAL"),
              _event([_mkt(f"{other}-9", "o9.5", day)], other, "KXMLBTOTAL")]
    books = {f"{EV_TOTAL}-8": _ob(), f"{other}-9": _ob()}
    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient(events, books), settings).run_once(session)

    assert summ.per_book.get("Gmmsell1") == 2
    assert summ.skipped_contest_cap == 0


def test_the_cap_persists_across_cycles(settings):
    """It counts OPEN positions, so a game entered in an earlier cycle still blocks — the live
    clustering accumulated over hours, not inside one scan."""
    _setup(settings, "Gmmsell1:lo=5,hi=10,maxyes=7,contestcap=1",
           mmsell_contest_cap_enabled=False)
    day = _anchor()
    with db.session_scope() as session:
        MmSellTracker(FakeClient(
            [_event([_mkt(f"{EV_TOTAL}-8", "o8.5", day)], EV_TOTAL, "KXMLBTOTAL")],
            {f"{EV_TOTAL}-8": _ob()}), settings).run_once(session)

    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient(
            [_event([_mkt(f"{EV_SPREAD}-NYY3", "-1.5", day)], EV_SPREAD, "KXMLBSPREAD")],
            {f"{EV_SPREAD}-NYY3": _ob()}), settings).run_once(session)

    assert summ.per_book.get("Gmmsell1") is None
    assert summ.skipped_contest_cap == 1


def test_config_parses_the_override_and_drops_a_malformed_one(settings):
    settings.mmsell_variants = (
        "Gmmsell1:lo=5,hi=10,contestcap=1;"
        "GmmsellZeroCap:lo=5,hi=10,contestcap=0;"
        "GmmsellNaN:lo=5,hi=10,contestcap=lots"
    )
    by_tag = {b["tag"]: b for b in settings.mmsell_variant_list}
    assert by_tag["Gmmsell1"]["contestcap"] == 1
    # A cap of 0 would admit nothing at all rather than capping, and a non-integer is a typo.
    # Both drop the book: one that reads as capped and trades zero (or uncapped) is worse than
    # no book, because it looks like a result.
    assert "GmmsellZeroCap" not in by_tag and "GmmsellNaN" not in by_tag


def test_existing_books_carry_no_override(settings):
    by_tag = {b["tag"]: b for b in settings.mmsell_variant_list}
    assert by_tag["Tmmsell6"]["contestcap"] is None
    assert by_tag["Gmmsell0"]["contestcap"] is None     # the control opts out explicitly
    assert by_tag["Gmmsell1"]["contestcap"] == 1
