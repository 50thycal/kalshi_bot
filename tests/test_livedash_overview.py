"""The dashboard landing page: month vs goal, health lights, order board, size-split scorecard.

Fixtures use the record shapes the real writers produce (kalshi_bot/live/executor.py reconcile:
`live_orders` + `fills`, a settled ticker = a `positions` snapshot with `quantity=0` and the
exchange's realized P&L; the twin harness: `live_paper_parity_events.live_outcome` carrying the
mirror's gate code). What must never break:

  * **only money realized THIS month counts toward the goal**, and a ticker two books both
    filled is split by contracts, never claimed whole by each;
  * **a tripped loss stop and a refusing cap are visible on the book they hit**;
  * **a resting order reads as resting**, never as failed — and an order that got nothing and
    ended reads as expired;
  * **scorecard arms are recomputed from the ticker**, so a mis-sized order shows up in S0
    rather than moving between arms;
  * the cache serves the last good build when a rebuild fails.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kalshi_bot import models as m
from kalshi_bot.live.sizing import ticker_size
from kalshi_bot.livedash import overview as ov
from kalshi_bot.models import Base

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
SALT = "mmsell-partition-v1"


@pytest.fixture
def session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    yield s
    s.close()


def _order(s, ticker, *, book="Hbook", at=None, qty=1, price=93, status="filled",
           filled=None, fill_at=None, oid=None, reason=None):
    at = at or NOW - timedelta(hours=2)
    oid = oid or f"o-{book}-{ticker}-{at.isoformat()}"
    s.add(m.LiveOrder(kalshi_order_id=oid, client_order_id=f"c-{oid}", market_ticker=ticker,
                      strategy=book, created_at=at, side="no", action="buy", limit_price=price,
                      quantity=qty, status=status, cancel_reason=reason))
    if filled:
        s.add(m.Fill(kalshi_fill_id=f"f-{oid}", kalshi_order_id=oid, market_ticker=ticker,
                     filled_at=fill_at or at + timedelta(minutes=30), side="no", action="buy",
                     price=price, quantity=filled, fee=0.0))
    s.flush()
    return oid


def _settle(s, ticker, pnl, *, at):
    s.add(m.Position(market_ticker=ticker, captured_at=at, side="no", quantity=0,
                     quantity_fp=0, realized_pnl=pnl, market_exposure=0,
                     raw_json={"settled_time": at.isoformat()}))
    s.flush()


def _hold(s, ticker, qty, exposure, *, at=None):
    s.add(m.Position(market_ticker=ticker, captured_at=at or NOW - timedelta(minutes=5),
                     side="no", quantity=-qty, quantity_fp=-qty, market_exposure=exposure,
                     avg_price=exposure / qty * 100))
    s.flush()


def _pair(s, live="Hbook", twin="Hbook_pt", *, started=NOW - timedelta(days=1), params=None):
    s.add(m.LivePaperTwin(twin_tag=twin, live_tag=live, started_at=started,
                          params_json=params or {"lo": 5, "hi": 10}))
    s.flush()


def _tape(s, outcome, n=1, *, twin="Hbook_pt", live="Hbook", at=None):
    for i in range(n):
        s.add(m.LivePaperParityEvent(
            recorded_at=at or NOW - timedelta(minutes=10), twin_tag=twin, live_tag=live,
            market_ticker=f"KXT-{outcome}-{i}", live_outcome=outcome))
    s.flush()


def _book(payload, tag):
    return next(b for b in payload["headline"]["books"] if b["live_tag"] == tag)


# --- 1. headline ------------------------------------------------------------------------


def test_only_this_months_settlements_count_toward_the_goal(session):
    _order(session, "KXA-1", filled=1, at=NOW - timedelta(days=6))
    _settle(session, "KXA-1", 0.07, at=NOW - timedelta(days=2))          # October
    _order(session, "KXA-2", filled=1, at=NOW - timedelta(days=8))
    _settle(session, "KXA-2", -0.93, at=datetime(2026, 9, 29, tzinfo=timezone.utc))  # September
    _order(session, "KXA-3", filled=3, qty=3)
    _hold(session, "KXA-3", 3, 2.79)                                      # still open

    p = ov.build_overview(session, now=NOW)
    h = p["headline"]
    assert h["month"] == "2026-10" and h["goal_usd"] == 100.0
    assert h["month_realized_usd"] == pytest.approx(0.07)
    b = _book(p, "Hbook")
    assert (b["settled_this_month"], b["wins_this_month"]) == (1, 1)
    assert (b["open_positions"], b["open_cost_usd"]) == (1, pytest.approx(2.79))


def test_a_ticker_two_books_filled_is_split_by_contracts_not_double_counted(session):
    """`positions` is account-wide per ticker: a draining book and its successor holding the
    same market must not each claim the whole settlement."""
    _order(session, "KXS-1", book="Fbook", filled=1, at=NOW - timedelta(days=3))
    _order(session, "KXS-1", book="Hbook", filled=3, qty=3, at=NOW - timedelta(days=2))
    _settle(session, "KXS-1", 0.28, at=NOW - timedelta(hours=1))

    p = ov.build_overview(session, now=NOW)
    assert _book(p, "Fbook")["month_realized_usd"] == pytest.approx(0.07)
    assert _book(p, "Hbook")["month_realized_usd"] == pytest.approx(0.21)
    assert p["headline"]["month_realized_usd"] == pytest.approx(0.28)
    assert p["headline"]["shared_tickers"] == 1


def test_a_book_with_an_open_twin_is_listed_before_its_first_order(session):
    _pair(session, live="Newbook", twin="Newbook_pt")
    b = _book(ov.build_overview(session, now=NOW), "Newbook")
    assert b["twin_tag"] == "Newbook_pt" and b["health"]["state"] == "quiet"
    assert b["health"]["text"] == "no orders yet"


def test_health_lights_name_the_stop_or_cap_that_refused_orders(session):
    _pair(session)
    _order(session, "KXH-1", at=NOW - timedelta(minutes=20), status="resting")
    _tape(session, "placed", 2)
    _tape(session, "gate:daily_loss", 3)
    _tape(session, "gate:total_exposure", 1)
    _tape(session, "gate:open_cap", 2, at=NOW - timedelta(hours=3))      # outside the window

    hl = _book(ov.build_overview(session, now=NOW), "Hbook")["health"]
    assert hl["state"] == "on"
    texts = {(x["level"], x["text"]) for x in hl["lights"]}
    assert ("bad", "daily loss stop tripped") in texts
    assert ("warn", "total exposure cap blocking orders") in texts
    assert not any("open-position" in t for _, t in texts)
    assert hl["candidates_last_hour"] == 6


def test_a_book_off_the_live_list_reads_as_off_not_broken(session):
    _pair(session)
    _order(session, "KXH-1", at=NOW - timedelta(hours=5), filled=1)
    _tape(session, "gate:switches", 4)
    hl = _book(ov.build_overview(session, now=NOW), "Hbook")["health"]
    assert hl["state"] == "off" and hl["lights"] == []


def test_a_book_without_a_twin_says_its_stops_are_not_tracked(session):
    _order(session, "KXA-1", book="Abook", at=NOW - timedelta(minutes=5), status="resting")
    hl = _book(ov.build_overview(session, now=NOW), "Abook")["health"]
    assert hl["state"] == "on" and hl["tape"] is False
    assert hl["candidates_last_hour"] is None


# --- 2. order board ---------------------------------------------------------------------


def test_board_labels_resting_partial_filled_and_expired(session):
    _order(session, "KXB-R", status="resting", at=NOW - timedelta(minutes=40), qty=3)
    _order(session, "KXB-P", status="partial", qty=3, filled=1, at=NOW - timedelta(minutes=90))
    _order(session, "KXB-F", status="filled", qty=3, filled=3)
    _order(session, "KXB-E", status="canceled", reason="timeout", at=NOW - timedelta(hours=5))
    _order(session, "KXB-OLD", status="canceled", at=NOW - timedelta(days=3))   # out of window
    _order(session, "KXB-OLDR", status="resting", at=NOW - timedelta(days=3))   # still working

    board = ov.build_overview(session, now=NOW)["board"]
    by = {r["market"]: r for r in board["rows"]}
    assert by["KXB-R"]["status"] == "resting"
    assert by["KXB-P"]["status"] == "partial"
    assert by["KXB-F"]["status"] == "filled"
    assert by["KXB-E"]["status"] == "expired" and by["KXB-E"]["cancel_reason"] == "timeout"
    assert "KXB-OLD" not in by and by["KXB-OLDR"]["status"] == "resting"
    assert board["counts"] == {"resting": 2, "partial": 1, "filled": 1, "expired": 1}
    # working orders first, oldest first: the one nearest its timeout leads
    assert [r["market"] for r in board["rows"][:2]] == ["KXB-OLDR", "KXB-R"]


def test_board_reports_the_books_typical_wait_to_fill(session):
    for i, wait in enumerate((10, 30, 50)):
        at = NOW - timedelta(hours=6 + i)
        _order(session, f"KXW-{i}", filled=1, at=at, fill_at=at + timedelta(minutes=wait))
    typ = ov.build_overview(session, now=NOW)["board"]["typical_fill"]["Hbook"]
    assert typ == {"median_minutes": 30, "n": 3}


def test_board_status_rules():
    o = m.LiveOrder(quantity=3, status="resting")
    assert ov.board_status(o, 0) == ov.RESTING
    assert ov.board_status(o, 1) == ov.PARTIAL
    assert ov.board_status(o, 3) == ov.FILLED
    o.status = "canceled"
    assert ov.board_status(o, 0) == ov.EXPIRED
    assert ov.board_status(o, 1) == ov.FILLED      # ended with something: a (partial) fill


# --- 3. scorecard -----------------------------------------------------------------------


def _split_pair(s, started=NOW - timedelta(days=2)):
    _pair(s, started=started, params={"lo": 5, "hi": 10, "sizes": [1, 3],
                                      "live_size_salt": SALT, "contestkey": "split"})


def test_no_scorecard_without_a_declared_size_split(session):
    _pair(session)
    _order(session, "KXRAIN-A", filled=1)
    assert ov.build_overview(session, now=NOW)["scorecards"] == []


def test_scorecard_arms_come_from_the_ticker_hash(session):
    _split_pair(session)
    big = [t for t in ("KXRAIN-A", "KXRAIN-C") if ticker_size(t, (1, 3), salt=SALT)[1] == 3]
    small = [t for t in ("KXRAIN-B", "KXRAIN-D") if ticker_size(t, (1, 3), salt=SALT)[1] == 1]
    assert big and small
    _order(session, big[0], qty=3, filled=3)
    _settle(session, big[0], 0.21, at=NOW - timedelta(hours=1))
    _order(session, big[1], qty=3, filled=1, status="canceled")             # partly filled
    _order(session, small[0], qty=1, filled=1)
    _settle(session, small[0], -0.93, at=NOW - timedelta(hours=1))
    _order(session, small[1], qty=3, status="canceled")                     # MIS-SIZED order
    _order(session, "KXRAIN-X", qty=1, at=NOW - timedelta(days=5), filled=1)  # before start

    card = ov.build_overview(session, now=NOW)["scorecards"][0]
    one, three = card["arms"]
    assert (one["contracts"], three["contracts"]) == (1, 3)
    assert (one["orders"], three["orders"]) == (2, 2)
    assert three["contracts_filled"] == 4 and three["full_fill_pct"] == 50.0
    assert three["settled_markets"] == 1 and three["realized_usd"] == pytest.approx(0.21)
    assert three["net_cents_per_contract"] == pytest.approx(7.0)
    assert one["net_cents_per_contract"] == pytest.approx(-93.0) and one["wins"] == 0
    assert one["fill_rate_pct"] == 50.0
    s0 = next(c for c in card["checks"] if c["id"] == "S0")
    assert s0["value_pct"] == 75.0 and s0["n"] == 4                         # 3 of 4 on size
    s2 = next(p for p in card["progress"] if p["id"] == "S2")
    assert (s2["value"], s2["floor"]) == (1, 150)


def test_scorecard_counts_the_slow_cell_and_wide_spread_reads(session):
    _split_pair(session)
    oid = _order(session, "KXTRUMPSAY-26OCT03-WORD", filled=1,
                 qty=ticker_size("KXTRUMPSAY-26OCT03-WORD", (1, 3), salt=SALT)[1])
    session.add(m.ExecutionOrderContext(kalshi_order_id=oid, market_ticker="KXTRUMPSAY-26OCT03-WORD",
                                        decided_at=NOW - timedelta(hours=2), spread=4))
    _settle(session, "KXTRUMPSAY-26OCT03-WORD", 0.07, at=NOW - timedelta(hours=1))
    _order(session, "KXMLBGAME-26OCT03NYYBOS-NYY", filled=1,
           qty=ticker_size("KXMLBGAME-26OCT03NYYBOS-NYY", (1, 3), salt=SALT)[1])
    _settle(session, "KXMLBGAME-26OCT03NYYBOS-NYY", 0.07, at=NOW - timedelta(hours=1))

    prog = {p["id"]: p for p in ov.build_overview(session, now=NOW)["scorecards"][0]["progress"]}
    assert (prog["P-SLOW"]["value"], prog["P-SLOW"]["floor"]) == (1, 100)
    assert (prog["P-SPREAD"]["value"], prog["P-SPREAD"]["floor"]) == (1, 80)


def test_a_type_sized_book_gets_a_slow_vs_rest_scorecard(session):
    """The Kmmsell10 canary (docs/MMSELL_CELL_SIZE_CANARY.md): groups come from the market's
    TYPE, losses are shown per group, and the progress bars are the canary's own reads."""
    _pair(session, started=NOW - timedelta(days=2),
          params={"lo": 5, "hi": 10, "size": 1, "bigsize": 3,
                  "bigtypes": sorted(ov.SLOW_CELL_TYPES), "contestkey": "split"})
    _order(session, "KXTRUMPSAY-26OCT03-WORD", qty=3, filled=3)          # mention -> slow
    _settle(session, "KXTRUMPSAY-26OCT03-WORD", 0.21, at=NOW - timedelta(hours=1))
    _order(session, "KXMLBGAME-26OCT03NYYBOS-NYY", qty=1, filled=1)      # h2h -> rest
    _settle(session, "KXMLBGAME-26OCT03NYYBOS-NYY", -0.93, at=NOW - timedelta(hours=1))
    _order(session, "KXMLBTOTAL-26OCT03NYYBOS-9", qty=3, status="canceled")  # MIS-SIZED rest

    card = ov.build_overview(session, now=NOW)["scorecards"][0]
    assert card["kind"] == "cell_size"
    assert card["title"] == "slow-information vs everything else"
    rest, slow = card["arms"]
    assert "Slow-information" in slow["label"] and "Everything else" in rest["label"]
    assert (rest["contracts"], slow["contracts"]) == (1, 3)
    assert slow["settled_markets"] == 1 and slow["losses"] == 0
    assert slow["net_cents_per_contract"] == pytest.approx(7.0)
    assert rest["losses"] == 1 and rest["net_cents_per_contract"] == pytest.approx(-93.0)
    assert rest["orders"] == 2 and rest["progress_pct"] is None
    c0 = next(c for c in card["checks"] if c["id"] == "C0")
    assert c0["value_pct"] == pytest.approx(66.7, abs=0.1)
    prog = {p["id"]: p for p in card["progress"]}
    assert (prog["C3"]["value"], prog["C3"]["floor"]) == (1, 50)
    assert (prog["P-SLOW"]["value"], prog["P-SLOW"]["floor"]) == (1, 100)


