"""WS-025 unattended desk agents: cycle budget, wake signal, watchdog, dry run, ntfy alerts."""
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError
from test_desks_integration import (
    SOURCE,
    OfflineExchange,
    OfflineNotifier,
    make_decision,
    verified_source,
)
from test_desks_research import NOW, OUTPUT, runtime, store_at
from test_desks_service import settings

from kalshi_bot.desks import execution
from kalshi_bot.desks.contracts import DeskError
from kalshi_bot.desks.execution import DeskExecutor
from kalshi_bot.desks.notifications import AlertNotifier
from kalshi_bot.desks.service import DeskService
from kalshi_bot.desks.store import DeskStore

D = Decimal
CHICAGO = {"research_timezone": "America/Chicago"}  # NOW = 07:00 CDT


def claim_and_complete(sup, at, output=OUTPUT, desk="chatgpt"):
    job = sup.claim_external(desk, f"{desk}-host", at)
    sup.complete_external(job["job_id"], job["claim_token"], output, "model", at, desk_id=desk)
    return job


def test_new_settings_default_to_todays_behaviour_and_validate():
    plain = settings(research_mode="session")
    assert plain.wake_windows() == {"chatgpt": [], "claude": []}
    assert plain.max_cycles_per_day == 0 and plain.min_cycle_gap_minutes == 0
    assert plain.shadow_desk_set() == frozenset() and plain.alert_format == "json"
    configured = settings(research_mode="session", chatgpt_wake_windows="08:00,12:00,16:00",
                          shadow_desks="chatgpt, claude", alert_format="ntfy")
    assert configured.wake_windows()["chatgpt"] == ["08:00", "12:00", "16:00"]
    assert configured.shadow_desk_set() == {"chatgpt", "claude"}
    with pytest.raises(ValidationError, match="wake windows"):
        settings(claude_wake_windows="8:00")
    with pytest.raises(ValidationError, match="shadow desks"):
        settings(shadow_desks="gemini")


def test_cycle_budget_and_gap_bound_every_claim(tmp_path):
    sup = runtime(store_at(tmp_path), research_mode="session", max_cycles_per_day=2,
                  min_cycle_gap_minutes=60, **CHICAGO)
    claim_and_complete(sup, NOW)
    with pytest.raises(DeskError, match="cycle_gap_not_elapsed"):
        sup.claim_external("chatgpt", "host", NOW + timedelta(minutes=30))
    claim_and_complete(sup, NOW + timedelta(minutes=61))
    with pytest.raises(DeskError, match="cycle_budget_exhausted"):
        sup.claim_external("chatgpt", "host", NOW + timedelta(hours=5))
    # The other desk has its own budget; the next Chicago day restores this one.
    assert sup.claim_external("claude", "host", NOW + timedelta(hours=5))
    assert sup.claim_external("chatgpt", "host", NOW + timedelta(hours=18))


def test_wake_reasons_follow_windows_check_ins_and_continue(tmp_path):
    sup = runtime(store_at(tmp_path), research_mode="session",
                  wake_windows={"chatgpt": ["08:00", "16:00"]}, **CHICAGO)
    first = sup.wake("chatgpt", NOW)  # Yesterday's 16:00 window was never claimed.
    assert first["due"] and first["reason"] == "base_window"
    job = sup.claim_external("chatgpt", "host", NOW)
    assert sup.wake("chatgpt", NOW)["reason"] == "job_active"
    sup.complete_external(job["job_id"], job["claim_token"],
                          {**OUTPUT, "next_check_at": (NOW + timedelta(hours=2)).isoformat()},
                          "model", NOW, desk_id="chatgpt")
    idle = sup.wake("chatgpt", NOW + timedelta(minutes=30))
    assert not idle["due"] and idle["next_wake_at"] == (NOW + timedelta(hours=1)).isoformat()  # 08:00 CDT
    window = sup.wake("chatgpt", NOW + timedelta(minutes=90))
    assert window["due"] and window["reason"] == "base_window"
    claim_and_complete(sup, NOW + timedelta(minutes=90))
    check_in = sup.wake("chatgpt", NOW + timedelta(hours=2, minutes=5))
    assert check_in["due"] and check_in["reason"] == "agent_check_in"
    claim_and_complete(sup, NOW + timedelta(hours=2, minutes=5))
    assert not sup.wake("chatgpt", NOW + timedelta(hours=3))["due"]
    sup.request_cycle("chatgpt", NOW + timedelta(hours=3))  # The dashboard Continue button.
    assert sup.wake("chatgpt", NOW + timedelta(hours=3))["reason"] == "continue_requested"
    assert sup.wake("claude", NOW)["reason"] == "idle"  # No windows: nothing unattended.


