"""The evolution engine (spec §22, §26-§30): agent creation, cohort finalization
(transactionally locked — never twice), retirement, reproduction with inheritance
snapshots + birth-divergence plans, and wildcard founders.

Idempotency chain: EvoTransition is UNIQUE per cohort (the finalization lock);
EvoBirth is UNIQUE per (cohort, slot_key) (a retry can never duplicate a child);
EvoRetirement is UNIQUE per agent; carryover/liquidation are unique per position."""

from __future__ import annotations

import logging
import random
import uuid as uuidlib
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import graveyard, memory
from . import paper as papermod
from .audit import audit
from .cognition import Cognition
from .cohorts import cohort_members, ensure_current_cohort, join_cohort
from .config import EvoSettings
from .constitution import ensure_config_version
from .fitness import evaluate_cohort, group_by_fractions
from .genomes import (
    current_genome,
    default_cognitive,
    default_trading,
    write_genome_revision,
)
from .heartbeats import run_heartbeat
from .marketdata import MarketData
from .models import (
    EvoAgent,
    EvoBirth,
    EvoCohort,
    EvoInheritanceSnapshot,
    EvoListener,
    EvoMemory,
    EvoRetirement,
    EvoTransition,
)
from .naming import display_name, draw_first_name, draw_surname, make_agent_code

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Agent creation
# ---------------------------------------------------------------------------


def create_agent(
    session,
    settings: EvoSettings,
    cohort: EvoCohort,
    rng: random.Random,
    *,
    origin: str,
    parent: EvoAgent | None = None,
    slot_key: str,
    cognitive_doc: dict | None = None,
    trading_doc: dict | None = None,
    inheritance_snapshot_id: int | None = None,
) -> EvoAgent | None:
    """Create one agent (founder/child/wildcard) idempotently via the birth slot.
    Returns None if the slot was already used (retry-safe)."""
    existing_birth = session.scalar(
        select(EvoBirth).where(
            EvoBirth.cohort_id == cohort.id, EvoBirth.slot_key == slot_key
        )
    )
    if existing_birth is not None:
        return session.scalar(
            select(EvoAgent).where(EvoAgent.agent_uuid == existing_birth.child_uuid)
        )
    if origin == "child":
        if parent is None:
            raise ValueError("child creation requires a parent")
        surname = parent.surname
        generation = parent.generation + 1
    else:
        surname = draw_surname(session, rng)
        generation = 1
    first = draw_first_name(session, rng, surname)
    code = make_agent_code(session, surname, generation)
    agent_uuid = str(uuidlib.UUID(int=rng.getrandbits(128), version=4))
    name = display_name(first, surname, generation, code)
    agent = EvoAgent(
        agent_uuid=agent_uuid,
        first_name=first,
        surname=surname,
        display_name=name,
        historical_name=name,
        agent_code=code,
        founder_uuid=parent.founder_uuid if parent is not None else agent_uuid,
        parent_uuid=parent.agent_uuid if parent is not None else None,
        generation=generation,
        origin=origin,
        birth_cohort_id=cohort.id,
        status="active",
    )
    session.add(agent)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return None
    write_genome_revision(
        session, agent, "cognitive", cognitive_doc or default_cognitive(),
        hypothesis=f"{origin} genome at birth", material=False,
    )
    write_genome_revision(
        session, agent, "trading", trading_doc or default_trading(),
        hypothesis=f"{origin} genome at birth", material=False,
    )
    papermod.ensure_portfolio(session, agent_uuid, papermod.LIFETIME,
                              settings.starting_capital_usd)
    join_cohort(session, agent, cohort, settings)
    birth = EvoBirth(
        cohort_id=cohort.id,
        slot_key=slot_key,
        kind=origin if origin != "founder" else "founder",
        parent_uuid=parent.agent_uuid if parent is not None else None,
        child_uuid=agent_uuid,
        inheritance_snapshot_id=inheritance_snapshot_id,
    )
    session.add(birth)
    session.flush()
    audit(session, "agent_created", agent_uuid=agent_uuid, origin=origin,
          name=name, cohort_id=cohort.id, parent=birth.parent_uuid)
    return agent


def bootstrap_founders(
    session, settings: EvoSettings, *, cognition: Cognition | None = None,
    md: MarketData | None = None, now: datetime | None = None,
) -> list[EvoAgent]:
    """Idempotently create founders up to the population target in the current
    cohort. Safe to call every cycle."""
    cohort = ensure_current_cohort(session, settings, now=now)
    active = list(session.scalars(select(EvoAgent).where(EvoAgent.status == "active")))
    created: list[EvoAgent] = []
    rng = random.Random(cohort.rng_seed)
    for i in range(len(active), settings.population_size):
        agent = create_agent(
            session, settings, cohort, rng, origin="founder",
            slot_key=f"founder:{i}",
        )
        if agent is not None:
            created.append(agent)
            if cognition is not None and md is not None:
                run_heartbeat(
                    session, settings, agent=agent, cohort=cohort, kind="birth",
                    slot_id=f"birth:{cohort.id}", cognition=cognition, md=md,
                    extra_context={"birth_note": "You are a founder: produce a founder "
                                                 "thesis, cognitive plan and research plan."},
                )
    if created:
        audit(session, "founders_bootstrapped", count=len(created), cohort_id=cohort.id)
    return created


