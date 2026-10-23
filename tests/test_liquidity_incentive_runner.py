"""The live RUNNER — pair entries, the book's own exits, and the incentive reward ledger.

The runner is the piece that turns decisions into orders, so its tests are about what it does
and does NOT do: it is inert unless armed, never exceeds a cap, mirrors every leg to the twin,
exits a held leg when a registered rule fires — and does NOTHING when it cannot establish the
position with certainty, because a marketable exit sent against a flat position opens one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy import select as sa_select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot.liquidity_incentive import live as limm
from kalshi_bot.liquidity_incentive import runner as run
from kalshi_bot.live.executor import LiveExecutor
from kalshi_bot.risk.manager import RiskManager

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)


class FakeClient:
    """GETs a canned book per ticker; a ticker mapped to an Exception raises it."""

    def __init__(self, books, cancel_fail=None):
        self.books = books
        self.asked: list[str] = []
        self.placed: list[dict] = []
        self.canceled: list[str] = []
        self.cancel_fail = cancel_fail

    def get_orderbook(self, ticker, depth=None):
        self.asked.append(ticker)
        book = self.books[ticker]
        if isinstance(book, Exception):
            raise book
        return book

    def create_events_order(self, order):
        self.placed.append(order)
        return {"order": {"order_id": f"K-{len(self.placed)}", "status": "resting"}}

    def cancel_events_order(self, order_id, *, exchange_index=None):
        if self.cancel_fail is not None:
            raise self.cancel_fail
        self.canceled.append(order_id)
        return {}

    def get_market(self, ticker):
        return {"market": {"ticker": ticker}}


@pytest.fixture(autouse=True)
def _generic_universe(monkeypatch):
    """Most tests here exercise mechanics on generic KXTEST markets; the §9.55 event-mention
    universe has its own tests below, which switch it back on."""
    monkeypatch.setattr(limm, "EVENT_MENTION_ONLY", False)
    # The slot/budget/event mechanics below are written against a two-market book; §9.56's
    # raised caps have their own tests at the end of this file.
    monkeypatch.setattr(limm, "MAX_OPEN_ORDERS", 2)
    monkeypatch.setattr(limm, "MAX_STRATEGY_EXPOSURE_USD", 50.0)
    monkeypatch.setattr(limm, "MAX_MARKETS_PER_EVENT", 1)
    # §9.55–9.62 tests were written against the 15-minute cutoff; §9.63's 48h step-aside has
    # its own tests at the end of this file.
    monkeypatch.setattr(limm, "ENTRY_STEP_ASIDE_HOURS", 0.0)


def _book(yes, no):
    return {"orderbook": {"yes": [list(x) for x in yes], "no": [list(x) for x in no]}}


def _program(session, ticker, *, target=200.0, hours=48.0, close_hours=240.0, series="KXTEST",
             event=None, age_hours=24.0):
    # `target` 200 against the 500-a-side fixture books is 2.5x — inside the universe rule.
    # One event per market by default (§9.15). The close is ten days out, inside the window.
    row = m.IncentiveProgram(
        program_id=f"p-{ticker}", market_ticker=ticker, event_ticker=event or ticker,
        series_ticker=series, incentive_type="liquidity",
        start_date=NOW - timedelta(hours=age_hours), end_date=NOW + timedelta(hours=hours),
        close_time=None if close_hours is None else NOW + timedelta(hours=close_hours),
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


def _cycle(client, settings, s, **kw):
    ex = _exec(settings, client)
    return run.IncentiveLiveRunner(client, settings, **kw).cycle(
        s, ex, {"cash_balance": 500.0}, now=NOW)


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
    client = FakeClient({"KXTEST-A": _book([(3, 500)], [(4, 500)])})
    with db.session_scope() as s:
        _program(s, "KXTEST-A")
        out = _cycle(client, settings, s)
    assert out == {"armed": False, "considered": 0, "fetched": 0, "placed": 0,
                   "twin_opened": 0, "outcomes": {}, "managed": {}}
    assert client.asked == []


# ------------------------------------------------------------------ candidate construction


def test_candidate_carries_native_prices_references_and_the_close(live_db, settings):
    with db.session_scope() as s:
        program = _program(s, "KXTEST-A", target=100.0, close_hours=300.0, age_hours=6.0)
        c = run.candidate_from_book(
            program, _book([(20, 60), (19, 200)], [(70, 300), (69, 50)]), now=NOW)
    assert c["best_yes_bid"] == 20 and c["best_no_bid"] == 70
    assert c["yes_resting_total"] == 260.0 and c["no_resting_total"] == 350.0
    assert c["reference_price_by_side"] == {limm.SIDE_YES: 20, limm.SIDE_NO: 70}
    assert c["program_hours_remaining"] == pytest.approx(48.0)
    assert c["hours_to_close"] == pytest.approx(300.0)
    assert c["program_age_hours"] == pytest.approx(6.0)
    assert (c["yes_touch_depth"], c["no_touch_depth"]) == (60.0, 300.0)


def test_a_book_that_cannot_be_fetched_is_an_outcome_not_an_exception(live_db, settings):
    client = FakeClient({"KXTEST-A": RuntimeError("502")})
    with db.session_scope() as s:
        _program(s, "KXTEST-A")
        out = _cycle(client, settings, s)
    assert out["outcomes"] == {run.SKIP_BOOK_ERROR: 1}
    assert out["placed"] == 0


# ------------------------------------------------------------------ the close-time window


@pytest.mark.parametrize("close_hours", [None, limm.MIN_HOURS_TO_CLOSE - 1,
                                         limm.MAX_HOURS_TO_CLOSE + 1, -190.0])
def test_a_market_outside_the_close_window_is_never_fetched(live_db, settings, close_hours):
    client = FakeClient({"KXTEST-A": _book([(3, 500)], [(4, 500)])})
    with db.session_scope() as s:
        _program(s, "KXTEST-A", close_hours=close_hours)
        out = _cycle(client, settings, s)
    assert out["considered"] == 0 and client.asked == [] and client.placed == []


# ------------------------------------------------------------------ pair entries + caps


def test_places_a_pair_on_one_market(live_db, settings, monkeypatch):
    monkeypatch.setattr(limm, "QUOTE_MODE", "pair")
    client = FakeClient({"KXTEST-A": _book([(3, 500)], [(4, 500)])})
    with db.session_scope() as s:
        _program(s, "KXTEST-A")
        out = _cycle(client, settings, s)
    assert out["placed"] == 1
    assert [(o["ticker"], o["side"]) for o in client.placed] == [
        ("KXTEST-A", "bid"), ("KXTEST-A", "ask")]
    assert client.placed[0]["count"] == client.placed[1]["count"]


def test_newest_program_first_and_stops_at_the_market_cap(live_db, settings):
    books = {t: _book([(3, 500)], [(4, 500)]) for t in ("KXTEST-A", "KXTEST-B", "KXTEST-C")}
    client = FakeClient(books)
    with db.session_scope() as s:
        _program(s, "KXTEST-A", age_hours=40.0)
        _program(s, "KXTEST-B", age_hours=1.0)
        _program(s, "KXTEST-C", age_hours=13.0)
        out = _cycle(client, settings, s)
    assert out["placed"] == limm.MAX_OPEN_ORDERS
    placed_markets = list(dict.fromkeys(o["ticker"] for o in client.placed))
    assert placed_markets == ["KXTEST-B", "KXTEST-C"]


def test_the_book_budget_bounds_the_cycle_even_with_slots_free(live_db, settings, monkeypatch):
    monkeypatch.setattr(limm, "QUOTE_MODE", "pair")
    client = FakeClient({"KXTEST-A": _book([(4, 500)], [(5, 500)])})
    with db.session_scope() as s:
        _program(s, "KXTEST-A")
        # $40 committed elsewhere; a 4/5 pair commits $18 more, past the $50 ceiling.
        s.add(m.LiveOrder(market_ticker="KXOTHER-Z", strategy=limm.LIVE_TAG, side="yes",
                          action="buy", limit_price=40, quantity=100, status="resting",
                          created_at=NOW))
        s.flush()
        out = _cycle(client, settings, s)
    assert out["placed"] == 0
    assert out["outcomes"].get(limm.REFUSE_EXPOSURE_CAP) == 1
    assert client.placed == []


def test_an_excluded_series_is_never_quoted(live_db, settings):
    settings.liquidity_incentive_excluded_series = "kxtest"   # case-insensitive on purpose
    client = FakeClient({"KXTEST-A": _book([(3, 500)], [(4, 500)])})
    with db.session_scope() as s:
        _program(s, "KXTEST-A")
        out = _cycle(client, settings, s)
    assert out["placed"] == 0
    # Dropped before any book is fetched (§9.39); the decision layer still refuses it too.
    assert out["considered"] == 0 and client.asked == []


def test_a_program_ending_too_soon_is_never_selected(live_db, settings):
    client = FakeClient({"KXTEST-A": _book([(3, 500)], [(4, 500)])})
    with db.session_scope() as s:
        _program(s, "KXTEST-A", hours=limm.MIN_PROGRAM_HOURS_REMAINING - 0.5)
        out = _cycle(client, settings, s)
    assert out["considered"] == 0 and out["fetched"] == 0
    assert client.asked == []


def test_book_fetches_are_bounded_per_cycle(live_db, settings):
    settings.liquidity_incentive_live_max_book_fetches = 2
    books = {f"KXTEST-{i}": _book([(3, 500)], [(4, 500)]) for i in range(5)}
    client = FakeClient(books)
    with db.session_scope() as s:
        for i, t in enumerate(books):
            _program(s, t, age_hours=10.0 + i)
        out = _cycle(client, settings, s)
    assert out["considered"] == 5 and out["fetched"] == 2
    assert client.asked == ["KXTEST-0", "KXTEST-1"]


# ------------------------------------------------------------------ the twin


def test_both_legs_are_mirrored_to_the_twin(live_db, settings, monkeypatch):
    monkeypatch.setattr(limm, "QUOTE_MODE", "pair")
    from kalshi_bot.twin.harness import TwinHarness

    settings.live_paper_twin_enabled = True
    settings.live_paper_twins = f"{limm.LIVE_TAG}:{limm.TWIN_TAG}"
    client = FakeClient({"KXTEST-A": _book([(2, 500)], [(5, 500)])})
    with db.session_scope() as s:
        _program(s, "KXTEST-A")
        out = _cycle(client, settings, s, twin_harness=TwinHarness(settings))
    assert out["placed"] == 1 and out["twin_opened"] == 2
    qty = limm.pair_quantity(2, 5)
    with db.session_scope() as s:
        trades = s.query(m.PaperTrade).filter(m.PaperTrade.strategy == limm.TWIN_TAG).all()
        assert sorted((t.side, int(t.assumed_price), int(t.quantity)) for t in trades) == [
            ("no", 5, qty), ("yes", 2, qty)]


# ------------------------------------------------------- event cap (thesis §9.15)


def test_two_markets_of_one_event_are_one_commitment(live_db, settings):
    books = {
        "KXRT-RES-93": _book([(3, 500)], [(4, 500)]),
        "KXRT-RES-94": _book([(2, 500)], [(3, 500)]),
    }
    client = FakeClient(books)
    with db.session_scope() as s:
        for t in books:
            _program(s, t, event="KXRT-RES")
        out = _cycle(client, settings, s)
    assert out["placed"] == 1
    assert {o["ticker"] for o in client.placed} == {"KXRT-RES-94"}   # wider edge wins the tie
    assert out["outcomes"].get(limm.REFUSE_EVENT_CAP) == 1


def test_another_live_book_holding_the_event_blocks_it(live_db, settings):
    client = FakeClient({"KXRT-RES-93": _book([(3, 500)], [(4, 500)])})
    with db.session_scope() as s:
        _program(s, "KXRT-RES-93", event="KXRT-RES")
        s.add(m.LiveOrder(market_ticker="KXRT-RES-97", event_ticker="KXRT-RES",
                          strategy="Fmmsell10", side="no", action="buy", limit_price=93,
                          quantity=1, status="filled", created_at=NOW - timedelta(days=4)))
        s.add(m.Position(market_ticker="KXRT-RES-97", captured_at=NOW, side="no", quantity=-1,
                         avg_price=93.0, market_exposure=0.93))
        s.flush()
        out = _cycle(client, settings, s)
    assert out["placed"] == 0 and client.placed == []
    assert out["outcomes"].get(limm.REFUSE_EVENT_CAP) == 1


def test_an_unrelated_event_is_not_blocked(live_db, settings):
    client = FakeClient({"KXOTHER-1": _book([(4, 500)], [(5, 500)])})
    with db.session_scope() as s:
        _program(s, "KXOTHER-1", event="KXOTHER")
        s.add(m.LiveOrder(market_ticker="KXRT-RES-97", event_ticker="KXRT-RES",
                          strategy="Fmmsell10", side="no", action="buy", limit_price=93,
                          quantity=1, status="filled", created_at=NOW - timedelta(days=4)))
        s.add(m.Position(market_ticker="KXRT-RES-97", captured_at=NOW, side="no", quantity=-1,
                         avg_price=93.0, market_exposure=0.93))
        s.flush()
        out = _cycle(client, settings, s)
    assert out["placed"] == 1


# ------------------------------------------------------------------ held positions (§9.37)
#
# A held leg: this book's YES bid at 40 filled 13, its NO leg at 55 may still rest. The
# snapshot Kalshi reported THIS cycle agrees. Each test changes one thing.

HELD = "KXHELD-1"


def _held(s, *, side="yes", entry=40, qty=13, snap_qty=None, snap_age_s=0, other_leg=True,
          close_hours=24.0):
    _program(s, HELD, close_hours=close_hours)
    s.add(m.LiveOrder(market_ticker=HELD, event_ticker=HELD, strategy=limm.LIVE_TAG, side=side,
                      action="buy", limit_price=entry, quantity=qty, status="filled",
                      kalshi_order_id="K-HELD", client_order_id="held", created_at=NOW))
    s.add(m.Fill(kalshi_fill_id="F-HELD", kalshi_order_id="K-HELD", market_ticker=HELD,
                 side=side, action="buy", price=entry, quantity=qty))
    if other_leg:
        s.add(m.LiveOrder(market_ticker=HELD, event_ticker=HELD, strategy=limm.LIVE_TAG,
                          side=limm.other_side(side), action="buy", limit_price=55, quantity=qty,
                          status="resting", kalshi_order_id="K-LEG", client_order_id="leg",
                          created_at=NOW))
    signed = snap_qty if snap_qty is not None else (qty if side == "yes" else -qty)
    s.add(m.Position(market_ticker=HELD, captured_at=NOW - timedelta(seconds=snap_age_s),
                     side=side, quantity=signed, quantity_fp=signed, avg_price=float(entry)))
    s.flush()


def _managed(out):
    return out["managed"]


def test_stop_loss_cancels_the_resting_leg_then_sells_marketably(live_db, settings):
    client = FakeClient({HELD: _book([(20, 500)], [(75, 500)])})   # YES bid fell 40 -> 20
    with db.session_scope() as s:
        _held(s)
        out = _cycle(client, settings, s)
    assert _managed(out) == {"stop_loss:placed": 1}
    assert client.canceled == ["K-LEG"]
    exit_order = client.placed[0]
    assert exit_order["ticker"] == HELD and exit_order["side"] == "ask"
    assert exit_order["time_in_force"] == "immediate_or_cancel"
    assert exit_order["count"] == "13.00"
    assert exit_order["price"] == f"{(20 - limm.EXIT_SLIPPAGE_CENTS) / 100:.4f}"


def test_take_profit_on_a_held_no_leg_buys_yes(live_db, settings):
    client = FakeClient({HELD: _book([(10, 500)], [(80, 500)])})   # NO bid rose 55 -> 80
    with db.session_scope() as s:
        _held(s, side="no", entry=55, other_leg=False)
        out = _cycle(client, settings, s)
    assert _managed(out) == {"take_profit:placed": 1}
    exit_order = client.placed[0]
    assert exit_order["side"] == "bid"
    assert exit_order["price"] == f"{(100 - (80 - limm.EXIT_SLIPPAGE_CENTS)) / 100:.4f}"


def test_pre_close_flattens_even_a_flat_mark(live_db, settings):
    client = FakeClient({HELD: _book([(40, 500)], [(55, 500)])})
    with db.session_scope() as s:
        _held(s, close_hours=0.5)
        out = _cycle(client, settings, s)
    assert _managed(out) == {"pre_close:placed": 1}


def test_a_held_leg_with_nothing_resting_gets_a_profitable_exit_leg(live_db, settings):
    # YES bid 39: under the 40c entry (no scratch), above the 20c stop.
    client = FakeClient({HELD: _book([(39, 500)], [(50, 500)])})
    with db.session_scope() as s:
        _held(s, other_leg=False)
        out = _cycle(client, settings, s)
    assert _managed(out) == {"exit_leg:placed": 1}
    leg = client.placed[0]
    # Held YES at 40: rest a NO bid at the 50c touch (== YES ask 50c), locking 10c on 13.
    assert (leg["side"], leg["price"], leg["post_only"], leg["count"]) == (
        "ask", "0.5000", True, "13.00")


def test_a_held_leg_with_its_opposite_leg_resting_is_left_alone(live_db, settings):
    client = FakeClient({HELD: _book([(38, 500)], [(55, 500)])})
    with db.session_scope() as s:
        _held(s)
        out = _cycle(client, settings, s)
    assert _managed(out) == {"holding": 1}
    assert client.placed == [] and client.canceled == []


def test_a_stale_snapshot_means_do_nothing(live_db, settings):
    """A marketable exit against a position that is already flat OPENS one."""
    client = FakeClient({HELD: _book([(20, 500)], [(75, 500)])})
    with db.session_scope() as s:
        _held(s, snap_age_s=int(limm.POSITION_FRESH_SECONDS) + 60)
        out = _cycle(client, settings, s)
    assert _managed(out) == {"no_fresh_position": 1}
    assert client.placed == [] and client.canceled == []


def test_kalshi_and_our_own_fills_must_agree_on_the_side(live_db, settings):
    client = FakeClient({HELD: _book([(20, 500)], [(75, 500)])})
    with db.session_scope() as s:
        _held(s, snap_qty=-13)            # Kalshi says NO, our fills say YES: mid-update
        out = _cycle(client, settings, s)
    assert _managed(out) == {"unsettled_state": 1}
    assert client.placed == [] and client.canceled == []


def test_exit_size_never_exceeds_our_own_fills(live_db, settings):
    client = FakeClient({HELD: _book([(20, 500)], [(75, 500)])})
    with db.session_scope() as s:
        _held(s, snap_qty=40)             # the account holds more than this book bought
        _cycle(client, settings, s)
    assert client.placed[0]["count"] == "13.00"


def test_a_closed_market_awaiting_settlement_is_left_alone(live_db, settings):
    """The KXBIGGESTQUAKE shape — and the operator's standing "leave them alone"."""
    client = FakeClient({HELD: _book([(20, 500)], [(75, 500)])})
    with db.session_scope() as s:
        _held(s, close_hours=-190.0)
        out = _cycle(client, settings, s)
    assert _managed(out) == {"closed_awaiting_settlement": 1}
    assert client.placed == [] and client.canceled == [] and HELD not in client.asked


