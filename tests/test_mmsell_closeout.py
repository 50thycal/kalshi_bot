"""LiveExecutor.close_mmsell_positions — the one-shot end-of-strategy exit path.

mmsell was built hold-to-settlement only (no TP/SL, no early exit); this is the ONLY mechanism
that can close a live mmsell NO position before settlement. Proves it's inert by default, finds
open NO positions via Kalshi-truth position snapshots (not the paper record), buys YES at the
ask (marketable, crosses the spread) to flatten, tags the order distinctly, skips flat/YES
positions and in-flight orders, and is fail-soft per-position.
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
    def __init__(self, yes_ask=10):
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
    settings.mmsell_closeout_enabled = True
    settings.mmsell_closeout_strategies = "mmsell3"
    settings.mmsell_closeout_slippage_cents = 3
    for k, v in over.items():
        setattr(settings, k, v)
    return settings


def _exec(settings, client):
    return LiveExecutor(client, settings, RiskManager(settings))


def _seed_open_no_position(session, *, ticker="KXTEAM-26-A", strategy="mmsell3", no_price=90, qty=1):
    """A filled mmsell entry (side='no') + a Kalshi position snapshot showing it still open.
    quantity is SIGNED to match real reconcile() behavior (_to_count on the raw signed
    position_fp -> negative for a NO position) -- a positive fixture value here would mask the
    exact sign bug this test file exists to catch (real: 0 closeouts fired on 50 open positions
    because `int(snap.quantity or ...)` picked the negative signed int)."""
    repo.create_live_order(
        session, signal_id=None, ticker=ticker, event_ticker="KXTEAM-26", strategy=strategy,
        side="no", action="buy", limit_price=no_price, quantity=qty, status="filled",
        client_order_id=f"{strategy}:{uuid.uuid4()}", raw_order_json={},
    )
    repo.insert_position_snapshot(
        session, ticker=ticker, side="no", quantity=-qty, quantity_fp=-float(qty),
        avg_price=float(no_price), market_exposure=qty * no_price / 100.0,
        realized_pnl=None,
    )


def test_closeout_inert_by_default(settings):
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeCloseoutClient()
    ex = _exec(settings, client)  # closeout_enabled defaults False
    with db.session_scope() as session:
        _seed_open_no_position(session)
        n = ex.close_mmsell_positions(session)
    assert n == 0 and client.placed == []


def test_closeout_each_switch_blocks(settings):
    db.init_engine(settings.database_url)
    db.create_all()
    for over in ({"mmsell_closeout_enabled": False}, {"mmsell_closeout_strategies": ""},
                 {"bot_mode": "weather"}):
        client = FakeCloseoutClient()
        ex = _exec(_closeout_settings(settings, **over), client)
        with db.session_scope() as session:
            _seed_open_no_position(session)
            ex.close_mmsell_positions(session)
        assert client.placed == [], f"should not close with {over}"


def test_closeout_buys_yes_at_ask_to_flatten(settings):
    _closeout_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeCloseoutClient(yes_ask=10)  # no-bid = 90 (matches the seeded entry price)
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _seed_open_no_position(session, no_price=90, qty=2)
        n = ex.close_mmsell_positions(session)
    assert n == 1
    o = client.placed[0]
    # buys YES (side=bid) to flatten a held NO position; crosses the ask + slippage.
    assert o["side"] == "bid" and o["count"] == "2.00"
    assert o["price"] == "0.1300"  # ask 10c + 3c slippage = 13c
    assert o["time_in_force"] == "immediate_or_cancel"
    assert "post_only" not in o  # confirmed live: 400 invalid_parameters if paired with IOC
    assert o["client_order_id"].startswith("closeout:mmsell3:KXTEAM-26-A:")
    # required by the V2 API -- omitting it 400s live (missing_parameters); regression guard.
    assert o["self_trade_prevention_type"] == "taker_at_cross"
    with db.session_scope() as session:
        row = session.scalar(select(m.LiveOrder).where(m.LiveOrder.action == "buy",
                                                        m.LiveOrder.side == "yes"))
        assert row is not None
        assert row.strategy == "mmsell3_closeout"  # distinctly tagged, per the ask
        assert row.status == "submitted" and row.kalshi_order_id == "K-1"


def test_closeout_skips_flat_and_yes_positions(settings):
    _closeout_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeCloseoutClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        # flat (settled) position -> nothing to close
        repo.create_live_order(
            session, signal_id=None, ticker="KXTEAM-26-B", event_ticker="KXTEAM-26",
            strategy="mmsell3", side="no", action="buy", limit_price=90, quantity=1,
            status="filled", client_order_id="mmsell3:flat", raw_order_json={})
        repo.insert_position_snapshot(
            session, ticker="KXTEAM-26-B", side="no", quantity=0, quantity_fp=0.0,
            avg_price=90.0, market_exposure=0.0, realized_pnl=0.10)
        n = ex.close_mmsell_positions(session)
    assert n == 0 and client.placed == []


def test_closeout_skips_ticker_with_inflight_order(settings):
    _closeout_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeCloseoutClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _seed_open_no_position(session)
        repo.create_live_order(  # a resting order already in flight on this exact ticker
            session, signal_id=None, ticker="KXTEAM-26-A", event_ticker="KXTEAM-26",
            strategy="mmsell3", side="no", action="buy", limit_price=90, quantity=1,
            status="resting", client_order_id="mmsell3:inflight", raw_order_json={})
        n = ex.close_mmsell_positions(session)
    assert n == 0 and client.placed == []


def test_closeout_only_matches_listed_strategy_prefix(settings):
    _closeout_settings(settings, mmsell_closeout_strategies="mmsell4")  # NOT mmsell3
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeCloseoutClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _seed_open_no_position(session, strategy="mmsell3")
        n = ex.close_mmsell_positions(session)
    assert n == 0 and client.placed == []  # mmsell3 not in the allowlist -> untouched


def test_closeout_one_rejection_does_not_block_others(settings):
    _closeout_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeCloseoutClient()
    client.raise_exc = KalshiAPIError(400, "invalid_order", "/portfolio/events/orders")
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _seed_open_no_position(session, ticker="KXTEAM-26-A")
        _seed_open_no_position(session, ticker="KXTEAM-26-B")
        n = ex.close_mmsell_positions(session)
    assert n == 0  # both rejected, but neither raised -- the loop completed
    with db.session_scope() as session:
        rows = session.scalars(
            select(m.LiveOrder).where(m.LiveOrder.action == "buy", m.LiveOrder.side == "yes")
        ).all()
        assert len(rows) == 2 and all(r.status == "rejected" for r in rows)
