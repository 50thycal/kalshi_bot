"""The settlement-date concentration cap (docs/MMSELL_SEASONAL_FORECAST.md "Reading 3").

mmsell's risk model IS diversification: many small positions, none able to sink the book alone.
That model silently breaks when a book's open positions cluster on one SETTLEMENT DATE against
a shared driver — measured live 2026-08-03: 55% of the live position cap already settled on a
single August date, no election required. This caps a book's own open positions on any one date
at a fraction of that book's own max_open_positions, plus a tighter distinct-EVENTS cap on
regimes (elections) whose rungs settle together on a shared national outcome.

What these tests pin, in order of what would silently make the cap useless or over-strict:
  * the cap is read PER BOOK (own tag's own positions), not pooled across every mmsell book —
    otherwise one variant's fill would silently starve every other book's entries.
  * the date cap is a percentage OF the book's own max_open_positions (paper's 200, a twin's
    live-sized 60), so a twin inherits the tighter live-shaped number automatically.
  * the event cap only restricts NEW events on a saturated correlated-regime date — adding
    another rung to an event already represented must stay allowed, since that is the
    mutual-exclusivity hedge working, not additional correlated exposure.
  * the event cap must not fire on regimes that are not configured as correlated.
  * the repo-level query must exclude the candidate's OWN ticker, isolate by strategy, and
    respect the calendar-date boundary (not bleed into the neighboring day).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot import repository as repo
from kalshi_bot.mmsell.tracker import MmSellTracker


def _anchor() -> datetime:
    """A close-time anchor safely away from any UTC midnight boundary, so 'same calendar date'
    vs 'the next calendar date' never flakes depending on what instant the suite runs at."""
    return (datetime.now(timezone.utc) + timedelta(days=3)).replace(
        hour=12, minute=0, second=0, microsecond=0)


def _mkt(ticker, sub, yes_bid_c, yes_ask_c, close_dt, vol=500):
    return {
        "ticker": ticker,
        "yes_sub_title": sub,
        "close_time": close_dt.isoformat(),
        "volume_fp": f"{vol}.0",
        "yes_bid_dollars": f"{yes_bid_c / 100:.4f}",
        "yes_ask_dollars": f"{yes_ask_c / 100:.4f}",
    }


def _event(markets, event_ticker, series):
    return {"event_ticker": event_ticker, "series_ticker": series, "markets": markets}


def _ob(yes_bid_c, yes_ask_c):
    no_bid_c = 100 - yes_ask_c
    return {"orderbook_fp": {
        "yes_dollars": [[f"{yes_bid_c / 100:.4f}", "300"]],
        "no_dollars": [[f"{no_bid_c / 100:.4f}", "300"]],
    }}


class FakeClient:
    def __init__(self, events, books):
        self._events = events
        self._books = books

    def get_exchange_status(self):
        return {"exchange_active": True, "trading_active": True}

    def get_events(self, status="open", with_nested_markets=True, limit=200, cursor=None):
        return {"events": self._events, "cursor": ""}

    def get_orderbook(self, ticker, depth=None):
        return self._books[ticker]


def _setup(settings, **over):
    settings.bot_mode = "mmsell"
    settings.mmsell_variants = ""
    for k, v in over.items():
        setattr(settings, k, v)
    db.init_engine(settings.database_url)
    db.create_all()


# ------------------------------------------------------------------- repo-level


def test_ensure_settlement_meta_is_insert_only(session):
    close_dt = _anchor()
    repo.ensure_mmsell_settlement_meta(
        session, market_ticker="KXTEAM-A", event_ticker="KXTEAM-A-EV",
        series_ticker="KXTEAM", close_time=close_dt)
    repo.ensure_mmsell_settlement_meta(
        session, market_ticker="KXTEAM-A", event_ticker="DIFFERENT",
        series_ticker="DIFFERENT", close_time=close_dt + timedelta(days=100))

    row = session.query(m.MmSellSettlementMeta).one()
    assert row.event_ticker == "KXTEAM-A-EV"          # the FIRST write wins
    assert row.close_time.date() == close_dt.date()


def test_settlement_summary_excludes_the_candidates_own_ticker(session):
    close_dt = _anchor()
    for tk in ("KXTEAM-A", "KXTEAM-B"):
        repo.ensure_mmsell_settlement_meta(
            session, market_ticker=tk, event_ticker=f"{tk}-EV",
            series_ticker="KXTEAM", close_time=close_dt)
        session.add(m.PaperPosition(market_ticker=tk, strategy="mmsell", side="no",
                                    quantity=1, avg_price=80, status="open"))
    session.flush()

    n, events = repo.open_positions_settlement_summary(
        session, "mmsell", close_dt.date(), "KXTEAM-A")
    assert n == 1                      # KXTEAM-B only; KXTEAM-A excludes itself
    assert events == {"KXTEAM-B-EV"}


def test_settlement_summary_is_isolated_by_strategy_and_status(session):
    close_dt = _anchor()
    repo.ensure_mmsell_settlement_meta(
        session, market_ticker="KXTEAM-A", event_ticker="EV-A",
        series_ticker="KXTEAM", close_time=close_dt)
    repo.ensure_mmsell_settlement_meta(
        session, market_ticker="KXTEAM-B", event_ticker="EV-B",
        series_ticker="KXTEAM", close_time=close_dt)
    repo.ensure_mmsell_settlement_meta(
        session, market_ticker="KXTEAM-C", event_ticker="EV-C",
        series_ticker="KXTEAM", close_time=close_dt)
    session.add_all([
        m.PaperPosition(market_ticker="KXTEAM-A", strategy="mmsell", side="no",
                        quantity=1, avg_price=80, status="open"),
        m.PaperPosition(market_ticker="KXTEAM-B", strategy="mmsell10", side="no",
                        quantity=1, avg_price=80, status="open"),   # different BOOK
        m.PaperPosition(market_ticker="KXTEAM-C", strategy="mmsell", side="no",
                        quantity=1, avg_price=80, status="settled"),  # not OPEN
    ])
    session.flush()

    n, events = repo.open_positions_settlement_summary(
        session, "mmsell", close_dt.date(), "KXTEAM-ZZZ")
    assert n == 1 and events == {"EV-A"}


def test_settlement_summary_respects_the_calendar_date_boundary(session):
    close_dt = _anchor()
    repo.ensure_mmsell_settlement_meta(
        session, market_ticker="KXTEAM-TODAY", event_ticker="EV-TODAY",
        series_ticker="KXTEAM", close_time=close_dt)
    repo.ensure_mmsell_settlement_meta(
        session, market_ticker="KXTEAM-NEXTDAY", event_ticker="EV-NEXTDAY",
        series_ticker="KXTEAM", close_time=close_dt + timedelta(days=1))
    session.add_all([
        m.PaperPosition(market_ticker="KXTEAM-TODAY", strategy="mmsell", side="no",
                        quantity=1, avg_price=80, status="open"),
        m.PaperPosition(market_ticker="KXTEAM-NEXTDAY", strategy="mmsell", side="no",
                        quantity=1, avg_price=80, status="open"),
    ])
    session.flush()

    n, _events = repo.open_positions_settlement_summary(
        session, "mmsell", close_dt.date(), "KXTEAM-ZZZ")
    assert n == 1                     # NEXTDAY must not bleed into TODAY's count


# ------------------------------------------------------------------- through the tracker


def test_settlement_cap_blocks_the_nth_entry_on_a_busy_date(settings):
    """date_cap = ceil(4 * 0.5) = 2. The first two same-date candidates open; the third is
    refused for THAT date; a fourth candidate on a different date is unaffected."""
    _setup(settings, mmsell_max_open_positions=4, mmsell_settlement_cap_pct=0.5)
    day1, day2 = _anchor(), _anchor() + timedelta(days=1)
    ev1 = _event([
        _mkt("KXTEAM-A", "A", 18, 20, day1), _mkt("KXTEAM-B", "B", 18, 20, day1),
    ], "KXTEAM-EV1", "KXTEAM")
    books = {"KXTEAM-A": _ob(18, 20), "KXTEAM-B": _ob(18, 20)}
    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient([ev1], books), settings).run_once(session)
    assert summ.opened == 2 and summ.skipped_settlement_cap == 0

    ev2 = _event([_mkt("KXTEAM-C", "C", 18, 20, day1)], "KXTEAM-EV2", "KXTEAM")
    books2 = {"KXTEAM-C": _ob(18, 20)}
    with db.session_scope() as session:
        summ2 = MmSellTracker(FakeClient([ev2], books2), settings).run_once(session)
    assert summ2.opened == 0
    assert summ2.skipped_settlement_cap == 1

    ev3 = _event([_mkt("KXTEAM-D", "D", 18, 20, day2)], "KXTEAM-EV3", "KXTEAM")
    books3 = {"KXTEAM-D": _ob(18, 20)}
    with db.session_scope() as session:
        summ3 = MmSellTracker(FakeClient([ev3], books3), settings).run_once(session)
    assert summ3.opened == 1                     # a different date is untouched
    assert summ3.skipped_settlement_cap == 0


def test_settlement_cap_can_be_disabled(settings):
    _setup(settings, mmsell_max_open_positions=4, mmsell_settlement_cap_pct=0.5,
          mmsell_settlement_cap_enabled=False)
    day1 = _anchor()
    ev1 = _event([
        _mkt("KXTEAM-A", "A", 18, 20, day1), _mkt("KXTEAM-B", "B", 18, 20, day1),
    ], "KXTEAM-EV1", "KXTEAM")
    with db.session_scope() as session:
        MmSellTracker(FakeClient([ev1], {"KXTEAM-A": _ob(18, 20), "KXTEAM-B": _ob(18, 20)}),
                     settings).run_once(session)

    ev2 = _event([_mkt("KXTEAM-C", "C", 18, 20, day1)], "KXTEAM-EV2", "KXTEAM")
    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient([ev2], {"KXTEAM-C": _ob(18, 20)}),
                             settings).run_once(session)
    assert summ.opened == 1 and summ.skipped_settlement_cap == 0


def test_settlement_cap_is_scoped_per_book_not_pooled(settings):
    """A control-book saturated date must not block a DIFFERENT book's entries — each book's
    positions are its own diversification pool, exactly like the existing position-count cap."""
    _setup(settings, mmsell_max_open_positions=2, mmsell_settlement_cap_pct=0.5,
          mmsell_variants="mmsellX:lo=5,hi=40")
    day1 = _anchor()
    ev = _event([_mkt("KXTEAM-A", "A", 18, 20, day1)], "KXTEAM-EV1", "KXTEAM")
    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient([ev], {"KXTEAM-A": _ob(18, 20)}),
                             settings).run_once(session)
    # both books admit the same band -> both open on the same ticker+date, each its own book
    assert summ.per_book.get("mmsell") == 1
    assert summ.per_book.get("mmsellX") == 1
    assert summ.skipped_settlement_cap == 0     # cap ceil(2*0.5)=1 per book, each book used 1


def test_correlated_regime_event_cap_blocks_a_new_event_but_allows_another_rung(settings):
    """Elections is a CORRELATED regime: a national race breaking one way moves every ladder at
    once, so the cap counts distinct EVENTS on a shared date, not raw market count. Adding a
    rung to an event already represented must stay allowed — that pairing is the real hedge
    (at most one rung of one race can lose); a brand-new race on a saturated date is refused."""
    _setup(settings, mmsell_settlement_event_cap=2)
    day1 = _anchor()
    ev_a = _event([_mkt("KXSENATE-A-R1", "R1", 6, 7, day1)], "KXSENATE-A", "KXSENATE")
    ev_b = _event([_mkt("KXSENATE-B-R1", "R1", 6, 7, day1)], "KXSENATE-B", "KXSENATE")
    books = {"KXSENATE-A-R1": _ob(6, 7), "KXSENATE-B-R1": _ob(6, 7)}
    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient([ev_a, ev_b], books), settings).run_once(session)
    assert summ.opened == 2 and summ.skipped_event_cap == 0    # 2 distinct events, at the cap

    # a THIRD, NEW event on the same date -> refused (event cap saturated)
    ev_c = _event([_mkt("KXSENATE-C-R1", "R1", 6, 7, day1)], "KXSENATE-C", "KXSENATE")
    with db.session_scope() as session:
        summ2 = MmSellTracker(FakeClient([ev_c], {"KXSENATE-C-R1": _ob(6, 7)}),
                              settings).run_once(session)
    assert summ2.opened == 0 and summ2.skipped_event_cap == 1

    # ANOTHER rung of event A (already represented) -> allowed despite the same saturation
    ev_a2 = _event([_mkt("KXSENATE-A-R2", "R2", 6, 7, day1)], "KXSENATE-A", "KXSENATE")
    with db.session_scope() as session:
        summ3 = MmSellTracker(FakeClient([ev_a2], {"KXSENATE-A-R2": _ob(6, 7)}),
                              settings).run_once(session)
    assert summ3.opened == 1 and summ3.skipped_event_cap == 0


def test_event_cap_does_not_apply_outside_correlated_regimes(settings):
    """The same shape of scenario (many distinct events, one date) on a regime NOT configured
    as correlated must be gated only by the (much looser) settlement-date cap, never the
    tighter event cap — sports h2h matchups are not one shared national outcome."""
    _setup(settings, mmsell_settlement_event_cap=2)   # would block a 3rd event if it applied
    day1 = _anchor()
    events = [
        _event([_mkt(f"KXTEAM-{i}-A", "A", 18, 20, day1)], f"KXTEAM-EV{i}", "KXTEAM")
        for i in range(3)
    ]
    books = {f"KXTEAM-{i}-A": _ob(18, 20) for i in range(3)}
    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient(events, books), settings).run_once(session)
    assert summ.opened == 3 and summ.skipped_event_cap == 0


@pytest.fixture()
def session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite://")
    m.Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