def test_a_market_another_book_is_in_is_not_ours_to_close(live_db, settings):
    client = FakeClient({HELD: _book([(20, 500)], [(75, 500)])})
    with db.session_scope() as s:
        _held(s)
        s.add(m.LiveOrder(market_ticker=HELD, strategy="Fmmsell10", side="no", action="buy",
                          limit_price=90, quantity=1, status="filled", created_at=NOW))
        s.flush()
        out = _cycle(client, settings, s)
    assert _managed(out) == {"shared_ticker": 1}
    assert client.placed == [] and client.canceled == []


def test_a_failed_cancel_blocks_the_exit(live_db, settings):
    client = FakeClient({HELD: _book([(20, 500)], [(75, 500)])}, cancel_fail=RuntimeError("404"))
    with db.session_scope() as s:
        _held(s)
        out = _cycle(client, settings, s)
    assert _managed(out) == {"stop_loss:cancel_pending": 1}
    assert client.placed == []


def test_an_exit_in_flight_is_not_doubled(live_db, settings):
    client = FakeClient({HELD: _book([(20, 500)], [(75, 500)])})
    with db.session_scope() as s:
        _held(s, other_leg=False)
        s.add(m.LiveOrder(market_ticker=HELD, strategy=limm.LIVE_TAG, side="yes", action="sell",
                          limit_price=18, quantity=13, status="submitted",
                          client_order_id="limmexit:stop_loss:x", created_at=NOW))
        s.flush()
        out = _cycle(client, settings, s)
    assert _managed(out) == {"stop_loss:in_flight": 1}
    assert client.placed == []


def test_exits_give_up_after_the_attempt_cap(live_db, settings):
    client = FakeClient({HELD: _book([(20, 500)], [(75, 500)])})
    with db.session_scope() as s:
        _held(s, other_leg=False)
        for i in range(limm.EXIT_MAX_ATTEMPTS):
            s.add(m.LiveOrder(market_ticker=HELD, strategy=limm.LIVE_TAG, side="yes",
                              action="sell", limit_price=18, quantity=13, status="canceled",
                              client_order_id=f"limmexit:stop_loss:{i}", created_at=NOW))
        s.flush()
        out = _cycle(client, settings, s)
    assert _managed(out) == {"stop_loss:exhausted": 1}
    assert client.placed == []


def test_a_completed_round_trip_cancels_whatever_still_rests(live_db, settings):
    client = FakeClient({HELD: _book([(20, 500)], [(75, 500)])})
    with db.session_scope() as s:
        _held(s, snap_qty=0)             # YES 13 bought ...
        s.add(m.LiveOrder(market_ticker=HELD, strategy=limm.LIVE_TAG, side="yes", action="sell",
                          limit_price=45, quantity=13, status="filled", kalshi_order_id="K-OUT",
                          client_order_id="limmexit:take_profit:x", created_at=NOW))
        s.add(m.Fill(kalshi_fill_id="F-OUT", kalshi_order_id="K-OUT", market_ticker=HELD,
                     side="yes", action="sell", price=45, quantity=13))   # ... and 13 sold
        s.flush()
        out = _cycle(client, settings, s)
    assert _managed(out) == {"round_trip_cleanup": 1}
    assert client.canceled == ["K-LEG"]


def test_an_evenly_part_filled_pair_keeps_quoting(live_db, settings):
    """YES and NO each filled 5 of 13: flat, but 8 a side still rest as a balanced pair."""
    client = FakeClient({})
    with db.session_scope() as s:
        for side, price, koid in (("yes", 40, "K-Y"), ("no", 55, "K-N")):
            s.add(m.LiveOrder(market_ticker="KXPART-1", strategy=limm.LIVE_TAG, side=side,
                              action="buy", limit_price=price, quantity=13, status="partial",
                              kalshi_order_id=koid, client_order_id=koid, created_at=NOW))
            s.add(m.Fill(kalshi_fill_id=f"F-{koid}", kalshi_order_id=koid,
                         market_ticker="KXPART-1", side=side, action="buy", price=price,
                         quantity=5))
        s.add(m.Position(market_ticker="KXPART-1", captured_at=NOW, side="yes", quantity=0,
                         quantity_fp=0))
        s.flush()
        out = _cycle(client, settings, s)
    assert _managed(out) == {"flat": 1}
    assert client.canceled == []


def test_a_resting_pair_that_never_filled_is_never_cancelled(live_db, settings):
    """Genuine liquidity: nothing held, nothing pulled."""
    client = FakeClient({})
    with db.session_scope() as s:
        for side, price, koid in (("yes", 40, "K-1"), ("no", 55, "K-2")):
            s.add(m.LiveOrder(market_ticker="KXREST-1", strategy=limm.LIVE_TAG, side=side,
                              action="buy", limit_price=price, quantity=13, status="resting",
                              kalshi_order_id=koid, client_order_id=koid, created_at=NOW))
        s.add(m.Position(market_ticker="KXREST-1", captured_at=NOW, side="yes", quantity=0,
                         quantity_fp=0))
        s.flush()
        out = _cycle(client, settings, s)
    assert _managed(out) == {"flat": 1}
    assert client.canceled == [] and client.placed == []


# --- the reward ledger (thesis §9.23) -------------------------------------------------------
#
# Kalshi publishes a programme's terms and never our credit against them, so a liquidity reward
# is only visible as the part of a balance change nothing else explains. The arithmetic itself
# is covered in `test_incentive_reward_ledger.py`; these cover the RUNNER's half — that the
# reading happens on its own schedule, persists, announces a material residual, survives its own
# failures, and above all keeps running when the book is stood down.
#
# It lives here and not in the collector because the collector is handed an
# `IncentiveReadOnlyKalshi`, which has no `get_balance` — production said so out loud twice
# before this moved.


class LedgerClient(FakeClient):
    """`FakeClient` plus the three portfolio reads the ledger makes."""

    def __init__(self, books=None, *, balance=18_651, fills=None, settlements=None, fail=False):
        super().__init__(books or {})
        self.balance = balance
        self._fills = fills or []
        self._settlements = settlements or []
        self.fail = fail
        self.balance_calls = 0

    def get_balance(self):
        self.balance_calls += 1
        if self.fail:
            raise RuntimeError("balance unavailable")
        return {"balance": self.balance}

    def get_fills(self, **params):
        return {"fills": self._fills, "cursor": None}

    def get_settlements(self, **params):
        return {"settlements": self._settlements, "cursor": None}


def _ledger_rows():
    with db.session_scope() as s:
        return s.execute(
            sa_select(m.IncentiveBalanceObservation)
            .order_by(m.IncentiveBalanceObservation.at, m.IncentiveBalanceObservation.id)
        ).scalars().all()


def _clear_ledger():
    """Each ledger row differences the PREVIOUS one, so a neighbouring test's row would silently
    become this test's anchor and make its residual meaningless."""
    with db.session_scope() as s:
        s.execute(sa_delete(m.IncentiveBalanceObservation))
        s.execute(sa_delete(m.IncentiveCollectorEvent))
        s.commit()


def test_the_first_cycle_anchors_the_ledger_without_claiming_a_residual(live_db, settings):
    """Differencing the first balance against nothing would report a credit the size of the
    whole account. The anchor row leaves every attribution column NULL — a zero residual and an
    unmeasurable one are different claims."""
    _clear_ledger()
    client = LedgerClient(balance=18_651)
    ex = _exec(settings, client)
    with db.session_scope() as s:
        run.IncentiveLiveRunner(client, settings).cycle(s, ex, {"cash_balance": 500.0}, now=NOW)
    rows = _ledger_rows()
    assert len(rows) == 1
    assert rows[0].balance_cents == 18_651
    assert rows[0].residual_cents is None
    assert rows[0].prev_balance_cents is None


def test_the_ledger_runs_even_when_the_book_is_stood_down(live_db, settings):
    """THE point of putting this before the armed gate. Kalshi credits only AFTER a programme
    ends, so a reward for quoting we already did can land days later — including after the book
    has been switched off. A ledger that stopped with the book would miss the payment it exists
    to catch."""
    _clear_ledger()
    settings.liquidity_incentive_live_enabled = False
    client = LedgerClient(balance=18_651)
    ex = _exec(settings, client)
    with db.session_scope() as s:
        out = run.IncentiveLiveRunner(client, settings).cycle(s, ex, {"cash_balance": 500.0},
                                                              now=NOW)
    assert out["armed"] is False, "the book must still be stood down"
    assert len(_ledger_rows()) == 1, "but the ledger still took its reading"


