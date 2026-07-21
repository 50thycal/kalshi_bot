"""Data-source registry (spec §17): exploratory vs operational use, health events,
and the fail-closed staleness contract strategies depend on."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import select

from .audit import audit
from .models import EvoDataHealthEvent, EvoDataSource

# Near-duplicate detection (mirrors tickets.py's semantic dedup): different agents
# independently name the same real feed differently (kalshi_markets_live vs
# kalshi_official_api vs kalshi_markets_realtime are all the same Kalshi feed) —
# exact-name matching alone never catches this. Source names/providers are short
# slugs with no natural-language filler to strip (unlike ticket capability prose),
# so every token counts and the bar is lower than tickets' 0.6.
_DEDUP_JACCARD = 0.4


def _name_tokens(name: str, provider: str | None) -> frozenset[str]:
    raw = re.findall(r"[a-z0-9]+", f"{name} {provider or ''}".lower())
    # light singular normalization catches market/markets-style mismatches that
    # are otherwise invisible to exact token comparison
    return frozenset(t[:-1] if len(t) > 3 and t.endswith("s") else t for t in raw)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_similar_source(session, name: str, provider: str | None) -> EvoDataSource | None:
    tokens = _name_tokens(name, provider)
    best, best_score = None, 0.0
    for src in session.scalars(select(EvoDataSource)):
        score = _jaccard(tokens, _name_tokens(src.name, src.provider))
        if score > best_score:
            best, best_score = src, score
    return best if best_score >= _DEDUP_JACCARD else None

# Sources available out of the box (already collected by the legacy workers into
# provenance-labeled tables). Registered as operational at bootstrap.
BUILTIN_SOURCES = (
    dict(name="kalshi_markets", provider="Kalshi", retrieval_method="rest",
         provenance="kalshi_live", update_frequency="per-cycle", auth_required=True,
         cost_note="free with account", approved_usage="operational"),
    dict(name="weather_live_tables", provider="NWS/Open-Meteo/stations", retrieval_method="db",
         provenance="live_collected", update_frequency="5-60min", auth_required=False,
         cost_note="free", approved_usage="operational"),
    dict(name="backfill_weather_history", provider="Kalshi REST archive", retrieval_method="db",
         provenance="backfill", update_frequency="static+append", auth_required=False,
         cost_note="free", approved_usage="operational"),
    dict(name="crypto_spot_candles", provider="Coinbase Exchange", retrieval_method="db",
         provenance="exchange_feed", update_frequency="1min", auth_required=False,
         cost_note="free", approved_usage="operational"),
    dict(name="polymarket_snapshots", provider="Polymarket Gamma", retrieval_method="db",
         provenance="polymarket_gamma", update_frequency="5min", auth_required=False,
         cost_note="free", approved_usage="operational"),
)


def seed_builtin_sources(session) -> int:
    added = 0
    for src in BUILTIN_SOURCES:
        if session.scalar(
            select(EvoDataSource).where(EvoDataSource.name == src["name"])
        ) is None:
            session.add(EvoDataSource(**src))
            added += 1
    session.flush()
    return added


def register_source(
    session,
    *,
    agent_uuid: str,
    name: str,
    provider: str | None = None,
    retrieval_method: str | None = None,
    endpoint: str | None = None,
    fields: dict | None = None,
    provenance: str | None = None,
    update_frequency: str | None = None,
    expected_latency_seconds: float | None = None,
    cost_note: str | None = "free",
    auth_required: bool = False,
    usage_restrictions: str | None = None,
    rate_limits: str | None = None,
    validation_rules: dict | None = None,
) -> tuple[EvoDataSource | None, str | None]:
    """Agent-facing registration for repeatable operational use of a FREE public
    source (spec §17). Paid/credentialed sources must go through a ticket — this
    API refuses them."""
    name = name.strip().lower().replace(" ", "_")[:64]
    if not name:
        return None, "source name required"
    if auth_required:
        return None, "credentialed sources require a capability ticket, not registration"
    if cost_note and cost_note.strip().lower() not in ("free", "none", "0", "$0"):
        return None, "paid sources require a capability ticket, not registration"
    existing = session.scalar(select(EvoDataSource).where(EvoDataSource.name == name))
    if existing is not None:
        return existing, None
    similar = find_similar_source(session, name, provider)
    if similar is not None:
        return similar, None
    row = EvoDataSource(
        registered_by_uuid=agent_uuid,
        name=name,
        provider=provider,
        retrieval_method=retrieval_method,
        endpoint=endpoint,
        fields_json=fields,
        provenance=provenance or "external_public",
        update_frequency=update_frequency,
        expected_latency_seconds=expected_latency_seconds,
        cost_note=cost_note,
        auth_required=False,
        usage_restrictions=usage_restrictions,
        rate_limits=rate_limits,
        validation_rules_json=validation_rules,
        approved_usage="exploratory",
    )
    session.add(row)
    session.flush()
    audit(session, "data_source_registered", agent_uuid=agent_uuid, source=name)
    return row, None


def record_health_event(
    session, source_name: str, *, level: str, kind: str, detail: dict | None = None
) -> EvoDataHealthEvent:
    row = EvoDataHealthEvent(
        source_name=source_name, level=level, kind=kind, detail_json=detail
    )
    session.add(row)
    src = session.scalar(select(EvoDataSource).where(EvoDataSource.name == source_name))
    if src is not None and level == "error":
        src.status = "degraded"
    session.flush()
    return row


def resolve_health_events(session, source_name: str) -> int:
    n = 0
    for ev in session.scalars(
        select(EvoDataHealthEvent).where(
            EvoDataHealthEvent.source_name == source_name,
            EvoDataHealthEvent.resolved_at.is_(None),
        )
    ):
        ev.resolved_at = datetime.now(timezone.utc)
        n += 1
    src = session.scalar(select(EvoDataSource).where(EvoDataSource.name == source_name))
    if src is not None:
        src.status = "ok"
    session.flush()
    return n


def sources_summary(session) -> list[dict]:
    return [
        {
            "name": s.name,
            "provider": s.provider,
            "provenance": s.provenance,
            "approved_usage": s.approved_usage,
            "status": s.status,
            "registered_by": s.registered_by_uuid,
        }
        for s in session.scalars(select(EvoDataSource).order_by(EvoDataSource.name))
    ]
