"""Activity feed for the evolution fleet dashboard.

The v0.1 page merged seven tables, sorted them and cut the result to the latest 20
rows total, so a burst of heartbeats could bury every trade and every evolution
event on the page — and nothing older than those 20 rows was reachable at all.

This module replaces that with a categorised, server-filtered, cursor-paginated
stream:

* Each source table is a **stream** that declares which categories it belongs to
  and whether its rows are *important* (worth showing by default). The category
  requested decides which streams even run, so asking for "trades" never pays for
  a heartbeat query.
* Filtering by bot, generation, date range and free text happens on the server.
* Paging is a composite `(<timestamp>|<stream>|<pk>)` cursor. It is stable across
  streams with equal timestamps and lets "load more" walk back indefinitely
  instead of stopping at a fixed recent-window.
* Heartbeats are condensed. They never enter the default view; the health summary
  reports check-in coverage, and the raw history is reachable under System.

Security: the same rules as data.py. Free text is length-capped, and heartbeat
`status_detail` (which can carry internal tracebacks) is reduced to its status
label and never emitted.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select

from ..evo.models import (
    EvoAgent,
    EvoAnnouncement,
    EvoAuditEvent,
    EvoBirth,
    EvoCohort,
    EvoDataHealthEvent,
    EvoExperiment,
    EvoFill,
    EvoGenome,
    EvoHeartbeat,
    EvoInfluence,
    EvoListener,
    EvoOrder,
    EvoPosition,
    EvoRetirement,
    EvoTicket,
    EvoTransition,
)

CATEGORIES = ("important", "trades", "evolution", "strategy", "announcements", "system", "all")
DEFAULT_CATEGORY = "important"
DEFAULT_LIMIT = 40
MAX_LIMIT = 200

# A finished trade at or beyond this size is called out as a significant move.
SIGNIFICANT_PNL_USD = 5.0


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Stream:
    """One source table's contribution to the feed."""

    key: str
    categories: frozenset[str]
    # (session, ctx) -> list of raw events
    fetch: Callable[..., list[dict]]
    # True when at least some of this stream's rows can be important; streams that
    # can never be important are skipped entirely for the default view.
    can_be_important: bool = False


class _Ctx:
    """Query context shared by every stream in one request."""

    def __init__(self, *, limit, before, agent_uuid, since, until, important_only, cap):
        self.limit = limit
        self.before = before
        self.agent_uuid = agent_uuid
        self.since = since
        self.until = until
        self.important_only = important_only
        self.cap = cap


def _window(query, column, ctx: _Ctx):
    """Apply the shared time window + cursor bound to a stream query."""
    upper = ctx.before or ctx.until
    if ctx.before is not None and ctx.until is not None:
        upper = min(ctx.before, ctx.until)
    if upper is not None:
        query = query.where(column <= upper)
    if ctx.since is not None:
        query = query.where(column >= ctx.since)
    return query.order_by(column.desc()).limit(ctx.limit)


def _event(
    stream: str, pk: int, at: datetime | None, *, category: str, kind: str,
    title: str, description: str, agent_uuid: str | None = None,
    severity: str = "info", important: bool = False, market: str | None = None,
) -> dict | None:
    when = _aware(at)
    if when is None:
        return None
    return {
        "id": f"{stream}:{pk}",
        "stream": stream,
        "pk": pk,
        "_at": when,
        "at": when.isoformat(),
        "category": category,
        "kind": kind,
        "title": title,
        "description": description,
        "agent_uuid": agent_uuid,
        "severity": severity,
        "important": important,
        "market": market,
    }


def _money(value: float) -> str:
    sign = "+" if value >= 0 else "−"
    return f"{sign}${abs(value):,.2f}"


# ---------------------------------------------------------------------------
# Streams
# ---------------------------------------------------------------------------


