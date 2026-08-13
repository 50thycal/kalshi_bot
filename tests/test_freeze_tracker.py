"""FREEZE tracker — arm routing, favorite selection, and the guards that keep the control honest.

The load-bearing behaviours pinned here:
  * a dark-window market feeds only the `dark=1` arms and an open-window market only the control;
  * Pyth-continuous commodities (metals/energy) are never traded, in either arm;
  * the "favorite" is the side the book prices above 50c, bought at ITS ask;
  * per-book discount / price filters gate independently, so the four arms really are different
    experiments rather than four copies of one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kalshi_bot.config import Settings
from kalshi_bot.freeze.tracker import FreezeTracker, classify


def _et_ts(y, m, d, hh, mm) -> float:
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp() + 4 * 3600


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


class _FakeClient:
    """Returns one page of markets per series and a fixed orderbook."""

    def __init__(self, markets, yes_bid=80, yes_ask=84):
        self._markets = markets
        self._yes_bid, self._yes_ask = yes_bid, yes_ask
        self.orderbook_calls = 0

    def get_markets(self, **kw):
        series = kw.get("series_ticker")
        return {"markets": [m for m in self._markets if m["ticker"].startswith(series)],
                "cursor": None}

    def get_orderbook(self, ticker, depth=None):
        self.orderbook_calls += 1
        # Kalshi's book is quoted in yes-bids and no-bids; no_bid = 100 - yes_ask.
        return {"orderbook": {"yes": [[self._yes_bid, 500]],
                              "no": [[100 - self._yes_ask, 500]]}}


class _FakeSession:
    pass


def _mkt(ticker, title, close_ts):
    return {"ticker": ticker, "title": title, "close_time": _iso(close_ts),
            "status": "open", "volume": 5000, "open_interest": 5000}


def _settings(**kw):
    base = dict(freeze_enabled=True, freeze_order_size=5, freeze_max_open_positions=40,
                freeze_min_discount_cents=3.0, freeze_series="KXCORN,KXGOLD",
                paper_fees_enabled=True)
    base.update(kw)
    return Settings(**base)


def _run(monkeypatch, tracker, session, now_ts):
    """Run one cycle with the clock pinned, capturing created paper trades."""
    created: list[dict] = []
    import kalshi_bot.freeze.tracker as mod

    monkeypatch.setattr(mod.time, "time", lambda: now_ts)
    monkeypatch.setattr(mod.repo, "count_open_paper_positions", lambda s, tag=None: 0)
    monkeypatch.setattr(mod.repo, "open_paper_position_tickers", lambda s, tag=None: set())
    monkeypatch.setattr(mod.repo, "open_paper_position_for_trade",
                        lambda *a, **k: None)
    monkeypatch.setattr(mod.repo, "create_paper_trade",
                        lambda session, **kw: created.append(kw))
    summ = tracker.run_once(session)
    return summ, created


# --- classification ----------------------------------------------------------------

def test_classify_maps_grain_and_metal():
    assert classify("KXCORN-26AUG15 Corn above $4.20") == ("corn", "grain")
    assert classify("KXGOLD-26AUG15 Gold above 3000") == ("gold", "metal")
    assert classify("KXNBA-LAL Lakers win") is None


# --- arm routing -------------------------------------------------------------------

def test_dark_window_feeds_only_the_dark_arms(monkeypatch):
    """Wed 16:00 ET with a 18:00 ET close: grains are dark and the window does not reopen, so
    the three dark arms take it and the open-window control does not."""
    now = _et_ts(2026, 8, 12, 16, 0)
    mkts = [_mkt("KXCORN-1", "Corn above $4.20", _et_ts(2026, 8, 12, 18, 0))]
    tr = FreezeTracker(_FakeClient(mkts), _settings())
    summ, created = _run(monkeypatch, tr, _FakeSession(), now)

    assert summ.pinned == 1
    tags = sorted(t["strategy"] for t in created)
    # 84c ask => 16c discount, which clears every dark arm's bar (freeze2 needs >=8c,
    # freeze4's 95c cap is not binding). The control must stay out.
    assert tags == ["freeze1", "freeze2", "freeze4"]
    assert summ.per_book.get("freeze3") is None


def test_deep_discount_arm_takes_a_cheap_favorite(monkeypatch):
    """At a 84c ask the discount is 16c, so freeze2 (>=8c) must also fire — this guards the
    discount filter against an off-by-sign that would silently make freeze2 inert."""
    now = _et_ts(2026, 8, 12, 16, 0)
    mkts = [_mkt("KXCORN-1", "Corn above $4.20", _et_ts(2026, 8, 12, 18, 0))]
    tr = FreezeTracker(_FakeClient(mkts, yes_bid=80, yes_ask=84), _settings())
    _, created = _run(monkeypatch, tr, _FakeSession(), now)
    assert "freeze2" in {t["strategy"] for t in created}


def test_open_window_feeds_only_the_control(monkeypatch):
    """Wed 11:00 ET: the CBOT day session is live, so nothing is pinned and only freeze3 trades."""
    now = _et_ts(2026, 8, 12, 11, 0)
    mkts = [_mkt("KXCORN-1", "Corn above $4.20", _et_ts(2026, 8, 12, 13, 0))]
    tr = FreezeTracker(_FakeClient(mkts), _settings())
    summ, created = _run(monkeypatch, tr, _FakeSession(), now)

    assert summ.pinned == 0
    assert {t["strategy"] for t in created} == {"freeze3"}


def test_pyth_continuous_commodity_never_trades(monkeypatch):
    """Gold on a Saturday: the exchange is shut but Pyth keeps pricing, so this is NOT a freeze.
    Trading it is the v1 lookahead bug — neither arm may take it."""
    now = _et_ts(2026, 8, 15, 3, 0)
    mkts = [_mkt("KXGOLD-1", "Gold above 3000", _et_ts(2026, 8, 15, 9, 0))]
    tr = FreezeTracker(_FakeClient(mkts), _settings())
    summ, created = _run(monkeypatch, tr, _FakeSession(), now)

    assert summ.commodity == 1 and summ.freeze_eligible == 0
    assert created == []


def test_straddling_close_is_not_pinned_and_not_controlled(monkeypatch):
    """Now is dark but the close is after the reopen: the source can still move the outcome.
    That is neither a valid pin nor a valid open-window control, so nothing trades."""
    now = _et_ts(2026, 8, 12, 16, 0)
    mkts = [_mkt("KXCORN-1", "Corn above $4.20", _et_ts(2026, 8, 12, 22, 0))]
    tr = FreezeTracker(_FakeClient(mkts), _settings())
    summ, created = _run(monkeypatch, tr, _FakeSession(), now)

    assert summ.pinned == 0
    assert created == []


# --- entry shape -------------------------------------------------------------------

def test_buys_the_favorite_at_its_ask(monkeypatch):
    now = _et_ts(2026, 8, 12, 16, 0)
    mkts = [_mkt("KXCORN-1", "Corn above $4.20", _et_ts(2026, 8, 12, 18, 0))]
    tr = FreezeTracker(_FakeClient(mkts, yes_bid=80, yes_ask=84), _settings())
    _, created = _run(monkeypatch, tr, _FakeSession(), now)

    t = next(t for t in created if t["strategy"] == "freeze1")
    assert t["side"] == "yes" and t["action"] == "buy"
    assert t["assumed_price"] == 84          # the YES ask, not the mid or the bid
    assert t["edge"] == 16                   # discount to certainty


def test_buys_the_no_side_when_no_is_the_favorite(monkeypatch):
    """yes mid 20c => NO is the favorite; the book must buy NO at the NO ask (100-yes_bid)."""
    now = _et_ts(2026, 8, 12, 16, 0)
    mkts = [_mkt("KXCORN-1", "Corn above $4.20", _et_ts(2026, 8, 12, 18, 0))]
    tr = FreezeTracker(_FakeClient(mkts, yes_bid=16, yes_ask=20), _settings())
    _, created = _run(monkeypatch, tr, _FakeSession(), now)

    t = next(t for t in created if t["strategy"] == "freeze1")
    assert t["side"] == "no"
    assert t["assumed_price"] == 84          # no_ask = 100 - yes_bid = 84


def test_price_cap_arm_skips_the_expensive_end(monkeypatch):
    """A 97c favorite has only 3c of headroom: freeze4 (maxprice=95) must skip it while the
    uncapped dark arm still takes it."""
    now = _et_ts(2026, 8, 12, 16, 0)
    mkts = [_mkt("KXCORN-1", "Corn above $4.20", _et_ts(2026, 8, 12, 18, 0))]
    tr = FreezeTracker(_FakeClient(mkts, yes_bid=95, yes_ask=97), _settings())
    _, created = _run(monkeypatch, tr, _FakeSession(), now)

    tags = {t["strategy"] for t in created}
    assert "freeze1" in tags                 # 3c discount clears the 3c bar
    assert "freeze4" not in tags             # but 97c is over the cap
    assert "freeze2" not in tags             # and 3c is under the 8c deep-discount bar


def test_no_discount_left_is_skipped(monkeypatch):
    """At a 99c ask there is 1c of discount — under every arm's bar, so nothing trades."""
    now = _et_ts(2026, 8, 12, 16, 0)
    mkts = [_mkt("KXCORN-1", "Corn above $4.20", _et_ts(2026, 8, 12, 18, 0))]
    tr = FreezeTracker(_FakeClient(mkts, yes_bid=98, yes_ask=99), _settings())
    summ, created = _run(monkeypatch, tr, _FakeSession(), now)

    assert created == [] and summ.skipped_discount > 0


def test_no_books_configured_is_a_clean_noop(monkeypatch):
    tr = FreezeTracker(_FakeClient([]), _settings(freeze_books=""))
    summ, created = _run(monkeypatch, tr, _FakeSession(), _et_ts(2026, 8, 12, 16, 0))
    assert created == [] and summ.markets_seen == 0
