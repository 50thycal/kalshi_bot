"""theta as a second consumer of the live/paper twin harness (kalshi_bot/twin).

The harness itself is already fully generic (proven in test_live_paper_twin.py against mmsell);
this file covers what's THETA-SPECIFIC and could silently break independently of that:

  * the twin is priced/sized by theta's OWN live knobs, not mmsell's and not paper's
    theta_order_size clip;
  * the twin is exempt from theta_collect_only's variant-shelving gate — it isn't a shelved
    revival experiment, it's the paper shadow of an armed live tag, and gating it the same way
    would silently starve the parity read the moment the family is shelved (the normal
    production state: theta_collect_only=True, theta_live_variants="theta4");
  * the twin still evaluates through the SAME model-probability/edge gate as its parent (since
    it's a `dict(parent)` copy sharing lo/hi/edge/mult/ttemin/ttemax/thronly), not a separate,
    possibly-drifted admission rule;
  * model_probability/edge flow through to the twin's own paper_trades row.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot.live.executor import LiveExecutor
from kalshi_bot.paper.engine import PaperTradingEngine
from kalshi_bot.risk.manager import RiskManager
from kalshi_bot.theta.tracker import ThetaTracker
from kalshi_bot.twin import TwinHarness


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


class FakeSpotClient:
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
        self.placed: list[dict] = []

    def get_markets(self, *, status="open", series_ticker=None, limit=200, cursor=None,
                    event_ticker=None, min_close_ts=None):
        return {"markets": self._markets.get(series_ticker, []), "cursor": ""}

    def get_orderbook(self, ticker, depth=None):
        return self._books[ticker]

    def create_events_order(self, order):
        self.placed.append(order)
        return {"order": {"order_id": f"K-{len(self.placed)}", "status": "resting"}}

    def get_balance(self):
        return {"balance": 100_000}


def _armed(settings, **over):
    """A fully armed live deployment trading theta4, with its twin auto-derived — the standing
    production shape (theta_collect_only stays on; theta4 is the designated live variant)."""
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
    settings.theta_series = "KXBTCD:BTC-USD"
    settings.theta_variants = "theta4:hi=20,ttemax=35,mult=2.0,edge=6"
    settings.theta_live_variants = "theta4"
    # theta_collect_only stays at its True default -- the twin's collect-only exemption is
    # exactly what's under test unless a case overrides it.
    for k, v in over.items():
        setattr(settings, k, v)
    db.init_engine(settings.database_url)
    db.create_all()
    return settings


def _tracker(settings, client):
    ex = LiveExecutor(client, settings, RiskManager(settings))
    tr = ThetaTracker(
        client, settings, spot_client=FakeSpotClient(_flat_closes(900)),
        live_executor=ex, twin_harness=TwinHarness(settings),
    )
    tr._account_state = {"cash_balance": 150.0}
    return tr


def _tail_event():
    """A 20c 'greater 60.5k' strike: model p=0 on a flat spot, excess 20 >= theta4's edge=6."""
    mkts = [_mkt("KXBTCD-26JUL0317-T60500", "greater", 60500.0, None, 19, 21)]
    return {"KXBTCD": mkts}, {"KXBTCD-26JUL0317-T60500": _ob(19, 21)}


def test_theta_twin_exempt_from_collect_only_shelving(settings):
    """The production shape: theta_collect_only=True, theta_live_variants={'theta4'} — the
    twin's tag ('theta4_pt') is NOT a member of that set, so without the exemption this test
    would fail with twin_opened == 0, silently starving the parity read the instant a live
    strategy is armed under the normal (collect-only) operating mode."""
    _armed(settings)  # theta_collect_only left at its True default
    mkts, books = _tail_event()
    client = FakeKalshi(mkts, books)
    with db.session_scope() as session:
        summ = _tracker(settings, client).run_once(session)
    assert summ.per_book.get("theta4", 0) == 1     # the live variant itself traded
    assert summ.twin_opened == 1                    # ...and so did its twin
    with db.session_scope() as session:
        assert session.scalar(
            select(func.count()).select_from(m.PaperTrade).where(
                m.PaperTrade.strategy == "theta4_pt")
        ) == 1