def test_cash_that_no_trade_explains_is_persisted_as_a_residual(live_db, settings):
    _clear_ledger()
    client = LedgerClient(balance=18_651)
    ex = _exec(settings, client)
    r = run.IncentiveLiveRunner(client, settings)
    with db.session_scope() as s:
        r.cycle(s, ex, {"cash_balance": 500.0}, now=NOW)
    client.balance = 18_654
    with db.session_scope() as s:
        r.cycle(s, ex, {"cash_balance": 500.0}, now=NOW + timedelta(minutes=20))
    rows = _ledger_rows()
    assert len(rows) == 2
    assert rows[1].residual_cents == 3
    assert rows[1].prev_balance_cents == 18_651
    assert rows[1].presumed_transfer is False


def test_a_material_residual_is_announced_in_the_event_stream(live_db, settings):
    """A credit must be visible to anyone reading the collector's events, not only to whoever
    thinks to query the new table."""
    _clear_ledger()
    client = LedgerClient(balance=18_651)
    ex = _exec(settings, client)
    r = run.IncentiveLiveRunner(client, settings)
    with db.session_scope() as s:
        r.cycle(s, ex, {"cash_balance": 500.0}, now=NOW)
    client.balance = 18_654
    with db.session_scope() as s:
        r.cycle(s, ex, {"cash_balance": 500.0}, now=NOW + timedelta(minutes=20))
    with db.session_scope() as s:
        kinds = s.execute(
            sa_select(m.IncentiveCollectorEvent.kind)
            .where(m.IncentiveCollectorEvent.kind == run.EV_REWARD_RESIDUAL)
        ).scalars().all()
    assert kinds == [run.EV_REWARD_RESIDUAL]


def test_the_ledger_nets_an_exit_against_the_position_it_saw_last_window(live_db, settings):
    """Thesis §9.40: each reading stores where every market stands, so the next window can
    credit the $1 Kalshi pays when a YES we buy meets a NO we hold. The CO gas stop-loss —
    16 of 35 held NO closed by buying YES at 90c — must reconcile to zero, not +1600c."""
    _clear_ledger()

    class PosLedgerClient(LedgerClient):
        positions = [{"ticker": "KXCO", "position_fp": "-35.00"}]

        def get_positions(self, **params):
            return {"market_positions": self.positions, "cursor": None}

    client = PosLedgerClient(balance=10_000)
    ex = _exec(settings, client)
    r = run.IncentiveLiveRunner(client, settings)
    with db.session_scope() as s:
        r.cycle(s, ex, {"cash_balance": 500.0}, now=NOW)
    client.balance = 10_160
    client._fills = [{"ticker": "KXCO", "side": "yes", "action": "buy", "count_fp": "16.00",
                      "yes_price_dollars": "0.90"}]
    client.positions = [{"ticker": "KXCO", "position_fp": "-19.00"}]
    with db.session_scope() as s:
        r.cycle(s, ex, {"cash_balance": 500.0}, now=NOW + timedelta(minutes=20))
    rows = _ledger_rows()
    assert rows[0].notes_json["positions"] == {"KXCO": -35}
    assert rows[1].residual_cents == 0
    assert rows[1].presumed_transfer is False
    assert rows[1].notes_json["positions"] == {"KXCO": -19}


def test_a_deposit_is_recorded_but_never_announced_as_a_reward(live_db, settings):
    """The worst possible false positive. A book risking at most $10 did not earn $50."""
    _clear_ledger()
    client = LedgerClient(balance=18_651)
    ex = _exec(settings, client)
    r = run.IncentiveLiveRunner(client, settings)
    with db.session_scope() as s:
        r.cycle(s, ex, {"cash_balance": 500.0}, now=NOW)
    client.balance = 23_651
    with db.session_scope() as s:
        r.cycle(s, ex, {"cash_balance": 500.0}, now=NOW + timedelta(minutes=20))
    rows = _ledger_rows()
    assert rows[1].residual_cents == 5_000
    assert rows[1].presumed_transfer is True
    with db.session_scope() as s:
        kinds = s.execute(
            sa_select(m.IncentiveCollectorEvent.kind)
            .where(m.IncentiveCollectorEvent.kind == run.EV_REWARD_RESIDUAL)
        ).scalars().all()
    assert kinds == []


def test_the_reading_has_its_own_slower_schedule(live_db, settings):
    """Each reading costs paged portfolio calls, and a residual only means anything over a
    window long enough for a reward to have been credited in — so it must not fire every cycle
    the way the placement logic does."""
    _clear_ledger()
    settings.liquidity_incentive_balance_seconds = 900.0
    client = LedgerClient(balance=18_651)
    ex = _exec(settings, client)
    r = run.IncentiveLiveRunner(client, settings)
    with db.session_scope() as s:
        r.cycle(s, ex, {"cash_balance": 500.0}, now=NOW)
    assert len(_ledger_rows()) == 1
    with db.session_scope() as s:
        r.cycle(s, ex, {"cash_balance": 500.0}, now=NOW + timedelta(seconds=60))
    assert len(_ledger_rows()) == 1, "read again before its interval elapsed"
    with db.session_scope() as s:
        r.cycle(s, ex, {"cash_balance": 500.0}, now=NOW + timedelta(seconds=901))
    assert len(_ledger_rows()) == 2


def test_a_portfolio_read_that_fails_cannot_stop_the_book_placing(live_db, settings):
    """The measurement must never be able to break the thing that spends the money. This is the
    exact failure that happened in production when the ledger was wired to the read-only shadow
    client — it recorded a loop_error and carried on, which is the behaviour kept here."""
    _clear_ledger()
    client = LedgerClient({"KXTEST-A": _book([(3, 500)], [(4, 500)])}, fail=True)
    ex = _exec(settings, client)
    with db.session_scope() as s:
        _program(s, "KXTEST-A")
        out = run.IncentiveLiveRunner(client, settings).cycle(s, ex, {"cash_balance": 500.0},
                                                              now=NOW)
    assert _ledger_rows() == []
    assert out["placed"] == 1, "the book must still have placed its order"
    with db.session_scope() as s:
        details = s.execute(
            sa_select(m.IncentiveCollectorEvent.detail)
            .where(m.IncentiveCollectorEvent.kind == "loop_error")
        ).scalars().all()
    assert any("reward_ledger" in (d or "") for d in details)


def test_the_ledger_is_not_wired_to_the_read_only_shadow_client(live_db, settings):
    """Regression guard for the production failure. `IncentiveReadOnlyKalshi` deliberately
    exposes only market-data GETs; it has no portfolio surface, and widening it to reach the
    balance would put research code inside our account. If someone re-adds `get_balance` there,
    this fails and they have to argue for it on purpose."""
    from kalshi_bot.liquidity_incentive.readonly import IncentiveReadOnlyKalshi

    for name in ("get_balance", "get_fills", "get_settlements"):
        assert not hasattr(IncentiveReadOnlyKalshi, name), (
            f"{name} on the shadow's read-only client puts research code in the portfolio")


# --- closed markets do not hold slots (thesis §9.38) ----------------------------------------


def _stuck_quake(s, ticker, *, close_hours=-190.0):
    """A filled leg on a market that closed long ago and has not settled — the KXBIGGESTQUAKE
    shape that held every slot on 2026-09-25."""
    _program(s, ticker, close_hours=close_hours)
    s.add(m.LiveOrder(market_ticker=ticker, event_ticker=ticker, strategy=limm.LIVE_TAG,
                      side="yes", action="buy", limit_price=1, quantity=1, status="filled",
                      kalshi_order_id=f"K-{ticker}", client_order_id=ticker, created_at=NOW))
    s.add(m.Position(market_ticker=ticker, captured_at=NOW, side="yes", quantity=1,
                     quantity_fp=1, avg_price=1.0))


def test_closed_markets_awaiting_settlement_do_not_block_new_pairs(live_db, settings):
    """Production 2026-09-25: three closed, unsettled quake positions against a cap of two left
    `no_slots` on every cycle — the book could never quote again until Kalshi settled them."""
    client = FakeClient({"KXTEST-A": _book([(3, 500)], [(4, 500)])})
    with db.session_scope() as s:
        for t in ("KXQUAKE-1", "KXQUAKE-2", "KXQUAKE-3"):
            _stuck_quake(s, t)
        _program(s, "KXTEST-A")
        s.flush()
        from kalshi_bot import repository as repo
        assert repo.count_live_book_open(s, limm.LIVE_TAG) == 3
        assert repo.count_live_book_open_tradeable(s, limm.LIVE_TAG, NOW) == 0
        out = _cycle(client, settings, s)
    assert out["placed"] == 1
    assert out["managed"] == {"closed_awaiting_settlement": 3}
    assert {o["ticker"] for o in client.placed} == {"KXTEST-A"}


def test_a_market_that_has_not_closed_still_holds_its_slot(live_db, settings):
    from kalshi_bot import repository as repo

    with db.session_scope() as s:
        _stuck_quake(s, "KXOPEN-1", close_hours=5.0)       # held, but still trading
        s.add(m.LiveOrder(market_ticker="KXNOCLOSE-1", strategy=limm.LIVE_TAG, side="yes",
                          action="buy", limit_price=4, quantity=1, status="resting",
                          created_at=NOW))                  # no known close time: counted
        s.flush()
        assert repo.count_live_book_open_tradeable(s, limm.LIVE_TAG, NOW) == 2


def test_closed_markets_still_count_against_the_budget(live_db, settings):
    from kalshi_bot import repository as repo

    with db.session_scope() as s:
        _stuck_quake(s, "KXQUAKE-1")
        s.flush()
        assert repo.live_strategy_exposure(s, limm.LIVE_TAG) == pytest.approx(0.01)


# --- excluded series never consume the fetch budget (thesis §9.39) ---------------------------


def test_excluded_series_are_dropped_before_any_book_is_fetched(live_db, settings):
    """Production 2026-09-25: the eight first-ranked programmes were all in excluded series,
    so the bounded fetch budget was spent on them every cycle and nothing was ever placed."""
    settings.liquidity_incentive_excluded_series = "KXOTHERBOOK"
    settings.liquidity_incentive_live_max_book_fetches = 2
    books = {f"KXOTHERBOOK-{i}": _book([(3, 500)], [(4, 500)]) for i in range(3)}
    books["KXTEST-OK"] = _book([(3, 500)], [(4, 500)])
    client = FakeClient(books)
    with db.session_scope() as s:
        for i in range(3):
            _program(s, f"KXOTHERBOOK-{i}", series="KXOTHERBOOK", age_hours=1.0 + i)
        _program(s, "KXTEST-OK", age_hours=40.0)
        out = _cycle(client, settings, s)
    assert out["considered"] == 1
    assert client.asked == ["KXTEST-OK"]
    assert out["placed"] == 1


# --- a refused book is not fetched again for a while (thesis §9.43) ---------------------------


def test_a_book_refused_market_cools_down_so_the_fetch_budget_reaches_the_next_ones(live_db,
                                                                                   settings):
    """Production 2026-09-30: the eight newest programmes were all too deep, so every cycle
    fetched the same eight and never reached the rest."""
    settings.liquidity_incentive_live_max_book_fetches = 2
    books = {f"KXTEST-DEEP{i}": _book([(3, 5000)], [(4, 5000)]) for i in range(2)}
    books["KXTEST-OK"] = _book([(3, 500)], [(4, 500)])
    client = FakeClient(books)
    with db.session_scope() as s:
        for i in range(2):
            _program(s, f"KXTEST-DEEP{i}", age_hours=1.0 + i)
        _program(s, "KXTEST-OK", age_hours=40.0)
        runner = run.IncentiveLiveRunner(client, settings)
        first = runner.cycle(s, _exec(settings, client), {"cash_balance": 500.0}, now=NOW)
        assert first["placed"] == 0 and first["outcomes"] == {limm.REFUSE_BOOK_TOO_DEEP: 2}
        second = runner.cycle(s, _exec(settings, client), {"cash_balance": 500.0},
                              now=NOW + timedelta(minutes=3))
    assert second["cooling_down"] == 2 and second["placed"] == 1
    assert client.asked[-1] == "KXTEST-OK"


def test_the_cooldown_expires(live_db, settings):
    settings.liquidity_incentive_live_max_book_fetches = 1
    client = FakeClient({"KXTEST-DEEP": _book([(3, 5000)], [(4, 5000)])})
    with db.session_scope() as s:
        _program(s, "KXTEST-DEEP")
        runner = run.IncentiveLiveRunner(client, settings)
        runner.cycle(s, _exec(settings, client), {"cash_balance": 500.0}, now=NOW)
        later = NOW + timedelta(seconds=run.BOOK_REFUSAL_COOLDOWN_SECONDS + 1)
        again = runner.cycle(s, _exec(settings, client), {"cash_balance": 500.0}, now=later)
    assert again["fetched"] == 1 and client.asked == ["KXTEST-DEEP", "KXTEST-DEEP"]