def test_own_settlement_wakes_the_desk(tmp_path, monkeypatch):
    store = store_at(tmp_path)
    sup = runtime(store, research_mode="session", wake_windows={"chatgpt": ["08:00"]}, **CHICAGO)
    claim_and_complete(sup, NOW)
    later = NOW + timedelta(minutes=30)
    assert not sup.wake("chatgpt", later)["due"]
    real = store.snapshot
    def with_settlement(now):
        result = real(now)
        result["decisions"] = [{"desk_id": "chatgpt", "settled": True,
                                "settlement": {"settled_at": (NOW + timedelta(minutes=20)).isoformat()}}]
        return result
    monkeypatch.setattr(store, "snapshot", with_settlement)
    woke = sup.wake("chatgpt", later)
    assert woke["due"] and woke["reason"] == "own_settlement"


def test_spent_budget_holds_wakes_and_check_ins_are_clamped(tmp_path):
    sup = runtime(store_at(tmp_path), research_mode="session", max_cycles_per_day=1,
                  wake_windows={"chatgpt": ["08:00"]}, **CHICAGO)
    claim_and_complete(sup, NOW, {**OUTPUT, "next_check_at": (NOW + timedelta(days=9)).isoformat()})
    held = sup.wake("chatgpt", NOW + timedelta(hours=2))  # 08:00 passed, but the budget is spent.
    assert not held["due"] and held["reason"] == "cycle_budget_exhausted"
    assert sup._clamp_check(NOW + timedelta(days=9), NOW) == NOW + timedelta(hours=24)
    assert sup._clamp_check(NOW - timedelta(days=1), NOW) == NOW + timedelta(minutes=10)
    cycle = next(p for p in sup.store.snapshot(NOW)["publications"] if p["kind"] == "research_cycle")
    assert cycle["payload"]["requested_next_check_at"] == (NOW + timedelta(days=9)).isoformat()


def test_watchdog_flags_missed_wakes_only_for_unattended_desks(tmp_path):
    sup = runtime(store_at(tmp_path), research_mode="session",
                  wake_windows={"chatgpt": ["08:00"]}, wake_grace_minutes=90, **CHICAGO)
    early = sup.status(NOW + timedelta(hours=1, minutes=30))["health"]  # 08:30, 30 min after the window
    assert "agent_wake_missed" not in early["chatgpt"]["reasons"]
    late = sup.status(NOW + timedelta(hours=3))["health"]
    assert "agent_wake_missed" in late["chatgpt"]["reasons"]
    assert late["chatgpt"]["wake"]["due"] and late["claude"]["wake"] is None
    assert "agent_wake_missed" not in late["claude"]["reasons"]
    claim_and_complete(sup, NOW + timedelta(hours=3))
    assert "agent_wake_missed" not in sup.status(NOW + timedelta(hours=4))["health"]["chatgpt"]["reasons"]
    stale = sup.status(NOW + timedelta(days=2))["health"]
    assert "no_completed_cycle_24h" in stale["chatgpt"]["reasons"]
    assert "no_completed_cycle_24h" not in stale["claude"]["reasons"]  # App sessions stay idle-OK.


@pytest.fixture
def shadow_case(tmp_path, monkeypatch):
    store = DeskStore(f"sqlite:///{tmp_path / 'shadow.db'}")
    store.initialize("integration-round", NOW)
    exchange = OfflineExchange(1)
    quotes = []
    real_quote = exchange.quote
    def counted(ticker, side):
        quotes.append(ticker)
        return real_quote(ticker, side)
    exchange.quote = counted
    exchange.submit_ioc = lambda *args: pytest.fail("a shadow desk must never send an order")
    monkeypatch.setattr(execution, "utcnow", lambda: NOW)
    executor = DeskExecutor(store, exchange, "chatgpt", live_enabled=True,
                            isolation_verified=True, existing_workers_isolated=True)
    sup = runtime(store, research_mode="session")
    config = settings(database_url=f"sqlite:///{tmp_path / 'shadow.db'}", round_id="integration-round",
                      research_mode="session", shadow_desks="chatgpt")
    service = DeskService(config, store, sup, {"chatgpt": executor}, notifier=OfflineNotifier())
    decision = make_decision(verified_source(SOURCE, NOW), NOW)
    return store, exchange, quotes, executor, service, decision