def test_theta_twin_priced_and_sized_like_live_not_like_paper(settings):
    _armed(settings, theta_live_max_order_dollars=5.0)
    mkts, books = _tail_event()
    client = FakeKalshi(mkts, books)
    with db.session_scope() as session:
        _tracker(settings, client).run_once(session)
    assert len(client.placed) == 1        # exactly ONE real order: theta4's, not the twin's
    with db.session_scope() as session:
        twin = session.scalar(select(m.PaperTrade).where(m.PaperTrade.strategy == "theta4_pt"))
        parent = session.scalar(select(m.PaperTrade).where(m.PaperTrade.strategy == "theta4"))
        live = session.scalar(select(m.LiveOrder))
        assert twin.assumed_price == live.limit_price == 79
        # twin size == live's $5/0.79 -> 6, capped at theta_live_max_contracts=5; NOT
        # theta_order_size (paper's clip, default 5 -- coincidentally equal here, so also check
        # the live order agrees with the twin exactly, which paper's own knob can't guarantee)
        assert twin.quantity == live.quantity == 5
        assert parent.quantity == settings.theta_order_size
        assert twin.side == "no" and twin.action == "buy"
        # model_probability/edge flow through to the twin's own row (theta-specific; mmsell
        # has none of its own to pass)
        assert twin.model_probability == parent.model_probability == 0.0
        assert twin.edge == parent.edge == 20.0


def test_theta_twin_places_no_real_order_even_when_allowlisted_by_prefix(settings):
    # "theta4".startswith is not the relevant direction here, but confirm the same defense the
    # mmsell suite pins: a twin tag never reaches the executor regardless of prefix shape.
    _armed(settings)
    mkts, books = _tail_event()
    client = FakeKalshi(mkts, books)
    with db.session_scope() as session:
        _tracker(settings, client).run_once(session)
    assert len(client.placed) == 1
    with db.session_scope() as session:
        strategies = list(session.scalars(select(m.LiveOrder.strategy)))
        assert strategies == ["theta4"]


def test_theta_twin_uses_the_live_open_cap_not_the_paper_cap(settings):
    _armed(settings, theta_live_max_open_positions=1, theta_max_open_positions=200,
           theta_max_per_event=200)
    # three distinct strikes, all clearly ABOVE the flat 60k spot (never exactly AT it, which
    # is an ambiguous boundary for the "greater" probability model) -> p_book=0 on each.
    mkts_list = [
        _mkt(f"KXBTCD-26JUL0317-T{60500 + i * 500}", "greater", 60500.0 + i * 500, None, 19, 21)
        for i in range(3)
    ]
    books = {mk["ticker"]: _ob(19, 21) for mk in mkts_list}
    with db.session_scope() as session:
        summ = _tracker(settings, FakeKalshi({"KXBTCD": mkts_list}, books)).run_once(session)
    assert summ.twin_opened == 1            # capped like live
    assert summ.per_book.get("theta4", 0) == 3   # the paper/live book was not


def test_theta_twin_respects_the_live_spread_sanity_gate(settings):
    _armed(settings, theta_live_max_spread_cents=1)
    mkts, books = _tail_event()
    with db.session_scope() as session:
        summ = _tracker(settings, FakeKalshi(mkts, books)).run_once(session)
    assert summ.twin_opened == 0
    with db.session_scope() as session:
        row = session.scalar(select(m.LivePaperParityEvent))
        assert row.twin_outcome == "skip_spread"


def test_theta_twin_settles_through_the_shared_paper_engine(settings):
    _armed(settings)
    mkts, books = _tail_event()
    client = FakeKalshi(mkts, books)
    with db.session_scope() as session:
        _tracker(settings, client).run_once(session)
    engine = PaperTradingEngine(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        trade = session.scalar(select(m.PaperTrade).where(m.PaperTrade.strategy == "theta4_pt"))
        trade.created_at = trade.created_at.replace(year=2020)  # force well past any timeout
        engine._settle(session, trade, "no")   # the tail missed
        assert trade.status == "settled" and float(trade.pnl) > 0