def _closed_trades(session, ctx: _Ctx) -> list[dict]:
    """Finished positions. Restricted to cohort ledgers: the lifetime ledger holds a
    mirror of the same fills, and counting both would double every trade."""
    query = select(EvoPosition).where(
        EvoPosition.status.in_(("closed", "settled", "liquidated", "transferred")),
        EvoPosition.closed_at.is_not(None),
        EvoPosition.ledger.like("cohort:%"),
    )
    if ctx.agent_uuid:
        query = query.where(EvoPosition.agent_uuid == ctx.agent_uuid)
    out = []
    for pos in session.scalars(_window(query, EvoPosition.closed_at, ctx)):
        pnl = float(pos.realized_pnl_usd or 0)
        big = abs(pnl) >= SIGNIFICANT_PNL_USD
        verb = {
            "settled": "settled", "closed": "closed out",
            "liquidated": "was liquidated in", "transferred": "carried over",
        }.get(pos.status, "closed")
        out.append(_event(
            "trade", pos.id, pos.closed_at, category="trades",
            kind=f"trade_{pos.status}", agent_uuid=pos.agent_uuid,
            market=pos.market_ticker,
            title=("Significant " + ("win" if pnl > 0 else "loss")) if big else "Trade closed",
            description=(
                f"{verb} {pos.quantity:g} {pos.side} in {pos.market_ticker} "
                f"for {_money(pnl)}"
            ),
            severity="good" if pnl > 0 else ("bad" if pnl < 0 else "info"),
            important=True,
        ))
    return [e for e in out if e]


def _orders(session, ctx: _Ctx) -> list[dict]:
    query = select(EvoOrder)
    if ctx.important_only:
        # Only failures are important; a routine order is Trades-tab material.
        query = query.where(EvoOrder.status == "rejected")
    if ctx.agent_uuid:
        query = query.where(EvoOrder.agent_uuid == ctx.agent_uuid)
    out = []
    for o in session.scalars(_window(query, EvoOrder.created_at, ctx)):
        action = "buy" if o.action == "buy" else "sell"
        if o.status == "rejected":
            title, severity, important = "Order rejected", "bad", True
            description = (
                f"had a {action} order for {o.quantity} {o.side} in "
                f"{o.market_ticker} rejected"
            )
        elif o.status == "open":
            # A resting order is NOT a position: zero fills, no position row, no
            # capital deployed. Saying "opened a position" here once made unfilled
            # orders read as completed trades on this page.
            title, severity, important = "Order placed", "info", False
            description = (
                f"placed a {action} order for {o.quantity} {o.side} in "
                f"{o.market_ticker} (resting, unfilled)"
            )
        elif o.status in ("canceled", "expired"):
            title, severity, important = f"Order {o.status}", "warn", False
            description = (
                f"had a {action} order for {o.quantity} {o.side} in "
                f"{o.market_ticker} {o.status}"
            )
        else:
            filled = o.filled_quantity or 0
            qualifier = "" if o.status == "filled" else f" ({filled}/{o.quantity} filled)"
            title, severity, important = "Order filled", "info", False
            description = (
                f"{'bought' if o.action == 'buy' else 'sold'} {o.side} in "
                f"{o.market_ticker}{qualifier}"
            )
        out.append(_event(
            "order", o.id, o.created_at, category="trades", kind=f"order_{o.status}",
            agent_uuid=o.agent_uuid, market=o.market_ticker, title=title,
            description=description, severity=severity, important=important,
        ))
    return [e for e in out if e]


def _fills(session, ctx: _Ctx) -> list[dict]:
    query = select(EvoFill)
    if ctx.agent_uuid:
        query = query.where(EvoFill.agent_uuid == ctx.agent_uuid)
    out = []
    for f in session.scalars(_window(query, EvoFill.created_at, ctx)):
        out.append(_event(
            "fill", f.id, f.created_at, category="trades", kind="fill",
            agent_uuid=f.agent_uuid, market=f.market_ticker, title="Fill",
            description=(
                f"{'bought' if f.action == 'buy' else 'sold'} {f.quantity} {f.side} "
                f"@ {f.price_cents}¢ in {f.market_ticker} ({f.liquidity})"
            ),
        ))
    return [e for e in out if e]