def test_the_cooldown_survives_a_restart(live_db, settings):
    """§9.44: production redeploys several times a day; a fresh runner must pick the cooldown
    up from the last saved copy instead of refetching the same refused books."""
    settings.liquidity_incentive_live_max_book_fetches = 2
    books = {f"KXTEST-DEEP{i}": _book([(3, 5000)], [(4, 5000)]) for i in range(2)}
    books["KXTEST-OK"] = _book([(3, 500)], [(4, 500)])
    client = FakeClient(books)
    with db.session_scope() as s:
        for i in range(2):
            _program(s, f"KXTEST-DEEP{i}", age_hours=1.0 + i)
        _program(s, "KXTEST-OK", age_hours=40.0)
        run.IncentiveLiveRunner(client, settings).cycle(
            s, _exec(settings, client), {"cash_balance": 500.0}, now=NOW)
    with db.session_scope() as s:
        restarted = run.IncentiveLiveRunner(client, settings)
        out = restarted.cycle(s, _exec(settings, client), {"cash_balance": 500.0},
                              now=NOW + timedelta(minutes=3))
    assert out["cooling_down"] == 2 and out["placed"] == 1


def test_an_expired_saved_cooldown_is_ignored(live_db, settings):
    settings.liquidity_incentive_live_max_book_fetches = 1
    client = FakeClient({"KXTEST-DEEP": _book([(3, 5000)], [(4, 5000)])})
    with db.session_scope() as s:
        _program(s, "KXTEST-DEEP")
        run.IncentiveLiveRunner(client, settings).cycle(
            s, _exec(settings, client), {"cash_balance": 500.0}, now=NOW)
    with db.session_scope() as s:
        later = NOW + timedelta(seconds=run.BOOK_REFUSAL_COOLDOWN_SECONDS + 1)
        out = run.IncentiveLiveRunner(client, settings).cycle(
            s, _exec(settings, client), {"cash_balance": 500.0}, now=later)
    assert "cooling_down" not in out and out["fetched"] == 1


# --- a market on another matching-engine shard is rested, not retried (thesis §9.45) ----------


class ShardedFakeClient(FakeClient):
    def __init__(self, books, shards):
        super().__init__(books)
        self.shards = shards

    def get_market(self, ticker):
        return {"market": {"ticker": ticker, "exchange_index": self.shards.get(ticker, 0)}}

    def get_market_exchange_index(self, ticker):
        return self.shards.get(ticker, 0)


def test_a_non_default_shard_market_cools_down_and_the_next_one_is_placed(live_db, settings):
    """Production 2026-10-01: two oil pairs (exchange_index 2) were answered 409, never rested,
    and held both slots for hours."""
    settings.liquidity_incentive_live_max_book_fetches = 1
    books = {"KXTEST-OIL": _book([(3, 500)], [(4, 500)]),
             "KXTEST-OK": _book([(3, 500)], [(4, 500)])}
    client = ShardedFakeClient(books, {"KXTEST-OIL": 2})
    with db.session_scope() as s:
        _program(s, "KXTEST-OIL", age_hours=1.0)
        _program(s, "KXTEST-OK", age_hours=40.0)
        runner = run.IncentiveLiveRunner(client, settings)
        first = runner.cycle(s, _exec(settings, client), {"cash_balance": 500.0}, now=NOW)
        assert first["placed"] == 0
        assert first["outcomes"] == {limm.GATE_NON_DEFAULT_SHARD: 1}
        assert client.placed == []
        second = runner.cycle(s, _exec(settings, client), {"cash_balance": 500.0},
                              now=NOW + timedelta(minutes=3))
    assert second["cooling_down"] == 1 and second["placed"] == 1
    assert client.asked == ["KXTEST-OIL", "KXTEST-OK"]


# --- §9.47: replace a stale resting pair only when a better one is ready ---------------------

def _held_pair(s, ticker, *, hours_old, qty=250, yes=3, no=4):
    _program(s, ticker)
    for side, price, koid in (("yes", yes, f"K-{ticker}-y"), ("no", no, f"K-{ticker}-n")):
        s.add(m.LiveOrder(market_ticker=ticker, event_ticker=ticker, strategy=limm.LIVE_TAG,
                          side=side, action="buy", limit_price=price, quantity=qty,
                          status="resting", kalshi_order_id=koid, client_order_id=koid,
                          created_at=NOW - timedelta(hours=hours_old)))
    s.flush()


def _status_by_ticker(s):
    return {r.market_ticker: r.status for r in s.scalars(sa_select(m.LiveOrder))}


def _replace_books(new_depth):
    return {
        # Held books include our own 250 a side: KXHELD-1 is the deep (weak) one.
        "KXHELD-1": _book([(3, 2000)], [(4, 2000)]),
        "KXHELD-2": _book([(3, 1000)], [(4, 1000)]),
        "KXTEST-N": _book([(3, new_depth)], [(4, new_depth)]),
    }


def test_a_fresh_full_book_fetches_nothing_and_keeps_its_pairs(live_db, settings):
    client = FakeClient(_replace_books(500))
    with db.session_scope() as s:
        _held_pair(s, "KXHELD-1", hours_old=1)
        _held_pair(s, "KXHELD-2", hours_old=1)
        _program(s, "KXTEST-N")
        out = _cycle(client, settings, s)
    assert out["outcomes"] == {run.SKIP_NO_SLOTS: 1}
    # No candidate book is fetched. The only GETs are the book's own two markets, snapshotted
    # for the dashboard (§9.53) — at most once per OWN_BOOK_SNAPSHOT_SECONDS.
    assert client.asked == ["KXHELD-1", "KXHELD-2"] and out["book_snapshots"] == 2
    assert client.canceled == [] and client.placed == []


def test_a_live_quote_is_kept_even_when_another_market_is_better(live_db, settings):
    # §9.49: KXTEST-N earns more per hour than KXHELD-1, but KXHELD-1 is not DEAD. Leaving a
    # programme early forfeits what it accrued (paid at programme end, $1.00 minimum), so stay.
    client = FakeClient(_replace_books(500))
    with db.session_scope() as s:
        _held_pair(s, "KXHELD-1", hours_old=25)
        _held_pair(s, "KXHELD-2", hours_old=25)
        _program(s, "KXTEST-N")
        out = _cycle(client, settings, s)
    check = out["replace_check"]
    assert check["new_per_hour"] > check["held_per_hour"] * 1.25        # the old rule would move
    assert check["held_per_hour"] >= check["new_per_hour"] * limm.DEAD_QUOTE_FRACTION
    assert "replaced" not in out["outcomes"] and out["placed"] == 0
    assert client.canceled == [] and client.placed == []


def test_a_dead_quote_is_replaced(live_db, settings):
    books = _replace_books(500)
    books["KXHELD-1"] = _book([(3, 40000)], [(4, 40000)])             # our 250 is now ~nothing
    client = FakeClient(books)
    with db.session_scope() as s:
        _held_pair(s, "KXHELD-1", hours_old=25)
        _held_pair(s, "KXHELD-2", hours_old=25)
        _program(s, "KXTEST-N")
        out = _cycle(client, settings, s)
        statuses = _status_by_ticker(s)
    assert out["outcomes"].get("replaced") == 1 and out["placed"] == 1
    check = out["replace_check"]
    assert check["held"] == "KXHELD-1"
    assert check["held_per_hour"] < check["new_per_hour"] * limm.DEAD_QUOTE_FRACTION
    assert sorted(client.canceled) == ["K-KXHELD-1-n", "K-KXHELD-1-y"]
    assert {o["ticker"] for o in client.placed} == {"KXTEST-N"}
    assert statuses["KXHELD-2"] == "resting"


def test_the_programme_list_is_read_once_a_cycle(live_db, settings, monkeypatch):
    """Retire, reprice, candidates and replace all read the running programmes; each read was a
    full scan of `incentive_programs` (WS-024). One read a cycle, same decisions."""
    from kalshi_bot.liquidity_incentive import programs as progs

    reads = []
    real = progs.current_programs

    def counted(*a, **k):
        reads.append(k.get("now"))
        return real(*a, **k)

    monkeypatch.setattr(progs, "current_programs", counted)
    books = _replace_books(500)
    books["KXHELD-1"] = _book([(3, 40000)], [(4, 40000)])
    client = FakeClient(books)
    r = run.IncentiveLiveRunner(client, settings)
    with db.session_scope() as s:
        _held_pair(s, "KXHELD-1", hours_old=25)
        _held_pair(s, "KXHELD-2", hours_old=25)
        _program(s, "KXTEST-N")
        out = r.cycle(s, _exec(settings, client), {"cash_balance": 500.0}, now=NOW)
    assert out["outcomes"].get("replaced") == 1 and out["placed"] == 1
    assert reads == [NOW]
    # A new cycle reads again rather than reusing last cycle's list.
    with db.session_scope() as s:
        r.cycle(s, _exec(settings, client), {"cash_balance": 500.0},
                now=NOW + timedelta(seconds=60))
    assert reads == [NOW, NOW + timedelta(seconds=60)]


def test_a_dead_quote_younger_than_the_minimum_hold_is_kept(live_db, settings):
    # §9.55: a held quote is not swapped before MIN_HOLD_SECONDS, even when it is DEAD; the
    # book swapped gas -> IASAND -> gas inside two hours on 2026-10-14.
    books = _replace_books(500)
    books["KXHELD-1"] = _book([(3, 40000)], [(4, 40000)])
    client = FakeClient(books)
    with db.session_scope() as s:
        _held_pair(s, "KXHELD-1", hours_old=5)
        _held_pair(s, "KXHELD-2", hours_old=5)
        _program(s, "KXTEST-N")
        out = _cycle(client, settings, s)
    assert out["outcomes"] == {run.SKIP_NO_SLOTS: 1}
    assert client.canceled == [] and client.placed == []


def test_a_stale_pair_is_kept_when_nothing_is_better(live_db, settings):
    client = FakeClient(_replace_books(1900))
    with db.session_scope() as s:
        _held_pair(s, "KXHELD-1", hours_old=5)
        _held_pair(s, "KXHELD-2", hours_old=5)
        _program(s, "KXTEST-N")
        out = _cycle(client, settings, s)
        statuses = _status_by_ticker(s)
    assert "replaced" not in out["outcomes"] and out["placed"] == 0
    assert client.canceled == [] and client.placed == []
    assert statuses == {"KXHELD-1": "resting", "KXHELD-2": "resting"}


def test_a_pair_with_a_fill_is_never_swapped(live_db, settings):
    client = FakeClient(_replace_books(500))
    with db.session_scope() as s:
        _held_pair(s, "KXHELD-1", hours_old=5)
        _held_pair(s, "KXHELD-2", hours_old=5)
        for t in ("KXHELD-1", "KXHELD-2"):
            s.add(m.Position(market_ticker=t, captured_at=NOW, side="yes", quantity=5,
                             quantity_fp=5))
        _program(s, "KXTEST-N")
        s.flush()
        out = _cycle(client, settings, s)
    assert "replaced" not in out["outcomes"]
    assert client.canceled == [] and client.placed == []


def test_a_resting_pair_whose_programme_ended_is_taken_down(live_db, settings):
    client = FakeClient({})
    with db.session_scope() as s:
        _held_pair(s, "KXHELD-1", hours_old=1)
        prog = s.scalars(sa_select(m.IncentiveProgram).where(
            m.IncentiveProgram.market_ticker == "KXHELD-1")).one()
        prog.end_date = NOW - timedelta(hours=1)
        s.flush()
        out = _cycle(client, settings, s)
    assert out["outcomes"].get("program_ended") == 1
    assert sorted(client.canceled) == ["K-KXHELD-1-n", "K-KXHELD-1-y"]


# --- §9.48: one bid on the cheap side at its reference price; out after a fill ----------------

def test_places_one_bid_on_the_cheap_side_at_its_reference(live_db, settings):
    # Target 200 -> reference = first level where cumulative size reaches 40. YES: 23c holds 100
    # -> reference 23 (too dear). NO: 4c holds 30, 3c brings 330 -> reference 3.
    client = FakeClient({"KXTEST-A": _book([(23, 100), (20, 400)], [(4, 30), (3, 300), (2, 300)])})
    with db.session_scope() as s:
        _program(s, "KXTEST-A")
        out = _cycle(client, settings, s)
        rows = list(s.scalars(sa_select(m.LiveOrder)))
    assert out["placed"] == 1 and len(client.placed) == 1
    o = client.placed[0]
    # A NO bid at 3c is a YES "ask" at 97c on the wire.
    assert (o["ticker"], o["side"], o["price"], o["post_only"]) == ("KXTEST-A", "ask", "0.9700", True)
    assert o["count"] == f"{min(limm.MAX_CONTRACTS_PER_ORDER, 1000 // 3)}.00"
    assert [(r.side, r.limit_price) for r in rows] == [("no", 3)]


def test_a_held_leg_back_at_entry_is_held_with_a_take_profit_exit_leg(live_db, settings):
    # §9.53: no scratch at entry. The leg is held and the opposite-side exit bid rests at the
    # touch (50c), which locks more than the +5c floor (cap 100 - 40 - 5 = 55).
    client = FakeClient({HELD: _book([(40, 500)], [(50, 500)])})
    with db.session_scope() as s:
        _held(s, other_leg=False)
        out = _cycle(client, settings, s)
        legs = [r for r in s.scalars(sa_select(m.LiveOrder))
                if (r.client_order_id or "").startswith("limmexitleg:")]
    (code,) = _managed(out)
    assert code.startswith("exit_leg:"), code
    assert [(r.side, r.limit_price) for r in legs] == [("no", 50)]


def test_a_held_leg_up_five_cents_takes_profit(live_db, settings):
    client = FakeClient({HELD: _book([(45, 500)], [(50, 500)])})
    with db.session_scope() as s:
        _held(s, other_leg=False)
        out = _cycle(client, settings, s)
    (code,) = _managed(out)
    assert code.startswith(limm.EXIT_TAKE_PROFIT + ":"), code


