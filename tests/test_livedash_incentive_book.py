"""The liquidity-incentive section of the landing page (`livedash.incentive_book`).

What must hold: all-time realized P&L covers every market the book ever traded (not just this
month); reward credits come only from trustworthy, non-transfer positive residuals; each resting
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
