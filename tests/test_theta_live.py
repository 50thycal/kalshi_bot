"""The theta LIVE mirror path (LiveExecutor.mirror_theta_entry + ThetaTracker wiring).

Proves the maker NO-buy live entry is INERT by default, fires only when fully enabled +
allowlisted, places a resting BUY-NO at the no-bid (not a YES-taker), dedups per ticker, and
enforces its OWN theta-scoped gates (deliberately not mmsell's — see docs/THETA_LIVE_PLAN.md).
Mirrors the fixture style of test_mmsell_live.py / test_theta.py.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot.live.executor import LiveExecutor
from kalshi_bot.risk.manager import RiskManager
from kalshi_bot.scanner.metrics import MarketMetrics
from kalshi_bot.theta.tracker import ThetaTracker


def _flat_closes(
    n_minutes: int, price: float = 60000.0, end: int | None = None
) -> dict[int, float]:
    """`end` defaults to "right now" computed AT CALL TIME, not at module-import time — a slow
    full-suite run can put minutes between import and this test's actual execution, and
    SpotModel.spot_at only tolerates a 6-minute gap, so a frozen default silently starves the
    model and turns entry assertions into flaky failures that only show up on a full run."""
    if end is None:
        end = int(time.time()) // 60 * 60
    return {end - i * 60: price for i in range(1, n_minutes + 1)}


def _metrics(*, yes_bid=19, yes_ask=21, depth=300, two_sided=True, spread=2):
    """A tail sell candidate: yes 19/21 -> no_bid = 100-21 = 79 (buy NO at 79)."""
    no_bid = 100 - yes_ask
    no_ask = 100 - yes_bid
    raw = {"orderbook_fp": {
        "yes_dollars": [[f"{yes_bid / 100:.2f}", str(depth)]],
        "no_dollars": [[f"{no_bid / 100:.2f}", str(depth)]],
    }}
    return MarketMetrics(
        ticker="KXBTCD-26JUL0317-T60500", best_yes_bid=yes_bid, best_yes_ask=yes_ask,
        best_no_bid=no_bid, best_no_ask=no_ask,
        midpoint=(yes_bid + yes_ask) / 2, spread=spread,
        depth_at_best_bid=depth, depth_at_best_ask=depth, top_depth=depth, volume=1000,
        open_interest=500, last_price=yes_ask, time_to_close_seconds=1800,
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
    settings.live_strategies = "theta4"
    settings.max_market_exposure = 25.0
    settings.theta_live_max_order_dollars = 3.0
    settings.theta_live_max_contracts = 5
    settings.theta_live_max_open_positions = 15
    settings.theta_live_price_offset_cents = 0
    settings.theta_live_max_spread_cents = 40
    for k, v in over.items():
        setattr(settings, k, v)
    return settings


def _exec(settings, client):
    return LiveExecutor(client, settings, RiskManager(settings))


def _enter(ex, session, *, strategy="theta4", ticker="KXBTCD-26JUL0317-T60500", metrics=None,
           account_state=None):
    ex.mirror_theta_entry(
        session, strategy=strategy, event_ticker="KXBTCD-26JUL0317", ticker=ticker,
        metrics=metrics or _metrics(), no_price=(metrics or _metrics()).best_no_bid,
        account_state=account_state if account_state is not None else {"cash_balance": 150.0},
    )


# --- inert / gating ----------------------------------------------------------------


def test_theta_live_inert_by_default(settings):
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)  # defaults: not live, kill on, disabled, empty allowlist
    with db.session_scope() as session:
        _enter(ex, session)
    assert client.placed == []
    with db.session_scope() as session:
        assert session.scalars(select(m.LiveOrder)).all() == []


def test_theta_live_each_switch_blocks(settings):
    db.init_engine(settings.database_url)
    db.create_all()
    for over in ({"live_enabled": False}, {"kill_switch": True},
                 {"bot_mode": "weather"}, {"live_strategies": ""},
                 {"live_strategies": "theta1"}):  # a DIFFERENT book -> not allowlisted
        client = FakeLiveClient()
        ex = _exec(_live_settings(settings, **over), client)
        with db.session_scope() as session:
            _enter(ex, session)
        assert client.placed == [], f"should not place with {over}"


def test_theta_live_places_resting_no_buy_at_no_bid(settings):
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
    assert o["price"] == "0.2100"       # sell yes @ 21c == buy no @ no-bid 79c
    assert o["count"] == "3.00"         # $3 cap / 0.79 -> 3 contracts (theta's OWN cap, not mmsell's)
    assert o["time_in_force"] == "good_till_canceled"
    uuid.UUID(o["client_order_id"])     # valid UUID (raises if not)
    with db.session_scope() as session:
        row = session.scalar(select(m.LiveOrder))
        assert row.status == "resting" and row.side == "no" and row.strategy == "theta4"
        assert row.limit_price == 79 and row.kalshi_order_id == "K-1"  # NO cost basis recorded
        assert session.scalar(select(func.count()).select_from(m.RiskEvent)) == 1  # audit trail


def test_theta_live_price_offset_capped_at_no_ask(settings):
    # offset improves the bid to fill faster, but never pays THROUGH the no-ask (81 here).
    _live_settings(settings, theta_live_price_offset_cents=5)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _enter(ex, session)
    # 79 + 5 = 84, capped at no_ask 81 -> buy NO @ 81c == sell YES @ 19c == price "0.1900"
    assert client.placed[0]["price"] == "0.1900"


def test_theta_live_hot_market_prices_defensively_but_still_places(settings):
    """A ladder quote that has gone quiet for longer than the lookback window marks this entry
    hot, so it rests BELOW the no-bid instead of joining the queue. This is the defence against
    the `400 invalid_order — "post only cross"` rejection theta took live on 2026-08-02: the book
    moved through our resting price between quote and placement, and because the order never
    rests at all, the market is lost for the rest of theta's short (10-55min) window."""
    _live_settings(settings, theta_live_hot_market_lookback_minutes=15,
                   theta_live_hot_market_defensive_offset_cents=-3)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    stale = datetime.now(timezone.utc) - timedelta(minutes=45)
    with db.session_scope() as session:
        session.add(m.CryptoLadderSnapshot(
            captured_at=stale, event_ticker="KXBTCD-26JUL0317",
            market_ticker="KXBTCD-26JUL0317-T60500", yes_bid_cents=19.0, yes_ask_cents=21.0))
        session.flush()
        _enter(ex, session)
    assert len(client.placed) == 1                 # still entered — never excluded
    # buy NO @ 79 - 3 = 76 == sell YES @ 24c
    assert client.placed[0]["price"] == "0.2400"
    with db.session_scope() as session:
        assert session.scalar(select(m.RiskEvent)).reason_codes_json == ["hot_entry"]