def test_a_resting_exit_leg_does_not_count_against_the_book_budget(live_db, settings):
    # §9.53 (1A): on 2026-10-13 an 85c x100 exit bid read as $85 against the $50 cap and froze
    # every entry. It can only net the held leg flat, so only the held leg's cost counts.
    from kalshi_bot import repository as repo
    with db.session_scope() as s:
        _held(s, side="no", entry=10, qty=100, other_leg=False)
        s.add(m.LiveOrder(market_ticker=HELD, event_ticker=HELD, strategy=limm.LIVE_TAG,
                          side="yes", action="buy", limit_price=85, quantity=100,
                          status="resting", kalshi_order_id="K-EXITLEG",
                          client_order_id="limmexitleg:abc", created_at=NOW))
        s.flush()
        assert repo.live_strategy_exposure(s, limm.LIVE_TAG) == pytest.approx(10.0)


# --- an account with no free cash: one refusal, then back off -------------------------------

class BrokeClient(FakeClient):
    def create_events_order(self, order):
        from kalshi_bot.kalshi.errors import KalshiAPIError

        self.placed.append(order)
        raise KalshiAPIError(400, '{"error":{"code":"insufficient_balance"}}',
                             "/trade-api/v2/portfolio/events/orders")


def test_insufficient_balance_stops_the_cycle_and_backs_off(live_db, settings):
    books = {t: _book([(3, 500)], [(4, 500)]) for t in ("KXTEST-A", "KXTEST-B", "KXTEST-C")}
    client = BrokeClient(books)
    with db.session_scope() as s:
        for t in books:
            _program(s, t)
        ex = _exec(settings, client)
        runner = run.IncentiveLiveRunner(client, settings)
        out = runner.cycle(s, ex, {"cash_balance": 500.0}, now=NOW)
        assert len(client.placed) == 1                      # not one attempt per candidate
        assert out["outcomes"].get(limm.REJECT_INSUFFICIENT_BALANCE) == 1
        again = runner.cycle(s, ex, {"cash_balance": 500.0}, now=NOW + timedelta(minutes=5))
        assert again["outcomes"] == {"balance_backoff": 1} and len(client.placed) == 1
        later = runner.cycle(s, ex, {"cash_balance": 500.0},
                             now=NOW + timedelta(seconds=limm.BALANCE_BACKOFF_SECONDS + 60))
        assert "balance_backoff" not in later["outcomes"] and len(client.placed) == 2


# --- §9.49: re-price in place; rank by projected payout ----------------------------------------

def test_a_quote_behind_its_reference_is_repriced_in_the_same_market(live_db, settings):
    # Held: NO 2c x500 (in the book). Someone stacked 3c, so the NO reference is now 3c and our
    # bid scores at a discount. Move it to 3c in the SAME market rather than leave the programme.
    t = "KXTEST-R"
    client = FakeClient({t: _book([(23, 100), (20, 400)], [(4, 30), (3, 300), (2, 800)])})
    with db.session_scope() as s:
        _program(s, t)
        s.add(m.LiveOrder(market_ticker=t, event_ticker=t, strategy=limm.LIVE_TAG, side="no",
                          action="buy", limit_price=2, quantity=500, status="resting",
                          kalshi_order_id="K-R", client_order_id="K-R",
                          created_at=NOW - timedelta(hours=2)))
        s.flush()
        out = _cycle(client, settings, s)
        rows = [(r.side, r.limit_price, r.status) for r in s.scalars(
            sa_select(m.LiveOrder).where(m.LiveOrder.market_ticker == t)
            .order_by(m.LiveOrder.id))]
    assert out["outcomes"].get("repriced") == 1
    assert client.canceled == ["K-R"]
    assert [(o["ticker"], o["price"]) for o in client.placed] == [(t, "0.9700")]
    assert rows == [("no", 2, "canceled"), ("no", 3, "resting")]


def test_a_quote_at_its_reference_is_left_alone(live_db, settings):
    t = "KXTEST-R"
    client = FakeClient({t: _book([(23, 100), (20, 400)], [(4, 30), (3, 800)])})
    with db.session_scope() as s:
        _program(s, t)
        s.add(m.LiveOrder(market_ticker=t, event_ticker=t, strategy=limm.LIVE_TAG, side="no",
                          action="buy", limit_price=3, quantity=500, status="resting",
                          kalshi_order_id="K-R", client_order_id="K-R",
                          created_at=NOW - timedelta(hours=2)))
        s.flush()
        out = _cycle(client, settings, s)
    assert "repriced" not in out["outcomes"] and client.canceled == []


def test_rank_by_projected_payout_sorts_and_drops_hopeless(monkeypatch):
    monkeypatch.setattr(run, "quote_rate", lambda cq: cq[0]["r"])
    a = ({"r": 0.10, "program_hours_remaining": 100}, "a")    # $10 projected
    b = ({"r": 0.50, "program_hours_remaining": 48}, "b")     # $24
    c = ({"r": 0.01, "program_hours_remaining": 50}, "c")     # $0.50 -> under $1, dropped
    kept, dropped = run.rank_by_projected_payout([a, b, c])
    assert [q for _, q in kept] == ["b", "a"]
    assert [q for _, q in dropped] == ["c"]


# --- programme discovery inside the runner (WS-024 D1) --------------------------------------
#
# The shadow collector on `evo` was the only writer of `incentive_programs`; with it gone the
# list this book reads froze. The armed runner now refreshes it itself — only when nothing else
# has, in a savepoint, through the GET-only wrapper, and never at the cost of the cycle.


def _listed(pid, ticker, *, hours=48.0):
    return {"id": pid, "market_id": f"m-{pid}", "market_ticker": ticker,
            "incentive_type": "liquidity", "incentive_description": "Rest size near the touch.",
            "start_date": (NOW - timedelta(hours=1)).isoformat(),
            "end_date": (NOW + timedelta(hours=hours)).isoformat(), "period_reward": 1_000_000,
            "paid_out": False, "discount_factor_bps": 9000, "target_size_fp": "200.00"}


class DiscoveryClient(FakeClient):
    """FakeClient plus the discovery GETs, counting how often the listing is asked for."""

    def __init__(self, books, programs=()):
        super().__init__(books)
        self.programs = list(programs)
        self.listings: list[str] = []

    def iter_incentive_programs(self, **kw):
        status = kw.get("status", "active")
        self.listings.append(status)
        yield from (self.programs if status == "active" else [])

    def get_market(self, ticker):
        return {"market": {"ticker": ticker, "event_ticker": ticker, "status": "active",
                           "title": ticker,
                           "close_time": (NOW + timedelta(hours=240)).isoformat()}}

    def get_series(self, series_ticker):
        return {"series": {"fee_type": "quadratic", "fee_multiplier": 1.0}}


def _cycles(s):
    return s.scalars(sa_select(m.IncentiveDiscoveryCycle)).all()


def _clear_discovery(s):
    s.execute(sa_delete(m.IncentiveDiscoveryCycle))
    s.execute(sa_delete(m.IncentiveProgram))


def test_a_stale_programme_list_is_refreshed_and_the_new_programme_is_quoted(
        live_db, settings, monkeypatch):
    monkeypatch.setattr(limm, "QUOTE_MODE", "pair")
    client = DiscoveryClient({"KXTEST-NEW": _book([(3, 500)], [(4, 500)])},
                             programs=[_listed("p-new", "KXTEST-NEW")])
    with db.session_scope() as s:
        _clear_discovery(s)
        out = _cycle(client, settings, s)
        assert out["discovery"]["new"] == 1 and out["discovery"]["errors"] == 0
        assert client.listings == ["active", "paid_out"]
        assert len(_cycles(s)) == 1
        row = s.scalars(sa_select(m.IncentiveProgram)).one()
        assert row.market_ticker == "KXTEST-NEW" and run._aware(row.last_seen_at) == NOW
        # Same cycle, fresh list: the new programme is a candidate and gets its pair.
        assert out["placed"] == 1


def test_a_fresh_programme_list_is_left_to_whoever_keeps_it_fresh(live_db, settings):
    client = DiscoveryClient({}, programs=[_listed("p-x", "KXTEST-X")])
    with db.session_scope() as s:
        _clear_discovery(s)
        s.add(m.IncentiveDiscoveryCycle(started_at=NOW - timedelta(seconds=60)))
        s.flush()
        out = _cycle(client, settings, s)
        assert "discovery" not in out and client.listings == []
        assert len(_cycles(s)) == 1


def test_a_programme_list_older_than_the_cadence_is_refreshed(live_db, settings):
    client = DiscoveryClient({}, programs=[_listed("p-x", "KXTEST-X")])
    with db.session_scope() as s:
        _clear_discovery(s)
        s.add(m.IncentiveDiscoveryCycle(started_at=NOW - timedelta(seconds=301)))
        s.flush()
        out = _cycle(client, settings, s)
        assert out["discovery"]["new"] == 1 and len(_cycles(s)) == 2


def test_runner_discovery_is_off_with_its_flag(live_db, settings):
    settings.liquidity_incentive_runner_discovery = False
    client = DiscoveryClient({}, programs=[_listed("p-x", "KXTEST-X")])
    with db.session_scope() as s:
        _clear_discovery(s)
        out = _cycle(client, settings, s)
        assert "discovery" not in out and client.listings == [] and _cycles(s) == []


def test_an_unarmed_runner_runs_no_discovery(live_db, settings):
    settings.liquidity_incentive_live_enabled = False
    client = DiscoveryClient({}, programs=[_listed("p-x", "KXTEST-X")])
    with db.session_scope() as s:
        _clear_discovery(s)
        out = _cycle(client, settings, s)
        assert out["armed"] is False and client.listings == [] and _cycles(s) == []


def test_discovery_goes_through_the_get_only_wrapper(live_db, settings):
    from kalshi_bot.liquidity_incentive.readonly import IncentiveReadOnlyKalshi

    client = DiscoveryClient({}, programs=[_listed("p-x", "KXTEST-X")])
    r = run.IncentiveLiveRunner(client, settings)
    with db.session_scope() as s:
        _clear_discovery(s)
        r.cycle(s, _exec(settings, client), {"cash_balance": 500.0}, now=NOW)
    assert isinstance(r._discovery_client, IncentiveReadOnlyKalshi)
    for name in ("create_events_order", "cancel_events_order", "create_order", "cancel_order"):
        assert not hasattr(r._discovery_client, name)


def test_a_failed_discovery_rolls_back_alone_and_the_book_still_places(
        live_db, settings, monkeypatch):
    """The savepoint: whatever the pass wrote before failing is gone, the cycle's own work is
    not, and the failure is a summary entry rather than an exception."""
    monkeypatch.setattr(limm, "QUOTE_MODE", "pair")
    from kalshi_bot.liquidity_incentive import programs as progs

    def half_then_boom(client, session, **kw):
        session.add(m.IncentiveDiscoveryCycle(started_at=NOW))
        session.flush()
        raise RuntimeError("db went away mid-pass")

    monkeypatch.setattr(progs, "run_discovery", half_then_boom)
    client = DiscoveryClient({"KXTEST-A": _book([(3, 500)], [(4, 500)])})
    r = run.IncentiveLiveRunner(client, settings)
    with db.session_scope() as s:
        _clear_discovery(s)
        _program(s, "KXTEST-A")
        out = r.cycle(s, _exec(settings, client), {"cash_balance": 500.0}, now=NOW)
        assert "db went away" in out["discovery"]["failed"]
        assert _cycles(s) == []
        assert out["placed"] == 1
    # Not retried every cycle: the next attempt waits for the discovery cadence.
    calls = []
    monkeypatch.setattr(progs, "run_discovery", lambda *a, **k: calls.append(1))
    with db.session_scope() as s:
        out = r.cycle(s, _exec(settings, client), {"cash_balance": 500.0},
                      now=NOW + timedelta(seconds=60))
    assert calls == [] and "discovery" not in out


def test_a_full_book_still_refreshes_the_programme_list(live_db, settings, monkeypatch):
    """The no-slots early return comes after the refresh: a book holding every slot is the
    one that most needs to know when a programme it rests in has ended."""
    monkeypatch.setattr(limm, "MAX_OPEN_ORDERS", 0)
    client = DiscoveryClient({}, programs=[_listed("p-x", "KXTEST-X")])
    with db.session_scope() as s:
        _clear_discovery(s)
        out = _cycle(client, settings, s)
        assert out["outcomes"].get(run.SKIP_NO_SLOTS) == 1
        assert out["discovery"]["new"] == 1


def test_runner_discovery_is_bounded_per_pass(live_db, settings):
    settings.liquidity_incentive_runner_discovery_max_new_terms = 2
    client = DiscoveryClient({}, programs=[_listed(f"p-{i}", f"KXTEST-{i}") for i in range(5)])
    with db.session_scope() as s:
        _clear_discovery(s)
        out = _cycle(client, settings, s)
        assert out["discovery"]["new"] == 2 and out["discovery"]["deferred"] == 3
        assert len(s.scalars(sa_select(m.IncentiveProgram)).all()) == 2


# --- the book snapshots its own markets for the dashboard (§9.53) ----------------------------

def test_a_cycle_snapshots_the_markets_the_book_holds_then_waits(live_db, settings):
    client = FakeClient({HELD: _book([(40, 500)], [(50, 500)])})
    with db.session_scope() as s:
        _held(s, other_leg=False)
        out = _cycle(client, settings, s)
        rows = list(s.scalars(sa_select(m.IncentiveMarketSnapshot)
                              .where(m.IncentiveMarketSnapshot.market_ticker == HELD)))
    assert out.get("book_snapshots") == 1
    assert [(r.best_yes_bid, r.best_no_bid) for r in rows] == [(40, 50)]


# --- §9.54: an exit leg may not tie up more than MAX_EXIT_LEG_DOLLARS ------------------------

