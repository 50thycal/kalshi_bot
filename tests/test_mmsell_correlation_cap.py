"""The correlation cap — the unit of correlation is the OCCASION, not the event ticker.

Why this exists (XOS-000020, docs/MMSELL_CORRELATION_CAP.md). Every mmsell cap before this one
counted `event_ticker`, which is series x occasion. The live book `Dmmsell10` therefore held
positions on one MLB game under five separate series and no cap noticed: it believed it held
five independent 7c lottery tickets and held one bet on whether that game would be high-scoring.

What these tests pin, in the order in which a silent regression would matter most:
  * the game key SPANS SERIES — the whole point. If a qualifier or a series prefix leaks into
    it, the key degenerates back to `event_ticker` and the cap simply never fires, invisibly.
  * `scheduled` markets must NOT merge across series: KXBTCD and KXETHD at the same hour are
    different underlyings. The naive "strip the prefix" key merged them, and merged every
    date-suffixed series with every other, which is what made its measured gain uninterpretable.
  * an UNCLASSIFIED series is keyed to itself and never merged with anything.
  * `corrscope` isolates the two mechanics: `game` must leave ladder caps untouched, so
    Gmmsell1 differs from its control by the contest axis ALONE.
  * the repo read is NOT settlement-date scoped, unlike the settlement cap's — a game's markets
    do not share a UTC date (an F5 total closes early; a late start crosses midnight), so a
    date-scoped read would let the clustering through exactly when a slate straddles midnight.
  * a book that declares no `corrcap` is completely unaffected — that is the entire running
    cohort, whose candidate streams must not move by a single market.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot import repository as repo
from kalshi_bot.mmsell.correlation import (
    EVENT,
    GAME,
    UNKNOWN,
    contest_token,
    correlation_key,
    in_scope,
)
from kalshi_bot.mmsell.tracker import MmSellTracker

# One real MLB game, as Kalshi actually tickers it: date + start time + the two team codes,
# carried under a different series for each kind of contract.
GAME_TOKEN = "26SEP022210STLLAD"
EV_TOTAL = f"KXMLBTOTAL-{GAME_TOKEN}"
EV_SPREAD = f"KXMLBSPREAD-{GAME_TOKEN}"


def _anchor() -> datetime:
    return (datetime.now(timezone.utc) + timedelta(days=3)).replace(
        hour=12, minute=0, second=0, microsecond=0)


def _mkt(ticker, sub, close_dt, yes_bid_c=6, yes_ask_c=7, vol=500):
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


def _ob(yes_bid_c=6, yes_ask_c=7):
    return {"orderbook_fp": {
        "yes_dollars": [[f"{yes_bid_c / 100:.4f}", "300"]],
        "no_dollars": [[f"{(100 - yes_ask_c) / 100:.4f}", "300"]],
    }}


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


# ------------------------------------------------------------------- the key


def test_one_game_is_one_key_across_different_series():
    """The finding XOS-000020 recorded: five series, one bet. If this ever splits, the cap
    silently reverts to being the rung cap under a new name."""
    assert correlation_key("KXMLBTOTAL", EV_TOTAL) == (GAME, GAME_TOKEN)
    assert correlation_key("KXMLBSPREAD", EV_SPREAD) == (GAME, GAME_TOKEN)
    assert correlation_key("KXMLBTOTAL", EV_TOTAL) == correlation_key("KXMLBSPREAD", EV_SPREAD)


def test_a_trailing_qualifier_still_keys_to_its_game():
    """Under-merging is the dangerous direction: a leaked qualifier makes the cap never fire,
    and nothing in the telemetry would say so."""
    assert contest_token(f"KXMLBTEAMTOTAL-{GAME_TOKEN}-NYY") == GAME_TOKEN
    assert correlation_key("KXMLBTEAMTOTAL", f"KXMLBTEAMTOTAL-{GAME_TOKEN}-NYY") \
        == (GAME, GAME_TOKEN)


def test_different_games_are_different_keys():
    assert correlation_key("KXMLBTOTAL", EV_TOTAL) \
        != correlation_key("KXMLBTOTAL", "KXMLBTOTAL-26SEP021940MILCHC")


def test_scheduled_markets_do_not_merge_across_underlyings():
    """BTC and ETH at the same hour share a timestamp and nothing else. The naive
    strip-the-prefix key merged them (and merged every bare-date series with every other),
    which is precisely why its measured gain could not be attributed."""
    btc = correlation_key("KXBTCD", "KXBTCD-26SEP0512")
    eth = correlation_key("KXETHD", "KXETHD-26SEP0512")
    assert btc == (EVENT, "KXBTCD-26SEP0512")
    assert btc != eth


def test_an_unclassified_series_is_never_merged():
    kind, key = correlation_key("KXBRANDNEWTHING", "KXBRANDNEWTHING-26SEP05")
    assert kind == UNKNOWN and key == "KXBRANDNEWTHING-26SEP05"


def test_an_empty_event_ticker_keys_to_nothing():
    assert correlation_key("KXMLBTOTAL", "") == (UNKNOWN, "")


def test_in_scope_isolates_the_two_mechanics():
    assert in_scope(GAME, "game") and not in_scope(EVENT, "game")
    assert in_scope(GAME, "all") and in_scope(EVENT, "all") and in_scope(UNKNOWN, "all")
    assert not in_scope(GAME, "typo")     # an unknown scope caps nothing; config rejects it


# ------------------------------------------------------------------- config


def test_config_parses_and_validates_the_cap_keys(settings):
    settings.mmsell_variants = (
        "Gmmsell1:lo=5,hi=10,corrcap=1,corrscope=game;"
        "Gmmsell2:lo=5,hi=10,corrcap=2,corrscope=all;"
        "GmmsellBadScope:lo=5,hi=10,corrcap=1,corrscope=nonsense;"
        "GmmsellBadCap:lo=5,hi=10,corrcap=0"
    )
    by_tag = {b["tag"]: b for b in settings.mmsell_variant_list}
    assert by_tag["Gmmsell1"]["corrcap"] == 1 and by_tag["Gmmsell1"]["corrscope"] == "game"
    assert by_tag["Gmmsell2"]["corrcap"] == 2 and by_tag["Gmmsell2"]["corrscope"] == "all"
    # Both malformed books are dropped, not run: a book that reads as capped and trades
    # uncapped would look like a null result for the cap rather than a typo.
    assert "GmmsellBadScope" not in by_tag and "GmmsellBadCap" not in by_tag


def test_books_without_a_cap_are_untouched(settings):
    by_tag = {b["tag"]: b for b in settings.mmsell_variant_list}
    assert by_tag["Tmmsell6"]["corrcap"] is None


@pytest.fixture()
def session():
    """A bare in-memory session for the repo-level reads — same shape as the settlement cap's,
    so the two caps' unit tests stay directly comparable."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite://")
    m.Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# ------------------------------------------------------------------- repo read