def test_shadow_desk_records_and_grades_without_ordering(shadow_case, monkeypatch):
    store, exchange, quotes, executor, service, decision = shadow_case
    service._isolation["chatgpt"] = {"at": NOW, "verified": True}
    service.last_tick = NOW
    monkeypatch.setattr(service.supervisor, "verify_decision_sources", lambda decision: None)
    monkeypatch.setattr(service.supervisor, "verify_session_decision", lambda decision, now: None)
    from kalshi_bot.desks import service as desk_service
    monkeypatch.setattr(desk_service, "utcnow", lambda: NOW)
    recorded = service.submit(decision, NOW)
    assert recorded["status"] == "would_submit" and recorded["mode"] == "shadow"
    assert recorded["quantity"] == 2 and D(recorded["ask"]) == D(".40")
    assert service.submit(decision, NOW) == recorded and len(quotes) == 1  # Replay never re-quotes.
    assert not exchange.orders and not store.snapshot(NOW)["decisions"]
    book = store.snapshot(NOW)["desks"][0]
    assert D(book["committed"]) == 0 and book["attempts_today"] == 0
    service.settle_shadow("chatgpt", executor, NOW)
    assert not store.publications("chatgpt", "shadow_settlement")  # Not settled yet.
    exchange.outcome = D(1)
    service.settle_shadow("chatgpt", executor, NOW + timedelta(minutes=1))
    assert not store.publications("chatgpt", "shadow_settlement")  # Throttled: read 1 min ago.
    service.settle_shadow("chatgpt", executor, NOW + timedelta(minutes=6))
    service.settle_shadow("chatgpt", executor, NOW + timedelta(minutes=12))
    graded = store.publications("chatgpt", "shadow_settlement")
    assert len(graded) == 1
    assert graded[0]["payload"]["fee"] == "0.04" and D(graded[0]["payload"]["pnl"]) == D("1.16")
    assert D(graded[0]["payload"]["brier_score"]) == D(".04")


def test_shadow_refusals_are_recorded_and_paused_desk_never_quotes(shadow_case):
    store, exchange, quotes, executor, service, decision = shadow_case
    capped = decision.model_copy(update={"decision_id": "capped-decision", "max_price": D(".39")})
    refused = service.shadow(capped, executor, NOW)
    assert refused["status"] == "would_refuse" and refused["reason"] == "price_cap_exceeded"
    store.pause("chatgpt", "operator hold")
    paused = service.shadow(decision, executor, NOW)
    assert paused["status"] == "would_refuse" and paused["reason"] == "desk_paused"
    assert quotes == ["OBSERVATION-MARKET"]  # Only the capped decision was quoted.
    exchange.outcome = D(0)
    service.settle_shadow("chatgpt", executor, NOW)
    assert not store.publications("chatgpt", "shadow_settlement")  # Refusals are not graded as fills.


def test_ntfy_format_sends_readable_text_with_headers(tmp_path):
    store = DeskStore(f"sqlite:///{tmp_path / 'alerts.db'}")
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200)
    notifier = AlertNotifier(store, "https://ntfy.example/private-topic",
                             client=httpx.Client(transport=httpx.MockTransport(handler)),
                             resolver=lambda host, port: ["93.184.216.34"], fmt="ntfy")
    assert notifier.test_delivery(NOW)["verified"]
    assert calls[0].content == b"Desk alert channel test."
    status = {"started_at": NOW.isoformat(), "desks": [{"desk_id": "claude", "paused": True, "cash": "30"}],
              "decisions": [], "health": {}}
    notifier.observe(status, NOW)
    notifier.deliver(NOW)
    alert = calls[-1]
    assert alert.content == b"Claude desk: trading paused. Open the dashboard for details."
    assert alert.headers["title"] == "Claude desk needs attention"
    assert alert.headers["priority"] == "high" and alert.headers["idempotency-key"]
    assert b"{" not in alert.content
    with pytest.raises(DeskError, match="invalid_alert_format"):
        AlertNotifier(store, "https://ntfy.example/topic", fmt="xml")


