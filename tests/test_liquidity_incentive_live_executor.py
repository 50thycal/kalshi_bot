"""The liquidity-incentive LIVE path — pair entry and the book's own exits (thesis §9.37).

Every test is a safety property. The default posture is INERT; each gate is proved to place
nothing; the placing tests prove the exact wire format of both legs and of each exit order. If a
change makes this file pass while spending more, the change is wrong.
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
    def __init__(self, fail=None, cancel_fail=None):
        self.placed: list[dict] = []
        self.canceled: list[str] = []
        self.fail = fail
        self.cancel_fail = cancel_fail

    def create_events_order(self, order):
        self.placed.append(order)
        if self.fail is not None:
            raise self.fail
        return {"order": {"order_id": f"K-{len(self.placed)}", "status": "resting"}}

    def cancel_events_order(self, order_id, *, exchange_index=None):
        if self.cancel_fail is not None:
            raise self.cancel_fail
        self.canceled.append(order_id)
        return {}

    def get_market(self, ticker):
        return {"market": {"ticker": ticker}}


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


def _leg(side, price, qty, ticker="KXTEST-A"):
    return limm.LiveQuote(
        market_ticker=ticker, side=side, price_cents=price, quantity=qty,
        collateral_usd=round(price * qty / 100.0, 4), max_loss_usd=round(price * qty / 100.0, 4),
        reference_price_cents=price, at_or_above_reference=True, reason="test",
    )


def _pair(yes=40, no=55, qty=None, ticker="KXTEST-A", no_qty=None):
    q = qty if qty is not None else limm.pair_quantity(yes, no)
    y, n = _leg(limm.SIDE_YES, yes, q, ticker), _leg(limm.SIDE_NO, no, no_qty or q, ticker)
    return limm.PairQuote(
        market_ticker=ticker, yes=y, no=n, quantity=q, edge_cents=100 - yes - no,
        collateral_usd=round(y.collateral_usd + n.collateral_usd, 4),
        max_loss_usd=max(y.collateral_usd, n.collateral_usd), hours_to_close=24.0)


def _exec(settings, client=None):
    return LiveExecutor(client or FakeLiveClient(), settings, RiskManager(settings))


def _place(ex, settings, *, pair=None, account_state=None, ticker="KXTEST-A", strategy=TAG):
    with db.session_scope() as s:
        outcome, _legs = ex.mirror_incentive_pair(
            s, strategy=strategy, event_ticker="KXTEST", ticker=ticker,
            pair=pair or _pair(ticker=ticker),
            account_state=account_state if account_state is not None else {"cash_balance": 500.0},
        )
        return outcome


# ------------------------------------------------------------------ inert by default


def test_inert_unless_every_switch_is_on(settings):
    _db(settings)
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


def test_refuses_a_leg_above_the_registered_price_cap(settings):
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    assert _place(ex, settings, pair=_pair(yes=2, no=limm.MAX_PRICE_CENTS + 1, qty=1)) == "gate:size"
    assert ex.client.placed == []


def test_refuses_a_leg_above_the_registered_contract_cap(settings):
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    assert _place(ex, settings,
                  pair=_pair(yes=1, no=1, qty=limm.MAX_CONTRACTS_PER_ORDER + 1)) == "gate:size"
    assert ex.client.placed == []


def test_refuses_a_leg_above_the_per_leg_dollar_cap(settings):
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    too_many = int(limm.MAX_ORDER_DOLLARS * 100 // 55) + 1
    assert _place(ex, settings, pair=_pair(yes=40, no=55, qty=too_many)) == "gate:size"
    assert ex.client.placed == []


def test_refuses_unequal_legs_and_a_pair_with_no_edge(settings):
    """Unequal legs are not a hedge; a pair that locks nothing is not a pair."""
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    assert _place(ex, settings, pair=_pair(yes=40, no=55, qty=5, no_qty=6)) == "gate:size"
    assert _place(ex, settings, pair=_pair(yes=45, no=55, qty=5)) == "gate:size"
    assert ex.client.placed == []


def test_strategy_budget_is_enforced_from_the_database(settings):
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    with db.session_scope() as s:
        for i in range(12):
            s.add(m.LiveOrder(
                market_ticker=f"KXOTHER-{i}", event_ticker="KXOTHER", strategy=TAG, side="no",
                action="buy", limit_price=99, quantity=5, status="resting",
                client_order_id=f"c-{i}", created_at=datetime.now(timezone.utc)))
    with db.session_scope() as s:
        assert repo.live_strategy_exposure(s, TAG) > limm.MAX_STRATEGY_EXPOSURE_USD
    assert _place(ex, settings) in ("gate:strategy_exposure", "gate:open_cap")
    assert ex.client.placed == []


def test_open_market_cap_is_this_strategys_own(settings):
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
    """The shared, strategy-agnostic dedup gate is UNCHANGED by two-sided quoting."""
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


def test_never_stacks_a_second_pair_on_its_own_market(settings):
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    assert _place(ex, settings) == "placed"
    assert _place(ex, settings) == "gate:dedup"
    assert len(ex.client.placed) == 2


def test_the_pair_is_counted_against_the_shared_market_exposure_cap(settings):
    _db(settings)
    _live_settings(settings)
    settings.max_market_exposure = 5.0          # below one pair's collateral
    ex = _exec(settings)
    assert _place(ex, settings) == "gate:exposure"
    assert ex.client.placed == []


def test_shared_daily_loss_stop_blocks_entry(settings):
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    ex._daily_loss_tripped = True
    assert _place(ex, settings) == "gate:daily_loss"
    assert ex.client.placed == []


# ------------------------------------------------------------------ the wire format


def test_places_both_legs_post_only_with_the_right_yes_side_prices(settings):
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    pair = _pair(yes=40, no=55)
    assert _place(ex, settings, pair=pair) == "placed"
    yes_order, no_order = ex.client.placed
    assert (yes_order["side"], yes_order["price"]) == ("bid", "0.4000")    # buying YES
    assert (no_order["side"], no_order["price"]) == ("ask", "0.4500")      # NO 55c == YES ask 45c
    for o in (yes_order, no_order):
        assert o["count"] == f"{pair.quantity:.2f}"                        # decimal STRINGS
        assert o["post_only"] is True and o["time_in_force"] == "good_till_canceled"
    with db.session_scope() as s:
        rows = s.scalars(select(m.LiveOrder).order_by(m.LiveOrder.id)).all()
        assert [(r.side, r.action, r.limit_price, r.quantity, r.status) for r in rows] == [
            ("yes", "buy", 40, pair.quantity, "resting"),
            ("no", "buy", 55, pair.quantity, "resting")]


def test_a_failed_second_leg_leaves_the_first_resting_and_says_so(settings):
    from kalshi_bot.kalshi.errors import KalshiAPIError

    class SecondFails(FakeLiveClient):
        def create_events_order(self, order):
            self.placed.append(order)
            if len(self.placed) == 2:
                raise KalshiAPIError(400, "post only cross", "/portfolio/events/orders")
            return {"order": {"order_id": "K-1", "status": "resting"}}

    _db(settings)
    _live_settings(settings)
    ex = _exec(settings, client=SecondFails())
    with db.session_scope() as s:
        outcome, legs = ex.mirror_incentive_pair(
            s, strategy=TAG, event_ticker="KXTEST", ticker="KXTEST-A", pair=_pair(),
            account_state={"cash_balance": 500.0})
    assert outcome == "partial:rejected"
    assert [leg.side for leg in legs] == [limm.SIDE_YES]


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


def test_the_whole_book_cannot_exceed_its_budget(settings):
    """End to end: place until the strategy's own caps stop it, and prove the total committed is
    bounded by the registered budget — the property the operator is actually relying on."""
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    codes = [_place(ex, settings, ticker=f"KXTEST-{i}") for i in range(10)]
    assert codes.count("placed") == limm.MAX_OPEN_ORDERS
    assert all(c == "gate:open_cap" for c in codes[limm.MAX_OPEN_ORDERS:])
    with db.session_scope() as s:
        spent = repo.live_strategy_exposure(s, TAG)
        n = s.scalar(select(func.count()).select_from(m.LiveOrder))
    assert n == 2 * limm.MAX_OPEN_ORDERS
    assert spent <= limm.MAX_STRATEGY_EXPOSURE_USD
    assert spent <= limm.MAX_OPEN_ORDERS * 2 * limm.MAX_ORDER_DOLLARS


# ------------------------------------------------------------------ the book's own exits


def _close(ex, *, held_side, mark, qty=10, rule=limm.EXIT_STOP_LOSS, strategy=TAG):
    with db.session_scope() as s:
        return ex.close_incentive_position(
            s, strategy=strategy, ticker="KXTEST-A", held_side=held_side, qty=qty,
            mark_bid_cents=mark, rule=rule)


def test_closing_a_yes_leg_sells_yes_marketably(settings):
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    assert _close(ex, held_side=limm.SIDE_YES, mark=30) == "placed"
    order = ex.client.placed[0]
    sell_at = 30 - limm.EXIT_SLIPPAGE_CENTS
    assert order["side"] == "ask"                                   # sell YES
    assert order["price"] == f"{sell_at / 100:.4f}"
    assert order["time_in_force"] == "immediate_or_cancel"
    # The mmsell closeout's recorded-201 field set: no post_only (contradicts IOC), no
    # reduce_only (400 invalid_parameters on these contracts).
    assert "post_only" not in order and "reduce_only" not in order
    assert order["self_trade_prevention_type"] == "taker_at_cross"
    with db.session_scope() as s:
        row = s.scalars(select(m.LiveOrder)).one()
        assert (row.side, row.action, row.limit_price, row.quantity) == ("yes", "sell", sell_at, 10)
        assert row.client_order_id.startswith("limmexit:stop_loss:")
        assert row.status == "submitted"


def test_closing_a_no_leg_buys_yes_like_the_mmsell_closeout(settings):
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    assert _close(ex, held_side=limm.SIDE_NO, mark=60) == "placed"
    order = ex.client.placed[0]
    sell_at = 60 - limm.EXIT_SLIPPAGE_CENTS
    assert order["side"] == "bid"                                   # buy YES flattens NO
    assert order["price"] == f"{(100 - sell_at) / 100:.4f}"


def test_an_exit_is_not_blocked_by_the_loss_breaker(settings):
    """A loss stop that also blocked the stop-loss would be the wrong way round."""
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    ex._daily_loss_tripped = True
    assert _close(ex, held_side=limm.SIDE_YES, mark=30) == "placed"


def test_an_exit_still_respects_the_switches(settings):
    _db(settings)
    _live_settings(settings)
    settings.live_enabled = False
    ex = _exec(settings)
    assert _close(ex, held_side=limm.SIDE_YES, mark=30) == "gate:switches"
    assert ex.client.placed == []


def test_the_exit_leg_rests_post_only_on_the_opposite_side(settings):
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    with db.session_scope() as s:
        assert ex.place_incentive_exit_leg(s, strategy=TAG, ticker="KXTEST-A",
                                           held_side=limm.SIDE_YES, qty=10, price=55) == "placed"
    order = ex.client.placed[0]
    assert (order["side"], order["price"], order["post_only"]) == ("ask", "0.4500", True)
    with db.session_scope() as s:
        row = s.scalars(select(m.LiveOrder)).one()
        assert (row.side, row.action, row.limit_price) == ("no", "buy", 55)
        assert row.client_order_id.startswith("limmexitleg:")


def test_the_exit_leg_never_joins_a_market_with_something_working(settings):
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    with db.session_scope() as s:
        s.add(m.LiveOrder(market_ticker="KXTEST-A", strategy="Fmmsell10", side="no",
                          action="buy", limit_price=92, quantity=1, status="resting",
                          client_order_id="x", created_at=datetime.now(timezone.utc)))
        s.flush()
        assert ex.place_incentive_exit_leg(s, strategy=TAG, ticker="KXTEST-A",
                                           held_side=limm.SIDE_YES, qty=1,
                                           price=55) == "gate:dedup"
    assert ex.client.placed == []


def _working(s, *, strategy=TAG, koid="K-9", status="resting", coid="c"):
    s.add(m.LiveOrder(market_ticker="KXTEST-A", strategy=strategy, side="no", action="buy",
                      limit_price=55, quantity=10, status=status, kalshi_order_id=koid,
                      client_order_id=coid, created_at=datetime.now(timezone.utc)))
    s.flush()


def test_cancel_takes_only_this_books_working_orders(settings):
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    with db.session_scope() as s:
        _working(s, koid="K-OURS", coid="a")
        _working(s, strategy="Fmmsell10", koid="K-THEIRS", coid="b")
        assert ex.cancel_incentive_orders(s, strategy=TAG, ticker="KXTEST-A") is True
        statuses = {r.kalshi_order_id: r.status for r in s.scalars(select(m.LiveOrder))}
    assert ex.client.canceled == ["K-OURS"]
    assert statuses == {"K-OURS": "canceled", "K-THEIRS": "resting"}


def test_cancel_reports_failure_so_no_exit_follows(settings):
    """An exit sent while a leg may still rest would let that leg reopen the position."""
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings, client=FakeLiveClient(cancel_fail=RuntimeError("404")))
    with db.session_scope() as s:
        _working(s)
        assert ex.cancel_incentive_orders(s, strategy=TAG, ticker="KXTEST-A") is False
        assert s.scalars(select(m.LiveOrder)).one().status == "resting"


def test_cancel_refuses_to_vouch_for_an_order_with_no_exchange_id(settings):
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    with db.session_scope() as s:
        _working(s, koid=None, status="pending")
        assert ex.cancel_incentive_orders(s, strategy=TAG, ticker="KXTEST-A") is False


def test_exit_attempts_count_only_marketable_exits(settings):
    _db(settings)
    _live_settings(settings)
    ex = _exec(settings)
    with db.session_scope() as s:
        _working(s, coid="limmexit:stop_loss:1", status="canceled")
        _working(s, coid="limmexitleg:2", status="resting")
        _working(s, coid="plain", status="filled")
        assert ex.incentive_exit_attempts(s, strategy=TAG, ticker="KXTEST-A") == 1


# ------------------------------------------------------------------ the process-wide exits


def test_the_tp_sl_exit_path_skips_this_book(settings, monkeypatch):
    """This book runs its OWN exit rules from its runner. The process-wide TP/SL (production
    runs LIVE_EXIT_MODE=tp_sl for the YES/weather books) must never also fire on it: two rule
    sets on one real-money position, and one of them can only close YES."""
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
    monkeypatch.setattr(LiveExecutor, "_remaining_open_qty",
                        lambda self, s, t, q: (seen.append(t) or 1.0))
    with db.session_scope() as s:
        ex.manage_exits(s)
    # The weather book is still managed; neither incentive tag is ever looked at.
    assert seen == ["KXB-1"]


# ------------------------------------------------------------------ the twin must survive a restart


def test_a_configured_twin_is_never_abandoned_as_foreign(settings):
    """The defect observed in production on 2026-09-18.

    `abandon_open_paper_trades` runs on every live worker start and wipes open paper positions
    whose strategy is not in `keep_prefixes`. In live mode those prefixes are family names
    (`weather`, `mmsell`, ...). A twin tag carries its parent's generation letter — `Alimm1_pt3`
    — so it matches NONE of them, and the incentive canary's twin had the mirrors of two FILLED
    live positions marked `abandoned` while the live book still held them. mmsell's twins
    survive only through the `"mmsell"` substring special case, which is an accident of that
    family's naming, so every other book's twin was exposed."""
    settings.live_paper_twin_enabled = True
    settings.live_strategies = f"Fmmsell10,{limm.LIVE_TAG}"
    settings.live_paper_twins = f"{limm.LIVE_TAG}:{limm.TWIN_TAG}"
    base = ("weather", "mmsell")

    # Without the fix the twin tag is foreign to every kept family.
    assert not repo.strategy_is_kept(limm.TWIN_TAG, base)

    kept = repo.keep_with_configured_twins(base, settings)
    assert repo.strategy_is_kept(limm.TWIN_TAG, kept)
    # The families it was given are untouched, and a genuinely foreign tag is still foreign.
    assert repo.strategy_is_kept("weather_h14", kept)
    assert repo.strategy_is_kept("Fmmsell10_pt4", kept)
    assert not repo.strategy_is_kept("someone_elses_book", kept)


def test_keep_with_configured_twins_is_idempotent_and_drops_nothing(settings):
    settings.live_paper_twin_enabled = True
    settings.live_strategies = limm.LIVE_TAG
    settings.live_paper_twins = f"{limm.LIVE_TAG}:{limm.TWIN_TAG}"
    base = ("weather", "mmsell")
    once = repo.keep_with_configured_twins(base, settings)
    assert repo.keep_with_configured_twins(once, settings) == once
    assert all(p in once for p in base), "a configured family must never be dropped"


def test_the_live_book_tag_itself_is_not_a_paper_family(settings):
    """`Alimm1` places REAL orders and writes no paper_trades, so it must not be added to the
    paper keep list by this helper — only the twin belongs there."""
    settings.live_paper_twin_enabled = True
    settings.live_strategies = limm.LIVE_TAG
    settings.live_paper_twins = f"{limm.LIVE_TAG}:{limm.TWIN_TAG}"
    kept = repo.keep_with_configured_twins(("weather",), settings)
    assert limm.TWIN_TAG in kept
    assert limm.LIVE_TAG not in kept


# ------------------------------------------ the open-order cap under-count (thesis §9.15)


def test_a_resting_order_counts_even_with_a_zero_quantity_snapshot(settings):
    _db(settings)
    NOW = datetime.now(timezone.utc)
    """The 2026-09-18 production case.

    `KXRT-RES-93` rested unfilled while a position snapshot for that market read quantity 0.
    Consulting the snapshot first cannot tell "position closed" from "order not filled yet", so
    the resting order vanished from the cap and the book ran four commitments against a cap of
    three."""
    with db.session_scope() as s:
        s.add(m.LiveOrder(market_ticker="KXRT-RES-93", event_ticker="KXRT-RES",
                          strategy="Alimm1", side="no", action="buy", limit_price=3,
                          quantity=1, status="resting", created_at=NOW))
        s.add(m.Position(market_ticker="KXRT-RES-93", captured_at=NOW, side="no", quantity=0,
                         avg_price=3.0, market_exposure=0.0003))
        s.flush()
        assert repo.count_live_book_open(s, "Alimm1") == 1


def test_a_filled_order_that_settled_flat_does_not_count(settings):
    _db(settings)
    NOW = datetime.now(timezone.utc)
    with db.session_scope() as s:
        s.add(m.LiveOrder(market_ticker="KXUSLEI-26SEP18-T0.2", event_ticker="KXUSLEI-26SEP18",
                          strategy="Alimm1", side="yes", action="buy", limit_price=5,
                          quantity=1, status="filled", created_at=NOW))
        s.add(m.Position(market_ticker="KXUSLEI-26SEP18-T0.2", captured_at=NOW, side="no",
                         quantity=0, market_exposure=0.0, realized_pnl=-0.05))
        s.flush()
        assert repo.count_live_book_open(s, "Alimm1") == 0


def test_a_canceled_order_never_counts(settings):
    _db(settings)
    NOW = datetime.now(timezone.utc)
    with db.session_scope() as s:
        s.add(m.LiveOrder(market_ticker="KXGONE-1", strategy="Alimm1", side="yes", action="buy",
                          limit_price=1, quantity=1, status="canceled", created_at=NOW))
        s.flush()
        assert repo.count_live_book_open(s, "Alimm1") == 0


def test_open_events_share_the_definition_of_open(settings):
    _db(settings)
    NOW = datetime.now(timezone.utc)
    with db.session_scope() as s:
        s.add(m.LiveOrder(market_ticker="KXRT-RES-93", event_ticker="KXRT-RES",
                          strategy="Alimm1", side="no", action="buy", limit_price=3,
                          quantity=1, status="resting", created_at=NOW))
        s.add(m.Position(market_ticker="KXRT-RES-93", captured_at=NOW, side="no", quantity=0,
                         avg_price=3.0, market_exposure=0.0003))
        s.add(m.LiveOrder(market_ticker="KXGONE-1", event_ticker="KXGONE", strategy="Alimm1",
                          side="yes", action="buy", limit_price=1, quantity=1,
                          status="canceled", created_at=NOW))
        s.flush()
        assert repo.live_book_open_events(s, "Alimm1") == {"KXRT-RES"}
