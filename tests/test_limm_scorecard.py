"""The liquidity-incentive scorecard (`scripts/limm_scorecard.py`): pure parts only."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import limm_scorecard as sc  # noqa: E402

NOW = dt.datetime(2026, 10, 25, 16, tzinfo=dt.timezone.utc)


def test_payouts_are_read_from_the_ledger_source():
    payouts = sc.load_payouts()
    assert ("2026-10-25", 182, "KXTRUMPMENTION-26OCT24") in payouts
    # Must agree with the confirmed list the bot itself uses.
    from kalshi_bot.liquidity_incentive.reward_ledger import CONFIRMED_REWARD_PAYOUTS
    assert payouts == [tuple(p) for p in CONFIRMED_REWARD_PAYOUTS]


def test_market_types():
    assert sc.category("KXTRUMPMENTION-26OCT27-CHEA") == "mention_event"
    assert sc.category("KXTRUMPMENTION-26OCT27") == "mention_event"
    assert sc.category("KXMAMDANIMENTION-26NOV18-NQE") == "mention_event"
    assert sc.category("KXTRUMPSAY-26OCT12") == "mention_window"
    assert sc.category("KXYTVIEWSHIGH-TAT26NOV-8.0M") == "youtube_views"
    assert sc.category("KXANTHVREQ-28SEP26-T9P5") == "ai_share"
    assert sc.category("KXAAAGASDCA-26SEP26-6.3150") == "moving_number"


def test_event_dates_read_either_order():
    assert sc.event_date("KXTRUMPMENTION-26OCT27") == dt.date(2026, 10, 27)
    assert sc.event_date("KXANTHVREQ-28SEP26") == dt.date(2026, 9, 28)
    assert sc.event_date("KXYTVIEWSHIGH-TAT26NOV") is None


def test_market_pnl():
    # CHEA: 100 NO bought at 10c, sold by buying 100 YES at 95c -> 100 - 105 - fees.
    fills = [("no", 10, 100, 0.0), ("yes", 95, 100, 0.33)]
    assert sc.market_pnl(fills, None, 0.0) == ("closed", -5.33, 105.0)
    assert sc.market_pnl(fills, -5.9883, None)[:2] == ("settled", -5.9883)
    assert sc.market_pnl([("no", 10, 30, 0.0)], None, -30.0) == ("open", None, 3.0)


def test_summary_joins_rewards_and_fills_by_event():
    payouts = [("2026-10-20", 1121, "KXTRUMPMENTIONB-26OCT19"),
               ("2026-10-23", 505, "KXYTVIEWSHIGH-TAT26NOV")]
    fills = {"KXTRUMPMENTIONB-26OCT19-NQE": [
        ("no", 2, 166, 0.0, "2026-10-19T00:59:03Z"), ("yes", 97, 166, 0.2, "2026-10-19T01:56Z")]}
    s = sc.summarise(payouts, fills, {}, {"KXTRUMPMENTIONB-26OCT19-NQE": 0.0},
                     {"KXTRUMPMENTIONB-26OCT19": 3}, now=NOW, days=7)
    ev = s["events"]["KXTRUMPMENTIONB-26OCT19"]
    # 166 matched pairs pay $166; they cost $164.34 plus $0.20 fees.
    assert ev["rewards"] == 11.21 and round(ev["fill_pnl"], 2) == 1.46 and ev["bids"] == 3
    assert s["categories"]["youtube_views"]["net"] == 5.05
    assert s["categories"]["mention_event"]["net"] == 12.67
    text = sc.render(s)
    js = json.loads(text.rsplit("JSON ", 1)[1])
    assert js["categories"]["mention_event"]["rewards"] == 11.21


def test_the_scorecard_is_allowlisted_and_imports_nothing_heavy():
    import ast as _ast

    import ops_runner

    assert "limm_scorecard" in ops_runner.ALLOWED_SCRIPTS
    src = (pathlib.Path(__file__).resolve().parents[1] / "scripts" / "limm_scorecard.py")
    mods = set()
    for node in _ast.walk(_ast.parse(src.read_text())):
        if isinstance(node, _ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, _ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    # The ops runner installs psycopg and nothing else for a `script` request.
    assert not mods & {"kalshi_bot", "sqlalchemy", "pydantic"}


def test_idle_old_universe_events_are_not_listed():
    s = sc.summarise([], {}, {}, {}, {"KXCLAUDEAPP-26NOV08": 2, "KXTRUMPMENTION-26OCT27": 4},
                     now=NOW, days=7)
    text = sc.render(s)
    assert "KXCLAUDEAPP-26NOV08" not in text            # quoted, never moved money, retired type
    assert "KXTRUMPMENTION-26OCT27" in text             # current universe: shown


def test_json_rows_for_the_dashboard():
    payouts = [("2026-10-23", 505, "KXYTVIEWSHIGH-TAT26NOV")]
    fills = {"KXTRUMPMENTION-26OCT27-CHEA": [
        ("no", 10, 100, 0.0, "2026-10-23T21:29:55Z"), ("yes", 95, 100, 0.33, "2026-10-25T00:40Z")]}
    s = sc.summarise(payouts, fills, {}, {"KXTRUMPMENTION-26OCT27-CHEA": 0.0},
                     {"KXTRUMPMENTION-26OCT27": 5}, now=NOW, days=7)
    rows = sc.as_rows(s)
    json.dumps(rows)                                    # serialisable
    types = {r["type"]: r for r in rows["by_type"]}
    assert types["youtube_views"]["net"] == 5.05 and types["youtube_views"]["trading"]
    ev = {r["event"]: r for r in rows["by_event"]}
    assert ev["KXTRUMPMENTION-26OCT27"]["fills"] == -5.33
    assert not ev["KXTRUMPMENTION-26OCT27"]["payout_recorded"]
    (loser,) = rows["losers"]
    assert loser["pnl"] == -5.33 and loser["fills"] == 2 and loser["lead_h"] == 74.5


def test_open_positions_are_marked_to_the_held_side_bid():
    # MOR: 400 YES at 5c, 200 sold at 4c (booked as 200 NO at 96c); 200 YES still held.
    fills = [("yes", 5, 400, 0.0), ("no", 96, 200, 0.0)]
    # 200 pairs pay $200, 200 YES at a 3c bid are worth $6, against $212 paid.
    assert sc.marked_pnl(fills, (3, 96)) == -6.0
    # OLI: 488 NO at 1c with nobody bidding NO -> worth nothing.
    assert sc.marked_pnl([("no", 1, 488, 0.0)], (99, None)) == -4.88
    assert sc.marked_pnl(fills, None) is None


def test_summary_reports_unrealized_per_type_and_in_total():
    fills = {
        "KXYTVIEWSHIGH-MOR26NOV-7.5M": [("yes", 5, 400, 0.0, "2026-10-28T13:48Z"),
                                         ("no", 96, 200, 0.0, "2026-10-29T05:22Z")],
        "KXYTVIEWSHIGH-OLI26NOV-12.0M": [("no", 1, 488, 0.0, "2026-10-27T04:34Z")],
        "KXTRUMPMENTION-26OCT27-SAVE": [("no", 6, 100, 0.0, None), ("yes", 100, 100, 0.0, None)],
    }
    positions = {"KXYTVIEWSHIGH-MOR26NOV-7.5M": 200.0, "KXYTVIEWSHIGH-OLI26NOV-12.0M": -487.97,
                 "KXTRUMPMENTION-26OCT27-SAVE": 0.0}
    s = sc.summarise([("2026-10-29", 685, "KXTRUMPMENTION-26OCT27")], fills, {}, positions, {},
                     now=NOW, days=7, marks={"KXYTVIEWSHIGH-MOR26NOV-7.5M": (3, 96)})
    yt = s["categories"]["youtube_views"]
    assert (yt["open"], yt["unrealized"], yt["unpriced"]) == (2, -6.0, 1)
    me = s["categories"]["mention_event"]
    assert (me["net"], me["unrealized"], me["total"]) == (0.85, 0.0, 0.85)
    assert s["totals"]["unrealized"] == -6.0 and s["totals"]["unpriced"] == 1
    assert s["totals"]["total"] == round(s["totals"]["net"] - 6.0, 2)
    text = sc.render(s)
    assert "REALIZED vs UNREALIZED" in text and "unpriced" in text
    rows = sc.as_rows(s)
    assert rows["totals"]["unrealized"] == -6.0
    assert {r["type"]: r["unrealized"] for r in rows["by_type"]}["youtube_views"] == -6.0


def test_bids_are_read_from_a_market_object():
    import market_marks as mm

    assert mm.bids_from_market({"yes_bid_dollars": "0.0300", "no_bid_dollars": "0.9600"}) == (3, 96)
    # No NO quote: the NO bid is the complement of the YES ask; a zero bid is no bid.
    assert mm.bids_from_market({"yes_bid_dollars": "0.0000", "yes_ask_dollars": "0.0400"}) == (None, 96)
    assert mm.bids_from_market({"yes_bid": 12, "yes_ask": 100}) == (12, None)
