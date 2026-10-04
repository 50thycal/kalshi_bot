"""A paper-only position live cannot hold must not fill a live book's cap slot.

WHY THIS EXISTS. The live mirror copies a book's PAPER entries, and the two live-only bars
(the series pause, the review tier) deliberately leave paper alone, so a live book's paper side
holds positions real money never took. Its concentration caps counted those too. Seen on
`Hmmsell10` on 2026-10-04: the book opened a paper-only KXNFLSPREAD (paused for real money) on
two NFL games, which used each game's one contest slot, so the same games' KXNFLTOTAL — a
market live may trade — was refused before the mirror was ever asked, while the twin (which
applies the live bars) took it. 2 of ~25 live placements were lost that way (XOS-000038).

`mmsell_live_caps_count_live_eligible_only` makes such a candidate's caps count only positions
live could hold. What must never break:

  * **off is byte-identical** — the shipped behaviour, so merging changes nothing that runs;
  * **on, the live-tradable leg is admitted and mirrored** — the defect itself;
  * **a candidate live cannot trade still counts the whole paper book**, so paper evidence on
    paused / below-tier series accrues under exactly the old caps;
  * **a book live would not act on is untouched**, and so is every twin.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kalshi_bot import db, repository
from kalshi_bot.mmsell.tracker import MmSellTracker
from kalshi_bot.mmsell.universe import GRADUATED

GAME = "26OCT04MIAMIN"
SPREAD_EV, TOTAL_EV = f"KXNFLSPREAD-{GAME}", f"KXNFLTOTAL-{GAME}"
SPREAD, TOTAL = f"{SPREAD_EV}-MIN31", f"{TOTAL_EV}-60"
BOOK = "Xmmsell10"


def _anchor():
    return (datetime.now(timezone.utc) + timedelta(days=3)).replace(
        hour=12, minute=0, second=0, microsecond=0)


def _mkt(ticker, close_dt, vol):
    return {"ticker": ticker, "yes_sub_title": "x", "close_time": close_dt.isoformat(),
            "volume_fp": f"{vol}.0", "yes_bid_dollars": "0.0600", "yes_ask_dollars": "0.0700"}


def _ob():
    return {"orderbook_fp": {"yes_dollars": [["0.0600", "300"]],
                             "no_dollars": [["0.9300", "300"]]}}


class FakeClient:
    def __init__(self, events, books):
        self._events, self._books = events, books

    def get_exchange_status(self):
        return {"exchange_active": True, "trading_active": True}

    def get_events(self, status="open", with_nested_markets=True, limit=200, cursor=None):
        return {"events": self._events, "cursor": ""}

    def get_orderbook(self, ticker, depth=None):
        return self._books[ticker]


class RecordingExecutor:
    """Live for `BOOK` only, so the control book `mmsell` stays a pure paper book."""

    def __init__(self, live_tags=(BOOK,)):
        self.mirrored = []
        self._live = set(live_tags)

    def _switches_on(self):
        return True

    def _allowed(self, strategy):
        return strategy in self._live

    def mirror_mmsell_entry(self, session, *, strategy, event_ticker, ticker, **kw):
        # The real executor refuses a tag outside LIVE_STRATEGIES itself; so does this stub.
        if not self._allowed(strategy):
            return "gate:switches"
        self.mirrored.append((strategy, ticker))
        return "placed"


def _run(settings, *, flag, spread_first=True, executor=None, variants=None):
    settings.bot_mode = "mmsell"
    settings.mmsell_variants = variants or f"{BOOK}:lo=5,hi=10,maxyes=7,contestcap=1"
    settings.mmsell_live_min_tier = GRADUATED
    settings.mmsell_live_skip_series = "KXNFLSPREAD"
    settings.mmsell_live_caps_count_live_eligible_only = flag
    db.init_engine(settings.database_url)
    db.create_all()
    day = _anchor()
    # Events are scanned by volume, highest first: the order is the whole scenario.
    big, small = (900, 500) if spread_first else (500, 900)
    events = [{"event_ticker": SPREAD_EV, "series_ticker": "KXNFLSPREAD",
               "markets": [_mkt(SPREAD, day, big)]},
              {"event_ticker": TOTAL_EV, "series_ticker": "KXNFLTOTAL",
               "markets": [_mkt(TOTAL, day, small)]}]
    ex = executor if executor is not None else RecordingExecutor()
    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient(events, {SPREAD: _ob(), TOTAL: _ob()}), settings,
                             live_executor=ex).run_once(session)
    with db.session_scope() as session:
        held = sorted(repository.open_paper_position_tickers(session, BOOK))
    return summ, ex, held


def test_off_reproduces_the_defect_exactly(settings):
    """The shipped behaviour, kept as the regression baseline: the paused spread is paper-only,
    and it still blocks the tradable total on the same game, so nothing reaches real money."""
    summ, ex, held = _run(settings, flag=False)
    assert held == [SPREAD]
    assert ex.mirrored == []
    assert summ.skipped_contest_cap == 1


def test_on_the_live_tradable_leg_is_admitted_and_mirrored(settings):
    summ, ex, held = _run(settings, flag=True)
    assert held == [SPREAD, TOTAL]               # paper keeps the spread AND takes the total
    assert ex.mirrored == [(BOOK, TOTAL)]        # real money takes the total, never the spread
    assert summ.skipped_contest_cap == 0


def test_a_candidate_live_cannot_trade_still_counts_the_whole_book(settings):
    """Reversed order: the total opens (and is mirrored) first, then the PAUSED spread arrives.
    Live cannot trade it, so its caps count everything and it is refused exactly as before —
    paper evidence on the paused series accrues under the old caps."""
    summ, ex, held = _run(settings, flag=True, spread_first=False)
    assert held == [TOTAL]
    assert ex.mirrored == [(BOOK, TOTAL)]
    assert summ.skipped_contest_cap == 1


def test_a_book_live_would_not_act_on_is_untouched(settings):
    summ, ex, held = _run(settings, flag=True, executor=RecordingExecutor(live_tags=()))
    assert held == [SPREAD]
    assert ex.mirrored == []
    assert summ.skipped_contest_cap == 1


def test_two_tradable_legs_on_one_game_still_get_one(settings):
    """The cap is scoped, not switched off: two live-tradable legs on one game still get one."""
    settings.bot_mode = "mmsell"
    settings.mmsell_variants = f"{BOOK}:lo=5,hi=10,maxyes=7,contestcap=1"
    settings.mmsell_live_min_tier = GRADUATED
    settings.mmsell_live_skip_series = "KXNFLSPREAD"
    settings.mmsell_live_caps_count_live_eligible_only = True
    db.init_engine(settings.database_url)
    db.create_all()
    day = _anchor()
    other = f"{TOTAL_EV}-63"
    events = [{"event_ticker": TOTAL_EV, "series_ticker": "KXNFLTOTAL",
               "markets": [_mkt(TOTAL, day, 900), _mkt(other, day, 500)]}]
    ex = RecordingExecutor()
    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient(events, {TOTAL: _ob(), other: _ob()}), settings,
                             live_executor=ex).run_once(session)
    assert len([t for b, t in ex.mirrored if b == BOOK]) == 1
    assert summ.skipped_contest_cap + summ.skipped_event_rung_cap == 1


def test_the_flag_ships_off(settings):
    assert settings.mmsell_live_caps_count_live_eligible_only is False
