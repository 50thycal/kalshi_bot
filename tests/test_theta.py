from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot import repository as repo
from kalshi_bot.theta.spot import SpotModel
from kalshi_bot.theta.tracker import ThetaTracker

NOW = int(time.time()) // 60 * 60


def _flat_closes(
    n_minutes: int, price: float = 60000.0, end: int | None = None
) -> dict[int, float]:
    """n_minutes of constant 1-min closes ending at `end` (exclusive of end).

    `end` defaults to "right now" computed AT CALL TIME, not at module-import time — a caller
    that needs a fixed, reproducible anchor (paired with its own `model.spot_at(NOW)` assertions)
    passes `end=NOW` explicitly. Callers that just want fresh-enough data for a live `ThetaTracker`
    run (whose own `now_unix` is real wall-clock time at whenever the test actually executes) must
    NOT anchor to the frozen module-level `NOW`: `SpotModel.spot_at` only tolerates a 6-minute gap,
    and a slow test suite can easily put minutes between module import and this test's execution,
    silently starving the tracker's model and turning `opened` assertions into flaky failures on a
    full-suite run (this exact bug — see the same pattern below, now fixed, in tracker-facing
    callers)."""
    if end is None:
        end = int(time.time()) // 60 * 60
    return {end - i * 60: price for i in range(1, n_minutes + 1)}


def _trending_closes(n_minutes: int, per_min: float, price: float = 60000.0) -> dict[int, float]:
    out = {}
    p = price
    for i in range(n_minutes, 0, -1):
        out[NOW - i * 60] = p
        p *= math.exp(per_min)
    return out


# ---------------------------------------------------------------- SpotModel
def test_spot_model_extremes_on_flat_series():
    model = SpotModel(_flat_closes(900, end=NOW), trail_days=1.0)
    spot = model.spot_at(NOW)
    assert spot == 60000.0
    # flat series: no 30-min return ever exceeds 0 -> P(greater, strike above spot) = 0
    assert model.p_yes(NOW, 30, "greater", 60500.0, None) == 0.0
    # ... and every return stays inside a bucket that contains spot -> P(between) = 1
    assert model.p_yes(NOW, 30, "between", 59500.0, 60500.0) == 1.0
    # a 'less' strike below spot never hits on a flat series
    assert model.p_yes(NOW, 30, "less", None, 59500.0) == 0.0


def test_spot_model_gates():
    model = SpotModel(_flat_closes(100, end=NOW), trail_days=1.0)  # < MIN_SAMPLES
    assert model.p_yes(NOW, 30, "greater", 60500.0, None) is None
    model = SpotModel(_flat_closes(900, end=NOW), trail_days=1.0)
    assert model.p_yes(NOW, 0, "greater", 60500.0, None) is None      # no time left
    assert model.p_yes(NOW, 30, "between", None, None) is None        # unparseable strike
    empty = SpotModel({}, trail_days=1.0)
    assert empty.spot_at(NOW) is None


def test_spot_model_matches_analytic_on_drift():
    # steady +0.005%/min drift: every 30-min return = +0.15%; strikes below/above that
    # move settle deterministically.
    model = SpotModel(_trending_closes(2000, 5e-5), trail_days=2.0)
    spot = model.spot_at(NOW)
    up_less = spot * math.exp(0.0010)  # below the 30-min drift -> always exceeded
    up_more = spot * math.exp(0.0020)  # above it -> never reached
    assert model.p_yes(NOW, 30, "greater", up_less, None) == 1.0
    assert model.p_yes(NOW, 30, "greater", up_more, None) == 0.0


# ---------------------------------------------------------------- tracker
class FakeSpotClient:
    """Serves a canned candle dict once (the bootstrap fetch), then nothing new."""

    def __init__(self, closes: dict[int, float]):
        self._closes = dict(closes)
        self.calls = 0

    def candles(self, product: str, start: int, end: int) -> dict[int, float]:
        self.calls += 1
        return {ts: c for ts, c in self._closes.items() if start <= ts < end}


