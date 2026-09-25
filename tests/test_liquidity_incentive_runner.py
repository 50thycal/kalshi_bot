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


def _book(yes, no):
    return {"orderbook": {"yes": [list(x) for x in yes], "no": [list(x) for x in no]}}


def _program(session, ticker, *, target=200.0, hours=24.0, close_hours=24.0, series="KXTEST",
             event=None):
    # `target` 200 against the 500-a-side fixture books is 2.5x — inside the universe rule.
    # One event per market by default (§9.15). The close is a day out, inside the window.
    row = m.IncentiveProgram(
        program_id=f"p-{ticker}", market_ticker=ticker, event_ticker=event or ticker,
        series_ticker=series, incentive_type="liquidity",
        start_date=NOW - timedelta(days=1), end_date=NOW + timedelta(hours=hours),
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
    client = FakeClient({"KXTEST-A": _book([(20, 500)], [(70, 500)])})
    with db.session_scope() as s:
        _program(s, "KXTEST-A")
        out = _cycle(client, settings, s)
    assert out == {"armed": False, "considered": 0, "fetched": 0, "placed": 0,
                   "twin_opened": 0, "outcomes": {}, "managed": {}}
    assert client.asked == []


# ------------------------------------------------------------------ candidate construction


def test_candidate_carries_native_prices_references_and_the_close(live_db, settings):
    with db.session_scope() as s:
        program = _program(s, "KXTEST-A", target=100.0, close_hours=30.0)
        c = run.candidate_from_book(
            program, _book([(20, 60), (19, 200)], [(70, 300), (69, 50)]), now=NOW)
    assert c["best_yes_bid"] == 20 and c["best_no_bid"] == 70
    assert c["yes_resting_total"] == 260.0 and c["no_resting_total"] == 350.0
    assert c["reference_price_by_side"] == {limm.SIDE_YES: 20, limm.SIDE_NO: 70}
    assert c["program_hours_remaining"] == pytest.approx(24.0)
    assert c["hours_to_close"] == pytest.approx(30.0)


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
    client = FakeClient({"KXTEST-A": _book([(20, 500)], [(70, 500)])})
    with db.session_scope() as s:
        _program(s, "KXTEST-A", close_hours=close_hours)
        out = _cycle(client, settings, s)
    assert out["considered"] == 0 and client.asked == [] and client.placed == []


# ------------------------------------------------------------------ pair entries + caps


def test_places_a_pair_on_one_market(live_db, settings):
    client = FakeClient({"KXTEST-A": _book([(20, 500)], [(70, 500)])})
    with db.session_scope() as s:
        _program(s, "KXTEST-A")
        out = _cycle(client, settings, s)
    assert out["placed"] == 1
    assert [(o["ticker"], o["side"]) for o in client.placed] == [
        ("KXTEST-A", "bid"), ("KXTEST-A", "ask")]
    assert client.placed[0]["count"] == client.placed[1]["count"]


def test_soonest_closing_first_and_stops_at_the_market_cap(live_db, settings):
    books = {t: _book([(20, 500)], [(70, 500)]) for t in ("KXTEST-A", "KXTEST-B", "KXTEST-C")}
    client = FakeClient(books)
    with db.session_scope() as s:
        _program(s, "KXTEST-A", close_hours=40.0)
        _program(s, "KXTEST-B", close_hours=6.0)
        _program(s, "KXTEST-C", close_hours=20.0)
        out = _cycle(client, settings, s)
    assert out["placed"] == limm.MAX_OPEN_ORDERS
    placed_markets = list(dict.fromkeys(o["ticker"] for o in client.placed))
    assert placed_markets == ["KXTEST-B", "KXTEST-C"]


def test_the_book_budget_bounds_the_cycle_even_with_slots_free(live_db, settings):
    client = FakeClient({"KXTEST-A": _book([(20, 500)], [(75, 500)])})
    with db.session_scope() as s:
        _program(s, "KXTEST-A")
        # $40 committed elsewhere; a 20/75 pair commits ~$12 more, past the $50 ceiling.
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
    client = FakeClient({"KXTEST-A": _book([(10, 500)], [(85, 500)])})
    with db.session_scope() as s:
        _program(s, "KXTEST-A")
        out = _cycle(client, settings, s)
    assert out["placed"] == 0
    assert out["outcomes"].get(limm.REFUSE_EXCLUDED_SERIES) == 1


def test_a_program_ending_too_soon_is_never_selected(live_db, settings):
    client = FakeClient({"KXTEST-A": _book([(10, 500)], [(85, 500)])})
    with db.session_scope() as s:
        _program(s, "KXTEST-A", hours=limm.MIN_PROGRAM_HOURS_REMAINING - 0.5)
        out = _cycle(client, settings, s)
    assert out["considered"] == 0 and out["fetched"] == 0
    assert client.asked == []


def test_book_fetches_are_bounded_per_cycle(live_db, settings):
    settings.liquidity_incentive_live_max_book_fetches = 2
    books = {f"KXTEST-{i}": _book([(10, 500)], [(85, 500)]) for i in range(5)}
    client = FakeClient(books)
    with db.session_scope() as s:
        for i, t in enumerate(books):
            _program(s, t, close_hours=10.0 + i)
        out = _cycle(client, settings, s)
    assert out["considered"] == 5 and out["fetched"] == 2
    assert client.asked == ["KXTEST-0", "KXTEST-1"]


# ------------------------------------------------------------------ the twin


def test_both_legs_are_mirrored_to_the_twin(live_db, settings):
    from kalshi_bot.twin.harness import TwinHarness

    settings.live_paper_twin_enabled = True
    settings.live_paper_twins = f"{limm.LIVE_TAG}:{limm.TWIN_TAG}"
    client = FakeClient({"KXTEST-A": _book([(20, 500)], [(75, 500)])})
    with db.session_scope() as s:
        _program(s, "KXTEST-A")
        out = _cycle(client, settings, s, twin_harness=TwinHarness(settings))
    assert out["placed"] == 1 and out["twin_opened"] == 2
    qty = limm.pair_quantity(20, 75)
    with db.session_scope() as s:
        trades = s.query(m.PaperTrade).filter(m.PaperTrade.strategy == limm.TWIN_TAG).all()
        assert sorted((t.side, int(t.assumed_price), int(t.quantity)) for t in trades) == [
            ("no", 75, qty), ("yes", 20, qty)]


# ------------------------------------------------------- event cap (thesis §9.15)


def test_two_markets_of_one_event_are_one_commitment(live_db, settings):
    books = {
        "KXRT-RES-93": _book([(3, 500)], [(90, 500)]),
        "KXRT-RES-94": _book([(10, 500)], [(80, 500)]),
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
    client = FakeClient({"KXRT-RES-93": _book([(3, 500)], [(90, 500)])})
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
    client = FakeClient({"KXOTHER-1": _book([(4, 500)], [(90, 500)])})
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
    client = FakeClient({HELD: _book([(41, 500)], [(50, 500)])})
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
    client = LedgerClient({"KXTEST-A": _book([(20, 500)], [(70, 500)])}, fail=True)
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
    client = FakeClient({"KXTEST-A": _book([(20, 500)], [(70, 500)])})
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
