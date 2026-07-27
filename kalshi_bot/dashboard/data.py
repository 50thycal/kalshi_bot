"""Read-only data layer: turn the evo_* tables into the dashboard JSON payload.

Security (the page is served at a public Railway URL with no auth): this module
returns ONLY the structured, non-sensitive fields the dashboard needs. It never
returns API keys, environment variables, credentials, raw prompts, hidden
reasoning, database connection info, or internal stack traces. Agent-authored
free text (thesis, journal decision, ticket text) is already structured/bounded
and is additionally length-capped here. Heartbeat error detail (which can contain
internal tracebacks) is deliberately reduced to a generic status label.

Everything here is a SELECT; no row is ever added, updated or committed. Existing
evo services are reused where practical (paper ledger math, budgets, tickets).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from ..evo import announcements as announce
from ..evo import budgets, paper, tickets
from ..evo.cohorts import active_agents, current_cohort
from ..evo.config import EvoSettings
from ..evo.fitness import group_by_fractions
from ..evo.models import (
    EvoAgent,
    EvoConfigVersion,
    EvoDataSource,
    EvoExperiment,
    EvoFitness,
    EvoGenome,
    EvoHeartbeat,
    EvoInfluence,
    EvoListener,
    EvoMemory,
    EvoOrder,
    EvoPosition,
    EvoStrategy,
    EvoTicket,
)

TEXT_CAP = 400


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _cap(text: str | None, cap: int = TEXT_CAP) -> str | None:
    if not text:
        return None
    text = str(text)
    return text if len(text) <= cap else text[: cap - 1] + "…"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _effective_cap(session) -> int:
    """The evo worker's configured live-agent throttle (EVO_MAX_ACTIVE_AGENTS),
    read from its latest config snapshot — the worker is the source of truth, so
    the dashboard reflects the real cap without a duplicate env var here. 0 = none."""
    row = session.scalar(
        select(EvoConfigVersion).order_by(EvoConfigVersion.id.desc()).limit(1)
    )
    cfg = row.population_config_json if row else None
    if isinstance(cfg, dict):
        try:
            return max(0, int(cfg.get("max_active_agents", 0) or 0))
        except (TypeError, ValueError):
            return 0
    return 0


# ---------------------------------------------------------------------------
# Top-level builder
# ---------------------------------------------------------------------------


def build_dashboard_data(session, settings: EvoSettings, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    cohort = current_cohort(session)
    if cohort is None:
        return {
            "generated_at": _iso(now),
            "status": "between_cohorts",
            "cohort": None,
            "cards": _empty_cards(settings),
            "agents": [],
            "throttle": {"live": 0, "total": 0, "throttled": False},
            "activity": [],
            "components": _components(session, settings, cohort=None, now=now),
            "requests": _requests(session),
            "announcements": _announcements(session, now),
        }

    all_active = active_agents(session)
    # Show only the agents that actually run live work. When the evo worker is
    # throttled (EVO_MAX_ACTIVE_AGENTS), the lowest-id N run and the rest are
    # dormant; hide the dormant ones. The cap is read from the worker's own config
    # snapshot (single source of truth), not this service's env, so the two can't
    # drift.
    cap = _effective_cap(session)
    agents = all_active[:cap] if cap > 0 else all_active
    ledger = paper.cohort_ledger(cohort.id)
    fitness_by_uuid = _latest_interim_fitness(session, cohort.id)
    groups = _projected_groups(fitness_by_uuid, settings)

    agent_rows = [
        _agent_row(session, settings, cohort, a, ledger, fitness_by_uuid.get(a.agent_uuid),
                   groups.get(a.agent_uuid, "unranked"), now)
        for a in agents
    ]
    # rank ordering: ranked agents first (by rank), then unranked
    agent_rows.sort(key=lambda r: (r["rank"] is None, r["rank"] or 0, -r["equity"]))

    return {
        "generated_at": _iso(now),
        "status": _system_status(session, cohort, now),
        "cohort": _cohort_header(cohort, now),
        "cards": _cards(session, settings, cohort, agents, ledger),
        "agents": agent_rows,
        "throttle": {
            "live": len(agents),
            "total": len(all_active),
            "throttled": cap > 0 and len(agents) < len(all_active),
        },
        "activity": _recent_activity(session, now),
        "components": _components(session, settings, cohort=cohort, now=now),
        "requests": _requests(session),
        "announcements": _announcements(session, now),
    }


# ---------------------------------------------------------------------------
# Cohort header + status
# ---------------------------------------------------------------------------


def _cohort_header(cohort, now: datetime) -> dict:
    ends = _aware(cohort.ends_at)
    starts = _aware(cohort.starts_at)
    remaining = max(0.0, (ends - now).total_seconds())
    return {
        "number": cohort.number,
        "starts_at": _iso(starts),
        "ends_at": _iso(ends),
        "seconds_remaining": int(remaining),
        "wildcard_next": bool(cohort.wildcard_cohort),
    }


def _last_heartbeat_at(session, *, cohort_id: int | None = None) -> datetime | None:
    q = select(func.max(EvoHeartbeat.created_at))
    if cohort_id is not None:
        q = q.where(EvoHeartbeat.cohort_id == cohort_id)
    return session.scalar(q)


def _system_status(session, cohort, now: datetime) -> str:
    if cohort.status in ("finalizing", "finalized"):
        return "between_cohorts"
    last_hb = _last_heartbeat_at(session)
    if last_hb is None:
        return "starting"
    minutes = (now - _aware(last_hb)).total_seconds() / 60.0
    # heartbeats are bounded by the cycle/slot cadence; a long gap means the
    # worker isn't running (paused/error) rather than merely idle.
    if minutes > 90:
        return "paused"
    return "running"


# ---------------------------------------------------------------------------
# Summary cards
# ---------------------------------------------------------------------------


def _empty_cards(settings: EvoSettings) -> dict:
    return {
        "active_agents": 0,
        "starting_capital_usd": 0.0,
        "per_agent_capital_usd": settings.starting_capital_usd,
        "cohort_equity_usd": 0.0,
        "cohort_profit_usd": 0.0,
        "completed_trades": 0,
        "llm_cost_usd": 0.0,
        "llm_budget_usd": 0.0,
    }


def _cards(session, settings: EvoSettings, cohort, agents: list, ledger: str) -> dict:
    n = len(agents)
    starting = n * settings.starting_capital_usd
    equity = sum(paper.nav(session, a.agent_uuid, ledger) for a in agents)
    completed = session.scalar(
        select(func.count(EvoPosition.id)).where(
            EvoPosition.ledger == ledger,
            EvoPosition.status.in_(("settled", "closed", "liquidated", "transferred")),
        )
    ) or 0
    from ..evo.models import EvoLlmUsage

    llm_cost = session.scalar(
        select(func.coalesce(func.sum(EvoLlmUsage.cost_usd), 0)).where(
            EvoLlmUsage.cohort_id == cohort.id
        )
    ) or 0
    return {
        "active_agents": n,
        "starting_capital_usd": round(starting, 2),
        "per_agent_capital_usd": settings.starting_capital_usd,
        "cohort_equity_usd": round(equity, 2),
        "cohort_profit_usd": round(equity - starting, 2),
        "completed_trades": int(completed),
        "llm_cost_usd": round(float(llm_cost), 2),
        "llm_budget_usd": round(n * settings.weekly_llm_ceiling_usd, 2),
    }


# ---------------------------------------------------------------------------
# Fitness / grouping
# ---------------------------------------------------------------------------


def _latest_interim_fitness(session, cohort_id: int) -> dict[str, EvoFitness]:
    """Newest interim fitness row per agent (operator view — not the 6h-delayed
    peer copy, since this is the human's private dashboard)."""
    rows = list(
        session.scalars(
            select(EvoFitness)
            .where(EvoFitness.cohort_id == cohort_id, EvoFitness.kind == "interim")
            .order_by(EvoFitness.computed_at.desc())
        )
    )
    out: dict[str, EvoFitness] = {}
    for r in rows:
        out.setdefault(r.agent_uuid, r)
    return out


def _projected_groups(fitness_by_uuid: dict[str, EvoFitness], settings: EvoSettings) -> dict[str, str]:
    ranked = sorted(
        fitness_by_uuid.values(), key=lambda f: (-(f.selection_score or 0), f.agent_uuid)
    )
    if not ranked:
        return {}
    groups = group_by_fractions(ranked, settings)
    out: dict[str, str] = {}
    for name in ("top", "middle", "bottom"):
        for f in groups[name]:
            out[f.agent_uuid] = name
    return out


# ---------------------------------------------------------------------------
# Agent rows + details
# ---------------------------------------------------------------------------


def _agent_strategy_name(session, agent: EvoAgent) -> tuple[str | None, str]:
    active = session.scalar(
        select(EvoStrategy)
        .where(EvoStrategy.agent_uuid == agent.agent_uuid, EvoStrategy.status == "active")
        .order_by(EvoStrategy.id.desc())
        .limit(1)
    )
    trading = session.scalar(
        select(EvoGenome)
        .where(EvoGenome.agent_uuid == agent.agent_uuid, EvoGenome.kind == "trading")
        .order_by(EvoGenome.revision.desc())
        .limit(1)
    )
    doc = (trading.document_json if trading else {}) or {}
    if active is not None:
        return active.name, str(doc.get("strategy_family") or "")
    fam = str(doc.get("strategy_family") or "unassigned")
    return None, fam


def _agent_status(session, agent: EvoAgent, ledger: str, has_active_strategy: bool) -> str:
    if agent.status != "active":
        return "paused"
    open_pos = session.scalar(
        select(func.count(EvoPosition.id)).where(
            EvoPosition.agent_uuid == agent.agent_uuid,
            EvoPosition.ledger == ledger,
            EvoPosition.status == "open",
        )
    ) or 0
    if open_pos or has_active_strategy:
        return "trading"
    open_exp = session.scalar(
        select(func.count(EvoExperiment.id)).where(
            EvoExperiment.agent_uuid == agent.agent_uuid, EvoExperiment.status == "open"
        )
    ) or 0
    if open_exp:
        return "researching"
    return "sleeping"


def _agent_row(session, settings, cohort, agent, ledger, fitness, group, now) -> dict:
    equity = paper.nav(session, agent.agent_uuid, ledger)
    start = settings.starting_capital_usd
    strat_name, strat_family = _agent_strategy_name(session, agent)
    last_hb = session.scalar(
        select(func.max(EvoHeartbeat.created_at)).where(
            EvoHeartbeat.agent_uuid == agent.agent_uuid
        )
    )
    return {
        "uuid": agent.agent_uuid,
        "rank": fitness.rank if fitness else None,
        "name": agent.display_name.split(" · ")[0],
        "display_name": agent.display_name,
        "family": agent.surname,
        "generation": agent.generation,
        "strategy": strat_name or (strat_family if strat_family != "unassigned" else "researching"),
        "equity": round(equity, 2),
        "pnl": round(equity - start, 2),
        "return_pct": round((equity - start) / start * 100, 2) if start else 0.0,
        "fitness": round(fitness.selection_score, 1) if fitness else None,
        "status": _agent_status(session, agent, ledger, strat_name is not None),
        "last_heartbeat_at": _iso(last_hb),
        "group": group,
        "detail": _agent_detail(session, settings, cohort, agent, ledger),
    }


def _agent_detail(session, settings, cohort, agent, ledger) -> dict:
    trading = session.scalar(
        select(EvoGenome)
        .where(EvoGenome.agent_uuid == agent.agent_uuid, EvoGenome.kind == "trading")
        .order_by(EvoGenome.revision.desc())
        .limit(1)
    )
    doc = (trading.document_json if trading else {}) or {}
    positions = paper.open_positions(session, agent.agent_uuid, ledger)
    fitness = session.scalar(
        select(EvoFitness)
        .where(EvoFitness.cohort_id == cohort.id, EvoFitness.agent_uuid == agent.agent_uuid,
               EvoFitness.kind == "interim")
        .order_by(EvoFitness.computed_at.desc())
        .limit(1)
    )
    components = {}
    if fitness and fitness.components_json:
        for key in ("profit", "consistency", "risk", "evidence", "forecast", "adaptive"):
            comp = fitness.components_json.get(key)
            if isinstance(comp, dict) and "points" in comp:
                components[key] = round(float(comp["points"]), 1)
    # last heartbeat's journal decision (structured summary, NOT hidden reasoning)
    last_hb = session.scalar(
        select(EvoHeartbeat)
        .where(EvoHeartbeat.agent_uuid == agent.agent_uuid, EvoHeartbeat.status == "completed")
        .order_by(EvoHeartbeat.id.desc())
        .limit(1)
    )
    last_summary = None
    if last_hb and last_hb.journal_memory_id:
        journal = session.get(EvoMemory, last_hb.journal_memory_id)
        if journal and journal.body_json:
            last_summary = _cap(journal.body_json.get("decision"))
    # last trading strategy change
    last_change = session.scalar(
        select(EvoGenome)
        .where(EvoGenome.agent_uuid == agent.agent_uuid, EvoGenome.kind == "trading",
               EvoGenome.material.is_(True), EvoGenome.revision > 1)
        .order_by(EvoGenome.revision.desc())
        .limit(1)
    )
    parent = None
    if agent.parent_uuid:
        p = session.scalar(select(EvoAgent).where(EvoAgent.agent_uuid == agent.parent_uuid))
        parent = p.display_name if p else None
    children = session.scalar(
        select(func.count(EvoAgent.id)).where(EvoAgent.parent_uuid == agent.agent_uuid)
    ) or 0
    budget = budgets.budget_summary(session, agent.agent_uuid, cohort.id).get(
        "llm_cost_usd", {}
    )
    return {
        "thesis": _cap(doc.get("thesis")),
        "strategy_family": doc.get("strategy_family"),
        "trading_revision": trading.revision if trading else None,
        "positions": [
            {"ticker": p.market_ticker, "side": p.side, "qty": float(p.quantity),
             "avg_price_cents": float(p.avg_price_cents),
             "mark_cents": p.last_mark_cents,
             "unrealized_usd": (round(float(p.unrealized_pnl_usd), 2)
                                if p.unrealized_pnl_usd is not None else None)}
            for p in positions[:20]
        ],
        "fitness_components": components,
        "last_heartbeat_summary": last_summary,
        "last_strategy_change": (
            {"revision": last_change.revision, "at": _iso(last_change.created_at),
             "changed": (last_change.changed_fields_json or [])[:8]}
            if last_change else None
        ),
        "parent": parent,
        "children": int(children),
        "llm_budget_remaining_usd": round(float(budget.get("remaining", 0.0)), 3),
        "llm_budget_total_usd": round(float(budget.get("allocated", 0.0)), 2),
    }


# ---------------------------------------------------------------------------
# Recent activity feed (merged, structured, sanitized)
# ---------------------------------------------------------------------------


def _name_map(session) -> dict[str, str]:
    return {
        a.agent_uuid: a.display_name.split(" · ")[0]
        for a in session.scalars(select(EvoAgent))
    }


def _recent_activity(session, now: datetime, limit: int = 20) -> list[dict]:
    names = _name_map(session)
    events: list[dict] = []

    for hb in session.scalars(
        select(EvoHeartbeat).where(EvoHeartbeat.status == "completed")
        .order_by(EvoHeartbeat.id.desc()).limit(25)
    ):
        events.append({
            "at": _iso(hb.created_at), "category": "heartbeats",
            "agent": names.get(hb.agent_uuid),
            "type": f"{hb.kind} heartbeat",
            "description": f"completed a {hb.kind} heartbeat",
        })

    for o in session.scalars(
        select(EvoOrder).where(EvoOrder.status.in_(("filled", "partial", "open")))
        .order_by(EvoOrder.id.desc()).limit(25)
    ):
        # A resting order is NOT a position. Saying "opened a position" for a
        # status='open' row (zero fills, no position row, no capital deployed)
        # reads as a completed trade and contradicts the same page's per-agent
        # "no open positions" card. Describe what actually happened instead, and
        # say how much of it filled when it did.
        side_action = "buy" if o.action == "buy" else "sell"
        if o.status == "open":
            desc = (f"placed a {side_action} order for {o.quantity} {o.side} "
                    f"in {o.market_ticker} (resting, unfilled)")
        else:
            verb = "opened" if o.action == "buy" else "closed"
            filled = o.filled_quantity or 0
            qualifier = "" if o.status == "filled" else f" ({filled}/{o.quantity} filled)"
            desc = (f"{verb} a {o.side} position in {o.market_ticker}"
                    f"{qualifier}")
        events.append({
            "at": _iso(o.created_at), "category": "trades",
            "agent": names.get(o.agent_uuid),
            "type": "paper order",
            "description": desc,
        })

    for g in session.scalars(
        select(EvoGenome).where(EvoGenome.kind == "trading", EvoGenome.material.is_(True),
                                EvoGenome.revision > 1)
        .order_by(EvoGenome.id.desc()).limit(15)
    ):
        events.append({
            "at": _iso(g.created_at), "category": "strategy",
            "agent": names.get(g.agent_uuid),
            "type": "strategy change",
            "description": f"revised trading strategy to revision {g.revision}",
        })

    for li in session.scalars(
        select(EvoListener).order_by(EvoListener.id.desc()).limit(10)
    ):
        events.append({
            "at": _iso(li.created_at), "category": "strategy",
            "agent": names.get(li.owner_uuid),
            "type": "listener created",
            "description": f"created a listener ({_cap(li.name, 40)})",
        })

    for e in session.scalars(
        select(EvoExperiment).where(EvoExperiment.status == "concluded")
        .order_by(EvoExperiment.id.desc()).limit(15)
    ):
        events.append({
            "at": _iso(e.concluded_at or e.created_at), "category": "experiments",
            "agent": names.get(e.agent_uuid),
            "type": "experiment concluded",
            "description": "completed a research experiment",
        })

    for t in session.scalars(
        select(EvoTicket).order_by(EvoTicket.id.desc()).limit(10)
    ):
        events.append({
            "at": _iso(t.created_at), "category": "requests",
            "agent": names.get(t.requesting_uuid),
            "type": "capability request",
            "description": f"submitted a {t.category} request",
        })

    for i in session.scalars(
        select(EvoInfluence).order_by(EvoInfluence.id.desc()).limit(10)
    ):
        events.append({
            "at": _iso(i.created_at), "category": "evolution",
            "agent": names.get(i.receiving_uuid),
            "type": "peer influence",
            "description": f"adopted an idea from a peer ({_cap(i.concept, 40)})",
        })

    events = [e for e in events if e["at"]]
    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:limit]