def test_a_size_split_card_keeps_its_shape(session):
    _split_pair(session)
    card = ov.build_overview(session, now=NOW)["scorecards"][0]
    assert card["kind"] == "size_split"
    assert [p["id"] for p in card["progress"]] == ["S2", "P-SLOW", "P-SPREAD"]


# --- cache + read-only ------------------------------------------------------------------


def test_the_cache_builds_inline_once_and_keeps_the_last_good_build():
    calls = {"n": 0}

    def builder():
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("db down")
        return {"build": calls["n"]}

    cache = ov.OverviewCache(builder, refresh_seconds=3600)
    assert cache.get()["build"] == 1
    assert cache.get()["build"] == 1 and calls["n"] == 1     # fresh: no rebuild
    cache.refresh()                                           # fails
    got = cache.get()
    assert got["build"] == 1 and got["cache_error"]
    cache.refresh()
    assert cache.get() == {**cache.get(), "build": 3, "cache_error": None}


def test_the_refresher_stops_touching_the_database_when_nobody_is_looking():
    """WS-024 step 3: an idle dashboard sends no outbound traffic, so Railway can sleep it."""
    now = {"t": 1000.0}
    calls = {"n": 0}

    def builder():
        calls["n"] += 1
        return {"build": calls["n"]}

    cache = ov.OverviewCache(builder, refresh_seconds=3600, idle_seconds=900,
                             clock=lambda: now["t"])
    assert cache.tick() is False and calls["n"] == 0        # never used, never started
    cache.get()
    assert calls["n"] == 1
    now["t"] += 600
    assert cache.tick() is True and calls["n"] == 2         # looked at 10 min ago: keep fresh
    now["t"] += 301
    assert cache.tick() is False and calls["n"] == 2        # idle 15 min: stop
    assert cache.get()["build"] == 2                        # still served when asked again
    assert cache.tick() is True and calls["n"] == 3


