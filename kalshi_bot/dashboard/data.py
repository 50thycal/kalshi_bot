"""Read-only data layer: turn the evo_* tables into the dashboard's JSON payloads.

Security (the page is served at a public Railway URL with no auth): this module
returns ONLY the structured, non-sensitive fields the dashboard needs. It never
returns API keys, environment variables, credentials, raw prompts, hidden
reasoning, database connection info, or internal stack traces. Agent-authored
free text (thesis, journal decision, ticket text) is already structured/bounded
and is additionally length-capped here. Heartbeat error detail (which can contain
internal tracebacks) is deliberately reduced to a generic status label.

Everything here is a SELECT; no row is ever added, updated or committed. Existing
evo services are reused where practical (paper ledger math, budgets, tickets), and
all financial aggregation lives in metrics.py so the fleet cards, the leaderboard
and the per-bot detail can never disagree.

Payloads (one per API route — see server.py):
  build_fleet          -> /api/fleet          cohort header, totals, health, generations
  build_bots           -> /api/bots           the sortable/filterable leaderboard
  build_bot_detail     -> /api/bots/<uuid>    strategy, performance, lineage
  build_bot_trades     -> /api/bots/<uuid>/trades
  build_events         -> /api/events         categorised, paginated activity
  build_announcements  -> /api/announcements
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from ..evo import budgets, paper, tickets
from ..evo.cohorts import active_agents, current_cohort
from ..evo.config import EvoSettings
from ..evo.fitness import group_by_fractions
from ..evo.models import (
    EvoAgent,
    EvoCohort,
    EvoCohortMember,
    EvoConfigVersion,
    EvoDataSource,
    EvoExperiment,
    EvoFitness,
    EvoGenome,
    EvoHeartbeat,
    EvoListener,
    EvoLlmUsage,
    EvoMemory,
    EvoPosition,
    EvoStrategy,
    EvoTicket,
)
from . import events as events_mod
from . import metrics
from .strategy_view import strategy_view, summarize_spec

TEXT_CAP = 400

# Bot classifications surfaced on the leaderboard, most-to-least engaged.
STATUS_TRADING = "trading"
STATUS_RESEARCHING = "researching"
STATUS_IDLE = "idle"
STATUS_DORMANT = "dormant"
STATUS_PAUSED = "paused"
STATUS_ELIMINATED = "eliminated"

BOT_SORTS = (
    "total_pnl", "realized_pnl", "unrealized_pnl", "win_rate", "trades",
    "best_trade", "worst_trade", "last_activity", "fitness", "name",
)
DEFAULT_BOT_SORT = "total_pnl"


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


def _live_uuids(session) -> set[str]:
    """The bots the worker actually runs this cycle.

    The cap is a real population bound now, not a filter over a larger fleet:
    the worker keeps exactly that many agents `active` and marks the rest
    `suspended`, so `active_agents` alone is already the live set. The slice
    below only covers the window before the worker has reconciled (e.g. right
    after a cap decrease), and must never become load-bearing — slicing
    "lowest id first" is what previously made newborn children invisible to
    the fleet, since reproduction assigns them the highest ids.

    Suspended bots are NOT hidden: they stay in the cohort roster and are
    labelled `paused` by _classify, so a shrunken fleet is visible as such
    rather than silently disappearing."""
    everyone = active_agents(session)
    cap = _effective_cap(session)
    live = everyone[:cap] if cap > 0 else everyone
    return {a.agent_uuid for a in live}


# ---------------------------------------------------------------------------
# Cohort / generation resolution
# ---------------------------------------------------------------------------


def _resolve_cohort(session, generation: int | None) -> EvoCohort | None:
    """The cohort a request is scoped to: an explicit generation number, else the
    open one, else the most recent."""
    if generation is not None:
        return session.scalar(select(EvoCohort).where(EvoCohort.number == generation))
    return current_cohort(session) or session.scalar(
        select(EvoCohort).order_by(EvoCohort.number.desc()).limit(1)
    )


def _cohort_header(cohort, now: datetime) -> dict:
    ends, starts = _aware(cohort.ends_at), _aware(cohort.starts_at)
    elapsed = max(0.0, (now - starts).total_seconds())
    return {
        "number": cohort.number,
        "cohort_id": cohort.id,
        "status": cohort.status,
        "starts_at": _iso(starts),
        "ends_at": _iso(ends),
        "seconds_elapsed": int(elapsed),
        "seconds_remaining": int(max(0.0, (ends - now).total_seconds())),
        "wildcard_next": bool(cohort.wildcard_cohort),
    }


def _last_heartbeat_at(session) -> datetime | None:
    return session.scalar(select(func.max(EvoHeartbeat.created_at)))


def _system_status(session, cohort, now: datetime) -> str:
    if cohort is None or cohort.status in ("finalizing", "finalized"):
        return "between_cohorts"
    last_hb = _last_heartbeat_at(session)
    if last_hb is None:
        return "starting"
    minutes = (now - _aware(last_hb)).total_seconds() / 60.0
    # heartbeats are bounded by the cycle/slot cadence; a long gap means the
    # worker isn't running (paused/error) rather than merely idle.
    return "paused" if minutes > 90 else "running"


# ---------------------------------------------------------------------------
# Fitness / projected grouping
# ---------------------------------------------------------------------------


def _latest_interim_fitness(session, cohort_id: int) -> dict[str, EvoFitness]:
    """Newest interim fitness row per bot (operator view — not the 6h-delayed peer
    copy, since this is the human's private dashboard)."""
    out: dict[str, EvoFitness] = {}
    for row in session.scalars(
        select(EvoFitness)
        .where(EvoFitness.cohort_id == cohort_id, EvoFitness.kind == "interim")
        .order_by(EvoFitness.computed_at.desc())
    ):
        out.setdefault(row.agent_uuid, row)
    return out


def _projected_groups(fitness_by_uuid: dict[str, EvoFitness], settings: EvoSettings) -> dict[str, str]:
    ranked = sorted(
        fitness_by_uuid.values(), key=lambda f: (-(f.selection_score or 0), f.agent_uuid)
    )
    if not ranked:
        return {}
    groups = group_by_fractions(ranked, settings)
    return {f.agent_uuid: name for name in ("top", "middle", "bottom") for f in groups[name]}


def _previous_results(session, agent_uuids: list[str], cohort_id: int) -> dict[str, str]:
    """Each bot's most recent FINALIZED result — 'top' means it was promoted (it
    reproduced), 'bottom' means it was eliminated. Distinct from the projection for
    the generation currently in flight."""
    if not agent_uuids:
        return {}
    out: dict[str, str] = {}
    for member in session.scalars(
        select(EvoCohortMember)
        .where(
            EvoCohortMember.agent_uuid.in_(agent_uuids),
            EvoCohortMember.cohort_id != cohort_id,
            EvoCohortMember.final_group.is_not(None),
        )
        .order_by(EvoCohortMember.cohort_id.desc())
    ):
        out.setdefault(member.agent_uuid, member.final_group)
    return out


# ---------------------------------------------------------------------------
# Bot roster
# ---------------------------------------------------------------------------


def _roster(session, cohort) -> list[EvoAgent]:
    """Every bot that belongs to this generation — including ones retired at its
    close, so a finished generation still shows its full cohort."""
    if cohort is None:
        return list(session.scalars(select(EvoAgent).where(EvoAgent.status == "active")))
    uuids = [
        m.agent_uuid
        for m in session.scalars(
            select(EvoCohortMember).where(EvoCohortMember.cohort_id == cohort.id)
        )
    ]
    if not uuids:
        return list(session.scalars(select(EvoAgent).where(EvoAgent.status == "active")))
    return list(
        session.scalars(select(EvoAgent).where(EvoAgent.agent_uuid.in_(uuids)).order_by(EvoAgent.id))
    )


def _active_strategies(session, agent_uuids: list[str]) -> dict[str, EvoStrategy]:
    if not agent_uuids:
        return {}
    out: dict[str, EvoStrategy] = {}
    for s in session.scalars(
        select(EvoStrategy)
        .where(EvoStrategy.agent_uuid.in_(agent_uuids), EvoStrategy.status == "active")
        .order_by(EvoStrategy.id.desc())
    ):
        out.setdefault(s.agent_uuid, s)
    return out


def _trading_genomes(session, agent_uuids: list[str]) -> dict[str, EvoGenome]:
    if not agent_uuids:
        return {}
    out: dict[str, EvoGenome] = {}
    for g in session.scalars(
        select(EvoGenome)
        .where(EvoGenome.agent_uuid.in_(agent_uuids), EvoGenome.kind == "trading")
        .order_by(EvoGenome.revision.desc())
    ):
        out.setdefault(g.agent_uuid, g)
    return out


def _open_experiment_counts(session, agent_uuids: list[str]) -> dict[str, int]:
    if not agent_uuids:
        return {}
    return {
        uuid: int(count)
        for uuid, count in session.execute(
            select(EvoExperiment.agent_uuid, func.count(EvoExperiment.id))
            .where(EvoExperiment.agent_uuid.in_(agent_uuids), EvoExperiment.status == "open")
            .group_by(EvoExperiment.agent_uuid)
        )
    }


def _classify(agent, *, live: bool, has_strategy: bool, open_positions: int, open_experiments: int) -> str:
    if agent.status == "retired":
        return STATUS_ELIMINATED
    if agent.status != "active":
        return STATUS_PAUSED
    if not live:
        return STATUS_DORMANT
    if open_positions or has_strategy:
        return STATUS_TRADING
    if open_experiments:
        return STATUS_RESEARCHING
    return STATUS_IDLE


def _bot_rows(session, settings: EvoSettings, cohort, *, now: datetime) -> list[dict]:
    """One fully-costed row per bot in the generation. Batched: a fixed number of
    queries regardless of fleet size."""
    agents = _roster(session, cohort)
    uuids = [a.agent_uuid for a in agents]
    if not uuids:
        return []

    ledger = metrics.cohort_ledger(cohort) or paper.LIFETIME
    perf = metrics.bot_performance(
        session, uuids, ledger, default_capital=settings.starting_capital_usd
    )
    activity = metrics.last_activity(session, uuids)
    strategies = _active_strategies(session, uuids)
    genomes = _trading_genomes(session, uuids)
    experiments = _open_experiment_counts(session, uuids)
    live = _live_uuids(session)
    fitness = _latest_interim_fitness(session, cohort.id) if cohort else {}
    groups = _projected_groups(fitness, settings)
    previous = _previous_results(session, uuids, cohort.id if cohort else -1)

    rows: list[dict] = []
    for agent in agents:
        uuid = agent.agent_uuid
        p = perf[uuid]
        strategy = strategies.get(uuid)
        doc = (genomes[uuid].document_json if uuid in genomes else {}) or {}
        family = str(doc.get("strategy_family") or "unassigned")
        fit = fitness.get(uuid)
        acts = activity.get(uuid, {})
        last_activity_at = max(
            [t for t in (acts.get("heartbeat_at"), acts.get("order_at"), p["last_trade_at"]) if t],
            default=None,
        )
        rows.append({
            "uuid": uuid,
            "name": agent.display_name.split(" · ")[0],
            "display_name": agent.display_name,
            "code": agent.agent_code,
            "family": agent.surname,
            "generation": agent.generation,
            "origin": agent.origin,
            "status": _classify(
                agent, live=uuid in live, has_strategy=strategy is not None,
                open_positions=p["trades_open"], open_experiments=experiments.get(uuid, 0),
            ),
            "strategy_name": strategy.name if strategy else None,
            "strategy_family": family,
            "strategy_summary": (
                summarize_spec(strategy.spec_json or {}) if strategy
                else _cap(doc.get("thesis")) or "No strategy armed yet — still researching."
            ),
            "rank": fit.rank if fit else None,
            "fitness": round(fit.selection_score, 1) if fit else None,
            "projected_group": groups.get(uuid, "unranked"),
            "last_result": previous.get(uuid),
            "last_activity_at": last_activity_at,
            "last_heartbeat_at": acts.get("heartbeat_at"),
            "performance": p,
        })
    return rows


_SORT_KEYS = {
    "total_pnl": lambda r: r["performance"]["total_pnl_usd"],
    "realized_pnl": lambda r: r["performance"]["realized_pnl_usd"],
    "unrealized_pnl": lambda r: r["performance"]["unrealized_pnl_usd"],
    "win_rate": lambda r: r["performance"]["win_rate"],
    "trades": lambda r: r["performance"]["trades_total"],
    "best_trade": lambda r: (r["performance"]["best_trade"] or {}).get("realized_pnl_usd"),
    "worst_trade": lambda r: (r["performance"]["worst_trade"] or {}).get("realized_pnl_usd"),
    "last_activity": lambda r: r["last_activity_at"],
    "fitness": lambda r: r["fitness"],
    "name": lambda r: r["name"],
}


def _sorted(rows: list[dict], sort: str, order: str) -> list[dict]:
    """Sort with missing values always last, in either direction — a bot with no
    trades should never win a "best trade" sort by being null."""
    key = _SORT_KEYS.get(sort, _SORT_KEYS[DEFAULT_BOT_SORT])
    descending = order != "asc"

    def sort_key(row):
        value = key(row)
        if value is None:
            return (1, 0, row["name"])
        if isinstance(value, str):
            return (0, 0, value.lower())
        return (0, -value if descending else value, row["name"].lower())

    rows = sorted(rows, key=sort_key)
    if descending and sort == "name":
        rows.reverse()
    return rows


def build_bots(
    session,
    settings: EvoSettings,
    *,
    generation: int | None = None,
    status: str | None = None,
    family: str | None = None,
    strategy: str | None = None,
    profitability: str | None = None,
    activity: str | None = None,
    sort: str = DEFAULT_BOT_SORT,
    order: str = "desc",
    limit: int | None = None,
    now: datetime | None = None,
) -> dict:
    """The bot leaderboard: every bot in the generation, filtered and sorted on the
    server so the browser never has to recompute a financial value."""
    now = now or datetime.now(timezone.utc)
    cohort = _resolve_cohort(session, generation)
    rows = _bot_rows(session, settings, cohort, now=now)

    # Filter option lists come from the UNFILTERED roster so the controls never
    # collapse to just the options that survived the current filter.
    facets = {
        "statuses": sorted({r["status"] for r in rows}),
        "families": sorted({r["family"] for r in rows}),
        "strategy_families": sorted({r["strategy_family"] for r in rows}),
        "strategies": sorted({r["strategy_name"] for r in rows if r["strategy_name"]}),
        "generations": sorted({r["generation"] for r in rows}),
    }

    if status:
        rows = [r for r in rows if r["status"] == status]
    if family:
        rows = [r for r in rows if r["family"] == family]
    if strategy:
        rows = [
            r for r in rows
            if strategy in (r["strategy_name"], r["strategy_family"])
        ]
    if profitability == "profitable":
        rows = [r for r in rows if r["performance"]["total_pnl_usd"] > 0]
    elif profitability == "unprofitable":
        rows = [r for r in rows if r["performance"]["total_pnl_usd"] < 0]
    elif profitability == "flat":
        rows = [r for r in rows if r["performance"]["total_pnl_usd"] == 0]
    if activity == "active":
        rows = [r for r in rows if r["status"] in (STATUS_TRADING, STATUS_RESEARCHING)]
    elif activity == "inactive":
        rows = [r for r in rows
                if r["status"] in (STATUS_IDLE, STATUS_DORMANT, STATUS_PAUSED, STATUS_ELIMINATED)]

    rows = _sorted(rows, sort if sort in BOT_SORTS else DEFAULT_BOT_SORT, order)
    total = len(rows)
    if limit:
        rows = rows[: max(1, int(limit))]
    return {
        "generated_at": _iso(now),
        "generation": cohort.number if cohort else None,
        "ledger": metrics.cohort_ledger(cohort),
        "bots": rows,
        "total": total,
        "sort": sort if sort in BOT_SORTS else DEFAULT_BOT_SORT,
        "order": "asc" if order == "asc" else "desc",
        "facets": facets,
    }


# ---------------------------------------------------------------------------
# Fleet overview
# ---------------------------------------------------------------------------


def _components(session, *, cohort, now: datetime) -> list[dict]:
    """Coarse liveness of each moving part, for the fleet-health panel."""

    def count(model, *where):
        query = select(func.count(model.id))
        for clause in where:
            query = query.where(clause)
        return int(session.scalar(query) or 0)

    worker_alive = False
    cohort_info = "no open generation"
    if cohort is not None:
        secs = max(0, int((_aware(cohort.ends_at) - now).total_seconds()))
        cohort_info = f"generation {cohort.number} ends in {secs // 86400}d {secs % 86400 // 3600}h"
        last_hb = _last_heartbeat_at(session)
        worker_alive = last_hb is not None and (now - _aware(last_hb)).total_seconds() < 90 * 60

    hb_today = 0
    completed_trades = 0
    if cohort is not None:
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        hb_today = count(EvoHeartbeat, EvoHeartbeat.status == "completed",
                         EvoHeartbeat.created_at >= day_start)
        completed_trades = count(
            EvoPosition, EvoPosition.ledger == paper.cohort_ledger(cohort.id),
            EvoPosition.status.in_(metrics.CLOSED_STATUSES),
        )
    last_fitness = session.scalar(select(func.max(EvoFitness.computed_at)))
    run = "running" if worker_alive else "waiting"
    return [
        {"system": "Cohort manager", "status": run, "info": cohort_info},
        {"system": "Heartbeat system", "status": run, "info": f"{hb_today} completed today"},
        {"system": "Listener system", "status": run,
         "info": f"{count(EvoListener, EvoListener.status == 'active')} active listeners"},
        {"system": "Paper simulator", "status": run, "info": f"{completed_trades} completed trades"},
        {"system": "Agent memory", "status": run, "info": f"{count(EvoMemory)} memory records"},
        {"system": "Strategy sandbox", "status": run,
         "info": f"{count(EvoExperiment, EvoExperiment.status == 'open')} active experiments"},
        {"system": "Fitness evaluator", "status": run,
         "info": f"last calculated {_rel(now, last_fitness)}"},
        {"system": "Evolution engine", "status": "waiting", "info": "runs at generation end"},
        {"system": "Data registry", "status": run,
         "info": f"{count(EvoDataSource, EvoDataSource.status == 'ok')} active sources"},
        {"system": "Request queue", "status": run,
         "info": f"{count(EvoTicket, EvoTicket.status.in_(('open', 'in_review')))} open requests"},
    ]


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


def build_fleet(session, settings: EvoSettings, *, now: datetime | None = None) -> dict:
    """Everything the top of the page needs: status, generation header, cohort
    totals, fleet health, per-generation history and the request queue."""
    now = now or datetime.now(timezone.utc)
    cohort = _resolve_cohort(session, None)
    rows = _bot_rows(session, settings, cohort, now=now)
    requests = _requests(session, now=now)
    perf_by_uuid = {r["uuid"]: r["performance"] for r in rows}
    names = {r["uuid"]: r["name"] for r in rows}
    summary = metrics.fleet_summary(perf_by_uuid, names=names)

    live = _live_uuids(session)
    # "active" here means status=='active' (excludes ELIMINATED/retired and PAUSED/
    # suspended) — a suspended agent (EVO_MAX_ACTIVE_AGENTS capped it out) isn't
    # running and shouldn't read as part of "how big is the fleet right now". This
    # can still exceed `live` for one cycle right after a cap change, before the
    # worker's next reconcile_population catches up (the dormant-but-not-yet-
    # suspended window) — that gap is real signal, not noise, so it is kept rather
    # than collapsed to `live` everywhere.
    active_rows = [r for r in rows if r["status"] not in (STATUS_ELIMINATED, STATUS_PAUSED)]
    active_perf = [perf_by_uuid[r["uuid"]] for r in active_rows]
    llm_cost = 0.0
    if cohort is not None:
        llm_cost = float(session.scalar(
            select(func.coalesce(func.sum(EvoLlmUsage.cost_usd), 0)).where(
                EvoLlmUsage.cohort_id == cohort.id
            )
        ) or 0)

    return {
        "generated_at": _iso(now),
        "status": _system_status(session, cohort, now),
        "generation": _cohort_header(cohort, now) if cohort else None,
        "summary": {
            **summary,
            # "bots" (and the profitable/unprofitable/flat split derived from it) is
            # the active population, not the cohort's full historical roster. Money
            # aggregates above (`**summary`: total/realized/unrealized P&L, trades,
            # fees) are deliberately left scoped to every cohort member, active or
            # suspended — that history is real and must not disappear from the total.
            "bots": len(active_rows),
            "active_bots": len(active_rows),
            "live_bots": len(live),
            "eliminated_bots": sum(1 for r in rows if r["status"] == STATUS_ELIMINATED),
            "trading_bots": sum(1 for r in rows if r["status"] == STATUS_TRADING),
            "profitable_bots": sum(1 for p in active_perf if p["total_pnl_usd"] > 0),
            "unprofitable_bots": sum(1 for p in active_perf if p["total_pnl_usd"] < 0),
            "flat_bots": sum(1 for p in active_perf if p["total_pnl_usd"] == 0),
            "per_bot_capital_usd": settings.starting_capital_usd,
            "llm_cost_usd": round(llm_cost, 2),
            "llm_budget_usd": round(len(active_rows) * settings.weekly_llm_ceiling_usd, 2),
        },
        "throttle": {
            "live": len(live),
            "total": len(active_rows),
            "throttled": _effective_cap(session) > 0,
        },
        "health": events_mod.fleet_health(
            session, settings, [r["uuid"] for r in rows if r["uuid"] in live],
            now=now,
        ),
        # generation_history reports `bots` as the cohort's full historical
        # membership (correct and immutable for a FINALIZED generation — that really
        # was its population). The still-OPEN generation is the one row where that
        # figure is a live, moving thing, so it is overridden here to match the same
        # active-population count as the summary tile above instead of the stale
        # pre-cap roster size.
        "generations": [
            {**g, "bots": len(active_rows)} if cohort and g["cohort_id"] == cohort.id else g
            for g in metrics.generation_history(session)
        ],
        "components": _components(session, cohort=cohort, now=now),
        "requests": requests,
        "request_count": len(requests),
        "announcement_count": events_mod.announcement_page(
            session, cap=_cap, now=now, limit=1
        )["total"],
    }


# ---------------------------------------------------------------------------
# Bot detail
# ---------------------------------------------------------------------------


def build_bot_detail(
    session, settings: EvoSettings, agent_uuid: str, *, now: datetime | None = None
) -> dict | None:
    """One bot in full: identity, strategy in plain English, performance, exposure
    and lineage. Returns None when the uuid is unknown."""
    now = now or datetime.now(timezone.utc)
    agent = session.scalar(select(EvoAgent).where(EvoAgent.agent_uuid == agent_uuid))
    if agent is None:
        return None

    cohort = _resolve_cohort(session, None)
    ledger = metrics.cohort_ledger(cohort) or paper.LIFETIME
    current = metrics.bot_performance(
        session, [agent_uuid], ledger, default_capital=settings.starting_capital_usd
    )[agent_uuid]
    lifetime = metrics.bot_performance(
        session, [agent_uuid], paper.LIFETIME, default_capital=settings.starting_capital_usd
    )[agent_uuid]

    fitness = None
    components: dict[str, float] = {}
    if cohort is not None:
        fitness = session.scalar(
            select(EvoFitness)
            .where(EvoFitness.cohort_id == cohort.id, EvoFitness.agent_uuid == agent_uuid,
                   EvoFitness.kind == "interim")
            .order_by(EvoFitness.computed_at.desc())
            .limit(1)
        )
    if fitness and fitness.components_json:
        for key in ("profit", "consistency", "risk", "evidence", "forecast", "adaptive"):
            comp = fitness.components_json.get(key)
            if isinstance(comp, dict) and "points" in comp:
                components[key] = round(float(comp["points"]), 1)

    # last heartbeat's journal decision (structured summary, NOT hidden reasoning)
    last_hb = session.scalar(
        select(EvoHeartbeat)
        .where(EvoHeartbeat.agent_uuid == agent_uuid, EvoHeartbeat.status == "completed")
        .order_by(EvoHeartbeat.id.desc())
        .limit(1)
    )
    last_summary = None
    if last_hb and last_hb.journal_memory_id:
        journal = session.get(EvoMemory, last_hb.journal_memory_id)
        if journal and journal.body_json:
            last_summary = _cap(journal.body_json.get("decision"))

    children = int(session.scalar(
        select(func.count(EvoAgent.id)).where(EvoAgent.parent_uuid == agent_uuid)
    ) or 0)
    budget = {}
    if cohort is not None:
        budget = budgets.budget_summary(session, agent_uuid, cohort.id).get("llm_cost_usd", {})

    open_positions = [
        {
            "market": p.market_ticker, "side": p.side, "quantity": float(p.quantity),
            "entry_price_cents": round(float(p.avg_price_cents), 2),
            "mark_cents": p.last_mark_cents,
            "unrealized_pnl_usd": (round(float(p.unrealized_pnl_usd), 2)
                                   if p.unrealized_pnl_usd is not None else None),
        }
        for p in paper.open_positions(session, agent_uuid, ledger)[:50]
    ]

    equity_curve = _equity_curve(session, agent_uuid, ledger)

    return {
        "generated_at": _iso(now),
        "uuid": agent_uuid,
        "name": agent.display_name.split(" · ")[0],
        "display_name": agent.display_name,
        "historical_name": agent.historical_name,
        "code": agent.agent_code,
        "family": agent.surname,
        "generation": agent.generation,
        "origin": agent.origin,
        "agent_status": agent.status,
        "status_reason": _cap(agent.status_reason, 200),
        "created_at": _iso(agent.created_at),
        "retired_at": _iso(agent.retired_at),
        "ledger": ledger,
        "performance": current,
        "lifetime_performance": lifetime,
        "open_positions": open_positions,
        "equity_curve": equity_curve,
        "fitness": {
            "rank": fitness.rank if fitness else None,
            "selection_score": round(fitness.selection_score, 1) if fitness else None,
            "components": components,
            "computed_at": _iso(fitness.computed_at) if fitness else None,
        },
        "strategy": strategy_view(session, agent, cap=_cap, iso=_iso),
        "children": children,
        "last_heartbeat_summary": last_summary,
        "last_heartbeat_at": _iso(last_hb.created_at) if last_hb else None,
        "llm_budget_remaining_usd": round(float(budget.get("remaining", 0.0)), 3),
        "llm_budget_total_usd": round(float(budget.get("allocated", 0.0)), 2),
    }


def _equity_curve(session, agent_uuid: str, ledger: str, *, limit: int = 60) -> list[dict]:
    """NAV over time from the daily portfolio snapshots the worker already writes.
    Empty until the bot has lived through at least one snapshot boundary."""
    from ..evo.models import EvoPortfolioSnapshot

    rows = list(session.scalars(
        select(EvoPortfolioSnapshot)
        .where(
            EvoPortfolioSnapshot.agent_uuid == agent_uuid,
            EvoPortfolioSnapshot.ledger == ledger,
        )
        .order_by(EvoPortfolioSnapshot.captured_at.desc())
        .limit(limit)
    ))
    return [
        {
            "at": _iso(row.captured_at),
            "label": row.label,
            "nav_usd": round(float(row.nav_usd), 2),
        }
        for row in reversed(rows)
    ]


def build_bot_trades(
    session,
    settings: EvoSettings,
    agent_uuid: str,
    *,
    status: str = "all",
    sort: str = "recent",
    limit: int = 100,
    offset: int = 0,
    scope: str = "generation",
) -> dict | None:
    agent = session.scalar(select(EvoAgent).where(EvoAgent.agent_uuid == agent_uuid))
    if agent is None:
        return None
    cohort = _resolve_cohort(session, None)
    ledger = paper.LIFETIME if scope == "lifetime" else (
        metrics.cohort_ledger(cohort) or paper.LIFETIME
    )
    payload = metrics.trade_rows(
        session, agent_uuid, ledger,
        status=status, sort=sort,
        limit=max(1, min(int(limit or 100), 500)),
        offset=max(0, int(offset or 0)),
    )
    payload["ledger"] = ledger
    payload["scope"] = "lifetime" if ledger == paper.LIFETIME else "generation"
    return payload


# ---------------------------------------------------------------------------
# Events + announcements (thin wrappers that inject the text policy)
# ---------------------------------------------------------------------------


def build_events(session, **kwargs) -> dict:
    return events_mod.fetch_events(session, cap=_cap, **kwargs)


def build_announcements(session, *, now: datetime | None = None, **kwargs) -> dict:
    now = now or datetime.now(timezone.utc)
    return events_mod.announcement_page(session, cap=_cap, now=now, **kwargs)


# ---------------------------------------------------------------------------
# Capability requests
# ---------------------------------------------------------------------------


_URGENCY_RANK = {"high": 0, "normal": 1, "low": 2}


def _requests(session, *, now: datetime | None = None) -> list[dict]:
    """Open capability requests, grouped so repetition reads as urgency.

    Ticket dedup keys on (category, capability), and an agent that cannot get an
    answer varies BOTH — one live agent cycled performance -> bug_report ->
    infrastructure -> platform_deployment -> permissions filing eighteen versions
    of the same ask over six days. Ungrouped, that buries every other request in
    the fleet under one agent's repetition.

    So group across category on the deduper's own token signature and surface the
    repeat count: "asked 18 times over 6 days" is the operator signal, and one row
    per distinct ask keeps a quieter agent's request visible next to it."""
    now = now or datetime.now(timezone.utc)
    groups: dict[frozenset[str], list[dict]] = {}
    for t in tickets.review_queue(session):
        _sig, tokens = tickets._signature(t["category"], t["capability"])
        key = next(
            (k for k in groups if tickets._jaccard(k, tokens) >= 0.6), tokens
        )
        groups.setdefault(key, []).append(t)

    rows: list[dict] = []
    for members in groups.values():
        newest = max(members, key=lambda m: m["created_at"])
        oldest = min(members, key=lambda m: m["created_at"])
        first_at = _aware(datetime.fromisoformat(oldest["created_at"]))
        # the most explanatory `problem` in the group, not just the latest
        reason = max((m.get("problem") or "" for m in members), key=len)
        rows.append({
            "request": _cap(newest["capability"], 120),
            "category": newest["category"],
            "agent": newest["requesting_uuid"][:8],
            "times_asked": len(members),
            "requested_by": 1 + sum(m["supporters"] for m in members),
            "families": max(1, max(m["supporting_families"] for m in members)),
            "urgency": min((m["urgency"] for m in members),
                           key=lambda u: _URGENCY_RANK.get(u, 1)),
            "reason": _cap(reason, 240) or "—",
            "age_days": max(0, int((now - first_at).total_seconds() // 86400)),
            "first_asked": _iso(first_at),
            "status": newest["status"],
        })
    # loudest first, then most urgent, then oldest — what an operator should read first
    rows.sort(key=lambda r: (-r["times_asked"], _URGENCY_RANK.get(r["urgency"], 1),
                             -r["age_days"]))
    return rows
