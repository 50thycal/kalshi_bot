"""DEC-024 open market discovery: whole-board index, sorting, filters, pagination, live reads."""
import http.client
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot.desks.contracts import DeskError
from kalshi_bot.desks.exchange import rules_hash
from kalshi_bot.desks.markets import MarketBrowser, market_row

NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
CATEGORIES = ("Climate and Weather", "Economics", "Politics")


def board(n_events=60, per_event=3):
    events = []
    for e in range(n_events):
        series = f"KXS{e % 7}"
        markets = []
        for m in range(per_event):
            i = e * per_event + m
            markets.append({
                "ticker": f"{series}-26OCT{e:02d}-M{m}", "event_ticker": f"{series}-26OCT{e:02d}",
                "market_type": "binary", "status": "active", "title": f"Market {i}",
                "yes_sub_title": "rain in Chicago" if i % 10 == 0 else f"bucket {m}",
                "yes_bid_dollars": "0.4000", "yes_ask_dollars": "0.4300",
                "volume_24h_fp": f"{(i * 37) % 1000}.00", "volume_fp": f"{i * 10}.00",
                "open_interest_fp": f"{(i * 53) % 700}.00",
                "open_time": (NOW - timedelta(hours=i)).isoformat(),
                "close_time": (NOW + timedelta(hours=1 + (i * 7) % 300)).isoformat(),
                "rules_primary": f"Resolves Yes if bucket {m} occurs.",
            })
        events.append({"event_ticker": f"{series}-26OCT{e:02d}", "series_ticker": series,
                       "title": f"Event {e}", "category": CATEGORIES[e % 3], "markets": markets})
    events[0]["markets"].append({"ticker": "KXMVECOMBO-1", "event_ticker": events[0]["event_ticker"],
                                 "mve_collection_ticker": "KXMVE", "market_type": "binary",
                                 "close_time": (NOW + timedelta(days=1)).isoformat()})
    events[1]["markets"].append({"ticker": "CLOSED-1", "event_ticker": events[1]["event_ticker"],
                                 "market_type": "binary", "status": "active",
                                 "close_time": (NOW - timedelta(minutes=1)).isoformat()})
    return events


