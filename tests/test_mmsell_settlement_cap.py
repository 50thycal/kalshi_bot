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


def _event(markets, event_ticker, series, mutually_exclusive=None):
    ev = {"event_ticker": event_ticker, "series_ticker": series, "markets": markets}
    if mutually_exclusive is not None:
        ev["mutually_exclusive"] = mutually_exclusive
    return ev


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
    assert dict(events) == {"KXTEAM-B-EV": 1}


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
    assert n == 1 and dict(events) == {"EV-A": 1}


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


# ------------------------------------------------------ within-event rung cap (mutual exclusivity)


def test_rung_cap_does_not_apply_to_a_mutually_exclusive_event(settings):
    """A disjoint bucket ladder (Kalshi `mutually_exclusive: true`, e.g. KXWTIW's $1-wide
    `between` strikes) is the case the exemption was written for: at most ONE rung can resolve
    YES, so stacking rungs really is a hedge and must stay uncapped."""
    _setup(settings, mmsell_event_rung_cap=2)
    day1 = _anchor()
    markets = [_mkt(f"KXWTIW-EV-B{i}", f"B{i}", 6, 7, day1) for i in range(4)]
    ev = _event(markets, "KXWTIW-EV", "KXWTIW", mutually_exclusive=True)
    books = {f"KXWTIW-EV-B{i}": _ob(6, 7) for i in range(4)}
    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient([ev], books), settings).run_once(session)
    assert summ.opened == 4                     # all four rungs, cap of 2 notwithstanding
    assert summ.skipped_event_rung_cap == 0


def test_rung_cap_blocks_stacking_on_a_non_exclusive_ladder(settings):
    """A NESTED threshold ladder (`mutually_exclusive: false`, e.g. KXWTI's `-T` "above X"
    strikes) resolves every rung the same way off one print, so N rungs is one position at N x
    size. Past the cap, further rungs of that same event are refused."""
    _setup(settings, mmsell_event_rung_cap=2)
    day1 = _anchor()
    markets = [_mkt(f"KXWTI-EV-T{i}", f"T{i}", 6, 7, day1) for i in range(4)]
    ev = _event(markets, "KXWTI-EV", "KXWTI", mutually_exclusive=False)
    books = {f"KXWTI-EV-T{i}": _ob(6, 7) for i in range(4)}
    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient([ev], books), settings).run_once(session)
    assert summ.opened == 2                     # rungs 3 and 4 refused
    assert summ.skipped_event_rung_cap == 2


def test_rung_cap_treats_a_missing_exclusivity_flag_as_not_exclusive(settings):
    """Fail safe: an event page without `mutually_exclusive` gets the cap. Guessing "exclusive"
    on an unknown event is the expensive direction — it is exactly the concentration the cap
    exists to prevent, and an over-strict cap only costs a few marginal entries."""
    _setup(settings, mmsell_event_rung_cap=2)
    day1 = _anchor()
    markets = [_mkt(f"KXUNK-EV-T{i}", f"T{i}", 6, 7, day1) for i in range(3)]
    ev = _event(markets, "KXUNK-EV", "KXUNK")           # no mutually_exclusive key at all
    books = {f"KXUNK-EV-T{i}": _ob(6, 7) for i in range(3)}
    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient([ev], books), settings).run_once(session)
    assert summ.opened == 2 and summ.skipped_event_rung_cap == 1


def test_rung_cap_counts_only_the_same_event(settings):
    """The cap is per EVENT, not per date — a different non-exclusive event on the same date
    starts its own count, otherwise this would silently become a much tighter date cap."""
    _setup(settings, mmsell_event_rung_cap=2)
    day1 = _anchor()
    ev_a = _event([_mkt(f"KXWTI-A-T{i}", f"T{i}", 6, 7, day1) for i in range(3)],
                  "KXWTI-A", "KXWTI", mutually_exclusive=False)
    ev_b = _event([_mkt("KXWTI-B-T0", "T0", 6, 7, day1)], "KXWTI-B", "KXWTI",
                  mutually_exclusive=False)
    books = {f"KXWTI-A-T{i}": _ob(6, 7) for i in range(3)}
    books["KXWTI-B-T0"] = _ob(6, 7)
    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient([ev_a, ev_b], books), settings).run_once(session)
    assert summ.opened == 3                     # 2 from event A (capped) + 1 from event B
    assert summ.skipped_event_rung_cap == 1