def test_correlation_rows_exclude_own_ticker_and_isolate_strategy_and_status(session):
    close_dt = _anchor()
    rows = [("KXMLBTOTAL-A", "KXMLBTOTAL", EV_TOTAL, "Gmmsell2", "open"),
            ("KXMLBSPREAD-B", "KXMLBSPREAD", EV_SPREAD, "Gmmsell2", "open"),
            ("KXMLBTOTAL-C", "KXMLBTOTAL", EV_TOTAL, "mmsell10", "open"),    # other book
            ("KXMLBTOTAL-D", "KXMLBTOTAL", EV_TOTAL, "Gmmsell2", "settled")]  # not open
    for tk, series, ev, strat, status in rows:
        repo.ensure_mmsell_settlement_meta(
            session, market_ticker=tk, event_ticker=ev, series_ticker=series,
            close_time=close_dt)
        session.add(m.PaperPosition(market_ticker=tk, strategy=strat, side="no",
                                    quantity=1, avg_price=93, status=status))
    session.flush()

    got = repo.open_positions_correlation_rows(session, "Gmmsell2", "KXMLBTOTAL-A")
    assert got == [("KXMLBSPREAD", EV_SPREAD)]


def test_correlation_rows_are_not_settlement_date_scoped(session):
    """A game's markets do not share a UTC calendar date — an F5 total closes hours before the
    full-game total, and a 22:10 start crosses midnight. The settlement cap's date-scoped read
    would drop the earlier leg and let the concentration through exactly on the slates where it
    is worst."""
    close_dt = _anchor()
    for tk, series, offset in (("KXMLBF5TOTAL-A", "KXMLBF5TOTAL", timedelta(0)),
                               ("KXMLBTOTAL-B", "KXMLBTOTAL", timedelta(days=1))):
        repo.ensure_mmsell_settlement_meta(
            session, market_ticker=tk, event_ticker=f"{series}-{GAME_TOKEN}",
            series_ticker=series, close_time=close_dt + offset)
        session.add(m.PaperPosition(market_ticker=tk, strategy="Gmmsell2", side="no",
                                    quantity=1, avg_price=93, status="open"))
    session.flush()

    got = repo.open_positions_correlation_rows(session, "Gmmsell2", "ZZZ")
    assert len(got) == 2
    assert len({correlation_key(s, e) for s, e in got}) == 1   # still ONE bet