class FakeKalshi:
    def __init__(self, events, page=25):
        self.events, self.page, self.calls = events, page, []

    def __call__(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if path == "/events":
            start = int(params.get("cursor") or 0)
            chunk = self.events[start:start + self.page]
            following = start + self.page
            return {"events": chunk, "cursor": str(following) if following < len(self.events) else ""}
        if path.startswith("/markets/") and path.endswith("/orderbook"):
            return {"orderbook_fp": {"yes_dollars": [["0.4000", "12.00"], ["0.4100", "3.00"]],
                                     "no_dollars": [["0.5500", "8.00"]]}}
        if path == "/markets/trades":
            return {"trades": [{"created_time": NOW.isoformat(), "yes_price_dollars": "0.4200",
                                "count_fp": "5.00", "taker_side": "yes", "trade_id": "t1"}], "cursor": "c2"}
        if path.startswith("/markets/"):
            ticker = path.rsplit("/", 1)[1]
            for event in self.events:
                for market in event["markets"]:
                    if market["ticker"] == ticker:
                        return {"market": market}
            raise DeskError("market_not_found")
        if path.startswith("/events/"):
            ticker = path.rsplit("/", 1)[1]
            event = next(e for e in self.events if e["event_ticker"] == ticker)
            return {"event": event}
        if path.startswith("/series/"):
            return {"series": {"ticker": path.rsplit("/", 1)[1], "frequency": "daily",
                               "settlement_sources": [{"name": "NWS", "url": "https://www.weather.gov/"}]}}
        raise AssertionError(path)


@pytest.fixture
def browser():
    clock = {"now": NOW}
    fake = FakeKalshi(board())
    result = MarketBrowser(fake, clock=lambda: clock["now"])
    result.fake, result.clock_state = fake, clock
    return result


def walk(browser, params):
    rows, cursor = [], None
    while True:
        page = browser.markets({**params, **({"cursor": cursor} if cursor else {})})
        rows += page["markets"]
        cursor = page["cursor"]
        if not cursor:
            return rows, page


def test_whole_board_is_indexed_without_a_twenty_market_cap(browser):
    rows, page = walk(browser, {"limit": "7"})
    assert len(rows) == 180 == page["total"]                      # combos and closed excluded
    assert len({r["ticker"] for r in rows}) == 180                 # no overlap, no gaps
    assert page["coverage"]["complete"] and page["coverage"]["events_scanned"] == 60
    assert len(browser.markets({"limit": "500"})["markets"]) == 180


@pytest.mark.parametrize("sort,field,reverse", [
    ("volume", "volume_24h", True), ("open_interest", "open_interest", True),
    ("volume_total", "volume", True)])
def test_numeric_sorts(browser, sort, field, reverse):
    values = [float(r[field]) for r in walk(browser, {"sort": sort, "limit": "50"})[0]]
    assert values == sorted(values, reverse=reverse)


def test_newest_and_closing_soon_sorts(browser):
    newest = [r["open_time"] for r in browser.markets({"sort": "newest", "limit": "500"})["markets"]]
    assert newest == sorted(newest, reverse=True)
    closing = [r["close_time"] for r in browser.markets({"sort": "closing_soon", "limit": "500"})["markets"]]
    assert closing == sorted(closing)
    with pytest.raises(DeskError, match="invalid_sort"):
        browser.markets({"sort": "random"})


def test_filters(browser):
    weather = browser.markets({"category": "weather", "limit": "500"})["markets"]
    assert weather and all(r["category"] == "Climate and Weather" for r in weather)
    series = browser.markets({"series": "KXS3", "limit": "500"})["markets"]
    assert series and all(r["series_ticker"] == "KXS3" for r in series)
    search = browser.markets({"search": "rain chicago", "limit": "500"})["markets"]
    assert len(search) == 18
    event = browser.markets({"event": "KXS2-26OCT02", "limit": "500"})["markets"]
    assert len(event) == 3
    soon = browser.markets({"close_within_hours": "24", "limit": "500"})["markets"]
    assert soon and all(datetime.fromisoformat(r["close_time"]) <= NOW + timedelta(hours=24) for r in soon)
    vol = browser.markets({"min_volume": "900", "limit": "500"})["markets"]
    assert vol and all(float(r["volume_24h"]) >= 900 for r in vol)
    fresh = browser.markets({"opened_within_hours": "5", "limit": "500"})["markets"]
    assert len(fresh) == 6
    with pytest.raises(DeskError, match="invalid_min_volume"):
        browser.markets({"min_volume": "lots"})
    with pytest.raises(DeskError, match="invalid_limit"):
        browser.markets({"limit": "100000"})


def test_index_is_cached_then_rebuilt_and_old_cursors_go_stale(browser):
    first = browser.markets({"limit": "10"})
    pages = sum(1 for path, _ in browser.fake.calls if path == "/events")
    browser.markets({"limit": "10", "cursor": first["cursor"]})
    assert sum(1 for path, _ in browser.fake.calls if path == "/events") == pages
    browser.clock_state["now"] = NOW + timedelta(minutes=6)
    browser.markets({"limit": "10"})
    assert sum(1 for path, _ in browser.fake.calls if path == "/events") == 2 * pages
    with pytest.raises(DeskError, match="market_cursor_stale"):
        browser.markets({"limit": "10", "cursor": first["cursor"]})
    with pytest.raises(DeskError, match="market_cursor_invalid"):
        browser.markets({"cursor": "garbage!"})


def test_events_series_and_categories(browser):
    events = browser.events({"sort": "closing_soon", "limit": "500"})
    assert events["total"] == 60 and all(e["markets"] == 3 for e in events["events"])
    closes = [e["first_close"] for e in events["events"]]
    assert closes == sorted(closes)
    series = browser.series({})
    assert {s["series_ticker"] for s in series["series"]} == {f"KXS{i}" for i in range(7)}
    assert {c["category"] for c in browser.categories()["categories"]} == set(CATEGORIES)


def test_market_detail_carries_full_rules_hash_and_series_settlement_sources(browser):
    detail = browser.market("KXS0-26OCT00-M1")
    market = detail["market"]
    assert detail["rules_primary"] == "Resolves Yes if bucket 1 occurs."
    assert detail["rules_sha256"] == rules_hash(market)
    assert detail["series"]["settlement_sources"][0]["name"] == "NWS"
    assert detail["capture_url"].endswith("/markets/KXS0-26OCT00-M1")
    assert detail["summary"]["breakeven_yes"] == "0.4500"  # 0.43 ask + 2c taker fee
    for bad in ("../../etc", "A/B", "", "x" * 300):
        with pytest.raises(DeskError, match="market_identifier_invalid"):
            browser.market(bad)


def test_orderbook_trades_and_whole_event_ladder(browser):
    book = browser.orderbook("KXS0-26OCT00-M1", 5)
    assert book["yes_bids"][0] == {"price": "0.4100", "quantity": "3.00"}
    assert book["implied_yes_ask"] == "0.4500"
    trades = browser.trades("KXS0-26OCT00-M1", 10)
    assert trades["trades"][0]["yes_price"] == "0.4200" and trades["cursor"] == "c2"
    big = FakeKalshi(board(n_events=2, per_event=80))
    ladder = MarketBrowser(big, clock=lambda: NOW).event("KXS1-26OCT01")
    assert ladder["market_count"] == 81 and len(json.dumps(ladder)) > 12000


def test_market_row_handles_legacy_cent_fields():
    row = market_row({"ticker": "T", "yes_bid": 40, "yes_ask": 43, "volume_24h": 10})
    assert row["yes_ask"] == "0.43" and row["no_ask"] == "0.6" and row["volume_24h"] == "10"


def test_server_routes_are_authenticated_read_only_and_classified(tmp_path, browser):
    from test_desks_service import TOKENS, FakeNotifier, FakeSupervisor, settings

    from kalshi_bot.desks.server import make_server
    from kalshi_bot.desks.service import DeskService
    from kalshi_bot.desks.store import DeskStore
    store = DeskStore(f"sqlite:///{tmp_path / 'b.db'}")
    store.initialize("test-round", NOW)
    service = DeskService(settings(), store, FakeSupervisor(), {}, notifier=FakeNotifier(), browser=browser)
    server = make_server(service, ("127.0.0.1", 0))
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()

    def get(path, role="claude"):
        conn = http.client.HTTPConnection(*server.server_address, timeout=5)
        headers = {"Authorization": "Bearer " + TOKENS[role]} if role else {}
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    try:
        assert get("/api/markets", role=None)[0] == 401
        status, page = get("/api/markets?sort=newest&limit=5&category=economics")
        assert status == 200 and len(page["markets"]) == 5 and page["cursor"]
        assert get("/api/markets/KXS0-26OCT00-M1")[1]["rules_sha256"]
        assert get("/api/markets/KXS0-26OCT00-M1/orderbook?depth=3", role="chatgpt")[0] == 200
        assert get("/api/markets/KXS0-26OCT00-M1/trades?limit=5")[0] == 200
        assert get("/api/events?sort=closing_soon")[0] == 200
        assert get("/api/events/KXS1-26OCT01")[1]["market_count"] == 4  # live view keeps every market
        assert get("/api/series")[0] == 200 and get("/api/series/KXS1")[0] == 200
        assert get("/api/categories")[0] == 200
        assert get("/api/markets?sort=bogus") == (400, {"error": "invalid_sort"})
        assert get("/api/markets/NOPE-1") == (404, {"error": "market_not_found"})
    finally:
        server.shutdown()
        server.server_close()


def test_client_builds_browse_paths_and_renders_a_handoff():
    import argparse
    import pathlib
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    import desk_client

    def args(command, **values):
        base = {attr: None for attr, _ in desk_client.FILTERS}
        base.update({"command": command, "ticker": None, "depth": 10, "refresh": False, "list": False})
        base.update(values)
        return argparse.Namespace(**base)
    path = desk_client.browse_path(args("markets", sort="newest", search="rain chicago", close_within="24"))
    assert path == "/api/markets?sort=newest&search=rain+chicago&close_within_hours=24"
    assert desk_client.browse_path(args("orderbook", ticker="KX/Y")) == "/api/markets/KX%2FY/orderbook?depth=10"
    assert desk_client.browse_path(args("series", series="KXHIGHNY")) == "/api/series/KXHIGHNY"
    assert desk_client.browse_path(args("series", series="KXHIGH", list=True)) == "/api/series?series=KXHIGH"
    status = {"round_id": "desks-round-1", "generated_at": "2026-10-01T12:00:00+00:00",
              "desks": [{"desk_id": "claude", "cash": "29.10", "initial_bankroll": "30.00"}],
              "decisions": [{"desk_id": "claude", "decision_id": "d1", "ticker": "KXHIGHNY-T72"},
                            {"desk_id": "chatgpt", "decision_id": "other"}],
              "publications": [{"desk_id": "claude", "kind": "lesson", "created_at": "2026-10-01T10:00",
                                "payload": {"lesson": "Take two time-spaced reads."}},
                               {"desk_id": "chatgpt", "kind": "lesson", "created_at": "2026-10-01T10:00",
                                "payload": {"lesson": "peer"}}]}
    text = desk_client.render_handoff(status, "claude", "desks-round-1")
    assert "Take two time-spaced reads." in text and "KXHIGHNY-T72" in text
    assert "peer" not in text and "other" not in text