def _mkt(ticker, strike_type, floor, cap, yb_c, ya_c, minutes=30, vol=5000):
    close = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    out = {
        "ticker": ticker,
        "close_time": close,
        "volume_fp": f"{vol}.0",
        "yes_bid_dollars": f"{yb_c / 100:.4f}",
        "yes_ask_dollars": f"{ya_c / 100:.4f}",
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
        self.orderbook_calls = []

    def get_markets(self, *, status="open", series_ticker=None, limit=200, cursor=None,
                    event_ticker=None, min_close_ts=None):
        return {"markets": self._markets.get(series_ticker, []), "cursor": ""}

    def get_orderbook(self, ticker, depth=None):
        self.orderbook_calls.append(ticker)
        return self._books[ticker]


def _setup(settings):
    db.init_engine(settings.database_url)
    db.create_all()


def _tracker(settings, markets_by_series, books, closes=None, variants=""):
    settings.theta_series = "KXBTCD:BTC-USD"
    settings.theta_variants = variants  # control-only unless a test opts in
    settings.theta_collect_only = False  # exercise entries (prod default is collect-only/shelved)
    client = FakeKalshi(markets_by_series, books)
    spot = FakeSpotClient(closes if closes is not None else _flat_closes(900))
    return ThetaTracker(client, settings, spot_client=spot)


def test_theta_sells_model_overpriced_tail_only(settings):
    """Flat spot at 60k: a 20c 'greater 60.5k' strike has model p=0 (excess 20 >= 5) ->
    SELL (buy NO at the no-bid). A 20c bucket CONTAINING spot has model p=1 -> skip.
    A 50c mid is out of the tail band -> skip."""
    _setup(settings)
    mkts = [
        _mkt("KXBTCD-26JUL0317-T60500", "greater", 60500.0, None, 19, 21),
        _mkt("KXBTCD-26JUL0317-B60250", "between", 59500.0, 60500.0, 19, 21),
        _mkt("KXBTCD-26JUL0317-T59000", "greater", 59000.0, None, 49, 51),
    ]
    books = {mk["ticker"]: _ob(19, 21) for mk in mkts}
    tracker = _tracker(settings, {"KXBTCD": mkts}, books)

    with db.session_scope() as session:
        summ = tracker.run_once(session)

    assert summ.products_ok == 1
    assert summ.opened == 1
    assert summ.edged == 1
    with db.session_scope() as session:
        t = session.scalars(select(m.PaperTrade)).one()
        assert t.market_ticker == "KXBTCD-26JUL0317-T60500"
        assert t.side == "no" and t.action == "buy"
        assert t.assumed_price == 79            # buy NO at no-bid = 100 - ask(21)
        assert t.model_probability == 0.0
        assert t.quantity == settings.theta_order_size
        assert len(t.fill_assumption) <= 64


def test_theta_snapshots_ladder_with_model(settings):
    _setup(settings)
    mkts = [
        _mkt("KXBTCD-26JUL0317-T60500", "greater", 60500.0, None, 19, 21),
        _mkt("KXBTCD-26JUL0317-T59000", "greater", 59000.0, None, 49, 51),
        _mkt("KXBTCD-26JUL0318-T61000", "greater", 61000.0, None, 5, 7, minutes=95),  # > cap
    ]
    books = {mk["ticker"]: _ob(19, 21) for mk in mkts}
    tracker = _tracker(settings, {"KXBTCD": mkts}, books)
    with db.session_scope() as session:
        summ = tracker.run_once(session)
    assert summ.snapshot_rows == 2              # the 95-min market is beyond snapshot_max
    with db.session_scope() as session:
        rows = session.scalars(select(m.CryptoLadderSnapshot)).all()
        by_tk = {r.market_ticker: r for r in rows}
        tail = by_tk["KXBTCD-26JUL0317-T60500"]
        assert tail.model_p == 0.0 and tail.spot == 60000.0
        assert tail.model_excess_cents == 20.0  # mid 20 - 0
        assert by_tk["KXBTCD-26JUL0317-T59000"].mid_cents == 50.0


def test_theta_collect_only_snapshots_but_no_entries(settings):
    """Fully shelved (no live variants): the model-priced ladder snapshot is still written
    for the research dataset, but NO entries are opened even on a clean sell signal."""
    _setup(settings)
    mkts = [_mkt("KXBTCD-26JUL0317-T60500", "greater", 60500.0, None, 19, 21)]  # would sell
    books = {mk["ticker"]: _ob(19, 21) for mk in mkts}
    tracker = _tracker(settings, {"KXBTCD": mkts}, books)
    settings.theta_collect_only = True   # override the _tracker default back to shelved
    settings.theta_live_variants = ""    # fully shelved — nothing trades
    with db.session_scope() as session:
        summ = tracker.run_once(session)
    assert summ.snapshot_rows == 1          # dataset still collected
    assert summ.opened == 0                 # ...but nothing traded
    with db.session_scope() as session:
        assert session.scalars(select(m.PaperTrade)).all() == []
        snap = session.scalars(select(m.CryptoLadderSnapshot)).one()
        assert snap.model_p == 0.0 and snap.model_excess_cents == 20.0  # model still priced


def test_theta_collect_only_live_variant_still_trades(settings):
    """collect-only shelves the control but a designated live variant (theta_live_variants)
    keeps trading — the mechanism that runs the pre-registered theta4 revival test while the
    rest of the family stays snapshot-only."""
    _setup(settings)
    mkts = [_mkt("KXBTCD-26JUL0317-T60500", "greater", 60500.0, None, 19, 21)]
    books = {mk["ticker"]: _ob(19, 21) for mk in mkts}
    # theta9 inherits the base band/window; it is the ONLY book allowed to trade while shelved.
    tracker = _tracker(settings, {"KXBTCD": mkts}, books, variants="theta9:hi=40")
    settings.theta_collect_only = True
    settings.theta_live_variants = "theta9"
    with db.session_scope() as session:
        summ = tracker.run_once(session)
    assert summ.snapshot_rows == 1
    assert summ.opened == 1                  # only the live variant traded
    with db.session_scope() as session:
        t = session.scalars(select(m.PaperTrade)).one()
        assert t.strategy == "theta9"        # control 'theta' stayed shelved
        assert t.side == "no" and t.assumed_price == 79


def test_theta_dedup_and_event_cap(settings):
    _setup(settings)
    settings.theta_max_per_event = 2
    mkts = [
        _mkt("KXBTCD-26JUL0317-T60500", "greater", 60500.0, None, 19, 21),
        _mkt("KXBTCD-26JUL0317-T60750", "greater", 60750.0, None, 14, 16),
        _mkt("KXBTCD-26JUL0317-T61000", "greater", 61000.0, None, 9, 11),
    ]
    books = {mk["ticker"]: _ob(19, 21) for mk in mkts}
    tracker = _tracker(settings, {"KXBTCD": mkts}, books)
    with db.session_scope() as session:
        summ = tracker.run_once(session)
    assert summ.opened == 2 and summ.capped == 1    # third strike hits the per-event cap
    with db.session_scope() as session:
        summ2 = tracker.run_once(session)
    assert summ2.opened == 0 and summ2.already_open == 2


def test_theta_no_model_no_trade(settings):
    """Thin/absent spot data must self-gate: snapshots still written, no entries."""
    _setup(settings)
    mkts = [_mkt("KXBTCD-26JUL0317-T60500", "greater", 60500.0, None, 19, 21)]
    books = {mk["ticker"]: _ob(19, 21) for mk in mkts}
    tracker = _tracker(settings, {"KXBTCD": mkts}, books, closes={})
    with db.session_scope() as session:
        summ = tracker.run_once(session)
    assert summ.products_ok == 0
    assert summ.opened == 0 and summ.skipped_no_model == 1
    assert summ.snapshot_rows == 1


def test_theta_entry_window_gate(settings):
    """A tail 70min out is snapshotted (<=90) but NOT traded (>entry_max 55)."""
    _setup(settings)
    mkts = [_mkt("KXBTCD-26JUL0318-T60500", "greater", 60500.0, None, 19, 21, minutes=70)]
    books = {mk["ticker"]: _ob(19, 21) for mk in mkts}
    tracker = _tracker(settings, {"KXBTCD": mkts}, books)
    with db.session_scope() as session:
        summ = tracker.run_once(session)
    assert summ.snapshot_rows == 1 and summ.in_window == 0 and summ.opened == 0


def test_theta_positions_hold_through_timeout(settings):
    """The paper engine must not force-close theta positions on the 2h max-hold timeout."""
    from kalshi_bot.paper.engine import PaperTradingEngine
    from kalshi_bot.risk.manager import RiskManager
    from kalshi_bot.scanner.metrics import compute_metrics

    _setup(settings)
    with db.session_scope() as session:
        t = repo.create_paper_trade(
            session, signal_id=None, ticker="KXBTCD-26JUL0317-T60500", strategy="theta",
            side="no", action="buy", assumed_price=79, quantity=5,
            fill_assumption="x", entry_fee=0.05,
        )
        t.created_at = datetime.now(timezone.utc) - timedelta(hours=6)
        repo.open_paper_position_for_trade(
            session, ticker=t.market_ticker, strategy="theta", side="no",
            quantity=5, avg_price=79,
        )
    engine = PaperTradingEngine(None, settings, RiskManager(settings))
    market = _mkt("KXBTCD-26JUL0317-T60500", "greater", 60500.0, None, 19, 21)
    metrics = compute_metrics(market, _ob(19, 21), top_n=10)
    with db.session_scope() as session:
        trade = session.scalars(select(m.PaperTrade)).one()
        engine._mark_or_exit(session, trade, metrics)
    with db.session_scope() as session:
        trade = session.scalars(select(m.PaperTrade)).one()
        assert trade.status == "open"           # held to settlement, not timed out


def test_theta_spot_persistence_roundtrip(settings):
    _setup(settings)
    closes = _flat_closes(600)
    with db.session_scope() as session:
        n = repo.insert_spot_candles(session, "BTC-USD", closes)
        assert n == 600
        # idempotent: re-inserting the same minutes adds nothing
        assert repo.insert_spot_candles(session, "BTC-USD", closes) == 0
        latest = repo.latest_spot_minute(session, "BTC-USD")
        assert latest is not None
        since = datetime.now(timezone.utc) - timedelta(days=1)
        loaded = repo.load_spot_closes(session, "BTC-USD", since)
        assert len(loaded) == 600
        pruned = repo.prune_spot_candles(
            session, "BTC-USD", datetime.now(timezone.utc) - timedelta(minutes=300)
        )
        assert pruned > 0


# ---------------------------------------------------------------- revision books
def test_variant_spec_parsing(settings):
    settings.theta_variants = (
        "theta1:hi=20,ttemax=35;theta2:hi=20,ttemax=35,thronly=1;"
        "theta3:edge=12,mult=1.25;theta:hi=10;badtag:hi=10;theta4:hi=5,lo=20"
    )
    vs = settings.theta_variant_list
    tags = [v["tag"] for v in vs]
    # 'theta' (control collision), 'badtag', and inverted-band theta4 are all rejected
    assert tags == ["theta1", "theta2", "theta3"]
    t1, t2, t3 = vs
    assert t1["hi"] == 20 and t1["ttemax"] == 35 and t1["mult"] == 1.0 and not t1["thronly"]
    assert t2["thronly"] is True
    assert t3["edge"] == 12 and t3["mult"] == 1.25
    # unset keys inherit the base knobs
    assert t3["hi"] == settings.theta_price_hi_cents
    assert t1["lo"] == settings.theta_price_lo_cents


def test_vol_mult_widens_distribution():
    """prob_from_returns with k scales thresholds by 1/k: returns of +-0.1%, threshold
    0.15% -> p=0 at k=1 but p=0.5 at k=2 (0.15/2 = 0.075% < 0.1%)."""
    rets = [0.001] * 300 + [-0.001] * 300
    spot = 60000.0
    strike = spot * math.exp(0.0015)
    p1 = SpotModel.prob_from_returns(rets, spot, "greater", strike, None, vol_mult=1.0)
    p2 = SpotModel.prob_from_returns(rets, spot, "greater", strike, None, vol_mult=2.0)
    assert p1 == 0.0
    assert p2 == 0.5


def test_variants_gate_independently(settings):
    """One 25c tail 45min out: control (band<=40, tte<=55) trades it; theta1 (band<=20,
    tte<=35) skips on BOTH gates; theta2 additionally skips 'between'; theta3 (edge 12)
    trades it (excess 25 >= 12). A second 15c between-bucket 30min out: control+theta1
    trade, theta2 skips (thronly), theta3 skips (edge 15 >= 12 -> trades it too)."""
    _setup(settings)
    mkts = [
        _mkt("KXBTCD-26JUL0417-T60500", "greater", 60500.0, None, 24, 26, minutes=45),
        _mkt("KXBTCD-26JUL0417-B60750", "between", 60600.0, 60900.0, 14, 16, minutes=30),
    ]
    books = {
        "KXBTCD-26JUL0417-T60500": _ob(24, 26),
        "KXBTCD-26JUL0417-B60750": _ob(14, 16),
    }
    tracker = _tracker(
        settings, {"KXBTCD": mkts}, books,
        variants="theta1:hi=20,ttemax=35;theta2:hi=20,ttemax=35,thronly=1;theta3:edge=12",
    )
    with db.session_scope() as session:
        summ = tracker.run_once(session)
    # T60500 (25c, 45m, greater): theta yes, theta1 no (band+tte), theta2 no, theta3 yes
    # B60750 (15c, 30m, between): theta yes, theta1 yes, theta2 no (thronly), theta3 yes
    assert summ.per_book == {"theta": 2, "theta1": 1, "theta3": 2}
    with db.session_scope() as session:
        trades = session.scalars(select(m.PaperTrade)).all()
        by_strat = {}
        for t in trades:
            by_strat.setdefault(t.strategy, set()).add(t.market_ticker)
        assert by_strat["theta1"] == {"KXBTCD-26JUL0417-B60750"}
        assert "theta2" not in by_strat
        assert by_strat["theta3"] == {"KXBTCD-26JUL0417-T60500", "KXBTCD-26JUL0417-B60750"}
        for t in trades:
            assert len(t.fill_assumption) <= 64
            assert t.fill_assumption.startswith(f"[{t.strategy}]")


def test_variant_dedup_and_caps_are_per_book(settings):
    _setup(settings)
    settings.theta_max_per_event = 1
    mkts = [
        _mkt("KXBTCD-26JUL0417-T60500", "greater", 60500.0, None, 9, 11, minutes=30),
        _mkt("KXBTCD-26JUL0417-T60750", "greater", 60750.0, None, 9, 11, minutes=30),
    ]
    books = {mk["ticker"]: _ob(9, 11) for mk in mkts}
    tracker = _tracker(settings, {"KXBTCD": mkts}, books, variants="theta1:hi=20")
    with db.session_scope() as session:
        summ = tracker.run_once(session)
    # per-event cap of 1 applies PER BOOK: control 1 + theta1 1
    assert summ.per_book == {"theta": 1, "theta1": 1}
    with db.session_scope() as session:
        summ2 = tracker.run_once(session)
    assert summ2.opened == 0 and summ2.already_open + summ2.capped >= 2