# ---------------------------------------------------------------------------
# Inheritance
# ---------------------------------------------------------------------------


def build_inheritance_snapshot(
    session, cohort_id: int, parent: EvoAgent
) -> EvoInheritanceSnapshot:
    existing = session.scalar(
        select(EvoInheritanceSnapshot).where(
            EvoInheritanceSnapshot.cohort_id == cohort_id,
            EvoInheritanceSnapshot.parent_uuid == parent.agent_uuid,
        )
    )
    if existing is not None:
        return existing
    cognitive = current_genome(session, parent.agent_uuid, "cognitive")
    trading = current_genome(session, parent.agent_uuid, "trading")
    beliefs = list(
        session.scalars(
            select(EvoMemory)
            .where(
                EvoMemory.agent_uuid == parent.agent_uuid,
                EvoMemory.kind == "belief",
                EvoMemory.superseded.is_(False),
            )
            .order_by(EvoMemory.importance.desc())
            .limit(30)
        )
    )
    listeners_rows = list(
        session.scalars(
            select(EvoListener).where(EvoListener.owner_uuid == parent.agent_uuid)
        )
    )
    episodes = list(
        session.scalars(
            select(EvoMemory)
            .where(EvoMemory.agent_uuid == parent.agent_uuid, EvoMemory.kind == "episode")
            .order_by(EvoMemory.importance.desc())
            .limit(15)
        )
    )
    bundle = {
        "parent": {"uuid": parent.agent_uuid, "name": parent.historical_name,
                   "generation": parent.generation},
        "cognitive_revision": cognitive.revision if cognitive else None,
        "trading_revision": trading.revision if trading else None,
        "beliefs": [
            {"title": b.title, "belief": (b.body_json or {}).get("belief"),
             "confidence": b.confidence, "tags": [b.tag1, b.tag2]}
            for b in beliefs
        ],
        "episodes": [
            {"title": e.title, "detail": (e.body_json or {}).get("detail", "")[:400]}
            for e in episodes
        ],
        "listeners": [
            {"name": li.name, "condition": li.condition_json, "effect": li.effect,
             "status": li.status, "useful": li.useful_count, "useless": li.useless_count}
            for li in listeners_rows
        ],
    }
    snap = EvoInheritanceSnapshot(
        cohort_id=cohort_id,
        parent_uuid=parent.agent_uuid,
        cognitive_genome_id=cognitive.id if cognitive else None,
        trading_genome_id=trading.id if trading else None,
        bundle_json=bundle,
    )
    session.add(snap)
    session.flush()
    return snap


def _inherit_memories(session, parent_uuid: str, child_uuid: str, bundle: dict) -> int:
    """Copy the snapshot beliefs + episode summaries into the child (marked
    inherited_from). The parent's rows are untouched."""
    n = 0
    for belief in bundle.get("beliefs", []):
        session.add(
            EvoMemory(
                agent_uuid=child_uuid,
                kind="belief",
                immutable=False,
                title=str(belief.get("title", "inherited belief"))[:200],
                body_json={"belief": belief.get("belief"),
                           "inherited": True,
                           "evidence_for": "inherited from parent snapshot",
                           "confidence_after": belief.get("confidence")},
                importance=0.5,
                confidence=belief.get("confidence"),
                tag1=(belief.get("tags") or [None])[0],
                inherited_from_uuid=parent_uuid,
            )
        )
        n += 1
    for episode in bundle.get("episodes", []):
        session.add(
            EvoMemory(
                agent_uuid=child_uuid,
                kind="episode",
                immutable=False,
                title=str(episode.get("title", "inherited episode"))[:200],
                body_json={"detail": episode.get("detail"), "inherited": True},
                importance=0.4,
                inherited_from_uuid=parent_uuid,
            )
        )
        n += 1
    session.flush()
    return n


DIVERGENCE_QUESTIONS = (
    "1 What did your parent do well? 2 What evidence supports that? 3 What limitation "
    "prevented better performance? 4 Which parent behavior stays unchanged? 5 Which "
    "cognitive process will you reconsider? 6 Which trading component will you "
    "reconsider? 7 What SPECIFIC improvement will you explore? 8 Why might it improve "
    "profitability/consistency? 9 What evidence would support it? 10 What evidence "
    "forces rollback? 11 How do you avoid duplicating the parent? 12 What listener/"
    "test/experiment comes first? 13 What parent strategy remains as fallback?"
)


