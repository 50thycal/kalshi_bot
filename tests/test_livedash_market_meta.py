"""Market tag classification and capital-deployed accounting.

Ticker prefixes here are drawn from a live production query against mmsell10's
actual traded universe (727 distinct tickers, 40 distinct series prefixes), not
invented — see kalshi_bot/livedash/market_meta.py's docstring for the sourcing.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kalshi_bot import models as m
from kalshi_bot.livedash import legs, market_meta
from kalshi_bot.livedash.market_meta import classify, classify_many
from kalshi_bot.models import Base

T0 = datetime(2026, 7, 26, 21, 0, tzinfo=timezone.utc)


def _session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)()


# ---------------------------------------------------------------------------
# Classification — real production prefixes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ticker,category,subject_contains", [
    ("KXBTCD-26JUL2717-T65249.99", "Crypto", "BTC"),
    ("KXBTCMAXMON-26JUL27-X", "Crypto", "BTC"),
    ("KXWTIW-26JUL27-X", "Commodities", "Oil"),
    ("KXBRENTW-26JUL27-X", "Commodities", "Brent"),
    ("KXMLBHRDERBY-26JUL27-X", "Sports", "MLB"),
    ("KXMLBHRDERBY500-26JUL27-X", "Sports", "MLB"),
    ("KXMLBTOTAL-26JUL27-X", "Sports", "MLB"),
    ("KXWNBASPREAD-26JUL27-X", "Sports", "WNBA"),
    ("KXATPCHALLENGERMATCH-26JUL27-X", "Sports", "ATP"),
    ("KXWTAMATCH-26JUL27-X", "Sports", "WTA"),
    ("KXT20MATCH-26JUL27-X", "Sports", "cricket"),
    ("KXODIMATCH-26JUL27-X", "Sports", "cricket"),
    ("KXTESTMATCH-26JUL251100PAKWI-TIE", "Sports", "cricket"),
    ("KXPGATOP10-26JUL27-X", "Sports", "PGA"),
    ("KXLIVTOUR-26JUL27-X", "Sports", "golf"),
    ("KXUFCMOV-26JUL27-X", "Sports", "MMA"),
    ("KXMLSGAME-26JUL27-X", "Sports", "MLS"),
    ("KXLIGAMXGAME-26JUL27-X", "Sports", "Liga MX"),
    ("KXNPBGAME-26JUL27-X", "Sports", "NPB"),
    ("KXWCGOAL-26JUL27-X", "Sports", "World Cup"),
    ("KXWCFIRSTSONG-26JUL27-X", "Sports", "World Cup"),
])
def test_known_prefixes_classify_confidently(ticker, category, subject_contains):
    tag = classify(ticker)
    assert tag.category == category
    assert subject_contains.lower() in (tag.subject or "").lower()
    # the raw prefix is always present regardless of classification success
    assert tag.series == ticker.split("-", 1)[0]


@pytest.mark.parametrize("ticker", [
    "KXAAAGASM-26JUL27-X",
    "KXTRUMPSAYMONTH-26JUL27-X",
    "KXRANKLISTSONGSPOTUSA-26JUL27-X",
    "KXRT-26JUL27-X",
])
def test_unrecognized_prefixes_are_labelled_other_not_guessed(ticker):
    """These are real observed production series this module has no confirmed
    meaning for. It must say so rather than invent a category."""
    tag = classify(ticker)
    assert tag.category == market_meta.OTHER
    assert tag.subject is None
    # the label falls back to the raw series so nothing is hidden
    assert tag.label == tag.series


def test_market_type_suffix_is_derived_where_recognizable():
    hr = classify("KXMLBHR-26JUL27-X")
    assert hr.market_type == "Home run"
    spread = classify("KXWNBASPREAD-26JUL27-X")
    assert spread.market_type == "Spread"
    derby = classify("KXMLBHRDERBY-26JUL27-X")
    assert derby.market_type == "Home run derby"


def test_test_match_does_not_repeat_match_in_the_label():
    tag = classify("KXTESTMATCH-26JUL251100PAKWI-TIE")
    assert tag.label.count("match") + tag.label.count("Match") == 1


def test_classification_is_deterministic():
    a = classify("KXMLBHRDERBY-26JUL27-X")
    b = classify("KXMLBHRDERBY-26AUG02-Y")  # different specific market, same series
    assert (a.category, a.subject, a.market_type) == (b.category, b.subject, b.market_type)


def test_classify_many_prefers_real_kalshi_title_when_available():
    session = _session()
    session.add(m.Market(ticker="KXMLBHRDERBY-26JUL27-X", title="Yankees Home Run Derby",
                         category="Sports"))
    session.flush()
    out = classify_many(session, ["KXMLBHRDERBY-26JUL27-X", "KXBTCD-26JUL27-Y"])
    real = out["KXMLBHRDERBY-26JUL27-X"]
    assert real.source == "kalshi" and real.title == "Yankees Home Run Derby"
    inferred = out["KXBTCD-26JUL27-Y"]
    assert inferred.source == "inferred" and inferred.category == "Crypto"


def test_classify_many_handles_empty_input():
    session = _session()
    assert classify_many(session, []) == {}
    assert classify_many(session, [None, ""]) == {}


def test_to_dict_is_json_safe_and_complete():
    d = classify("KXBTCD-26JUL27-X").to_dict()
    for key in ("ticker", "series", "category", "subject", "market_type", "label",
                "source", "title"):
        assert key in d


# ---------------------------------------------------------------------------
# Capital deployed
# ---------------------------------------------------------------------------


def _pos(ticker, cost, status="open", entry_at=None):
    p = legs.LegPosition(ticker=ticker, environment="live", status=status,
                         cost_basis_usd=cost, quantity=2, entry_price_cents=93,
                         entry_at=entry_at or T0)
    return p


def test_capital_deployed_sums_entry_cost_across_open_and_closed():
    leg = legs.Leg(environment="live", tag="mmsell10")
    leg.positions = [
        _pos("A", 2.0, status="open"),
        _pos("B", 2.0, status="settled"),
        _pos("C", 2.0, status="closed"),
    ]
    s = leg.summary()
    assert s["capital_deployed_usd"] == pytest.approx(6.0)
    assert s["avg_capital_per_trade_usd"] == pytest.approx(2.0)


def test_capital_deployed_matches_the_worked_example():
    """20 trades at $2 each = $40 deployed, regardless of how they closed."""
    leg = legs.Leg(environment="paper", tag="mmsell10_pt")
    leg.positions = [_pos(f"T{i}", 2.0, status="settled") for i in range(20)]
    assert leg.summary()["capital_deployed_usd"] == pytest.approx(40.0)


def test_unfilled_orders_contribute_zero_capital():
    """An order that never filled put no money at risk — it must not inflate
    capital deployed even though a LegPosition row exists for it."""
    leg = legs.Leg(environment="live", tag="mmsell10")
    leg.positions = [
        legs.LegPosition(ticker="A", environment="live", status="unfilled",
                         cost_basis_usd=None, quantity=None, entry_price_cents=None,
                         entry_at=T0),
        _pos("B", 2.0, status="settled"),
    ]
    s = leg.summary()
    assert s["capital_deployed_usd"] == pytest.approx(2.0)
    assert s["avg_capital_per_trade_usd"] == pytest.approx(2.0)  # averaged over 1 filled trade


def test_capital_deployed_is_zero_not_none_for_an_empty_leg():
    leg = legs.Leg(environment="live", tag="mmsell10")
    s = leg.summary()
    assert s["capital_deployed_usd"] == 0.0
    assert s["avg_capital_per_trade_usd"] is None


def test_capital_deployed_is_not_netted_against_losses():
    """A losing trade still deployed its full entry cost — capital deployed is not
    the same thing as exposure or P&L, and must not shrink when a trade loses."""
    leg = legs.Leg(environment="live", tag="mmsell10")
    leg.positions = [_pos("A", 5.0, status="settled")]
    leg.positions[0].realized_pnl_usd = -4.9  # nearly total loss
    assert leg.summary()["capital_deployed_usd"] == pytest.approx(5.0)


def test_capital_deployed_comparison_row_flags_a_sizing_gap():
    """When paper deployed far more capital than live (e.g. because live missed
    fills), the comparison table must surface that as its own row."""
    from datetime import timedelta as _td

    from kalshi_bot.livedash import compare

    live = legs.Leg(environment="live", tag="mmsell10")
    live.positions = [_pos("A", 2.0, status="settled")]
    live.positions[0].realized_pnl_usd = 0.1
    live.realized_pnl_usd = 0.1
    live.unrealized_pnl_usd = 0.0
    live.entry_fees_usd = 0.01
    live.last_data_at = T0

    paper = legs.Leg(environment="paper", tag="mmsell10_pt")
    paper.positions = [_pos(f"T{i}", 2.0, status="settled") for i in range(10)]
    for p in paper.positions:
        p.realized_pnl_usd = 0.1
    paper.realized_pnl_usd = 1.0
    paper.unrealized_pnl_usd = 0.0
    paper.entry_fees_usd = 0.1
    paper.last_data_at = T0 + _td(minutes=1)

    result = compare.compare_legs(live, paper, compare.Thresholds(), now=T0)
    row = next(r for r in result["rows"] if r["metric"] == "Capital deployed")
    assert row["live"] == pytest.approx(2.0)
    assert row["paper"] == pytest.approx(20.0)
    assert row["difference"] == pytest.approx(18.0)
    assert row["verdict"] == compare.MATERIAL
