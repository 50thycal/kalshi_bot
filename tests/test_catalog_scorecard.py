"""Series scorecard: descriptive live P&L per approved series, bets grouped by exposure."""

import json
import threading
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from kalshi_bot.catalog.scorecard import scorecard, series_scorecard
from kalshi_bot.catalog.service import APPROVALS, make_server
from kalshi_bot.catalog.store import Store

MARKETS = json.loads(
    (Path(__file__).parent / "fixtures/catalog_template_markets.json").read_text()
)["markets"]
APPROVED = {item["ticker"]: item for item in json.loads(APPROVALS.read_text())["items"]}
GAME = "KXMLBTOTAL-26SEP271510STLMIL-11"
OTHER_GAME = "KXMLBTOTAL-26JUL142000ALNL-12"


def raw(ticker):
    return {k: v for k, v in deepcopy(MARKETS[ticker]).items() if not k.startswith("_")}


def ledger(ticker, pnl, **changes):
    return {
        "market_ticker": ticker, "series_ticker": ticker.split("-", 1)[0],
        "strategy_version": "mmsell10", "deployment_arm_id": 7, "side": "no",
        "status": "attributed_source_ledger", "source_fill_count": 1, "blocked_reasons": [],
        "first_fill_at": "2026-09-27T18:00:00+00:00", "last_fill_at": "2026-09-27T18:05:00+00:00",
        "actual_fees_dollars": "0.02", "net_pnl_dollars": pnl, "buy_contracts": "3",
        **changes,
    }


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "catalog.db")
    for ticker in (GAME, OTHER_GAME):
        store.upsert("market", ticker, raw(ticker), "KXMLBTOTAL")
    sibling = raw(GAME)
    sibling.update(floor_strike=9.5, rules_primary=sibling["rules_primary"].replace("10.5", "9.5"),
                   title=sibling["title"].replace("10.5", "9.5"))
    store.upsert("market", "KXMLBTOTAL-26SEP271510STLMIL-9", sibling, "KXMLBTOTAL")
    store.review(APPROVED["KXMLBTOTAL"])
    store.save_live_economics([
        ledger(GAME, "0.30"),
        ledger("KXMLBTOTAL-26SEP271510STLMIL-9", "-0.50"),
        ledger(OTHER_GAME, "0.25"),
        ledger("KXMLBTOTAL-26AUG011910AAABBB-8", None, status="blocked", net_pnl_dollars=None,
               actual_fees_dollars=None, buy_contracts=None,
               blocked_reasons=["unsupported_or_conflicting_settlement"]),
        ledger("KXBTCD-26OCT1617-T87999.99", "1.00"),
    ])
    return store


def test_series_scorecard_adds_up_and_groups_one_game_as_one_bet(store):
    row = series_scorecard(store, "KXMLBTOTAL")
    assert (row["live_markets"], row["attributed_markets"], row["blocked_markets"]) == (4, 3, 1)
    assert row["blocked_reasons"] == {"unsupported_or_conflicting_settlement": 1}
    assert row["net_pnl_dollars"] == "0.05"
    assert row["actual_fees_dollars"] == "0.06"
    assert row["contracts_bought"] == "9"
    assert row["winning_markets"] == 2
    # Two lines on the STL-MIL game share one outcome: one bet of -0.20, plus ALNL +0.25.
    assert row["independent_bets"] == 2
    assert row["independent_bets_winning"] == 1
    assert row["worst_bet_dollars"] == "-0.20"
    assert row["mean_pnl_per_bet_dollars"] == 0.025
    assert row["stderr_pnl_per_bet_dollars"] == 0.225
    assert row["reviewed_attributed_markets"] == 3
    assert row["by_book_and_side"] == [
        {"book": "mmsell10", "side": "no", "markets": 3, "net_pnl_dollars": "0.05",
         "contracts_bought": "9"}]


def test_scorecard_is_descriptive_and_totals_only_its_series(store):
    card = scorecard(store, ["KXMLBTOTAL", "KXRAIN"])
    assert card["descriptive_only"] and card["confidence_score"] is None
    assert card["qualified"] is False and card["independence_verified"] is False
    assert card["total"]["net_pnl_dollars"] == "0.05"
    rain = card["series"][1]
    assert rain["live_markets"] == 0 and rain["mean_pnl_per_bet_dollars"] is None


def test_scorecard_endpoint_needs_the_token(store):
    server = make_server(store, "a" * 32, ("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False
        ) as client:
            assert client.get("/v1/scorecard").status_code == 401
            client.headers["Authorization"] = "Bearer " + "a" * 32
            body = client.get("/v1/scorecard?series=KXMLBTOTAL").json()
            assert [r["series"] for r in body["series"]] == ["KXMLBTOTAL"]
            assert len(client.get("/v1/scorecard").json()["series"]) == len(APPROVED)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_one_game_across_mlb_series_is_one_bet(store):
    spread = "KXMLBSPREAD-26SEP271510STLMIL-STL2"
    sibling = raw("KXMLBSPREAD-26OCT032030SDMIL-SD5")
    sibling["event_ticker"] = "KXMLBSPREAD-26SEP271510STLMIL"
    store.upsert("market", spread, sibling, "KXMLBSPREAD")
    store.review(APPROVED["KXMLBSPREAD"])
    store.save_live_economics([
        ledger(GAME, "0.30"),
        ledger("KXMLBTOTAL-26SEP271510STLMIL-9", "-0.50"),
        ledger(OTHER_GAME, "0.25"),
        ledger(spread, "-0.90"),
    ])
    [mlb] = scorecard(store, ["KXMLBTOTAL", "KXMLBSPREAD", "KXRAIN"])["families"]
    assert mlb["family"] == "candidate-game:MLB"
    assert mlb["series"] == ["KXMLBSPREAD", "KXMLBTOTAL"]
    # STL-MIL: total +0.30 - 0.50 and spread -0.90 are one game, -1.10; ALNL is +0.25.
    assert mlb["independent_bets"] == 2 and mlb["bets_spanning_two_or_more_series"] == 1
    assert mlb["worst_bet"] == "candidate-game:MLB:26SEP271510STLMIL"
    assert mlb["worst_bet_dollars"] == "-1.10"
    assert mlb["net_pnl_dollars"] == "-0.85"
    assert mlb["net_by_series"] == {"KXMLBSPREAD": "-0.90", "KXMLBTOTAL": "0.05"}
