"""scripts/limm_paper_strategies_read.py — pins the pre-registered constants, the $1 minimum, the
fill-cost sources, the labels and the pairwise rule (docs/LIMM_PAPER_STRATEGIES.md); runs end to
end on synthetic rows and on rows the real collector wrote."""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import sys

from sqlalchemy import select

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("limm_paper_strategies_read",
                                               _SCRIPTS / "limm_paper_strategies_read.py")
rd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rd)  # type: ignore[union-attr]

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 11, 10, tzinfo=UTC)


def test_constants_match_the_preregistration():
    from kalshi_bot.liquidity_incentive import fills as fm
    from kalshi_bot.liquidity_incentive import paper_arms as pa

    assert rd.ARMS == pa.ARMS
    assert set(rd.MODELS) == set(fm.ARM_FILL_MODELS)
    assert (rd.PRIMARY_MODEL, rd.STRESS_MODEL) == ("conservative", "whole_order")
    assert rd.CALIBRATION == pa.DEFAULT_RULES.calibration == 1.34
    assert (rd.PROMISING_USD, rd.MIN_BID_DAYS, rd.MIN_EVENTS, rd.KALSHI_MINIMUM_USD) == (0.15, 100.0, 10, 1.00)
    assert rd.READ_NOT_BEFORE >= "2026-11-24" and rd.RECORDING_START < rd.READ_NOT_BEFORE


def test_full_read_refuses_before_the_read_date(capsys, monkeypatch):
    monkeypatch.setattr(rd, "READ_NOT_BEFORE", "2999-01-01T00:00:00+00:00")
    assert rd.main([]) == 2
    assert "REFUSED" in capsys.readouterr().out


def test_fill_cost_sources():
    assert rd.fill_cost(3, 1000, 1, None) == (20.0, "mark24h")
    assert rd.fill_cost(3, 1000, None, -30.0) == (30.0, "settlement")
    assert rd.fill_cost(4, 500, None, None) == (10.0, "unmarked")
    assert rd.fill_cost(4, 0, None, None) == (0.0, "none")


def test_kalshi_minimum_is_per_programme():
    paid, n, clear = rd.apply_minimum({("A", "s1"): 0.99, ("A", "s2"): 1.0, ("B", ""): 3.0})
    assert paid == {("A", "s1"): 0.0, ("A", "s2"): 1.0, ("B", ""): 3.0} and (n, clear) == (3, 2)


def test_labels_and_decisions():
    good = {"R": 0.30, "se": 0.05, "events": 12, "md": 150}
    assert rd.label(good, 150, 12, 0.1) == "PROMISING"
    assert rd.label(good, 150, 12, -0.01) == "FRAGILE"
    assert rd.label(good, 99, 12, 0.1) == "HOLD" and rd.label(good, 150, 9, 0.1) == "HOLD"
    assert rd.label({"R": 0.30, "se": 0.2, "events": 12, "md": 150}, 150, 12, 0.1) == "NOT-PROMISING"
    assert rd.decides({"R": 0.2, "se": 0.05}) == "A-BETTER"
    assert rd.decides({"R": -0.2, "se": 0.05}) == "B-BETTER"
    assert rd.decides({"R": 0.05, "se": 0.05}) == "UNDECIDED" and rd.decides(None) == "UNDECIDED"


