"""The live real-money execution layer (kalshi_bot/live). Proves it is INERT by default
and fires only when fully enabled + allowlisted, with fail-closed handling throughout."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot import repository as repo
from kalshi_bot.kalshi.errors import AuthError, KalshiAPIError, TransientError
from kalshi_bot.live.executor import LiveExecutor
from kalshi_bot.live.exit_rules import should_exit
from kalshi_bot.risk.manager import RiskManager
from kalshi_bot.scanner.metrics import MarketMetrics


def _metrics(*, ask=50, bid=48, depth=100, two_sided=True, spread=2, liq=10.0):
    return MarketMetrics(
        ticker="KXHIGHLAX-26JUN12-B74.5", best_yes_bid=bid, best_yes_ask=ask,
        best_no_bid=100 - ask if ask else None, best_no_ask=100 - bid if bid else None,
        midpoint=(bid + ask) / 2 if (bid and ask) else None, spread=spread,
        depth_at_best_bid=depth, depth_at_best_ask=depth, top_depth=depth, volume=1000,
        open_interest=500, last_price=ask, time_to_close_seconds=6 * 3600,
        liquidity_score=liq, two_sided=two_sided,
    )


class FakeLiveClient:
    def __init__(self):
        self.placed: list[dict] = []
        self.canceled: list[str] = []
        self.orders: list[dict] = []
        self.fills: list[dict] = []
        self.positions: list[dict] = []
        self.settlements: list[dict] = []
        self.balance = {"balance": 100_000}
        self.place_exc: Exception | None = None
        # Optional per-call script: each item is an Exception to raise or a dict to return.
        self.place_responses: list = []
        self.v1_user_ids: list[str] = []
        self.v1_market_tickers: list[str] = ["WX-D1-B1"]  # tickers the v1 event exposes

    def _respond(self, default):
        if self.place_responses:
            item = self.place_responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if self.place_exc is not None:
            raise self.place_exc
        return default

    def place_order(self, **order):
        self.placed.append(order)
        return self._respond({"order": {"order_id": f"K-{len(self.placed)}", "status": "resting"}})

    def create_v1_order(self, user_id, order):
        self.placed.append(order)
        self.v1_user_ids.append(user_id)
        return self._respond({"order": {"order_id": f"K-{len(self.placed)}", "status": "executed"}})

    def get_v1_event(self, series, event):
        # nested markets carry the UUID; key by ticker_name like the real v1 event
        return {"event": {"markets": [
            {"ticker_name": t, "id": f"MID-{t}"} for t in self.v1_market_tickers]}}

    def cancel_order(self, order_id):
        self.canceled.append(order_id)
        return {}

    def get_orders(self, **kw):
        return {"orders": self.orders}

    def get_fills(self, **kw):
        return {"fills": self.fills}

    def get_positions(self, **kw):
        return {"market_positions": self.positions}

    def get_settlements(self, **kw):
        return {"settlements": self.settlements}

    def get_balance(self):
        return self.balance

    def get_orderbook(self, ticker, depth=None):
        return {"orderbook_fp": {"yes_dollars": [["0.60", "100"]], "no_dollars": [["0.38", "100"]]}}


def _live_settings(settings, **over):
    settings.bot_mode = "live"
    settings.kill_switch = False
    settings.live_enabled = True
    settings.live_strategies = "weather_low_fav"
    settings.live_max_order_dollars = 5.0
    settings.max_order_size = 100
    settings.live_entry_style = "marketable"
    settings.live_user_id = "U-TEST"
    for k, v in over.items():
        setattr(settings, k, v)
    return settings


def _exec(settings, client=None):
    return LiveExecutor(client or FakeLiveClient(), settings, RiskManager(settings))


def _enter(ex, session, *, strategy="weather_low_fav_h20", metrics=None):
    ex.mirror_entry(
        session, strategy=strategy, event_ticker="KXHIGHLAX-26JUN12",
        ticker="KXHIGHLAX-26JUN12-B74.5", side="yes", action="buy",
        metrics=metrics or _metrics(), account_state={"cash_balance": 1000.0},
    )


# --- inert / gating ----------------------------------------------------------------


def test_inert_by_default(settings):
    # defaults: bot_mode!=live, kill_switch on, live_enabled false, empty allowlist
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _enter(ex, session)
        assert client.placed == []
        assert session.scalars(select(m.LiveOrder)).all() == []


def test_each_gate_blocks_independently(settings):
    db.init_engine(settings.database_url)
    db.create_all()
    for over in ({"live_enabled": False}, {"kill_switch": True},
                 {"bot_mode": "weather"}, {"live_strategies": ""}):
        client = FakeLiveClient()
        s = _live_settings(settings, **over)
        ex = _exec(s, client)
        with db.session_scope() as session:
            _enter(ex, session)
        assert client.placed == [], f"should not place with {over}"


def test_fires_when_fully_enabled_and_allowlisted(settings):
    _live_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _enter(ex, session)
        assert len(client.placed) == 1
        o = client.placed[0]
        assert o["ticker"] == "KXHIGHLAX-26JUN12-B74.5"
        assert o["action"] == "buy" and o["side"] == "yes" and o["type"] == "limit"
        assert o["client_order_id"] == "weather_low_fav_h20:KXHIGHLAX-26JUN12"
        row = session.scalar(select(m.LiveOrder))
        assert row.status == "submitted" and row.kalshi_order_id == "K-1"


def test_fractional_entry_sizes_by_dollars(settings):
    # live_fractional -> entry uses count_fp = dollars/price (fixed-point), not integer count.
    _live_settings(settings, live_fractional=True, live_max_order_dollars=1.5)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        # entry ask = 50c (metrics ask) -> 1.5 / 0.50 = 3.00 contracts
        _enter(ex, session, metrics=_metrics(ask=50, bid=48))
        assert len(client.placed) == 1
        o = client.placed[0]
        assert o["count_fp"] == "3.00" and "count" not in o
        assert o["action"] == "buy" and o["side"] == "yes" and o["type"] == "limit"


def test_city_filter_blocks_non_listed_city(settings):
    _live_settings(settings, live_strategies="weather_fav", live_cities="DEN,MIA,LAX")
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        # NYC high favorite -> not in the city allowlist -> blocked
        ex.mirror_entry(session, strategy="weather_fav_h8", event_ticker="KXHIGHNY-26JUN12",
                        ticker="KXHIGHNY-26JUN12-B74.5", side="yes", action="buy",
                        metrics=_metrics(), account_state={"cash_balance": 1000.0})
        assert client.placed == []
        # DEN high favorite -> allowed
        ex.mirror_entry(session, strategy="weather_fav_h8", event_ticker="KXHIGHDEN-26JUN12",
                        ticker="KXHIGHDEN-26JUN12-B74.5", side="yes", action="buy",
                        metrics=_metrics(), account_state={"cash_balance": 1000.0})
        assert len(client.placed) == 1


def test_window_filter_blocks_non_listed_window(settings):
    _live_settings(settings, live_strategies="weather_fav", live_windows="8")
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        ex.mirror_entry(session, strategy="weather_fav_h20", event_ticker="KXHIGHDEN-26JUN12",
                        ticker="KXHIGHDEN-26JUN12-B74.5", side="yes", action="buy",
                        metrics=_metrics(), account_state={"cash_balance": 1000.0})
        assert client.placed == []  # h20 not in {8}
        ex.mirror_entry(session, strategy="weather_fav_h8", event_ticker="KXHIGHDEN-26JUN13",
                        ticker="KXHIGHDEN-26JUN13-B74.5", side="yes", action="buy",
                        metrics=_metrics(), account_state={"cash_balance": 1000.0})
        assert len(client.placed) == 1  # h8 allowed


def test_dedup_blocks_second_entry(settings):
    _live_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _enter(ex, session)
        _enter(ex, session)  # same (event, strategy)
        assert len(client.placed) == 1


def test_risk_blocks_and_records_reason(settings):
    _live_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _enter(ex, session, metrics=_metrics(two_sided=False))
        assert client.placed == []
        re = session.scalar(select(m.RiskEvent))
        assert re is not None and not re.approved
        assert "MARKET_NOT_TWO_SIDED" in re.reason_codes_json


# --- sizing ------------------------------------------------------------------------


def test_dollar_cap_sizing(settings):
    db.init_engine(settings.database_url)
    db.create_all()
    cases = [
        # (cap, ask, depth, max_order_size) -> expected count
        (5.0, 50, 100, 100, 10),   # floor(5/0.50)=10
        (5.0, 50, 4, 100, 4),      # depth caps
        (5.0, 50, 100, 2, 2),      # max_order_size caps
        (5.0, 99, 100, 100, 5),    # floor(5/0.99)=5
    ]
    for cap, ask, depth, mos, expected in cases:
        client = FakeLiveClient()
        s = _live_settings(settings, live_max_order_dollars=cap, max_order_size=mos)
        ex = _exec(s, client)
        with db.session_scope() as session:
            ex.mirror_entry(
                session, strategy="weather_low_fav_h20",
                event_ticker=f"E-{ask}-{depth}-{mos}", ticker=f"T-{ask}-{depth}-{mos}",
                side="yes", action="buy", metrics=_metrics(ask=ask, depth=depth),
                account_state={"cash_balance": 1000.0},
            )
        assert client.placed[0]["count"] == expected, (cap, ask, depth, mos)


def test_passive_vs_marketable_price(settings):
    db.init_engine(settings.database_url)
    db.create_all()

    def _place(client, event):
        ex = _exec(settings, client)
        with db.session_scope() as session:
            ex.mirror_entry(session, strategy="weather_low_fav_h20", event_ticker=event,
                            ticker="T-" + event, side="yes", action="buy",
                            metrics=_metrics(ask=50), account_state={"cash_balance": 1000.0})

    # marketable: yes_price == ask
    c1 = FakeLiveClient()
    _live_settings(settings, live_entry_style="marketable")
    _place(c1, "EVT-MKT")
    assert c1.placed[0]["yes_price"] == 50
    # passive: yes_price == ask - offset (distinct event so dedup doesn't block)
    c2 = FakeLiveClient()
    _live_settings(settings, live_entry_style="passive", live_passive_offset_cents=3)
    _place(c2, "EVT-PASV")
    assert c2.placed[0]["yes_price"] == 47


# --- failure modes -----------------------------------------------------------------


def test_kalshi_api_error_marks_rejected_no_raise(settings):
    _live_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    client.place_exc = KalshiAPIError(400, "bad order", "/portfolio/orders")
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _enter(ex, session)  # must not raise
        row = session.scalar(select(m.LiveOrder))
        assert row.status == "rejected"


def test_transient_error_marks_unknown(settings):
    _live_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    client.place_exc = TransientError("network")
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _enter(ex, session)
        row = session.scalar(select(m.LiveOrder))
        assert row.status == "unknown"


def test_auth_error_propagates(settings):
    _live_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    client.place_exc = AuthError("401")
    ex = _exec(settings, client)
    with db.session_scope() as session:
        try:
            _enter(ex, session)
            raised = False
        except AuthError:
            raised = True
        assert raised


# --- reconciliation ----------------------------------------------------------------


def test_reconcile_dedups_fills_and_snapshots_positions(settings):
    _live_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    # real Kalshi fill shape: *_dollars price strings, count_fp, fee_cost, market_ticker
    client.fills = [{"trade_id": "F1", "order_id": "K-1", "market_ticker": "T1", "side": "yes",
                     "action": "buy", "yes_price_dollars": "0.60", "no_price_dollars": "0.40",
                     "count_fp": "2.00", "fee_cost": "0.01"}]
    # real Kalshi market_positions shape: position_fp, *_dollars strings
    client.positions = [{"ticker": "T1", "position_fp": "2.00",
                         "market_exposure_dollars": "1.20", "realized_pnl_dollars": "0.000000"}]
    ex = _exec(settings, client)
    with db.session_scope() as session:
        ex.reconcile(session)
        ex.reconcile(session)  # same fill again -> still one row
        fills = session.scalars(select(m.Fill)).all()
        assert len(fills) == 1
        # parsed from Kalshi's real field names
        assert fills[0].price == 60 and fills[0].quantity == 2
        assert float(fills[0].fee) == 0.01 and fills[0].market_ticker == "T1"
        positions = session.scalars(select(m.Position)).all()
        assert len(positions) == 2  # one snapshot per cycle
        assert positions[0].quantity == 2 and positions[0].side == "yes"
        assert float(positions[0].realized_pnl) == 0.0


def test_reconcile_cancels_timed_out_passive_order(settings):
    _live_settings(settings, live_order_timeout_seconds=60)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        row = repo.create_live_order(
            session, signal_id=None, ticker="T1", event_ticker="E1", strategy="weather_low_fav_h20",
            side="yes", action="buy", limit_price=48, quantity=1, status="resting",
            client_order_id="weather_low_fav_h20:E1", raw_order_json={})
        row.created_at = datetime.now(timezone.utc) - timedelta(seconds=120)
        row.kalshi_order_id = "K-9"
        session.flush()
        ex.reconcile(session)
        assert client.canceled == ["K-9"]
        assert session.get(m.LiveOrder, row.id).status == "canceled"


def test_unknown_order_resolved_by_client_order_id(settings):
    _live_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    client.orders = [{"order_id": "K-7", "client_order_id": "weather_low_fav_h20:E1",
                      "status": "executed"}]
    ex = _exec(settings, client)
    with db.session_scope() as session:
        repo.create_live_order(
            session, signal_id=None, ticker="T1", event_ticker="E1", strategy="weather_low_fav_h20",
            side="yes", action="buy", limit_price=48, quantity=1, status="unknown",
            client_order_id="weather_low_fav_h20:E1", raw_order_json={})
        ex.reconcile(session)
        row = session.scalar(select(m.LiveOrder))
        assert row.status == "filled" and row.kalshi_order_id == "K-7"


def test_never_landed_order_marked_not_landed(settings):
    _live_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()  # no matching exchange order
    ex = _exec(settings, client)
    with db.session_scope() as session:
        repo.create_live_order(
            session, signal_id=None, ticker="T1", event_ticker="E1", strategy="weather_low_fav_h20",
            side="yes", action="buy", limit_price=48, quantity=1, status="unknown",
            client_order_id="weather_low_fav_h20:E1", raw_order_json={})
        ex.reconcile(session)
        assert session.scalar(select(m.LiveOrder)).status == "not_landed"


# --- daily-loss circuit breaker ----------------------------------------------------


def test_daily_loss_trips_and_blocks(settings):
    _live_settings(settings, max_daily_loss=5.0)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        repo.insert_position_snapshot(session, ticker="T1", side="yes", quantity=0,
                                      avg_price=0.0, realized_pnl=-10.0)
        _enter(ex, session)
        assert client.placed == []  # breaker blocks new entries


# --- exit rules --------------------------------------------------------------------


def test_should_exit_rules():
    assert should_exit(50, [55], 61, tp=10, sl=None, be=None) == "tp"
    assert should_exit(50, [], 39, tp=None, sl=10, be=None) == "sl"
    assert should_exit(50, [58, 56], 49, tp=None, sl=None, be=5) == "be"   # armed then fell back
    assert should_exit(50, [51, 52], 49, tp=None, sl=None, be=5) is None   # never armed
    assert should_exit(50, [60], 55, tp=10, sl=10, be=None) is None        # no trigger now


def _filled_entry(session, *, ticker="WX-D1-B1", strategy="weather_low_fav_h20", qty=1, price=48,
                  qty_fp=None):
    repo.create_live_order(
        session, signal_id=None, ticker=ticker, event_ticker="E1", strategy=strategy,
        side="yes", action="buy", limit_price=price, quantity=qty, status="filled",
        client_order_id=f"{strategy}:E1", raw_order_json={})
    # open_live_positions is driven by Kalshi position snapshots, so seed one (net-long YES).
    repo.insert_position_snapshot(
        session, ticker=ticker, side="yes", quantity=qty, quantity_fp=qty_fp,
        avg_price=float(price), market_exposure=qty * price / 100.0, realized_pnl=0.0)


def test_probe_buy_is_fractional(settings):
    # live_probe buy -> a fractional v2 order (count_fp = dollars/ask), isolated from the strategy.
    _live_settings(settings, live_probe="buy:WX-D1-B1:1.5")
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()  # orderbook -> best_yes_ask = 100 - no_bid(38) = 62
    ex = _exec(settings, client)
    with db.session_scope() as session:
        ex.run_probe(session)
        assert len(client.placed) == 1
        o = client.placed[0]
        assert o["action"] == "buy" and o["side"] == "yes" and o["type"] == "limit"
        assert o["count_fp"] == "2.42"  # round(1.5 / 0.62, 2)
        assert o["client_order_id"] == "probe:buy:WX-D1-B1"
        # one-shot: a second run does not duplicate
        ex.run_probe(session)
        assert len(client.placed) == 1


def test_probe_close_only_matches_prefix(settings):
    # live_probe close -> targeted fractional close of ONLY the prefix; strategy positions untouched.
    _live_settings(settings, live_probe="close:KXHIGHMIA", live_exit_mode="settlement")
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    client.v1_market_tickers = ["KXHIGHMIA-26JUN15-B93.5"]  # the v1 event exposes this market's id
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _filled_entry(session, ticker="KXHIGHMIA-26JUN15-B93.5", strategy="probe", qty=3, qty_fp=3.12)
        _filled_entry(session, ticker="KXHIGHDEN-26JUN15-B79.5", strategy="weather_fav_h14", qty=3)
        ex.run_probe(session)
        # only the MIA position is closed (exact fractional remainder); DEN is never touched
        assert len(client.placed) == 1
        assert client.placed[0]["count_fp"] == "3.12"
        assert client.placed[0]["market_id"] == "MID-KXHIGHMIA-26JUN15-B93.5"


def test_fractional_close_sizes_exact_remainder(settings):
    # A fractional position (3.12 shares) must close the EXACT remainder, not round to 3.00.
    _live_settings(settings, live_exit_mode="tp_sl", live_take_profit_cents=10)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _filled_entry(session, qty=3, qty_fp=3.12, price=48)
        ex.manage_exits(session)
        assert len(client.placed) == 1
        assert client.placed[0]["count_fp"] == "3.12"


def test_fractional_residual_is_managed(settings):
    # A sub-1-share residual (0.12) must still be surfaced for closing (not rounded to flat).
    _live_settings(settings, live_exit_mode="tp_sl", live_take_profit_cents=10)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _filled_entry(session, qty=0, qty_fp=0.12, price=48)
        ex.manage_exits(session)
        assert len(client.placed) == 1
        assert client.placed[0]["count_fp"] == "0.12"


def _rejected_exit(session, attempt, *, ticker="WX-D1-B1", strategy="weather_low_fav_h20"):
    repo.create_live_order(
        session, signal_id=None, ticker=ticker, event_ticker=None, strategy=strategy,
        side="yes", action="sell", limit_price=60, quantity=1, status="rejected",
        client_order_id=f"exit:{strategy}:{ticker}:{attempt}", raw_order_json={})


def test_exit_primary_sell_yes_close(settings):
    _live_settings(settings, live_exit_mode="tp_sl", live_take_profit_cents=10)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _filled_entry(session, price=48)  # bid 60 - entry 48 = +12 >= tp 10 -> close
        ex.manage_exits(session)
        assert len(client.placed) == 1
        o = client.placed[0]
        # close = the v1 position-capped market sell (the app's request shape)
        assert o["order_action"] == "sell" and o["user_side"] == "yes" and o["side"] == "no"
        assert o["order_type"] == "market" and o["count_fp"] == "1.00"
        assert o["sell_position_capped"] is True and o["market_id"] == "MID-WX-D1-B1"
        assert o["price_dollars"] == "0.4000"  # no-price = 100 - yes_bid(60)
        assert client.v1_user_ids[0] == "U-TEST"
        assert ex.summary.exits_placed == 1
        # after the IOC close fills, Kalshi shows the position flat -> no re-attempt
        client.positions = [{"ticker": "WX-D1-B1", "position_fp": "0",
                             "market_exposure_dollars": "0", "realized_pnl_dollars": "0"}]
        ex.reconcile(session)
        ex.manage_exits(session)
        assert len(client.placed) == 1


def test_exit_rejection_escalates_and_does_not_block(settings):
    _live_settings(settings, live_exit_mode="tp_sl", live_take_profit_cents=10,
                   live_exit_slippage_cents=3, live_exit_max_attempts=3)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    client.place_responses = [KalshiAPIError(400, "invalid parameters", "/p")]  # 1st rejects
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _filled_entry(session, price=48)
        ex.manage_exits(session)  # attempt 1 -> rejected (terminal, not in-flight)
        assert ex.summary.rejected == 1 and len(client.placed) == 1
        ex.manage_exits(session)  # rejected didn't block -> attempt 2 fires (regression)
        assert len(client.placed) == 2
        assert client.placed[1]["order_action"] == "sell"  # still the v1 close
        rows = session.scalars(select(m.LiveOrder).where(
            m.LiveOrder.client_order_id.like("exit:%"))).all()
        assert any(r.client_order_id.endswith(":2") for r in rows)


def test_exit_409_treated_as_success(settings):
    _live_settings(settings, live_exit_mode="tp_sl", live_take_profit_cents=10)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    client.place_responses = [KalshiAPIError(409, "order already exists", "/p")]
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _filled_entry(session, price=48)
        ex.manage_exits(session)
        row = session.scalar(select(m.LiveOrder).where(m.LiveOrder.client_order_id.like("exit:%")))
        assert row.status == "submitted" and ex.summary.exits_placed == 1
        ex.manage_exits(session)  # now in-flight -> no new order
        assert len(client.placed) == 1


def test_exit_partial_fill_sizes_remainder(settings):
    _live_settings(settings, live_exit_mode="tp_sl", live_take_profit_cents=10)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    client.positions = [{"ticker": "WX-D1-B1", "position_fp": "2.00",
                         "market_exposure_dollars": "1.00", "realized_pnl_dollars": "0"}]
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _filled_entry(session, qty=5, price=48)
        ex.reconcile(session)       # snapshot shows 2 still open
        ex.manage_exits(session)    # size the close to the remaining 2, not the original 5
        assert client.placed[0]["count_fp"] == "2.00"


def test_exit_flat_position_skips(settings):
    _live_settings(settings, live_exit_mode="tp_sl", live_take_profit_cents=10)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    client.positions = [{"ticker": "WX-D1-B1", "position_fp": "0",
                         "market_exposure_dollars": "0", "realized_pnl_dollars": "0"}]
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _filled_entry(session, qty=1)
        ex.reconcile(session)       # snapshot shows flat
        ex.manage_exits(session)
        assert client.placed == []  # nothing to close


def test_v1_exit_resolved_by_fill_on_reconcile(settings):
    # A submitted v1 close (IOC, not in the v2 orders feed) is marked 'filled' once its fill
    # lands (matched by kalshi_order_id), since v1 fills async.
    _live_settings(settings, live_exit_mode="tp_sl", live_take_profit_cents=10)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _filled_entry(session, price=48)
        ex.manage_exits(session)            # places the v1 close -> submitted, koid 'K-1'
        row = session.scalar(select(m.LiveOrder).where(m.LiveOrder.client_order_id.like("exit:%")))
        assert row.status == "submitted" and row.kalshi_order_id == "K-1"
        # the async fill lands carrying that order_id -> reconcile resolves the order to filled
        client.fills = [{"trade_id": "XF1", "order_id": "K-1", "market_ticker": "WX-D1-B1",
                         "side": "no", "action": "sell", "no_price_dollars": "0.37",
                         "count_fp": "1.00", "fee_cost": "0.01"}]
        ex.reconcile(session)
        session.refresh(row)
        assert row.status == "filled"


def test_rejected_exit_does_not_permanently_block(settings):
    # Regression for the dedup bug: a rejected exit must NOT hide the still-open position.
    _live_settings(settings, live_exit_mode="tp_sl", live_take_profit_cents=10)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _filled_entry(session, price=48)
        _rejected_exit(session, 1)
        ex.manage_exits(session)
        assert len(client.placed) == 1  # the rejected :1 did not hide the open position
        row = session.scalar(select(m.LiveOrder).where(
            m.LiveOrder.client_order_id == "exit:weather_low_fav_h20:WX-D1-B1:2"))
        assert row is not None and row.status == "submitted"


def test_exit_bounded_attempts_holds_and_logs_critical(settings, caplog):
    import logging
    _live_settings(settings, live_exit_mode="tp_sl", live_take_profit_cents=10,
                   live_exit_max_attempts=2)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _filled_entry(session)
        _rejected_exit(session, 1)
        _rejected_exit(session, 2)  # 2 attempts == cap
        with caplog.at_level(logging.CRITICAL):
            ex.manage_exits(session)
        assert client.placed == []          # no new attempt; hold to settlement
        assert ex.summary.exits_abandoned == 1
        assert any("exhausted attempts" in r.message for r in caplog.records)
        ex.manage_exits(session)            # one-shot -> not double counted
        assert ex.summary.exits_abandoned == 1


def test_exit_v1_close_is_position_capped_market_sell(settings):
    _live_settings(settings, live_exit_mode="tp_sl", live_take_profit_cents=10,
                   live_exit_max_attempts=3)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _filled_entry(session)
        _rejected_exit(session, 1)  # a prior reject must not change the v1 close shape
        ex.manage_exits(session)
        o = client.placed[0]
        assert o["order_type"] == "market" and o["order_action"] == "sell"
        assert o["count_fp"] == "1.00" and o["sell_position_capped"] is True
        assert "yes_price" not in o and "no_price" not in o


def test_exit_full_error_logged_on_rejection(settings, caplog):
    import logging
    _live_settings(settings, live_exit_mode="tp_sl", live_take_profit_cents=10)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    client.place_responses = [KalshiAPIError(400, '{"error":{"code":"invalid_parameters"}}', "/p")]
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _filled_entry(session, price=48)
        with caplog.at_level(logging.ERROR):
            ex.manage_exits(session)
        row = session.scalar(select(m.LiveOrder).where(
            m.LiveOrder.client_order_id.like("exit:%"), m.LiveOrder.action == "sell"))
        assert row.status == "rejected" and row.cancel_reason.startswith("400:")
        assert row.raw_order_json.get("order_action") == "sell"  # exact v1 payload kept for RCA
        assert any("REJECTED" in r.message for r in caplog.records)


def test_exit_transient_marks_unknown(settings):
    _live_settings(settings, live_exit_mode="tp_sl", live_take_profit_cents=10)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    client.place_responses = [TransientError("net")]
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _filled_entry(session, price=48)
        ex.manage_exits(session)
        row = session.scalar(select(m.LiveOrder).where(m.LiveOrder.client_order_id.like("exit:%")))
        assert row.status == "unknown"


def test_reconcile_records_settlement_pnl_for_daily_loss(settings):
    _live_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    client = FakeLiveClient()
    # a losing YES position: bought $0.82, revenue 0 at settlement -> ~-0.83 realized
    client.settlements = [{"ticker": "KXLOWTDEN-26JUN13-B54.5", "market_result": "no",
                           "revenue": 0, "yes_total_cost_dollars": "0.82",
                           "no_total_cost_dollars": "0.00", "fee_cost": "0.0104",
                           "settled_time": now_iso}]
    ex = _exec(settings, client)
    with db.session_scope() as session:
        ex.reconcile(session)
        ex.reconcile(session)  # idempotent: already recorded -> not double counted
        snaps = session.scalars(
            select(m.Position).where(m.Position.market_ticker == "KXLOWTDEN-26JUN13-B54.5")
        ).all()
        assert len(snaps) == 1 and snaps[0].quantity == 0
        assert abs(float(snaps[0].realized_pnl) - (-0.8304)) < 1e-6
        assert abs(repo.live_realized_pnl_today(session) - (-0.8304)) < 1e-6


def test_manage_exits_noop_in_settlement_mode(settings):
    _live_settings(settings, live_exit_mode="settlement")
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        repo.create_live_order(
            session, signal_id=None, ticker="T1", event_ticker="E1", strategy="weather_low_fav_h20",
            side="yes", action="buy", limit_price=48, quantity=1, status="filled",
            client_order_id="weather_low_fav_h20:E1", raw_order_json={})
        ex.manage_exits(session)
        assert client.placed == []
