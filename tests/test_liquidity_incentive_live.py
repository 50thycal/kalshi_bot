"""The two-sided live book — the decision layer (thesis §9.37). Every test is a safety property:
the function must refuse rather than spend, must never exceed a declared cap, and must get out
when a registered exit rule says so."""

from __future__ import annotations

import pytest

from kalshi_bot.liquidity_incentive import live as lv


def _p(**over):
    # The resting totals sit just above Target Size and inside the universe rule; the close is
    # a day out, inside the close-time window. Tests that care about either pass their own.
    base = dict(
        market_ticker="KXTEST-A", best_yes_bid=3, best_no_bid=4,
        yes_resting_total=1500.0, no_resting_total=2000.0, target_size=1000.0,
        hours_to_close=240.0, program_hours_remaining=48.0,
    )
    base.update(over)
    return lv.build_pair_quote(**base)


# ------------------------------------------------------------------ the pair


def test_quotes_both_touches_with_one_quantity():
    q = _p()
    assert isinstance(q, lv.PairQuote)
    assert (q.yes.side, q.yes.price_cents) == (lv.SIDE_YES, 3)
    assert (q.no.side, q.no.price_cents) == (lv.SIDE_NO, 4)
    # Equal quantity is what makes it a hedge: both legs filled nets flat at the locked edge.
    assert q.yes.quantity == q.no.quantity == q.quantity
    assert q.edge_cents == 93


