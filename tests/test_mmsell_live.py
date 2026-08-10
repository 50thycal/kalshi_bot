"""The mmsell LIVE mirror path (LiveExecutor.mirror_mmsell_entry + MmSellTracker wiring).

Proves the maker NO-buy live entry is INERT by default, fires only when fully enabled +
allowlisted, places a resting BUY-NO at the no-bid (not a YES-taker), dedups per ticker, and
enforces its mmsell-scoped gates. Mirrors the fixture style of test_live.py / test_mmsell.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot import repository as repo
from kalshi_bot.live.executor import LiveExecutor
from kalshi_bot.mmsell.tracker import MmSellTracker
from kalshi_bot.risk.manager import RiskManager
from kalshi_bot.scanner.metrics import MarketMetrics


def _metrics(*, yes_bid=6, yes_ask=8, depth=300, two_sided=True, spread=2):
    """A cheap longshot: yes 6/8 -> no_bid = 100-8 = 92, no_ask = 100-6 = 94 (buy NO at 92)."""
    no_bid = 100 - yes_ask
    no_ask = 100 - yes_bid
    raw = {"orderbook_fp": {
        "yes_dollars": [[f"{yes_bid / 100:.2f}", str(depth)]],
        "no_dollars": [[f"{no_bid / 100:.2f}", str(depth)]],
    }}
    return MarketMetrics(
        ticker="KXTEAM-26-A", best_yes_bid=yes_bid, best_yes_ask=yes_ask,
        best_no_bid=no_bid, best_no_ask=no_ask,
        midpoint=(yes_bid + yes_ask) / 2, spread=spread,
        depth_at_best_bid=depth, depth_at_best_ask=depth, top_depth=depth, volume=1000,
        open_interest=500, last_price=yes_ask, time_to_close_seconds=48 * 3600,
        liquidity_score=10.0, two_sided=two_sided, raw_orderbook=raw,
    )


class FakeLiveClient:
    def __init__(self):
        self.placed: list[dict] = []
        self.balance = {"balance": 100_000}

    def create_events_order(self, order):
        self.placed.append(order)
        return {"order": {"order_id": f"K-{len(self.placed)}", "status": "resting"}}

    def get_balance(self):
        return self.balance


def _live_settings(settings, **over):
    settings.bot_mode = "live"
    settings.kill_switch = False
    settings.live_enabled = True
    settings.live_strategies = "mmsell3"
    settings.live_max_order_dollars = 1.0
    settings.max_order_size = 100
    settings.max_market_exposure = 25.0
    settings.mmsell_live_max_open_positions = 60
    settings.mmsell_live_price_offset_cents = 0
    settings.mmsell_live_max_spread_cents = 40
    for k, v in over.items():
        setattr(settings, k, v)
    return settings


def _exec(settings, client):
    return LiveExecutor(client, settings, RiskManager(settings))


def _enter(ex, session, *, strategy="mmsell3", ticker="KXTEAM-26-A", metrics=None,
           account_state=None):
    ex.mirror_mmsell_entry(
        session, strategy=strategy, event_ticker="KXTEAM-26", ticker=ticker,
        metrics=metrics or _metrics(), no_price=(metrics or _metrics()).best_no_bid,
        account_state=account_state if account_state is not None else {"cash_balance": 150.0},
    )


# --- inert / gating ----------------------------------------------------------------


def test_mmsell_live_inert_by_default(settings):
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)  # defaults: not live, kill on, disabled, empty allowlist
    with db.session_scope() as session:
        _enter(ex, session)
    assert client.placed == []
    with db.session_scope() as session:
        assert session.scalars(select(m.LiveOrder)).all() == []


def test_mmsell_live_each_switch_blocks(settings):
    db.init_engine(settings.database_url)
    db.create_all()
    for over in ({"live_enabled": False}, {"kill_switch": True},
                 {"bot_mode": "weather"}, {"live_strategies": ""},
                 {"live_strategies": "mmsell1"}):  # a DIFFERENT book -> not allowlisted
        client = FakeLiveClient()
        ex = _exec(_live_settings(settings, **over), client)
        with db.session_scope() as session:
            _enter(ex, session)
        assert client.placed == [], f"should not place with {over}"


def test_mmsell_live_places_resting_no_buy_at_no_bid(settings):
    _live_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _enter(ex, session)
    assert len(client.placed) == 1
    o = client.placed[0]
    # V2 events shape: SELL yes (== buy NO) as a maker ask; price is the YES-side price in DOLLARS
    # as a STRING; count is a decimal STRING; client_order_id is a UUID.
    assert o["side"] == "ask" and o["post_only"] is True
    assert o["price"] == "0.0800" and "no_price" not in o   # sell yes @ 8c == buy no @ no-bid 92c
    assert o["count"] == "1.00"                             # $1 cap / 0.92 -> 1 contract
    assert o["time_in_force"] == "good_till_canceled"
    uuid.UUID(o["client_order_id"])                         # valid UUID (raises if not)
    with db.session_scope() as session:
        row = session.scalar(select(m.LiveOrder))
        assert row.status == "resting" and row.side == "no" and row.strategy == "mmsell3"
        assert row.limit_price == 92 and row.kalshi_order_id == "K-1"  # NO cost basis recorded
        assert session.scalar(select(func.count()).select_from(m.RiskEvent)) == 1  # audit trail


def test_mmsell_live_price_offset_capped_BELOW_no_ask(settings):
    """The offset improves the bid to fill faster, but a post_only order must stop one cent BELOW
    the no-ask, not AT it. Resting at exactly the ask IS a cross, and Kalshi rejects the order
    (`invalid_order` / "post only cross") instead of letting it take — so capping at the ask did
    not cap, it rejected. Measured live once the +1c A/B arm armed: 140 of mmsell10b's 331 orders
    rejected vs 1 for the +0c arm, concentrated in 1c-wide (tightest, most liquid) markets."""
    _live_settings(settings, mmsell_live_price_offset_cents=5)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _enter(ex, session)
    # 92 + 5 = 97, capped at no_ask 94 - 1 = 93 -> buy NO @ 93c == sell YES @ 7c == "0.0700"
    assert client.placed[0]["price"] == "0.0700"


def test_mmsell_live_hot_market_prices_defensively_but_still_places(settings):
    """The KXFEDMENTION shape end-to-end: a candidate tape gone quiet just outside the lookback
    window (mmsell_live_hot_market_lookback_minutes) must still place an order — never exclude
    the market — but at the defensive offset instead of the normal one."""
    _live_settings(settings, mmsell_live_hot_market_lookback_minutes=30,
                   mmsell_live_hot_market_defensive_offset_cents=-3)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    stale_at = datetime.now(timezone.utc) - timedelta(minutes=45)
    with db.session_scope() as session:
        repo.insert_mmsell_candidate_tick(session, "KXTEAM-26-A", _metrics(), captured_at=stale_at)
        _enter(ex, session)
    assert len(client.placed) == 1                          # still entered — never excluded
    # buy NO @ 92 - 3 = 89 == sell YES @ 11c == price "0.1100"
    assert client.placed[0]["price"] == "0.1100"
    with db.session_scope() as session:
        row = session.scalar(select(m.RiskEvent))
        assert row.reason_codes_json == ["hot_entry"]        # audited so it's verifiable later


def test_mmsell_live_calm_market_ignores_stale_but_in_window_tick(settings):
    """A recent, PRICE-STABLE tick inside the lookback window must price normally — hot pricing
    is about the market having actually moved, not merely having a comparison point at all."""
    _live_settings(settings, mmsell_live_price_offset_cents=0)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    recent_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    with db.session_scope() as session:
        repo.insert_mmsell_candidate_tick(session, "KXTEAM-26-A", _metrics(), captured_at=recent_at)
        _enter(ex, session)
    assert client.placed[0]["price"] == "0.0800"             # unchanged: sell YES @ 8c (no-bid 92)


def test_mmsell_live_normal_scan_cadence_gap_is_not_hot(settings):
    """Regression for the 2026-08-03 finding: mmsell_interval_minutes (the ride-along scan that
    captures a candidate tick every cycle) is 30, so a healthy cycle-to-cycle gap always runs a
    little OVER 30min in practice (measured live: p25-p95 30.2-31.7min across 4409 gaps, 141
    tickers, every series). A lookback equal to the scan interval made 90% of ordinary re-scans
    misfire as hot purely from cycle-time jitter. Uses the real config default (no override) so
    this breaks if the default ever drifts back toward the scan interval."""
    _live_settings(settings, mmsell_live_price_offset_cents=0)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    one_normal_cycle_ago = datetime.now(timezone.utc) - timedelta(minutes=31, seconds=42)
    with db.session_scope() as session:
        repo.insert_mmsell_candidate_tick(
            session, "KXTEAM-26-A", _metrics(), captured_at=one_normal_cycle_ago)
        _enter(ex, session)
    assert client.placed[0]["price"] == "0.0800"              # normal price: not hot
    with db.session_scope() as session:
        row = session.scalar(select(m.RiskEvent))
        assert row.reason_codes_json == []


def test_mmsell_live_two_cycle_absence_is_still_hot(settings):
    """The flip side of the cadence fix: a ticker that misses a WHOLE extra cycle (skipped the
    trading band, or the scan ran unusually slow) is a genuine gone-quiet signal and must still
    price defensively — raising the lookback must not blind the check entirely."""
    _live_settings(settings, mmsell_live_price_offset_cents=0,
                   mmsell_live_hot_market_defensive_offset_cents=-3)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    two_cycles_ago = datetime.now(timezone.utc) - timedelta(minutes=62)
    with db.session_scope() as session:
        repo.insert_mmsell_candidate_tick(
            session, "KXTEAM-26-A", _metrics(), captured_at=two_cycles_ago)
        _enter(ex, session)
    # buy NO @ 92 - 3 = 89 == sell YES @ 11c == price "0.1100"
    assert client.placed[0]["price"] == "0.1100"
    with db.session_scope() as session:
        row = session.scalar(select(m.RiskEvent))
        assert row.reason_codes_json == ["hot_entry"]


def test_mmsell_live_wide_spread_guard(settings):
    _live_settings(settings, mmsell_live_max_spread_cents=1)  # 2c spread exceeds the sanity cap
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _enter(ex, session)
    assert client.placed == []


def test_mmsell_live_requires_real_balance(settings):
    _live_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _enter(ex, session, account_state={"cash_balance": None})
    assert client.placed == []


def test_mmsell_live_per_ticker_dedup(settings):
    _live_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _enter(ex, session)
        _enter(ex, session)  # same ticker again -> deduped
    assert len(client.placed) == 1


def test_mmsell_live_max_open_cap_blocks(settings):
    _live_settings(settings, mmsell_live_max_open_positions=0)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _enter(ex, session)
    assert client.placed == []


def test_mmsell_live_market_exposure_blocks(settings):
    _live_settings(settings, max_market_exposure=0.5)  # any existing exposure trips it
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        # seed an existing position snapshot on the ticker (exposure > cap)
        repo_insert = m.Position(
            market_ticker="KXTEAM-26-A", captured_at=datetime.now(timezone.utc),
            side="no", quantity=1, quantity_fp=1.0, avg_price=92.0, market_exposure=0.92,
            realized_pnl=None, unrealized_pnl=None, raw_json=None)
        session.add(repo_insert)
        session.flush()
        ex.mirror_mmsell_entry(
            session, strategy="mmsell3", event_ticker="KXTEAM-26", ticker="KXTEAM-26-A",
            metrics=_metrics(), no_price=92, account_state={"cash_balance": 150.0})
    assert client.placed == []


# --- tracker wiring (end-to-end) ---------------------------------------------------


def _mkt(ticker, sub, yes_bid_c, yes_ask_c, vol=500, hours=48):
    close = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    return {"ticker": ticker, "yes_sub_title": sub, "close_time": close,
            "volume_fp": f"{vol}.0", "yes_bid_dollars": f"{yes_bid_c / 100:.4f}",
            "yes_ask_dollars": f"{yes_ask_c / 100:.4f}"}


def _ob(yes_bid_c, yes_ask_c):
    no_bid_c = 100 - yes_ask_c
    return {"orderbook_fp": {
        "yes_dollars": [[f"{yes_bid_c / 100:.4f}", "300"]],
        "no_dollars": [[f"{no_bid_c / 100:.4f}", "300"]]}}


class FakeMarketClient:
    def __init__(self, events, books):
        self._events, self._books = events, books

    def get_events(self, status="open", with_nested_markets=True, limit=200, cursor=None):
        return {"events": self._events, "cursor": ""}

    def get_orderbook(self, ticker, depth=None):
        return self._books[ticker]


def test_tracker_mirrors_paper_entry_to_live(settings):
    _live_settings(settings)
    settings.mmsell_variants = "mmsell3:lo=5,hi=10"  # only mmsell3 (matches the allowlist)
    settings.mmsell_entry_lo_cents = 5
    settings.mmsell_entry_hi_cents = 10
    db.init_engine(settings.database_url)
    db.create_all()

    # a cheap longshot with yes mid ~8 (in the 5-10 band): yes 6/10 -> buy NO at 90
    ev = {"event_ticker": "KXTEAM-26", "series_ticker": "KXTEAM",
          "markets": [_mkt("KXTEAM-26-A", "A wins", 6, 10)]}
    market_client = FakeMarketClient([ev], {"KXTEAM-26-A": _ob(6, 10)})
    live_client = FakeLiveClient()
    ex = _exec(settings, live_client)
    tracker = MmSellTracker(market_client, settings, live_executor=ex)
    tracker._account_state = {"cash_balance": 150.0}

    with db.session_scope() as session:
        summ = tracker.run_once(session)

    # the mmsell3 book opened a paper trade AND mirrored it to a real resting NO order
    assert summ.per_book.get("mmsell3", 0) == 1
    assert len(live_client.placed) == 1
    o = live_client.placed[0]
    # buy NO @ 90c == sell YES @ 10c: maker ask, price "0.1000", UUID coid
    assert o["side"] == "ask" and o["price"] == "0.1000"
    uuid.UUID(o["client_order_id"])
    with db.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(m.LiveOrder)) == 1
        assert session.scalar(
            select(func.count()).select_from(m.PaperTrade).where(m.PaperTrade.strategy == "mmsell3")
        ) == 1  # paper shadow still recorded


def test_tracker_without_executor_stays_paper_only(settings):
    settings.bot_mode = "weather"
    settings.mmsell_variants = "mmsell3:lo=5,hi=10"
    settings.mmsell_entry_lo_cents = 5
    settings.mmsell_entry_hi_cents = 10
    db.init_engine(settings.database_url)
    db.create_all()
    ev = {"event_ticker": "KXTEAM-26", "series_ticker": "KXTEAM",
          "markets": [_mkt("KXTEAM-26-A", "A wins", 6, 10)]}
    tracker = MmSellTracker(FakeMarketClient([ev], {"KXTEAM-26-A": _ob(6, 10)}), settings)
    with db.session_scope() as session:
        tracker.run_once(session)
        assert session.scalar(select(func.count()).select_from(m.LiveOrder)) == 0
        assert session.scalar(select(func.count()).select_from(m.PaperTrade)) >= 1
