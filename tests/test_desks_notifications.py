from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from kalshi_bot.desks.contracts import DeskError, utcnow
from kalshi_bot.desks.notifications import AlertDelivery, AlertNotifier
from kalshi_bot.desks.store import DeskStore


@pytest.fixture
def case(tmp_path):
    store = DeskStore(f"sqlite:///{tmp_path / 'alerts.db'}")
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(204)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    notifier = AlertNotifier(store, "https://alerts.example/hook/secret-token", client=client,
                             resolver=lambda host, port: ["93.184.216.34"])
    return store, notifier, calls


def status(*, paused=False, health=None, cash="30", unknown=False):
    return {"started_at": utcnow().isoformat(), "desks": [{"desk_id": "chatgpt", "paused": paused,
        "cash": cash, "pause_reason": "sensitive-account-ID-password"}], "decisions": [
            {"desk_id": "chatgpt", "status": "unknown"}] if unknown else [], "health": health or {}}


def test_explicit_test_verifies_destination_and_only_sends_safe_payload(case):
    store, notifier, calls = case
    now = utcnow()
    assert not notifier.verified()
    result = notifier.test_delivery(now)
    assert result["state"] == "delivered" and notifier.verified()
    assert calls[0].url.host == "93.184.216.34"
    assert calls[0].headers["host"] == "alerts.example"
    assert calls[0].extensions["sni_hostname"] == "alerts.example"
    assert calls[0].headers["idempotency-key"] == result["event_id"]
    assert "secret-token" not in str(notifier.status())
    # Verification is durable, but a different destination has no proof.
    resumed = AlertNotifier(store, "https://alerts.example/hook/secret-token", client=notifier.client,
                            resolver=lambda h, p: ["93.184.216.34"])
    assert resumed.verified()
    changed = AlertNotifier(store, "https://alerts.example/hook/new", client=notifier.client,
                            resolver=lambda h, p: ["93.184.216.34"])
    assert not changed.verified()


def test_material_alert_dedup_and_recurrent_incident(case):
    store, notifier, calls = case
    now = utcnow()
    assert notifier.observe(status(paused=True), now) == 1
    assert notifier.observe(status(paused=True), now + timedelta(seconds=10)) == 0
    assert not calls  # observe never sends
    notifier.deliver(now)
    assert len(calls) == 1
    assert b"sensitive-account" not in calls[0].content
    notifier.observe(status(), now + timedelta(seconds=20))
    assert notifier.observe(status(paused=True), now + timedelta(seconds=30)) == 1
    notifier.deliver(now + timedelta(seconds=30))
    assert len(calls) == 2
    assert calls[0].headers["idempotency-key"] != calls[1].headers["idempotency-key"]


def test_healthy_passes_and_recoverable_issues_do_not_alert(case):
    store, notifier, calls = case
    now = utcnow()
    healthy = status(health={"chatgpt": {"state": "healthy", "reasons": []}})
    healthy["publications"] = [{"kind": "no_pick", "reason": "no edge"}]
    assert notifier.observe(healthy, now) == 0
    transient = status(health={"chatgpt": {"state": "recovering", "reasons": ["temporary"]}})
    assert notifier.observe(transient, now + timedelta(hours=1)) == 0
    assert notifier.deliver(now) == [] and not calls


def test_persistent_health_threshold_and_reason_sanitizing(case):
    store, notifier, calls = case
    now = utcnow()
    blocked = status(health={"chatgpt": {"state": "needs_operator",
        "reasons": ["something contains secret-account-token"]}})
    assert notifier.observe(blocked, now) == 0
    assert notifier.observe(blocked, now + timedelta(minutes=14)) == 0
    assert notifier.observe(blocked, now + timedelta(minutes=15)) == 1
    notifier.deliver(now + timedelta(minutes=15))
    assert b"persistent_desk_blocker" in calls[0].content
    assert b"secret-account-token" not in calls[0].content