def test_a_dear_exit_leg_is_not_placed(live_db, settings):
    # The 2026-10-13 TSWIF case: YES 1c x499 held, NO touch 51c -> a $254 exit bid.
    client = FakeClient({HELD: _book([(2, 100)], [(51, 500)])})
    with db.session_scope() as s:
        _held(s, entry=1, qty=499, other_leg=False)
        out = _cycle(client, settings, s)
    assert _managed(out) == {"holding:exit_leg_over_cap": 1}
    assert client.placed == []


def test_a_resting_exit_leg_over_the_cap_is_cancelled(live_db, settings):
    client = FakeClient({HELD: _book([(2, 100)], [(51, 500)])})
    with db.session_scope() as s:
        _held(s, entry=1, qty=499, other_leg=False)
        s.add(m.LiveOrder(market_ticker=HELD, event_ticker=HELD, strategy=limm.LIVE_TAG,
                          side="no", action="buy", limit_price=51, quantity=499,
                          status="resting", kalshi_order_id="K-DEAR",
                          client_order_id="limmexitleg:dear", created_at=NOW))
        s.flush()
        out = _cycle(client, settings, s)
    assert _managed(out) == {"exit_leg_over_cap:cancelled": 1}
    assert client.canceled == ["K-DEAR"] and client.placed == []


def test_exit_leg_cap_is_the_per_order_budget():
    assert limm.MAX_EXIT_LEG_DOLLARS == limm.MAX_ORDER_DOLLARS == 10.0
    assert limm.exit_leg_affordable(50, 20) and not limm.exit_leg_affordable(51, 499)


# --- §9.55: event-tied mention markets only, and step aside before the event -------------------

def _mention(s, ticker, *, event, hours=48.0, close_hours=240.0, series="KXTRUMPMENTION",
             title="What will Donald Trump say during Midterm Rally?"):
    row = _program(s, ticker, series=series, event=event, hours=hours, close_hours=close_hours)
    row.market_title = title
    s.flush()
    return row


def test_only_event_tied_mention_markets_are_quoted(live_db, settings, monkeypatch):
    monkeypatch.setattr(limm, "EVENT_MENTION_ONLY", True)
    book = _book([(23, 100), (20, 400)], [(4, 30), (3, 300), (2, 300)])
    client = FakeClient({"KXTRUMPMENTION-26SEP20-PEAC": book, "KXTRUMPSAY-26SEP25-X": book,
                         "KXTEST-A": book, "KXTRUMPMENTION-26SEP18-SOON": book})
    with db.session_scope() as s:
        _mention(s, "KXTRUMPMENTION-26SEP20-PEAC", event="KXTRUMPMENTION-26SEP20")
        # A "say X before <date>" window: no event to step aside for.
        _mention(s, "KXTRUMPSAY-26SEP25-X", event="KXTRUMPSAY-26SEP25", series="KXTRUMPSAY",
                 title='Will Trump say "X" before Sep 25, 2026?')
        _program(s, "KXTEST-A")                                  # not a mention market
        # Event-tied, but its programme ends at the event 3h from now: under 6h to earn.
        _mention(s, "KXTRUMPMENTION-26SEP18-SOON", event="KXTRUMPMENTION-26SEP18", hours=3.0)
        out = _cycle(client, settings, s)
    assert client.asked == ["KXTRUMPMENTION-26SEP20-PEAC"]
    assert out["placed"] == 1 and client.placed[0]["ticker"] == "KXTRUMPMENTION-26SEP20-PEAC"


def _resting(s, ticker, *, hours_old=2):
    s.add(m.LiveOrder(market_ticker=ticker, event_ticker=ticker, strategy=limm.LIVE_TAG,
                      side="no", action="buy", limit_price=3, quantity=300, status="resting",
                      kalshi_order_id=f"K-{ticker}", client_order_id=f"K-{ticker}",
                      created_at=NOW - timedelta(hours=hours_old)))
    s.flush()


def test_a_resting_bid_steps_aside_before_its_event(live_db, settings, monkeypatch):
    monkeypatch.setattr(limm, "EVENT_MENTION_ONLY", True)
    book = _book([(23, 100)], [(4, 30), (3, 300)])
    client = FakeClient({"KXTRUMPMENTION-26SEP18-A": book, "KXTRUMPMENTION-26SEP25-B": book})
    with db.session_scope() as s:
        # The programme ends (the rally starts) in 10 minutes: inside the 15-minute cutoff.
        _mention(s, "KXTRUMPMENTION-26SEP18-A", event="KXTRUMPMENTION-26SEP18",
                 hours=10 / 60)
        _resting(s, "KXTRUMPMENTION-26SEP18-A")
        # A week away: stays.
        _mention(s, "KXTRUMPMENTION-26SEP25-B", event="KXTRUMPMENTION-26SEP25", hours=168.0)
        _resting(s, "KXTRUMPMENTION-26SEP25-B")
        out = _cycle(client, settings, s)
        statuses = _status_by_ticker(s)
    assert out["outcomes"].get("pre_event") == 1
    assert client.canceled == ["K-KXTRUMPMENTION-26SEP18-A"]
    assert statuses["KXTRUMPMENTION-26SEP25-B"] == "resting"


def test_a_resting_bid_outside_the_universe_is_cancelled(live_db, settings, monkeypatch):
    monkeypatch.setattr(limm, "EVENT_MENTION_ONLY", True)
    client = FakeClient({"KXAAAGASMINM-26OCT31-3.90": _book([(2, 500)], [(97, 500)])})
    with db.session_scope() as s:
        _program(s, "KXAAAGASMINM-26OCT31-3.90", series="KXAAAGASMINM")
        _resting(s, "KXAAAGASMINM-26OCT31-3.90")
        out = _cycle(client, settings, s)
    assert out["outcomes"].get("out_of_universe") == 1
    assert client.canceled == ["K-KXAAAGASMINM-26OCT31-3.90"]


def test_a_held_position_is_not_touched_by_the_pre_event_step(live_db, settings, monkeypatch):
    # Only a purely resting bid steps aside; a filled leg belongs to the exit rules.
    monkeypatch.setattr(limm, "EVENT_MENTION_ONLY", True)
    runner = run.IncentiveLiveRunner(FakeClient({}), settings)
    with db.session_scope() as s:
        _mention(s, "KXTRUMPMENTION-26SEP18-A", event="KXTRUMPMENTION-26SEP18", hours=0.1)
        s.add(m.LiveOrder(market_ticker="KXTRUMPMENTION-26SEP18-A", strategy=limm.LIVE_TAG,
                          side="no", action="buy", limit_price=3, quantity=300, status="filled",
                          kalshi_order_id="K-f", client_order_id="K-f",
                          created_at=NOW - timedelta(hours=2)))
        s.flush()
        assert runner._retire_before_event(s, _exec(settings, runner.client), NOW) == {}


# --- §9.56: up to MAX_MARKETS_PER_EVENT markets of one event ------------------------------------

def test_up_to_three_markets_of_one_event_are_quoted(live_db, settings, monkeypatch):
    monkeypatch.setattr(limm, "MAX_OPEN_ORDERS", 10)
    monkeypatch.setattr(limm, "MAX_STRATEGY_EXPOSURE_USD", 100.0)
    monkeypatch.setattr(limm, "MAX_MARKETS_PER_EVENT", 3)
    book = _book([(23, 100), (20, 400)], [(4, 30), (3, 300), (2, 300)])
    tickers = [f"KXTEST-EV-{i}" for i in range(5)]
    client = FakeClient({t: book for t in tickers})
    with db.session_scope() as s:
        for t in tickers:
            _program(s, t, event="KXTEST-EV")
        out = _cycle(client, settings, s)
    assert out["placed"] == 3
    assert out["outcomes"].get(limm.REFUSE_EVENT_CAP) == 2


# --- §9.57: a bid priced out of the band for an hour is cancelled -----------------------------

def _oob_runner(settings, book):
    client = FakeClient({"KXTEST-OOB": book})
    return client, run.IncentiveLiveRunner(client, settings)


def test_a_bid_out_of_the_band_for_an_hour_is_cancelled(live_db, settings):
    # Our NO bid rests at 3c; 100 NO contracts at 30c put the reference (Target/5 = 40) at 30c.
    out_book = _book([(23, 500)], [(30, 100), (3, 300)])
    client, runner = _oob_runner(settings, out_book)
    with db.session_scope() as s:
        _program(s, "KXTEST-OOB")
        _resting(s, "KXTEST-OOB")
        ex = _exec(settings, client)
        assert runner._retire_out_of_band(s, ex, NOW) == 0          # clock starts
        for minute in range(5, 60, 5):
            assert runner._retire_out_of_band(s, ex, NOW + timedelta(minutes=minute)) == 0
        assert client.canceled == []
        assert runner._retire_out_of_band(s, ex, NOW + timedelta(minutes=61)) == 1
    assert client.canceled == ["K-KXTEST-OOB"]


def test_a_return_inside_the_band_restarts_the_clock(live_db, settings):
    out_book = _book([(23, 500)], [(30, 100), (3, 300)])
    in_book = _book([(23, 500)], [(4, 100), (3, 300)])
    client, runner = _oob_runner(settings, out_book)
    with db.session_scope() as s:
        _program(s, "KXTEST-OOB")
        _resting(s, "KXTEST-OOB")
        ex = _exec(settings, client)
        runner._retire_out_of_band(s, ex, NOW)
        client.books["KXTEST-OOB"] = in_book
        runner._retire_out_of_band(s, ex, NOW + timedelta(minutes=30))
        client.books["KXTEST-OOB"] = out_book
        for minute in range(40, 100, 5):
            assert runner._retire_out_of_band(s, ex, NOW + timedelta(minutes=minute)) == 0
        assert runner._retire_out_of_band(s, ex, NOW + timedelta(minutes=101)) == 1


def test_an_unreadable_book_neither_runs_nor_resets_the_clock(live_db, settings):
    out_book = _book([(23, 500)], [(30, 100), (3, 300)])
    client, runner = _oob_runner(settings, out_book)
    with db.session_scope() as s:
        _program(s, "KXTEST-OOB")
        _resting(s, "KXTEST-OOB")
        ex = _exec(settings, client)
        for minute in range(0, 31, 5):
            runner._retire_out_of_band(s, ex, NOW + timedelta(minutes=minute))
        client.books["KXTEST-OOB"] = RuntimeError("down")
        assert runner._retire_out_of_band(s, ex, NOW + timedelta(minutes=35)) == 0
        client.books["KXTEST-OOB"] = out_book
        for minute in range(38, 60, 5):
            assert runner._retire_out_of_band(s, ex, NOW + timedelta(minutes=minute)) == 0
        assert runner._retire_out_of_band(s, ex, NOW + timedelta(minutes=61)) == 1


def test_a_long_gap_without_evidence_restarts_the_clock(live_db, settings):
    out_book = _book([(23, 500)], [(30, 100), (3, 300)])
    client, runner = _oob_runner(settings, out_book)
    with db.session_scope() as s:
        _program(s, "KXTEST-OOB")
        _resting(s, "KXTEST-OOB")
        ex = _exec(settings, client)
        runner._retire_out_of_band(s, ex, NOW)
        # 70 minutes with no read at all: the hour was never observed.
        for minute in range(70, 130, 5):        # clock restarted at 70
            assert runner._retire_out_of_band(s, ex, NOW + timedelta(minutes=minute)) == 0
        assert runner._retire_out_of_band(s, ex, NOW + timedelta(minutes=131)) == 1


def test_a_restart_resumes_the_clock(live_db, settings):
    out_book = _book([(23, 500)], [(30, 100), (3, 300)])
    client, first = _oob_runner(settings, out_book)
    with db.session_scope() as s:
        _program(s, "KXTEST-OOB")
        _resting(s, "KXTEST-OOB")
        ex = _exec(settings, client)
        for minute in range(0, 51, 5):
            first._retire_out_of_band(s, ex, NOW + timedelta(minutes=minute))
    # A redeploy: a new process, same database, back 6 minutes after the last read.
    second = run.IncentiveLiveRunner(client, settings)
    with db.session_scope() as s:
        ex = _exec(settings, client)
        assert second._retire_out_of_band(s, ex, NOW + timedelta(minutes=56)) == 0
        assert second._retire_out_of_band(s, ex, NOW + timedelta(minutes=61)) == 1
    assert client.canceled == ["K-KXTEST-OOB"]


def test_a_restart_after_a_long_outage_restarts_the_clock(live_db, settings):
    out_book = _book([(23, 500)], [(30, 100), (3, 300)])
    client, first = _oob_runner(settings, out_book)
    with db.session_scope() as s:
        _program(s, "KXTEST-OOB")
        _resting(s, "KXTEST-OOB")
        ex = _exec(settings, client)
        for minute in range(0, 31, 5):
            first._retire_out_of_band(s, ex, NOW + timedelta(minutes=minute))
    second = run.IncentiveLiveRunner(client, settings)
    with db.session_scope() as s:
        ex = _exec(settings, client)
        # Down for 30 minutes: what the book did then is unknown, so the hour starts at 60.
        assert second._retire_out_of_band(s, ex, NOW + timedelta(minutes=60)) == 0
        assert second._retire_out_of_band(s, ex, NOW + timedelta(minutes=65)) == 0
    assert client.canceled == []


def test_a_bid_inside_the_band_is_left_alone(live_db, settings):
    client, runner = _oob_runner(settings, _book([(23, 500)], [(4, 100), (3, 300)]))
    with db.session_scope() as s:
        _program(s, "KXTEST-OOB")
        _resting(s, "KXTEST-OOB")
        ex = _exec(settings, client)
        runner._retire_out_of_band(s, ex, NOW)
        assert runner._retire_out_of_band(s, ex, NOW + timedelta(hours=5)) == 0
    assert client.canceled == []


# --- §9.59: capped events do not spend the book-fetch budget ----------------------------------

