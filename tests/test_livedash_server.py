"""The live/paper dashboard HTTP surface: routing, parameters, and read-only-ness.

The read-only guarantee is the important one here — this page observes a book that
is trading real money, so it is checked at the protocol level (only GET/HEAD are
implemented) as well as in the data layer (see test_livedash's no-write-path test).
"""

from __future__ import annotations

import json
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
from kalshi_bot.livedash import server as srv
from kalshi_bot.models import Base

T0 = datetime(2026, 7, 26, 21, 0, tzinfo=timezone.utc)


@pytest.fixture
def live_server(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(m.LivePaperTwin(twin_tag="mm10_pt", live_tag="mmsell10", started_at=T0,
                                params_json={"lo": 5, "hi": 10}))
    session.add(m.LiveOrder(kalshi_order_id="o1", market_ticker="T1", strategy="mmsell10",
                            created_at=T0 + timedelta(minutes=1), side="no", action="buy",
                            limit_price=93, quantity=2, status="filled"))
    session.add(m.Fill(kalshi_fill_id="f1", kalshi_order_id="o1", market_ticker="T1",
                       filled_at=T0 + timedelta(minutes=2), side="no", action="buy",
                       price=93, quantity=2, fee=0.01))
    session.add(m.PaperTrade(market_ticker="T1", strategy="mm10_pt",
                             created_at=T0 + timedelta(minutes=1), side="no", action="buy",
                             assumed_price=93, quantity=2, fees=0.01, status="open",
                             legacy=False))
    session.add(m.MmSellPositionTick(market_ticker="T1", captured_at=T0 + timedelta(hours=1),
                                     no_bid=94, no_ask=96, yes_bid=4, yes_ask=6, mid=5))
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
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return r.status, json.loads(r.read().decode())


def _status(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_page_and_health(live_server):
    base, _ = live_server
    with urllib.request.urlopen(base + "/", timeout=10) as r:
        body = r.read().decode()
    assert r.status == 200 and "<title>Live vs Paper</title>" in body
    # the run is chosen at runtime from the API, never baked into the page
    assert "mm10_pt" not in body and "mmsell10" not in body
    with urllib.request.urlopen(base + "/healthz", timeout=10) as r:
        assert r.read() == b"ok"


def test_all_routes_answer(live_server):
    base, _ = live_server
    code, runs = _get(base, "/api/runs")
    assert code == 200 and runs["default_run"] == "mm10_pt"

    code, run = _get(base, "/api/runs/mm10_pt")
    assert code == 200 and run["pair"]["live_tag"] == "mmsell10"

    code, series = _get(base, "/api/runs/mm10_pt/series")
    assert code == 200 and "pnl" in series

    code, orders = _get(base, "/api/runs/mm10_pt/orders")
    assert code == 200 and orders["total"] == 2      # one live, one paper

    code, events = _get(base, "/api/runs/mm10_pt/events")
    assert code == 200 and events["twin_tag"] == "mm10_pt"


def test_the_page_loads_in_phases_and_never_pins_itself_to_a_retired_run():
    """Source-level guards, in the spirit of the no-write-path test: these are the two
    properties a later edit could silently undo, and both were operator-visible faults.

    The page used to show nothing until every request returned, and its 60s timer re-ran
    the whole selection — which is how a stale response ended up overwriting a newer one
    and the board reverted to the pair you had just navigated away from."""
    import pathlib as _pathlib

    page = (_pathlib.Path(srv.__file__).parent / "static" / "index.html").read_text()

    # boot asks for the cheap selector view; the summary list is fetched separately
    assert "/api/runs?view=selector" in page
    assert "async function loadHistory()" in page

    # a load carries the generation it began under, and a stale one is dropped
    assert "const stale = g => g !== GEN;" in page
    assert "AbortController" in page

    # the timer refreshes in place instead of re-running the selection
    assert "setInterval(refreshInPlace, 60000);" in page
    assert "setInterval(() => { if(S.tag) selectRun(S.tag)" not in page

    # and a retired pair is never written back into the URL
    assert "ended(tag) ? location.pathname" in page


def test_selector_view_is_the_cheap_half_of_the_run_list(live_server):
    """Phase 1 of the page load: which pairs exist, so the picker is on screen before the
    per-run P&L columns have been rebuilt."""
    base, _ = live_server
    code, sel = _get(base, "/api/runs?view=selector")
    assert code == 200 and sel["summaries"] is False
    assert [r["twin_tag"] for r in sel["runs"]] == ["mm10_pt"]
    assert "live_realized_usd" not in sel["runs"][0]
    assert "unpaired_live_strategies" not in sel

    _, full = _get(base, "/api/runs")
    assert full["summaries"] is True and "live_realized_usd" in full["runs"][0]
    # an unrecognised view is the full list, not an error
    _, junk = _get(base, "/api/runs?view=nonsense")
    assert junk["summaries"] is True


def test_the_incumbent_leg_is_carried_only_when_asked_for(live_server):
    base, _ = live_server
    _, default = _get(base, "/api/runs/mm10_pt")
    assert default["incumbent_paper"] is None
    _, asked = _get(base, "/api/runs/mm10_pt?incumbent=1")
    assert asked["incumbent_paper"]["tag"] == "mmsell10"


def test_query_parameters_are_honoured_and_clamped(live_server):
    base, _ = live_server
    _, live_only = _get(base, "/api/runs/mm10_pt/orders?env=live")
    assert {o["environment"] for o in live_only["orders"]} == {"live"}

    _, ev = _get(base, "/api/runs/mm10_pt/events?category=decisions&limit=5")
    assert ev["category"] == "decisions" and ev["limit"] == 5

    # junk falls back instead of erroring
    _, bad = _get(base, "/api/runs/mm10_pt/events?category=zzz&limit=abc")
    assert bad["category"] == "all" and bad["limit"] == 50
    _, s = _get(base, "/api/runs/mm10_pt/series?since=notadate&points=99999")
    assert "pnl" in s


def test_unknown_run_and_bad_tag_are_404(live_server):
    base, _ = live_server
    assert _status(base, "/api/runs/nope_pt") == 404
    assert _status(base, "/api/runs/..%2Fetc%2Fpasswd") == 404
    assert _status(base, "/api/runs/mm10_pt/nonsense") == 404
    assert _status(base, "/nope") == 404


def test_only_read_methods_exist(live_server):
    """A dashboard for a live money book must not be able to act on it."""
    base, _ = live_server
    for verb in ("POST", "PUT", "DELETE", "PATCH"):
        assert not hasattr(srv.LiveDashHandler, f"do_{verb}")
    req = urllib.request.Request(base + "/api/runs", method="POST", data=b"{}")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=10)
    assert exc.value.code == 501


def test_backend_failure_returns_a_generic_envelope(live_server, monkeypatch):
    base, _ = live_server

    def boom(*a, **k):
        raise RuntimeError("SECRET-DETAIL postgresql://user:pw@host/db")

    monkeypatch.setattr(srv.data, "build_runs", boom)
    try:
        urllib.request.urlopen(base + "/api/runs", timeout=10)
        raise AssertionError("expected an error status")
    except urllib.error.HTTPError as exc:
        assert exc.code == 503
        body = exc.read().decode()
    assert "SECRET-DETAIL" not in body and "postgresql://" not in body
    assert json.loads(body)["error"] == "dashboard temporarily unavailable"
