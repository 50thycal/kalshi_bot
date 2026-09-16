"""The execution research view on the livedash: read-only, no score, one order's trace."""

from __future__ import annotations

import json
import pathlib
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from kalshi_bot import models as m
from kalshi_bot.livedash import execution as ex
from kalshi_bot.livedash import server as srv
from kalshi_bot.models import Base

T0 = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def seeded(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    o = m.LiveOrder(kalshi_order_id="K-1", client_order_id="c-1", market_ticker="KXT-A",
                    event_ticker="KXT", strategy="Fmmsell10", created_at=T0, side="no",
                    action="buy", limit_price=7, quantity=1, status="filled")
    session.add(o)
    session.flush()
    session.add(m.ExecutionOrderContext(
        live_order_id=o.id, kalshi_order_id="K-1", client_order_id="c-1", strategy="Fmmsell10",
        twin_tag="Fmmsell10_pt4", market_ticker="KXT-A", decided_at=T0, no_price=7,
        yes_price=93, quantity=1, candidate_mid=6.0, best_yes_bid=5, best_yes_ask=93,
        spread=88, hours_to_close=2.0, review_tier="graduated", acked_at=T0 + timedelta(seconds=1),
        ack_ts_ms=1758024000123, terminal_reason="filled"))
    for i, (trig, ahead) in enumerate([("at_rest", 40), ("interval", 30), ("event:trade", 12),
                                       ("terminal", None)]):
        session.add(m.LiveOrderQueueTick(
            live_order_id=o.id, kalshi_order_id="K-1", strategy="Fmmsell10",
            market_ticker="KXT-A", captured_at=T0 + timedelta(seconds=10 * (i + 1)),
            queue_position=ahead, contracts_ahead=ahead, limit_price=7, rest_seconds=10 * (i + 1),
            trigger=trig, source="rest_batch",
            features_json={"best_yes_bid": 5, "best_yes_ask": 93, "qty_at_our_price": 10.0,
                           "qty_better": 0.0, "trades_since_placement": i, "book_valid": True}))
    session.add(m.ExecutionFillEvent(
        trade_id="f-1", kalshi_order_id="K-1", market_ticker="KXT-A", ts_ms=1758024050000,
        received_at=T0 + timedelta(seconds=50), yes_price_cents=93, count_fp=1, fee_cost=0.01,
        is_taker=False, outcome_side="no", book_side="ask"))
    session.add(m.ExecutionTradeEvent(
        trade_id="t-1", market_ticker="KXT-A", ts_ms=1758024045000,
        received_at=T0 + timedelta(seconds=45), yes_price_cents=93, no_price_cents=7, count_fp=4,
        taker_outcome_side="yes", taker_book_side="bid", is_block_trade=False))
    session.add(m.ExecutionCollectorEvent(at=T0, kind="thread_started"))
    session.add(m.ExecutionCollectorEvent(at=T0 + timedelta(seconds=1), kind="connected"))
    session.commit()

    class _Scope:
        def __enter__(self):
            return session

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(srv, "session_scope", lambda: _Scope())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.LiveDashHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield base, session
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def test_summary_reports_coverage_funnel_and_queue_at_rest(seeded):
    base, session = seeded
    now = T0 + timedelta(hours=1)
    payload = ex.build_summary(session, hours=24, now=now)
    assert payload["collector"]["alive"] is True
    cov = payload["coverage"]
    assert cov["orders"] == 1 and cov["with_context"] == 1 and cov["with_at_rest_tick"] == 1
    assert cov["with_terminal_tick"] == 1 and cov["ws_fills"] == 1
    assert cov["ticks_by_trigger"] == {"at_rest": 1, "interval": 1, "event:trade": 1,
                                       "terminal": 1}
    assert payload["funnel"]["Fmmsell10"]["filled"] == 1
    assert payload["queue_at_rest"]["p50"] == 40 and payload["queue_at_rest"]["orders"] == 1
    assert payload["recent_orders"][0]["first_ahead"] == 40
    code, via_http = _get(base, "/api/execution/summary?hours=24")
    assert code == 200 and via_http["coverage"]["orders"] == 1


def test_order_trace_carries_context_queue_fills_and_trades(seeded):
    base, _ = seeded
    code, d = _get(base, "/api/execution/orders/K-1")
    assert code == 200
    assert d["order"]["our_yes_price"] == 93 and d["context"]["twin_tag"] == "Fmmsell10_pt4"
    assert [q["trigger"] for q in d["queue"]] == ["at_rest", "interval", "event:trade", "terminal"]
    assert d["queue"][3]["ahead"] is None, "a null sample stays null on the page"
    assert d["fills"][0]["ts_ms"] == 1758024050000 and d["fills"][0]["rest_matched"] is False
    assert d["trades"][0]["at_our_price"] is True
    code, _ = _get(base, "/api/execution/orders/nope")
    assert code == 404
    code, _ = _get(base, "/api/execution/orders/bad%20id%3Cscript%3E")
    assert code == 404


def test_page_serves_and_shows_no_predictive_score(seeded):
    base, _ = seeded
    with urllib.request.urlopen(base + "/execution", timeout=10) as r:
        body = r.read().decode()
    assert r.status == 200 and "<title>Execution Telemetry</title>" in body
    lowered = body.lower()
    for forbidden in ("fill probability:", "expected value:", "p(fill)", "ev(", "score:"):
        assert forbidden not in lowered


def test_execution_module_contains_no_write_path():
    src = pathlib.Path(ex.__file__).read_text()
    for forbidden in ("session.add", "session.delete", "session.commit", "update(", "insert(",
                      "delete("):
        assert forbidden not in src