def test_rung_cap_can_be_disabled(settings):
    _setup(settings, mmsell_event_rung_cap=2, mmsell_event_rung_cap_enabled=False)
    day1 = _anchor()
    markets = [_mkt(f"KXWTI-EV-T{i}", f"T{i}", 6, 7, day1) for i in range(4)]
    ev = _event(markets, "KXWTI-EV", "KXWTI", mutually_exclusive=False)
    books = {f"KXWTI-EV-T{i}": _ob(6, 7) for i in range(4)}
    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient([ev], books), settings).run_once(session)
    assert summ.opened == 4 and summ.skipped_event_rung_cap == 0


@pytest.fixture()
def session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite://")
    m.Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# --- the CONTEST cap (XOS-000020) -------------------------------------------
#
# Every cap above keys on the event ticker, which is SERIES x contest. One baseball
# game is therefore up to five separate "events" — KXMLBTOTAL, KXMLBTEAMTOTAL,
# KXMLBSPREAD, KXMLBHR and KXMLBF5TOTAL all price the same nine innings — so three
# rungs under each is fifteen positions on one result and nothing above notices.
#
# Measured on Dmmsell10's live canary: the 2026-09-02 NYYLAA game carried 6 markets
# across 5 series, STLLAD 5 across 3, NYMTB 5 across 2, and those three contests
# alone held -5.66 USD of a -6.78 USD drawdown. Every contest with 5+ markets lost.
#
# Two failure directions matter and both are pinned below. Under-grouping is the
# bug being fixed. OVER-grouping is worse in a quieter way: a cap that refuses
# entries sharing no risk starves the book invisibly, with no error and no log line
# anyone reads. Hence the ungrouped-regime tests.

_NYYLAA = "26SEP022138NYYLAA"


def _open_contest_position(session, ticker, close_dt, *, strategy="mmsell"):
    repo.ensure_mmsell_settlement_meta(
        session, market_ticker=ticker,
        event_ticker=ticker.rsplit("-", 1)[0],
        series_ticker=ticker.split("-", 1)[0], close_time=close_dt)
    session.add(m.PaperPosition(market_ticker=ticker, strategy=strategy, side="no",
                                quantity=1, avg_price=80, status="open"))
    session.flush()


def test_the_contest_counter_collapses_five_series_into_one_game(session):
    """The repo read the cap depends on. Five open positions, five different event
    tickers, ONE contest — which is exactly what the event counter cannot see."""
    close_dt = _anchor()
    for series, outcome in (("KXMLBF5TOTAL", "5"), ("KXMLBHR", "JUDGE1"),
                            ("KXMLBSPREAD", "NYY3"), ("KXMLBTEAMTOTAL", "NYY6"),
                            ("KXMLBTOTAL", "8")):
        _open_contest_position(session, f"{series}-{_NYYLAA}-{outcome}", close_dt)

    n, events = repo.open_positions_settlement_summary(
        session, "mmsell", close_dt.date(), "KXMLBTOTAL-26SEP022210STLLAD-13")
    contests = repo.open_positions_contest_summary(
        session, "mmsell", "KXMLBTOTAL-26SEP022210STLLAD-13")

    assert n == 5
    assert len(events) == 5, "five series look like five events — the gap being fixed"
    assert contests == {f"MLB:{_NYYLAA}": 5}, "and they are ONE contest"


def test_the_contest_counter_keeps_distinct_games_distinct(session):
    """A cap built on this must never refuse an unrelated game."""
    close_dt = _anchor()
    _open_contest_position(session, f"KXMLBTOTAL-{_NYYLAA}-8", close_dt)
    _open_contest_position(session, "KXMLBTOTAL-26SEP022210STLLAD-13", close_dt)

    contests = repo.open_positions_contest_summary(session, "mmsell", "KXOTHER-X-Y")

    assert len(contests) == 2


def test_the_contest_counter_is_isolated_by_book(session):
    """Same rule the date cap already follows: one book's fills must not starve
    another book's entries."""
    close_dt = _anchor()
    _open_contest_position(session, f"KXMLBTOTAL-{_NYYLAA}-8", close_dt,
                           strategy="mmsell")
    _open_contest_position(session, f"KXMLBHR-{_NYYLAA}-JUDGE1", close_dt,
                           strategy="mmsell10")

    contests = repo.open_positions_contest_summary(session, "mmsell", "KXOTHER-X-Y")

    assert contests == {f"MLB:{_NYYLAA}": 1}


