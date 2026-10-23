"""The landing page's total P&L chart (`livedash.total_pnl`): every live book's realized money
together, over time.

What must hold: **the month view ends on the headline figure** (same records, same rules — a
shared market counted once, rewards counted, a stopped book counted); a market is placed at
the moment it was realized; unconfirmed rewards never count; points are cumulative and bounded.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kalshi_bot import models as m
from kalshi_bot.livedash import overview as ov
from kalshi_bot.livedash import total_pnl as tp
from kalshi_bot.models import Base

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
SEP = datetime(2026, 9, 20, tzinfo=timezone.utc)


@pytest.fixture
def session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    yield s
    s.close()


def _fill(s, ticker, book, *, at, qty=1):
    oid = f"o-{book}-{ticker}"
    s.add(m.LiveOrder(kalshi_order_id=oid, client_order_id=f"c-{oid}", market_ticker=ticker,
                      strategy=book, created_at=at, side="no", action="buy", limit_price=93,
                      quantity=max(qty, 1), status="filled"))
    s.add(m.Fill(kalshi_fill_id=f"f-{oid}", kalshi_order_id=oid, market_ticker=ticker,
                 filled_at=at + timedelta(minutes=5), side="no", action="buy", price=93,
                 quantity=qty, fee=0.0))
    s.flush()


def _flat(s, ticker, pnl, at):
    s.add(m.Position(market_ticker=ticker, captured_at=at, side="no", quantity=0, quantity_fp=0,
                     realized_pnl=pnl, market_exposure=0))
    s.flush()


def _seed(s):
    _fill(s, "KXA-1", "Hbook", at=SEP - timedelta(days=2))
    _flat(s, "KXA-1", 1.00, SEP)                                           # September win
    _fill(s, "KXA-2", "Hbook", at=NOW - timedelta(days=5))
    _flat(s, "KXA-2", -0.93, NOW - timedelta(days=4))                       # October loss
    _fill(s, "KXA-3", "Fbook", at=NOW - timedelta(days=30))                 # a stopped book...
    _fill(s, "KXA-3", "Hbook", at=NOW - timedelta(days=3), qty=3)           # ...sharing a market
    _flat(s, "KXA-3", 0.28, NOW - timedelta(days=2))
    _fill(s, "KXA-4", "Hbook", at=NOW - timedelta(days=1), qty=0)           # zero contracts
    _flat(s, "KXA-4", 5.00, NOW - timedelta(hours=20))
    for cents, at, transfer in ((209, datetime(2026, 10, 10, 18, tzinfo=timezone.utc), False),
                                (275, NOW - timedelta(hours=3), True)):     # confirmed / not
        s.add(m.IncentiveBalanceObservation(at=at, balance_cents=1, residual_cents=cents,
                                            presumed_transfer=transfer))
    s.flush()


def _at(points, when):
    """The cumulative point in force at `when` (zeros before the first)."""
    out = {"total": 0.0, "trading": 0.0, "rewards": 0.0}
    for p in points:
        if datetime.fromisoformat(p["at"]) < when:
            out = p
    return out


def test_the_month_view_ends_on_the_headline_figure(session):
    _seed(session)
    x = tp.build_total_pnl(session, now=NOW)
    pts = x["points"]
    month = {k: pts[-1][k] - _at(pts, ov.month_start(NOW))[k] for k in ("total", "trading", "rewards")}
    h = ov.build_overview(session, now=NOW)["headline"]
    assert month["total"] == pytest.approx(h["month_realized_usd"])
    assert month["trading"] == pytest.approx(h["month_trading_usd"])
    assert month["rewards"] == pytest.approx(h["month_rewards_usd"]) == pytest.approx(2.09)
    # All time: September's win, October's two markets (the shared one once), one reward.
    assert pts[-1] == {**pts[-1], "trading": pytest.approx(1.00 - 0.93 + 0.28),
                       "rewards": pytest.approx(2.09), "total": pytest.approx(0.35 + 2.09)}
    assert (x["markets_settled"], x["reward_payouts"]) == (3, 1)
    assert x["month_start"].startswith("2026-10-01")


def test_points_are_placed_when_the_money_was_realized_and_rise_in_time(session):
    _fill(session, "KXB-1", "Hbook", at=SEP - timedelta(days=1))
    _flat(session, "KXB-1", 0.50, SEP)
    _flat(session, "KXB-1", 0.50, NOW - timedelta(hours=1))                 # re-reported later
    pts = tp.build_total_pnl(session, now=NOW)["points"]
    assert [p["at"][:10] for p in pts] == ["2026-09-20"]
    assert pts[0]["total"] == pytest.approx(0.50)


def test_points_are_bucketed_and_bounded(session, monkeypatch):
    for i in range(40):
        _fill(session, f"KXC-{i}", "Hbook", at=SEP)
        _flat(session, f"KXC-{i}", 0.10, SEP + timedelta(hours=i))
    monkeypatch.setattr(tp, "MAX_POINTS", 10)
    pts = tp.build_total_pnl(session, now=NOW)["points"]
    assert len(pts) <= 10
    assert pts[-1]["total"] == pytest.approx(4.0)
    assert [p["total"] for p in pts] == sorted(p["total"] for p in pts)


def test_an_empty_account_has_no_points(session):
    x = tp.build_total_pnl(session, now=NOW)
    assert x["points"] == [] and x["markets_settled"] == 0


def test_the_cache_serves_one_build_inside_its_ttl(session, monkeypatch):
    calls = []
    monkeypatch.setattr(tp, "build_total_pnl", lambda s: calls.append(1) or {"points": []})
    cache = tp.TotalPnlCache(ttl=60)
    cache.build(session)
    cache.build(session)
    assert len(calls) == 1
