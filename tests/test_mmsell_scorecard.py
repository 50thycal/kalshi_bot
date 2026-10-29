"""The mmsell scorecard (`scripts/mmsell_scorecard.py`): pure parts only."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import mmsell_scorecard as sc  # noqa: E402

NOW = dt.datetime(2026, 10, 29, 12, tzinfo=dt.timezone.utc)
TYPES = sc.load_series_types()


def _settled(at="2026-10-28T20:00:00Z", yes=0.0):
    return {"yes_value": yes, "open_qty": 0.0, "settled_at": at}


def test_taxonomy_is_read_from_source_and_matches_the_module():
    from kalshi_bot.mmsell.market_types import SERIES_TYPES

    assert TYPES == {prefix: kind for prefix, kind, _mode in SERIES_TYPES}


def test_categories():
    assert sc.category("KXRAIN-26OCT27-DAL", TYPES) == "weather"
    assert sc.category("KXHIGHNY-26OCT28-B60.5", TYPES) == "weather"
    assert sc.category("KXINXU-26OCT28H1600-B6750", TYPES) == "index"
    assert sc.category("KXNASDAQ100U-26OCT28H1600-T25000", TYPES) == "index"
    assert sc.category("KXTRUMPMENTION-26OCT27-CHEA", TYPES) == "mentions"
    assert sc.category("KXFEDMENTION-26OCT29-TARI", TYPES) == "mentions"
    assert sc.category("KXTRUMPSAY-26OCT12-TARI", TYPES) == "politics"
    assert sc.category("KXMLBGAME-26OCT28LADNYY-LAD", TYPES) == "sports"
    assert sc.category("KXNFLSPREAD-26NOV02KCBUF-KC3", TYPES) == "sports"
    assert sc.category("KXBTCD-26OCT2817-T110000", TYPES) == "other"


def test_a_maker_no_buy_is_priced_from_the_order_not_the_fill():
    # Kalshi reports the NO buy as side=no, action=sell at the NO price. Direction comes from
    # the order: 3 NO at 94c, settled NO (yes_value 0) -> +3 * 6c = +0.18, less the fee.
    legs = [sc.leg("no", "buy", "no", 94, 3, 0.02)]
    assert sc.market_pnl(legs, "no", 0.0) == 0.16
    # Settled YES: the full clip is lost.
    assert sc.market_pnl(legs, "no", 100.0) == -2.84
    # A fill reported on the other side maps into the order's frame (100 - p).
    assert sc.leg("no", "buy", "yes", 6, 1, 0.0) == (1, 1.0, 94, 0.0)
    assert sc.market_pnl(legs, "no", None) is None


def _book():
    fills = [
        ("Kmmsell10", "KXRAIN-26OCT27-DAL", "no", "buy", "no", 94, 3, 0.0, "2026-10-27T10:00:00Z"),
        ("Vmmsell10", "KXRAIN-26OCT27-DAL", "no", "buy", "no", 93, 3, 0.0, "2026-10-27T11:00:00Z"),
        ("Kmmsell10", "KXINXU-26OCT28H1600-B6750", "no", "buy", "no", 95, 1, 0.0,
         "2026-10-28T14:00:00Z"),
        ("Vmmsell10", "KXTRUMPMENTION-26OCT27-CHEA", "no", "buy", "no", 94, 3, 0.0,
         "2026-10-26T12:00:00Z"),
        ("Kmmsell10", "KXMLBGAME-26OCT30LADNYY-LAD", "no", "buy", "no", 92, 1, 0.0,
         "2026-10-29T01:00:00Z"),
    ]
    settlements = {
        "KXRAIN-26OCT27-DAL": _settled("2026-10-28T06:00:00Z", 0.0),
        "KXINXU-26OCT28H1600-B6750": _settled("2026-10-28T20:00:00Z", 100.0),
        "KXTRUMPMENTION-26OCT27-CHEA": _settled("2026-10-24T23:00:00Z", 0.0),
        "KXMLBGAME-26OCT30LADNYY-LAD": {"yes_value": None, "open_qty": -1.0, "settled_at": None},
    }
    twin = [("Kmmsell10_pt4", "KXRAIN-26OCT27-DAL", 3, 0.15),
            ("Kmmsell10_pt4", "KXINXU-26OCT28H1600-B6750", 1, -0.95)]
    return sc.summarise(fills, settlements, twin, {"KXTRUMPMENTION-26OCT27-CHEA"}, TYPES,
                        now=NOW, days=3)


def test_summary_per_book_then_category():
    s = _book()
    k, v = s["books"]["Kmmsell10"], s["books"]["Vmmsell10"]
    # A shared market charges each book for its own clip only.
    assert s["cells"][("Kmmsell10", "weather")]["pnl"] == 0.18
    assert s["cells"][("Vmmsell10", "weather")]["pnl"] == 0.21
    assert k["settled"] == 2 and k["pnl"] == -0.77 and k["losing"] == 1
    assert k["open"] == 1 and k["open_cost"] == 0.92
    assert k["cents_per_contract"] == -19.25
    # Settled before the 3-day window: lifetime only.
    assert s["cells"][("Vmmsell10", "mentions")]["recent_settled"] == 0
    assert v["recent_settled"] == 1 and v["recent_pnl"] == 0.21
    assert s["books"]["Nmmsell10"]["settled"] == 0


def test_twin_gap_uses_same_market_pairs():
    tw = _book()["books"]["Kmmsell10"]["twin"]
    # live: (0.18 - 0.95) / 4 contracts = -19.25c; twin: (0.15 - 0.95) / 4 = -20.0c.
    assert tw == {"pairs": 2, "live_cpc": -19.25, "twin_cpc": -20.0, "gap": 0.75}
    assert _book()["books"]["Vmmsell10"]["twin"]["pairs"] == 0


def test_render_and_json_line():
    s = _book()
    text = sc.render(s)
    assert "Kmmsell10" in text and "  weather" in text and "gap +0.75c" in text
    assert "KXINXU-26OCT28H1600-B6750" in text.split("LOSING MARKETS", 1)[1]
    js = json.loads(text.rsplit("JSON ", 1)[1])
    assert js["books"]["Kmmsell10"]["pnl"] == -0.77
    assert js["overlap"]["Vmmsell10"] == {"markets": 1, "settled": 1, "pnl": 0.18, "with_limm": 1}
    assert js["loss_markets"] == 1 and js["avg_loss"] == -0.95


def test_json_rows_for_the_dashboard():
    rows = sc.as_rows(_book())
    json.dumps(rows)
    cells = {(r["book"], r["category"]): r for r in rows["by_tag"]}
    assert cells[("Kmmsell10", "index")]["pnl"] == -0.95
    books = {r["book"]: r for r in rows["by_book"]}
    assert books["Kmmsell10"]["twin_gap_cents"] == 0.75
    # by_event holds the recent window only.
    assert {e["event"] for e in rows["by_event"]} == {"KXRAIN-26OCT27", "KXINXU-26OCT28H1600"}
    (loser,) = rows["losers"]
    assert loser["market"] == "KXINXU-26OCT28H1600-B6750" and loser["hours_to_settle"] == 6.0


def test_the_scorecard_is_allowlisted_and_imports_nothing_heavy():
    import ast as _ast

    import ops_runner

    assert "mmsell_scorecard" in ops_runner.ALLOWED_SCRIPTS
    src = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "mmsell_scorecard.py"
    mods = set()
    for node in _ast.walk(_ast.parse(src.read_text())):
        if isinstance(node, _ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, _ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    # The ops runner installs psycopg and nothing else for a `script` request.
    assert not mods & {"kalshi_bot", "sqlalchemy", "pydantic"}