def test_theta_live_calm_ladder_prices_normally(settings):
    """A recent, price-stable ladder quote must leave the entry on the normal path — hot pricing
    is about the market having actually moved, not about having a comparison point at all."""
    _live_settings(settings, theta_live_hot_market_lookback_minutes=15)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    recent = datetime.now(timezone.utc) - timedelta(minutes=5)
    with db.session_scope() as session:
        session.add(m.CryptoLadderSnapshot(
            captured_at=recent, event_ticker="KXBTCD-26JUL0317",
            market_ticker="KXBTCD-26JUL0317-T60500", yes_bid_cents=19.0, yes_ask_cents=21.0))
        session.flush()
        _enter(ex, session)
    assert client.placed[0]["price"] == "0.2100"   # unchanged: sell yes @ 21c (no-bid 79)


def test_theta_hot_pricing_does_not_read_mmsells_tape(settings):
    """Each book reads its OWN tape. An mmsell candidate tick on the same ticker must not make a
    theta entry look calm (or hot) — that cross-wiring is the bug the required `lookup` argument
    in live/sizing.py exists to prevent."""
    _live_settings(settings, theta_live_hot_market_lookback_minutes=15)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    recent = datetime.now(timezone.utc) - timedelta(minutes=5)
    with db.session_scope() as session:
        # a CALM mmsell tick; theta has no ladder row at all, so theta must read "no tape" (calm)
        # from its own table rather than picking this up.
        session.add(m.MmSellCandidateTick(
            market_ticker="KXBTCD-26JUL0317-T60500", captured_at=recent,
            no_bid=79, no_ask=81, yes_bid=19, yes_ask=21, mid=20.0))
        session.flush()
        _enter(ex, session)
    assert client.placed[0]["price"] == "0.2100"   # normal path, from theta's own (empty) tape
    with db.session_scope() as session:
        assert session.scalar(select(m.RiskEvent)).reason_codes_json == []


def test_theta_live_wide_spread_guard(settings):
    _live_settings(settings, theta_live_max_spread_cents=1)  # 2c spread exceeds the sanity cap
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _enter(ex, session)
    assert client.placed == []


