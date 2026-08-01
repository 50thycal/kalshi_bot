"""mmsell LIVE entry retry — recovering the one-shot-per-ticker execution gap.

The bug this closes: paper never misses a fill, so its position stays open to settlement and the
entry loop's `skip_already_open` guard fires every later cycle — which ALSO skipped the live
mirror. Live therefore got exactly ONE attempt per ticker for the ticker's whole life. Measured
live 2026-07-31: all 71 tickers in the epoch had exactly 1 live order, 29 never filled, and the
missed set earned the same in paper as the captured one (6.15 vs 6.26 c/contract) — lost volume,
not adverse selection dodged.

What must stay true, and is pinned here:
  * a cancelled attempt is retried (the fix), a RESTING one never is (no double-ups);
  * the retry is bounded by mmsell_live_max_attempts_per_ticker;
  * the retry never chases a market that repriced past mmsell_live_retry_max_drift_cents;
  * the PAPER books are byte-for-byte unaffected — this only re-fires the live mirror.

Fixtures mirror tests/test_live_paper_twin.py so the whole tracker cycle is exercised, not just
the helper in isolation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot.live.executor import LiveExecutor
from kalshi_bot.mmsell.tracker import MmSellTracker
from kalshi_bot.risk.manager import RiskManager
from kalshi_bot.twin import TwinHarness


def _mkt(ticker, sub, yes_bid_c, yes_ask_c, vol=500, hours=48):
    close = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    return {
        "ticker": ticker, "yes_sub_title": sub, "close_time": close,
        "volume_fp": f"{vol}.0",
        "yes_bid_dollars": f"{yes_bid_c / 100:.4f}",
        "yes_ask_dollars": f"{yes_ask_c / 100:.4f}",
    }


def _ob(yes_bid_c, yes_ask_c, depth=300):
    return {"orderbook_fp": {
        "yes_dollars": [[f"{yes_bid_c / 100:.4f}", str(depth)]],
        "no_dollars": [[f"{(100 - yes_ask_c) / 100:.4f}", str(depth)]],
    }}


class FakeClient:
    def __init__(self, events, books):
        self._events = events
        self._books = books
        self.placed: list[dict] = []

    def get_exchange_status(self):
        return {"exchange_active": True, "trading_active": True}

    def get_events(self, status="open", with_nested_markets=True, limit=200, cursor=None):
        return {"events": self._events, "cursor": ""}

    def get_orderbook(self, ticker, depth=None):
        return self._books[ticker]

    def create_events_order(self, order):
        self.placed.append(order)
        return {"order": {"order_id": f"K-{len(self.placed)}", "status": "resting"}}

    def get_balance(self):
        return {"balance": 100_000}


TICKER = "KXTEAM-26-A"


def _armed(settings, *, variants="mmsell10:lo=5,hi=10,maxyes=7", **over):
    settings.bot_mode = "live"
    settings.kill_switch = False
    settings.live_enabled = True
    settings.live_strategies = "mmsell10"
    settings.live_max_order_dollars = 1.0
    settings.max_order_size = 100
    settings.max_market_exposure = 25.0
    settings.mmsell_live_max_open_positions = 60
    settings.mmsell_live_price_offset_cents = 0
    settings.mmsell_live_max_spread_cents = 40
    settings.mmsell_variants = variants
    settings.mmsell_capture_candidates = False
    settings.mmsell_live_max_attempts_per_ticker = 6
    settings.mmsell_live_retry_max_drift_cents = 2
    for k, v in over.items():
        setattr(settings, k, v)
    db.init_engine(settings.database_url)
    db.create_all()
    return settings


def _tracker(settings, client):
    tr = MmSellTracker(client, settings,
                       live_executor=LiveExecutor(client, settings, RiskManager(settings)),
                       twin_harness=TwinHarness(settings))
    tr._account_state = {"cash_balance": 150.0}
    return tr


def _cycle(settings, client):
    with db.session_scope() as session:
        return _tracker(settings, client).run_once(session)


def _cancel_all_live_orders():
    """The state the fix exists for: our resting order died (exchange cross-cancel, or the old
    timeout) without ever filling, so live holds nothing while paper still 'holds' the ticker."""
    with db.session_scope() as session:
        for row in session.scalars(select(m.LiveOrder)):
            row.status = "canceled"
            row.cancel_reason = "timeout"


# yes 5/7 -> mid 6 (inside mmsell10's 5-10 band), yes-ask 7 <= maxyes 7, so live buys NO at 93.
def _cheap_event():
    return ({"event_ticker": "KXTEAM-26", "series_ticker": "KXTEAM",
             "markets": [_mkt(TICKER, "A", 5, 7)]}, {TICKER: _ob(5, 7)})


# --------------------------------------------------------------------------- the fix


def test_retry_places_a_new_order_after_the_first_was_cancelled(settings):
    _armed(settings)
    ev, books = _cheap_event()
    client = FakeClient([ev], books)
    _cycle(settings, client)                      # paper opens + live places attempt #1
    assert len(client.placed) == 1
    _cancel_all_live_orders()
    _cycle(settings, client)                      # paper skips (already open) — live RETRIES
    assert len(client.placed) == 2, "live never got a second attempt at a still-cheap ticker"
    with db.session_scope() as session:
        orders = session.scalars(select(m.LiveOrder).order_by(m.LiveOrder.id)).all()
        assert [o.market_ticker for o in orders] == [TICKER, TICKER]
        assert orders[-1].status == "resting"


def test_retry_is_recorded_on_the_parity_tape_as_a_live_placement(settings):
    """The retry has to show up in the decision tape, or the dashboard would still read the
    epoch as 'live never attempted' on exactly the markets live did fight for."""
    _armed(settings)
    ev, books = _cheap_event()
    client = FakeClient([ev], books)
    _cycle(settings, client)
    _cancel_all_live_orders()
    _cycle(settings, client)
    with db.session_scope() as session:
        row = session.scalars(select(m.LivePaperParityEvent).order_by(
            m.LivePaperParityEvent.id)).all()[-1]
        assert row.parent_outcome == "skip_already_open"   # paper stood down...
        assert row.live_outcome == "placed"                # ...and live did not


def test_retry_does_not_fire_while_the_first_order_is_still_resting(settings):
    _armed(settings)
    ev, books = _cheap_event()
    client = FakeClient([ev], books)
    _cycle(settings, client)
    _cycle(settings, client)                      # no cancel: the first order is still working
    assert len(client.placed) == 1


def test_retry_stops_at_the_attempt_cap(settings):
    _armed(settings, mmsell_live_max_attempts_per_ticker=2)
    ev, books = _cheap_event()
    client = FakeClient([ev], books)
    for _ in range(5):
        _cycle(settings, client)
        _cancel_all_live_orders()
    assert len(client.placed) == 2                # attempt 1 + one retry, then capped


def test_a_cap_of_one_restores_the_old_one_shot_behaviour(settings):
    _armed(settings, mmsell_live_max_attempts_per_ticker=1)
    ev, books = _cheap_event()
    client = FakeClient([ev], books)
    for _ in range(3):
        _cycle(settings, client)
        _cancel_all_live_orders()
    assert len(client.placed) == 1


def test_retry_declines_once_the_market_drifts_past_the_limit(settings):
    """A retry must never chase a market that has repriced away from the edge the first attempt
    was sized against — that is how a 'recover the miss' rule turns into buying a worse book."""
    # a permissive band, so the drifted quote is still a candidate and the DRIFT rule is what
    # declines it rather than the band check upstream.
    _armed(settings, variants="mmsell10:lo=1,hi=40,maxyes=40")
    ev, books = _cheap_event()
    client = FakeClient([ev], books)
    _cycle(settings, client)                      # first attempt priced off no-bid 93
    assert len(client.placed) == 1
    _cancel_all_live_orders()
    books[TICKER] = _ob(5, 12)                    # no-bid 93 -> 88: 5c away, past the 2c rule
    _cycle(settings, client)
    assert len(client.placed) == 1

    summ = _cycle(settings, client)
    assert summ.live_retry_drifted >= 1           # and it is counted, not silently dropped


def test_retry_still_fires_inside_the_drift_tolerance(settings):
    _armed(settings, variants="mmsell10:lo=1,hi=40,maxyes=40")
    ev, books = _cheap_event()
    client = FakeClient([ev], books)
    _cycle(settings, client)
    _cancel_all_live_orders()
    books[TICKER] = _ob(5, 9)                     # no-bid 93 -> 91: 2c away, exactly at tolerance
    _cycle(settings, client)
    assert len(client.placed) == 2


# --------------------------------------------------------------------------- blast radius


def test_retry_leaves_the_paper_books_untouched(settings):
    """The retry must be live-only. If it moved a paper book by even one trade it would corrupt
    the control the whole mmsell experiment is measured against."""
    _armed(settings)
    ev, books = _cheap_event()
    client = FakeClient([ev], books)
    _cycle(settings, client)
    with db.session_scope() as session:
        before = session.scalar(select(func.count()).select_from(m.PaperTrade))
    _cancel_all_live_orders()
    _cycle(settings, client)
    with db.session_scope() as session:
        after = session.scalar(select(func.count()).select_from(m.PaperTrade))
    assert after == before                        # not one extra paper trade
    assert len(client.placed) == 2                # while live really did re-post


def test_retry_is_inert_when_the_book_is_not_live(settings):
    """A paper-only deployment has no live executor at all; the retry path must be a no-op."""
    _armed(settings, live_enabled=False)
    ev, books = _cheap_event()
    client = FakeClient([ev], books)
    _cycle(settings, client)
    _cycle(settings, client)
    assert client.placed == []
