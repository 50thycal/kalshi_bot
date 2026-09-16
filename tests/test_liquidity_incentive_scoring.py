"""LIP scoring against the published sentences (scoring.py RULE_* docstring)."""

from kalshi_bot.liquidity_incentive import scoring as s


def test_r2_published_worked_example():
    assert s.worked_example_reward(0.20, 100.0, 8_000, 10_000) == 16.0


def test_api_units():
    assert s.period_reward_usd(1_000_000) == 100.0          # centi-cents
    assert s.discount_factor(5_000) == 0.5 and s.discount_factor(10_000) == 1.0
    assert s.discount_factor(None) == 1.0 and s.period_reward_usd(None) is None


def test_r3_reference_price_walks_cumulative_depth_not_the_touch():
    # quantfirm's real example: thin top over a fat 1-2c layer. Target 1000 -> need 200.
    yes = {25: 100.0, 2: 780.0, 1: 1000.0}
    side = s.side_score(yes, 1000, 0.5)
    assert side.reference_price == 2
    assert side.meets_target is True and side.resting_total == 1880.0
    # R4: 25c and 2c both at/above the reference -> multiplier 1; 1c is one tick below -> 0.5
    assert side.field_score == 100 + 780 + 1000 * 0.5


def test_r3_too_thin_to_set_a_reference():
    side = s.side_score({50: 10.0}, 1000, 0.5)
    assert side.reference_price is None and side.field_score == 0.0 and not side.meets_target


def test_r4_order_multiplier():
    assert s.order_multiplier(50, 48, 0.5) == 1.0       # better than reference
    assert s.order_multiplier(48, 48, 0.5) == 1.0       # at reference
    assert s.order_multiplier(45, 48, 0.5) == 0.125     # three ticks below
    assert s.order_multiplier(45, 48, 1.0) == 1.0       # no penalty
    assert s.order_multiplier(45, None, 0.5) == 0.0


def test_r5_r6_two_sided_share_and_reward_rate():
    # Field: 900 yes at 46, 900 no at 52; target 1000 -> neither side qualifies without us.
    est = s.estimate(yes_levels={46: 900.0}, no_levels={52: 900.0}, target_size=1000,
                     discount_factor_bps=5000, our_yes_price=46, our_yes_size=100,
                     our_no_price=52, our_no_size=100,
                     period_reward_usd_value=100.0, period_seconds=86_400)
    assert est.snapshot_qualifies is True                  # A4: our size counts toward target
    assert abs(est.our_yes_share - 0.1) < 1e-9 and abs(est.our_no_share - 0.1) < 1e-9
    assert abs(est.snapshot_score - 0.2) < 1e-9 and abs(est.period_share - 0.1) < 1e-9
    # $100/day accrues $100/86400 per second; we take 10% of it.
    assert abs(est.reward_per_second_usd - 100.0 / 86_400 * 0.1) < 1e-12
    assert abs(est.reward_per_hour_usd - 100.0 / 24 * 0.1) < 1e-9


def test_r2_non_qualifying_snapshot_pays_nothing():
    est = s.estimate(yes_levels={46: 300.0}, no_levels={52: 5000.0}, target_size=1000,
                     discount_factor_bps=5000, our_yes_price=46, our_yes_size=10,
                     our_no_price=52, our_no_size=10,
                     period_reward_usd_value=100.0, period_seconds=86_400)
    assert est.snapshot_qualifies is False and est.reward_per_second_usd == 0.0
    assert est.our_yes_share > 0        # the share exists; the snapshot just does not pay


def test_one_sided_quote_earns_only_that_side():
    est = s.estimate(yes_levels={46: 900.0}, no_levels={52: 1900.0}, target_size=1000,
                     discount_factor_bps=5000, our_yes_price=46, our_yes_size=100,
                     our_no_price=None, our_no_size=0,
                     period_reward_usd_value=100.0, period_seconds=3600)
    assert est.our_no_share == 0.0 and abs(est.our_yes_share - 0.1) < 1e-9
    assert abs(est.period_share - 0.05) < 1e-9