def _genome_revisions(session, ctx: _Ctx) -> list[dict]:
    query = select(EvoGenome).where(
        EvoGenome.kind == "trading", EvoGenome.material.is_(True), EvoGenome.revision > 1
    )
    if ctx.agent_uuid:
        query = query.where(EvoGenome.agent_uuid == ctx.agent_uuid)
    out = []
    for g in session.scalars(_window(query, EvoGenome.created_at, ctx)):
        changed = ", ".join(str(c) for c in (g.changed_fields_json or [])[:4]) or "its strategy"
        out.append(_event(
            "genome", g.id, g.created_at, category="strategy",
            kind="strategy_rollback" if g.is_rollback else "strategy_mutation",
            agent_uuid=g.agent_uuid,
            title="Strategy rolled back" if g.is_rollback else "Strategy mutation",
            description=(
                f"{'rolled back to' if g.is_rollback else 'revised'} trading genome "
                f"revision {g.revision} (changed {changed})"
            ),
            important=True,
        ))
    return [e for e in out if e]


def _audit_strategy(session, ctx: _Ctx) -> list[dict]:
    query = select(EvoAuditEvent).where(EvoAuditEvent.kind == "strategy_activated")
    if ctx.agent_uuid:
        query = query.where(EvoAuditEvent.agent_uuid == ctx.agent_uuid)
    out = []
    for a in session.scalars(_window(query, EvoAuditEvent.created_at, ctx)):
        detail = a.detail_json if isinstance(a.detail_json, dict) else {}
        name = str(detail.get("name") or detail.get("strategy") or "a strategy")[:48]
        out.append(_event(
            "strategy", a.id, a.created_at, category="strategy", kind="strategy_activated",
            agent_uuid=a.agent_uuid, title="Strategy activated",
            description=f"activated the strategy {name}",
            severity="good", important=True,
        ))
    return [e for e in out if e]


def _listeners(session, ctx: _Ctx) -> list[dict]:
    query = select(EvoListener)
    if ctx.agent_uuid:
        query = query.where(EvoListener.owner_uuid == ctx.agent_uuid)
    out = []
    for li in session.scalars(_window(query, EvoListener.created_at, ctx)):
        out.append(_event(
            "listener", li.id, li.created_at, category="strategy", kind="listener_created",
            agent_uuid=li.owner_uuid, title="Listener created",
            description=f"created a market listener ({ctx.cap(li.name, 48)})",
        ))
    return [e for e in out if e]


def _experiments(session, ctx: _Ctx) -> list[dict]:
    query = select(EvoExperiment).where(EvoExperiment.status == "concluded")
    if ctx.agent_uuid:
        query = query.where(EvoExperiment.agent_uuid == ctx.agent_uuid)
    out = []
    for e in session.scalars(_window(query, EvoExperiment.concluded_at, ctx)):
        out.append(_event(
            "experiment", e.id, e.concluded_at, category="strategy",
            kind="experiment_concluded", agent_uuid=e.agent_uuid,
            title="Experiment concluded",
            description=f"concluded a research experiment: {ctx.cap(e.hypothesis, 140)}",
            important=True,
        ))
    return [e for e in out if e]


def _births(session, ctx: _Ctx) -> list[dict]:
    query = select(EvoBirth)
    if ctx.agent_uuid:
        query = query.where(EvoBirth.child_uuid == ctx.agent_uuid)
    out = []
    for b in session.scalars(_window(query, EvoBirth.created_at, ctx)):
        out.append(_event(
            "birth", b.id, b.created_at, category="evolution", kind=f"bot_born_{b.kind}",
            agent_uuid=b.child_uuid, title="New bot created",
            description=f"was created as a {b.kind} bot",
            severity="good", important=True,
        ))
    return [e for e in out if e]


