"""The dashboard HTTP surface: routing, query-parameter handling, read-only-ness.

Exercises the real handler over a real socket so the routes, the parameter parsing
and the error envelope are all covered end to end.
"""

from __future__ import annotations

import json
import random
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from kalshi_bot.dashboard import server as srv
from kalshi_bot.evo import budgets, llm
from kalshi_bot.evo.cohorts import ensure_current_cohort
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.evolution import create_agent
from kalshi_bot.models import Base


@pytest.fixture
def live_server(monkeypatch):
    """The handler bound to an in-memory database, on a real ephemeral port."""
    # StaticPool + check_same_thread=False: every request thread must reach the SAME
    # in-memory database (a fresh connection would otherwise get an empty one).
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    settings = EvoSettings(_env_file=None)
    llm.seed_model_prices(session, settings)
    cohort = ensure_current_cohort(session, settings)
    agents = []
    for i in range(2):
        a = create_agent(session, settings, cohort, random.Random(i),
                         origin="founder", slot_key=f"founder:{i}")
        budgets.ensure_budgets(session, a.agent_uuid, cohort.id, settings)
        agents.append(a)
    session.commit()

    class _Scope:
        def __enter__(self):
            return session

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(srv, "session_scope", lambda: _Scope())
    monkeypatch.setattr(srv, "get_evo_settings", lambda: settings)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.DashboardHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield base, agents, session
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


def test_index_and_health(live_server):
    base, _, _ = live_server
    with urllib.request.urlopen(base + "/", timeout=10) as r:
        body = r.read().decode()
    assert r.status == 200 and "<title>Evolution Fleet</title>" in body
    with urllib.request.urlopen(base + "/healthz", timeout=10) as r:
        assert r.read() == b"ok"


def test_every_api_route_answers(live_server):
    base, agents, _ = live_server
    uuid = agents[0].agent_uuid

    code, fleet = _get(base, "/api/fleet")
    assert code == 200 and fleet["summary"]["bots"] == 2

    code, bots = _get(base, "/api/bots")
    assert code == 200 and bots["total"] == 2

    code, detail = _get(base, f"/api/bots/{uuid}")
    assert code == 200 and detail["uuid"] == uuid

    code, trades = _get(base, f"/api/bots/{uuid}/trades")
    assert code == 200 and trades["trades"] == []

    code, events = _get(base, "/api/events")
    assert code == 200 and events["category"] == "important"

    code, ann = _get(base, "/api/announcements")
    assert code == 200 and "announcements" in ann


def test_query_parameters_are_honoured(live_server):
    base, agents, _ = live_server
    _, sorted_asc = _get(base, "/api/bots?sort=name&order=asc")
    assert sorted_asc["sort"] == "name" and sorted_asc["order"] == "asc"
    names = [b["name"] for b in sorted_asc["bots"]]
    assert names == sorted(names)

    _, filtered = _get(base, "/api/bots?profitability=profitable")
    assert filtered["total"] == 0

    _, events = _get(base, "/api/events?category=system&limit=3")
    assert events["category"] == "system" and events["limit"] == 3

    _, scoped = _get(base, f"/api/bots/{agents[0].agent_uuid}/trades?status=open&limit=5")
    assert scoped["limit"] == 5


def test_invalid_parameters_fall_back_instead_of_erroring(live_server):
    base, _, _ = live_server
    # unknown sort key, unknown category, non-numeric limit, malformed date
    _, bots = _get(base, "/api/bots?sort=DROP+TABLE&order=sideways")
    assert bots["sort"] == "total_pnl" and bots["order"] == "desc"
    _, events = _get(base, "/api/events?category=zzz&limit=abc&since=notadate")
    assert events["category"] == "important" and events["limit"] == 40


def test_unknown_routes_and_bots_are_404(live_server):
    base, _, _ = live_server
    assert _status(base, "/api/nope") == 404
    assert _status(base, "/nope") == 404
    assert _status(base, "/api/bots/00000000-0000-0000-0000-000000000000") == 404
    # a uuid that could not be one is rejected before it reaches the database
    assert _status(base, "/api/bots/..%2Fetc%2Fpasswd") == 404


def test_only_read_methods_are_served(live_server):
    """There is no write path: the handler implements GET/HEAD and nothing else."""
    base, _, _ = live_server
    assert not hasattr(srv.DashboardHandler, "do_POST")
    assert not hasattr(srv.DashboardHandler, "do_PUT")
    assert not hasattr(srv.DashboardHandler, "do_DELETE")
    assert not hasattr(srv.DashboardHandler, "do_PATCH")
    req = urllib.request.Request(base + "/api/fleet", method="POST", data=b"{}")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=10)
    assert exc.value.code == 501


def test_backend_failure_returns_a_generic_envelope_not_a_traceback(live_server, monkeypatch):
    base, _, _ = live_server

    def boom(*a, **k):
        raise RuntimeError("SECRET-INTERNAL-DETAIL postgresql://user:pw@host/db")

    monkeypatch.setattr(srv.data, "build_fleet", boom)
    try:
        urllib.request.urlopen(base + "/api/fleet", timeout=10)
        raise AssertionError("expected an error status")
    except urllib.error.HTTPError as exc:
        assert exc.code == 503
        body = exc.read().decode()
    assert "SECRET-INTERNAL-DETAIL" not in body and "postgresql://" not in body
    assert json.loads(body)["error"] == "dashboard temporarily unavailable"