def test_theta_live_requires_real_balance(settings):
    _live_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _enter(ex, session, account_state={"cash_balance": None})
    assert client.placed == []


def test_theta_live_per_ticker_dedup(settings):
    _live_settings(settings)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _enter(ex, session)
        _enter(ex, session)  # same ticker again -> deduped
    assert len(client.placed) == 1


def test_theta_live_max_open_cap_blocks(settings):
    _live_settings(settings, theta_live_max_open_positions=0)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _enter(ex, session)
    assert client.placed == []


def test_theta_live_market_exposure_blocks(settings):
    _live_settings(settings, max_market_exposure=0.1)  # any existing exposure trips it
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        session.add(m.Position(
            market_ticker="KXBTCD-26JUL0317-T60500", captured_at=datetime.now(timezone.utc),
            side="no", quantity=1, quantity_fp=1.0, avg_price=79.0, market_exposure=0.79,
            realized_pnl=None, unrealized_pnl=None, raw_json=None))
        session.flush()
        ex.mirror_theta_entry(
            session, strategy="theta4", event_ticker="KXBTCD-26JUL0317",
            ticker="KXBTCD-26JUL0317-T60500", metrics=_metrics(), no_price=79,
            account_state={"cash_balance": 150.0})
    assert client.placed == []


def test_theta_live_sizing_is_independent_of_mmsell(settings):
    """theta's clip must come from its OWN knobs, not mmsell's live_max_order_dollars /
    max_order_size — the whole reason theta got its own knobs was to avoid a second live book
    silently resizing (or being resized by) mmsell's already-live sizing."""
    _live_settings(settings, theta_live_max_order_dollars=1.0, theta_live_max_contracts=1)
    settings.live_max_order_dollars = 999.0   # mmsell's global knobs, wildly different
    settings.max_order_size = 999
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        _enter(ex, session)
    assert client.placed[0]["count"] == "1.00"  # theta's own $1/1-contract cap, not mmsell's 999


# --- tracker wiring (end-to-end) ---------------------------------------------------


class FakeSpotClient:
    """Serves a canned candle dict once (the bootstrap fetch), then nothing new."""

    def __init__(self, closes: dict[int, float]):
        self._closes = dict(closes)

    def candles(self, product: str, start: int, end: int) -> dict[int, float]:
        return {ts: c for ts, c in self._closes.items() if start <= ts < end}


def _mkt(ticker, strike_type, floor, cap, yb_c, ya_c, minutes=30, vol=5000):
    close = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    out = {
        "ticker": ticker, "close_time": close, "volume_fp": f"{vol}.0",
        "yes_bid_dollars": f"{yb_c / 100:.4f}", "yes_ask_dollars": f"{ya_c / 100:.4f}",
        "strike_type": strike_type,
    }
    if floor is not None:
        out["floor_strike"] = floor
    if cap is not None:
        out["cap_strike"] = cap
    return out


def _ob(yes_bid_c, yes_ask_c, depth=300):
    no_bid_c = 100 - yes_ask_c
    return {"orderbook_fp": {
        "yes_dollars": [[f"{yes_bid_c / 100:.4f}", str(depth)]],
        "no_dollars": [[f"{no_bid_c / 100:.4f}", str(depth)]],
    }}


class FakeKalshi:
    def __init__(self, markets_by_series, books):
        self._markets = markets_by_series
        self._books = books

    def get_markets(self, *, status="open", series_ticker=None, limit=200, cursor=None,
                    event_ticker=None, min_close_ts=None):
        return {"markets": self._markets.get(series_ticker, []), "cursor": ""}

    def get_orderbook(self, ticker, depth=None):
        return self._books[ticker]