def test_a_cache_that_never_built_raises_rather_than_serving_nothing():
    cache = ov.OverviewCache(lambda: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        cache.get()


def test_the_overview_has_no_write_path():
    src = inspect.getsource(ov)
    for verb in ("session.add", "session.delete", "session.commit", "session.flush",
                 "insert(", "update(m.", "delete(m."):
        assert verb not in src


# --- realized + unrealized + trades ------------------------------------------------------


def _tick(s, ticker, *, no_bid, yes_bid=None, at=None):
    yes_bid = 100 - no_bid - 2 if yes_bid is None else yes_bid
    s.add(m.MmSellPositionTick(market_ticker=ticker, captured_at=at or NOW - timedelta(minutes=3),
                               no_bid=no_bid, no_ask=100 - yes_bid, yes_bid=yes_bid,
                               yes_ask=100 - no_bid, mid=50))
    s.flush()


def test_open_positions_are_marked_to_the_bid_and_unpriced_ones_are_counted_not_zeroed(session):
    _order(session, "KXU-1", qty=3, filled=3, price=93)
    _hold(session, "KXU-1", 3, 2.79)                       # cost 93c x 3
    _tick(session, "KXU-1", no_bid=95)                     # worth 2.85 now
    _order(session, "KXU-2", filled=1, price=92)
    _hold(session, "KXU-2", 1, 0.92)                       # never taped: no price
    _order(session, "KXU-3", filled=1, at=NOW - timedelta(days=3))
    _settle(session, "KXU-3", 0.07, at=NOW - timedelta(hours=2))

    h = ov.build_overview(session, now=NOW)["headline"]
    b = _book({"headline": h}, "Hbook")
    assert b["unrealized_usd"] == pytest.approx(0.06)
    assert b["open_unpriced"] == 1 and h["open_unpriced"] == 1
    assert b["total_pnl_usd"] == pytest.approx(0.13)
    assert (h["month_realized_usd"], h["unrealized_usd"], h["total_pnl_usd"]) == (
        pytest.approx(0.07), pytest.approx(0.06), pytest.approx(0.13))
    # the goal still counts realized money only
    assert h["progress_pct"] == pytest.approx(0.1)


def test_a_yes_position_is_marked_at_the_yes_bid(session):
    _order(session, "KXY-1", book="Abook", qty=5, filled=5, price=40)
    session.add(m.Position(market_ticker="KXY-1", captured_at=NOW - timedelta(minutes=5),
                           side="yes", quantity=5, quantity_fp=5, market_exposure=2.0))
    _tick(session, "KXY-1", no_bid=55, yes_bid=43)
    b = _book(ov.build_overview(session, now=NOW), "Abook")
    assert b["unrealized_usd"] == pytest.approx(0.15)       # 5 x 43c - $2.00


def test_trades_count_markets_filled_this_month_and_all_time(session):
    _order(session, "KXT-1", filled=1, at=NOW - timedelta(days=1))
    _order(session, "KXT-1", filled=1, at=NOW - timedelta(hours=20), oid="retry")  # same market
    _order(session, "KXT-2", filled=1, at=datetime(2026, 9, 28, tzinfo=timezone.utc),
           fill_at=datetime(2026, 9, 28, 1, tzinfo=timezone.utc))
    _order(session, "KXT-3", filled=1, at=datetime(2026, 6, 1, tzinfo=timezone.utc))  # old
    _order(session, "KXT-4", status="canceled")                                        # no fill
    p = ov.build_overview(session, now=NOW)
    b = _book(p, "Hbook")
    assert (b["trades_this_month"], b["trades_all_time"]) == (1, 3)
    assert (p["headline"]["trades_this_month"], p["headline"]["trades_all_time"]) == (1, 3)


def test_the_months_peak_is_the_realized_high_water_mark_replayed_in_time_order(session):
    """+$0.07, +$0.07 (high $0.14), then −$0.93: the card says the month peaked at $0.14 on
    the second settlement and now sits $0.93 below it. September's win never counts."""
    _order(session, "KXP-0", filled=1, at=NOW - timedelta(days=9))
    _settle(session, "KXP-0", 5.0, at=datetime(2026, 9, 30, tzinfo=timezone.utc))
    for i, (pnl, days) in enumerate([(0.07, 3), (0.07, 2), (-0.93, 1)], start=1):
        _order(session, f"KXP-{i}", filled=1, at=NOW - timedelta(days=4))
        _settle(session, f"KXP-{i}", pnl, at=NOW - timedelta(days=days))

    h = ov.build_overview(session, now=NOW)["headline"]
    assert h["month_peak_usd"] == pytest.approx(0.14)
    assert h["month_peak_at"].startswith((NOW - timedelta(days=2)).date().isoformat())
    assert h["below_peak_usd"] == pytest.approx(0.93)


def test_a_month_that_only_lost_has_a_zero_peak_and_no_peak_time(session):
    _order(session, "KXL-1", filled=1, at=NOW - timedelta(days=3))
    _settle(session, "KXL-1", -0.93, at=NOW - timedelta(days=1))
    h = ov.build_overview(session, now=NOW)["headline"]
    assert (h["month_peak_usd"], h["month_peak_at"]) == (0.0, None)
    assert h["below_peak_usd"] == pytest.approx(0.93)


# --- every dollar this month is on a card (2026-10-22 audit, DEC-039) ---------------------


def test_a_book_that_stopped_trading_still_counts_what_settled_this_month(session):
    """Fbook's last order is outside the two-week active window, but its market settled in
    October: the money is on the headline, on its own card, and a market it shared with the
    active book is split, not handed whole to the successor."""
    old = NOW - timedelta(days=20)
    _order(session, "KXR-1", book="Fbook", filled=1, at=old)
    _settle(session, "KXR-1", 0.07, at=NOW - timedelta(days=2))
    _order(session, "KXR-2", book="Fbook", filled=1, at=old)
    _order(session, "KXR-2", book="Hbook", filled=3, qty=3, at=NOW - timedelta(days=1))
    _settle(session, "KXR-2", -2.00, at=NOW - timedelta(hours=3))

    p = ov.build_overview(session, now=NOW)
    f, hb = _book(p, "Fbook"), _book(p, "Hbook")
    assert (f["active"], hb["active"]) == (False, True)
    assert f["month_realized_usd"] == pytest.approx(0.07 - 0.50)
    assert hb["month_realized_usd"] == pytest.approx(-1.50)
    assert p["headline"]["month_realized_usd"] == pytest.approx(-1.93)
    assert sum(b["month_realized_usd"] for b in p["headline"]["books"]) == pytest.approx(-1.93)


def test_a_stopped_book_with_nothing_this_month_gets_no_card(session):
    _order(session, "KXQ-1", book="Fbook", filled=1, at=NOW - timedelta(days=30))
    _settle(session, "KXQ-1", 0.07, at=datetime(2026, 9, 20, tzinfo=timezone.utc))
    _order(session, "KXQ-2", filled=1)
    p = ov.build_overview(session, now=NOW)
    assert [b["live_tag"] for b in p["headline"]["books"]] == ["Hbook"]


def _ledger(s, cents, at, *, transfer=False):
    s.add(m.IncentiveBalanceObservation(at=at, balance_cents=1, residual_cents=cents,
                                        presumed_transfer=transfer))
    s.flush()


def test_confirmed_rewards_count_toward_the_goal_and_the_peak(session):
    """A reward Kalshi lists on its Rewards page is realized cash: it is in the goal figure, the
    reward book's card and the peak replay. Last month's payout and an unconfirmed credit are
    not."""
    from kalshi_bot.liquidity_incentive import live as limm

    now = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
    _order(session, "KXW-1", filled=1, at=now - timedelta(days=4))
    _settle(session, "KXW-1", -0.93, at=now - timedelta(days=3))       # Oct 9
    _ledger(session, 221, datetime(2026, 9, 29, 18, tzinfo=timezone.utc))  # September payout
    _ledger(session, 209, datetime(2026, 10, 10, 18, tzinfo=timezone.utc))  # listed Oct 10
    _ledger(session, 275, now - timedelta(hours=5), transfer=True)          # never listed
    _order(session, "KXW-2", book=limm.LIVE_TAG, status="resting", at=now - timedelta(hours=1))

    h = ov.build_overview(session, now=now)["headline"]
    assert (h["month_trading_usd"], h["month_rewards_usd"]) == (
        pytest.approx(-0.93), pytest.approx(2.09))
    assert h["month_realized_usd"] == pytest.approx(1.16)
    assert h["progress_pct"] == pytest.approx(1.2)
    r = _book({"headline": h}, limm.LIVE_TAG)
    assert (r["month_rewards_usd"], r["month_realized_usd"], r["total_pnl_usd"]) == (
        pytest.approx(2.09), pytest.approx(2.09), pytest.approx(2.09))
    assert _book({"headline": h}, "Hbook")["month_rewards_usd"] == 0
    assert sum(b["month_realized_usd"] for b in h["books"]) == pytest.approx(1.16)
    # -0.93 on Oct 9, then +2.09 on Oct 10: the month peaked at the reward.
    assert h["month_peak_usd"] == pytest.approx(1.16) and h["below_peak_usd"] == 0


def test_a_reward_keeps_its_book_on_the_page_after_it_stops_quoting(session):
    from kalshi_bot.liquidity_incentive import live as limm

    now = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
    _ledger(session, 209, datetime(2026, 10, 10, 18, tzinfo=timezone.utc))
    h = ov.build_overview(session, now=now)["headline"]
    r = _book({"headline": h}, limm.LIVE_TAG)
    assert r["active"] is False and r["month_realized_usd"] == pytest.approx(2.09)
    assert h["month_realized_usd"] == pytest.approx(2.09)


def test_a_closed_position_reported_again_later_counts_when_it_went_flat(session):
    """The positions feed reports a sold-out position flat on later cycles too. The money was
    realized when it first went flat (September here), not on the newest report (October)."""
    _order(session, "KXF-1", filled=2, qty=2, at=datetime(2026, 9, 27, tzinfo=timezone.utc))
    for day in (29, 30):
        session.add(m.Position(market_ticker="KXF-1", captured_at=datetime(2026, 9, day, tzinfo=timezone.utc),
                               side="no", quantity=0, quantity_fp=0, realized_pnl=-0.50))
    session.add(m.Position(market_ticker="KXF-1", captured_at=datetime(2026, 10, 2, tzinfo=timezone.utc),
                           side="no", quantity=0, quantity_fp=0, realized_pnl=-0.50))
    _order(session, "KXF-2", filled=1)
    _settle(session, "KXF-2", 0.07, at=NOW - timedelta(hours=1))
    session.flush()

    h = ov.build_overview(session, now=NOW)["headline"]
    assert h["month_realized_usd"] == pytest.approx(0.07)
    assert _book({"headline": h}, "Hbook")["settled_this_month"] == 1


def test_a_market_off_the_tape_is_marked_from_the_incentive_book_snapshot(session):
    # The liquidity-incentive book's markets are never on the mmsell tape; their own book
    # snapshots price them while fresh. A stale snapshot leaves the position unpriced.
    _order(session, "KXYT-1", book="Alimm1", qty=200, filled=200, price=5)
    session.add(m.Position(market_ticker="KXYT-1", captured_at=NOW - timedelta(minutes=5),
                           side="yes", quantity=200, quantity_fp=200, market_exposure=10.0))
    session.add(m.IncentiveMarketSnapshot(market_ticker="KXYT-1", at=NOW - timedelta(minutes=10),
                                          best_yes_bid=3, best_no_bid=96))
    _order(session, "KXYT-2", book="Alimm1", qty=488, filled=488, price=1)
    _hold(session, "KXYT-2", 488, 4.88)
    session.add(m.IncentiveMarketSnapshot(market_ticker="KXYT-2", at=NOW - timedelta(hours=5),
                                          best_yes_bid=99, best_no_bid=None))
    session.flush()
    b = _book(ov.build_overview(session, now=NOW), "Alimm1")
    assert b["unrealized_usd"] == pytest.approx(-4.0)       # 200 x 3c - $10.00
    assert b["open_unpriced"] == 1