def _blocks(session, settings, ticker, close_dt, *, mutually_exclusive=None, **over):
    """Run the real gate the entry scan runs, with nothing stubbed."""
    from kalshi_bot.mmsell.tracker import MmSellCycleSummary

    for k, v in over.items():
        setattr(settings, k, v)
    tracker = MmSellTracker(FakeClient([], {}), settings)
    return tracker._settlement_cap_blocks(
        session, settings, book_cap=200, tag="mmsell", ticker=ticker,
        close_dt=close_dt, series=ticker.split("-", 1)[0],
        event_ticker=ticker.rsplit("-", 1)[0],
        mutually_exclusive=mutually_exclusive,
        summ=MmSellCycleSummary(), recorder=None)


def test_a_second_series_on_the_same_game_is_REFUSED(session, settings):
    """The whole point. One position on the NYYLAA game under KXMLBTOTAL must block
    a second under KXMLBSPREAD — a different event ticker, the same nine innings."""
    _setup(settings)
    close_dt = _anchor()
    _open_contest_position(session, f"KXMLBTOTAL-{_NYYLAA}-8", close_dt)

    blocked = _blocks(session, settings, f"KXMLBSPREAD-{_NYYLAA}-NYY3", close_dt,
                      mmsell_contest_cap_enabled=True, mmsell_contest_cap=1)

    assert blocked is True


def test_a_DIFFERENT_game_is_still_allowed(session, settings):
    """Over-grouping would starve the book silently. A different matchup on the same
    night shares no result and must pass."""
    _setup(settings)
    close_dt = _anchor()
    _open_contest_position(session, f"KXMLBTOTAL-{_NYYLAA}-8", close_dt)

    blocked = _blocks(session, settings, "KXMLBSPREAD-26SEP022210STLLAD-STL3", close_dt,
                      mmsell_contest_cap_enabled=True, mmsell_contest_cap=1)

    assert blocked is False


def test_the_contest_cap_is_OFF_by_default(session, settings):
    """tracker.py is shared by every mmsell book, so switching this on changes
    selection for all of them at once — a shared-semantic change that is Platform
    Change Review's call, not a merge's. It must ship inert and provably so."""
    _setup(settings)
    close_dt = _anchor()
    _open_contest_position(session, f"KXMLBTOTAL-{_NYYLAA}-8", close_dt)

    assert settings.mmsell_contest_cap_enabled is False
    assert _blocks(session, settings, f"KXMLBSPREAD-{_NYYLAA}-NYY3", close_dt) is False


def test_a_mutually_exclusive_event_stays_exempt(session, settings):
    """Same carve-out the rung cap makes, for the same reason: when at most one leg
    can lose, stacking is a genuine hedge rather than concentration. Removing this
    would cap real hedges as if they were correlated bets."""
    _setup(settings)
    close_dt = _anchor()
    _open_contest_position(session, f"KXMLBTOTAL-{_NYYLAA}-8", close_dt)

    blocked = _blocks(session, settings, f"KXMLBSPREAD-{_NYYLAA}-NYY3", close_dt,
                      mutually_exclusive=True,
                      mmsell_contest_cap_enabled=True, mmsell_contest_cap=1)

    assert blocked is False


def test_an_UNGROUPED_regime_behaves_exactly_as_before(session, settings):
    """Outside the sports regimes the contest key IS the event ticker, so enabling
    the cap cannot re-scope the caps that already exist. Two economic releases
    sharing the token "26SEP" share no result."""
    _setup(settings)
    close_dt = _anchor()
    _open_contest_position(session, "KXPAYROLLS-26SEP-A", close_dt)

    blocked = _blocks(session, settings, "KXCPI-26SEP-B", close_dt,
                      mmsell_contest_cap_enabled=True, mmsell_contest_cap=1)

    assert blocked is False


def test_the_cap_counts_ACROSS_series_up_to_its_limit(session, settings):
    """At cap 2 the third series on one game is refused, not the second — so the
    threshold is a real dial rather than a hard-coded one-per-contest."""
    _setup(settings)
    close_dt = _anchor()
    _open_contest_position(session, f"KXMLBTOTAL-{_NYYLAA}-8", close_dt)

    assert _blocks(session, settings, f"KXMLBSPREAD-{_NYYLAA}-NYY3", close_dt,
                   mmsell_contest_cap_enabled=True, mmsell_contest_cap=2) is False

    _open_contest_position(session, f"KXMLBSPREAD-{_NYYLAA}-NYY3", close_dt)

    assert _blocks(session, settings, f"KXMLBHR-{_NYYLAA}-JUDGE1", close_dt,
                   mmsell_contest_cap_enabled=True, mmsell_contest_cap=2) is True