def _q(qid, arm, tk, *, price=3, size=10, rest_h=240.0, prog="2026-11-09T00:00:00+00:00"):
    meta = {"arm": arm, "side": "yes", "event": tk.rsplit("-", 1)[0], "programme_start": prog}
    qty = int(size * 100 // price)
    return (qid, arm, tk, T0, rest_h * 3600, size, price, 0, qty, price * qty / 100, meta)


def test_end_to_end_on_synthetic_rows():
    quotes, outcomes, marks, hours = [], [], [], []
    qid = 0
    for e in range(12):
        tk = f"KXE{e}-26-A"
        for arm, reward in (("S2-best", 2.0), ("S2-behind", 1.0)):
            qid += 1
            quotes.append(_q(qid, arm, tk))
            for model in rd.MODELS:
                filled = arm == "S2-best" and e == 0 and model in ("whole_order", "optimistic")
                outcomes.append((qid, model, reward, "yes" if filled else None,
                                 333 if filled else None, None))
                if filled:
                    marks.append((qid, model, 3, 1))
            hours.append((qid, {"seconds_scored": 3600, "seconds_qualifying": 3600, "seconds_thin": 600}))
    # One programme under $1 (0.5 x 1.34 = 0.67) counts $0.
    qid += 1
    quotes.append(_q(qid, "S1", "KXSMALL-26-A", rest_h=24))
    outcomes += [(qid, m, 0.5, None, None, None) for m in rd.MODELS]
    res = rd.analyse(quotes, outcomes, marks, hours)
    best = res["arms"]["S2-best"]
    p = best["models"]["conservative"]
    assert p["bid_days_10usd"] == 120.0 and p["events"] == 12 and p["programmes_clearing_1usd"] == 12
    assert round(p["net_per_10usd_bid_day"]["R"], 4) == round(12 * 2.0 * 1.34 / 120, 4)
    w = best["models"]["whole_order"]
    assert w["fills"] == 1 and w["fill_cost_24h"] == round(333 * 2 / 100, 4)
    assert w["fill_cost_sources"] == {"mark24h": 1}
    assert best["thin_share_of_scored_time"] == 600 / 3600
    assert res["arms"]["S1"]["models"]["conservative"]["reward_calibrated_after_min"] == 0.0
    pair = res["pairs"]["S2-best - S2-behind [conservative]"]
    assert pair["events"] == 12 and pair["decision"] in ("A-BETTER", "UNDECIDED")
    text = rd.render(res)
    assert "S2-best" in text and "pairwise" in text
    c = rd.counts(quotes)
    assert c["S2-best"] == {"quotes": 12, "markets": 12, "bid_hours": 2880.0}


def test_reads_the_rows_the_collector_writes(settings):
    """The read script must understand what `paper_arms` + the collector actually persist."""
    import test_limm_paper_arms as arms_test

    from kalshi_bot import db
    from kalshi_bot import models as m
    from kalshi_bot.liquidity_incentive import paper_arms as pa

    clock = arms_test._Clock()
    st = arms_test._up(settings, clock, liquidity_incentive_paper_arms_enabled=True)
    st.tick()
    st.tick(clock.tick(400))
    clock.tick(30)
    st.handle_message(arms_test._trade("KXT-26-A", 3, 2500, "no", "t1"))
    st.tick(clock.tick(120))
    st.stop()
    with db.session_scope() as s:
        qs = s.scalars(select(m.IncentiveShadowQuote).where(m.IncentiveShadowQuote.policy.in_(pa.ARMS))).all()
        quotes = [(q.id, q.policy, q.market_ticker, q.placed_at, q.rest_seconds or 0, q.capital_tier_usd,
                   q.yes_bid, q.no_bid, q.qty_per_side, q.capital_required_usd, q.book_json.get("arm"))
                  for q in qs]
        outcomes = [(o.quote_id, o.fill_model, o.est_reward_usd, o.single_leg_side, o.single_leg_qty,
                     o.settlement_pnl_usd)
                    for o in s.scalars(select(m.IncentiveShadowOutcome).where(
                        m.IncentiveShadowOutcome.policy.in_(pa.ARMS))).all()]
        hours = [(e.quote_id, e.detail_json) for e in s.scalars(select(m.IncentiveShadowEvent).where(
            m.IncentiveShadowEvent.kind == "arm_hour")).all()]
    res = rd.analyse(quotes, outcomes, [], hours)
    s1 = res["arms"]["S1"]["models"]
    assert s1["whole_order"]["fills"] == 1 and s1["whole_order"]["fill_cost_sources"] == {"unmarked": 1}
    assert s1["conservative"]["programmes"] == 1 and s1["conservative"]["events"] == 1
    assert res["arms"]["S1"]["label"] == "HOLD"
    assert res["arms"]["S1"]["qualifying_share_of_scored_time"] == 1.0
