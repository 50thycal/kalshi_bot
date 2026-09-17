"""The liquidity-incentive LIVE path (`LiveExecutor.mirror_incentive_entry`, WS-020 Phase 1a).

Every test is a safety property. The default posture is INERT; each gate is proved to place
nothing; and the one test that does place proves the exact wire format of a post-only resting
bid on each side. If a change makes this file pass while spending more, the change is wrong.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot import repository as repo
from kalshi_bot.liquidity_incentive import live as limm
from kalshi_bot.live.executor import LiveExecutor
from kalshi_bot.risk.manager import RiskManager

TAG = "Limm1"


class FakeLiveClient:
    def __init__(self, fail=None):
        self.placed: list[dict] = []
        self.fail = fail

    def create_events_order(self, order):
        self.placed.append(order)
        if self.fail is not None:
            raise self.fail
        return {"order": {"order_id": f"K-{len(self.placed)}", "status": "resting"}}


def _live_settings(settings):
    settings.bot_mode = "live"
    settings.kill_switch = False
    settings.live_enabled = True
    settings.live_strategies = TAG
    settings.max_market_exposure = 25.0
    settings.max_total_exposure = 100.0
    settings.max_daily_loss = 5.0
    settings.live_kill_on_daily_loss = True
    return settings


def _db(settings):
    db.init_engine(settings.database_url)
    db.create_all()


def _quote(side=limm.SIDE_NO, price=21, qty=1, ticker="KXTEST-A"):
    return limm.LiveQuote(
        market_ticker=ticker, side=side, price_cents=price, quantity=qty,
        collateral_usd=round(price * qty / 100.0, 4), max_loss_usd=round(price * qty / 100.0, 4),
        reference_price_cents=price, at_or_above_reference=True, reason="test",
    )


def _exec(settings, client=None):
    return LiveExecutor(client or FakeLiveClient(), settings, RiskManager(settings))


def _place(ex, settings, *, quote=None, account_state=None, ticker="KXTEST-A", strategy=TAG):
    with db.session_scope() as s:
        return ex.mirror_incentive_entry(
            s, strategy=strategy, event_ticker="KXTEST", ticker=ticker,
            quote=quote or _quote(ticker=ticker),
            account_state=account_state if account_state is not None else {"cash_balance": 500.0},
        )


# ------------------------------------------------------------------ inert by default


def test_inert_unless_every_switch_is_on(settings):
    _db(settings)
    _live_settings(settings)
    for attr, value in (("bot_mode", "paper"), ("kill_switch", True), ("live_enabled", False),
                        ("live_strategies", "")):
        _live_settings(settings)
        setattr(settings, attr, value)
        ex = _exec(settings)
        assert _place(ex, settings) == "gate:switches", attr
        assert ex.client.placed == []


def test_a_tag_outside_the_allowlist_places_nothing(settings):
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    assert _place(ex, settings, strategy="SomethingElse") == "gate:switches"
    assert ex.client.placed == []


def test_missing_balance_fails_closed(settings):
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    assert _place(ex, settings, account_state={}) == "gate:no_balance"
    assert ex.client.placed == []


# ------------------------------------------------------------------ the caps


def test_refuses_a_quote_above_the_registered_price_cap(settings):
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    bad = _quote(price=limm.MAX_PRICE_CENTS + 1)
    assert _place(ex, settings, quote=bad) == "gate:size"
    assert ex.client.placed == []


def test_refuses_a_quote_above_the_registered_contract_cap(settings):
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    bad = _quote(qty=limm.MAX_CONTRACTS_PER_ORDER + 1)
    assert _place(ex, settings, quote=bad) == "gate:size"
    assert ex.client.placed == []


def test_strategy_budget_is_enforced_from_the_database(settings):
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    # Pre-load this strategy with orders worth more than its own budget.
    with db.session_scope() as s:
        for i in range(12):
            s.add(m.LiveOrder(
                market_ticker=f"KXOTHER-{i}", event_ticker="KXOTHER", strategy=TAG, side="no",
                action="buy", limit_price=99, quantity=1, status="resting",
                client_order_id=f"c-{i}", created_at=datetime.now(timezone.utc)))
    with db.session_scope() as s:
        assert repo.live_strategy_exposure(s, TAG) > limm.MAX_STRATEGY_EXPOSURE_USD
    assert _place(ex, settings) in ("gate:strategy_exposure", "gate:open_cap")
    assert ex.client.placed == []


def test_open_order_cap_is_this_strategys_own(settings):
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    with db.session_scope() as s:
        for i in range(limm.MAX_OPEN_ORDERS):
            s.add(m.LiveOrder(
                market_ticker=f"KXOPEN-{i}", event_ticker="KXOPEN", strategy=TAG, side="no",
                action="buy", limit_price=5, quantity=1, status="resting",
                client_order_id=f"o-{i}", created_at=datetime.now(timezone.utc)))
    assert _place(ex, settings) == "gate:open_cap"
    assert ex.client.placed == []


def test_never_contests_a_market_another_book_is_resting_in(settings):
    """The cross-strategy guard: an mmsell order on this ticker must block us, and vice versa."""
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    with db.session_scope() as s:
        s.add(m.LiveOrder(
            market_ticker="KXTEST-A", event_ticker="KXTEST", strategy="Fmmsell10", side="no",
            action="buy", limit_price=92, quantity=1, status="resting",
            client_order_id="mmsell-1", created_at=datetime.now(timezone.utc)))
    assert _place(ex, settings) == "gate:dedup"
    assert ex.client.placed == []


def test_shared_daily_loss_stop_blocks_entry(settings):
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    ex._daily_loss_tripped = True
    assert _place(ex, settings) == "gate:daily_loss"
    assert ex.client.placed == []


# ------------------------------------------------------------------ the wire format


def test_places_a_post_only_no_bid_with_the_right_yes_side_price(settings):
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    assert _place(ex, settings, quote=_quote(side=limm.SIDE_NO, price=21)) == "placed"
    order = ex.client.placed[0]
    assert order["side"] == "ask"                    # selling YES == buying NO
    assert order["price"] == "0.7900"                # a NO bid at 21c is a YES ask at 79c
    assert order["count"] == "1.00"                  # decimal STRINGS, not numbers
    assert order["post_only"] is True
    assert order["time_in_force"] == "good_till_canceled"
    with db.session_scope() as s:
        row = s.scalars(select(m.LiveOrder)).one()
        assert (row.side, row.action, row.limit_price, row.quantity) == ("no", "buy", 21, 1)
        assert row.status == "resting" and row.strategy == TAG


def test_places_a_post_only_yes_bid_when_yes_is_the_cheap_side(settings):
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    assert _place(ex, settings, quote=_quote(side=limm.SIDE_YES, price=9)) == "placed"
    order = ex.client.placed[0]
    assert order["side"] == "bid"                    # buying YES
    assert order["price"] == "0.0900"
    assert order["post_only"] is True
    with db.session_scope() as s:
        row = s.scalars(select(m.LiveOrder)).one()
        assert (row.side, row.limit_price) == ("yes", 9)


def test_intent_is_committed_before_the_post(settings):
    """A rejected order must still leave a durable row, or the dedup guard cannot work."""
    from kalshi_bot.kalshi.errors import KalshiAPIError

    _db(settings)
    _live_settings(settings)
    ex = _exec(settings, client=FakeLiveClient(fail=KalshiAPIError(400, "nope", "/portfolio/events/orders")))
    assert _place(ex, settings) == "rejected"
    with db.session_scope() as s:
        row = s.scalars(select(m.LiveOrder)).one()
        assert row.status == "rejected"


def test_a_transient_error_is_never_read_as_not_placed(settings):
    from kalshi_bot.kalshi.errors import TransientError

    _db(settings)
    _live_settings(settings)
    ex = _exec(settings, client=FakeLiveClient(fail=TransientError("timeout")))
    assert _place(ex, settings) == "unknown"
    with db.session_scope() as s:
        assert s.scalars(select(m.LiveOrder)).one().status == "unknown"


def test_one_order_per_call_and_the_whole_test_cannot_exceed_its_budget(settings):
    """End to end: place until the strategy's own caps stop it, and prove the total spent is
    bounded by the registered budget — the property the operator is actually relying on."""
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    codes = []
    for i in range(10):
        codes.append(_place(ex, settings, ticker=f"KXTEST-{i}"))
    assert codes.count("placed") == limm.MAX_OPEN_ORDERS
    assert all(c == "gate:open_cap" for c in codes[limm.MAX_OPEN_ORDERS:])
    with db.session_scope() as s:
        spent = repo.live_strategy_exposure(s, TAG)
        n = s.scalar(select(func.count()).select_from(m.LiveOrder))
    assert n == limm.MAX_OPEN_ORDERS
    assert spent <= limm.MAX_STRATEGY_EXPOSURE_USD
    assert spent <= limm.MAX_OPEN_ORDERS * limm.MAX_ORDER_DOLLARS


# ------------------------------------------------------------------ hold to settlement


def test_the_tp_sl_exit_path_skips_this_book(settings, monkeypatch):
    """This book's registered contract is HOLD TO SETTLEMENT. Production runs LIVE_EXIT_MODE
    =tp_sl for the YES/weather books, and `open_live_positions` returns net-LONG YES positions
    — which a filled YES incentive bid is. Without the tag skip, `manage_exits` would place
    exit orders this book's risk envelope never declared, spending real money and real fees to
    leave a position whose entire downside is the <=$1 already paid."""
    _db(settings)
    _live_settings(settings)
    settings.live_exit_mode = "tp_sl"
    ex = _exec(settings)
    seen: list[str] = []
    monkeypatch.setattr(
        repo, "open_live_positions",
        lambda _s: [("KXA-1", limm.LIVE_TAG, 20, datetime.now(timezone.utc), 1),
                    ("KXA-2", limm.TWIN_TAG, 20, datetime.now(timezone.utc), 1),
                    ("KXB-1", "wx20", 60, datetime.now(timezone.utc), 1)])
    monkeypatch.setattr(repo, "_remaining_open_qty", lambda *a, **k: 1.0, raising=False)
    monkeypatch.setattr(LiveExecutor, "_remaining_open_qty",
                        lambda self, s, t, q: (seen.append(t) or 1.0))
    with db.session_scope() as s:
        ex.manage_exits(s)
    # The weather book is still managed; neither incentive tag is ever looked at.
    assert seen == ["KXB-1"]
