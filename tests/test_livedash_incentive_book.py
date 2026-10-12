"""The liquidity-incentive section of the landing page (`livedash.incentive_book`).

What must hold: all-time realized P&L covers every market the book ever traded (not just this
month); reward credits are only payouts listed on Kalshi's Rewards page (§9.51); each resting
order carries its market, size, committed dollars and an estimated reward from the scoring rules;
and no other book gets the section.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kalshi_bot import models as m
from kalshi_bot.liquidity_incentive import live as limm
from kalshi_bot.livedash import incentive_book as ib
from kalshi_bot.livedash import overview as ov
from kalshi_bot.models import Base

NOW = datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc)
TICKER = "KXFEAR-26OCT09-GREE"


@pytest.fixture
def session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    yield s
    s.close()


def _setup(s):
    # Old, settled market from last month: counts all time, not this month.
    s.add(m.LiveOrder(kalshi_order_id="o-old", client_order_id="c-old", market_ticker="KXOLD-1",
                      strategy=limm.LIVE_TAG, created_at=NOW - timedelta(days=20), side="no",
                      action="buy", limit_price=5, quantity=200, status="filled"))
    s.add(m.Position(market_ticker="KXOLD-1", captured_at=NOW - timedelta(days=19), side="no",
                     quantity=0, quantity_fp=0, realized_pnl=-10.0, market_exposure=0))
    # The resting cheap-side bid.
    s.add(m.LiveOrder(kalshi_order_id="o-fear", client_order_id="c-fear", market_ticker=TICKER,
                      strategy=limm.LIVE_TAG, created_at=NOW - timedelta(hours=1), side="yes",
                      action="buy", limit_price=2, quantity=500, status="resting"))
    s.add(m.IncentiveProgram(
        program_id="p-fear", market_ticker=TICKER, market_title="Fear & Greed Index on Oct 9, 2026?",
        incentive_type="liquidity", start_date=NOW - timedelta(days=1),
        end_date=NOW + timedelta(days=9), period_reward_usd=100.0, target_size=1000.0,
        discount_factor_bps=5000, terms_hash="h", first_seen_at=NOW - timedelta(days=1),
        last_seen_at=NOW))
    # YES book with our 500 in it at 2c: reference 2c (1000 >= 200), field 2000.
    s.add(m.IncentiveMarketSnapshot(
        market_ticker=TICKER, at=NOW - timedelta(minutes=5), book_valid=True,
        yes_levels_json=[[2, 1000.0], [1, 2000.0]], no_levels_json=[[96, 3000.0]],
        est_yes_score_total=2000.0, est_no_score_total=2700.0,
        est_yes_meets_target=True, est_no_meets_target=True))
    # Ledger: a reward credit, a flagged reading, a deposit and an unexplained fee.
    for cents, transfer, notes in ((221, False, None), (500, False, {"residual_untrustworthy": True}),
                                   (10_000, True, None), (-40, False, None)):
        s.add(m.IncentiveBalanceObservation(at=NOW - timedelta(days=5), balance_cents=1,
                                            residual_cents=cents, presumed_transfer=transfer,
                                            notes_json=notes))
    s.flush()


def test_all_time_rewards_net_and_resting_orders(session):
    _setup(session)
    x = ib.build_incentive_block(session, limm.LIVE_TAG, NOW, ov._latest_snapshots)
    assert x["realized_all_time_usd"] == -10.0 and x["settled_markets_all_time"] == 1
    assert x["reward_credits_usd"] == 2.21           # flagged + transfer excluded
    assert x["unexplained_cash_out_usd"] == -0.40
    assert x["net_all_time_usd"] == -7.79
    (r,) = x["resting"]
    assert (r["title"], r["side"], r["price_cents"], r["quantity"]) == (
        "Fear & Greed Index on Oct 9, 2026?", "yes", 2, 500.0)
    assert r["committed_usd"] == 10.0 and r["max_payout_usd"] == 500.0
    # $100 over 10 days = $10/day; our 500 of a 2000 field on one side = 25% / 2.
    assert r["est_reward_per_day_usd"] == pytest.approx(1.25, abs=1e-3)
    assert r["est_reward_to_end_usd"] == pytest.approx(1.25 * 9, abs=0.02)
    assert x["est_reward_per_day_usd"] == pytest.approx(1.25, abs=1e-3)
    assert (x["slots_used"], x["slots_max"], x["committed_usd"]) == (1, limm.MAX_OPEN_ORDERS, 10.0)


def test_a_book_that_does_not_qualify_estimates_zero_and_a_stale_book_unknown(session):
    _setup(session)
    snap = session.query(m.IncentiveMarketSnapshot).one()
    snap.est_no_meets_target = False
    session.flush()
    x = ib.build_incentive_block(session, limm.LIVE_TAG, NOW, ov._latest_snapshots)
    assert x["resting"][0]["est_reward_per_day_usd"] == 0.0
    snap.est_no_meets_target = True
    snap.at = NOW - timedelta(hours=5)
    session.flush()
    x = ib.build_incentive_block(session, limm.LIVE_TAG, NOW, ov._latest_snapshots)
    assert x["resting"][0]["est_reward_per_day_usd"] is None
    assert x["est_reward_per_day_usd"] is None


def test_only_the_incentive_book_gets_the_section(session):
    _setup(session)
    session.add(m.LiveOrder(kalshi_order_id="o-mm", client_order_id="c-mm", market_ticker="KXMM-1",
                            strategy="Fmmsell10", created_at=NOW - timedelta(hours=1), side="no",
                            action="buy", limit_price=93, quantity=1, status="resting"))
    session.flush()
    d = ov.build_overview(session, now=NOW)
    books = {b["live_tag"]: b for b in d["headline"]["books"]}
    assert books[limm.LIVE_TAG]["incentive"]["reward_credits_usd"] == 2.21
    assert books["Fmmsell10"]["incentive"] is None


def test_an_attributable_credit_is_unconfirmed_until_kalshi_lists_it(session):
    # §9.49 found the credits behind the old $1 "transfer" line; §9.51: that test also counted
    # $2.75 Kalshi never paid. An attributable credit is reported as unconfirmed and left out of
    # rewards and net; a whole-dollar one (a deposit) is neither.
    _setup(session)
    session.add(m.IncentiveProgram(
        program_id="p-old", market_ticker="KXOLD-1", incentive_type="liquidity",
        start_date=NOW - timedelta(days=25), end_date=NOW - timedelta(days=2, minutes=17),
        period_reward_usd=100.0, target_size=1000.0, terms_hash="h2",
        first_seen_at=NOW - timedelta(days=25), last_seen_at=NOW - timedelta(days=2)))
    for cents in (275, 300):
        session.add(m.IncentiveBalanceObservation(at=NOW - timedelta(days=2), balance_cents=1,
                                                  residual_cents=cents, presumed_transfer=True))
    session.flush()
    x = ib.build_incentive_block(session, limm.LIVE_TAG, NOW, ov._latest_snapshots)
    assert x["reward_credits_usd"] == 2.21                   # only the Rewards-page payout
    assert x["reward_unconfirmed_usd"] == 2.75               # the $3.00 deposit is neither
    assert x["net_all_time_usd"] == -7.79


def test_confirmed_reward_matches_the_rewards_page():
    from kalshi_bot.liquidity_incentive.reward_ledger import confirmed_reward
    oct10 = datetime(2026, 10, 10, 10, 26, tzinfo=timezone.utc)
    assert confirmed_reward(209, oct10)
    assert confirmed_reward(209, oct10 + timedelta(hours=20))              # local-date slack
    assert not confirmed_reward(275, datetime(2026, 10, 6, 21, 32, tzinfo=timezone.utc))
    assert not confirmed_reward(208, oct10)                                # cents must match
    assert not confirmed_reward(209, oct10 + timedelta(days=3))            # wrong day
    oct11 = datetime(2026, 10, 11, 10, 38, tzinfo=timezone.utc)
    assert confirmed_reward(330, oct11)            # $1.99 + $1.31 paid as one balance step
    assert confirmed_reward(131, oct11)
    assert not confirmed_reward(330, oct11 + timedelta(days=3))
    assert not confirmed_reward(None, oct10)


def test_attributable_reward_rules():
    from kalshi_bot.liquidity_incentive.reward_ledger import attributable_reward
    end = NOW - timedelta(hours=1)
    assert attributable_reward(275, NOW, [end])
    assert not attributable_reward(300, NOW, [end])                       # whole dollars
    assert not attributable_reward(4999, NOW, [end])                      # too large
    assert not attributable_reward(275, NOW, [NOW + timedelta(hours=1)])  # before the end
    assert not attributable_reward(275, NOW, [NOW - timedelta(hours=30)]) # too long after
    assert not attributable_reward(75, NOW, [end])                        # not a transfer at all
