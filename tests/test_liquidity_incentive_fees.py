from kalshi_bot.liquidity_incentive import fees as f


def test_roundup_anchors_from_gallantfox():
    assert f.roundup_to_cent(0.07 * 100 * 0.5 * 0.5) == 1.75
    assert f.roundup_to_cent(0.0001) == 0.01
    assert f.roundup_to_cent(1.001) == 1.01
    assert f.roundup_to_cent(0.0) == 0.0


def test_trade_fee_anchors():
    assert f.trade_fee_usd(100, 50, rate=0.07) == 1.75
    assert f.trade_fee_usd(1, 50, rate=0.07) == 0.02
    assert f.trade_fee_usd(1, 50, rate=0.0175) == 0.01
    assert f.trade_fee_usd(100, 50, rate=0.0175) == 0.44
    assert f.trade_fee_usd(100, 50, rate=0.035) == 0.88
    assert f.trade_fee_usd(100, 30, rate=0.07) == f.trade_fee_usd(100, 70, rate=0.07)
    assert f.trade_fee_usd(100, 50, rate=0.0) == 0.0
    assert f.trade_fee_usd(0, 50, rate=0.07) == 0.0
    assert f.trade_fee_usd(5, 0, rate=0.07) == 0.0 and f.trade_fee_usd(5, 100, rate=0.07) == 0.0


def test_default_rule_is_zero_maker_and_says_so():
    rule = f.rule_from_market({"ticker": "KXT-A"})
    assert rule.maker_rate == 0.0 and rule.taker_rate == 0.07 and rule.source == f.SOURCE_DEFAULT
    assert f.pair_maker_fee_cents(rule, 46, 52, 10) == 0.0


def test_market_field_with_maker_fees_charges_published_rate():
    rule = f.rule_from_market({"fee_type": "quadratic_with_maker_fees", "maker_fees": True})
    assert rule.maker_rate == 0.0175 and rule.source == f.SOURCE_MARKET_FIELD
    # per pair at qty 10: 0.0175*10*.46*.54=0.0435->0.05 ; 0.0175*10*.52*.48=0.0437->0.05 => 0.10/10 = 1c/pair
    assert f.pair_maker_fee_cents(rule, 46, 52, 10) == 1.0
    assert f.rule_from_market({"fee_type": "quadratic", "maker_fees": False}).maker_rate == 0.0


def test_series_override_wins():
    ov = {"KXNFL": f.FeeRule(maker_rate=0.0175, taker_rate=0.07, source=f.SOURCE_SERIES_OVERRIDE)}
    rule = f.rule_from_market({"maker_fees": False}, series_overrides=ov, series_ticker="KXNFL")
    assert rule.source == f.SOURCE_SERIES_OVERRIDE and rule.maker_rate == 0.0175
