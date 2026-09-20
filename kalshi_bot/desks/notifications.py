"""Durable material-alert outbox for the two desks.

No messages are sent by observe(). An operator configures an HTTPS webhook and
explicitly tests it before launch. Delivery is at least once with a stable
Idempotency-Key; recipients should use that key to suppress duplicate delivery.
Only generic reason codes leave the service, never research or account details.
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import socket
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import JSON, Boolean, DateTime, Integer, String, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Mapped, mapped_column

from .contracts import DeskError
from .models import DeskBase


class AlertChannel(DeskBase):
    __tablename__ = "desk_alert_channels"
    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlertCondition(DeskBase):
    __tablename__ = "desk_alert_conditions"
    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel: Mapped[str] = mapped_column(String(64), index=True)
    desk_id: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(String(80))
    active: Mapped[bool] = mapped_column(Boolean)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observations: Mapped[int] = mapped_column(Integer)
    episode: Mapped[int] = mapped_column(Integer)
    event_id: Mapped[str | None] = mapped_column(String(64))


class AlertDelivery(DeskBase):
    __tablename__ = "desk_alert_deliveries"
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(20), index=True)
    attempts: Mapped[int] = mapped_column(Integer)
    next_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(64))


REASONS = {
    "trading_paused", "unknown_order_status", "capital_exhausted",
    "settlement_learning_backlog", "unattended_runner_missing",
    "paid_research_budget_not_authorized", "research_budget_exhausted",
    "repeated_research_failures", "provider_bill_requires_reconciliation",
    "no_completed_cycle_24h", "persistent_desk_blocker",
}
IMMEDIATE = {"trading_paused", "unknown_order_status", "capital_exhausted"}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _resolve(host: str, port: int) -> list[str]:
    return list({row[4][0] for row in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})


class _WebhookLogFilter(logging.Filter):
    """httpx INFO logs include request URLs, which may embed webhook tokens."""

    def __init__(self, target):
        super().__init__()
        self.target = str(httpx.URL(target))

    def filter(self, record):
        args = record.args if isinstance(record.args, tuple) else ()
        if any(str(arg) == self.target for arg in args):
            record.msg, record.args = "Desk alert webhook request completed", ()
        return True


class AlertNotifier:
    def __init__(self, store, url: str, *, client: httpx.Client | None = None, resolver=None):
        self.store = store
        self._url = url.get_secret_value() if hasattr(url, "get_secret_value") else str(url)
        self.configured = bool(self._url)
        self.fingerprint = _hash(self._url)
        self._parts = None
        if self.configured:
            try:
                parts = urlsplit(self._url)
                if (parts.scheme != "https" or not parts.hostname or parts.username is not None
                        or parts.password is not None or parts.fragment or "\\" in self._url
                        or any(ord(c) < 33 for c in self._url)):
                    raise ValueError
                port = parts.port or 443
                if not 1 <= port <= 65535:
                    raise ValueError
                self._parts = parts
            except ValueError as exc:
                raise DeskError("invalid_alert_destination") from exc
        self.resolver = resolver or _resolve
        self.client = client or httpx.Client(timeout=5, follow_redirects=False, trust_env=False)
        self._owns_client = client is None
        DeskBase.metadata.create_all(store.engine, tables=[
            AlertChannel.__table__, AlertCondition.__table__, AlertDelivery.__table__])
        with self.store._tx() as session:
            self._channel(session)

    def close(self):
        if self._owns_client:
            self.client.close()

    def _channel(self, session):
        # One row serializes observations and claims, including first insert races.
        dialect = self.store.engine.dialect.name
        insert = sqlite_insert if dialect == "sqlite" else pg_insert if dialect == "postgresql" else None
        if insert is None:
            raise DeskError("unsupported_alert_database")
        statement = insert(AlertChannel).values(fingerprint=self.fingerprint).on_conflict_do_nothing()
        session.execute(statement)
        return session.scalar(select(AlertChannel).where(
            AlertChannel.fingerprint == self.fingerprint).with_for_update())

    def _enqueue(self, session, event_id: str, kind: str, desk: str, reason: str, now: datetime):
        payload = {"schema_version": 1, "event_id": event_id, "kind": kind,
                   "desk": desk, "reason": reason, "created_at": now.isoformat(),
                   "message": "Desk alert channel test." if kind == "test" else
                   "A trading desk needs attention. Open its authenticated dashboard for details."}
        session.add(AlertDelivery(event_id=event_id, channel=self.fingerprint, kind=kind,
            payload=payload, state="pending", attempts=0, next_at=now, created_at=now))

    def observe(self, status: dict, now: datetime) -> int:
        if not self.configured:
            return 0
        now = _utc(now)
        current: set[tuple[str, str]] = set()
        for desk in status.get("desks", []):
            identity = desk.get("desk_id")
            if identity not in ("chatgpt", "claude"):
                continue
            if desk.get("paused"):
                current.add((identity, "trading_paused"))
            try:
                if Decimal(str(desk.get("cash", "1"))) <= 0:
                    current.add((identity, "capital_exhausted"))
            except InvalidOperation:
                current.add((identity, "persistent_desk_blocker"))
        for decision in status.get("decisions", []):
            if decision.get("desk_id") in ("chatgpt", "claude") and decision.get("status") == "unknown":
                current.add((decision["desk_id"], "unknown_order_status"))
        health = status.get("health", status.get("research", {}).get("health", {}))
        # Setup blockers remain dashboard readiness information, not outage spam.
        if status.get("started_at") and isinstance(health, dict):
            for desk in ("chatgpt", "claude"):
                entry = health.get(desk, {})
                if entry.get("state", entry.get("status")) != "needs_operator":
                    continue
                for raw in entry.get("reasons", ["persistent_desk_blocker"]):
                    reason = str(raw).split(":", 1)[0]
                    if reason == "capital_exhausted":
                        # Actual cash above decides exhaustion; committed cash is not loss.
                        continue
                    current.add((desk, reason if reason in REASONS else "persistent_desk_blocker"))
        enqueued = 0
        with self.store._tx() as session:
            self._channel(session)
            conditions = {(row.desk_id, row.reason): row for row in session.scalars(
                select(AlertCondition).where(AlertCondition.channel == self.fingerprint))}
            for key, row in conditions.items():
                if key not in current:
                    row.active = False
            for desk, reason in sorted(current):
                row = conditions.get((desk, reason))
                if row is None:
                    row = AlertCondition(fingerprint=_hash(f"{self.fingerprint}:{desk}:{reason}"),
                        channel=self.fingerprint, desk_id=desk, reason=reason, active=True,
                        first_seen=now, last_seen=now, observations=1, episode=1)
                    session.add(row)
                elif not row.active:
                    row.active, row.first_seen, row.last_seen = True, now, now
                    row.observations, row.episode, row.event_id = 1, row.episode + 1, None
                elif now > _utc(row.last_seen):
                    row.observations += 1
                    row.last_seen = now
                persistent = row.observations >= 2 and now - _utc(row.first_seen) >= timedelta(minutes=15)
                if not row.event_id and (reason in IMMEDIATE or persistent):
                    row.event_id = _hash(f"{row.fingerprint}:{row.episode}")
                    self._enqueue(session, row.event_id, "material", desk, reason, now)
                    enqueued += 1
        return enqueued

    def _destination(self):
        parts = self._parts
        if parts is None:
            raise DeskError("alert_channel_not_configured")
        host = parts.hostname.encode("idna").decode("ascii")
        port = parts.port or 443
        try:
            addresses = self.resolver(host, port)
            if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
                raise ValueError
            chosen = ipaddress.ip_address(addresses[0])
        except (ValueError, OSError, TypeError) as exc:
            raise DeskError("alert_destination_not_public") from exc
        # Pin the validated IP. TLS SNI/certificate validation and Host use the
        # configured hostname, preventing a second DNS lookup/rebinding window.
        ip_host = f"[{chosen}]" if chosen.version == 6 else str(chosen)
        netloc = ip_host if port == 443 else f"{ip_host}:{port}"
        target = urlunsplit(("https", netloc, parts.path or "/", parts.query, ""))
        header_host = host if port == 443 else f"{host}:{port}"
        return target, header_host, host

    def _claim(self, now):
        with self.store._tx() as session:
            self._channel(session)
            rows = list(session.scalars(select(AlertDelivery).where(
                AlertDelivery.channel == self.fingerprint,
                AlertDelivery.state.in_(("pending", "retry", "sending")),
                AlertDelivery.next_at <= now).order_by(AlertDelivery.created_at)))
            for row in rows:
                if row.state == "sending" and row.lease_until and _utc(row.lease_until) > now:
                    continue
                if row.attempts >= 3:
                    row.state, row.error = "failed", "delivery_attempts_exhausted"
                    self._channel(session).failed_at = now
                    continue
                row.state, row.attempts = "sending", row.attempts + 1
                row.claim, row.lease_until = str(uuid.uuid4()), now + timedelta(minutes=2)
                return {"event_id": row.event_id, "claim": row.claim, "payload": row.payload}
        return None

    def deliver(self, now: datetime) -> list[dict]:
        if not self.configured:
            return []
        now = _utc(now)
        results = []
        for _ in range(5):
            claimed = self._claim(now)
            if claimed is None:
                break
            error = None
            try:
                target, host, sni = self._destination()
                logger = logging.getLogger("httpx")
                redact = _WebhookLogFilter(target)
                logger.addFilter(redact)
                try:
                    response = self.client.post(target, json=claimed["payload"],
                        headers={"Host": host, "Idempotency-Key": claimed["event_id"]},
                        extensions={"sni_hostname": sni}, follow_redirects=False, timeout=5)
                finally:
                    logger.removeFilter(redact)
                if not 200 <= response.status_code < 300:
                    error = "webhook_rejected"
            except DeskError as exc:
                error = exc.code
            except Exception:
                # Never persist exception strings: request URLs may contain secrets.
                error = "webhook_unavailable"
            with self.store._tx() as session:
                channel = self._channel(session)
                row = session.get(AlertDelivery, claimed["event_id"])
                if row.claim != claimed["claim"]:
                    continue
                row.claim, row.lease_until, row.error = None, None, error
                if error is None:
                    row.state, row.delivered_at = "delivered", now
                    if row.kind == "test":
                        channel.verified_at, channel.failed_at = now, None
                elif row.attempts >= 3:
                    row.state, channel.failed_at = "failed", now
                else:
                    row.state = "retry"
                    row.next_at = now + timedelta(seconds=30 if row.attempts == 1 else 120)
                results.append({"event_id": row.event_id, "state": row.state, "error": row.error})
        return results

    def test_delivery(self, now: datetime) -> dict:
        if not self.configured:
            raise DeskError("alert_channel_not_configured")
        now = _utc(now)
        event_id = str(uuid.uuid4())
        with self.store._tx() as session:
            self._channel(session)
            self._enqueue(session, event_id, "test", "system", "channel_test", now)
        self.deliver(now)
        with self.store._tx() as session:
            row = session.get(AlertDelivery, event_id)
            state = row.state
        return {"event_id": event_id, "state": state, "verified": self.verified()}

    def verified(self) -> bool:
        if not self.configured:
            return False
        with self.store._tx() as session:
            channel = session.get(AlertChannel, self.fingerprint)
            return bool(channel and channel.verified_at and not channel.failed_at)

    def status(self, now: datetime | None = None) -> dict:
        with self.store._tx() as session:
            channel = session.get(AlertChannel, self.fingerprint)
            rows = list(session.scalars(select(AlertDelivery).where(AlertDelivery.channel == self.fingerprint)))
            counts = {state: sum(row.state == state for row in rows)
                      for state in ("pending", "retry", "sending", "delivered", "failed")}
            return {"configured": self.configured,
                "verified": bool(self.configured and channel and channel.verified_at and not channel.failed_at),
                "counts": counts, "pending": counts["pending"] + counts["retry"] + counts["sending"],
                "failed": counts["failed"],
                "last_error": next((row.error for row in reversed(rows) if row.error), None)}
