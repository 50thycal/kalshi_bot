"""The S2 live test's order path (docs/LIMM_S2_LIVE_TEST.md): `LiveExecutor.place_s2_bid` and
`S2LiveRunner`. Every test is a safety property: inert by default, this book's own caps, one
market per event, no re-entry after a fill, the stops, and no change to any other book."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot import repository as repo
from kalshi_bot.liquidity_incentive import live as limm
from kalshi_bot.liquidity_incentive import s2_live as s2
from kalshi_bot.liquidity_incentive import s2_runner as sr
from kalshi_bot.live import executor as exmod
from kalshi_bot.live.executor import LiveExecutor
from kalshi_bot.risk.manager import RiskManager

NOW = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self, books=None):
        self.placed: list[dict] = []
        self.canceled: list[str] = []
        self.books = books or {}

    def create_events_order(self, order):
        self.placed.append(order)
        return {"order": {"order_id": f"K-{len(self.placed)}", "status": "resting"}}

    def cancel_events_order(self, order_id, *, exchange_index=None):
        self.canceled.append(order_id)
        return {}

    def get_market(self, ticker):
        return {"market": {"ticker": ticker}}

    def get_orderbook(self, ticker, depth=None):
        return self.books.get(ticker, {"orderbook": {"yes": [[90, 500]], "no": [[4, 800]]}})


def _live(settings, *, s2_on=True, tags=s2.LIVE_TAG):
    settings.bot_mode = "live"
    settings.kill_switch = False
    settings.live_enabled = True
    settings.live_strategies = tags
    settings.max_market_exposure = 25.0
    settings.max_total_exposure = 500.0
    settings.max_daily_loss = 50.0
    settings.live_kill_on_daily_loss = True
    settings.liquidity_incentive_s2_live_enabled = s2_on
    db.init_engine(settings.database_url)
    db.create_all()
    return settings


def _exec(settings, client=None):
    return LiveExecutor(client or FakeClient(), settings, RiskManager(settings))


def _bid(ex, ticker="KXAAAGASM-26NOV30-A", side="no", price=4, qty=250, balance=500.0):
    with db.session_scope() as s:
        return ex.place_s2_bid(s, ticker=ticker, event_ticker=ticker.rsplit("-", 1)[0],
                               side=side, price=price, qty=qty,
                               account_state={"cash_balance": balance})


# ------------------------------------------------------------------ executor: inert + caps


def test_the_tag_helper_names_exactly_two_books():
    for t in (limm.LIVE_TAG, limm.TWIN_TAG, s2.LIVE_TAG, s2.TWIN_TAG):
        assert exmod._incentive_managed(t)
    for t in (None, "", "As2best", "As2best1x", "Alimm", "Fmmsell10", s2.PAPER_TAG):
        assert not exmod._incentive_managed(t)


@pytest.mark.parametrize("attr,value", [("liquidity_incentive_s2_live_enabled", False),
                                        ("live_strategies", "Alimm1"), ("kill_switch", True),
                                        ("live_enabled", False), ("bot_mode", "paper")])
def test_inert_unless_every_switch_is_on(settings, attr, value):
    _live(settings)
    setattr(settings, attr, value)
    ex = _exec(settings)
    assert _bid(ex) == "gate:switches"
    assert ex.client.placed == []


def test_places_a_post_only_ten_dollar_bid_with_its_own_prefix(settings):
    _live(settings)
    ex = _exec(settings)
    assert _bid(ex) == "placed"
    (o,) = ex.client.placed
    assert o["side"] == "ask" and o["price"] == "0.9600" and o["count"] == "250.00"
    assert o["post_only"] is True and o["client_order_id"].startswith(s2.COID_PREFIX)
    with db.session_scope() as s:
        row = s.scalars(select(m.LiveOrder)).one()
        assert (row.strategy, row.side, row.limit_price, row.status) == (
            s2.LIVE_TAG, "no", 4, "resting")


@pytest.mark.parametrize("price,qty", [(11, 90), (4, 251), (1, 1001), (0, 10)])
def test_refuses_anything_above_ten_dollars_or_off_the_cheap_side(settings, price, qty):
    _live(settings)
    ex = _exec(settings)
    assert _bid(ex, price=price, qty=qty) == "gate:size"
    assert ex.client.placed == []


def test_five_markets_and_fifty_dollars_at_most(settings):
    _live(settings)
    ex = _exec(settings)
    for i in range(5):
        assert _bid(ex, ticker=f"KXE{i}-26NOV-A") == "placed"
    assert _bid(ex, ticker="KXE9-26NOV-A") in ("gate:open_cap", "gate:strategy_exposure")
    assert len(ex.client.placed) == 5


def test_never_contests_another_books_market(settings):
    _live(settings, tags=f"{s2.LIVE_TAG},Alimm1")
    ex = _exec(settings)
    with db.session_scope() as s:
        repo.create_live_order(s, signal_id=None, ticker="KXAAAGASM-26NOV30-A",
                               event_ticker="KXAAAGASM-26NOV30", strategy="Alimm1", side="no",
                               action="buy", limit_price=4, quantity=10, status="resting",
                               client_order_id="x", raw_order_json={})
    assert _bid(ex) == "gate:dedup"
    assert ex.client.placed == []


def test_missing_balance_fails_closed(settings):
    _live(settings)
    ex = _exec(settings)
    with db.session_scope() as s:
        assert ex.place_s2_bid(s, ticker="KXA-B-C", event_ticker="KXA-B", side="no", price=4,
                               qty=250, account_state={}) == "gate:no_balance"
    assert ex.client.placed == []


# ------------------------------------------------------------------ the runner


def _seed_paper(session, specs, now=NOW):
    """specs: (ticker, event, side, price, rate). One programme + one open S2-best row each."""
    for i, (ticker, event, side, price, rate) in enumerate(specs):
        p = m.IncentiveProgram(
            program_id=f"p{i}", market_ticker=ticker, event_ticker=event,
            series_ticker=ticker.split("-", 1)[0], incentive_type="liquidity",
            start_date=now - timedelta(days=1), end_date=now + timedelta(days=10),
            close_time=now + timedelta(days=20), period_reward_usd=100.0, target_size=100.0,
            discount_factor_bps=5000, terms_hash=f"h{i}", first_seen_at=now, last_seen_at=now)
        session.add(p)
        session.flush()
        qty = 1000 // price
        session.add(m.IncentiveShadowQuote(
            program_row_id=p.id, market_ticker=ticker, policy=s2.SOURCE_ARM,
            capital_tier_usd=10, placed_at=now - timedelta(minutes=5),
            yes_bid=price if side == "yes" else 0, no_bid=price if side == "no" else 0,
            yes_bid_yes_scale=0, no_bid_yes_scale=100, qty_per_side=qty, pair_cost_cents=price,
            reason=f"{s2.SOURCE_ARM}:{side}@{price}x{qty}",
            book_json={"arm": {"event": event, "series": ticker.split("-", 1)[0]}},
            est_reward_per_hour_usd=rate))
    session.flush()


SPECS = [(f"KXS{e}-26NOV-{k}", f"KXS{e}-26NOV", "no", 4, 0.05 - 0.005 * e + 0.001 * k)
         for e in range(7) for k in range(2)]


def _cycle(runner, ex, now=NOW):
    with db.session_scope() as s:
        return runner.cycle(s, ex, {"cash_balance": 500.0}, now=now)


def test_runner_is_inert_when_the_switch_is_off(settings):
    _live(settings, s2_on=False)
    ex = _exec(settings)
    with db.session_scope() as s:
        _seed_paper(s, SPECS)
    assert _cycle(sr.S2LiveRunner(ex.client, settings), ex) == {"armed": False}
    assert ex.client.placed == []


def test_runner_places_top_five_one_per_event_and_does_not_repeat(settings):
    _live(settings)
    ex = _exec(settings)
    runner = sr.S2LiveRunner(ex.client, settings)
    with db.session_scope() as s:
        _seed_paper(s, SPECS)
    out = _cycle(runner, ex)
    assert out["placed"] == 5
    tickers = [o["ticker"] for o in ex.client.placed]
    assert len({t.rsplit("-", 1)[0] for t in tickers}) == 5
    assert all(t.endswith("-1") for t in tickers)          # best market of each event
    assert {t.rsplit("-", 1)[0] for t in tickers} == {f"KXS{e}-26NOV" for e in range(5)}
    # Second cycle: nothing new, nothing canceled.
    out2 = _cycle(runner, ex, now=NOW + timedelta(minutes=3))
    assert out2["placed"] == 0 and out2["canceled"] == 0 and len(ex.client.placed) == 5
    assert out["estimates"] == 5
    with db.session_scope() as s:
        ev = s.scalars(select(m.SystemEvent).where(
            m.SystemEvent.component == sr.ESTIMATE_COMPONENT)).all()
        assert len(ev) == 5 and all(e.raw_json["est_reward_per_hour_usd"] >= 0 for e in ev)


def test_a_market_the_paper_arm_leaves_is_canceled(settings):
    _live(settings)
    ex = _exec(settings)
    runner = sr.S2LiveRunner(ex.client, settings)
    with db.session_scope() as s:
        _seed_paper(s, SPECS)
    _cycle(runner, ex)
    gone = ex.client.placed[0]["ticker"]
    with db.session_scope() as s:
        q = s.scalars(select(m.IncentiveShadowQuote).where(
            m.IncentiveShadowQuote.market_ticker == gone)).one()
        q.ended_at = NOW
    out = _cycle(runner, ex, now=NOW + timedelta(minutes=3))
    assert out["canceled"] == 1 and out["placed"] == 1
    with db.session_scope() as s:
        row = s.scalars(select(m.LiveOrder).where(m.LiveOrder.market_ticker == gone)).one()
        assert (row.status, row.cancel_reason) == ("canceled", sr.CANCEL_DESELECTED)


def _fill(session, ticker, price=4, qty=250):
    row = session.scalars(select(m.LiveOrder).where(m.LiveOrder.market_ticker == ticker)).one()
    session.add(m.Fill(kalshi_fill_id=f"f-{ticker}", kalshi_order_id=row.kalshi_order_id,
                       market_ticker=ticker, side="no", action="buy", price=price,
                       quantity=qty, fee=0))
    row.status = "filled"
    session.flush()


def test_a_filled_market_is_held_and_never_reentered(settings):
    _live(settings)
    ex = _exec(settings)
    runner = sr.S2LiveRunner(ex.client, settings)
    with db.session_scope() as s:
        _seed_paper(s, SPECS)
    _cycle(runner, ex)
    hit = ex.client.placed[0]["ticker"]
    with db.session_scope() as s:
        _fill(s, hit)
    out = _cycle(runner, ex, now=NOW + timedelta(minutes=3))
    assert hit not in out["selected"] and out["skipped"].get(hit) == s2.SKIP_FILLED_BEFORE
    # The held fill keeps its market slot and its event until it settles: nothing replaces it.
    assert out["placed"] == 0 and len(ex.client.placed) == 5
    assert hit.rsplit("-", 1)[0] + "-0" not in out["selected"]
    assert out["fill_loss_usd"] == 0.0                # marked at 4c, bought at 4c


def test_the_loss_stop_cancels_everything_and_sticks(settings):
    _live(settings)
    crash = {"orderbook": {"yes": [[99, 500]], "no": []}}
    books = {f"KXS{e}-26NOV-1": crash for e in range(7)}
    client = FakeClient(books=books)
    ex = _exec(settings, client)
    runner = sr.S2LiveRunner(client, settings)
    with db.session_scope() as s:
        _seed_paper(s, SPECS)
    _cycle(runner, ex)
    for o in client.placed[:3]:                       # three $10 fills, now worth 0
        with db.session_scope() as s:
            _fill(s, o["ticker"])
    out = _cycle(runner, ex, now=NOW + timedelta(minutes=3))
    assert out["stopped"] == s2.STOP_LOSS and out["fill_loss_usd"] >= 30.0
    assert len(client.canceled) == 2                  # the two still resting
    out2 = _cycle(runner, ex, now=NOW + timedelta(minutes=6))
    assert out2["stopped"] is True and len(client.placed) == 5
    with db.session_scope() as s:
        assert sr.S2LiveRunner.stopped(s) is not None


def test_day_fourteen_stops_the_book(settings):
    _live(settings)
    ex = _exec(settings)
    runner = sr.S2LiveRunner(ex.client, settings)
    with db.session_scope() as s:
        _seed_paper(s, SPECS)
    _cycle(runner, ex)
    with db.session_scope() as s:
        for row in s.scalars(select(m.LiveOrder)):
            row.created_at = NOW - timedelta(days=14)
    out = _cycle(runner, ex, now=NOW)
    assert out["stopped"] == s2.STOP_DURATION


def test_alimm1_keeps_its_own_caps_and_tag_rules():
    """The S2 book adds its tag to the executor's incentive handling and changes nothing for
    Alimm1: Alimm1's tag test and constants are untouched."""
    assert limm.owns_tag(limm.LIVE_TAG) and not limm.owns_tag(s2.LIVE_TAG)
    assert (limm.MAX_OPEN_ORDERS, limm.MAX_STRATEGY_EXPOSURE_USD) == (10, 100.00)


