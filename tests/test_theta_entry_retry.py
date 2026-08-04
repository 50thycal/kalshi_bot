"""theta LIVE entry retry — recovering the one-shot-per-ticker execution gap.

Ports kalshi_bot/mmsell/tracker.py's `_maybe_retry_live` to theta/tracker.py (see
tests/test_mmsell_entry_retry.py for the original). The bug is identical: paper never misses a
fill, so its position stays open for the rest of the entry window and the entry loop's
already-open guard fires every later cycle within that window — which ALSO skipped the live
mirror, giving live exactly one attempt per ticker. Measured live 2026-08-04: of 21 theta4
twin-opened candidates, 3 never got a live fill (2 exchange cross-cancels, 1 hard "post only
cross" reject), and none were retried because theta had no retry path at all.

What must stay true, and is pinned here:
  * a cancelled attempt is retried (the fix), a RESTING one never is (no double-ups);
  * the retry is bounded by theta_live_max_attempts_per_ticker;
  * the retry never chases a market that repriced past theta_live_retry_max_drift_cents;
  * the PAPER books are byte-for-byte unaffected — this only re-fires the live mirror.

Fixtures mirror tests/test_theta_live.py's tracker-wiring section (FakeKalshi/FakeSpotClient) so
the whole cycle is exercised, not just the helper in isolation.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot.live.executor import LiveExecutor
from kalshi_bot.risk.manager import RiskManager
from kalshi_bot.theta.tracker import ThetaTracker
from kalshi_bot.twin import TwinHarness


def _flat_closes(n_minutes: int, price: float = 60000.0, end: int | None = None) -> dict[int, float]:
    """`end` defaults to "right now" computed AT CALL TIME — see test_theta_live.py's copy of
    this helper for why a frozen default would be flaky on a slow full-suite run."""
    if end is None:
        end = int(time.time()) // 60 * 60
    return {end - i * 60: price for i in range(1, n_minutes + 1)}


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


class FakeLiveClient:
    def __init__(self):
        self.placed: list[dict] = []
        self.balance = {"balance": 100_000}

    def create_events_order(self, order):
        self.placed.append(order)
        return {"order": {"order_id": f"K-{len(self.placed)}", "status": "resting"}}

    def get_balance(self):
        return self.balance


TICKER = "KXBTCD-26JUL0317-T60500"


def _armed(settings, **over):
    settings.bot_mode = "live"
    settings.kill_switch = False
    settings.live_enabled = True
    settings.live_strategies = "theta4"
    settings.max_market_exposure = 25.0
    settings.theta_series = "KXBTCD:BTC-USD"
    settings.theta_variants = "theta4:hi=20,ttemax=35,mult=2.0,edge=6"
    settings.theta_live_variants = "theta4"
    settings.theta_live_max_order_dollars = 3.0
    settings.theta_live_max_contracts = 5
    settings.theta_live_max_open_positions = 15
    settings.theta_live_price_offset_cents = 0
    settings.theta_live_max_spread_cents = 40
    settings.theta_live_max_attempts_per_ticker = 6
    settings.theta_live_retry_max_drift_cents = 2
    for k, v in over.items():
        setattr(settings, k, v)
    db.init_engine(settings.database_url)
    db.create_all()
    return settings


def _cycle(settings, market_client, live_client, spot_client):
    tr = ThetaTracker(
        market_client, settings, spot_client=spot_client,
        live_executor=LiveExecutor(live_client, settings, RiskManager(settings)),
        twin_harness=TwinHarness(settings),
    )
    tr._account_state = {"cash_balance": 150.0}
    with db.session_scope() as session:
        return tr.run_once(session)


def _cancel_all_live_orders():
    """The state the fix exists for: our resting order died (exchange cross-cancel, or a hard
    reject at placement) without ever filling, so live holds nothing while paper still 'holds'
    the ticker."""
    with db.session_scope() as session:
        for row in session.scalars(select(m.LiveOrder)):
            row.status = "canceled"
            row.cancel_reason = "timeout"


def _cheap_market():
    # flat spot at 60k: a 20c 'greater 60.5k' strike has model p~=0 (excess ~20 >= edge 6) -> SELL
    # yes 19/21 -> mid 20 (theta4's hi=20), no-bid 79 (live buys NO at 79).
    mkts = [_mkt(TICKER, "greater", 60500.0, None, 19, 21)]
    books = {TICKER: _ob(19, 21)}
    return FakeKalshi({"KXBTCD": mkts}, books), books


# --------------------------------------------------------------------------- the fix


def test_retry_places_a_new_order_after_the_first_was_cancelled(settings):
    _armed(settings)
    market_client, books = _cheap_market()
    live_client = FakeLiveClient()
    spot = FakeSpotClient(_flat_closes(900))
    _cycle(settings, market_client, live_client, spot)   # paper opens + live places attempt #1
    assert len(live_client.placed) == 1
    _cancel_all_live_orders()
    _cycle(settings, market_client, live_client, spot)   # paper skips (already open) — live RETRIES
    assert len(live_client.placed) == 2, "live never got a second attempt at a still-cheap ticker"
    with db.session_scope() as session:
        orders = session.scalars(select(m.LiveOrder).order_by(m.LiveOrder.id)).all()
        assert [o.market_ticker for o in orders] == [TICKER, TICKER]
        assert orders[-1].status == "resting"


def test_retry_is_recorded_on_the_parity_tape_as_a_live_placement(settings):
    """The retry has to show up in the decision tape, or the dashboard would still read the
    epoch as 'live never attempted' on exactly the markets live did fight for."""
    _armed(settings)
    market_client, books = _cheap_market()
    live_client = FakeLiveClient()
    spot = FakeSpotClient(_flat_closes(900))
    _cycle(settings, market_client, live_client, spot)
    _cancel_all_live_orders()
    _cycle(settings, market_client, live_client, spot)
    with db.session_scope() as session:
        row = session.scalars(select(m.LivePaperParityEvent).order_by(
            m.LivePaperParityEvent.id)).all()[-1]
        assert row.parent_outcome == "skip_already_open"   # paper stood down...
        assert row.live_outcome == "placed"                # ...and live did not


def test_retry_does_not_fire_while_the_first_order_is_still_resting(settings):
    _armed(settings)
    market_client, books = _cheap_market()
    live_client = FakeLiveClient()
    spot = FakeSpotClient(_flat_closes(900))
    _cycle(settings, market_client, live_client, spot)
    _cycle(settings, market_client, live_client, spot)   # no cancel: the first order still works
    assert len(live_client.placed) == 1


def test_retry_stops_at_the_attempt_cap(settings):
    _armed(settings, theta_live_max_attempts_per_ticker=2)
    market_client, books = _cheap_market()
    live_client = FakeLiveClient()
    spot = FakeSpotClient(_flat_closes(900))
    for _ in range(5):
        _cycle(settings, market_client, live_client, spot)
        _cancel_all_live_orders()
    assert len(live_client.placed) == 2                  # attempt 1 + one retry, then capped


def test_a_cap_of_one_restores_the_old_one_shot_behaviour(settings):
    _armed(settings, theta_live_max_attempts_per_ticker=1)
    market_client, books = _cheap_market()
    live_client = FakeLiveClient()
    spot = FakeSpotClient(_flat_closes(900))
    for _ in range(3):
        _cycle(settings, market_client, live_client, spot)
        _cancel_all_live_orders()
    assert len(live_client.placed) == 1


def test_retry_declines_once_the_market_drifts_past_the_limit(settings):
    """A retry must never chase a market that has repriced away from the edge the first attempt
    was sized against — that is how a 'recover the miss' rule turns into buying a worse book."""
    _armed(settings)
    market_client, books = _cheap_market()
    live_client = FakeLiveClient()
    spot = FakeSpotClient(_flat_closes(900))
    _cycle(settings, market_client, live_client, spot)   # first attempt priced off no-bid 79
    assert len(live_client.placed) == 1
    _cancel_all_live_orders()
    books[TICKER] = _ob(14, 16)                          # mid 15 (still <= hi 20); no-bid 79->84:
    summ = _cycle(settings, market_client, live_client, spot)  # 5c away, past the 2c rule
    assert len(live_client.placed) == 1
    assert summ.live_retry_drifted >= 1                  # and it is counted, not silently dropped


def test_retry_still_fires_inside_the_drift_tolerance(settings):
    _armed(settings)
    market_client, books = _cheap_market()
    live_client = FakeLiveClient()
    spot = FakeSpotClient(_flat_closes(900))
    _cycle(settings, market_client, live_client, spot)
    _cancel_all_live_orders()
    books[TICKER] = _ob(17, 19)                          # mid 18 (still <= hi 20); no-bid 79->81:
    _cycle(settings, market_client, live_client, spot)   # 2c away, exactly at tolerance
    assert len(live_client.placed) == 2


# --------------------------------------------------------------------------- blast radius


def test_retry_leaves_the_paper_books_untouched(settings):
    """The retry must be live-only. If it moved a paper book by even one trade it would corrupt
    the control the whole theta4 test is measured against."""
    _armed(settings)
    market_client, books = _cheap_market()
    live_client = FakeLiveClient()
    spot = FakeSpotClient(_flat_closes(900))
    _cycle(settings, market_client, live_client, spot)
    with db.session_scope() as session:
        before = session.scalar(select(func.count()).select_from(m.PaperTrade))
    _cancel_all_live_orders()
    _cycle(settings, market_client, live_client, spot)
    with db.session_scope() as session:
        after = session.scalar(select(func.count()).select_from(m.PaperTrade))
    assert after == before                                # not one extra paper trade
    assert len(live_client.placed) == 2                    # while live really did re-post


def test_retry_is_inert_when_the_book_is_not_live(settings):
    """A paper-only deployment has no live executor at all; the retry path must be a no-op."""
    _armed(settings, live_enabled=False)
    market_client, books = _cheap_market()
    live_client = FakeLiveClient()
    spot = FakeSpotClient(_flat_closes(900))
    _cycle(settings, market_client, live_client, spot)
    _cycle(settings, market_client, live_client, spot)
    assert live_client.placed == []
