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


def test_a_filled_leg_shows_as_held_with_its_exit_plan_and_its_exit_bid_risks_nothing(session):
    # The 2026-10-12 Miller Lite case: NO 10c x100 filled, a YES 85c x100 exit bid resting.
    _setup(session)
    held = "KXMILLERLITEPOS-26NOV03-T84"
    session.add(m.LiveOrder(kalshi_order_id="o-ml", client_order_id="c-ml", market_ticker=held,
                            strategy=limm.LIVE_TAG, created_at=NOW - timedelta(hours=8),
                            side="no", action="buy", limit_price=10, quantity=100,
                            status="filled"))
    session.add(m.Fill(kalshi_fill_id="f-ml", kalshi_order_id="o-ml", market_ticker=held,
                       side="no", action="buy", price=10, quantity=100))
    session.add(m.Position(market_ticker=held, captured_at=NOW - timedelta(minutes=1), side="no",
                           quantity=-100, quantity_fp=-100, avg_price=10.0))
    session.add(m.LiveOrder(kalshi_order_id="o-leg", client_order_id="limmexitleg:x",
                            market_ticker=held, strategy=limm.LIVE_TAG,
                            created_at=NOW - timedelta(hours=2), side="yes", action="buy",
                            limit_price=85, quantity=100, status="resting"))
    session.flush()
    x = ib.build_incentive_block(session, limm.LIVE_TAG, NOW, ov._latest_snapshots)
    (p,) = x["positions"]
    assert (p["market"], p["side"], p["quantity"], p["entry_cents"], p["at_risk_usd"]) == (
        held, "no", 100, 10, 10.0)
    tp = 10 + limm.take_profit_distance_cents(10)
    stop = 10 - limm.stop_distance_cents(10)
    assert (p["take_profit_cents"], p["take_profit_usd"]) == (tp, (tp - 10) * 1.0)
    assert (p["stop_cents"], p["stop_usd"]) == (stop, (stop - 10) * 1.0)
    assert p["exit_order"] == {"side": "yes", "price_cents": 85, "quantity": 100, "locks_usd": 5.0}
    # The exit bid is not a waiting entry and is not money at risk.
    assert [r["market"] for r in x["resting"]] == [TICKER]
    assert (x["positions_at_risk_usd"], x["committed_usd"], x["at_risk_usd"]) == (10.0, 10.0, 20.0)
    assert x["slots_used"] == 2
    assert p["mark_cents"] is None and p["unrealized_usd"] is None   # no book for it yet


def test_a_held_leg_is_marked_at_its_side_bid_from_a_fresh_book(session):
    _setup(session)
    held = "KXHELD-MARK"
    session.add(m.LiveOrder(kalshi_order_id="o-h", client_order_id="c-h", market_ticker=held,
                            strategy=limm.LIVE_TAG, created_at=NOW - timedelta(hours=8),
                            side="no", action="buy", limit_price=10, quantity=100,
                            status="filled"))
    session.add(m.Fill(kalshi_fill_id="f-h", kalshi_order_id="o-h", market_ticker=held,
                       side="no", action="buy", price=10, quantity=100))
    session.add(m.Position(market_ticker=held, captured_at=NOW, side="no", quantity=-100,
                           quantity_fp=-100, avg_price=10.0))
    session.add(m.IncentiveMarketSnapshot(market_ticker=held, at=NOW - timedelta(minutes=5),
                                          book_valid=True, best_yes_bid=90, best_no_bid=6))
    session.flush()
    x = ib.build_incentive_block(session, limm.LIVE_TAG, NOW, ov._latest_snapshots)
    (p,) = x["positions"]
    assert (p["mark_cents"], p["value_usd"], p["unrealized_usd"]) == (6, 6.0, -4.0)
    assert x["positions_unrealized_usd"] == -4.0
    assert x["resting"][0]["days_left"] == 9.0