def test_the_dear_leg_sets_the_quantity_and_neither_leg_exceeds_its_cap():
    q = _p(best_yes_bid=2, best_no_bid=5)
    expected = min(lv.MAX_CONTRACTS_PER_ORDER, int(lv.MAX_ORDER_DOLLARS * 100 // 5))
    assert q.quantity == expected
    assert q.no.collateral_usd <= lv.MAX_ORDER_DOLLARS
    assert q.yes.collateral_usd <= lv.MAX_ORDER_DOLLARS
    assert q.collateral_usd == pytest.approx(q.yes.collateral_usd + q.no.collateral_usd)
    # The single-leg worst case is the dearer leg filled alone and lost.
    assert q.max_loss_usd == pytest.approx(q.no.collateral_usd)


def test_refuses_a_pair_with_no_edge():
    assert _p(best_yes_bid=45, best_no_bid=55).code == lv.REFUSE_NO_EDGE      # 100: nothing locked
    # 1c edge places (above the live leg cap, so the cap is lifted to isolate the edge rule)
    assert isinstance(_p(best_yes_bid=45, best_no_bid=54, max_price_cents=99), lv.PairQuote)


def test_refuses_a_leg_above_the_per_leg_price_cap():
    q = _p(best_yes_bid=3, best_no_bid=lv.MAX_PRICE_CENTS + 1)
    assert isinstance(q, lv.Refusal) and q.code == lv.REFUSE_TOO_EXPENSIVE


def test_refuses_crossed_and_one_sided_books():
    assert _p(best_yes_bid=60, best_no_bid=60).code == lv.REFUSE_POST_ONLY_CROSS
    assert _p(best_no_bid=None).code == lv.REFUSE_NOT_TWO_SIDED
    assert _p(best_yes_bid=None).code == lv.REFUSE_NOT_TWO_SIDED


def test_refuses_when_either_side_is_under_target_size():
    assert _p(no_resting_total=900.0).code == lv.REFUSE_TARGET_NOT_MET
    assert _p(yes_resting_total=10.0).code == lv.REFUSE_TARGET_NOT_MET


def test_refuses_a_program_about_to_end_or_without_target():
    assert _p(program_hours_remaining=0.5).code == lv.REFUSE_PROGRAM_ENDING
    assert _p(target_size=None).code == lv.REFUSE_NO_TARGET_SIZE


def test_refuses_excluded_series_and_blocked_events():
    assert _p(market_ticker="KXMLBSEASONGAMES-27-1215",
              excluded_series=frozenset({"KXMLBSEASONGAMES"})).code == lv.REFUSE_EXCLUDED_SERIES
    assert _p(event_ticker="EV", blocked_event_tickers=frozenset({"EV"})).code == lv.REFUSE_EVENT_CAP


def test_open_market_and_exposure_caps_refuse():
    assert _p(open_orders_now=lv.MAX_OPEN_ORDERS).code == lv.REFUSE_OPEN_ORDER_CAP
    assert _p(strategy_exposure_now_usd=lv.MAX_STRATEGY_EXPOSURE_USD).code == lv.REFUSE_EXPOSURE_CAP


@pytest.mark.parametrize("yes_bid,no_bid", [(a, b) for a in range(1, 100, 6) for b in range(1, 100, 6)])
def test_no_pair_ever_exceeds_a_declared_cap(yes_bid, no_bid):
    q = _p(best_yes_bid=yes_bid, best_no_bid=no_bid)
    if isinstance(q, lv.Refusal):
        return
    assert yes_bid + no_bid <= 100 - lv.MIN_PAIR_EDGE_CENTS
    for leg in q.legs:
        assert 1 <= leg.price_cents <= lv.MAX_PRICE_CENTS
        assert 1 <= leg.quantity <= lv.MAX_CONTRACTS_PER_ORDER
        assert leg.collateral_usd <= lv.MAX_ORDER_DOLLARS
    assert q.yes.quantity == q.no.quantity


# ------------------------------------------------------------------ the close-time window


def test_the_close_time_window_is_a_hard_refusal():
    assert _p(hours_to_close=None).code == lv.REFUSE_NO_CLOSE_TIME
    assert _p(hours_to_close=lv.MIN_HOURS_TO_CLOSE - 0.1).code == lv.REFUSE_CLOSES_TOO_SOON
    assert _p(hours_to_close=lv.MAX_HOURS_TO_CLOSE + 0.1).code == lv.REFUSE_CLOSES_TOO_LATE
    assert isinstance(_p(hours_to_close=lv.MIN_HOURS_TO_CLOSE), lv.PairQuote)
    assert isinstance(_p(hours_to_close=lv.MAX_HOURS_TO_CLOSE), lv.PairQuote)


def test_the_window_leaves_time_to_rest_before_the_flatten():
    assert lv.MIN_HOURS_TO_CLOSE > lv.FLATTEN_HOURS_BEFORE_CLOSE


def test_a_market_that_already_closed_is_refused():
    """The KXBIGGESTQUAKE shape: closed days ago, never settled."""
    assert _p(hours_to_close=-190.0).code == lv.REFUSE_CLOSES_TOO_SOON


# ------------------------------------------------------------------ exits


def test_stop_loss_fires_at_the_registered_distance():
    entry = 40
    dist = lv.stop_distance_cents(entry)
    assert lv.decide_exit(entry_cents=entry, mark_bid_cents=entry - dist + 1,
                          hours_to_close=24) is None
    assert lv.decide_exit(entry_cents=entry, mark_bid_cents=entry - dist,
                          hours_to_close=24) == lv.EXIT_STOP_LOSS


def test_take_profit_fires_at_the_registered_distance():
    entry = 40
    dist = lv.take_profit_distance_cents(entry)
    # Below the take-profit line but at or above entry is a scratch since §9.48.
    assert lv.decide_exit(entry_cents=entry, mark_bid_cents=entry + dist - 1,
                          hours_to_close=24) == lv.EXIT_SCRATCH
    assert lv.decide_exit(entry_cents=entry, mark_bid_cents=entry + dist,
                          hours_to_close=24) == lv.EXIT_TAKE_PROFIT


def test_the_stop_is_half_the_entry_rounded_up_so_a_cheap_leg_is_stopped_too():
    # §9.48 protection B: out at half. The old 3c floor never stopped a 1–3c leg at all.
    assert [lv.stop_distance_cents(e) for e in (1, 2, 3, 5, 10, 40)] == [1, 1, 2, 3, 5, 20]
    assert lv.decide_exit(entry_cents=5, mark_bid_cents=2, hours_to_close=24) == lv.EXIT_STOP_LOSS
    assert lv.decide_exit(entry_cents=5, mark_bid_cents=3, hours_to_close=24) is None
    assert lv.take_profit_distance_cents(99) == lv.EXIT_MIN_DISTANCE_CENTS


def test_a_held_leg_whose_bid_is_back_at_entry_is_scratched():
    assert lv.decide_exit(entry_cents=5, mark_bid_cents=5, hours_to_close=24) == lv.EXIT_SCRATCH
    assert lv.decide_exit(entry_cents=5, mark_bid_cents=4, hours_to_close=24) is None


def test_pre_close_flatten_wins_and_needs_no_mark():
    assert lv.decide_exit(entry_cents=40, mark_bid_cents=None,
                          hours_to_close=lv.FLATTEN_HOURS_BEFORE_CLOSE) == lv.EXIT_PRE_CLOSE
    # Even a position that is winning comes off before close: capital must not ride into
    # settlement lag.
    assert lv.decide_exit(entry_cents=40, mark_bid_cents=41, hours_to_close=0.5) == lv.EXIT_PRE_CLOSE


def test_no_mark_and_no_close_means_hold():
    assert lv.decide_exit(entry_cents=40, mark_bid_cents=None, hours_to_close=24) is None
    assert lv.decide_exit(entry_cents=40, mark_bid_cents=39, hours_to_close=None) is None


def test_exit_leg_joins_the_opposite_touch_but_never_pays_past_a_scratch():
    # Held YES at 40: a NO bid at p realises 100 - 40 - p. Joining a 50c touch locks 10c.
    assert lv.exit_leg_price(entry_cents=40, best_opposite_bid=50) == 50
    # A touch that would lose money is capped at the scratch (EXIT_LEG_EDGE_CENTS = 0, §9.48).
    assert lv.exit_leg_price(entry_cents=40, best_opposite_bid=65) == 100 - 40 - lv.EXIT_LEG_EDGE_CENTS
    assert lv.exit_leg_price(entry_cents=40, best_opposite_bid=None) == 60
    assert lv.exit_leg_price(entry_cents=100, best_opposite_bid=5) is None


# ------------------------------------------------------------------ ranking


def _c(ticker, *, depth=1500.0, hours=240.0, yes=3, no=4, age=5.0):
    return {"market_ticker": ticker, "best_yes_bid": yes, "best_no_bid": no,
            "yes_resting_total": depth, "no_resting_total": depth, "target_size": 1000.0,
            "hours_to_close": hours, "program_hours_remaining": 48.0, "program_age_hours": age}


def test_ranking_prefers_fresher_then_thinner_then_wider_edge(monkeypatch):
    monkeypatch.setattr(lv, "QUOTE_MODE", "pair")
    ranked = lv.rank_candidates([
        _c("OLD_THIN", depth=1100.0, age=100.0),
        _c("NEW_DEEP", depth=2900.0, age=2.0),
        _c("NEW_THIN", depth=1100.0, age=3.0),
        _c("NEW_THIN_WIDE", depth=1100.0, age=4.0, yes=2, no=3),
        _c("UNKNOWN_AGE", depth=1100.0, age=None),
    ])
    assert [c["market_ticker"] for c, _ in ranked] == [
        "NEW_THIN_WIDE", "NEW_THIN", "NEW_DEEP", "OLD_THIN", "UNKNOWN_AGE"]


def test_ranking_never_returns_a_refused_market():
    assert lv.rank_candidates([_c("WAYDEEP", depth=80_000.0), _c("SOON", hours=24.0)]) == []


# ------------------------------------------------------------------ behind the touch (§9.42)


def test_rests_one_tick_behind_the_touch_when_that_still_scores():
    q = _p(best_yes_bid=4, best_no_bid=5, yes_touch_depth=200.0, no_touch_depth=300.0)
    assert (q.yes.price_cents, q.no.price_cents) == (3, 4)


def test_joins_the_touch_when_the_touch_alone_meets_target_size():
    # Behind a touch that already fills Target Size, an order scores nothing.
    q = _p(best_yes_bid=4, best_no_bid=5, yes_touch_depth=1000.0, no_touch_depth=999.0)
    assert (q.yes.price_cents, q.no.price_cents) == (4, 4)


def test_joins_the_touch_at_one_cent_and_when_depth_is_unknown():
    assert lv.behind_touch_price(1, 10.0, 1000.0) == 1
    assert lv.behind_touch_price(4, None, 1000.0) == 4
    assert lv.behind_touch_price(4, 10.0, None) == 4
    q = _p(best_yes_bid=1, best_no_bid=3, yes_touch_depth=10.0, no_touch_depth=10.0)
    assert (q.yes.price_cents, q.no.price_cents) == (1, 2)


def test_caps_are_the_ones_the_risk_envelope_will_name():
    # Pinned so a silent edit fails. History: MAX_OPEN_ORDERS 3 -> 5 (§9.34); 1 -> 500 contracts,
    # $1 -> $20, 5 -> 2 markets, $10 -> $50 budget (§9.36); two-sided at $10 a leg with a per-leg
    # price cap of 90c replacing the one-sided 25c cheap-side cap (§9.37); leg cap 90c -> 5c
    # (§9.41); close window 3–72h -> 7–60 days (§9.42); leg cap 5c -> 10c (§9.46).
    assert (lv.MAX_CONTRACTS_PER_ORDER, lv.MAX_ORDER_DOLLARS, lv.MAX_OPEN_ORDERS,
            lv.MAX_STRATEGY_EXPOSURE_USD, lv.MAX_PRICE_CENTS) == (500, 10.00, 2, 50.00, 10)
    assert (lv.MIN_HOURS_TO_CLOSE, lv.MAX_HOURS_TO_CLOSE, lv.FLATTEN_HOURS_BEFORE_CLOSE) == (
        168.0, 1440.0, 1.0)
    assert (lv.MIN_PROGRAM_HOURS_REMAINING, lv.QUOTE_TICKS_BEHIND_TOUCH) == (24.0, 1)
    # Exits 0.40 / floor 3 / exit-leg edge 1 -> 0.50 / stop floor 1 / scratch, and entries
    # pair -> cheap side at the reference (§9.48).
    assert (lv.STOP_LOSS_FRACTION, lv.TAKE_PROFIT_FRACTION, lv.EXIT_MIN_DISTANCE_CENTS) == (
        0.50, 0.40, 3)
    assert (lv.STOP_MIN_DISTANCE_CENTS, lv.EXIT_LEG_EDGE_CENTS, lv.QUOTE_MODE) == (
        1, 0, "cheap_side")


class TestTheUniverseRule:
    """The competing-depth cap — the one lever this book has on its own reward share (§9.27)."""

    def test_the_multiple_is_the_operator_approved_ten(self):
        # §9.45: raised 3x -> 10x by the operator. A change here is a rule change, not a tweak.
        assert lv.MAX_COMPETING_DEPTH_TARGET_MULTIPLE == 10.0

    def test_a_book_far_deeper_than_target_is_refused(self):
        q = _p(yes_resting_total=30_000.0, no_resting_total=40_000.0, target_size=1000.0)
        assert isinstance(q, lv.Refusal) and q.code == lv.REFUSE_BOOK_TOO_DEEP

    def test_the_boundary_is_inclusive(self):
        exactly = lv.MAX_COMPETING_DEPTH_TARGET_MULTIPLE * 1000.0
        assert isinstance(_p(yes_resting_total=exactly, no_resting_total=exactly), lv.PairQuote)
        assert _p(yes_resting_total=exactly + 1,
                  no_resting_total=exactly + 1).code == lv.REFUSE_BOOK_TOO_DEEP

    def test_the_thinner_side_decides(self):
        assert isinstance(_p(yes_resting_total=1200.0, no_resting_total=99_000.0), lv.PairQuote)
        assert isinstance(_p(yes_resting_total=99_000.0, no_resting_total=1200.0), lv.PairQuote)

    def test_the_cap_scales_with_target_size(self):
        assert _p(yes_resting_total=20_000.0, no_resting_total=20_000.0,
                  target_size=1000.0).code == lv.REFUSE_BOOK_TOO_DEEP
        assert isinstance(_p(yes_resting_total=20_000.0, no_resting_total=20_000.0,
                             target_size=10_000.0), lv.PairQuote)


# ------------------------------------------------------------------ the cheap side (§9.48)


def _s(**over):
    base = dict(
        market_ticker="KXTEST-A", best_yes_bid=23, best_no_bid=74,
        yes_resting_total=1500.0, no_resting_total=2000.0, target_size=1000.0,
        hours_to_close=240.0, program_hours_remaining=48.0,
        reference_price_by_side={lv.SIDE_YES: 20, lv.SIDE_NO: 4},
    )
    base.update(over)
    return lv.build_side_quote(**base)


def test_one_bid_on_the_cheap_side_at_its_reference_price():
    q = _s()
    assert isinstance(q, lv.SideQuote) and len(q.legs) == 1
    assert (q.leg.side, q.leg.price_cents, q.leg.reference_price_cents) == (lv.SIDE_NO, 4, 4)
    assert q.leg.at_or_above_reference
    assert q.quantity == min(lv.MAX_CONTRACTS_PER_ORDER, int(lv.MAX_ORDER_DOLLARS * 100 // 4))
    assert q.collateral_usd <= lv.MAX_ORDER_DOLLARS and q.max_loss_usd == q.collateral_usd


def test_the_cheaper_side_wins_when_both_are_cheap():
    q = _s(reference_price_by_side={lv.SIDE_YES: 3, lv.SIDE_NO: 7})
    assert (q.leg.side, q.leg.price_cents) == (lv.SIDE_YES, 3)


def test_no_cheap_side_means_no_quote():
    q = _s(reference_price_by_side={lv.SIDE_YES: 40, lv.SIDE_NO: lv.MAX_PRICE_CENTS + 1})
    assert isinstance(q, lv.Refusal) and q.code == lv.REFUSE_TOO_EXPENSIVE
    assert _s(reference_price_by_side=None).code == lv.REFUSE_TOO_EXPENSIVE


def test_the_side_quote_keeps_every_shared_gate():
    assert _s(yes_resting_total=10.0).code == lv.REFUSE_TARGET_NOT_MET
    assert _s(hours_to_close=24.0).code == lv.REFUSE_CLOSES_TOO_SOON
    assert _s(open_orders_now=lv.MAX_OPEN_ORDERS).code == lv.REFUSE_OPEN_ORDER_CAP
    assert _s(strategy_exposure_now_usd=lv.MAX_STRATEGY_EXPOSURE_USD).code == lv.REFUSE_EXPOSURE_CAP


def test_quote_candidate_follows_the_quote_mode(monkeypatch):
    c = _c("KXTEST-A")
    c["reference_price_by_side"] = {lv.SIDE_YES: 3, lv.SIDE_NO: 4}
    assert isinstance(lv.quote_candidate(c), lv.SideQuote)
    monkeypatch.setattr(lv, "QUOTE_MODE", "pair")
    assert isinstance(lv.quote_candidate(c), lv.PairQuote)


def test_side_ranking_prefers_fresher_then_thinner_then_cheaper():
    def c(t, depth, age, ref):
        d = _c(t, depth=depth, age=age)
        d["reference_price_by_side"] = {lv.SIDE_YES: ref, lv.SIDE_NO: 90}
        return d
    ranked = lv.rank_candidates([c("OLD", 1100.0, 100.0, 2), c("NEW_DEEP", 2900.0, 2.0, 2),
                                 c("NEW_THIN_5", 1100.0, 3.0, 5), c("NEW_THIN_2", 1100.0, 4.0, 2)])
    assert [x["market_ticker"] for x, _ in ranked] == ["NEW_THIN_2", "NEW_THIN_5", "NEW_DEEP", "OLD"]