def _retirements(session, ctx: _Ctx) -> list[dict]:
    query = select(EvoRetirement)
    if ctx.agent_uuid:
        query = query.where(EvoRetirement.agent_uuid == ctx.agent_uuid)
    out = []
    for r in session.scalars(_window(query, EvoRetirement.created_at, ctx)):
        rank = f" at rank {r.final_rank}" if r.final_rank is not None else ""
        out.append(_event(
            "retirement", r.id, r.created_at, category="evolution", kind="bot_eliminated",
            agent_uuid=r.agent_uuid, title="Bot eliminated",
            description=f"was retired ({r.reason}){rank}",
            severity="bad", important=True,
        ))
    return [e for e in out if e]


def _transitions(session, ctx: _Ctx) -> list[dict]:
    if ctx.agent_uuid:
        return []  # fleet-level, not attributable to one bot
    out = []
    for t in session.scalars(_window(select(EvoTransition), EvoTransition.created_at, ctx)):
        outcome = t.outcome_json if isinstance(t.outcome_json, dict) else {}
        retired = len(outcome.get("retired") or [])
        children = len(outcome.get("children") or [])
        out.append(_event(
            "transition", t.id, t.created_at, category="evolution", kind="generation_finalized",
            title="Generation finalized",
            description=(
                f"generation {outcome.get('cohort', '?')} closed: {retired} eliminated, "
                f"{children} offspring created"
            ),
            important=True,
        ))
    return [e for e in out if e]


def _cohorts(session, ctx: _Ctx) -> list[dict]:
    if ctx.agent_uuid:
        return []
    out = []
    for c in session.scalars(_window(select(EvoCohort), EvoCohort.created_at, ctx)):
        out.append(_event(
            "cohort", c.id, c.created_at, category="evolution", kind="generation_started",
            title="New generation",
            description=(
                f"generation {c.number} opened"
                + (" (wildcard generation)" if c.wildcard_cohort else "")
            ),
            important=True,
        ))
    return [e for e in out if e]


def _influences(session, ctx: _Ctx) -> list[dict]:
    query = select(EvoInfluence)
    if ctx.agent_uuid:
        query = query.where(EvoInfluence.receiving_uuid == ctx.agent_uuid)
    out = []
    for i in session.scalars(_window(query, EvoInfluence.created_at, ctx)):
        out.append(_event(
            "influence", i.id, i.created_at, category="evolution", kind="peer_influence",
            agent_uuid=i.receiving_uuid, title="Peer influence",
            description=f"adopted an idea from a peer ({ctx.cap(i.concept, 48)})",
        ))
    return [e for e in out if e]


def _tickets(session, ctx: _Ctx) -> list[dict]:
    query = select(EvoTicket)
    if ctx.agent_uuid:
        query = query.where(EvoTicket.requesting_uuid == ctx.agent_uuid)
    out = []
    for t in session.scalars(_window(query, EvoTicket.created_at, ctx)):
        out.append(_event(
            "ticket", t.id, t.created_at, category="system", kind="capability_request",
            agent_uuid=t.requesting_uuid, title="Capability request",
            description=f"requested {ctx.cap(t.capability, 120)}",
        ))
    return [e for e in out if e]


# Heartbeat statuses that mean the bot actually failed rather than merely ran
# thin. `degraded` is common and systemic — it belongs in the health panel, not in
# the important feed, where it would bury every trade all over again.
_HEARTBEAT_FAILURES = ("error", "abandoned")


def _heartbeats(session, ctx: _Ctx) -> list[dict]:
    query = select(EvoHeartbeat)
    if ctx.important_only:
        query = query.where(EvoHeartbeat.status.in_(_HEARTBEAT_FAILURES))
    if ctx.agent_uuid:
        query = query.where(EvoHeartbeat.agent_uuid == ctx.agent_uuid)
    out = []
    for hb in session.scalars(_window(query, EvoHeartbeat.created_at, ctx)):
        failed = hb.status in _HEARTBEAT_FAILURES
        out.append(_event(
            "heartbeat", hb.id, hb.created_at, category="system",
            kind=f"heartbeat_{hb.status}", agent_uuid=hb.agent_uuid,
            title="Bot failure" if failed else f"{hb.kind.title()} heartbeat",
            # status label only — status_detail can carry internal tracebacks.
            description=f"{hb.kind} heartbeat {hb.status}",
            severity="bad" if failed else ("warn" if hb.status == "degraded" else "info"),
            important=failed,
        ))
    return [e for e in out if e]