# ------------------------------------------------------------------- through the tracker


def test_game_cap_blocks_a_second_series_on_the_same_game(settings):
    """The live failure, reproduced: one game offered under two series. The capped book takes
    one; the uncapped control takes both."""
    _setup(settings, "Gmmsell1:lo=5,hi=10,maxyes=7,corrcap=1,corrscope=game")
    day = _anchor()
    events = [_event([_mkt("KXMLBTOTAL-A", "o8.5", day)], EV_TOTAL, "KXMLBTOTAL"),
              _event([_mkt("KXMLBSPREAD-A", "-1.5", day)], EV_SPREAD, "KXMLBSPREAD")]
    books = {"KXMLBTOTAL-A": _ob(), "KXMLBSPREAD-A": _ob()}
    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient(events, books), settings).run_once(session)

    assert summ.per_book.get("Gmmsell1") == 1        # one bet on this game, not two
    assert summ.per_book.get("mmsell") == 2          # the uncapped control is unaffected
    assert summ.skipped_correlation_cap == 1


def test_a_different_game_is_still_admitted(settings):
    """The cap must bound one bet, not the book's flow. A second, unrelated game opens."""
    _setup(settings, "Gmmsell1:lo=5,hi=10,maxyes=7,corrcap=1,corrscope=game")
    day = _anchor()
    other = "KXMLBTOTAL-26SEP021940MILCHC"
    events = [_event([_mkt("KXMLBTOTAL-A", "o8.5", day)], EV_TOTAL, "KXMLBTOTAL"),
              _event([_mkt("KXMLBTOTAL-B", "o9.5", day)], other, "KXMLBTOTAL")]
    books = {"KXMLBTOTAL-A": _ob(), "KXMLBTOTAL-B": _ob()}
    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient(events, books), settings).run_once(session)

    assert summ.per_book.get("Gmmsell1") == 2
    assert summ.skipped_correlation_cap == 0


def test_corrscope_game_leaves_a_scheduled_ladder_alone_but_all_caps_it(settings):
    """The contrast the two arms exist to measure. Two rungs of one BTC hourly event are one
    bet on that hour's path — `all` refuses the second, `game` does not, and the difference
    between the arms is therefore the ladder axis alone."""
    _setup(settings,
           "Gmmsell1:lo=5,hi=10,maxyes=7,corrcap=1,corrscope=game;"
           "Gmmsell2:lo=5,hi=10,maxyes=7,corrcap=1,corrscope=all")
    day = _anchor()
    ev = _event([_mkt("KXBTCD-T1", "above 1", day), _mkt("KXBTCD-T2", "above 2", day)],
                "KXBTCD-26SEP0512", "KXBTCD")
    books = {"KXBTCD-T1": _ob(), "KXBTCD-T2": _ob()}
    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient([ev], books), settings).run_once(session)

    assert summ.per_book.get("Gmmsell1") == 2    # ladder cap untouched by the game scope
    assert summ.per_book.get("Gmmsell2") == 1    # one bet on this hour
    assert summ.skipped_correlation_cap == 1


def test_the_cap_persists_across_cycles(settings):
    """It counts OPEN positions, so a game entered in an earlier cycle still blocks — the live
    clustering accumulated over hours, not within one scan."""
    _setup(settings, "Gmmsell1:lo=5,hi=10,maxyes=7,corrcap=1,corrscope=game")
    day = _anchor()
    with db.session_scope() as session:
        MmSellTracker(FakeClient(
            [_event([_mkt("KXMLBTOTAL-A", "o8.5", day)], EV_TOTAL, "KXMLBTOTAL")],
            {"KXMLBTOTAL-A": _ob()}), settings).run_once(session)

    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient(
            [_event([_mkt("KXMLBSPREAD-A", "-1.5", day)], EV_SPREAD, "KXMLBSPREAD")],
            {"KXMLBSPREAD-A": _ob()}), settings).run_once(session)

    assert summ.per_book.get("Gmmsell1") is None
    assert summ.skipped_correlation_cap == 1
