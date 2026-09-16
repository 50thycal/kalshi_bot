"""Quote policies and fill models for the liquidity-incentive shadow MM (pure functions)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot.liquidity_incentive import fills as f
from kalshi_bot.liquidity_incentive import quotes as q

T0 = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def _view(yb=46, nb=52, **kw):
    return q.BookView(best_yes_bid=yb, best_no_bid=nb, **kw)


# ---------------------------------------------------------------- quote policies


def test_break_even_joins_best_when_pair_under_100():
    qp = q.build_quote(q.POLICY_BREAK_EVEN, _view(46, 52))
    assert (qp.yes_bid, qp.no_bid, qp.pair_cost_cents) == (46, 52, 98)
    assert qp.pair_edge_cents == 2.0 and qp.yes_joins_best and qp.no_joins_best
    assert qp.reason == "join_best"


def test_break_even_steps_down_when_pair_over_100():
    qp = q.build_quote(q.POLICY_BREAK_EVEN, _view(50, 52))   # 102 -> must reach 100
    assert qp.pair_cost_cents <= 100 and qp.pair_edge_cents >= 0
    assert not (qp.yes_joins_best and qp.no_joins_best)
    assert qp.reason.startswith("stepped_down_")


def test_break_even_accounts_for_maker_fee():
    qp = q.build_quote(q.POLICY_BREAK_EVEN, _view(50, 50), maker_fee_cents_per_pair=1.0)
    assert qp.pair_cost_cents <= 99 and qp.pair_edge_cents >= 0


def test_reward_efficient_accepts_declared_one_cent_loss():
    qp = q.build_quote(q.POLICY_REWARD_EFFICIENT, _view(49, 52), max_pair_loss_cents=1.0)
    assert qp.pair_cost_cents == 101 and qp.pair_edge_cents == -1.0
    assert qp.yes_joins_best and qp.no_joins_best
    assert qp.reason == "join_best_within_loss_budget"


def test_reward_efficient_falls_back_beyond_loss_budget():
    qp = q.build_quote(q.POLICY_REWARD_EFFICIENT, _view(52, 52), max_pair_loss_cents=1.0)
    assert qp.pair_cost_cents <= 100 and qp.reason.startswith("loss_budget_exceeded")


def test_conservative_rests_behind_best_and_widens_with_activity():
    quiet = q.build_quote(q.POLICY_CONSERVATIVE, _view(46, 52))
    assert (quiet.yes_bid, quiet.no_bid) == (45, 51) and quiet.reason == "behind_1_ticks"
    busy = q.build_quote(q.POLICY_CONSERVATIVE, _view(46, 52, trades_last_5m=50, price_range_5m_cents=9))
    assert q.conservative_distance(_view(46, 52, trades_last_5m=50, price_range_5m_cents=9)) == 5
    assert (busy.yes_bid, busy.no_bid) == (41, 47)


def test_no_quote_without_two_sided_book():
    assert q.build_quote(q.POLICY_BREAK_EVEN, q.BookView(best_yes_bid=None, best_no_bid=52)) is None


def test_book_view_converts_local_book_once():
    from kalshi_bot.execution.book import LocalBook
    book = LocalBook("KXT")
    book.yes = {46: 10.0, 40: 5.0}
    book.no = {48: 7.0, 60: 3.0}       # yes-scale: best NO bid = 100 - 48 = 52
    view = q.book_view_from_local(book)
    assert (view.best_yes_bid, view.best_no_bid) == (46, 52)
    assert (view.yes_depth_at_best, view.no_depth_at_best) == (10.0, 7.0)
    assert view.spread_cents == 2


def test_quantity_for_capital_and_target_cap():
    assert q.quantity_for_capital(25, 98) == 25          # $25 / $0.98 -> 25 pairs
    assert q.quantity_for_capital(500, 98) == 510
    assert q.quantity_for_capital(500, 98, target_size=100) == 100
    assert q.capital_required_usd(46, 52, 25) == 24.5
    assert q.quantity_for_capital(25, 0) == 0


@pytest.mark.parametrize("policy", q.POLICIES)
def test_every_policy_prices_where_we_would_accept_a_fill(policy):
    """Guardrail: no policy ever produces a pair whose declared loss exceeds the budget, and
    no price outside 1..99."""
    for yb in (5, 30, 50, 70, 95):
        for nb in (5, 30, 50, 70, 95):
            qp = q.build_quote(policy, _view(yb, nb), max_pair_loss_cents=1.0)
            if qp is None:
                continue
            assert 1 <= qp.yes_bid <= 99 and 1 <= qp.no_bid <= 99
            assert qp.pair_edge_cents >= -1.0


# ---------------------------------------------------------------- fill models


def _leg(side="yes", price=46, qty=10.0, ahead=20.0):
    return f.ShadowLeg(side=side, price_yes_scale=price, quantity=qty, placed_at=T0,
                       queue_ahead_at_placement=ahead)


def test_yes_leg_is_hit_only_by_yes_sellers_at_or_below_our_price():
    leg = _leg("yes", 46)
    assert leg.trade_hits_us(46, "no")
    assert leg.trade_hits_us(45, "no")           # swept through us
    assert not leg.trade_hits_us(47, "no")       # printed above us: a better bid took it
    assert not leg.trade_hits_us(46, "yes")      # a yes BUYER lifts NO bids, not us
    assert not leg.trade_hits_us(None, "no")


def test_no_leg_is_hit_only_by_yes_buyers_at_or_above_our_level():
    leg = _leg("no", 48)     # NO bid at 52 -> yes-scale 48
    assert leg.trade_hits_us(48, "yes")
    assert leg.trade_hits_us(49, "yes")
    assert not leg.trade_hits_us(47, "yes")
    assert not leg.trade_hits_us(48, "no")


def test_optimistic_fills_on_touch_conservative_waits_for_queue():
    leg = _leg("yes", 46, qty=10, ahead=20)
    got = leg.on_trade(yes_price_cents=46, count=5, taker_outcome_side="no", at=T0 + timedelta(seconds=1))
    assert got == {f.MODEL_OPTIMISTIC: 10.0}
    assert leg.filled[f.MODEL_CONSERVATIVE] == 0 and leg.queue_ahead(f.MODEL_CONSERVATIVE) == 15
    got = leg.on_trade(yes_price_cents=46, count=18, taker_outcome_side="no", at=T0 + timedelta(seconds=2))
    # 15 ahead consumed, 3 spill to us under both queue models
    assert got == {f.MODEL_CONSERVATIVE: 3.0, f.MODEL_QUEUE_AWARE: 3.0}
    assert leg.remaining(f.MODEL_CONSERVATIVE) == 7 and not leg.is_full(f.MODEL_CONSERVATIVE)
    assert leg.first_fill_at[f.MODEL_CONSERVATIVE] == T0 + timedelta(seconds=2)
    assert f.MODEL_CONSERVATIVE not in leg.full_fill_at
    got = leg.on_trade(yes_price_cents=46, count=100, taker_outcome_side="no", at=T0 + timedelta(seconds=3))
    assert got == {f.MODEL_CONSERVATIVE: 7.0, f.MODEL_QUEUE_AWARE: 7.0}
    assert leg.is_full(f.MODEL_CONSERVATIVE) and leg.full_fill_at[f.MODEL_CONSERVATIVE] == T0 + timedelta(seconds=3)
    assert leg.volume_through == 123


def test_queue_aware_shrinks_ahead_when_level_shrinks():
    leg = _leg("yes", 46, qty=10, ahead=20)
    leg.on_level_quantity(5.0)      # 15 contracts left the level ahead of us
    assert leg.queue_ahead(f.MODEL_QUEUE_AWARE) == 5 and leg.queue_ahead(f.MODEL_CONSERVATIVE) == 20
    leg.on_level_quantity(50.0)     # growth behind us changes nothing
    assert leg.queue_ahead(f.MODEL_QUEUE_AWARE) == 5
    got = leg.on_trade(yes_price_cents=46, count=8, taker_outcome_side="no", at=T0)
    assert got[f.MODEL_QUEUE_AWARE] == 3.0 and f.MODEL_CONSERVATIVE not in got


def test_trade_through_our_level_clears_the_queue_ahead():
    leg = _leg("yes", 46, qty=10, ahead=20)
    got = leg.on_trade(yes_price_cents=44, count=4, taker_outcome_side="no", at=T0)
    assert got == {f.MODEL_OPTIMISTIC: 10.0, f.MODEL_CONSERVATIVE: 4.0, f.MODEL_QUEUE_AWARE: 4.0}


def test_pair_outcome_labels():
    y, n = _leg("yes", 46, qty=10, ahead=0), _leg("no", 48, qty=10, ahead=0)
    assert f.pair_outcome(y, n, f.MODEL_OPTIMISTIC) == f.OUTCOME_NONE
    y.on_trade(yes_price_cents=46, count=10, taker_outcome_side="no", at=T0)
    assert f.pair_outcome(y, n, f.MODEL_OPTIMISTIC) == f.OUTCOME_YES_ONLY
    assert f.pair_outcome(y, n, f.MODEL_CONSERVATIVE) == f.OUTCOME_YES_ONLY
    n.on_trade(yes_price_cents=48, count=4, taker_outcome_side="yes", at=T0)
    assert f.pair_outcome(y, n, f.MODEL_CONSERVATIVE) == f.OUTCOME_PARTIAL_BOTH
    n.on_trade(yes_price_cents=48, count=6, taker_outcome_side="yes", at=T0)
    assert f.pair_outcome(y, n, f.MODEL_CONSERVATIVE) == f.OUTCOME_BOTH
    z = _leg("yes", 46, qty=10, ahead=0)
    z.on_trade(yes_price_cents=46, count=3, taker_outcome_side="no", at=T0)
    assert f.pair_outcome(z, _leg("no", 48, qty=10, ahead=0), f.MODEL_CONSERVATIVE) == f.OUTCOME_PARTIAL_YES