def _audit_alerts(session, ctx: _Ctx) -> list[dict]:
    query = select(EvoAuditEvent).where(EvoAuditEvent.severity.in_(("warn", "integrity")))
    if ctx.agent_uuid:
        query = query.where(EvoAuditEvent.agent_uuid == ctx.agent_uuid)
    out = []
    for a in session.scalars(_window(query, EvoAuditEvent.created_at, ctx)):
        out.append(_event(
            "audit", a.id, a.created_at, category="system", kind=a.kind,
            agent_uuid=a.agent_uuid,
            title="Integrity alert" if a.severity == "integrity" else "Warning",
            description=a.kind.replace("_", " "),
            severity="bad" if a.severity == "integrity" else "warn",
            important=True,
        ))
    return [e for e in out if e]


def _data_health(session, ctx: _Ctx) -> list[dict]:
    if ctx.agent_uuid:
        return []
    query = select(EvoDataHealthEvent).where(EvoDataHealthEvent.level.in_(("warn", "error")))
    if ctx.important_only:
        query = query.where(EvoDataHealthEvent.level == "error")
    out = []
    for d in session.scalars(_window(query, EvoDataHealthEvent.created_at, ctx)):
        out.append(_event(
            "data_health", d.id, d.created_at, category="system",
            kind=f"data_{d.kind}", title="Data feed issue",
            description=f"{d.source_name}: {d.kind}",
            severity="bad" if d.level == "error" else "warn",
            important=d.level == "error",
        ))
    return [e for e in out if e]


def _announcement_events(session, ctx: _Ctx) -> list[dict]:
    if ctx.agent_uuid:
        return []
    out = []
    for a in session.scalars(_window(select(EvoAnnouncement), EvoAnnouncement.effective_at, ctx)):
        out.append(_event(
            "announcement", a.id, a.effective_at, category="announcements",
            kind=f"announcement_{a.category}", title=ctx.cap(a.title, 160) or "Announcement",
            description=ctx.cap(a.body, 400) or "",
        ))
    return [e for e in out if e]


STREAMS: tuple[Stream, ...] = (
    Stream("trade", frozenset({"trades"}), _closed_trades, can_be_important=True),
    Stream("order", frozenset({"trades"}), _orders, can_be_important=True),
    Stream("fill", frozenset({"trades"}), _fills),
    Stream("genome", frozenset({"strategy"}), _genome_revisions, can_be_important=True),
    Stream("strategy", frozenset({"strategy"}), _audit_strategy, can_be_important=True),
    Stream("listener", frozenset({"strategy"}), _listeners),
    Stream("experiment", frozenset({"strategy"}), _experiments, can_be_important=True),
    Stream("birth", frozenset({"evolution"}), _births, can_be_important=True),
    Stream("retirement", frozenset({"evolution"}), _retirements, can_be_important=True),
    Stream("transition", frozenset({"evolution"}), _transitions, can_be_important=True),
    Stream("cohort", frozenset({"evolution"}), _cohorts, can_be_important=True),
    Stream("influence", frozenset({"evolution"}), _influences),
    Stream("ticket", frozenset({"system"}), _tickets),
    Stream("heartbeat", frozenset({"system"}), _heartbeats, can_be_important=True),
    Stream("audit", frozenset({"system"}), _audit_alerts, can_be_important=True),
    Stream("data_health", frozenset({"system"}), _data_health, can_be_important=True),
    Stream("announcement", frozenset({"announcements"}), _announcement_events),
)


# ---------------------------------------------------------------------------
# Cursor
# ---------------------------------------------------------------------------


def encode_cursor(event: dict) -> str:
    return f"{event['_at'].isoformat()}|{event['stream']}|{event['pk']}"


def decode_cursor(cursor: str | None) -> tuple[datetime, str, int] | None:
    if not cursor:
        return None
    try:
        raw_at, stream, pk = cursor.split("|", 2)
        at = datetime.fromisoformat(raw_at)
        return (_aware(at), stream, int(pk))
    except (ValueError, TypeError):
        return None