def test_theta_tracker_mirrors_paper_entry_to_live(settings):
    """The control book, plainly armed (LIVE_STRATEGIES=theta), with theta_collect_only off —
    proves the wiring end-to-end without the collect-only/live-variant machinery in the way.
    The real production shape (control shelved, theta4 the designated live variant) is the next
    test."""
    _live_settings(settings, live_strategies="theta")
    settings.theta_series = "KXBTCD:BTC-USD"
    settings.theta_variants = ""
    settings.theta_collect_only = False  # exercise entries (prod default is shelved)
    db.init_engine(settings.database_url)
    db.create_all()

    # flat spot at 60k: a 20c 'greater 60.5k' strike has model p=0 (excess 20 >= 5) -> SELL
    mkts = [_mkt("KXBTCD-26JUL0317-T60500", "greater", 60500.0, None, 19, 21)]
    market_client = FakeKalshi({"KXBTCD": mkts}, {"KXBTCD-26JUL0317-T60500": _ob(19, 21)})
    live_client = FakeLiveClient()
    ex = _exec(settings, live_client)
    tracker = ThetaTracker(
        market_client, settings, spot_client=FakeSpotClient(_flat_closes(900)),
        live_executor=ex,
    )
    tracker._account_state = {"cash_balance": 150.0}

    with db.session_scope() as session:
        summ = tracker.run_once(session)

    # the control book opened a paper trade AND mirrored it to a real resting NO order
    assert summ.per_book.get("theta", 0) == 1
    assert len(live_client.placed) == 1
    o = live_client.placed[0]
    assert o["side"] == "ask" and o["price"] == "0.2100"
    uuid.UUID(o["client_order_id"])
    with db.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(m.LiveOrder)) == 1
        row = session.scalar(select(m.LiveOrder))
        assert row.strategy == "theta"


def test_theta4_tracker_mirrors_paper_entry_to_live(settings):
    """The real production shape: theta_collect_only stays on (default), theta4 is the
    designated live variant, LIVE_STRATEGIES=theta4 — mirrors docs/THETA_LIVE_PLAN.md exactly."""
    _live_settings(settings)  # live_strategies="theta4"
    settings.theta_series = "KXBTCD:BTC-USD"
    settings.theta_variants = "theta4:hi=20,ttemax=35,mult=2.0,edge=6"
    settings.theta_live_variants = "theta4"
    db.init_engine(settings.database_url)
    db.create_all()

    mkts = [_mkt("KXBTCD-26JUL0317-T60500", "greater", 60500.0, None, 19, 21)]
    market_client = FakeKalshi({"KXBTCD": mkts}, {"KXBTCD-26JUL0317-T60500": _ob(19, 21)})
    live_client = FakeLiveClient()
    ex = _exec(settings, live_client)
    tracker = ThetaTracker(
        market_client, settings, spot_client=FakeSpotClient(_flat_closes(900)),
        live_executor=ex,
    )
    tracker._account_state = {"cash_balance": 150.0}

    with db.session_scope() as session:
        summ = tracker.run_once(session)

    assert summ.per_book.get("theta4", 0) == 1
    assert len(live_client.placed) == 1
    with db.session_scope() as session:
        row = session.scalar(select(m.LiveOrder))
        assert row.strategy == "theta4"
        assert session.scalar(
            select(func.count()).select_from(m.PaperTrade).where(m.PaperTrade.strategy == "theta4")
        ) == 1  # paper shadow still recorded


def test_theta_tracker_without_executor_stays_paper_only(settings):
    settings.bot_mode = "weather"
    settings.theta_series = "KXBTCD:BTC-USD"
    settings.theta_collect_only = False
    db.init_engine(settings.database_url)
    db.create_all()
    mkts = [_mkt("KXBTCD-26JUL0317-T60500", "greater", 60500.0, None, 19, 21)]
    market_client = FakeKalshi({"KXBTCD": mkts}, {"KXBTCD-26JUL0317-T60500": _ob(19, 21)})
    tracker = ThetaTracker(
        market_client, settings, spot_client=FakeSpotClient(_flat_closes(900)),
    )
    with db.session_scope() as session:
        tracker.run_once(session)
        assert session.scalar(select(func.count()).select_from(m.LiveOrder)) == 0
        assert session.scalar(select(func.count()).select_from(m.PaperTrade)) >= 1


def test_theta_live_total_exposure_cap_blocks(settings):
    """The portfolio-level breaker must gate the THETA path too, not just mmsell — the two
    books share one bankroll, so a cap enforced on only one of them is not a cap."""
    _live_settings(settings, max_total_exposure=2.0)
    db.init_engine(settings.database_url)
    db.create_all()
    client = FakeLiveClient()
    ex = _exec(settings, client)
    with db.session_scope() as session:
        for i in range(3):  # 3 x $0.94 of open NO exposure on OTHER tickers
            session.add(m.Position(
                market_ticker=f"KXOTHER-26-{i}", captured_at=datetime.now(timezone.utc),
                side="no", quantity=1, quantity_fp=-1.0, avg_price=94.0,
                market_exposure=0.94, realized_pnl=None, unrealized_pnl=None, raw_json=None))
        session.flush()
        _enter(ex, session)
    assert client.placed == []
