"""The live smoke-test RUNNER and the incentive shadow METRIC providers (WS-020 Phase 1a).

The runner is the piece that turns a decision into an order, so its tests are about what it
does NOT do: it is inert unless armed, it never exceeds a cap, it places no order it did not
also mirror to the twin, and it has no branch that pulls a resting quote.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot.liquidity_incentive import live as limm
from kalshi_bot.liquidity_incentive import runner as run
from kalshi_bot.live.executor import LiveExecutor
from kalshi_bot.risk.manager import RiskManager

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)


class FakeClient:
    """GETs a canned book per ticker; a ticker mapped to an Exception raises it."""

    def __init__(self, books):
        self.books = books
        self.asked: list[str] = []

    def get_orderbook(self, ticker, depth=None):
        self.asked.append(ticker)
        book = self.books[ticker]
        if isinstance(book, Exception):
            raise book
        return book

    def create_events_order(self, order):
        self.placed.append(order)
        return {"order": {"order_id": f"K-{len(self.placed)}", "status": "resting"}}

    placed: list = []


def _book(yes, no):
    return {"orderbook": {"yes": [list(x) for x in yes], "no": [list(x) for x in no]}}


def _program(session, ticker, *, target=100.0, hours=24.0, series="KXTEST"):
    row = m.IncentiveProgram(
        program_id=f"p-{ticker}", market_ticker=ticker, event_ticker=series,
        series_ticker=series, incentive_type="liquidity",
        start_date=NOW - timedelta(days=1), end_date=NOW + timedelta(hours=hours),
        period_reward_raw=1_000_000, period_reward_unit="centi_cents", period_reward_usd=100.0,
        target_size=target, discount_factor_bps=9000, market_status="active",
        terms_hash=f"h-{ticker}", first_seen_at=NOW - timedelta(days=1), last_seen_at=NOW,
    )
    session.add(row)
    session.flush()
    return row


def _live_settings(settings):
    settings.bot_mode = "live"
    settings.kill_switch = False
    settings.live_enabled = True
    settings.live_strategies = limm.LIVE_TAG
    settings.liquidity_incentive_live_enabled = True
    settings.max_market_exposure = 25.0
    settings.max_total_exposure = 100.0
    settings.max_daily_loss = 5.0
    settings.live_kill_on_daily_loss = True
    return settings


def _exec(settings, client):
    return LiveExecutor(client, settings, RiskManager(settings))


@pytest.fixture
def live_db(settings):
    _live_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    return settings


# ------------------------------------------------------------------ inert by default


def test_not_armed_unless_every_switch_is_on(live_db, settings):
    client = FakeClient({})
    ex = _exec(settings, client)
    r = run.IncentiveLiveRunner(client, settings)
    assert r.armed(ex)
    for attr, value in (("liquidity_incentive_live_enabled", False), ("kill_switch", True),
                        ("live_enabled", False), ("live_strategies", ""), ("bot_mode", "paper")):
        _live_settings(settings)
        setattr(settings, attr, value)
        assert not r.armed(ex), attr


def test_an_unarmed_cycle_fetches_no_books_and_places_nothing(live_db, settings):
    settings.liquidity_incentive_live_enabled = False
    client = FakeClient({"KXTEST-A": _book([(20, 500)], [(70, 500)])})
    ex = _exec(settings, client)
    with db.session_scope() as s:
        _program(s, "KXTEST-A")
        out = run.IncentiveLiveRunner(client, settings).cycle(s, ex, {"cash_balance": 500.0},
                                                              now=NOW)
    assert out == {"armed": False, "considered": 0, "fetched": 0, "placed": 0,
                   "twin_opened": 0, "outcomes": {}}
    assert client.asked == []


# ------------------------------------------------------------------ candidate construction


def test_candidate_carries_native_per_side_prices_and_a_reference_per_side(live_db, settings):
    with db.session_scope() as s:
        program = _program(s, "KXTEST-A", target=100.0)
        c = run.candidate_from_book(
            program, _book([(20, 60), (19, 200)], [(70, 300), (69, 50)]), now=NOW)
    assert c["best_yes_bid"] == 20 and c["best_no_bid"] == 70
    assert c["yes_resting_total"] == 260.0 and c["no_resting_total"] == 350.0
    # Reference price walks down from the best bid to one fifth of Target Size (20 contracts):
    # the yes book's top level (60) already covers it, the no book's (300) likewise.
    assert c["reference_price_by_side"] == {limm.SIDE_YES: 20, limm.SIDE_NO: 70}
    assert c["program_hours_remaining"] == pytest.approx(24.0)


def test_a_book_that_cannot_be_fetched_is_an_outcome_not_an_exception(live_db, settings):
    client = FakeClient({"KXTEST-A": RuntimeError("502")})
    ex = _exec(settings, client)
    with db.session_scope() as s:
        _program(s, "KXTEST-A")
        out = run.IncentiveLiveRunner(client, settings).cycle(s, ex, {"cash_balance": 500.0},
                                                              now=NOW)
    assert out["outcomes"] == {run.SKIP_BOOK_ERROR: 1}
    assert out["placed"] == 0


# ------------------------------------------------------------------ selection + caps


def test_it_places_the_cheapest_downside_first_and_stops_at_the_open_order_cap(live_db, settings):
    # Three candidates, cheap sides at 5c, 9c and 12c. The cap is 3, so all three place, in
    # order — but the ORDER is the property under test, so make the cap bind at two.
    books = {
        "KXTEST-A": _book([(12, 500)], [(80, 500)]),
        "KXTEST-B": _book([(5, 500)], [(90, 500)]),
        "KXTEST-C": _book([(9, 500)], [(85, 500)]),
    }
    client = FakeClient(books)
    client.placed = []
    ex = _exec(settings, client)
    with db.session_scope() as s:
        for t in books:
            _program(s, t)
        # Two slots: one order already resting under this tag.
        s.add(m.LiveOrder(market_ticker="KXOTHER-Z", strategy=limm.LIVE_TAG, side="yes",
                          action="buy", limit_price=10, quantity=1, status="resting",
                          created_at=NOW))
        s.flush()
        out = run.IncentiveLiveRunner(client, settings).cycle(s, ex, {"cash_balance": 500.0},
                                                              now=NOW)
    assert out["placed"] == 2
    assert [o["ticker"] for o in client.placed] == ["KXTEST-B", "KXTEST-C"]


def test_the_book_budget_bounds_the_cycle_even_with_slots_free(live_db, settings):
    books = {"KXTEST-A": _book([(20, 500)], [(75, 500)])}
    client = FakeClient(books)
    client.placed = []
    ex = _exec(settings, client)
    with db.session_scope() as s:
        _program(s, "KXTEST-A")
        # $9.90 already committed: a 20c clip would take the book past its $10 ceiling.
        s.add(m.LiveOrder(market_ticker="KXOTHER-Z", strategy=limm.LIVE_TAG, side="yes",
                          action="buy", limit_price=99, quantity=10, status="resting",
                          created_at=NOW))
        s.flush()
        out = run.IncentiveLiveRunner(client, settings).cycle(s, ex, {"cash_balance": 500.0},
                                                              now=NOW)
    assert out["placed"] == 0
    assert out["outcomes"].get(limm.REFUSE_EXPOSURE_CAP) == 1
    assert client.placed == []


def test_an_excluded_series_is_never_quoted(live_db, settings):
    settings.liquidity_incentive_excluded_series = "kxtest"   # case-insensitive on purpose
    client = FakeClient({"KXTEST-A": _book([(10, 500)], [(85, 500)])})
    client.placed = []
    ex = _exec(settings, client)
    with db.session_scope() as s:
        _program(s, "KXTEST-A")
        out = run.IncentiveLiveRunner(client, settings).cycle(s, ex, {"cash_balance": 500.0},
                                                              now=NOW)
    assert out["placed"] == 0
    assert out["outcomes"].get(limm.REFUSE_EXCLUDED_SERIES) == 1


def test_a_program_ending_too_soon_is_never_selected(live_db, settings):
    client = FakeClient({"KXTEST-A": _book([(10, 500)], [(85, 500)])})
    ex = _exec(settings, client)
    with db.session_scope() as s:
        _program(s, "KXTEST-A", hours=limm.MIN_PROGRAM_HOURS_REMAINING - 0.5)
        out = run.IncentiveLiveRunner(client, settings).cycle(s, ex, {"cash_balance": 500.0},
                                                              now=NOW)
    assert out["considered"] == 0 and out["fetched"] == 0
    assert client.asked == []


def test_book_fetches_are_bounded_per_cycle(live_db, settings):
    settings.liquidity_incentive_live_max_book_fetches = 2
    books = {f"KXTEST-{i}": _book([(10, 500)], [(85, 500)]) for i in range(5)}
    client = FakeClient(books)
    client.placed = []
    ex = _exec(settings, client)
    with db.session_scope() as s:
        for i, t in enumerate(books):
            _program(s, t, hours=10.0 + i)
        out = run.IncentiveLiveRunner(client, settings).cycle(s, ex, {"cash_balance": 500.0},
                                                              now=NOW)
    assert out["considered"] == 5 and out["fetched"] == 2
    # Soonest-ending first: those are the programs whose payout can be confirmed first.
    assert client.asked == ["KXTEST-0", "KXTEST-1"]


# ------------------------------------------------------------------ the twin


def test_every_placed_order_is_mirrored_to_the_twin(live_db, settings):
    from kalshi_bot.twin.harness import TwinHarness

    settings.live_paper_twin_enabled = True
    settings.live_paper_twins = f"{limm.LIVE_TAG}:{limm.TWIN_TAG}"
    client = FakeClient({"KXTEST-A": _book([(20, 500)], [(75, 500)])})
    client.placed = []
    ex = _exec(settings, client)
    harness = TwinHarness(settings)
    with db.session_scope() as s:
        _program(s, "KXTEST-A")
        out = run.IncentiveLiveRunner(client, settings, twin_harness=harness).cycle(
            s, ex, {"cash_balance": 500.0}, now=NOW)
    assert out["placed"] == 1 and out["twin_opened"] == 1
    with db.session_scope() as s:
        trades = s.query(m.PaperTrade).filter(m.PaperTrade.strategy == limm.TWIN_TAG).all()
        assert len(trades) == 1
        # Same ticker, same side, same price, same size as the live order — a twin that sizes
        # differently from live is not a twin.
        assert trades[0].market_ticker == "KXTEST-A"
        assert trades[0].side == limm.SIDE_YES and int(trades[0].assumed_price) == 20
        assert int(trades[0].quantity) == 1


# ------------------------------------------------------------------ genuine liquidity


def test_the_runner_has_no_cancel_path():
    """The strategy's claim is that its quotes are genuine. A branch here that pulled a resting
    order when it looked likely to trade would make that claim false, so there must not be one:
    orders leave the book only by a fill, the shared per-order timeout, or a stand-down drain."""
    src = (run.__file__).replace(".pyc", ".py")
    text = open(src).read()
    assert "cancel_order" not in text
    assert "delete_order" not in text
