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
    assert dist == lv.TAKE_PROFIT_CENTS == 5
    # Below the take-profit line, even at or above entry, is a hold since §9.53 (no scratch).
    assert lv.decide_exit(entry_cents=entry, mark_bid_cents=entry + dist - 1,
                          hours_to_close=24) is None
    assert lv.decide_exit(entry_cents=entry, mark_bid_cents=entry + dist,
                          hours_to_close=24) == lv.EXIT_TAKE_PROFIT


def test_the_stop_is_half_the_entry_rounded_up_so_a_cheap_leg_is_stopped_too():
    # §9.48 protection B: out at half. The old 3c floor never stopped a 1–3c leg at all.
    assert [lv.stop_distance_cents(e) for e in (1, 2, 3, 5, 10, 40)] == [1, 1, 2, 3, 5, 20]
    assert lv.decide_exit(entry_cents=5, mark_bid_cents=2, hours_to_close=24) == lv.EXIT_STOP_LOSS
    assert lv.decide_exit(entry_cents=5, mark_bid_cents=3, hours_to_close=24) is None
    assert lv.take_profit_distance_cents(99) == lv.TAKE_PROFIT_CENTS


def test_a_held_leg_back_at_entry_is_held_for_the_take_profit():
    # §9.53 (2C): the 2026-10-12 Miller Lite fill at 10c would have been scratched at 10c; now it
    # holds until 15c (+5c) or the stop at 5c.
    assert lv.decide_exit(entry_cents=10, mark_bid_cents=10, hours_to_close=24) is None
    assert lv.decide_exit(entry_cents=10, mark_bid_cents=14, hours_to_close=24) is None
    assert lv.decide_exit(entry_cents=10, mark_bid_cents=15, hours_to_close=24) == lv.EXIT_TAKE_PROFIT
    assert lv.decide_exit(entry_cents=10, mark_bid_cents=5, hours_to_close=24) == lv.EXIT_STOP_LOSS


def test_pre_close_flatten_wins_and_needs_no_mark():
    assert lv.decide_exit(entry_cents=40, mark_bid_cents=None,
                          hours_to_close=lv.FLATTEN_HOURS_BEFORE_CLOSE) == lv.EXIT_PRE_CLOSE
    # Even a position that is winning comes off before close: capital must not ride into
    # settlement lag.
    assert lv.decide_exit(entry_cents=40, mark_bid_cents=41, hours_to_close=0.5) == lv.EXIT_PRE_CLOSE


def test_no_mark_and_no_close_means_hold():
    assert lv.decide_exit(entry_cents=40, mark_bid_cents=None, hours_to_close=24) is None
    assert lv.decide_exit(entry_cents=40, mark_bid_cents=39, hours_to_close=None) is None


