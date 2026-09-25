"""Phase 1a one-sided live smoke test — the decision layer. Every test here is a safety
property: the function must refuse rather than spend, and must never exceed a declared cap."""

from __future__ import annotations

import pytest

from kalshi_bot.liquidity_incentive import live as lv


def _q(**over):
    # The resting totals sit just above Target Size and well inside
    # `MAX_COMPETING_DEPTH_TARGET_MULTIPLE`. They used to be 30,000 / 40,000 — a book so deep
    # that under the universe rule it is now refused outright, which is the whole point of the
    # rule and no longer describes a market this book will quote. Tests that care about depth
    # pass their own numbers.
    base = dict(
        market_ticker="KXTEST-A", best_yes_bid=78, best_no_bid=21,
        yes_resting_total=1500.0, no_resting_total=2000.0, target_size=1000.0,
        program_hours_remaining=48.0,
    )
    base.update(over)
    return lv.build_live_quote(**base)


def test_picks_the_cheaper_side_and_rests_at_its_touch():
    q = _q()
    assert isinstance(q, lv.LiveQuote)
    assert q.side == lv.SIDE_NO and q.price_cents == 21      # no 21 is cheaper than yes 78
    # Quantity is whatever MAX_ORDER_DOLLARS buys at this price (thesis §9.36) — sized here to
    # match the sizing formula itself, not a number that breaks every time the budget moves.
    expected_qty = min(lv.MAX_CONTRACTS_PER_ORDER, int(lv.MAX_ORDER_DOLLARS * 100 // 21))
    assert q.quantity == expected_qty
    assert q.collateral_usd == q.max_loss_usd == pytest.approx(expected_qty * 0.21)


def test_picks_yes_when_yes_is_the_cheap_side():
    q = _q(best_yes_bid=9, best_no_bid=90)
    assert isinstance(q, lv.LiveQuote) and q.side == lv.SIDE_YES and q.price_cents == 9
    expected_qty = min(lv.MAX_CONTRACTS_PER_ORDER, int(lv.MAX_ORDER_DOLLARS * 100 // 9))
    assert q.max_loss_usd == pytest.approx(expected_qty * 0.09)


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
        {"market_ticker": "A", "best_yes_bid": 20, "best_no_bid": 79, "yes_resting_total": 2000,
         "no_resting_total": 2000, "target_size": 1000, "program_hours_remaining": 100},
        {"market_ticker": "B", "best_yes_bid": 5, "best_no_bid": 94, "yes_resting_total": 2000,
         "no_resting_total": 2000, "target_size": 1000, "program_hours_remaining": 50},
        {"market_ticker": "C", "best_yes_bid": 60, "best_no_bid": 39, "yes_resting_total": 2000,
         "no_resting_total": 2000, "target_size": 1000, "program_hours_remaining": 10},
    ]
    ranked = lv.rank_candidates(cands)
    assert [c["market_ticker"] for c, _ in ranked] == ["B", "A"]   # C's cheapest touch is 39c
    expected_qty = min(lv.MAX_CONTRACTS_PER_ORDER, int(lv.MAX_ORDER_DOLLARS * 100 // 5))
    assert ranked[0][1].max_loss_usd == pytest.approx(expected_qty * 0.05)


def test_caps_are_the_ones_the_risk_envelope_will_name():
    # A later test asserts the XOS envelope equals these; pin them here so a silent edit fails.
    # History: MAX_OPEN_ORDERS 3 -> 5 on 2026-09-24 (thesis §9.34). Then, on 2026-09-25 (thesis
    # §9.36): MAX_CONTRACTS_PER_ORDER 1 -> 500, MAX_ORDER_DOLLARS 1.00 -> 20.00, MAX_OPEN_ORDERS
    # 5 -> 2, MAX_STRATEGY_EXPOSURE_USD 10.00 -> 50.00 -- sized so a resting bid could reach the
    # scoring floor on Kalshi's own Target Sizes. See the module comment on
    # MAX_CONTRACTS_PER_ORDER in liquidity_incentive/live.py, and
    # test_liquidity_incentive_xos_package.py::test_the_operator_guardrails_are_not_exceeded for
    # the current authorized ceiling.
    assert (lv.MAX_CONTRACTS_PER_ORDER, lv.MAX_ORDER_DOLLARS, lv.MAX_OPEN_ORDERS,
            lv.MAX_STRATEGY_EXPOSURE_USD, lv.MAX_PRICE_CENTS) == (500, 20.00, 2, 50.00, 25)


class TestTheUniverseRule:
    """The competing-depth cap — the one lever this book has on its own reward.

    Reward share is our size over the competing depth. A 1-contract bid in a book resting
    40,000 earns a share that rounds to zero while taking the same adverse selection as a bid in
    a book resting 2,000. The shadow tape agrees in both directions at once (§9.27): the `deep`
    bucket ran a worse mean single-leg mark AND a lower mean estimated reward than `medium`.
    """

    def test_a_book_far_deeper_than_target_is_refused(self):
        """The book this canary was actually quoting into: 30k/40k against a 1,000 target."""
        q = _q(yes_resting_total=30_000.0, no_resting_total=40_000.0, target_size=1000.0)
        assert isinstance(q, lv.Refusal) and q.code == lv.REFUSE_BOOK_TOO_DEEP

    def test_the_boundary_is_inclusive_so_exactly_the_multiple_still_places(self):
        """`> cap`, not `>= cap`. A book resting exactly 3x target is the deepest one the
        report's own `medium` bucket contains, and excluding it would move the line."""
        exactly = lv.MAX_COMPETING_DEPTH_TARGET_MULTIPLE * 1000.0
        assert isinstance(_q(yes_resting_total=exactly, no_resting_total=exactly,
                             target_size=1000.0), lv.LiveQuote)
        assert _q(yes_resting_total=exactly + 1, no_resting_total=exactly + 1,
                  target_size=1000.0).code == lv.REFUSE_BOOK_TOO_DEEP

    def test_the_thinner_side_decides_not_the_average(self):
        """One deep side does not disqualify the market, and does not buy the other a pass.
        `min`, not `mean`: a lopsided book must read the same whichever way round it is."""
        assert isinstance(_q(yes_resting_total=1200.0, no_resting_total=99_000.0,
                             target_size=1000.0), lv.LiveQuote)
        assert isinstance(_q(yes_resting_total=99_000.0, no_resting_total=1200.0,
                             target_size=1000.0), lv.LiveQuote)

    def test_under_target_still_reads_as_under_target_not_as_too_deep(self):
        """Two gates read the same depth number and must stay distinguishable in the refusal
        record: 'nobody is paid at all' is a different fact from 'our share is negligible'."""
        assert _q(no_resting_total=900.0).code == lv.REFUSE_TARGET_NOT_MET

    def test_the_cap_scales_with_target_size_rather_than_being_an_absolute_depth(self):
        """20,000 resting is deep against a 1,000 target and thin against a 10,000 one. The
        share that matters is relative to what the programme is paying for."""
        assert _q(yes_resting_total=20_000.0, no_resting_total=20_000.0,
                  target_size=1000.0).code == lv.REFUSE_BOOK_TOO_DEEP
        assert isinstance(_q(yes_resting_total=20_000.0, no_resting_total=20_000.0,
                             target_size=10_000.0), lv.LiveQuote)


class TestRankingPrefersTheThinnerBook:
    @staticmethod
    def _c(ticker, *, depth, hours, no_bid=21):
        return {"market_ticker": ticker, "best_yes_bid": 78, "best_no_bid": no_bid,
                "yes_resting_total": depth, "no_resting_total": depth,
                "target_size": 1000.0, "program_hours_remaining": hours}

    def test_equally_cheap_orders_break_the_tie_on_depth_not_timing(self):
        """The change in ordering. Both cost 21c, so the old key fell straight through to
        program end — which pays nothing. The thinner book is worth strictly more reward for
        identical risk, so it goes first."""
        ranked = lv.rank_candidates([
            self._c("KXDEEP", depth=2900.0, hours=4.0),     # ends soonest, nearly at the cap
            self._c("KXTHIN", depth=1100.0, hours=60.0),
        ])
        assert [c["market_ticker"] for c, _ in ranked] == ["KXTHIN", "KXDEEP"]

    def test_price_still_wins_over_depth(self):
        """Depth is the second key, never the first. Collateral is the entire downside and
        stays the safety ordering — a thinner book must not talk us into a dearer order."""
        ranked = lv.rank_candidates([
            self._c("KXTHIN_DEAR", depth=1100.0, hours=48.0, no_bid=20),
            self._c("KXDEEP_CHEAP", depth=2900.0, hours=48.0, no_bid=3),
        ])
        assert ranked[0][0]["market_ticker"] == "KXDEEP_CHEAP"
        assert ranked[0][1].price_cents == 3

    def test_ranking_never_returns_a_book_the_gate_refused(self):
        assert lv.rank_candidates([self._c("KXWAYDEEP", depth=80_000.0, hours=48.0)]) == []