def test_a_one_cent_leg_has_no_stop_and_can_lose_only_its_cost(session):
    _setup(session)
    held = "KXTOP10-TSWIF-10"
    session.add(m.LiveOrder(kalshi_order_id="o-ts", client_order_id="c-ts", market_ticker=held,
                            strategy=limm.LIVE_TAG, created_at=NOW - timedelta(hours=8),
                            side="yes", action="buy", limit_price=1, quantity=500,
                            status="filled"))
    session.add(m.Fill(kalshi_fill_id="f-ts", kalshi_order_id="o-ts", market_ticker=held,
                       side="yes", action="buy", price=1, quantity=500))
    session.add(m.Position(market_ticker=held, captured_at=NOW, side="yes", quantity=500,
                           quantity_fp=500, avg_price=1.0))
    session.flush()
    (p,) = ib.build_incentive_block(session, limm.LIVE_TAG, NOW, ov._latest_snapshots)["positions"]
    assert (p["stop_cents"], p["stop_usd"], p["exit_order"]) == (None, -5.0, None)
    # A live book with nobody bidding our side marks it at 0, not "unpriced" (TSWIF, Oct 15).
    session.add(m.IncentiveMarketSnapshot(market_ticker=held, at=NOW - timedelta(minutes=5),
                                          book_valid=True, best_yes_bid=None, best_no_bid=99))
    session.flush()
    x = ib.build_incentive_block(session, limm.LIVE_TAG, NOW, ov._latest_snapshots)
    (p,) = x["positions"]
    assert (p["mark_cents"], p["no_bid"], p["ask_cents"]) == (0, True, 1)
    assert (p["value_usd"], p["unrealized_usd"]) == (0.0, -5.0)
    assert x["positions_unrealized_usd"] == -5.0
    assert p["awaiting_settlement"] is False


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
    # 2026-10-14 KXTRUMPMENTION-26OCT13, observed as a 331c residual at 10:29Z
    assert confirmed_reward(331, datetime(2026, 10, 14, 10, 29, tzinfo=timezone.utc))
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


# --- the book's overall P&L chart (`livedash.incentive_pnl`) ------------------------------

from kalshi_bot.livedash import incentive_pnl as ipnl  # noqa: E402

HELD = "KXHELD-CHART"


def _held_leg(s, *, settle_at=None):
    """NO 10c x100 filled 8h ago; the collector's book marks NO at 6c from 6h ago on."""
    s.add(m.LiveOrder(kalshi_order_id="o-hc", client_order_id="c-hc", market_ticker=HELD,
                      strategy=limm.LIVE_TAG, created_at=NOW - timedelta(hours=9), side="no",
                      action="buy", limit_price=10, quantity=100, status="filled"))
    s.add(m.Fill(kalshi_fill_id="f-hc", kalshi_order_id="o-hc", market_ticker=HELD,
                 filled_at=NOW - timedelta(hours=8), side="no", action="buy", price=10,
                 quantity=100))
    s.add(m.Position(market_ticker=HELD, captured_at=NOW - timedelta(hours=7, minutes=55),
                     side="no", quantity=-100, quantity_fp=-100, avg_price=10.0))
    for h in range(6, 0, -1):
        s.add(m.IncentiveMarketSnapshot(market_ticker=HELD, at=NOW - timedelta(hours=h),
                                        book_valid=True, best_yes_bid=90, best_no_bid=6))
    if settle_at is not None:
        s.add(m.Position(market_ticker=HELD, captured_at=settle_at, side="no", quantity=0,
                         quantity_fp=0, realized_pnl=-10.0))
    s.flush()


def _at(points, when):
    """The last point at or before `when`."""
    return [p for p in points if datetime.fromisoformat(p["at"]) <= when][-1]


def test_the_net_line_ends_on_the_cards_all_time_net(session):
    _setup(session)
    _held_leg(session)
    card = ib.build_incentive_block(session, limm.LIVE_TAG, NOW, ov._latest_snapshots)
    x = ipnl.build_book_pnl(session, now=NOW)
    last = x["points"][-1]
    assert last["net"] == pytest.approx(card["net_all_time_usd"])          # -7.79
    assert last["realized"] == pytest.approx(card["realized_all_time_usd"])
    assert last["rewards"] == pytest.approx(card["reward_credits_usd"])
    assert last["unrealized"] == pytest.approx(card["positions_unrealized_usd"])  # -4.00
    assert last["total"] == pytest.approx(-11.79)
    s = x["stats"]
    assert (s["net_usd"], s["rewards_usd"], s["reward_payouts"], s["fills"]) == (-7.79, 2.21, 1, 1)


