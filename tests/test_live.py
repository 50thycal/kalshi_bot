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


def _metrics(*, ask=50, bid=48, depth=100, two_sided=True, spread=2, liq=10.0, raw=None):
    no_bid = 100 - ask if ask else None
    if raw is None:  # single-level book: `depth` contracts at the best bid/ask
        raw = {"orderbook_fp": {
            "yes": [[f"{bid / 100:.2f}", str(depth)]] if bid else [],
            "no": [[f"{no_bid / 100:.2f}", str(depth)]] if no_bid else [],
        }}
    return MarketMetrics(
        ticker="KXHIGHLAX-26JUN12-B74.5", best_yes_bid=bid, best_yes_ask=ask,
        best_no_bid=no_bid, best_no_ask=100 - bid if bid else None,
        midpoint=(bid + ask) / 2 if (bid and ask) else None, spread=spread,
        depth_at_best_bid=depth, depth_at_best_ask=depth, top_depth=depth, volume=1000,
        open_interest=500, last_price=ask, time_to_close_seconds=6 * 3600,
        liquidity_score=liq, two_sided=two_sided, raw_orderbook=raw,
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
    # live_fractional -> entry is a v1 fractional MARKET buy (count_fp = dollars/price), since the
    # v2 endpoint rejects count_fp. Routed through create_v1_order with the v1 close vocabulary.
    _live_settings(settings, live_fractional=True, live_max_order_dollars=1.5)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    client.v1_market_tickers = ["KXHIGHLAX-26JUN12-B74.5"]  # v1 event exposes the entry market id
    ex = _exec(settings, client)
    with db.session_scope() as session:
        # entry ask = 50c (metrics ask) -> 1.5 / 0.50 = 3.00 contracts
        _enter(ex, session, metrics=_metrics(ask=50, bid=48))
        assert len(client.placed) == 1
        o = client.placed[0]
        assert o["count_fp"] == "3.00" and "count" not in o
        assert o["order_action"] == "buy" and o["side"] == "yes" and o["order_type"] == "market"
        assert o["market_id"] == "MID-KXHIGHLAX-26JUN12-B74.5"
        assert client.v1_user_ids[0] == "U-TEST"
        assert o["price_dollars"] == "0.5200"  # buy_price = ask(50) + 2 through the ask


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


def test_cell_allowlist_targets_exact_cells(settings):
    # live_cells = precise (book:CITY:window) allowlist; it SUPERSEDES city/window filters so a
    # mix (high-fav DEN + low-fav NYC at h20) trades EXACTLY those cells and nothing adjacent.
    _live_settings(settings, live_strategies="weather_fav,weather_low_fav",
                   live_cells="weather_fav:DEN:20,weather_low_fav:NYC:20")
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    acct = {"cash_balance": 1000.0}
    with db.session_scope() as session:
        ent = lambda strat, ev, tkr: ex.mirror_entry(  # noqa: E731
            session, strategy=strat, event_ticker=ev, ticker=tkr, side="yes", action="buy",
            metrics=_metrics(), account_state=acct)
        ent("weather_fav_h20", "KXHIGHDEN-26JUN12", "KXHIGHDEN-26JUN12-B74.5")  # cell -> allowed
        assert len(client.placed) == 1
        ent("weather_fav_h20", "KXHIGHLAX-26JUN12", "KXHIGHLAX-26JUN12-B74.5")  # LAX high: not a cell
        ent("weather_low_fav_h20", "KXLOWTDEN-26JUN12", "KXLOWTDEN-26JUN12-T40")  # low DEN: not a cell
        ent("weather_fav_h14", "KXHIGHDEN-26JUN12", "KXHIGHDEN-26JUN12-B74.5")  # DEN h14: not a cell
        assert len(client.placed) == 1  # all three blocked
        ent("weather_low_fav_h20", "KXLOWTNYC-26JUN12", "KXLOWTNYC-26JUN12-T40")  # cell -> allowed
        assert len(client.placed) == 2


def test_entry_grace_skips_passed_window(settings):
    # On-time only: an entry whose hours_to_close is more than the grace past the window is
    # skipped (no late catch-up); within the grace it enters normally.
    _live_settings(settings, live_strategies="weather_fav", live_entry_grace_hours=2.0)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    acct = {"cash_balance": 1000.0}
    with db.session_scope() as session:
        # h20 window but only 16h to close -> 16 < 20-2 -> window missed -> skip
        ex.mirror_entry(session, strategy="weather_fav_h20", event_ticker="KXHIGHDEN-26JUN12",
                        ticker="KXHIGHDEN-26JUN12-B74.5", side="yes", action="buy",
                        metrics=_metrics(), account_state=acct, hours_to_close=16.0)
        assert client.placed == []
        # 19h to close -> within [18,20] -> on time -> enter
        ex.mirror_entry(session, strategy="weather_fav_h20", event_ticker="KXHIGHDEN-26JUN13",
                        ticker="KXHIGHDEN-26JUN13-B74.5", side="yes", action="buy",
                        metrics=_metrics(), account_state=acct, hours_to_close=19.0)
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
        # slippage=0 isolates the dollar-cap/depth/max_order_size logic (price == ask).
        s = _live_settings(settings, live_max_order_dollars=cap, max_order_size=mos,
                           live_entry_slippage_cents=0)
        ex = _exec(s, client)
        with db.session_scope() as session:
            ex.mirror_entry(
                session, strategy="weather_low_fav_h20",
                event_ticker=f"E-{ask}-{depth}-{mos}", ticker=f"T-{ask}-{depth}-{mos}",
                side="yes", action="buy", metrics=_metrics(ask=ask, depth=depth),
                account_state={"cash_balance": 1000.0},
            )
        assert client.placed[0]["count"] == expected, (cap, ask, depth, mos)


def test_marketable_walks_thin_book_within_slippage(settings):
    """Denver regression: only 1 contract at the best ask, more just above. Bounded-slippage
    sizing should fill the dollar-cap size within ask+slippage instead of a token 1 contract."""
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    s = _live_settings(settings, live_max_order_dollars=3.0, live_entry_slippage_cents=2,
                       max_order_size=100)
    ex = _exec(s, client)
    # YES asks: 1@45c, 4@46c, 10@47c  ->  resting NO bids at 55(1), 54(4), 53(10).
    book = {"orderbook_fp": {"yes": [["0.40", "50"]],
                             "no": [["0.55", "1"], ["0.54", "4"], ["0.53", "10"]]}}
    mx = _metrics(ask=45, bid=40, depth=1, raw=book)  # depth_at_best_ask == 1 (the old cap)
    with db.session_scope() as session:
        ex.mirror_entry(session, strategy="weather_low_fav_h20", event_ticker="E-DEN",
                        ticker="T-DEN", side="yes", action="buy", metrics=mx,
                        account_state={"cash_balance": 1000.0})
    o = client.placed[0]
    # ceiling = 47c; in-band YES-ask depth = 1+4+10 = 15; dollar cap floor(3.0/0.47) = 6 -> 6.
    assert o["yes_price"] == 47
    assert o["count"] == 6  # not 1 (the old depth_at_best_ask cap)


def test_window_exit_params_and_map(settings):
    _live_settings(settings, live_take_profit_by_window="20:5,14:20",
                   live_take_profit_cents=12, live_stop_loss_cents=8)
    assert settings.live_tp_by_window_map == {20: 5, 14: 20}
    ex = _exec(settings)
    # mapped windows are TP-ONLY (no stop), with their own TP.
    assert ex._window_exit_params("weather_fav_h20") == (5, None, None)
    assert ex._window_exit_params("weather_fav_h14") == (20, None, None)
    # an unmapped window falls back to the global TP/SL/BE.
    assert ex._window_exit_params("weather_fav_h8") == (12, 8, None)


def test_per_window_take_profit_triggers(settings):
    # h20 scalps a tight +5; h14 runs to +20. Bid is fixed at 60 by the fake order book.
    _live_settings(settings, live_exit_mode="tp_sl", live_take_profit_by_window="20:5,14:20")
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    client.v1_market_tickers = ["WX-D1-B1", "WX-D2-B1", "WX-D3-B1"]
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _filled_entry(session, ticker="WX-D1-B1", strategy="weather_fav_h20", price=53)   # +7 >= 5  -> exit
        _filled_entry(session, ticker="WX-D2-B1", strategy="weather_fav_h14", price=53)   # +7 < 20  -> hold
        _filled_entry(session, ticker="WX-D3-B1", strategy="weather_fav_h14", price=38)   # +22 >= 20 -> exit
        ex.manage_exits(session)
    assert {o["market_id"] for o in client.placed} == {"MID-WX-D1-B1", "MID-WX-D3-B1"}


def _open_h20(session, *, event, ticker, qty_fp):
    repo.create_live_order(
        session, signal_id=None, ticker=ticker, event_ticker=event, strategy="weather_fav_h20",
        side="yes", action="buy", limit_price=60, quantity=1, status="filled",
        client_order_id=f"weather_fav_h20:{event}", raw_order_json={})
    repo.insert_position_snapshot(
        session, ticker=ticker, side="yes", quantity=int(qty_fp), quantity_fp=qty_fp,
        avg_price=60.0, market_exposure=qty_fp * 0.60, realized_pnl=0.0)


def _h14_entry(ex, session, client):
    _live_settings(ex.settings, live_one_position_per_event=True, live_strategies="weather_fav",
                   live_cells="weather_fav:LAX:20,weather_fav:LAX:14")
    ex.mirror_entry(
        session, strategy="weather_fav_h14", event_ticker="KXHIGHLAX-26JUN12",
        ticker="KXHIGHLAX-26JUN12-B74.5", side="yes", action="buy",
        metrics=_metrics(), account_state={"cash_balance": 1000.0})


def test_one_position_per_event_blocks_later_window(settings):
    _live_settings(settings, live_one_position_per_event=True, live_strategies="weather_fav",
                   live_cells="weather_fav:LAX:20,weather_fav:LAX:14")
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _open_h20(session, event="KXHIGHLAX-26JUN12", ticker="KXHIGHLAX-26JUN12-B74.5", qty_fp=1.0)
        _h14_entry(ex, session, client)
    assert client.placed == []            # h14 blocked while the h20 position is open
    assert ex.summary.skipped_dedup >= 1


def test_one_position_per_event_allows_when_prior_flat(settings):
    _live_settings(settings, live_one_position_per_event=True, live_strategies="weather_fav",
                   live_cells="weather_fav:LAX:20,weather_fav:LAX:14")
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    client.v1_market_tickers = ["KXHIGHLAX-26JUN12-B74.5"]
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _open_h20(session, event="KXHIGHLAX-26JUN12", ticker="KXHIGHLAX-26JUN12-B74.5", qty_fp=0.0)
        _h14_entry(ex, session, client)
    assert len(client.placed) == 1        # h20 flat (settled) -> h14 free to enter


def test_passive_vs_marketable_price(settings):
    db.init_engine(settings.database_url)
    db.create_all()

    def _place(client, event):
        ex = _exec(settings, client)
        with db.session_scope() as session:
            ex.mirror_entry(session, strategy="weather_low_fav_h20", event_ticker=event,
                            ticker="T-" + event, side="yes", action="buy",
                            metrics=_metrics(ask=50), account_state={"cash_balance": 1000.0})

    # marketable: yes_price == ask + slippage (crosses up to the bounded ceiling)
    c1 = FakeLiveClient()
    _live_settings(settings, live_entry_style="marketable", live_entry_slippage_cents=2)
    _place(c1, "EVT-MKT")
    assert c1.placed[0]["yes_price"] == 52
    # marketable, zero slippage: yes_price == ask
    c1b = FakeLiveClient()
    _live_settings(settings, live_entry_style="marketable", live_entry_slippage_cents=0)
    _place(c1b, "EVT-MKT0")
    assert c1b.placed[0]["yes_price"] == 50
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


def test_intent_committed_before_post_survives_rollback_no_dup_order(settings):
    # Regression: the live_orders intent row is COMMITTED before the POST, so a later rollback
    # of the cycle-wide transaction cannot erase a placed order and let the dedup guard re-fire
    # a DUPLICATE real order on the next cycle (the bug that turned one $1.50 buy into three).
    _live_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    # cycle 1: place an entry, then the cycle rolls back (simulating a downstream cycle error).
    with db.session_scope() as session:
        _enter(ex, session)
        assert len(client.placed) == 1
        session.rollback()  # downstream error rolls the whole cycle back
    # the committed intent survived the rollback (without the pre-POST commit it would be gone).
    with db.session_scope() as session:
        assert len(session.scalars(select(m.LiveOrder)).all()) == 1
    # cycle 2: same (event, strategy) -> dedup sees the committed intent -> NO duplicate order.
    with db.session_scope() as session:
        _enter(ex, session)
    assert len(client.placed) == 1  # still exactly one real order ever placed


def test_transient_v1_entry_that_filled_is_not_re_entered(settings):
    # A v1 fractional entry whose POST was indeterminate (TransientError) but actually FILLED must
    # NOT be mislabeled 'not_landed' (which would let dedup re-fire a duplicate). reconcile sees
    # Kalshi's position for the ticker and resolves it to 'submitted', so the next attempt dedups.
    _live_settings(settings, live_fractional=True)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    client.v1_market_tickers = ["KXHIGHLAX-26JUN12-B74.5"]
    client.place_responses = [TransientError("network")]  # the entry POST is indeterminate
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _enter(ex, session)  # v1 POST raises Transient -> row status 'unknown'
    assert len(client.placed) == 1
    # Kalshi now shows the position DID fill (the transient POST actually landed on the exchange).
    client.positions = [{"ticker": "KXHIGHLAX-26JUN12-B74.5", "position_fp": "3.00"}]
    with db.session_scope() as session:
        ex.reconcile(session, {"cash_balance": 1000.0})
        row = session.scalars(select(m.LiveOrder)).all()[0]
        assert row.status == "submitted"  # resolved by exchange state, NOT not_landed
    # next cycle: same (event, strategy) -> dedup holds -> NO duplicate entry
    with db.session_scope() as session:
        _enter(ex, session)
    assert len(client.placed) == 1  # still exactly one real order ever placed


def test_indeterminate_entry_with_no_execution_evidence_is_not_landed(settings):
    # The other side: an indeterminate entry with NO position/fill evidence is correctly marked
    # 'not_landed' so a genuine non-landing can be retried (we don't over-suppress entries).
    _live_settings(settings, live_fractional=True)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    client.v1_market_tickers = ["KXHIGHLAX-26JUN12-B74.5"]
    client.place_responses = [TransientError("network")]
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _enter(ex, session)
    with db.session_scope() as session:
        ex.reconcile(session, {"cash_balance": 1000.0})  # no positions/fills -> no evidence
        assert session.scalars(select(m.LiveOrder)).all()[0].status == "not_landed"


def test_live_cell_coverage_note_deduped(settings):
    # A LIVE cell that can't trade at its window is recorded once (deduped per event+day) as a
    # system_event, so the daily digest can show the no-show (e.g. PHIL-h14 illiquid).
    _live_settings(settings, live_strategies="weather_low_fav",
                   live_cells="weather_low_fav:PHIL:14")
    db.init_engine(settings.database_url)
    db.create_all()
    ex = _exec(settings, FakeLiveClient())
    with db.session_scope() as session:
        assert ex.is_live_cell("weather_low_fav_h14", "KXLOWTPHIL-26JUN16-B59.5")
        assert not ex.is_live_cell("weather_low_fav_h14", "KXLOWTLAX-26JUN16-B59.5")  # wrong city
        assert not ex.is_live_cell("weather_low_fav_h20", "KXLOWTPHIL-26JUN16-B59.5")  # wrong window
        ex.note_cell_skip(session, "weather_low_fav_h14", "KXLOWTPHIL-26JUN16-B59.5", "illiquid")
        ex.note_cell_skip(session, "weather_low_fav_h14", "KXLOWTPHIL-26JUN16-B62.5", "illiquid")
        rows = session.scalars(
            select(m.SystemEvent).where(m.SystemEvent.component == "live_cell")).all()
        assert len(rows) == 1  # same event-day -> deduped to one
        assert "weather_low_fav_h14" in rows[0].message and "PHIL" in rows[0].message


def test_probe_buy_is_fractional(settings):
    # live_probe buy -> a fractional v1 MARKET order (count_fp = dollars/ask). Fractional is a
    # v1-only capability (v2 rejects count_fp), so the probe mirrors the v1 close shape as a buy.
    _live_settings(settings, live_probe="buy:WX-D1-B1:1.5")
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()  # orderbook -> best_yes_ask = 100 - no_bid(38) = 62
    ex = _exec(settings, client)
    with db.session_scope() as session:
        ex.run_probe(session)
        assert len(client.placed) == 1
        o = client.placed[0]
        assert o["order_action"] == "buy" and o["side"] == "yes" and o["order_type"] == "market"
        assert o["count_fp"] == "2.42"  # round(1.5 / 0.62, 2)
        assert o["market_id"] == "MID-WX-D1-B1"
        assert client.v1_user_ids[0] == "U-TEST"
        # buy price = ask(62) + 2 = 64c through the ask; cost cap > notional
        assert o["price_dollars"] == "0.6400"
        assert o["max_cost_cents"] == int(__import__("math").ceil(2.42 * 64)) + 5
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


def test_v1_order_resolved_to_filled_without_koid(settings):
    # The LAX-close fix: a v1 order whose POST was indeterminate (no exchange order_id) is
    # resolved to 'filled' (not mislabeled 'not_landed') when a same-ticker, same-action fill
    # lands — matched by ticker+action and the order_id backfilled for an accurate audit trail.
    _live_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        row = repo.create_live_order(
            session, signal_id=None, ticker="WX-D1-B1", event_ticker="WX-D1",
            strategy="weather_fav_h20", side="yes", action="sell", limit_price=99, quantity=1,
            status="unknown", client_order_id="exit:weather_fav_h20:WX-D1-B1:1", raw_order_json={})
        client.fills = [{"trade_id": "F9", "order_id": "K-EXT", "market_ticker": "WX-D1-B1",
                         "side": "no", "action": "sell", "no_price_dollars": "0.40",
                         "count_fp": "1.00", "fee_cost": "0.01"}]
        ex.reconcile(session)
        session.refresh(row)
        assert row.status == "filled"
        assert row.kalshi_order_id == "K-EXT"  # backfilled from the fill


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
