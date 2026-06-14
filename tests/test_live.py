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
        self.balance = {"balance": 100_000}
        self.place_exc: Exception | None = None

    def place_order(self, **order):
        self.placed.append(order)
        if self.place_exc is not None:
            raise self.place_exc
        return {"order": {"order_id": f"K-{len(self.placed)}", "status": "resting"}}

    def cancel_order(self, order_id):
        self.canceled.append(order_id)
        return {}

    def get_orders(self, **kw):
        return {"orders": self.orders}

    def get_fills(self, **kw):
        return {"fills": self.fills}

    def get_positions(self, **kw):
        return {"market_positions": self.positions}

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


def test_manage_exits_places_sell_on_tp(settings):
    _live_settings(settings, live_exit_mode="tp_sl", live_take_profit_cents=10)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    # current bid via orderbook -> best_yes_bid 60; entry 48 -> gain 12 >= tp 10 -> sell
    ex = _exec(settings, client)
    with db.session_scope() as session:
        repo.create_live_order(
            session, signal_id=None, ticker="T1", event_ticker="E1", strategy="weather_low_fav_h20",
            side="yes", action="buy", limit_price=48, quantity=1, status="filled",
            client_order_id="weather_low_fav_h20:E1", raw_order_json={})
        ex.manage_exits(session)
        assert len(client.placed) == 1 and client.placed[0]["action"] == "sell"
        assert ex.summary.exits_placed == 1
        # idempotent: a committed exit order blocks a second sell
        ex.manage_exits(session)
        assert len(client.placed) == 1


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
