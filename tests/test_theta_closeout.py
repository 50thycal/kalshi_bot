"""LiveExecutor.close_theta_positions — the one-shot end-of-strategy exit path.

Mirrors test_mmsell_closeout.py exactly (same mechanism, same V2 events endpoint, same
Kalshi-truth position-snapshot source) — theta was built hold-to-settlement only, so this is
the ONLY mechanism that can close a live theta NO position before settlement.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot import repository as repo
from kalshi_bot.kalshi.errors import KalshiAPIError
from kalshi_bot.live.executor import LiveExecutor
from kalshi_bot.risk.manager import RiskManager


class FakeCloseoutClient:
    def __init__(self, yes_ask=21):
        self.placed: list[dict] = []
        self.yes_ask = yes_ask
        self.raise_exc: Exception | None = None

    def create_events_order(self, order):
        if self.raise_exc is not None:
            raise self.raise_exc
        self.placed.append(order)
        return {"order": {"order_id": f"K-{len(self.placed)}", "status": "executed"}}

    def get_orderbook(self, ticker, depth=None):
        no_bid = 100 - self.yes_ask
        return {"orderbook_fp": {
            "yes_dollars": [[f"{(self.yes_ask - 2) / 100:.2f}", "300"]],
            "no_dollars": [[f"{no_bid / 100:.2f}", "300"]],
        }}


def _closeout_settings(settings, **over):
    settings.bot_mode = "live"
    settings.theta_closeout_enabled = True
    settings.theta_closeout_strategies = "theta4"
    settings.theta_closeout_slippage_cents = 3
    for k, v in over.items():
        setattr(settings, k, v)
    return settings


def _exec(settings, client):
    return LiveExecutor(client, settings, RiskManager(settings))


def _seed_open_no_position(session, *, ticker="KXBTCD-26JUL0317-T60500", strategy="theta4",
                           no_price=79, qty=3):
    repo.create_live_order(
        session, signal_id=None, ticker=ticker, event_ticker="KXBTCD-26JUL0317", strategy=strategy,
        side="no", action="buy", limit_price=no_price, quantity=qty, status="filled",
        client_order_id=f"{strategy}:{uuid.uuid4()}", raw_order_json={},
    )
    repo.insert_position_snapshot(
        session, ticker=ticker, side="no", quantity=-qty, quantity_fp=-float(qty),
        avg_price=float(no_price), market_exposure=qty * no_price / 100.0,
        realized_pnl=None,
    )


def test_theta_closeout_inert_by_default(settings):
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeCloseoutClient()
    ex = _exec(settings, client)  # closeout_enabled defaults False
    with db.session_scope() as session:
        _seed_open_no_position(session)
        n = ex.close_theta_positions(session)
    assert n == 0 and client.placed == []


def test_theta_closeout_each_switch_blocks(settings):
    db.init_engine(settings.database_url)
    db.create_all()
    for over in ({"theta_closeout_enabled": False}, {"theta_closeout_strategies": ""},
                 {"bot_mode": "weather"}):
        client = FakeCloseoutClient()
        ex = _exec(_closeout_settings(settings, **over), client)
        with db.session_scope() as session:
            _seed_open_no_position(session)
            ex.close_theta_positions(session)
        assert client.placed == [], f"should not close with {over}"


def test_theta_closeout_buys_yes_at_ask_to_flatten(settings):
    _closeout_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeCloseoutClient(yes_ask=21)  # no-bid = 79 (matches the seeded entry price)
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _seed_open_no_position(session, no_price=79, qty=3)
        n = ex.close_theta_positions(session)
    assert n == 1
    o = client.placed[0]
    assert o["side"] == "bid" and o["count"] == "3.00"
    assert o["price"] == "0.2400"  # ask 21c + 3c slippage = 24c
    assert o["time_in_force"] == "immediate_or_cancel"
    assert "post_only" not in o
    assert "reduce_only" not in o
    assert o["client_order_id"].startswith("closeout:theta4:KXBTCD-26JUL0317-T60500:")
    assert o["self_trade_prevention_type"] == "taker_at_cross"
    with db.session_scope() as session:
        row = session.scalar(select(m.LiveOrder).where(m.LiveOrder.action == "buy",
                                                        m.LiveOrder.side == "yes"))
        assert row is not None
        assert row.strategy == "theta4_closeout"
        assert row.status == "submitted" and row.kalshi_order_id == "K-1"


def test_theta_closeout_skips_flat_and_yes_positions(settings):
    _closeout_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeCloseoutClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        repo.create_live_order(
            session, signal_id=None, ticker="KXBTCD-26JUL0317-T61000",
            event_ticker="KXBTCD-26JUL0317", strategy="theta4", side="no", action="buy",
            limit_price=79, quantity=1, status="filled", client_order_id="theta4:flat",
            raw_order_json={})
        repo.insert_position_snapshot(
            session, ticker="KXBTCD-26JUL0317-T61000", side="no", quantity=0, quantity_fp=0.0,
            avg_price=79.0, market_exposure=0.0, realized_pnl=0.10)
        n = ex.close_theta_positions(session)
    assert n == 0 and client.placed == []


def test_theta_closeout_skips_ticker_with_inflight_order(settings):
    _closeout_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeCloseoutClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _seed_open_no_position(session)
        repo.create_live_order(
            session, signal_id=None, ticker="KXBTCD-26JUL0317-T60500",
            event_ticker="KXBTCD-26JUL0317", strategy="theta4", side="no", action="buy",
            limit_price=79, quantity=1, status="resting", client_order_id="theta4:inflight",
            raw_order_json={})
        n = ex.close_theta_positions(session)
    assert n == 0 and client.placed == []


def test_theta_closeout_only_matches_listed_strategy_prefix(settings):
    _closeout_settings(settings, theta_closeout_strategies="theta9")  # NOT theta4
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeCloseoutClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _seed_open_no_position(session, strategy="theta4")
        n = ex.close_theta_positions(session)
    assert n == 0 and client.placed == []  # theta4 not in the allowlist -> untouched


def test_theta_closeout_one_rejection_does_not_block_others(settings):
    _closeout_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeCloseoutClient()
    client.raise_exc = KalshiAPIError(400, "invalid_order", "/portfolio/events/orders")
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _seed_open_no_position(session, ticker="KXBTCD-26JUL0317-T60500")
        _seed_open_no_position(session, ticker="KXBTCD-26JUL0317-T61000")
        n = ex.close_theta_positions(session)
    assert n == 0
    with db.session_scope() as session:
        rows = session.scalars(
            select(m.LiveOrder).where(m.LiveOrder.action == "buy", m.LiveOrder.side == "yes")
        ).all()
        assert len(rows) == 2 and all(r.status == "rejected" for r in rows)


def test_theta_closeout_does_not_touch_mmsell(settings):
    """A sanity guard specific to having TWO closeout paths now: theta's closeout must never
    pick up an mmsell position, and mmsell's must never pick up a theta one."""
    _closeout_settings(settings, theta_closeout_strategies="theta4",
                       mmsell_closeout_enabled=True, mmsell_closeout_strategies="mmsell3")
    db.init_engine(settings.database_url)
    db.create_all()
    theta_client = FakeCloseoutClient()
    mmsell_client = FakeCloseoutClient()
    theta_ex = _exec(settings, theta_client)
    mmsell_ex = _exec(settings, mmsell_client)
    with db.session_scope() as session:
        _seed_open_no_position(session, ticker="KXBTCD-26JUL0317-T60500", strategy="theta4")
        _seed_open_no_position(session, ticker="KXTEAM-26-A", strategy="mmsell3", no_price=90)
        theta_n = theta_ex.close_theta_positions(session)
        mmsell_n = mmsell_ex.close_mmsell_positions(session)
    assert theta_n == 1 and mmsell_n == 1
    assert len(theta_client.placed) == 1 and theta_client.placed[0]["client_order_id"].startswith(
        "closeout:theta4:")
    assert len(mmsell_client.placed) == 1 and mmsell_client.placed[0][
        "client_order_id"].startswith("closeout:mmsell3:")
