from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot.tfav.tracker import TfavTracker


def _flat_closes(
    n_minutes: int, price: float = 60000.0, end: int | None = None
) -> dict[int, float]:
    """`end` defaults to "right now" computed AT CALL TIME, not at module-import time — a slow
    full-suite run can put minutes between import and this test's actual execution, and
    SpotModel.spot_at only tolerates a 6-minute gap, so a frozen default silently starves the
    model and turns `opened` assertions into flaky failures that only show up on a full run."""
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

    def get_markets(self, *, status="open", series_ticker=None, limit=200, cursor=None,
                    event_ticker=None, min_close_ts=None):
        return {"markets": self._markets.get(series_ticker, []), "cursor": ""}

    def get_orderbook(self, ticker, depth=None):
        return self._books[ticker]


def _setup(settings):
    db.init_engine(settings.database_url)
    db.create_all()


def _tracker(settings, markets_by_series, books, closes=None, variants=""):
    settings.theta_series = "KXBTCD:BTC-USD"
    settings.tfav_variants = variants
    client = FakeKalshi(markets_by_series, books)
    spot = FakeSpotClient(closes if closes is not None else _flat_closes(900))
    return TfavTracker(client, settings, spot_client=spot)


def test_tfav_buys_model_underpriced_favorite_only(settings):
    """Flat spot at 60k: a 70c 'greater 59k' favorite has model p=1.0 (100*1 - 70 = 30 >= 5)
    -> BUY YES at the ask. A 70c 'greater 61k' (above spot -> p=0) has negative edge -> skip.
    A 95c favorite is out of the 65-90 band -> skip."""
    _setup(settings)
    mkts = [
        _mkt("KXBTCD-26JUL0317-T59000", "greater", 59000.0, None, 68, 70),   # p=1, edge 30
        _mkt("KXBTCD-26JUL0317-T61000", "greater", 61000.0, None, 68, 70),   # p=0, edge -70
        _mkt("KXBTCD-26JUL0317-T58000", "greater", 58000.0, None, 93, 95),   # band > 90
    ]
    books = {
        "KXBTCD-26JUL0317-T59000": _ob(68, 70),
        "KXBTCD-26JUL0317-T61000": _ob(68, 70),
        "KXBTCD-26JUL0317-T58000": _ob(93, 95),
    }
    tracker = _tracker(settings, {"KXBTCD": mkts}, books)

    with db.session_scope() as session:
        summ = tracker.run_once(session)

    assert summ.products_ok == 1
    assert summ.opened == 1
    assert summ.edged == 1
    with db.session_scope() as session:
        t = session.scalars(select(m.PaperTrade)).one()
        assert t.market_ticker == "KXBTCD-26JUL0317-T59000"
        assert t.strategy == "tfav" and t.side == "yes" and t.action == "buy"
        assert t.assumed_price == 70          # taker buy YES at the ask
        assert t.model_probability == 1.0
        assert t.quantity == settings.tfav_order_size
        assert len(t.fill_assumption) <= 64 and t.fill_assumption.startswith("[tfav]")


def test_tfav_entry_window_gate(settings):
    """A favorite 70min out is beyond the final-hour window (tfav_entry_max 60) -> no trade."""
    _setup(settings)
    mkts = [_mkt("KXBTCD-26JUL0318-T59000", "greater", 59000.0, None, 68, 70, minutes=70)]
    books = {"KXBTCD-26JUL0318-T59000": _ob(68, 70)}
    tracker = _tracker(settings, {"KXBTCD": mkts}, books)
    with db.session_scope() as session:
        summ = tracker.run_once(session)
    assert summ.opened == 0 and summ.in_window == 0


def test_tfav_dedup_and_event_cap(settings):
    _setup(settings)
    settings.tfav_max_per_event = 2
    mkts = [
        _mkt("KXBTCD-26JUL0317-T59000", "greater", 59000.0, None, 68, 70),
        _mkt("KXBTCD-26JUL0317-T58500", "greater", 58500.0, None, 73, 75),
        _mkt("KXBTCD-26JUL0317-T58000", "greater", 58000.0, None, 78, 80),
    ]
    books = {mk["ticker"]: _ob(int(round(float(mk["yes_bid_dollars"]) * 100)),
                               int(round(float(mk["yes_ask_dollars"]) * 100))) for mk in mkts}
    tracker = _tracker(settings, {"KXBTCD": mkts}, books)
    with db.session_scope() as session:
        summ = tracker.run_once(session)
    assert summ.opened == 2 and summ.capped == 1      # third favorite hits the per-event cap
    with db.session_scope() as session:
        summ2 = tracker.run_once(session)
    assert summ2.opened == 0 and summ2.already_open == 2


def test_tfav_no_model_no_trade(settings):
    _setup(settings)
    mkts = [_mkt("KXBTCD-26JUL0317-T59000", "greater", 59000.0, None, 68, 70)]
    books = {"KXBTCD-26JUL0317-T59000": _ob(68, 70)}
    tracker = _tracker(settings, {"KXBTCD": mkts}, books, closes={})
    with db.session_scope() as session:
        summ = tracker.run_once(session)
    assert summ.products_ok == 0 and summ.opened == 0 and summ.skipped_no_model == 1


def test_tfav_variant_spec_parsing(settings):
    settings.tfav_variants = (
        "tfav1:hi=80,edge=8;tfav2:lo=70,ttemax=30;tfav:hi=70;bad:hi=70;tfav3:lo=90,hi=70"
    )
    vs = settings.tfav_variant_list
    tags = [v["tag"] for v in vs]
    # 'tfav' (control collision), 'bad' (prefix), inverted-band tfav3 all rejected
    assert tags == ["tfav1", "tfav2"]
    assert vs[0]["hi"] == 80 and vs[0]["edge"] == 8
    assert vs[1]["lo"] == 70 and vs[1]["ttemax"] == 30
    assert vs[0]["lo"] == settings.tfav_price_lo_cents  # unset inherits base


def test_tfav_variants_gate_by_band(settings):
    """An 88c favorite: control (65-90) + tfav1 (65-80 is out) differ. tfav1 caps hi=80 so it
    skips the 88c one; the control takes it."""
    _setup(settings)
    mkts = [_mkt("KXBTCD-26JUL0317-T57000", "greater", 57000.0, None, 86, 88)]
    books = {"KXBTCD-26JUL0317-T57000": _ob(86, 88)}
    tracker = _tracker(settings, {"KXBTCD": mkts}, books, variants="tfav1:hi=80")
    with db.session_scope() as session:
        summ = tracker.run_once(session)
    assert summ.per_book == {"tfav": 1}    # tfav1's hi=80 excludes the 88c ask
