"""Append-only audit trail for consequential evo actions."""

from __future__ import annotations

import logging
from typing import Any

from .models import EvoAuditEvent

logger = logging.getLogger(__name__)


def audit(
    session,
    kind: str,
    *,
    agent_uuid: str | None = None,
    heartbeat_id: int | None = None,
    severity: str = "info",
    **detail: Any,
) -> EvoAuditEvent:
    row = EvoAuditEvent(
        agent_uuid=agent_uuid,
        heartbeat_id=heartbeat_id,
        kind=kind,
        severity=severity,
        detail_json=detail or None,
    )
    session.add(row)
    session.flush()
    if severity == "integrity":
        logger.warning(
            "evo integrity event", extra={"extra_fields": {"kind": kind, "agent": agent_uuid}}
        )
    return row


def integrity_violation(
    session, agent_uuid: str, kind: str, *, heartbeat_id: int | None = None, **detail: Any
) -> EvoAuditEvent:
    """Record an integrity violation (spec §25): permanent audit row; fitness reads
    these when applying automatic penalties/disqualification."""
    return audit(
        session,
        kind,
        agent_uuid=agent_uuid,
        heartbeat_id=heartbeat_id,
        severity="integrity",
        **detail,
    )