# ---------------------------------------------------------------------------
# Cohort finalization (spec §22 — the 29 steps)
# ---------------------------------------------------------------------------


def maybe_finalize_cohort(
    session,
    settings: EvoSettings,
    cohort: EvoCohort,
    *,
    md: MarketData,
    cognition: Cognition,
    now: datetime | None = None,
) -> dict | None:
    """Finalize an ended cohort and open the next one. The unique EvoTransition
    row is the lock: exactly one worker proceeds; reruns return None."""
    now = now or _now()
    ends = cohort.ends_at if cohort.ends_at.tzinfo else cohort.ends_at.replace(
        tzinfo=timezone.utc
    )
    if now < ends or cohort.status == "finalized":
        return None
    transition = EvoTransition(cohort_id=cohort.id, status="running",
                               rng_seed=cohort.rng_seed)
    session.add(transition)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return None  # another worker holds (or held) the lock

    cohort.status = "finalizing"
    session.flush()
    rng = random.Random(cohort.rng_seed ^ 0xE7011)

    # 1-3: stop new intents, settle/mark through the cutoff, snapshot
    papermod.expire_cohort_orders(session, cohort.id)
    papermod.mark_and_settle(session, md)
    members = cohort_members(session, cohort.id)
    member_uuids = [m.agent_uuid for m in members]
    agents = {
        a.agent_uuid: a
        for a in session.scalars(
            select(EvoAgent).where(EvoAgent.agent_uuid.in_(member_uuids))
        )
    }
    active_uuids = [u for u in member_uuids if agents[u].status == "active"]
    for au in active_uuids:
        papermod.snapshot_portfolio(
            session, au, papermod.cohort_ledger(cohort.id), f"boundary:{cohort.number}"
        )
        papermod.snapshot_portfolio(session, au, papermod.LIFETIME,
                                    f"boundary:{cohort.number}")

    # 8-15: final scores + ranking
    cfg = ensure_config_version(session, settings)
    ranked = evaluate_cohort(
        session, settings, cohort.id, active_uuids, kind="final",
        config_version_id=cfg.id, now=now,
    )
    groups = group_by_fractions(ranked, settings)
    for group_name, rows in groups.items():
        for row in rows:
            member = next(m for m in members if m.agent_uuid == row.agent_uuid)
            member.final_rank = row.rank
            member.final_group = group_name
            member.cohort_score = row.cohort_score
            member.reliability_score = row.reliability_score
            member.selection_score = row.selection_score
    session.flush()

    # cohort-end heartbeats for survivors (reflection on the completed week)
    for group_name in ("top", "middle"):
        for row in groups[group_name]:
            agent = agents[row.agent_uuid]
            run_heartbeat(
                session, settings, agent=agent, cohort=cohort, kind="cohort_end",
                slot_id=f"cohort_end:{cohort.id}", cognition=cognition, md=md,
                extra_context={"final_rank": row.rank, "group": group_name},
            )

    # 16-19, 27: retire the bottom group
    retired: list[str] = []
    for row in groups["bottom"]:
        agent = agents[row.agent_uuid]
        hb = run_heartbeat(
            session, settings, agent=agent, cohort=cohort, kind="retirement",
            slot_id=f"retirement:{cohort.id}", cognition=cognition, md=md,
            extra_context={
                "retirement_note": (
                    "You are being retired (bottom group). Record your final "
                    "reflection: why you believe you failed, what you would change, "
                    "which lessons descendants should retain, which components "
                    "should not be repeated. This cannot change your score."
                ),
                "final_rank": row.rank,
            },
        )
        liquidation = papermod.liquidate_agent(
            session, agent_uuid=agent.agent_uuid, cohort_id=cohort.id, md=md
        )
        reflection = None
        if hb is not None and hb.journal_memory_id:
            journal = session.get(EvoMemory, hb.journal_memory_id)
            reflection = journal.body_json if journal else None
        if session.scalar(
            select(EvoRetirement).where(EvoRetirement.agent_uuid == agent.agent_uuid)
        ) is None:
            session.add(
                EvoRetirement(
                    agent_uuid=agent.agent_uuid,
                    cohort_id=cohort.id,
                    reason="bottom_group",
                    final_rank=row.rank,
                    final_selection_score=row.selection_score,
                    liquidation_value_usd=liquidation,
                    reflection_json=reflection,
                )
            )
        agent.status = "retired"
        agent.status_reason = f"bottom {int(settings.bottom_fraction * 100)}% of cohort "\
                              f"#{cohort.number} (rank {row.rank})"
        agent.retired_at = _now()
        graveyard.add_entry(
            session,
            slug=f"agent-{agent.agent_code.lower()}",
            title=f"Retired agent {agent.historical_name}",
            summary=(
                f"Retired at rank {row.rank}/{len(ranked)} of cohort #{cohort.number} "
                f"with selection score {row.selection_score:.1f}. Strategy family: "
                f"{(current_genome(session, agent.agent_uuid, 'trading').document_json or {}).get('strategy_family', 'unknown')}."
            ),
            outcome="retired",
            source="agent",
            source_ref=agent.agent_uuid,
            why_failed=str((reflection or {}).get("decision", ""))[:1000] or None,
        )
        retired.append(agent.agent_uuid)
    session.flush()

    # 22, 25-26: open the next cohort + move survivors with normalized capital
    cohort.status = "finalized"
    session.flush()
    next_cohort = ensure_current_cohort(session, settings, now=now)
    survivors = groups["top"] + groups["middle"]
    for row in survivors:
        agent = agents[row.agent_uuid]
        join_cohort(session, agent, next_cohort, settings)
        papermod.carry_over_positions(
            session, settings, agent_uuid=agent.agent_uuid,
            old_cohort_id=cohort.id, new_cohort_id=next_cohort.id, md=md,
        )

    # 23-24: reproduction (top group), wildcard every Nth cohort
    wildcard_slot = next_cohort.wildcard_cohort
    parents = list(groups["top"])
    skipped_parent: str | None = None
    if wildcard_slot and parents:
        skipped_parent = parents[-1].agent_uuid  # lowest-ranked top agent skips
        parents = parents[:-1]
    children: list[str] = []
    for row in parents:
        parent = agents[row.agent_uuid]
        snap = build_inheritance_snapshot(session, cohort.id, parent)
        p_cognitive = current_genome(session, parent.agent_uuid, "cognitive")
        p_trading = current_genome(session, parent.agent_uuid, "trading")
        child = create_agent(
            session, settings, next_cohort, rng,
            origin="child", parent=parent, slot_key=parent.agent_uuid,
            cognitive_doc=p_cognitive.document_json if p_cognitive else None,
            trading_doc=p_trading.document_json if p_trading else None,
            inheritance_snapshot_id=snap.id,
        )
        if child is None:
            continue
        _inherit_memories(session, parent.agent_uuid, child.agent_uuid, snap.bundle_json)
        hb = run_heartbeat(
            session, settings, agent=child, cohort=next_cohort, kind="birth",
            slot_id=f"birth:{next_cohort.id}", cognition=cognition, md=md,
            extra_context={
                "birth_note": (
                    "You are a NEW child agent. Your parent's knowledge is inherited. "
                    "Produce your birth-divergence plan answering: " + DIVERGENCE_QUESTIONS
                ),
                "inheritance": snap.bundle_json,
            },
        )
        if hb is not None and hb.journal_memory_id:
            journal = session.get(EvoMemory, hb.journal_memory_id)
            birth = session.scalar(
                select(EvoBirth).where(
                    EvoBirth.cohort_id == next_cohort.id,
                    EvoBirth.slot_key == parent.agent_uuid,
                )
            )
            if birth is not None and journal is not None:
                birth.divergence_plan_json = journal.body_json
        children.append(child.agent_uuid)

    wildcard_uuid: str | None = None
    if wildcard_slot:
        wc = create_agent(
            session, settings, next_cohort, rng,
            origin="wildcard", slot_key="wildcard:1",
        )
        if wc is not None:
            wildcard_uuid = wc.agent_uuid
            run_heartbeat(
                session, settings, agent=wc, cohort=next_cohort, kind="birth",
                slot_id=f"birth:{next_cohort.id}", cognition=cognition, md=md,
                extra_context={
                    "birth_note": (
                        "You are a WILDCARD founder with a new surname and no parent "
                        "memory. Favor UNDEREXPLORED strategy families, data sources, "
                        "market categories or cognitive approaches — check the "
                        "graveyard and the peer roster and avoid cloning the dominant "
                        "family. Produce a founder thesis, cognitive plan and research "
                        "plan."
                    ),
                },
            )

    # 28: audit + close the transition
    outcome = {
        "cohort": cohort.number,
        "ranked": [
            {"uuid": r.agent_uuid, "rank": r.rank, "selection": r.selection_score}
            for r in ranked
        ],
        "retired": retired,
        "survivors": [r.agent_uuid for r in survivors],
        "children": children,
        "wildcard": wildcard_uuid,
        "skipped_parent": skipped_parent,
        "next_cohort": next_cohort.number,
    }
    transition.status = "completed"
    transition.finished_at = _now()
    transition.outcome_json = outcome
    session.flush()
    audit(session, "cohort_finalized", cohort_id=cohort.id,
          retired=len(retired), children=len(children),
          wildcard=bool(wildcard_uuid))
    return outcome