def _sort_key(event: dict) -> tuple:
    return (event["_at"], event["stream"], event["pk"])


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def fetch_events(
    session,
    *,
    cap,
    category: str = DEFAULT_CATEGORY,
    agent_uuid: str | None = None,
    generation: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    search: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict:
    """A page of activity. Returns `{"events": [...], "next_cursor": str|None}`.

    `cap` is data.py's text-capping helper — passed in so this module holds no
    opinion about the publishable-text policy.
    """
    category = category if category in CATEGORIES else DEFAULT_CATEGORY
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    important_only = category == "important"

    decoded = decode_cursor(cursor)
    before = decoded[0] if decoded else None

    streams = [
        s for s in STREAMS
        if (category in ("important", "all") or category in s.categories)
        and (not important_only or s.can_be_important)
    ]
    # Per-stream fetch size. It must exceed `limit` by at least 2: one slot for the
    # cursor row itself (which every stream re-fetches at `created_at <= cursor` and
    # then drops), and one more so `len(merged) > limit` can still detect that a
    # further page exists. Fetching at least `limit` from every stream also
    # guarantees the merged top-`limit` is the true global top-`limit`, since no
    # single stream can contribute more than `limit` rows to it.
    # Generation and text filters are applied after the merge, so over-fetch further
    # when they are active. Bounded so a wide filter can't blow up.
    over_fetch = 4 if (generation is not None or search) else 1
    per_stream = min(MAX_LIMIT, (limit + 2) * over_fetch)

    ctx = _Ctx(
        limit=per_stream, before=before, agent_uuid=agent_uuid, since=since,
        until=until, important_only=important_only, cap=cap,
    )
    merged: list[dict] = []
    for stream in streams:
        merged.extend(stream.fetch(session, ctx))

    if important_only:
        merged = [e for e in merged if e["important"]]
    if decoded is not None:
        merged = [e for e in merged if _sort_key(e) < decoded]

    # Attach bot identity, then apply the post-merge filters.
    uuids = {e["agent_uuid"] for e in merged if e["agent_uuid"]}
    identities = _identities(session, uuids)
    for event in merged:
        identity = identities.get(event["agent_uuid"]) if event["agent_uuid"] else None
        event["agent"] = identity["name"] if identity else None
        event["generation"] = identity["generation"] if identity else None

    if generation is not None:
        merged = [e for e in merged if e["generation"] == generation]
    if search:
        needle = search.strip().lower()
        merged = [
            e for e in merged
            if needle in (e["description"] or "").lower()
            or needle in (e["title"] or "").lower()
            or needle in (e["agent"] or "").lower()
            or needle in (e["market"] or "").lower()
        ]

    merged.sort(key=_sort_key, reverse=True)
    page = merged[:limit]
    next_cursor = encode_cursor(page[-1]) if len(merged) > limit and page else None
    for event in page:
        event.pop("_at", None)
    return {
        "events": page,
        "next_cursor": next_cursor,
        "category": category,
        "limit": limit,
    }


def _identities(session, uuids: set[str]) -> dict[str, dict]:
    if not uuids:
        return {}
    return {
        a.agent_uuid: {
            "name": a.display_name.split(" · ")[0],
            "generation": a.generation,
        }
        for a in session.scalars(select(EvoAgent).where(EvoAgent.agent_uuid.in_(list(uuids))))
    }


# ---------------------------------------------------------------------------
# Heartbeat condensation / fleet health
# ---------------------------------------------------------------------------


def fleet_health(session, settings, agent_uuids: list[str], *, now: datetime) -> dict:
    """The compact answer to "is the fleet alive?" — check-in coverage over the last
    interval plus one latest-heartbeat row per bot, instead of a wall of heartbeats.

    The expected interval comes from the worker's own routine cadence
    (`EVO_ROUTINE_HEARTBEATS_PER_DAY`); a bot is stale once it has missed two.
    """
    per_day = max(1, int(getattr(settings, "routine_heartbeats_per_day", 6) or 6))
    interval_minutes = int(24 * 60 / per_day)
    window_minutes = interval_minutes * 2
    cutoff = now - timedelta(minutes=window_minutes)

    latest: dict[str, dict] = {}
    if agent_uuids:
        # Newest beat per bot, picked by TIME rather than by insertion id — a
        # backfilled or out-of-order row must not be mistaken for the latest one.
        newest = [
            ts for _, ts in session.execute(
                select(EvoHeartbeat.agent_uuid, func.max(EvoHeartbeat.created_at))
                .where(EvoHeartbeat.agent_uuid.in_(agent_uuids))
                .group_by(EvoHeartbeat.agent_uuid)
            ) if ts is not None
        ]
        if newest:
            for hb in session.scalars(
                select(EvoHeartbeat).where(
                    EvoHeartbeat.agent_uuid.in_(agent_uuids),
                    EvoHeartbeat.created_at.in_(newest),
                )
            ):
                at = _aware(hb.created_at)
                existing = latest.get(hb.agent_uuid)
                if existing and existing["at"] and at and existing["at"] >= at.isoformat():
                    continue
                latest[hb.agent_uuid] = {
                    "uuid": hb.agent_uuid,
                    "at": at.isoformat() if at else None,
                    "kind": hb.kind,
                    "status": hb.status,
                    "fresh": bool(at and at >= cutoff),
                }

    checked_in = sum(1 for row in latest.values() if row["fresh"])
    stale = [row for row in latest.values() if not row["fresh"]]
    never = [u for u in agent_uuids if u not in latest]

    recent_window = now - timedelta(hours=24)
    by_status = {
        status: int(count)
        for status, count in session.execute(
            select(EvoHeartbeat.status, func.count(EvoHeartbeat.id))
            .where(EvoHeartbeat.created_at >= recent_window)
            .group_by(EvoHeartbeat.status)
        )
    }
    last_at = session.scalar(select(func.max(EvoHeartbeat.created_at)))
    last_aware = _aware(last_at)
    return {
        "checked_in": checked_in,
        "total": len(agent_uuids),
        "never_checked_in": len(never),
        "expected_interval_minutes": interval_minutes,
        "window_minutes": window_minutes,
        "last_heartbeat_at": last_aware.isoformat() if last_aware else None,
        "last_24h": {
            "completed": by_status.get("completed", 0),
            "degraded": by_status.get("degraded", 0),
            "failed": by_status.get("error", 0) + by_status.get("abandoned", 0),
            "running": by_status.get("running", 0),
            "total": sum(by_status.values()),
        },
        "stale_bots": len(stale) + len(never),
        "latest_per_bot": sorted(latest.values(), key=lambda r: r["at"] or "", reverse=True),
    }


def announcement_page(session, *, cap, now: datetime, limit: int = 20, offset: int = 0) -> dict:
    """Announcements as their own paginated resource so they can live in a compact
    panel instead of a stack of full-width cards at the top of the page."""
    limit = max(1, min(int(limit or 20), 100))
    offset = max(0, int(offset or 0))
    active_clause = (
        EvoAnnouncement.active.is_(True),
        EvoAnnouncement.effective_at <= now,
        or_(EvoAnnouncement.expires_at.is_(None), EvoAnnouncement.expires_at > now),
    )
    total = int(session.scalar(
        select(func.count(EvoAnnouncement.id)).where(*active_clause)
    ) or 0)
    rows = list(session.scalars(
        select(EvoAnnouncement)
        .where(*active_clause)
        .order_by(EvoAnnouncement.effective_at.desc(), EvoAnnouncement.id.desc())
        .offset(offset)
        .limit(limit)
    ))
    items = []
    for a in rows:
        effective = _aware(a.effective_at)
        items.append({
            "id": a.id,
            "title": cap(a.title, 200),
            "body": cap(a.body, 600),
            "category": a.category,
            "effective_at": effective.isoformat() if effective else None,
        })
    return {
        "announcements": items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(items) < total,
    }