def test_markets_of_a_capped_event_are_not_fetched(live_db, settings, monkeypatch):
    monkeypatch.setattr(limm, "MAX_OPEN_ORDERS", 10)
    monkeypatch.setattr(limm, "MAX_STRATEGY_EXPOSURE_USD", 100.0)
    monkeypatch.setattr(limm, "MAX_MARKETS_PER_EVENT", 1)
    settings.liquidity_incentive_live_max_book_fetches = 2
    book = _book([(23, 100), (20, 400)], [(4, 30), (3, 300), (2, 300)])
    client = FakeClient({"KXTEST-FULL-0": book, "KXTEST-FULL-1": book, "KXTEST-FULL-2": book,
                         "KXTEST-OTHER": book})
    with db.session_scope() as s:
        # The held event is newest, so without the filter its words would take both fetches.
        _program(s, "KXTEST-FULL-0", event="KXTEST-FULL", age_hours=30.0)
        _program(s, "KXTEST-FULL-1", event="KXTEST-FULL", age_hours=1.0)
        _program(s, "KXTEST-FULL-2", event="KXTEST-FULL", age_hours=1.0)
        _program(s, "KXTEST-OTHER", event="KXTEST-OTHER", age_hours=20.0)
        s.add(m.LiveOrder(market_ticker="KXTEST-FULL-0", event_ticker="KXTEST-FULL",
                          strategy=limm.LIVE_TAG, side="no", action="buy", limit_price=3,
                          quantity=300, status="resting", kalshi_order_id="K-held",
                          client_order_id="K-held", created_at=NOW - timedelta(hours=2)))
        s.flush()
        out = _cycle(client, settings, s)
    assert "KXTEST-FULL-1" not in client.asked and "KXTEST-FULL-2" not in client.asked
    assert out["placed"] == 1 and client.placed[0]["ticker"] == "KXTEST-OTHER"


# --- §9.60: a held leg rests a sell at the current ask; sweeps are logged, never acted on -----

MENTION = "KXTRUMPMENTION-26SEP20-PALE"


def _held_mention(s, *, entry=10, qty=100, sell=None, sell_qty=None, sell_age_min=10,
                  hours=48.0, snap_at=NOW, sell_role="a", sells=()):
    _mention(s, MENTION, event="KXTRUMPMENTION-26SEP20", hours=hours)
    s.add(m.LiveOrder(market_ticker=MENTION, event_ticker="KXTRUMPMENTION-26SEP20",
                      strategy=limm.LIVE_TAG, side="yes", action="buy", limit_price=entry,
                      quantity=qty, status="filled", kalshi_order_id="K-PALE",
                      client_order_id="pale", created_at=NOW - timedelta(hours=5)))
    s.add(m.Fill(kalshi_fill_id="F-PALE", kalshi_order_id="K-PALE", market_ticker=MENTION,
                 side="yes", action="buy", price=entry, quantity=qty))
    if sell is not None:
        sells = ((sell_role, sell, sell_qty or qty),) + tuple(sells)
    for i, (role, price, n) in enumerate(sells):
        coid = f"{limm.RESTING_SELL_PREFIX}{role + ':' if role else ''}x{i}"
        s.add(m.LiveOrder(market_ticker=MENTION, event_ticker=None, strategy=limm.LIVE_TAG,
                          side="yes", action="sell", limit_price=price, quantity=n,
                          status="resting", kalshi_order_id="K-SELL" if i == 0 else f"K-SELL{i}",
                          client_order_id=coid,
                          created_at=NOW - timedelta(minutes=sell_age_min)))
    _snap(s, qty, snap_at)


def _snap(s, qty, at):
    s.add(m.Position(market_ticker=MENTION, captured_at=at, side="yes", quantity=qty,
                     quantity_fp=qty, avg_price=10.0))
    s.flush()


def test_a_held_mention_leg_rests_a_sell_at_the_best_ask(live_db, settings):
    # YES bid 8 (above the 5c stop, under the 15c take-profit); NO bid 88 == YES ask 12.
    client = FakeClient({MENTION: _book([(8, 3), (7, 10)], [(88, 100), (85, 400)])})
    with db.session_scope() as s:
        _held_mention(s, entry=14)
        out = _cycle(client, settings, s)
        rows = s.scalars(sa_select(m.LiveOrder).where(m.LiveOrder.action == "sell")
                         .order_by(m.LiveOrder.id)).all()
    assert _managed(out) == {"resting_sell:placed": 1}
    # §9.61: half at the 12c ask, half at the 14c entry (break-even).
    assert [(o["side"], o["price"], o["count"], o["post_only"], o["time_in_force"])
            for o in client.placed] == [
        ("ask", "0.1200", "50.00", True, "good_till_canceled"),
        ("ask", "0.1400", "50.00", True, "good_till_canceled")]
    assert [(r.side, r.limit_price, r.quantity, r.status) for r in rows] == [
        ("yes", 12, 50, "resting"), ("yes", 14, 50, "resting")]
    assert [r.client_order_id[:11] for r in rows] == ["limmsell:a:", "limmsell:b:"]


def test_the_resting_sell_never_goes_at_or_below_the_stop():
    # A 10c entry stops at a 5c bid, so the sell floor is 6c even when another seller asks 4c.
    assert limm.resting_sell_price(entry_cents=10, held_bid_cents=3, best_other_ask_cents=4) == 6
    # Never at or below the held bid (post-only would cross).
    assert limm.resting_sell_price(entry_cents=10, held_bid_cents=9, best_other_ask_cents=9) == 10
    # No other seller: ask entry + take-profit.
    assert limm.resting_sell_price(entry_cents=10, held_bid_cents=8,
                                   best_other_ask_cents=None) == 15
    assert limm.resting_sell_price(entry_cents=60, held_bid_cents=99,
                                   best_other_ask_cents=None) is None


def test_a_resting_sell_undercut_by_another_seller_is_moved_to_the_new_ask(live_db, settings):
    # Ours: YES ask 12 (== NO bid 88 x100). Someone else now asks 10 (NO bid 90).
    client = FakeClient({MENTION: _book([(8, 3)], [(90, 50), (88, 100)])})
    with db.session_scope() as s:
        _held_mention(s, sell=12)
        out = _cycle(client, settings, s)
    assert _managed(out) == {"resting_sell:repricing": 1}
    assert client.canceled == ["K-SELL"] and client.placed == []


def test_a_resting_sell_at_the_ask_is_kept(live_db, settings):
    # Our own 100 at NO 88 is taken out of the book: the other sellers' best ask is still 12.
    client = FakeClient({MENTION: _book([(8, 3)], [(88, 140), (85, 400)])})
    with db.session_scope() as s:
        _held_mention(s, sell=12)
        out = _cycle(client, settings, s)
    assert _managed(out) == {"resting_sell:resting": 1}
    assert client.canceled == [] and client.placed == []


def test_a_young_resting_sell_is_not_churned(live_db, settings):
    client = FakeClient({MENTION: _book([(8, 3)], [(90, 50), (88, 100)])})
    with db.session_scope() as s:
        _held_mention(s, sell=12, sell_age_min=2)
        out = _cycle(client, settings, s)
    assert _managed(out) == {"resting_sell:resting": 1} and client.canceled == []


def test_a_pre_split_single_sell_is_replaced_by_the_split(live_db, settings):
    client = FakeClient({MENTION: _book([(8, 3)], [(88, 100)])})
    with db.session_scope() as s:
        _held_mention(s, sell=12, sell_role=None)
        out = _cycle(client, settings, s)
    assert _managed(out) == {"resting_sell:resplit": 1} and client.canceled == ["K-SELL"]


def test_the_split_sells_step_down_in_the_last_hours_before_the_cutoff():
    # 14c entry, 8c held bid, 12c ask: floor 9c (one above the bid; the stop is at 7c).
    far = limm.resting_sell_prices(entry_cents=14, held_bid_cents=8, best_other_ask_cents=12,
                                   hours_to_cutoff=10.0)
    half = limm.resting_sell_prices(entry_cents=14, held_bid_cents=8, best_other_ask_cents=12,
                                    hours_to_cutoff=3.0)
    end = limm.resting_sell_prices(entry_cents=14, held_bid_cents=8, best_other_ask_cents=12,
                                   hours_to_cutoff=0.1)
    assert far == (12, 14)
    assert half == (10, 12)      # halfway: a 12 -> 10 (1.5 rounds up), b 14 -> 12 (toward a)
    assert end[0] == 9 and end[1] <= 10
    # Never under the stop floor: 10c entry stops at 5c, so 6c minimum.
    assert limm.resting_sell_prices(entry_cents=10, held_bid_cents=2, best_other_ask_cents=7,
                                    hours_to_cutoff=0.0) == (6, 6)


def test_after_the_ask_sell_fills_the_break_even_sell_is_left_alone(live_db, settings):
    # 50 sold at 12 (role a gone); 50 held, covered by the 14c break-even sell: nothing to add.
    client = FakeClient({MENTION: _book([(8, 3)], [(88, 100), (86, 50)])})
    with db.session_scope() as s:
        _held_mention(s, entry=14, qty=50, sells=(("b", 14, 50),))
        out = _cycle(client, settings, s)
    assert _managed(out) == {"resting_sell:resting": 1}
    assert client.placed == [] and client.canceled == []


def test_a_missing_split_half_is_placed_from_the_uncovered_contracts(live_db, settings):
    client = FakeClient({MENTION: _book([(8, 3)], [(88, 100)])})
    with db.session_scope() as s:
        _held_mention(s, entry=14, sells=(("a", 12, 50),))
        out = _cycle(client, settings, s)
    assert _managed(out) == {"resting_sell:placed": 1}
    assert [(o["price"], o["count"]) for o in client.placed] == [("0.1400", "50.00")]


def test_split_sells_grow_with_the_position(live_db, settings):
    """§9.67: 5 contracts of sells under a 100-contract position are re-placed over all of it."""
    client = FakeClient({MENTION: _book([(8, 3)], [(88, 100)])})
    with db.session_scope() as s:
        _held_mention(s, entry=14, sells=(("a", 12, 3), ("b", 14, 2)))
        out = _cycle(client, settings, s)
    assert _managed(out) == {"resting_sell:grown": 1}
    assert sorted(client.canceled) == ["K-SELL", "K-SELL1"] and client.placed == []


def test_a_resting_sell_larger_than_the_position_is_cancelled_at_once(live_db, settings):
    client = FakeClient({MENTION: _book([(8, 3)], [(88, 100)])})
    with db.session_scope() as s:
        _held_mention(s, qty=60, sell=12, sell_qty=100, sell_age_min=1)
        out = _cycle(client, settings, s)
    assert _managed(out) == {"resting_sell:resized": 1} and client.canceled == ["K-SELL"]


def test_the_stop_waits_for_a_position_read_after_the_sell_is_cancelled(live_db, settings):
    """The sell may part-fill until it is cancelled: the marketable exit is sized only from a
    position read taken after the cancel, or it could sell more than is held."""
    client = FakeClient({MENTION: _book([(5, 30)], [(88, 100)])})      # bid 5: the stop
    ex = _exec(settings, client)
    runner = run.IncentiveLiveRunner(client, settings)
    with db.session_scope() as s:
        _held_mention(s, sell=12)
        out = runner.cycle(s, ex, {"cash_balance": 500.0}, now=NOW)
        assert _managed(out) == {"stop_loss:resting_sell_cancelled": 1}
        assert client.canceled == ["K-SELL"] and client.placed == []
        out = runner.cycle(s, ex, {"cash_balance": 500.0}, now=NOW + timedelta(minutes=2))
        assert _managed(out) == {"stop_loss:await_position": 1} and client.placed == []
        _snap(s, 100, NOW + timedelta(minutes=3))
        out = runner.cycle(s, ex, {"cash_balance": 500.0}, now=NOW + timedelta(minutes=4))
    assert _managed(out) == {"stop_loss:placed": 1}
    assert client.placed[0]["time_in_force"] == "immediate_or_cancel"


def test_the_resting_sell_is_pulled_at_the_pre_event_cutoff(live_db, settings):
    # The programme ends (== the event starts) in 10 minutes: past the 15-minute cutoff.
    client = FakeClient({MENTION: _book([(8, 3)], [(88, 100)])})
    with db.session_scope() as s:
        _held_mention(s, sell=12, hours=10 / 60)
        out = _cycle(client, settings, s)
    assert _managed(out) == {"resting_sell:pulled": 1} and client.canceled == ["K-SELL"]


def test_no_resting_sell_is_placed_after_the_cutoff(live_db, settings):
    # Falls back to the pre-§9.60 rules: the opposite-side exit leg here costs 88c x100, over cap.
    client = FakeClient({MENTION: _book([(8, 3)], [(88, 100)])})
    with db.session_scope() as s:
        _held_mention(s, hours=10 / 60)
        out = _cycle(client, settings, s)
    assert _managed(out) == {"holding:exit_leg_over_cap": 1} and client.placed == []


class CashRefusingClient(FakeClient):
    def create_events_order(self, order):
        from kalshi_bot.kalshi.client import KalshiAPIError
        raise KalshiAPIError(400, '{"error":{"code":"insufficient_balance"}}')


def test_a_cash_refusal_switches_resting_sells_off_and_it_stays_off(live_db, settings):
    client = CashRefusingClient({MENTION: _book([(8, 3)], [(88, 100)])})
    with db.session_scope() as s:
        _held_mention(s)
        out = _cycle(client, settings, s)
        assert _managed(out) == {"resting_sell:rejected:insufficient_balance": 1}
    with db.session_scope() as s:
        fresh = run.IncentiveLiveRunner(FakeClient({}), settings)
        fresh._load_resting_sell_off(s)
    assert fresh._resting_sell_off