def test_exit_leg_joins_the_opposite_touch_but_never_locks_less_than_the_take_profit():
    # Held YES at 40: a NO bid at p realises 100 - 40 - p. Joining a 50c touch locks 10c.
    assert lv.exit_leg_price(entry_cents=40, best_opposite_bid=50) == 50
    # A touch that would lock less than +5c is capped there (EXIT_LEG_EDGE_CENTS = 5, §9.53).
    assert lv.EXIT_LEG_EDGE_CENTS == lv.TAKE_PROFIT_CENTS == 5
    assert lv.exit_leg_price(entry_cents=40, best_opposite_bid=65) == 55
    assert lv.exit_leg_price(entry_cents=40, best_opposite_bid=None) == 55
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
    # (§9.41); close window 3–72h -> 7–60 days (§9.42); leg cap 5c -> 10c (§9.46); 2 -> 10
    # markets, $50 -> $100 budget, 1 -> 3 markets per event (§9.56).
    assert (lv.MAX_CONTRACTS_PER_ORDER, lv.MAX_ORDER_DOLLARS, lv.MAX_OPEN_ORDERS,
            lv.MAX_STRATEGY_EXPOSURE_USD, lv.MAX_PRICE_CENTS) == (500, 10.00, 10, 100.00, 10)
    assert lv.MAX_MARKETS_PER_EVENT == 3
    assert (lv.MIN_HOURS_TO_CLOSE, lv.MAX_HOURS_TO_CLOSE, lv.FLATTEN_HOURS_BEFORE_CLOSE) == (
        168.0, 1440.0, 1.0)
    assert (lv.MIN_PROGRAM_HOURS_REMAINING, lv.QUOTE_TICKS_BEHIND_TOUCH) == (24.0, 1)
    # Exits: stop 0.50 / floor 1 and cheap side at the reference (§9.48); the scratch is retired
    # for a fixed +5c take-profit, which the exit leg also locks (§9.53).
    assert (lv.STOP_LOSS_FRACTION, lv.TAKE_PROFIT_CENTS) == (0.50, 5)
    assert (lv.STOP_MIN_DISTANCE_CENTS, lv.EXIT_LEG_EDGE_CENTS, lv.QUOTE_MODE) == (
        1, 5, "cheap_side")


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
    assert q.quantity == min(lv.MAX_CONTRACTS_PER_ORDER, int(lv.order_dollars(4) * 100 // 4))
    assert q.collateral_usd <= lv.order_dollars(q.leg.price_cents)
    assert q.max_loss_usd == q.collateral_usd


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


# --- §9.55: the event-mention universe and its pre-event cutoff ---------------------------------

def test_pre_event_cutoff_is_15_minutes_before_a_programme_that_ends_at_the_event():
    from datetime import datetime, timezone
    u = timezone.utc
    cut = lv.pre_event_cutoff(
        series="KXTRUMPMENTION", event_ticker="KXTRUMPMENTION-26OCT16",
        title="What will Donald Trump say during Midterm Rally at Dalton, Georgia?",
        program_end=datetime(2026, 10, 16, 22, tzinfo=u),
        close_time=datetime(2026, 10, 30, 14, tzinfo=u))
    assert cut == datetime(2026, 10, 16, 21, 45, tzinfo=u)


def test_pre_event_cutoff_falls_back_to_the_event_date_morning():
    # The programme runs to market close, so its end says nothing about the event's hour.
    from datetime import datetime, timezone
    u = timezone.utc
    cut = lv.pre_event_cutoff(
        series="KXWORLDNEWSMENTION", event_ticker="KXWORLDNEWSMENTION-26OCT14",
        title="What will any host or reporter say during ABC World News Tonight?",
        program_end=datetime(2026, 10, 28, 14, tzinfo=u),
        close_time=datetime(2026, 10, 28, 14, tzinfo=u))
    assert cut == datetime(2026, 10, 14, lv.EVENT_DATE_FALLBACK_HOUR_UTC, tzinfo=u)


def test_say_before_a_date_windows_and_non_mention_markets_are_outside_the_universe():
    assert not lv.is_event_mention("KXTRUMPSAY", "KXTRUMPSAY-26OCT19",
                                     'Will Trump say "Mamdani" before Oct 19, 2026?')
    assert not lv.is_event_mention("KXTRUMPSAYMONTH", "KXTRUMPSAYMONTH-26NOV01", "x")
    assert not lv.is_event_mention("KXAAAGASMINM", "KXAAAGASMINM-26OCT31", "Gas price")
    assert not lv.is_event_mention("KXTRUMPMENTION", "KXTRUMPMENTION", "no date")
    assert lv.is_event_mention("KXEARNINGSMENTIONNKE", "KXEARNINGSMENTIONNKE-26SEP30",
                                 "What will Nike say during its earnings call?")
    assert lv.pre_event_cutoff(series="KXTRUMPSAY", event_ticker="KXTRUMPSAY-26OCT19",
                                 title="Will Trump say X before Oct 19?", program_end=None,
                                 close_time=None) is None


# ------------------------------------------------------- §9.69


def test_a_bid_at_four_cents_or_more_gets_twenty_dollars():
    """Reward follows contracts: $10 already buys 333 at 3c, but only 100 at 10c."""
    cheap = _s(reference_price_by_side={lv.SIDE_YES: 20, lv.SIDE_NO: 3})
    assert (cheap.leg.price_cents, cheap.quantity) == (3, 333)        # $9.99
    five = _s(reference_price_by_side={lv.SIDE_YES: 20, lv.SIDE_NO: 5})
    assert (five.quantity, five.collateral_usd) == (400, 20.0)
    ten = _s(reference_price_by_side={lv.SIDE_YES: 20, lv.SIDE_NO: 10})
    assert (ten.quantity, ten.collateral_usd) == (200, 20.0)


def test_a_twenty_dollar_bid_still_fits_the_book_total():
    q = _s(reference_price_by_side={lv.SIDE_YES: 20, lv.SIDE_NO: 5},
           strategy_exposure_now_usd=lv.MAX_STRATEGY_EXPOSURE_USD - 15.0)
    assert isinstance(q, lv.Refusal) and q.code == lv.REFUSE_EXPOSURE_CAP


def test_youtube_views_markets_step_aside_before_their_month_ends():
    from datetime import datetime, timedelta, timezone
    kw = dict(series="KXYTVIEWSHIGH", event_ticker="KXYTVIEWSHIGH-TAT26NOV",
              title="Will Tate McRae have above 8M daily views at any point during October 2026?",
              program_end=datetime(2026, 11, 4, 15, tzinfo=timezone.utc),
              close_time=datetime(2026, 11, 4, 15, tzinfo=timezone.utc))
    end = datetime(2026, 11, 1, 4, tzinfo=timezone.utc)
    assert lv.pre_event_cutoff(**kw) == end
    assert lv.entry_cutoff(**kw) == end - timedelta(hours=lv.ENTRY_STEP_ASIDE_HOURS)
    assert lv.in_universe("KXYTVIEWSHIGH", "KXYTVIEWSHIGH-TAT26NOV", kw["title"])
    # A programme that ends first cuts it off sooner.
    early = datetime(2026, 10, 30, tzinfo=timezone.utc)
    assert lv.pre_event_cutoff(**{**kw, "program_end": early}) == early


def test_only_the_one_added_series_joins_the_universe():
    assert not lv.in_universe("KXYTVIEWSW", "KXYTVIEWSW-TAT26NOV", "weekly views")
    assert not lv.in_universe("KXRT", "KXRT-FILM26NOV", "Rotten Tomatoes")
    assert lv.pre_event_cutoff(series="KXRT", event_ticker="KXRT-FILM26NOV", title="x",
                               program_end=None, close_time=None) is None
    assert lv.in_universe("KXTRUMPMENTION", "KXTRUMPMENTION-26OCT27", "What will Trump say")


def test_a_views_threshold_is_not_a_shared_word():
    """Fill contagion links one WORD across events; '16.5M' for one artist says nothing of
    another artist's 16.5M."""
    assert lv.market_word("KXYTVIEWSHIGH-ARI26NOV-16.5M", "KXYTVIEWSHIGH-ARI26NOV") is None
    assert lv.market_word("KXTRUMPMENTION-26OCT19-CEAS", "KXTRUMPMENTION-26OCT19") == "CEAS"