def test_dashboard_budget_overrides_configuration_and_zero_stops_research(tmp_path):
    sup = runtime(store_at(tmp_path), research_mode="session", max_cycles_per_day=6, **CHICAGO)
    assert sup.cycle_cap("chatgpt") == 6 and sup.controls("chatgpt")["max_cycles_per_day"] is None
    sup.set_control("chatgpt", NOW, max_cycles_per_day=1)
    assert sup.cycle_cap("chatgpt") == 1 and sup.cycle_cap("claude") == 6
    claim_and_complete(sup, NOW)
    with pytest.raises(DeskError, match="cycle_budget_exhausted"):
        sup.claim_external("chatgpt", "host", NOW + timedelta(hours=2))
    sup.set_control("claude", NOW, max_cycles_per_day=0)
    with pytest.raises(DeskError, match="cycle_budget_exhausted"):
        sup.claim_external("claude", "host", NOW)
    for bad in (-1, 25, "3", True, None):
        with pytest.raises(DeskError, match="invalid_research_budget"):
            sup.set_control("chatgpt", NOW, max_cycles_per_day=bad)
    with pytest.raises(DeskError, match="invalid_desk"):
        sup.set_control("gemini", NOW, max_cycles_per_day=1)


def test_dashboard_mode_switch_overrides_configuration_and_is_published(shadow_case):
    store, exchange, quotes, executor, service, decision = shadow_case
    assert service.is_shadow("chatgpt") and not service.is_shadow("claude")  # configuration
    with pytest.raises(DeskError, match="mode_change_requires_confirmation"):
        service.set_mode("chatgpt", "live", None, NOW)
    with pytest.raises(DeskError, match="invalid_desk_mode"):
        service.set_mode("chatgpt", "paper", "chatgpt", NOW)
    state = service.set_mode("chatgpt", "live", "chatgpt", NOW)
    assert state["mode"] == "live" and state["mode_source"] == "dashboard"
    assert not service.is_shadow("chatgpt")
    service.set_mode("claude", "dry_run", "claude", NOW)
    assert service.is_shadow("claude")
    budget = service.set_budget("chatgpt", 3, NOW)
    assert budget["max_cycles_per_day"] == 3 and budget["budget_source"] == "dashboard"
    logged = [p["payload"] for p in store.publications("chatgpt", "operator_control")]
    assert [(p["control"], p["from"], p["to"]) for p in logged] == [
        ("mode", "dry_run", "live"), ("research_budget", None, 3)]


def test_automation_status_lists_dry_run_picks_and_graded_pnl(shadow_case):
    store, exchange, quotes, executor, service, decision = shadow_case
    service.shadow(decision, executor, NOW)
    exchange.outcome = D(1)
    service.settle_shadow("chatgpt", executor, NOW)
    view = service.automation("chatgpt", NOW)
    assert view["mode"] == "dry_run" and view["mode_source"] == "configuration"
    assert view["shadow_picks"][0]["ticker"] == "OBSERVATION-MARKET"
    assert view["shadow_picks"][0]["settled"] and D(view["shadow_pnl"]) == D("1.16")
    assert view["wake"]["desk_id"] == "chatgpt" and not view["unattended"]


def test_dashboard_controls_are_operator_only_over_http(shadow_case):
    import http.client
    import json as _json
    import threading

    from test_desks_service import TOKENS

    from kalshi_bot.desks.server import make_server
    store, exchange, quotes, executor, service, decision = shadow_case
    server = make_server(service, ("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    def post(path, body, role):
        conn = http.client.HTTPConnection(*server.server_address, timeout=3)
        conn.request("POST", path, body=_json.dumps(body), headers={
            "Authorization": "Bearer " + TOKENS[role], "Content-Type": "application/json"})
        response = conn.getresponse()
        value = _json.loads(response.read())
        conn.close()
        return response.status, value
    try:
        assert post("/api/desks/chatgpt/mode", {"mode": "live", "confirm": "chatgpt"}, "chatgpt")[0] == 403
        assert post("/api/desks/chatgpt/budget", {"max_cycles_per_day": 9}, "chatgpt")[0] == 403
        status, value = post("/api/desks/chatgpt/budget", {"max_cycles_per_day": 2}, "operator")
        assert status == 200 and value["max_cycles_per_day"] == 2
        status, value = post("/api/desks/chatgpt/mode", {"mode": "live"}, "operator")
        assert status == 409 and service.is_shadow("chatgpt")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