def test_a_sweep_is_recorded_with_follow_ups_and_changes_nothing(live_db, settings):
    client = FakeClient({"KXTEST-SW": _book([(11, 200), (10, 300), (8, 100)], [(85, 100)])})
    runner = run.IncentiveLiveRunner(client, settings)
    with db.session_scope() as s:
        _program(s, "KXTEST-SW")
        s.add(m.LiveOrder(market_ticker="KXTEST-SW", event_ticker="KXTEST-SW",
                          strategy=limm.LIVE_TAG, side="yes", action="buy", limit_price=10,
                          quantity=100, status="resting", kalshi_order_id="K-SW",
                          client_order_id="K-SW", created_at=NOW - timedelta(hours=2)))
        s.flush()
        assert runner._record_sweeps(s, NOW) == 0
        client.books["KXTEST-SW"] = _book([(8, 50), (7, 10)], [(85, 100)])
        runner._data_books = {}
        assert runner._record_sweeps(s, NOW + timedelta(minutes=2)) == 1
        for mins in (12, 32, 62):
            runner._data_books = {}
            runner._record_sweeps(s, NOW + timedelta(minutes=mins))
        rows = s.scalars(sa_select(m.IncentiveCollectorEvent)
                         .order_by(m.IncentiveCollectorEvent.id)).all()
    sweep = [r for r in rows if r.kind == run.EV_BOOK_SWEEP]
    follow = [r for r in rows if r.kind == run.EV_BOOK_SWEEP_FOLLOWUP]
    assert len(sweep) == 1
    d = sweep[0].detail_json
    assert (d["side"], d["best_bid_before"], d["best_bid_after"], d["contracts_removed"],
            d["own_contracts_in_range"]) == ("yes", 11, 8, 550.0, 100.0)
    assert [f.detail_json["minutes"] for f in follow] == [10, 30, 60]
    assert all(f.detail_json["recovered"] is False for f in follow)
    assert client.placed == [] and client.canceled == []


def test_a_re_price_keeps_each_halfs_size(live_db, settings):
    """§9.62: the ask half already filled; the 50 break-even contracts left are re-priced as 50
    break-even contracts, not re-split 25/25 (which would move half of them to the ask)."""
    client = FakeClient({MENTION: _book([(8, 3)], [(88, 100)])})
    ex = _exec(settings, client)
    runner = run.IncentiveLiveRunner(client, settings)
    with db.session_scope() as s:
        # 50 held at 14c, one break-even sell at 15c that is now stale (target 14c).
        _held_mention(s, entry=14, qty=50, sells=(("b", 15, 50),))
        out = runner.cycle(s, ex, {"cash_balance": 500.0}, now=NOW)
        assert _managed(out) == {"resting_sell:repricing": 1}
        _snap(s, 50, NOW + timedelta(minutes=1))
        out = runner.cycle(s, ex, {"cash_balance": 500.0}, now=NOW + timedelta(minutes=2))
    assert _managed(out) == {"resting_sell:placed": 1}
    assert [(o["price"], o["count"], o["client_order_id"][:11]) for o in client.placed] == [
        ("0.1400", "50.00", "limmsell:b:")]


# --- §9.63: bids step aside 48h before the event; a fill pulls its event and word -------------

def test_a_bid_steps_aside_48h_before_its_event(live_db, settings, monkeypatch):
    monkeypatch.setattr(limm, "EVENT_MENTION_ONLY", True)
    monkeypatch.setattr(limm, "ENTRY_STEP_ASIDE_HOURS", 48.0)
    client = FakeClient({})
    with db.session_scope() as s:
        # Programme ends (== event) in 40h: inside 48h, so the resting bid goes.
        _mention(s, "KXTRUMPMENTION-26SEP20-SOON", event="KXTRUMPMENTION-26SEP20", hours=40.0)
        _resting(s, "KXTRUMPMENTION-26SEP20-SOON")
        # Event in 100h: outside the window, kept.
        _mention(s, "KXTRUMPMENTION-26SEP22-LATE", event="KXTRUMPMENTION-26SEP22", hours=100.0)
        _resting(s, "KXTRUMPMENTION-26SEP22-LATE")
        runner = run.IncentiveLiveRunner(client, settings)
        out = runner._retire_before_event(s, _exec(settings, client), NOW)
    assert out == {"pre_event": 1}
    assert client.canceled == ["K-KXTRUMPMENTION-26SEP20-SOON"]


def test_no_entry_inside_the_48h_window(live_db, settings, monkeypatch):
    monkeypatch.setattr(limm, "EVENT_MENTION_ONLY", True)
    monkeypatch.setattr(limm, "ENTRY_STEP_ASIDE_HOURS", 48.0)
    book = _book([(23, 100), (20, 400)], [(4, 30), (3, 300), (2, 300)])
    client = FakeClient({"KXTRUMPMENTION-26SEP20-NEAR": book, "KXTRUMPMENTION-26SEP25-FAR": book})
    with db.session_scope() as s:
        _mention(s, "KXTRUMPMENTION-26SEP20-NEAR", event="KXTRUMPMENTION-26SEP20", hours=50.0)
        _mention(s, "KXTRUMPMENTION-26SEP25-FAR", event="KXTRUMPMENTION-26SEP25", hours=120.0)
        out = _cycle(client, settings, s)
    # NEAR's entry cutoff is 2h out (< MIN_HOURS_BEFORE_EVENT): never fetched.
    assert client.asked == ["KXTRUMPMENTION-26SEP25-FAR"]
    assert out["placed"] == 1


def test_a_new_bid_gets_a_day_before_it_steps_aside(live_db, settings, monkeypatch):
    """§9.64: an event 60h out leaves 12h before the 48h step-aside — too short to enter."""
    monkeypatch.setattr(limm, "EVENT_MENTION_ONLY", True)
    monkeypatch.setattr(limm, "ENTRY_STEP_ASIDE_HOURS", 48.0)
    assert limm.MIN_HOURS_BEFORE_EVENT == 24.0
    book = _book([(23, 100), (20, 400)], [(4, 30), (3, 300), (2, 300)])
    client = FakeClient({"KXTRUMPMENTION-26SEP21-SHORT": book,
                         "KXTRUMPMENTION-26SEP23-DAY": book})
    with db.session_scope() as s:
        _mention(s, "KXTRUMPMENTION-26SEP21-SHORT", event="KXTRUMPMENTION-26SEP21", hours=60.0)
        _mention(s, "KXTRUMPMENTION-26SEP23-DAY", event="KXTRUMPMENTION-26SEP23", hours=80.0)
        out = _cycle(client, settings, s)
    assert client.asked == ["KXTRUMPMENTION-26SEP23-DAY"]
    assert out["placed"] == 1


def _filled_bid(s, ticker, event, *, minutes_ago=30):
    s.add(m.LiveOrder(market_ticker=ticker, event_ticker=event, strategy=limm.LIVE_TAG,
                      side="yes", action="buy", limit_price=5, quantity=200, status="filled",
                      kalshi_order_id=f"K-F-{ticker}", client_order_id=f"F-{ticker}",
                      created_at=NOW - timedelta(hours=2)))
    s.add(m.Fill(kalshi_fill_id=f"FF-{ticker}", kalshi_order_id=f"K-F-{ticker}",
                 market_ticker=ticker, side="yes", action="buy", price=5, quantity=200,
                 filled_at=NOW - timedelta(minutes=minutes_ago)))
    s.flush()


def _resting_in(s, ticker, event):
    s.add(m.LiveOrder(market_ticker=ticker, event_ticker=event, strategy=limm.LIVE_TAG,
                      side="yes", action="buy", limit_price=3, quantity=300, status="resting",
                      kalshi_order_id=f"K-{ticker}", client_order_id=f"K-{ticker}",
                      created_at=NOW - timedelta(hours=2)))
    s.flush()


def test_a_fill_pulls_bids_in_its_event_and_on_its_word(live_db, settings):
    client = FakeClient({})
    with db.session_scope() as s:
        _filled_bid(s, "KXTRUMPMENTION-26OCT19-NQE", "KXTRUMPMENTION-26OCT19")
        _resting_in(s, "KXTRUMPMENTION-26OCT19-FENT", "KXTRUMPMENTION-26OCT19")   # same event
        _resting_in(s, "KXTRUMPMENTION-26OCT25-NQE", "KXTRUMPMENTION-26OCT25")    # same word
        _resting_in(s, "KXTRUMPMENTION-26OCT25-PEAC", "KXTRUMPMENTION-26OCT25")   # neither
        runner = run.IncentiveLiveRunner(client, settings)
        runner._load_contagion(s, NOW)
        n = runner._retire_contagion(s, _exec(settings, client), NOW)
    assert n == 2
    assert sorted(client.canceled) == ["K-KXTRUMPMENTION-26OCT19-FENT",
                                       "K-KXTRUMPMENTION-26OCT25-NQE"]


def test_an_old_fill_blocks_nothing(live_db, settings):
    client = FakeClient({})
    with db.session_scope() as s:
        _filled_bid(s, "KXTRUMPMENTION-26OCT19-NQE", "KXTRUMPMENTION-26OCT19",
                    minutes_ago=int(limm.FILL_CONTAGION_HOURS * 60) + 5)
        _resting_in(s, "KXTRUMPMENTION-26OCT19-FENT", "KXTRUMPMENTION-26OCT19")
        runner = run.IncentiveLiveRunner(client, settings)
        runner._load_contagion(s, NOW)
        assert runner._retire_contagion(s, _exec(settings, client), NOW) == 0
    assert client.canceled == []


def test_no_new_bid_in_a_contagion_blocked_event_or_word(live_db, settings, monkeypatch):
    monkeypatch.setattr(limm, "MAX_OPEN_ORDERS", 10)
    monkeypatch.setattr(limm, "MAX_STRATEGY_EXPOSURE_USD", 100.0)
    book = _book([(23, 100), (20, 400)], [(4, 30), (3, 300), (2, 300)])
    client = FakeClient({"KXTRUMPMENTION-26OCT25-NQE": book, "KXTRUMPMENTION-26OCT25-PEAC": book})
    with db.session_scope() as s:
        _filled_bid(s, "KXTRUMPMENTION-26OCT19-NQE", "KXTRUMPMENTION-26OCT19")
        _program(s, "KXTRUMPMENTION-26OCT25-NQE", event="KXTRUMPMENTION-26OCT25")
        _program(s, "KXTRUMPMENTION-26OCT25-PEAC", event="KXTRUMPMENTION-26OCT25B")
        out = _cycle(client, settings, s)
    assert "KXTRUMPMENTION-26OCT25-NQE" not in client.asked
    assert out["placed"] == 1 and client.placed[0]["ticker"] == "KXTRUMPMENTION-26OCT25-PEAC"


def test_market_word():
    assert limm.market_word("KXTRUMPMENTION-26OCT19-CEAS", "KXTRUMPMENTION-26OCT19") == "CEAS"
    assert limm.market_word("KXTEST-A", "KXTEST-A") is None
    assert limm.market_word("KXTEST-A", None) is None


# --- §9.65: a position-blocked event is dropped before the fetches; dust does not block ------

def _held_in(s, ticker, event, qty):
    s.add(m.LiveOrder(market_ticker=ticker, event_ticker=event, strategy=limm.LIVE_TAG,
                      side="yes", action="buy", limit_price=2, quantity=10, status="filled",
                      kalshi_order_id=f"K-H-{ticker}", client_order_id=f"H-{ticker}",
                      created_at=NOW - timedelta(hours=30)))
    s.add(m.Position(market_ticker=ticker, captured_at=NOW, side="yes", quantity=round(qty),
                     quantity_fp=qty))
    s.flush()


def test_position_blocked_event_does_not_eat_the_fetch_budget(live_db, settings, monkeypatch):
    monkeypatch.setattr(limm, "MAX_MARKETS_PER_EVENT", 3)
    settings.liquidity_incentive_live_max_book_fetches = 2
    book = _book([(3, 500)], [(4, 500)])
    blk = [f"KXTEST-BLK-{i}" for i in range(3)]
    client = FakeClient({**{t: book for t in blk}, "KXTEST-OTH-A": book,
                         "KXTEST-BLK-HELD": book})
    with db.session_scope() as s:
        for t in blk:                                   # newest programmes: ranked first
            _program(s, t, event="KXTEST-BLK", age_hours=1.0)
        _program(s, "KXTEST-OTH-A", event="KXTEST-OTH", age_hours=30.0)
        _held_in(s, "KXTEST-BLK-HELD", "KXTEST-BLK", 9.0)
        out = _cycle(client, settings, s)
    # The held market's own book is read for its exit; no BLK candidate is fetched.
    assert [t for t in client.asked if t != "KXTEST-BLK-HELD"] == ["KXTEST-OTH-A"]
    assert out["placed"] == 1


def test_fractional_dust_does_not_block_its_event(live_db, settings, monkeypatch):
    monkeypatch.setattr(limm, "MAX_MARKETS_PER_EVENT", 3)
    settings.liquidity_incentive_live_max_book_fetches = 2
    book = _book([(3, 500)], [(4, 500)])
    client = FakeClient({"KXTEST-BLK-0": book, "KXTEST-OTH-A": book, "KXTEST-BLK-HELD": book})
    with db.session_scope() as s:
        _program(s, "KXTEST-BLK-0", event="KXTEST-BLK", age_hours=1.0)
        _program(s, "KXTEST-OTH-A", event="KXTEST-OTH", age_hours=30.0)
        _held_in(s, "KXTEST-BLK-HELD", "KXTEST-BLK", 0.9)   # whole-contract exits left 0.9
        _cycle(client, settings, s)
    assert "KXTEST-BLK-0" in {o["ticker"] for o in client.placed}
