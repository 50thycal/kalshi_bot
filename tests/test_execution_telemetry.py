"""Execution telemetry collector (docs/MMSELL_QUEUE_FILL_TELEMETRY.md §8).

The state machine is driven directly: frames in, commands out, rows in SQLite. No socket.
Each test name maps to one of the handoff's required checks; the mapping is in the design
doc's test-plan table.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot import repository as repo
from kalshi_bot.execution import collector as c
from kalshi_bot.execution.book import LocalBook
from kalshi_bot.execution.readonly import ReadOnlyKalshi
from kalshi_bot.live.executor import LiveExecutor
from kalshi_bot.risk.manager import RiskManager

T0 = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- fakes


class _Clock:
    def __init__(self, start=T0):
        self.now = start

    def __call__(self):
        return self.now

    def tick(self, seconds):
        self.now = self.now + timedelta(seconds=seconds)
        return self.now


class _ReadClient:
    """Only the GET surface the collector is allowed."""

    def __init__(self, batch=None):
        self.batch = batch if batch is not None else {"queue_positions": []}
        self.calls: list[dict] = []
        self.ws_url = "wss://example.test/trade-api/ws/v2"

    def get_queue_positions(self, **kw):
        self.calls.append(kw)
        if isinstance(self.batch, Exception):
            raise self.batch
        if callable(self.batch):
            return self.batch()
        return self.batch

    def get_order_queue_position(self, order_id):
        raise AssertionError("the collector never uses the per-order endpoint")

    def ws_headers(self):
        return {}


def _db(settings):
    db.init_engine(settings.database_url)
    db.create_all()


def _order(session, *, koid="K-1", ticker="KXT-A", price=7, qty=1, strategy="Fmmsell10",
           status="resting", side="no", age_s=30):
    row = m.LiveOrder(
        market_ticker=ticker, event_ticker="KXT", strategy=strategy, side=side, action="buy",
        limit_price=price, quantity=qty, status=status, kalshi_order_id=koid,
        client_order_id=f"c-{koid}", created_at=T0 - timedelta(seconds=age_s),
    )
    session.add(row)
    session.flush()
    return row


def _state(settings, client=None, clock=None, **over):
    for k, v in over.items():
        setattr(settings, k, v)
    _db(settings)
    return c.CollectorState(client or _ReadClient(), settings, db.session_scope,
                            clock=clock or _Clock())


def _snapshot(ticker, sid=2, seq=1, yes=None, no=None):
    msg = {"market_ticker": ticker, "market_id": "u"}
    if yes is not None:
        msg["yes_dollars_fp"] = yes
    if no is not None:
        msg["no_dollars_fp"] = no
    return {"type": "orderbook_snapshot", "sid": sid, "seq": seq, "msg": msg}


def _delta(ticker, price, delta, side="no", sid=2, seq=2, ts_ms=1, coid=None):
    msg = {"market_ticker": ticker, "market_id": "u", "price_dollars": f"{price / 100:.4f}",
           "delta_fp": f"{delta:.2f}", "side": side, "ts_ms": ts_ms}
    if coid:
        msg["client_order_id"] = coid
    return {"type": "orderbook_delta", "sid": sid, "seq": seq, "msg": msg}


def _trade(ticker, yes_px, count, taker="yes", sid=3, seq=1, trade_id="t-1"):
    return {"type": "trade", "sid": sid, "seq": seq, "msg": {
        "trade_id": trade_id, "market_ticker": ticker, "yes_price_dollars": f"{yes_px / 100:.4f}",
        "no_price_dollars": f"{(100 - yes_px) / 100:.4f}", "count_fp": f"{count:.2f}",
        "taker_outcome_side": taker, "taker_book_side": "bid" if taker == "yes" else "ask",
        "is_block_trade": False, "ts_ms": 1758024000000}}


def _fill(koid, ticker, count, trade_id="f-1", yes_px=93):
    return {"type": "fill", "sid": 13, "msg": {
        "trade_id": trade_id, "order_id": koid, "client_order_id": f"c-{koid}",
        "market_ticker": ticker, "exchange_index": 2, "is_taker": False, "ts_ms": 1758024001234,
        "yes_price_dollars": f"{yes_px / 100:.4f}", "count_fp": f"{count:.2f}",
        "fee_cost": "0.010000", "outcome_side": "no", "book_side": "ask",
        "post_position_fp": f"{count:.2f}"}}


def _user_order(koid, ticker, status, remaining, filled):
    return {"type": "user_order", "sid": 14, "msg": {
        "order_id": koid, "user_id": "u", "ticker": ticker, "exchange_index": 2,
        "status": status, "outcome_side": "no", "book_side": "ask",
        "yes_price_dollars": "0.9300", "fill_count_fp": f"{filled:.2f}",
        "remaining_count_fp": f"{remaining:.2f}", "initial_count_fp": "1.00",
        "taker_fill_cost_dollars": "0", "maker_fill_cost_dollars": "0.07",
        "taker_fees_dollars": "0", "maker_fees_dollars": "0.01", "client_order_id": f"c-{koid}",
        "created_ts_ms": 1, "last_updated_ts_ms": 2}}


def _subscribed(cmds, channel, sid):
    cid = next(cmd["id"] for cmd in cmds if cmd["params"]["channels"] == [channel])
    return {"id": cid, "type": "subscribed", "msg": {"channel": channel, "sid": sid}}


def _connect_and_track(state, tickers_sids=None):
    """Bring the state to 'connected, subscribed, tracking' in one go."""
    cmds = state.on_connected()
    cmds += state.refresh_tracked()
    sids = {"orderbook_delta": 2, "trade": 3, "fill": 13, "user_orders": 14,
            "market_lifecycle_v2": 15}
    for channel, sid in sids.items():
        state.handle_message(_subscribed(cmds, channel, sid))
    return cmds


# ---------------------------------------------------------------- 16: no write path


def test_the_collector_client_has_no_write_methods():
    names = {n for n, _ in inspect.getmembers(ReadOnlyKalshi) if not n.startswith("_")}
    for forbidden in ("create", "place", "cancel", "amend", "decrease", "upgrade", "post",
                      "delete", "put", "batch", "transfer"):
        assert not any(forbidden in n.lower() for n in names), names
    assert names == {"get_queue_positions", "get_order_queue_position", "get_orderbook",
                     "get_market", "ws_url", "ws_headers"}


def test_collector_module_never_names_a_write_endpoint():
    src = inspect.getsource(c)
    for forbidden in ("create_events_order", "cancel_events_order", "place_order",
                      "cancel_order", "update_live_order_status", "create_live_order"):
        assert forbidden not in src


def test_collector_thread_never_touches_live_orders_status(settings):
    """Every frame kind, a failed poll, a terminal — and `live_orders` is byte-identical."""
    state = _state(settings)
    with db.session_scope() as s:
        _order(s, koid="K-1")
    _connect_and_track(state)
    state.handle_message(_snapshot("KXT-A", no=[["0.9300", "10.00"]]))
    state.handle_message(_delta("KXT-A", 93, -4))
    state.handle_message(_trade("KXT-A", 93, 4))
    state.handle_message(_fill("K-1", "KXT-A", 1))
    state.handle_message(_user_order("K-1", "KXT-A", "executed", 0, 1))
    state.client.batch = RuntimeError("boom")
    state.run_due_polls()
    with db.session_scope() as s:
        row = s.scalar(select(m.LiveOrder))
        assert row.status == "resting" and row.cancel_reason is None


# ---------------------------------------------------------------- 1, 2, 3, 18: queue samples


def test_interval_poll_writes_a_tick_per_tracked_order(settings):
    clock = _Clock()
    client = _ReadClient({"queue_positions": [
        {"order_id": "K-1", "market_ticker": "KXT-A", "queue_position_fp": "12.00"},
        {"order_id": "K-2", "market_ticker": "KXT-B", "queue_position_fp": "0.00"}]})
    state = _state(settings, client, clock)
    with db.session_scope() as s:
        _order(s, koid="K-1", ticker="KXT-A")
        _order(s, koid="K-2", ticker="KXT-B", price=8)
    _connect_and_track(state)
    assert state.run_due_polls() == c.TRIGGER_AT_REST
    assert state.run_due_polls() is None, "no second poll inside the interval"
    clock.tick(settings.execution_queue_poll_seconds + 1)
    assert state.run_due_polls() == c.TRIGGER_INTERVAL
    with db.session_scope() as s:
        ticks = s.scalars(select(m.LiveOrderQueueTick).order_by(m.LiveOrderQueueTick.id)).all()
    assert [t.trigger for t in ticks] == ["at_rest", "at_rest", "interval", "interval"]
    by = {(t.kalshi_order_id, t.trigger): t for t in ticks}
    assert by[("K-1", "interval")].contracts_ahead == 12
    assert by[("K-2", "interval")].contracts_ahead == 0      # front of queue survives
    assert by[("K-2", "interval")].queue_position == 0
    assert by[("K-1", "interval")].source == "rest_batch"
    assert by[("K-1", "interval")].limit_price == 7
    assert len(client.calls) == 2 and "KXT-A" in client.calls[0]["market_tickers"]


def test_fixed_point_fields_are_stored_verbatim(settings):
    client = _ReadClient({"queue_positions": [
        {"order_id": "K-1", "market_ticker": "KXT-A", "queue_position_fp": "2028.55"}]})
    state = _state(settings, client)
    with db.session_scope() as s:
        _order(s, koid="K-1")
    _connect_and_track(state)
    state.handle_message(_snapshot("KXT-A", no=[["0.9300", "10.50"]]))
    state.handle_message(_delta("KXT-A", 93, -0.25))
    state.run_due_polls()
    with db.session_scope() as s:
        tick = s.scalar(select(m.LiveOrderQueueTick))
        assert tick.raw_json["queue_position_fp"] == "2028.55"
        assert tick.contracts_ahead == 2028 or tick.contracts_ahead == 2029
        delta = s.scalar(select(m.ExecutionBookEvent).where(m.ExecutionBookEvent.kind == "delta"))
        assert float(delta.delta_fp) == -0.25 and float(delta.level_qty_after) == 10.25


def test_ticks_are_keyed_by_kalshi_order_id_not_by_position(settings):
    client = _ReadClient({"queue_positions": [
        {"order_id": "K-2", "market_ticker": "KXT-A", "queue_position_fp": "5.00"},
        {"order_id": "K-1", "market_ticker": "KXT-A", "queue_position_fp": "900.00"}]})
    state = _state(settings, client)
    with db.session_scope() as s:
        _order(s, koid="K-1", ticker="KXT-A")
        _order(s, koid="K-2", ticker="KXT-A", price=8)
    _connect_and_track(state)
    state.run_due_polls()
    with db.session_scope() as s:
        ticks = {t.kalshi_order_id: t.contracts_ahead
                 for t in s.scalars(select(m.LiveOrderQueueTick)).all()}
    assert ticks == {"K-1": 900, "K-2": 5}


def test_many_orders_on_many_markets_share_one_batch_poll(settings):
    rows = [{"order_id": f"K-{i}", "market_ticker": f"KXT-{i % 7}", "queue_position_fp": f"{i}.00"}
            for i in range(25)]
    client = _ReadClient({"queue_positions": rows})
    state = _state(settings, client)
    with db.session_scope() as s:
        for i in range(25):
            _order(s, koid=f"K-{i}", ticker=f"KXT-{i % 7}")
    cmds = _connect_and_track(state)
    subs = [cmd for cmd in cmds if cmd["cmd"] == "subscribe"
            and cmd["params"]["channels"] == ["orderbook_delta"]]
    assert len(subs) == 1 and len(subs[0]["params"]["market_tickers"]) == 7
    assert subs[0]["params"]["use_yes_price"] is True
    state.run_due_polls()
    assert len(client.calls) == 1
    with db.session_scope() as s:
        assert s.scalar(select(m.LiveOrderQueueTick.id).order_by(
            m.LiveOrderQueueTick.id.desc())) == 25


# ---------------------------------------------------------------- 13: missing is not zero


def test_a_failed_poll_writes_a_null_tick_and_a_collector_event(settings):
    client = _ReadClient(RuntimeError("transient status 502"))
    state = _state(settings, client)
    with db.session_scope() as s:
        _order(s, koid="K-1")
    _connect_and_track(state)
    state.run_due_polls()
    with db.session_scope() as s:
        tick = s.scalar(select(m.LiveOrderQueueTick))
        assert tick.queue_position is None and tick.contracts_ahead is None
        assert tick.raw_json == {"error": "RuntimeError: transient status 502"}
        kinds = [e.kind for e in s.scalars(select(m.ExecutionCollectorEvent)).all()]
    assert c.EV_POLL_FAILED in kinds


def test_an_order_absent_from_the_batch_is_reported_not_zeroed(settings):
    client = _ReadClient({"queue_positions": []})
    state = _state(settings, client)
    with db.session_scope() as s:
        _order(s, koid="K-1")
    _connect_and_track(state)
    state.run_due_polls()
    with db.session_scope() as s:
        tick = s.scalar(select(m.LiveOrderQueueTick))
        assert tick.queue_position is None
        ev = s.scalar(select(m.ExecutionCollectorEvent).where(
            m.ExecutionCollectorEvent.kind == c.EV_POLL_MISSING))
        assert ev is not None and ev.detail_json["order_ids"] == ["K-1"]


def test_a_429_backs_off_instead_of_retrying(settings):
    clock = _Clock()
    client = _ReadClient(RuntimeError("transient status 429"))
    state = _state(settings, client, clock)
    with db.session_scope() as s:
        _order(s, koid="K-1")
    _connect_and_track(state)
    state.run_due_polls()
    clock.tick(5)
    state.run_due_polls()
    assert len(client.calls) == 1
    clock.tick(30)
    state.run_due_polls()
    assert len(client.calls) == 2


# ---------------------------------------------------------------- 4, 5: fills and terminal


def test_a_partial_fill_keeps_the_order_tracked(settings):
    clock = _Clock()
    state = _state(settings, clock=clock)
    with db.session_scope() as s:
        _order(s, koid="K-1", qty=3)
    _connect_and_track(state)
    state.handle_message(_fill("K-1", "KXT-A", 1, trade_id="f-1"))
    od = state.orders["K-1"]
    assert od.terminal_at is None and od.remaining == 2.0
    state.handle_message(_user_order("K-1", "KXT-A", "resting", 2, 1))
    assert od.terminal_at is None and od.remaining == 2.0
    with db.session_scope() as s:
        fe = s.scalar(select(m.ExecutionFillEvent))
        assert fe.ts_ms == 1758024001234 and float(fe.count_fp) == 1.0
        assert float(fe.fee_cost) == 0.01 and fe.is_taker is False
        oe = s.scalar(select(m.ExecutionOrderEvent))
        assert float(oe.remaining_count_fp) == 2.0 and oe.status == "resting"


def test_a_full_fill_moves_the_market_to_the_post_window(settings):
    clock = _Clock()
    client = _ReadClient({"queue_positions": [
        {"order_id": "K-1", "market_ticker": "KXT-A", "queue_position_fp": "3.00"}]})
    state = _state(settings, client, clock, execution_telemetry_post_window_seconds=600)
    with db.session_scope() as s:
        _order(s, koid="K-1", qty=1)
    _connect_and_track(state)
    state.run_due_polls()                       # at_rest
    state.handle_message(_fill("K-1", "KXT-A", 1))
    od = state.orders["K-1"]
    assert od.terminal_at == clock.now and od.terminal_reason == "ws_filled"
    assert state.run_due_polls() == c.TRIGGER_TERMINAL
    assert state.run_due_polls() is None, "a terminal order is not polled again"
    assert "KXT-A" in state.markets, "post-fill window keeps the market"
    clock.tick(601)
    cmds = state.refresh_tracked()
    assert "KXT-A" not in state.markets and "K-1" not in state.orders
    assert any(cmd["params"].get("action") == "delete_markets" for cmd in cmds)
    with db.session_scope() as s:
        triggers = [t.trigger for t in s.scalars(select(m.LiveOrderQueueTick)).all()]
    assert triggers == ["at_rest", "terminal"]


def test_a_db_terminal_order_starts_the_post_window_too(settings):
    clock = _Clock()
    state = _state(settings, clock=clock)
    with db.session_scope() as s:
        row = _order(s, koid="K-1")
    _connect_and_track(state)
    with db.session_scope() as s:
        s.get(m.LiveOrder, row.id).status = "canceled"
    state.refresh_tracked()
    assert state.orders["K-1"].terminal_reason == "db_terminal"


# ---------------------------------------------------------------- 7, 8, 9, 10: the book


def test_deltas_reconstruct_price_level_quantities(settings):
    state = _state(settings)
    with db.session_scope() as s:
        _order(s, koid="K-1", price=7)       # NO bid at 7 == yes-price level 93 on side "no"
    _connect_and_track(state)
    state.handle_message(_snapshot("KXT-A", yes=[["0.0500", "40.00"]],
                                   no=[["0.9200", "5.00"], ["0.9300", "10.00"]]))
    state.handle_message(_delta("KXT-A", 93, -4, seq=2))
    state.handle_message(_delta("KXT-A", 93, 2.5, seq=3))
    state.handle_message(_delta("KXT-A", 92, -5, seq=4))
    book = state.markets["KXT-A"].book
    assert book.valid and book.no == {93: 8.5} and book.yes == {5: 40.0}
    assert book.best_yes_bid() == 5 and book.best_yes_ask() == 93
    feats = book.features_for("no", 93)
    assert feats["qty_at_our_price"] == 8.5 and feats["qty_better"] == 0.0
    assert feats["spread"] == 88 and feats["distance_from_best"] == 0
    with db.session_scope() as s:
        rows = s.scalars(select(m.ExecutionBookEvent).order_by(m.ExecutionBookEvent.id)).all()
    assert [r.kind for r in rows] == ["snapshot", "delta", "delta", "delta"]
    assert [float(r.level_qty_after) for r in rows[1:]] == [6.0, 8.5, 0.0]
    assert all(r.price_convention == "yes" for r in rows)


def test_price_convention_is_recorded_and_no_side_is_yes_priced(settings):
    """Our NO-bid at 7c lives at yes-price 93 on side `no`; a delta at yes 93 side `no` is at
    our level, a delta at no-price 7 (yes 7) is NOT. If the convention ever flipped, the
    check below would put our level on the wrong side of the spread."""
    assert c.our_level("no", 7) == ("no", 93)
    assert c.our_level("yes", 7) == ("yes", 7)
    assert c.our_level("no", None) is None and c.our_level("bogus", 7) is None
    state = _state(settings)
    with db.session_scope() as s:
        _order(s, koid="K-1", price=7)
    _connect_and_track(state)
    state.handle_message(_snapshot("KXT-A", no=[["0.9300", "10.00"]]))
    state.handle_message(_delta("KXT-A", 94, -3, side="no", seq=2))   # a WORSE no-bid
    assert state._event_poll_due is None
    state.handle_message(_delta("KXT-A", 93, -3, side="yes", seq=3))  # other side
    assert state._event_poll_due is None
    state.handle_message(_delta("KXT-A", 93, -3, side="no", seq=4))   # our level
    assert state._event_poll_trigger == c.TRIGGER_EVENT_DELTA


def test_a_sequence_gap_is_recorded_and_a_snapshot_requested(settings):
    state = _state(settings)
    with db.session_scope() as s:
        _order(s, koid="K-1")
    _connect_and_track(state)
    state.handle_message(_snapshot("KXT-A", no=[["0.9300", "10.00"]], seq=7))
    assert state.handle_message(_delta("KXT-A", 93, -1, seq=8)) == []
    cmds = state.handle_message(_delta("KXT-A", 93, -1, seq=10))
    assert cmds and cmds[0]["params"]["action"] == "get_snapshot"
    assert cmds[0]["params"]["market_tickers"] == ["KXT-A"] and cmds[0]["params"]["sid"] == 2
    assert state.markets["KXT-A"].book.valid is False
    with db.session_scope() as s:
        gap = s.scalar(select(m.ExecutionCollectorEvent).where(
            m.ExecutionCollectorEvent.kind == c.EV_SEQ_GAP))
        assert gap.detail_json == {"channel": "orderbook_delta", "sid": 2, "expected": 9, "got": 10}
    state.handle_message(_snapshot("KXT-A", no=[["0.9300", "8.00"]], seq=11))
    assert state.markets["KXT-A"].book.valid and state.markets["KXT-A"].book.no == {93: 8.0}
    assert state.handle_message(_delta("KXT-A", 93, -1, seq=12)) == []


def test_reconnect_resubscribes_and_marks_books_invalid_until_snapshot(settings):
    state = _state(settings)
    with db.session_scope() as s:
        _order(s, koid="K-1")
    _connect_and_track(state)
    state.handle_message(_snapshot("KXT-A", no=[["0.9300", "10.00"]]))
    assert state.markets["KXT-A"].book.valid
    state.on_disconnected("ConnectionClosed: 1006")
    assert not state.markets["KXT-A"].book.valid
    cmds = state.on_connected()
    channels = [tuple(cmd["params"]["channels"]) for cmd in cmds]
    assert ("fill",) in channels and ("user_orders",) in channels \
        and ("market_lifecycle_v2",) in channels
    assert any(cmd["params"].get("market_tickers") == ["KXT-A"] for cmd in cmds)
    assert state.connection_id == 2
    with db.session_scope() as s:
        kinds = [e.kind for e in s.scalars(select(m.ExecutionCollectorEvent)).all()]
    assert kinds.count(c.EV_CONNECTED) == 2 and c.EV_DISCONNECTED in kinds


def test_a_negative_level_marks_the_book_invalid_not_a_fiction(settings):
    state = _state(settings)
    with db.session_scope() as s:
        _order(s, koid="K-1")
    _connect_and_track(state)
    state.handle_message(_snapshot("KXT-A", no=[["0.9300", "1.00"]]))
    state.handle_message(_delta("KXT-A", 93, -5, seq=2))
    assert state.markets["KXT-A"].book.valid is False
    with db.session_scope() as s:
        assert s.scalar(select(m.ExecutionCollectorEvent).where(
            m.ExecutionCollectorEvent.kind == c.EV_BOOK_INVALID)) is not None
        row = s.scalar(select(m.ExecutionBookEvent).where(m.ExecutionBookEvent.kind == "delta"))
        assert row.level_qty_after is None and float(row.delta_fp) == -5


# ---------------------------------------------------------------- 11: trades


def test_trades_are_attributed_to_the_tracked_market_only_while_tracked(settings):
    clock = _Clock()
    state = _state(settings, clock=clock)
    with db.session_scope() as s:
        _order(s, koid="K-1", ticker="KXT-A", price=7)
    _connect_and_track(state)
    state.handle_message(_trade("KXT-B", 93, 4, trade_id="t-other"))   # not tracked
    state.handle_message(_trade("KXT-A", 93, 4, trade_id="t-1", taker="yes"))
    state.handle_message(_trade("KXT-A", 93, 4, trade_id="t-1", seq=2))  # replay: idempotent
    state.handle_message(_trade("KXT-A", 50, 9, trade_id="t-2", seq=3, taker="no"))
    with db.session_scope() as s:
        rows = s.scalars(select(m.ExecutionTradeEvent)).all()
    assert sorted(r.trade_id for r in rows) == ["t-1", "t-2"]
    assert all(r.market_ticker == "KXT-A" for r in rows)
    assert state._event_poll_trigger == c.TRIGGER_EVENT_TRADE
    feats = state._features(state.orders["K-1"], clock.now)
    assert feats["trades_since_placement"] == 2
    assert feats["volume_at_our_price"] == 4.0
    assert feats["volume_hitting_our_side_at_price"] == 4.0
    assert feats["volume_since_placement"] == 13.0


# ---------------------------------------------------------------- 17: budget


def test_event_polls_are_capped_per_minute_and_debounced(settings):
    clock = _Clock()
    client = _ReadClient({"queue_positions": [
        {"order_id": "K-1", "market_ticker": "KXT-A", "queue_position_fp": "3.00"}]})
    state = _state(settings, client, clock, execution_queue_max_polls_per_minute=3,
                   execution_queue_event_debounce_seconds=2.0,
                   execution_queue_poll_seconds=3600)
    with db.session_scope() as s:
        _order(s, koid="K-1", price=7)
    _connect_and_track(state)
    state.run_due_polls()                   # at_rest
    assert len(client.calls) == 1
    # a burst of trades at our price collapses into ONE poll after the debounce window
    for i in range(5):
        state.handle_message(_trade("KXT-A", 93, 1, trade_id=f"t-{i}", seq=i + 1))
    assert state.run_due_polls() is None    # inside the debounce window
    clock.tick(2.5)
    assert state.run_due_polls() == c.TRIGGER_EVENT_TRADE
    assert len(client.calls) == 2
    # the per-minute cap holds across further bursts
    for n in range(4):
        state.handle_message(_trade("KXT-A", 93, 1, trade_id=f"u-{n}", seq=10 + n))
        clock.tick(3)
        state.run_due_polls()
    assert len(client.calls) == 1 + 3, "3 event polls per minute, then throttled"
    with db.session_scope() as s:
        throttled = s.scalars(select(m.ExecutionCollectorEvent).where(
            m.ExecutionCollectorEvent.kind == c.EV_THROTTLED)).all()
    assert throttled and "per-minute cap" in throttled[-1].detail
    clock.tick(61)
    state.handle_message(_trade("KXT-A", 93, 1, trade_id="v-1", seq=30))
    clock.tick(3)
    assert state.run_due_polls() == c.TRIGGER_EVENT_TRADE


def test_raw_event_persistence_is_capped_and_the_drop_is_recorded(settings):
    state = _state(settings, execution_book_events_max_per_minute=3)
    with db.session_scope() as s:
        _order(s, koid="K-1")
    _connect_and_track(state)
    state.handle_message(_snapshot("KXT-A", no=[["0.9300", "100.00"]]))
    for i in range(6):
        state.handle_message(_delta("KXT-A", 93, -1, seq=i + 2))
    assert state.markets["KXT-A"].book.no == {93: 94.0}, "the local book still tracks"
    with db.session_scope() as s:
        n = len(s.scalars(select(m.ExecutionBookEvent)).all())
        ev = s.scalar(select(m.ExecutionCollectorEvent).where(
            m.ExecutionCollectorEvent.kind == c.EV_THROTTLED))
    assert n == 3 and ev is not None and ev.detail_json["dropped"] >= 1


# ---------------------------------------------------------------- lifecycle


def test_market_lifecycle_events_land_for_tracked_markets_only(settings):
    state = _state(settings)
    with db.session_scope() as s:
        _order(s, koid="K-1")
    _connect_and_track(state)
    pause = {"type": "market_lifecycle_v2", "sid": 15, "seq": 1, "msg": {
        "market_ticker": "KXT-A", "event_type": "deactivated", "is_deactivated": True}}
    other = {"type": "market_lifecycle_v2", "sid": 15, "seq": 2, "msg": {
        "market_ticker": "KXT-Z", "event_type": "settled", "settled_ts": 1758024000}}
    state.handle_message(pause)
    state.handle_message(other)
    with db.session_scope() as s:
        rows = s.scalars(select(m.ExecutionMarketEvent)).all()
    assert len(rows) == 1 and rows[0].event_type == "deactivated" and rows[0].is_deactivated


# ---------------------------------------------------------------- 6, 12, 14, 15: executor side


class _PlaceClient:
    def __init__(self):
        self.placed = []
        self.canceled = []

    def create_events_order(self, order):
        self.placed.append(order)
        return {"order": {"order_id": f"K-{len(self.placed)}", "ts_ms": 1758024000123}}

    def cancel_events_order(self, order_id, *, exchange_index=None):
        self.canceled.append(order_id)
        return {}

    def get_market_exchange_index(self, ticker):
        return 1

    def get_queue_positions(self, **kw):
        return {"queue_positions": []}

    def get_order_queue_position(self, order_id):
        raise RuntimeError("gone")

    def get_orders(self):
        return {"orders": []}

    def get_fills(self):
        return {"fills": []}

    def get_positions(self):
        return {"market_positions": []}


def _metrics(ticker="KXT-A"):
    from kalshi_bot.scanner.metrics import MarketMetrics
    return MarketMetrics(
        ticker=ticker, best_yes_bid=5, best_yes_ask=93, best_no_bid=7, best_no_ask=95,
        midpoint=49.0, spread=88, depth_at_best_bid=40, depth_at_best_ask=10, top_depth=50,
        volume=1200, open_interest=300, last_price=6, time_to_close_seconds=7200.0,
        liquidity_score=1.0, two_sided=True,
        raw_orderbook={"orderbook_fp": {"yes_dollars": [["0.0500", "40.00"]],
                                        "no_dollars": [["0.9300", "10.00"]]}})


def _live_executor(settings, client):
    settings.bot_mode = "live"
    settings.kill_switch = False
    settings.live_enabled = True
    settings.live_strategies = "Fmmsell10"
    settings.mmsell_live_max_spread_cents = 95   # the fixture book is 5/93 wide
    _db(settings)
    return LiveExecutor(client, settings, RiskManager(settings))


def test_decision_context_is_written_before_submit_and_carries_no_post_order_keys(settings):
    client = _PlaceClient()
    ex = _live_executor(settings, client)
    ctx = {"series": "KXT", "twin_tag": "Fmmsell10_pt4", "hours_to_close": 2.0,
           "band_lo": 5, "band_hi": 10, "max_yes": 7, "review_tier": "graduated",
           "regime": "sports", "market_type": "spread", "market_mode": "in_play",
           "open_positions_for_tag": 12, "open_position_cap": 40,
           "close_time": T0 + timedelta(hours=2)}
    with db.session_scope() as s:
        out = ex.mirror_mmsell_entry(
            s, strategy="Fmmsell10", event_ticker="KXT-E", ticker="KXT-A", metrics=_metrics(),
            no_price=7, account_state={"cash_balance": 100.0}, decision_context=ctx)
        assert out == "placed"
        row = s.scalar(select(m.LiveOrder))
        c_row = s.scalar(select(m.ExecutionOrderContext))
        assert c_row.live_order_id == row.id and c_row.client_order_id == row.client_order_id
        assert c_row.kalshi_order_id == "K-1" and c_row.ack_ts_ms == 1758024000123
        assert c_row.decided_at <= c_row.acked_at
        assert c_row.no_price == 7 and c_row.yes_price == 93 and float(c_row.quantity) == 1
        assert c_row.best_no_bid == 7 and c_row.depth_at_best_ask == 10
        assert c_row.hours_to_close == 2.0 and c_row.band_lo == 5 and c_row.max_yes == 7
        assert c_row.twin_tag == "Fmmsell10_pt4" and c_row.review_tier == "graduated"
        assert c_row.book_json["orderbook_fp"]["no_dollars"] == [["0.9300", "10.00"]]
        # Nothing post-submit is allowed into the decision-time payload.
        for forbidden in ("order_id", "kalshi_order_id", "fill", "status", "queue", "ack"):
            assert not any(forbidden in k for k in c_row.context_json), c_row.context_json
    # Decision-time columns can never be stamped afterwards.
    with db.session_scope() as s:
        try:
            repo.stamp_execution_order_context(s, row.id, no_price=99)
        except ValueError as exc:
            assert "decision-time" in str(exc)
        else:
            raise AssertionError("a decision-time column was stamped post-submit")


def test_context_row_records_twin_tag_and_never_a_paper_outcome(settings):
    """The paper/live linkage is the twin tag + market + time; the paper side's own outcome
    (opened / gate) stays in live_paper_parity_events, so a universe refusal and an execution
    miss can never be collapsed into one label here."""
    client = _PlaceClient()
    ex = _live_executor(settings, client)
    with db.session_scope() as s:
        ex.mirror_mmsell_entry(
            s, strategy="Fmmsell10", event_ticker="KXT-E", ticker="KXT-A", metrics=_metrics(),
            no_price=7, account_state={"cash_balance": 100.0},
            decision_context={"twin_tag": "Fmmsell10_pt4", "series": "KXT"})
        c_row = s.scalar(select(m.ExecutionOrderContext))
    assert c_row.twin_tag == "Fmmsell10_pt4"
    cols = {col.name for col in m.ExecutionOrderContext.__table__.columns}
    assert not any(n.startswith(("paper_", "twin_outcome", "live_outcome")) for n in cols)


def test_cancel_stamps_request_and_confirm_times(settings):
    client = _PlaceClient()
    ex = _live_executor(settings, client)
    with db.session_scope() as s:
        ex.mirror_mmsell_entry(
            s, strategy="Fmmsell10", event_ticker="KXT-E", ticker="KXT-A", metrics=_metrics(),
            no_price=7, account_state={"cash_balance": 100.0}, decision_context={})
        row = s.scalar(select(m.LiveOrder))
        row.created_at = datetime.now(timezone.utc) - timedelta(seconds=settings.live_order_timeout_seconds + 5)
    with db.session_scope() as s:
        ex.reconcile(s)
        c_row = s.scalar(select(m.ExecutionOrderContext))
        row = s.scalar(select(m.LiveOrder))
    assert client.canceled == ["K-1"] and row.status == "canceled"
    assert c_row.cancel_requested_at is not None
    assert c_row.cancel_confirmed_at >= c_row.cancel_requested_at
    assert c_row.terminal_reason == "timeout"


def test_reconcile_stamps_ws_fill_events_with_the_rest_fill(settings):
    client = _PlaceClient()
    ex = _live_executor(settings, client)
    with db.session_scope() as s:
        ex.mirror_mmsell_entry(
            s, strategy="Fmmsell10", event_ticker="KXT-E", ticker="KXT-A", metrics=_metrics(),
            no_price=7, account_state={"cash_balance": 100.0}, decision_context={})
        repo.insert_execution_fill_event(
            s, trade_id="f-1", kalshi_order_id="K-1", client_order_id=None,
            market_ticker="KXT-A", ts_ms=1758024001234, received_at=T0, yes_price_cents=93,
            count_fp=1.0, fee_cost=0.01, is_taker=False, outcome_side="no", book_side="ask",
            post_position_fp=1.0, exchange_index=2, raw_json={})
        repo.insert_execution_fill_event(
            s, trade_id="f-orphan", kalshi_order_id="K-9", client_order_id=None,
            market_ticker="KXT-Z", ts_ms=1, received_at=T0, yes_price_cents=50, count_fp=1.0,
            fee_cost=0.0, is_taker=False, outcome_side="no", book_side="ask",
            post_position_fp=1.0, exchange_index=2, raw_json={})
    client.get_fills = lambda: {"fills": [{
        "trade_id": "f-1", "fill_id": "f-1", "order_id": "K-1", "ticker": "KXT-A",
        "market_ticker": "KXT-A", "side": "no", "action": "buy", "count_fp": "1.00",
        "yes_price_dollars": "0.9300", "no_price_dollars": "0.0700", "is_taker": False,
        "created_time": "2026-09-16T12:00:01Z", "fee_cost": "0.010000"}]}
    with db.session_scope() as s:
        ex.reconcile(s)
        rest = s.scalar(select(m.Fill))
        events = {e.trade_id: e for e in s.scalars(select(m.ExecutionFillEvent)).all()}
    assert events["f-1"].rest_fill_id == rest.id and events["f-1"].rest_reconciled_at is not None
    assert events["f-orphan"].rest_fill_id is None, "an unmatched WS fill stays visible"


# ---------------------------------------------------------------- thread glue


def test_the_thread_is_fail_soft_and_records_its_own_life(settings):
    """A connect function that raises must not escape; the state records start/stop."""
    state = _state(settings)

    def boom(*a, **k):
        raise OSError("no network")

    thread = c.TelemetryThread(state, connect=boom, max_backoff=0.01)
    assert thread.start() is True
    import time
    time.sleep(0.15)
    thread.stop(timeout=2)
    with db.session_scope() as s:
        kinds = [e.kind for e in s.scalars(select(m.ExecutionCollectorEvent)).all()]
    assert c.EV_THREAD_STARTED in kinds and c.EV_THREAD_STOPPED in kinds


def test_start_collector_honours_the_kill_switch(settings):
    settings.execution_telemetry_enabled = False
    assert c.start_collector(object(), settings) is None


def test_local_book_features_handle_an_empty_book():
    book = LocalBook("KXT-A")
    feats = book.features_for("no", 93)
    assert feats["book_valid"] is False and feats["best_yes_bid"] is None
    assert feats["qty_at_our_price"] == 0.0 and feats["imbalance"] is None


def test_other_frame_types_on_a_sequenced_sid_are_not_read_as_gaps(settings):
    """The lifecycle sid also carries event_lifecycle / event_fee_update frames. Ignoring
    them must consume their seq — production recorded eight false gaps in its first hour."""
    state = _state(settings)
    with db.session_scope() as s:
        _order(s, koid="K-1")
    _connect_and_track(state)
    frames = [
        {"type": "market_lifecycle_v2", "sid": 15, "seq": 190, "msg": {"market_ticker": "KXT-Z", "event_type": "created"}},
        {"type": "event_lifecycle", "sid": 15, "seq": 191, "msg": {"event_ticker": "KXT", "title": "t"}},
        {"type": "event_fee_update", "sid": 15, "seq": 192, "msg": {"event_ticker": "KXT"}},
        {"type": "market_metadata_updated", "sid": 15, "seq": 193, "msg": {"market_ticker": "KXT-A", "event_type": "metadata_updated"}},
        {"type": "market_lifecycle_v2", "sid": 15, "seq": 194, "msg": {"market_ticker": "KXT-A", "event_type": "deactivated", "is_deactivated": True}},
    ]
    for f in frames:
        assert state.handle_message(f) == []
    with db.session_scope() as s:
        gaps = s.scalars(select(m.ExecutionCollectorEvent).where(
            m.ExecutionCollectorEvent.kind == c.EV_SEQ_GAP)).all()
        assert gaps == []
        assert s.scalar(select(m.ExecutionMarketEvent)).event_type == "deactivated"
    # and a real gap on that sid is still a gap
    state.handle_message({"type": "event_lifecycle", "sid": 15, "seq": 196, "msg": {}})
    with db.session_scope() as s:
        gap = s.scalar(select(m.ExecutionCollectorEvent).where(
            m.ExecutionCollectorEvent.kind == c.EV_SEQ_GAP))
        assert gap.detail_json["expected"] == 195 and gap.detail_json["channel"] == "market_lifecycle_v2"
