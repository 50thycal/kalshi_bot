"""Incentive paper strategies S1-S4 (DEC-043/044): the pure arm rules, the whole-order fill
model, and the collector recording one-sided paper bids end to end. No orders anywhere."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot.liquidity_incentive import collector as c
from kalshi_bot.liquidity_incentive import fills as fm
from kalshi_bot.liquidity_incentive import paper_arms as pa
from kalshi_bot.liquidity_incentive import quotes as qp

T0 = datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ whole-order fill model


def test_whole_order_fills_everything_once_the_queue_reaches_us():
    leg = fm.ShadowLeg("yes", 3, 600.0, T0, 2000.0, models=fm.ARM_FILL_MODELS)
    assert leg.filled[fm.MODEL_WHOLE_ORDER] == 0.0
    # 1,500 prints at our level: still 500 ahead under every queue model -> nothing.
    got = leg.on_trade(yes_price_cents=3, count=1500, taker_outcome_side="no", at=T0)
    assert set(got) == {fm.MODEL_OPTIMISTIC}
    # 600 more: 100 reach us. Queue-aware takes 100, whole-order takes all 600.
    got = leg.on_trade(yes_price_cents=3, count=600, taker_outcome_side="no", at=T0)
    assert got[fm.MODEL_QUEUE_AWARE] == 100 and got[fm.MODEL_WHOLE_ORDER] == 600
    assert leg.is_full(fm.MODEL_WHOLE_ORDER) and not leg.is_full(fm.MODEL_QUEUE_AWARE)


def test_whole_order_is_opt_in_and_a_sweep_through_fills_it():
    two_sided = fm.ShadowLeg("yes", 3, 10.0, T0, 50.0)
    assert fm.MODEL_WHOLE_ORDER not in two_sided.filled          # pairs are untouched
    leg = fm.ShadowLeg("no", 97, 400.0, T0, 900.0, models=fm.ARM_FILL_MODELS)
    got = leg.on_trade(yes_price_cents=98, count=5, taker_outcome_side="yes", at=T0)
    assert got[fm.MODEL_WHOLE_ORDER] == 400 and got[fm.MODEL_QUEUE_AWARE] == 5


# ------------------------------------------------------------------ pure arm rules


def _cand(ticker="KXA-26-X1", *, event=None, series=None, yes=3, no=95, rate_yes=0.05,
          rate_no=0.0, q=True, pool_day=100.0, start=T0 - timedelta(hours=1),
          end=T0 + timedelta(days=7), close=T0 + timedelta(days=30), at=T0):
    return pa.Candidate(
        ticker=ticker, event=pa.event_of(ticker, event), series=pa.series_of(ticker, series),
        at=at, start=start, end=end, close_time=close, pool_day_usd=pool_day,
        yes=pa.SideView(yes, rate_yes, q, 0.1), no=pa.SideView(no, rate_no, q, 0.1))


def test_cheap_qualifying_side_is_picked():
    r = pa.DEFAULT_RULES
    assert _cand(yes=3, no=95).best_side(r) == "yes"
    assert _cand(yes=60, no=8, rate_yes=0.0, rate_no=0.02).best_side(r) == "no"
    assert _cand(yes=11, no=89).best_side(r) is None                 # above 10c
    assert _cand(q=False).best_side(r) is None                       # would not qualify


def test_s1_ranks_by_daily_pay_caps_events_and_sizes_by_pool():
    sel = pa.ArmSelector()
    cands = {}
    for i in range(5):        # five markets of one event: only three may enter
        t = f"KXBIG-26NOV-{i}"
        cands[t] = _cand(t, pool_day=200.0)
    cands["KXSMALL-26-X"] = _cand("KXSMALL-26-X", pool_day=5.0)
    sel.select(cands, T0)
    chosen = set(sel.selected[pa.S1])
    assert len([t for t in chosen if t.startswith("KXBIG")]) == 3 and "KXSMALL-26-X" in chosen
    big = sorted(t for t in chosen if t.startswith("KXBIG"))[0]
    q = sel.desired(pa.ARM_S1, sel.selected[pa.S1][big], cands[big], T0)
    assert (q.size_usd, q.price, q.qty) == (20, 3, 666)
    q = sel.desired(pa.ARM_S1, sel.selected[pa.S1]["KXSMALL-26-X"], cands["KXSMALL-26-X"], T0)
    assert q.size_usd == 10


def test_s2_arms_share_markets_and_behind_is_one_tick_back_but_never_below_1c():
    sel = pa.ArmSelector()
    a, b = _cand("KXA-26-1", yes=4), _cand("KXB-26-1", yes=1, rate_yes=0.04)
    sel.select({a.ticker: a, b.ticker: b}, T0)
    s2 = sel.selected[pa.S2]
    assert set(s2) == {a.ticker, b.ticker}
    best = sel.desired(pa.ARM_S2_BEST, s2[a.ticker], a, T0)
    behind = sel.desired(pa.ARM_S2_BEHIND, s2[a.ticker], a, T0)
    assert (best.price, behind.price) == (4, 3) and best.qty == 250 and behind.qty == 333
    assert sel.desired(pa.ARM_S2_BEHIND, s2[b.ticker], b, T0).price == 1
    assert sel.desired(pa.ARM_S2_BEST, s2[b.ticker], b, T0).qty == 1000      # capped


def test_selection_is_sticky_and_drops_markets_that_leave_the_cheap_side():
    sel = pa.ArmSelector()
    a = _cand("KXA-26-1", yes=4, rate_yes=0.01)
    sel.select({a.ticker: a}, T0)
    better = _cand("KXZ-26-1", yes=2, rate_yes=0.5)
    sel.rules = pa.ArmRules(max_markets=((pa.S1, 1), (pa.S2, 1), (pa.S3, 1), (pa.S4, 1)))
    sel.select({a.ticker: a, better.ticker: better}, T0 + timedelta(hours=1))
    assert set(sel.selected[pa.S2]) == {a.ticker}            # a better market does not evict
    moved = _cand("KXA-26-1", yes=40, rate_yes=0.01)
    dropped = sel.select({moved.ticker: moved, better.ticker: better}, T0 + timedelta(hours=2))
    assert dropped[pa.S2] == [a.ticker] and set(sel.selected[pa.S2]) == {better.ticker}


def test_every_arm_leaves_48h_before_close():
    sel = pa.ArmSelector()
    late = _cand(close=T0 + timedelta(hours=47))
    sel.select({late.ticker: late}, T0)
    assert not any(sel.selected[s] for s in pa.STRATEGIES)


def test_s3_universe_and_the_aside_pull_rules():
    sel = pa.ArmSelector()
    emp = _cand("KXWAAEROEMP-27FEB28-T1", series="KXWAAEROEMP")
    gas = _cand("KXAAAGASM-26NOV30-3.20", series="KXAAAGASM")
    ski = _cand("KXASPENMTNOPEN-27-29NOV26", series="KXASPENMTNOPEN",
                close=T0 + timedelta(days=20))
    other = _cand("KXRT-26-X", series="KXRT")
    cands = {x.ticker: x for x in (emp, gas, ski, other)}
    sel.select(cands, T0)
    s3 = sel.selected[pa.S3]
    assert set(s3) == {emp.ticker, gas.ticker, ski.ticker}
    # BLS: off from 2 h before the 11-20 15:00Z release to 48 h after; the control stays on.
    rel = pa.BLS_RELEASES_UTC[0]
    for at, pulled in ((rel - timedelta(hours=2, minutes=1), False), (rel - timedelta(hours=2), True),
                       (rel + timedelta(hours=47), True), (rel + timedelta(hours=48), False)):
        q = sel.desired(pa.ARM_S3_ASIDE, s3[emp.ticker], emp, at)
        assert isinstance(q, pa.Pull) is pulled, at
        assert isinstance(sel.desired(pa.ARM_S3_CONTROL, s3[emp.ticker], emp, at), pa.ArmQuote)
    # Gas: off 12-16Z every day.
    noon = T0.replace(hour=12)
    assert sel.desired(pa.ARM_S3_ASIDE, s3[gas.ticker], gas, noon).reason == pa.PULL_GAS
    assert isinstance(sel.desired(pa.ARM_S3_ASIDE, s3[gas.ticker], gas, T0.replace(hour=16)), pa.ArmQuote)
    # One-event ladder: our side falling to 1c after being higher pulls it for good.
    s = s3[ski.ticker]
    sel.observe(s, ski)                                       # 3c seen
    at1 = _cand(ski.ticker, series="KXASPENMTNOPEN", yes=1, close=ski.close_time)
    sel.observe(s, at1)
    assert sel.desired(pa.ARM_S3_ASIDE, s, at1, T0).reason == pa.PULL_ONE_EVENT_DUMP
    assert isinstance(sel.desired(pa.ARM_S3_CONTROL, s, at1, T0), pa.ArmQuote)
    # ...and the last three days before close are off too.
    fresh = pa.Selection(ski.ticker, ski.event, "yes", T0, ski.start)
    late = ski.close_time - timedelta(days=3)
    assert sel.desired(pa.ARM_S3_ASIDE, fresh, ski, late).reason == pa.PULL_ONE_EVENT_LATE


def test_s4_takes_the_harvest_band_and_bursts_until_the_threshold():
    # $10 earns 0.01 $/h uncalibrated; 48 h to the step-aside x 1.34 = $0.64: in the band.
    inband = _cand("KXH-26-1", rate_yes=0.01, close=T0 + timedelta(hours=96), end=T0 + timedelta(days=9))
    rich = _cand("KXR-26-1", rate_yes=1.0)
    sel = pa.ArmSelector()
    sel.select({inband.ticker: inband, rich.ticker: rich}, T0)
    assert set(sel.selected[pa.S4]) == {inband.ticker}
    s = sel.selected[pa.S4][inband.ticker]
    q = sel.desired(pa.ARM_S4_BURST, s, inband, T0)
    assert (q.size_usd, q.qty) == (50, 1666)
    assert sel.desired(pa.ARM_S4_FLAT, s, inband, T0).size_usd == 10
    sel.add_burst_reward(s.ticker, s.programme_start, 0.5)        # x1.34 = 0.67
    assert isinstance(sel.desired(pa.ARM_S4_BURST, s, inband, T0), pa.ArmQuote)
    sel.add_burst_reward(s.ticker, s.programme_start, 0.45)       # total 1.27 >= 1.25
    assert sel.desired(pa.ARM_S4_BURST, s, inband, T0).reason == pa.PULL_BURST_DONE
    assert sel.desired(pa.ARM_S4_FLAT, s, inband, T0).size_usd == 10
    flat_after = pa.ArmSelector(rules=pa.ArmRules(s4_after_burst="flat"))
    flat_after.bursts = sel.bursts
    assert flat_after.desired(pa.ARM_S4_BURST, s, inband, T0).size_usd == 10


def test_s4_burst_window_from_the_age_study():
    r = pa.ArmRules(s4_burst_window_hours=24.0)
    sel = pa.ArmSelector(rules=r)
    old = _cand("KXO-26-1", rate_yes=0.01, start=T0 - timedelta(hours=30),
                close=T0 + timedelta(hours=96), end=T0 + timedelta(days=9))
    young = _cand("KXY-26-1", rate_yes=0.01, start=T0 - timedelta(hours=2),
                  close=T0 + timedelta(hours=96), end=T0 + timedelta(days=9))
    sel.select({old.ticker: old, young.ticker: young}, T0)
    assert set(sel.selected[pa.S4]) == {young.ticker}             # only inside the window
    s = sel.selected[pa.S4][young.ticker]
    assert sel.desired(pa.ARM_S4_BURST, s, young, T0).size_usd == 50
    assert sel.desired(pa.ARM_S4_BURST, s, young, T0 + timedelta(hours=23)).reason == pa.PULL_BURST_WINDOW


# ------------------------------------------------------------------ collector end to end


class _Clock:
    def __init__(self, start=T0):
        self.now = start

    def __call__(self):
        return self.now

    def tick(self, seconds):
        self.now = self.now + timedelta(seconds=seconds)
        return self.now


class _ReadClient:
    def __init__(self):
        self.programs = [{"id": "p1", "market_id": "m1", "market_ticker": "KXT-26-A",
                          "incentive_type": "liquidity", "incentive_description": "x",
                          "start_date": (T0 - timedelta(hours=1)).isoformat(),
                          "end_date": (T0 + timedelta(hours=23)).isoformat(),
                          "period_reward": 1_000_000, "paid_out": False,
                          "discount_factor_bps": 5000, "target_size_fp": "1000.00"}]
        self.market = {"market": {"title": "T", "status": "active", "event_ticker": "KXT-26",
                                  "close_time": (T0 + timedelta(days=10)).isoformat()}}
        self.ws_url = "wss://example.test/ws"

    def iter_incentive_programs(self, **kw):
        if kw.get("status", "active") == "paid_out":
            return iter(())
        return iter(self.programs)

    def get_market(self, ticker):
        return self.market

    def get_series(self, s):
        return {"series": {"fee_type": "quadratic", "fee_multiplier": 1.0}}

    def ws_headers(self):
        return {}


def _snapshot(ticker, yes, no, sid=1, seq=1):
    return {"type": "orderbook_snapshot", "sid": sid, "seq": seq,
            "msg": {"market_ticker": ticker,
                    "yes_dollars_fp": [[f"{p / 100:.2f}", f"{q:.2f}"] for p, q in yes],
                    "no_dollars_fp": [[f"{p / 100:.2f}", f"{q:.2f}"] for p, q in no]}}


def _trade(ticker, yes_px, count, taker, tid, seq=1):
    return {"type": "trade", "sid": 2, "seq": seq,
            "msg": {"market_ticker": ticker, "trade_id": tid, "yes_price_dollars": f"{yes_px / 100:.2f}",
                    "no_price_dollars": f"{(100 - yes_px) / 100:.2f}", "count_fp": f"{count:.2f}",
                    "taker_outcome_side": taker, "ts_ms": 2}}


def _up(settings, clock, **overrides):
    db.init_engine(settings.database_url)
    db.create_all()
    settings.liquidity_incentive_capital_tiers = "25"
    for k, v in overrides.items():
        setattr(settings, k, v)
    st = c.ShadowState(_ReadClient(), settings, db.session_scope, clock=clock)
    st.refresh_programs(force=True)
    st.on_connected()
    # Cheap YES at 3c with 2,000 resting (Target 1,000); NO bid 95c (yes-scale 5) x 2,000.
    st.handle_message(_snapshot("KXT-26-A", yes=[(3, 2000)], no=[(5, 2000)]))
    return st


def test_arms_are_off_by_default(settings):
    assert settings.liquidity_incentive_paper_arms_enabled is False
    clock = _Clock()
    st = _up(settings, clock)
    st.tick()
    st.tick(clock.tick(400))
    with db.session_scope() as s:
        policies = set(s.scalars(select(m.IncentiveShadowQuote.policy)).all())
    assert policies and policies <= set(qp.POLICIES)


def test_collector_records_one_sided_arms_under_four_fill_models(settings):
    clock = _Clock()
    st = _up(settings, clock, liquidity_incentive_paper_arms_enabled=True)
    st.tick()                                                    # candidates cached, no selection
    with db.session_scope() as s:
        assert not s.scalars(select(m.IncentiveShadowQuote).where(
            m.IncentiveShadowQuote.policy.in_(pa.ARMS))).all()
    st.tick(clock.tick(c.ARM_FIRST_SELECT_AFTER_SECONDS + 1))
    with db.session_scope() as s:
        arms = {q.policy: q for q in s.scalars(select(m.IncentiveShadowQuote).where(
            m.IncentiveShadowQuote.policy.in_(pa.ARMS))).all()}
        sel_ev = s.scalars(select(m.IncentiveCollectorEvent).where(
            m.IncentiveCollectorEvent.kind == c.EV_ARM_SELECT)).one()
    # $100/day pool: S1 at $20. S2 both arms. Not an S3 series; far above the S4 band.
    assert set(arms) == {pa.ARM_S1, pa.ARM_S2_BEST, pa.ARM_S2_BEHIND}
    assert sel_ev.detail_json["selected"]["S1"] == ["KXT-26-A"]
    s1 = arms[pa.ARM_S1]
    assert (s1.yes_bid, s1.no_bid, s1.capital_tier_usd, float(s1.qty_per_side)) == (3, 0, 20, 666)
    assert s1.book_json["arm"]["side"] == "yes" and s1.book_json["arm"]["qualifies"] is True
    assert 0 < s1.book_json["arm"]["share"] < 0.5 and s1.est_reward_per_hour_usd > 0
    assert (arms[pa.ARM_S2_BEHIND].yes_bid, float(arms[pa.ARM_S2_BEHIND].qty_per_side)) == (2, 500)
    # A taker sells 2,500 YES at 3c: 500 get past the 2,000 ahead.
    clock.tick(30)
    st.handle_message(_trade("KXT-26-A", 3, 2500, "no", "t1"))
    pair = st._arm_pair(st.markets["KXT-26-A"], pa.ARM_S1)
    assert pair.yes_leg.filled[fm.MODEL_QUEUE_AWARE] == 500
    assert pair.yes_leg.filled[fm.MODEL_WHOLE_ORDER] == 666
    assert not st._arm_pair(st.markets["KXT-26-A"], pa.ARM_S2_BEHIND).yes_leg.filled[fm.MODEL_OPTIMISTIC]
    # 24 h markout is scheduled for arm fills only.
    assert any(pm.horizon == 86400 for pm in st.pending_marks)
    # Accrue over a requote, then stop: outcomes under all four models, plus the hour's log.
    st.tick(clock.tick(120))
    st.stop()
    with db.session_scope() as s:
        outs = s.scalars(select(m.IncentiveShadowOutcome).where(
            m.IncentiveShadowOutcome.quote_id == s1.id)).all()
        hour = s.scalars(select(m.IncentiveShadowEvent).where(
            m.IncentiveShadowEvent.quote_id == s1.id,
            m.IncentiveShadowEvent.kind == c.EV_ARM_HOUR)).one()
        pair_outs = s.scalars(select(m.IncentiveShadowOutcome).where(
            m.IncentiveShadowOutcome.policy.in_(qp.POLICIES))).all()
    by = {o.fill_model: o for o in outs}
    assert set(by) == set(fm.ARM_FILL_MODELS)
    assert float(by[fm.MODEL_WHOLE_ORDER].yes_filled_qty) == 666
    assert float(by[fm.MODEL_QUEUE_AWARE].yes_filled_qty) == 500
    assert by[fm.MODEL_WHOLE_ORDER].single_leg_side == "yes" and float(by[fm.MODEL_WHOLE_ORDER].no_filled_qty) == 0
    assert float(by[fm.MODEL_CONSERVATIVE].est_reward_usd) > 0
    assert hour.detail_json["arm"] == pa.ARM_S1 and hour.detail_json["seconds_qualifying"] > 0
    assert {o.fill_model for o in pair_outs} == set(fm.FILL_MODELS)       # pairs: still three


def test_s4_burst_progress_survives_a_restart(settings):
    clock = _Clock()
    st = _up(settings, clock, liquidity_incentive_paper_arms_enabled=True)
    start = T0 - timedelta(hours=1)
    with db.session_scope() as s:
        s.add(m.IncentiveShadowOutcome(
            quote_id=1, market_ticker="KXT-26-A", policy=pa.ARM_S4_BURST, capital_tier_usd=50,
            fill_model=fm.MODEL_CONSERVATIVE, placed_at=T0 - timedelta(minutes=30), ended_at=T0,
            outcome=fm.OUTCOME_NONE, yes_filled_qty=0, no_filled_qty=0, matched_pairs=0,
            est_reward_usd=1.0))
    sel = pa.Selection("KXT-26-A", "KXT-26", "yes", T0, start)
    st._restore_burst(sel)
    b = st.arms.burst("KXT-26-A", start)
    assert round(b.accrued_calibrated_usd, 2) == 1.34 and b.done
