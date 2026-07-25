"""Heartbeat orchestrator (spec §9): slot scheduling, idempotent claiming,
execution lifecycle, degraded no-LLM mode, and the triggered-heartbeat pool.

Idempotency: a heartbeat's idem_key is (agent, kind, slot). Execution claims by
INSERTing the row (the unique key makes a double-claim from a concurrent or
restarted worker impossible), then executes, then finalizes. A `running` row
older than heartbeat_stale_minutes is marked `abandoned` — never silently
re-executed with side effects (its journal records what completed).

Slots: 6 routine slots/day (deterministic per-agent jitter so the fleet spreads
across the day and restarts recompute identical schedules), 1 reflection slot,
listener-triggered slots drawn from the weekly pool, plus birth/cohort_end/
retirement slots created by the evolution engine."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import budgets, listeners, memory
from .audit import audit
from .cognition import Cognition, CognitionResult, assemble_context, execute_actions
from .cohorts import active_agents
from .config import EvoSettings
from .marketdata import MarketData
from .models import EvoAgent, EvoCohort, EvoHeartbeat, EvoListener, EvoListenerEvent

logger = logging.getLogger(__name__)

RAW_OUTPUT_CAP = 8000  # chars kept from a failed heartbeat's raw model output


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _jitter_minutes(agent_uuid: str, day: str, slot: int) -> int:
    """Deterministic 0..119 minute jitter per (agent, day, slot)."""
    digest = hashlib.sha256(f"{agent_uuid}:{day}:{slot}".encode()).digest()
    return int.from_bytes(digest[:2], "big") % 120


def routine_slots(agent_uuid: str, day: datetime, per_day: int) -> list[tuple[str, datetime]]:
    """(slot_id, scheduled_for) pairs for one UTC day, spread across it."""
    base = day.replace(hour=0, minute=0, second=0, microsecond=0)
    day_key = base.strftime("%Y%m%d")
    interval = 24 * 60 // max(1, per_day)
    out = []
    for i in range(per_day):
        minute = i * interval + _jitter_minutes(agent_uuid, day_key, i)
        out.append((f"{day_key}:{i}", base + timedelta(minutes=min(minute, 24 * 60 - 1))))
    return out


def reflection_slots(agent_uuid: str, day: datetime, per_day: int) -> list[tuple[str, datetime]]:
    """Tier-2 (deep reflection) slots for one UTC day, spread evenly — per_day=2
    is every ~12h. Slot ids carry the index so raising the cadence adds slots
    rather than colliding with an already-claimed one."""
    base = day.replace(hour=0, minute=0, second=0, microsecond=0)
    day_key = base.strftime("%Y%m%d")
    interval = 24 * 60 // max(1, per_day)
    out = []
    for i in range(max(0, per_day)):
        # offset the jitter seed (90+i) so a reflection never lands on the same
        # deterministic minute as that agent's routine slot i.
        minute = i * interval + _jitter_minutes(agent_uuid, day_key, 90 + i)
        out.append((f"{day_key}:{i}", base + timedelta(minutes=min(minute, 24 * 60 - 1))))
    return out


def strategic_slots(agent_uuid: str, now: datetime, every_hours: float) -> list[tuple[str, datetime]]:
    """Tier-3 (strategic review) slots. Anchored to fixed epoch periods rather than
    to a UTC day, because the cadence (~48h) spans days: period = floor(epoch /
    every_hours), so the schedule stays stable across restarts and never
    double-fires. Returns the current and previous period so a worker that was
    down doesn't silently skip one."""
    if every_hours <= 0:
        return []
    span = every_hours * 3600.0
    period = int(now.timestamp() // span)
    out = []
    for p in (period, period - 1):
        start = datetime.fromtimestamp(p * span, tz=timezone.utc)
        # jitter inside the period so the whole fleet doesn't fire at once
        minute = _jitter_minutes(agent_uuid, f"strat{p}", 0)
        out.append((f"strat:{p}", start + timedelta(minutes=minute)))
    return out


def idem_key(agent_uuid: str, kind: str, slot_id: str) -> str:
    return f"{agent_uuid}:{kind}:{slot_id}"[:128]


def claim_heartbeat(
    session,
    *,
    agent_uuid: str,
    cohort_id: int | None,
    kind: str,
    slot_id: str,
    scheduled_for: datetime | None = None,
    trigger_reason: str | None = None,
    listener_event_id: int | None = None,
) -> EvoHeartbeat | None:
    """Insert-claim; None when the slot was already claimed (unique idem key)."""
    key = idem_key(agent_uuid, kind, slot_id)
    existing = session.scalar(select(EvoHeartbeat).where(EvoHeartbeat.idem_key == key))
    if existing is not None:
        return None
    hb = EvoHeartbeat(
        agent_uuid=agent_uuid,
        cohort_id=cohort_id,
        kind=kind,
        idem_key=key,
        scheduled_for=scheduled_for,
        trigger_reason=trigger_reason,
        listener_event_id=listener_event_id,
        status="running",
    )
    session.add(hb)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return None
    return hb


def sweep_stale(session, settings: EvoSettings) -> int:
    """Mark running heartbeats older than the stale timeout as abandoned."""
    cutoff = _now() - timedelta(minutes=settings.heartbeat_stale_minutes)
    n = 0
    for hb in session.scalars(
        select(EvoHeartbeat).where(EvoHeartbeat.status == "running")
    ):
        created = hb.created_at if hb.created_at.tzinfo else hb.created_at.replace(
            tzinfo=timezone.utc
        )
        if created < cutoff:
            hb.status = "abandoned"
            hb.status_detail = "worker restart or timeout mid-heartbeat"
            hb.finished_at = _now()
            n += 1
    if n:
        session.flush()
    return n


def due_routine_heartbeats(
    session, settings: EvoSettings, agents: list[EvoAgent], *, now: datetime | None = None
) -> list[tuple[EvoAgent, str, str, datetime]]:
    """(agent, kind, slot_id, scheduled_for) for every scheduled slot due now and
    not yet claimed, across all three tiers: routine (tier 1), reflection (tier 2)
    and strategic (tier 3). Looks back 1 day (1 period for strategic) so restarts
    don't lose slots; a recovery pass rather than re-execution decides what to do
    with old misses."""
    now = now or _now()
    out: list[tuple[EvoAgent, str, str, datetime]] = []
    for agent in agents:
        for day_offset in (0, -1):
            day = now + timedelta(days=day_offset)
            for slot_id, at in routine_slots(
                agent.agent_uuid, day, settings.routine_heartbeats_per_day
            ):
                if at <= now and not _claimed(session, agent.agent_uuid, "routine", slot_id):
                    out.append((agent, "routine", slot_id, at))
            for slot_id, at in reflection_slots(
                agent.agent_uuid, day, settings.deep_reflections_per_day
            ):
                if at <= now and not _claimed(session, agent.agent_uuid, "reflection", slot_id):
                    out.append((agent, "reflection", slot_id, at))
        for slot_id, at in strategic_slots(
            agent.agent_uuid, now, settings.strategic_review_hours
        ):
            if at <= now and not _claimed(session, agent.agent_uuid, "strategic", slot_id):
                out.append((agent, "strategic", slot_id, at))
    return out


def _claimed(session, agent_uuid: str, kind: str, slot_id: str) -> bool:
    return session.scalar(
        select(EvoHeartbeat.id).where(
            EvoHeartbeat.idem_key == idem_key(agent_uuid, kind, slot_id)
        )
    ) is not None


def due_triggered_heartbeats(
    session, settings: EvoSettings, agents_by_uuid: dict[str, EvoAgent]
) -> list[tuple[EvoAgent, EvoListenerEvent]]:
    """Unconsumed trigger_heartbeat listener events for agents with pool budget."""
    out = []
    events = list(
        session.scalars(
            select(EvoListenerEvent)
            .where(EvoListenerEvent.consumed.is_(False))
            .order_by(EvoListenerEvent.id)
            .limit(100)
        )
    )
    for event in events:
        payload = event.payload_json or {}
        if payload.get("effect") != "trigger_heartbeat":
            continue
        agent = agents_by_uuid.get(event.owner_uuid)
        if agent is None or agent.status != "active":
            event.consumed = True  # orphaned event
            continue
        out.append((agent, event))
    return out


def run_heartbeat(
    session,
    settings: EvoSettings,
    *,
    agent: EvoAgent,
    cohort: EvoCohort,
    kind: str,
    slot_id: str,
    cognition: Cognition,
    md: MarketData,
    scheduled_for: datetime | None = None,
    trigger_reason: str | None = None,
    listener_event: EvoListenerEvent | None = None,
    extra_context: dict | None = None,
) -> EvoHeartbeat | None:
    """Full lifecycle: claim -> (budget for triggered) -> context -> cognition ->
    actions -> journal -> finalize. Returns the heartbeat row, or None when the
    slot was already claimed."""
    # Self-heal budgets: back-fill any resource added to resource_allocations AFTER
    # this agent joined its cohort (e.g. a newly-shipped action's budget), and top up
    # an allocation the operator has raised. We COMMIT immediately after so these
    # evo_budgets row locks are NOT held across the (slow) LLM call below: the LLM cost
    # is booked in a SEPARATE transaction (llm._complete_*) that UPDATEs the SAME
    # evo_budgets rows, and if the heartbeat's outer transaction still held the locks it
    # would block that UPDATE while itself sitting idle-in-transaction waiting for the
    # LLM call to return — an application-level self-deadlock Postgres cannot break (no
    # lock cycle to detect) and statement_timeout cannot catch, which froze the whole
    # single-threaded population. Budgets are infrastructure that must persist even if
    # this heartbeat later rolls back, so committing them up front is also correct.
    budgets.ensure_budgets(session, agent.agent_uuid, cohort.id, settings)
    session.commit()
    if kind == "triggered":
        if not budgets.can_spend(
            session, agent.agent_uuid, cohort.id, "triggered_heartbeats", 1
        ):
            if listener_event is not None:
                listener_event.consumed = True
                session.flush()
            return None
    hb = claim_heartbeat(
        session,
        agent_uuid=agent.agent_uuid,
        cohort_id=cohort.id,
        kind=kind,
        slot_id=slot_id,
        scheduled_for=scheduled_for,
        trigger_reason=trigger_reason,
        listener_event_id=listener_event.id if listener_event else None,
    )
    if hb is None:
        return None
    if kind == "triggered":
        budgets.spend(session, agent.agent_uuid, cohort.id, "triggered_heartbeats", 1)
    if listener_event is not None:
        listener_event.consumed = True
        listener_event.consumed_by_heartbeat_id = hb.id

    events = [listener_event] if listener_event is not None else listeners.unconsumed_events(
        session, agent.agent_uuid, limit=5
    )
    ctx = assemble_context(
        session, settings, agent=agent, cohort=cohort, heartbeat=hb, md=md,
        listener_events=events, extra=extra_context,
    )
    result: CognitionResult = cognition.decide(session, settings, ctx)

    hb.model_alias = result.model_alias
    hb.model_id = result.model_id
    hb.input_tokens = result.input_tokens
    hb.output_tokens = result.output_tokens
    hb.cost_usd = result.cost_usd

    if result.error and not result.actions:
        # degraded: no usable cognition (budget exhausted, no key, bad output).
        # Deterministic bookkeeping still happened (marks/settlement run in the
        # orchestrator); journal the miss.
        hb.status = "degraded"
        hb.status_detail = result.error[:500]
        if result.raw_text:
            # only ever set on a FAILED heartbeat (parse/validation error) — the
            # bounded evidence an operator needs to diagnose it via the read-only
            # ops SQL channel; never set on success, never dashboard-exposed.
            hb.raw_output_text = result.raw_text[:RAW_OUTPUT_CAP]
        journal_row = memory.write_journal(
            session, agent.agent_uuid, hb.id,
            {"decision": f"degraded heartbeat: {result.error[:200]}",
             "kind": kind, "actions": []},
        )
        hb.journal_memory_id = journal_row.id
        hb.finished_at = _now()
        session.flush()
        return hb

    outcomes = execute_actions(session, settings, ctx, result.actions, md)
    # consume any other events shown to the agent
    for ev in events:
        if ev is not None and not ev.consumed:
            ev.consumed = True
            ev.consumed_by_heartbeat_id = hb.id

    journal_doc = dict(result.journal or {})
    journal_doc.update({"kind": kind, "actions": outcomes})
    journal_row = memory.write_journal(session, agent.agent_uuid, hb.id, journal_doc)
    hb.journal_memory_id = journal_row.id
    hb.actions_json = outcomes
    hb.status = "completed"
    if result.error:
        hb.status_detail = f"partial: {result.error[:300]}"
    hb.finished_at = _now()
    session.flush()
    return hb


def mark_listener_event_usefulness(session, heartbeat: EvoHeartbeat) -> None:
    """After a triggered heartbeat, score the listener: useful if the heartbeat
    produced any executed action beyond no_action."""
    if heartbeat.listener_event_id is None:
        return
    event = session.get(EvoListenerEvent, heartbeat.listener_event_id)
    if event is None:
        return
    listener = session.get(EvoListener, event.listener_id)
    if listener is None:
        return
    acted = any(
        o.get("ok") and o.get("type") not in ("no_action",)
        for o in (heartbeat.actions_json or [])
    )
    if acted:
        listener.useful_count += 1
    else:
        listener.useless_count += 1
    session.flush()


def run_due_heartbeats(
    session,
    settings: EvoSettings,
    *,
    cohort: EvoCohort,
    cognition: Cognition,
    md: MarketData,
    max_per_cycle: int = 10,
    now: datetime | None = None,
    candidate_tickers: list[str] | None = None,
) -> dict[str, int]:
    """One orchestrator pass: sweep stale, run due routine/reflection slots and
    triggered events, bounded per cycle so one pass never monopolizes the loop.

    candidate_tickers (from the orchestrator's bounded universe scan) is threaded
    into each heartbeat's context so agents have real, currently-open tickers to
    reference for submit_trade_intent — without it, an agent with zero open
    positions sees no live market data at all (market_summaries otherwise builds
    only from the agent's own positions), and has no legitimate ticker to use for
    its first trade."""
    counts = {"stale_abandoned": sweep_stale(session, settings), "run": 0, "skipped": 0}
    extra_context = {"candidate_tickers": candidate_tickers} if candidate_tickers else None
    # Honors the max_active_agents ops throttle (only the live subset runs).
    agents = active_agents(session, settings)
    agents_by_uuid = {a.agent_uuid: a for a in agents}
    due = due_routine_heartbeats(session, settings, agents, now=now)
    for agent, kind, slot_id, at in due[:max_per_cycle]:
        hb = run_heartbeat(
            session, settings, agent=agent, cohort=cohort, kind=kind, slot_id=slot_id,
            cognition=cognition, md=md, scheduled_for=at, extra_context=extra_context,
        )
        counts["run" if hb is not None else "skipped"] += 1
    remaining = max_per_cycle - counts["run"]
    if remaining > 0:
        for agent, event in due_triggered_heartbeats(session, settings, agents_by_uuid)[
            :remaining
        ]:
            hb = run_heartbeat(
                session, settings, agent=agent, cohort=cohort, kind="triggered",
                slot_id=f"levent:{event.id}", cognition=cognition, md=md,
                trigger_reason=(event.payload_json or {}).get("listener"),
                listener_event=event, extra_context=extra_context,
            )
            if hb is not None:
                mark_listener_event_usefulness(session, hb)
                counts["run"] += 1
            else:
                counts["skipped"] += 1
    if counts["run"]:
        audit(session, "heartbeat_pass", run=counts["run"], skipped=counts["skipped"])
    return counts
