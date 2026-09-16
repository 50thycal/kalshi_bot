from datetime import datetime, timezone

from kalshi_bot.liquidity_incentive import economics as e
from kalshi_bot.liquidity_incentive import fills as f
from kalshi_bot.liquidity_incentive.fees import FeeRule

T0 = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
ZERO = FeeRule(maker_rate=0.0, taker_rate=0.07, source="test")
MAKER = FeeRule(maker_rate=0.0175, taker_rate=0.07, source="test")


def _legs(y=0.0, n=0.0, qty=10.0):
    yl = f.ShadowLeg("yes", 46, qty, T0, 0.0)
    nl = f.ShadowLeg("no", 48, qty, T0, 0.0)
    if y:
        yl.on_trade(yes_price_cents=46, count=y, taker_outcome_side="no", at=T0)
    if n:
        nl.on_trade(yes_price_cents=48, count=n, taker_outcome_side="yes", at=T0)
    return yl, nl


def test_both_filled_pairs_settle_to_100():
    yl, nl = _legs(10, 10)
    pe = e.pair_economics(fill_model=f.MODEL_OPTIMISTIC, yes_leg=yl, no_leg=nl, yes_bid=46, no_bid=52,
                          rest_seconds=3600, capital_required_usd=9.8, fee_rule=ZERO,
                          reward_yes_usd=0.01, reward_no_usd=0.01)
    assert pe.outcome == f.OUTCOME_BOTH and pe.matched_pairs == 10
    assert pe.paired_pnl_usd == 0.2            # 2c x 10 pairs
    assert pe.fees_usd == 0.0 and pe.single_leg_side is None
    assert pe.net_before_settlement_usd == 0.22 and pe.capital_hours == 9.8


def test_single_leg_marked_at_bid_and_fees_charged():
    yl, nl = _legs(10, 0)
    pe = e.pair_economics(fill_model=f.MODEL_OPTIMISTIC, yes_leg=yl, no_leg=nl, yes_bid=46, no_bid=52,
                          rest_seconds=600, capital_required_usd=9.8, fee_rule=MAKER,
                          reward_yes_usd=0.0, reward_no_usd=0.002,
                          single_leg_mark_bid_cents=44, single_leg_worst_bid_cents=40)
    assert pe.outcome == f.OUTCOME_YES_ONLY and pe.single_leg_side == "yes" and pe.single_leg_qty == 10
    assert pe.single_leg_mtm_usd == -0.2 and pe.single_leg_max_adverse_usd == -0.6
    assert pe.fees_usd == 0.05          # 0.0175*10*.46*.54 = 0.0435 -> 0.05
    assert pe.net_before_settlement_usd == round(0.002 + 0 - 0.2 - 0.05, 4)


def test_settlement_pnl():
    assert e.settlement_pnl_usd(single_leg_side="yes", single_leg_qty=10, single_price_cents=46, result="yes") == 5.4
    assert e.settlement_pnl_usd(single_leg_side="yes", single_leg_qty=10, single_price_cents=46, result="no") == -4.6
    assert e.settlement_pnl_usd(single_leg_side="no", single_leg_qty=3, single_price_cents=52, result="no") == 1.44
    assert e.settlement_pnl_usd(single_leg_side=None, single_leg_qty=0, single_price_cents=46, result="yes") is None


def test_opportunity_components_and_states():
    o = e.opportunity(est_reward_per_day_usd=1.0, est_paired_value_per_day_usd=0.1,
                      est_single_leg_cost_per_day_usd=0.2, est_fees_per_day_usd=0.05,
                      capital_required_usd=100.0, both_sides_meet_target=True,
                      our_share_of_field=0.1, sample_outcomes=10)
    assert o.est_net_per_day_usd == round(1.0 + 0.1 - 0.2 - 0.05 - 100 * e.CAPITAL_COST_PER_DAY, 6)
    assert o.state == e.STATE_SHADOW and o.score == round(o.est_net_per_day_usd / 100, 6)
    assert o.reward_per_capital_dollar_per_day == 0.01
    poc = e.opportunity(est_reward_per_day_usd=1.0, est_paired_value_per_day_usd=0.0,
                        est_single_leg_cost_per_day_usd=0.0, est_fees_per_day_usd=0.0,
                        capital_required_usd=100.0, both_sides_meet_target=True,
                        our_share_of_field=0.1, sample_outcomes=60)
    assert poc.state == e.STATE_POC_CANDIDATE
    assert e.classify(est_net_per_day_usd=0.5, est_reward_per_day_usd=1.0, capital_required_usd=100,
                      both_sides_meet_target=False, our_share_of_field=0.1,
                      single_leg_cost_per_day_usd=0, sample_outcomes=0)[0] == e.STATE_WATCH
    assert e.classify(est_net_per_day_usd=0.5, est_reward_per_day_usd=1.0, capital_required_usd=100,
                      both_sides_meet_target=True, our_share_of_field=0.01,
                      single_leg_cost_per_day_usd=0, sample_outcomes=0)[0] == e.STATE_WATCH
    assert e.classify(est_net_per_day_usd=-0.1, est_reward_per_day_usd=1.0, capital_required_usd=100,
                      both_sides_meet_target=True, our_share_of_field=0.1,
                      single_leg_cost_per_day_usd=0, sample_outcomes=0)[0] == e.STATE_IGNORE
    assert e.classify(est_net_per_day_usd=0.5, est_reward_per_day_usd=1.0, capital_required_usd=0,
                      both_sides_meet_target=True, our_share_of_field=0.1,
                      single_leg_cost_per_day_usd=0, sample_outcomes=0)[0] == e.STATE_IGNORE
    assert e.classify(est_net_per_day_usd=0.5, est_reward_per_day_usd=1.0, capital_required_usd=100,
                      both_sides_meet_target=True, our_share_of_field=0.1,
                      single_leg_cost_per_day_usd=2.0, sample_outcomes=0)[0] == e.STATE_WATCH