def test_each_part_moves_only_when_its_record_exists(session):
    _setup(session)
    _held_leg(session)
    pts = ipnl.build_book_pnl(session, now=NOW)["points"]
    # The old market's -$10 lands at the snapshot that first shows it flat, not at its fill.
    assert _at(pts, NOW - timedelta(days=19, minutes=1))["realized"] == 0.0
    assert _at(pts, NOW - timedelta(days=19))["realized"] == -10.0
    # The reward lands when the ledger saw it.
    assert _at(pts, NOW - timedelta(days=5, minutes=1))["rewards"] == 0.0
    assert _at(pts, NOW - timedelta(days=5))["rewards"] == 2.21
    # Before the fill: nothing held. Filled but no book yet: a gap, not a move.
    before = _at(pts, NOW - timedelta(hours=8, minutes=1))
    assert (before["held_markets"], before["unrealized"], before["partial"]) == (0, 0.0, False)
    gap = _at(pts, NOW - timedelta(hours=8))
    assert (gap["held_markets"], gap["unrealized"], gap["partial"]) == (1, 0.0, True)
    marked = _at(pts, NOW - timedelta(hours=5))   # the book marks NO at 6c from 6h ago
    assert (marked["unrealized"], marked["partial"]) == (-4.0, False)


def test_a_settled_leg_leaves_the_open_line_and_joins_realized(session):
    _setup(session)
    _held_leg(session, settle_at=NOW - timedelta(hours=3))
    pts = ipnl.build_book_pnl(session, now=NOW)["points"]
    open_ = _at(pts, NOW - timedelta(hours=3, minutes=1))
    assert (open_["unrealized"], open_["realized"]) == (-4.0, -10.0)
    done = _at(pts, NOW - timedelta(hours=3))
    assert (done["unrealized"], done["held_markets"], done["realized"]) == (0.0, 0, -20.0)


def test_a_window_cuts_the_curve_but_not_the_all_time_figures(session):
    _setup(session)
    _held_leg(session)
    x = ipnl.build_book_pnl(session, now=NOW, since=NOW - timedelta(days=1), max_points=50)
    assert datetime.fromisoformat(x["points"][0]["at"]) == NOW - timedelta(days=1)
    assert x["stats"]["net_usd"] == -7.79 and x["stats"]["net_change_in_window_usd"] == 0.0
    # Events in the window only: the fill 8h ago, not the reward 5 days ago.
    assert [mk["kind"] for mk in x["markers"]] == ["fill"]
    assert x["markers"][0]["market"] == HELD and x["markers"][0]["cost_usd"] == 10.0
    assert len(x["points"]) <= 50 + 3


def test_daily_net_and_the_estimated_pace(session):
    _setup(session)
    x = ipnl.build_book_pnl(session, now=NOW)
    daily = {d["day"]: d for d in x["daily"]}
    assert x["daily"][0]["day"] == (NOW - timedelta(days=20)).date().isoformat()
    assert x["daily"][-1]["day"] == NOW.date().isoformat()
    assert daily[(NOW - timedelta(days=19)).date().isoformat()]["net_usd"] == -10.0
    assert daily[(NOW - timedelta(days=5)).date().isoformat()]["rewards_usd"] == 2.21
    s = x["stats"]
    # Only the reward falls in the last 7 days: $2.21 a week, ~$9.47 a month.
    assert s["pace_week_net_usd"] == 2.21
    assert s["est_pace_per_month_usd"] == pytest.approx(2.21 * 30 / 7, abs=0.01)
    assert s["goal_per_month_usd"] == 100.0


def test_a_book_with_no_orders_draws_nothing(session):
    x = ipnl.build_book_pnl(session, now=NOW)
    assert x["points"] == [] and x["stats"] is None


def test_the_cache_reloads_after_its_ttl(session):
    _setup(session)
    cache = ipnl.BookPnlCache(ttl=0.0)
    assert cache.build(session, now=NOW)["stats"]["fills"] == 0
    _held_leg(session)
    assert cache.build(session, now=NOW)["stats"]["fills"] == 1