def test_cash_exhausted_and_unknown_are_immediate(case):
    store, notifier, calls = case
    now = utcnow()
    assert notifier.observe(status(cash="0", unknown=True), now) == 2
    notifier.deliver(now)
    assert len(calls) == 2


def test_retries_stable_idempotency_bounded_and_durable(case):
    store, notifier, calls = case
    now = utcnow()
    notifier.test_delivery(now)
    assert notifier.verified()
    bad_calls = []
    def reject(req):
        bad_calls.append(req)
        return httpx.Response(503)
    notifier.client = httpx.Client(transport=httpx.MockTransport(reject))
    notifier.observe(status(paused=True), now)
    assert notifier.deliver(now)[0]["state"] == "retry"
    assert notifier.deliver(now + timedelta(seconds=29)) == []
    assert notifier.deliver(now + timedelta(seconds=30))[0]["state"] == "retry"
    assert notifier.deliver(now + timedelta(seconds=150))[0]["state"] == "failed"
    assert notifier.deliver(now + timedelta(days=1)) == []
    assert len(bad_calls) == 3
    assert len({req.headers["idempotency-key"] for req in bad_calls}) == 1
    assert notifier.status()["failed"] == 1 and not notifier.verified()


def test_crash_lease_recovery_reuses_same_event(case):
    store, notifier, calls = case
    now = utcnow()
    notifier.observe(status(paused=True), now)
    claim = notifier._claim(now)
    assert notifier.deliver(now + timedelta(seconds=30)) == []
    notifier.deliver(now + timedelta(minutes=2))
    assert calls[0].headers["idempotency-key"] == claim["event_id"]
    with store._tx() as session:
        row = session.scalar(select(AlertDelivery))
        assert row.attempts == 2 and row.state == "delivered"


@pytest.mark.parametrize("url", ["http://example.com/hook", "https://u:p@example.com/hook",
                                    "https://example.com/hook#secret", "https://example.com/\n"])
def test_invalid_url_rejected_without_network(tmp_path, url):
    store = DeskStore(f"sqlite:///{tmp_path / 'bad.db'}")
    with pytest.raises(DeskError, match="invalid_alert_destination"):
        AlertNotifier(store, url)


@pytest.mark.parametrize("ips", [["127.0.0.1"], ["169.254.169.254"], ["::1"],
                                    ["93.184.216.34", "10.0.0.1"]])
def test_private_dns_never_reaches_client(case, ips):
    store, notifier, calls = case
    notifier.resolver = lambda h, p: ips
    result = notifier.test_delivery(utcnow())
    assert result["state"] == "retry" and not calls and not notifier.verified()
    assert notifier.status()["last_error"] == "alert_destination_not_public"


def test_redirects_never_followed(case):
    store, notifier, calls = case
    captured = []
    def redirect(req):
        captured.append(req)
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/credentials"})
    notifier.client = httpx.Client(transport=httpx.MockTransport(redirect), follow_redirects=True)
    assert notifier.test_delivery(utcnow())["state"] == "retry"
    assert len(captured) == 1


def test_unconfigured_channel_stays_visible_and_never_delivers(tmp_path):
    store = DeskStore(f"sqlite:///{tmp_path / 'empty.db'}")
    notifier = AlertNotifier(store, "")
    assert not notifier.status()["configured"]
    assert not notifier.verified()
    assert notifier.observe(status(paused=True), utcnow()) == 0
    assert notifier.deliver(utcnow()) == []
    with pytest.raises(DeskError, match="alert_channel_not_configured"):
        notifier.test_delivery(utcnow())


def test_http_library_logs_do_not_expose_webhook_token(case, caplog):
    import logging
    store, notifier, calls = case
    with caplog.at_level(logging.INFO, logger="httpx"):
        notifier.test_delivery(utcnow())
    assert "secret-token" not in caplog.text
    assert "Desk alert webhook request completed" in caplog.text