# ---------------------------------------------------------------------------
# System components
# ---------------------------------------------------------------------------


def _rel(now: datetime, dt: datetime | None) -> str:
    if dt is None:
        return "never"
    mins = (now - _aware(dt)).total_seconds() / 60.0
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{int(mins)}m ago"
    if mins < 60 * 24:
        return f"{int(mins / 60)}h ago"
    return f"{int(mins / 1440)}d ago"


def _components(session, settings: EvoSettings, *, cohort, now: datetime) -> list[dict]:
    def count(model, *where):
        q = select(func.count(model.id))
        for w in where:
            q = q.where(w)
        return int(session.scalar(q) or 0)

    worker_alive = False
    cohort_info = "no open cohort"
    if cohort is not None:
        ends = _aware(cohort.ends_at)
        secs = max(0, int((ends - now).total_seconds()))
        cohort_info = f"cohort {cohort.number} ends in {secs // 86400}d {secs % 86400 // 3600}h"
        last_hb = _last_heartbeat_at(session)
        worker_alive = last_hb is not None and (now - _aware(last_hb)).total_seconds() < 90 * 60

    hb_today = 0
    if cohort is not None:
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        hb_today = count(EvoHeartbeat, EvoHeartbeat.status == "completed",
                         EvoHeartbeat.created_at >= day_start)
    active_listeners = count(EvoListener, EvoListener.status == "active")
    completed_trades = 0
    if cohort is not None:
        completed_trades = count(
            EvoPosition, EvoPosition.ledger == paper.cohort_ledger(cohort.id),
            EvoPosition.status.in_(("settled", "closed", "liquidated", "transferred")),
        )
    memory_records = count(EvoMemory)
    open_experiments = count(EvoExperiment, EvoExperiment.status == "open")
    active_sources = count(EvoDataSource, EvoDataSource.status == "ok")
    open_tickets = count(EvoTicket, EvoTicket.status.in_(("open", "in_review")))
    last_fitness = session.scalar(select(func.max(EvoFitness.computed_at)))

    run = "running" if worker_alive else ("waiting" if cohort else "waiting")
    return [
        {"system": "Cohort manager", "status": run, "info": cohort_info},
        {"system": "Heartbeat system", "status": run, "info": f"{hb_today} completed today"},
        {"system": "Listener system", "status": run, "info": f"{active_listeners} active listeners"},
        {"system": "Paper simulator", "status": run, "info": f"{completed_trades} completed trades"},
        {"system": "Agent memory", "status": run, "info": f"{memory_records} memory records"},
        {"system": "Strategy sandbox", "status": run, "info": f"{open_experiments} active experiments"},
        {"system": "Fitness evaluator", "status": run,
         "info": f"last calculated {_rel(now, last_fitness)}"},
        {"system": "Evolution engine", "status": "waiting", "info": "runs at cohort end"},
        {"system": "Data registry", "status": run, "info": f"{active_sources} active sources"},
        {"system": "Request queue", "status": run, "info": f"{open_tickets} open requests"},
    ]


# ---------------------------------------------------------------------------
# Operator announcements (broadcast to every agent)
# ---------------------------------------------------------------------------


def _announcements(session, now: datetime) -> list[dict]:
    """Currently-broadcasting operator notices. Operator-authored, no secrets;
    length-capped like all other free text on the page."""
    out = []
    for a in announce.active_announcements(session, now=now):
        out.append({
            "title": _cap(a.title, 200),
            "body": _cap(a.body, 600),
            "category": a.category,
            "effective_at": _iso(_aware(a.effective_at)),
        })
    return out


# ---------------------------------------------------------------------------
# Capability requests
# ---------------------------------------------------------------------------


def _requests(session) -> list[dict]:
    out = []
    for t in tickets.review_queue(session):
        out.append({
            "request": _cap(t["capability"], 120),
            "category": t["category"],
            "requested_by": 1 + t["supporters"],  # requester + co-signers
            "families": max(1, t["supporting_families"]),
            "reason": _cap(t.get("problem"), 200) or "—",
            "status": t["status"],
        })
    return out
