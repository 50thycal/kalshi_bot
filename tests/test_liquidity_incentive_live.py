"""Phase 1a one-sided live smoke test — the decision layer. Every test here is a safety
property: the function must refuse rather than spend, and must never exceed a declared cap."""

from __future__ import annotations

import pytest

from kalshi_bot.liquidity_incentive import live as lv


def _q(**over):
    base = dict(
        market_ticker="KXTEST-A", best_yes_bid=78, best_no_bid=21,
        yes_resting_total=30000.0, no_resting_total=40000.0, target_size=1000.0,
        program_hours_remaining=48.0,
    )
    base.update(over)
    return lv.build_live_quote(**base)


def test_picks_the_cheaper_side_and_rests_at_its_touch():
    q = _q()
    assert isinstance(q, lv.LiveQuote)
    assert q.side == lv.SIDE_NO and q.price_cents == 21      # no 21 is cheaper than yes 78
    assert q.quantity == 1
    assert q.collateral_usd == 0.21 and q.max_loss_usd == 0.21


def test_picks_yes_when_yes_is_the_cheap_side():
    q = _q(best_yes_bid=9, best_no_bid=90)
    assert isinstance(q, lv.LiveQuote) and q.side == lv.SIDE_YES and q.price_cents == 9
    assert q.max_loss_usd == 0.09


def test_downside_cap_refuses_an_expensive_touch():
    q = _q(best_yes_bid=49, best_no_bid=50)          # cheapest is 49c, above the 25c cap
    assert isinstance(q, lv.Refusal) and q.code == lv.REFUSE_TOO_EXPENSIVE


def test_refuses_when_either_side_is_under_target_size():
    assert _q(no_resting_total=900.0).code == lv.REFUSE_TARGET_NOT_MET
    assert _q(yes_resting_total=10.0).code == lv.REFUSE_TARGET_NOT_MET
    # a snapshot that does not qualify pays nobody, so there is nothing to smoke-test


def test_refuses_a_one_sided_or_crossed_book():
    assert _q(best_no_bid=None).code == lv.REFUSE_NOT_TWO_SIDED
    assert _q(best_yes_bid=None).code == lv.REFUSE_NOT_TWO_SIDED
    assert _q(best_yes_bid=60, best_no_bid=60).code == lv.REFUSE_POST_ONLY_CROSS


def test_refuses_a_program_about_to_end():
    assert _q(program_hours_remaining=0.5).code == lv.REFUSE_PROGRAM_ENDING
    assert _q(target_size=None).code == lv.REFUSE_NO_TARGET_SIZE


def test_refuses_a_ticker_reserved_for_another_live_book():
    q = _q(market_ticker="KXMLBSEASONGAMES-27-1215",
           excluded_series=frozenset({"KXMLBSEASONGAMES"}))
    assert isinstance(q, lv.Refusal) and q.code == lv.REFUSE_EXCLUDED_SERIES


def test_open_order_and_exposure_caps_refuse():
    assert _q(open_orders_now=lv.MAX_OPEN_ORDERS).code == lv.REFUSE_OPEN_ORDER_CAP
    assert _q(strategy_exposure_now_usd=lv.MAX_STRATEGY_EXPOSURE_USD).code == lv.REFUSE_EXPOSURE_CAP
    # just under the cap still places
    assert isinstance(_q(strategy_exposure_now_usd=9.5), lv.LiveQuote)


def test_reference_price_is_reported_not_enforced():
    q = _q(reference_price_by_side={lv.SIDE_NO: 21})
    assert isinstance(q, lv.LiveQuote) and q.at_or_above_reference is True
    deep = _q(reference_price_by_side={lv.SIDE_NO: 24})
    assert isinstance(deep, lv.LiveQuote) and deep.at_or_above_reference is False


@pytest.mark.parametrize("yes_bid,no_bid", [(a, b) for a in range(1, 100, 7) for b in range(1, 100, 7)])
def test_no_quote_ever_exceeds_a_declared_cap(yes_bid, no_bid):
    """The property that matters: across the whole price grid, anything returned is inside
    every cap, and everything else is a refusal."""
    q = _q(best_yes_bid=yes_bid, best_no_bid=no_bid)
    if isinstance(q, lv.Refusal):
        return
    assert q.quantity <= lv.MAX_CONTRACTS_PER_ORDER
    assert q.collateral_usd <= lv.MAX_ORDER_DOLLARS
    assert q.price_cents <= lv.MAX_PRICE_CENTS
    assert 1 <= q.price_cents <= 99
    assert q.max_loss_usd == q.collateral_usd          # a resting bid risks exactly its collateral
    assert yes_bid + no_bid <= 100                      # never quoted into a crossed book


def test_ranking_prefers_cheapest_then_soonest_payout():
    cands = [
        {"market_ticker": "A", "best_yes_bid": 20, "best_no_bid": 79, "yes_resting_total": 5000,
         "no_resting_total": 5000, "target_size": 1000, "program_hours_remaining": 100},
        {"market_ticker": "B", "best_yes_bid": 5, "best_no_bid": 94, "yes_resting_total": 5000,
         "no_resting_total": 5000, "target_size": 1000, "program_hours_remaining": 50},
        {"market_ticker": "C", "best_yes_bid": 60, "best_no_bid": 39, "yes_resting_total": 5000,
         "no_resting_total": 5000, "target_size": 1000, "program_hours_remaining": 10},
    ]
    ranked = lv.rank_candidates(cands)
    assert [c["market_ticker"] for c, _ in ranked] == ["B", "A"]   # C's cheapest touch is 39c
    assert ranked[0][1].max_loss_usd == 0.05


def test_caps_are_the_ones_the_risk_envelope_will_name():
    # A later test asserts the XOS envelope equals these; pin them here so a silent edit fails.
    assert (lv.MAX_CONTRACTS_PER_ORDER, lv.MAX_ORDER_DOLLARS, lv.MAX_OPEN_ORDERS,
            lv.MAX_STRATEGY_EXPOSURE_USD, lv.MAX_PRICE_CENTS) == (1, 1.00, 3, 10.00, 25)