def test_an_hourly_paper_refresh_does_not_churn_a_live_bid(settings):
    _live(settings)
    ex = _exec(settings)
    runner = sr.S2LiveRunner(ex.client, settings)
    with db.session_scope() as s:
        _seed_paper(s, SPECS)
    _cycle(runner, ex)
    t = ex.client.placed[0]["ticker"]
    with db.session_scope() as s:   # the collector ended the row; its replacement is not in yet
        q = s.scalars(select(m.IncentiveShadowQuote).where(
            m.IncentiveShadowQuote.market_ticker == t)).one()
        q.ended_at, q.end_reason = NOW + timedelta(minutes=1), sr.REFRESH_END_REASON
    out = _cycle(runner, ex, now=NOW + timedelta(minutes=3))
    assert out["canceled"] == 0 and out["placed"] == 0 and t in out["selected"]
    # but a row ended for any other reason is acted on at once
    with db.session_scope() as s:
        q = s.scalars(select(m.IncentiveShadowQuote).where(
            m.IncentiveShadowQuote.market_ticker == t)).one()
        q.end_reason = "arm_deselected"
    out = _cycle(runner, ex, now=NOW + timedelta(minutes=6))
    assert out["canceled"] == 1


def test_the_refresh_reason_is_the_collectors():
    from kalshi_bot.liquidity_incentive import collector

    assert sr.REFRESH_END_REASON == collector.END_REFRESH
