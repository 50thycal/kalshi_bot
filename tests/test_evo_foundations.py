"""Evo Stage-1 foundations: naming, genomes, memory, budgets, cohorts, lineage,
constitution, tickets, data sources, graveyard."""

from __future__ import annotations

import random
import uuid as uuidlib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from kalshi_bot.evo import (
    budgets,
    constitution,
    datasources,
    graveyard,
    graveyard_seed,
    lineage,
    memory,
    naming,
    tickets,
)
from kalshi_bot.evo import models as em
from kalshi_bot.evo.cohorts import (
    cohort_boundary_before,
    ensure_current_cohort,
    join_cohort,
)
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.genomes import (
    default_cognitive,
    default_trading,
    rollback_genome,
    validate_genome,
    write_genome_revision,
)


def _mk_agent(session, surname="Kepler", first="Mara", generation=1, parent=None):
    cohort = ensure_current_cohort(session, EvoSettings(_env_file=None))
    au = str(uuidlib.uuid4())
    agent = em.EvoAgent(
        agent_uuid=au,
        first_name=first,
        surname=surname,
        display_name=f"{first} {surname} · Generation {generation} · X-{au[:4]}",
        historical_name=f"{first} {surname}",
        agent_code=f"{naming.surname_prefix(surname)}-G{generation}-{au[:3]}",
        founder_uuid=parent.founder_uuid if parent else au,
        parent_uuid=parent.agent_uuid if parent else None,
        generation=generation,
        origin="child" if parent else "founder",
        birth_cohort_id=cohort.id,
        status="active",
    )
    session.add(agent)
    session.flush()
    return agent


# --- naming -----------------------------------------------------------------


def test_surnames_unique_across_population(evo_session):
    rng = random.Random(1)
    seen = set()
    for _ in range(len(naming.SURNAMES)):
        s = naming.draw_surname(evo_session, rng)
        assert s not in seen
        seen.add(s)
        _mk_agent(evo_session, surname=s)


def test_surname_pool_exhaustion_falls_back_to_suffix(evo_session):
    rng = random.Random(2)
    for s in naming.SURNAMES:
        _mk_agent(evo_session, surname=s)
    extra = naming.draw_surname(evo_session, rng)
    assert extra not in naming.SURNAMES
    assert any(extra.startswith(s) for s in naming.SURNAMES)


def test_first_name_unique_within_family(evo_session):
    rng = random.Random(3)
    used = set()
    for _ in range(10):
        f = naming.draw_first_name(evo_session, rng, "Kepler")
        assert f not in used
        used.add(f)
        _mk_agent(evo_session, surname="Kepler", first=f)


def test_display_name_format():
    assert (
        naming.display_name("Mara", "Kepler", 3, "KEP-G3-018")
        == "Mara Kepler · Generation 3 · KEP-G3-018"
    )
    assert naming.surname_prefix("Kepler") == "KEP"
    assert naming.surname_prefix("Yu") == "YUX"  # padded


# --- genomes ----------------------------------------------------------------


def test_genome_defaults_validate():
    for kind, doc in (("cognitive", default_cognitive()), ("trading", default_trading())):
        normalized, err = validate_genome(kind, doc)
        assert err is None
        assert normalized


def test_genome_versioning_and_diff(evo_session):
    agent = _mk_agent(evo_session)
    g1, err = write_genome_revision(evo_session, agent, "trading", default_trading())
    assert err is None and g1.revision == 1
    doc = default_trading()
    doc["thesis"] = "new thesis"
    g2, err = write_genome_revision(evo_session, agent, "trading", doc, hypothesis="h")
    assert err is None and g2.revision == 2
    assert g2.changed_fields_json == ["thesis"]
    assert agent.trading_genome_rev == 2
    # unchanged document is rejected
    g3, err = write_genome_revision(evo_session, agent, "trading", doc)
    assert g3 is None and "no fields changed" in err
    # invalid document is rejected
    bad = dict(doc)
    bad["universe"] = {"max_spread_cents": 500}
    g4, err = write_genome_revision(evo_session, agent, "trading", bad)
    assert g4 is None and err


def test_genome_rollback_preserves_history(evo_session):
    agent = _mk_agent(evo_session)
    write_genome_revision(evo_session, agent, "trading", default_trading())
    doc = default_trading()
    doc["thesis"] = "risky change"
    write_genome_revision(evo_session, agent, "trading", doc)
    rb, err = rollback_genome(evo_session, agent, "trading", 1, reason="failed")
    assert err is None
    assert rb.revision == 3 and rb.is_rollback and rb.rollback_of_revision == 1
    assert rb.document_json["thesis"] == default_trading()["thesis"]
    # all three revisions still exist
    revs = list(
        evo_session.scalars(
            select(em.EvoGenome).where(
                em.EvoGenome.agent_uuid == agent.agent_uuid, em.EvoGenome.kind == "trading"
            )
        )
    )
    assert len(revs) == 3


# --- memory -----------------------------------------------------------------


def test_fact_idempotent_and_immutable_kind(evo_session):
    agent = _mk_agent(evo_session)
    f1 = memory.record_fact(evo_session, agent.agent_uuid, "t", {"a": 1}, idem_key="k1")
    f2 = memory.record_fact(evo_session, agent.agent_uuid, "t", {"a": 2}, idem_key="k1")
    assert f1.id == f2.id and f1.immutable and f1.body_json == {"a": 1}


def test_belief_supersession_chain(evo_session):
    agent = _mk_agent(evo_session)
    b1, err = memory.revise_belief(
        evo_session, agent.agent_uuid, title="b", new_belief="v1",
        evidence_for="e1", evidence_against=None, confidence_after=0.5, heartbeat_id=None,
    )
    assert err is None
    b2, err = memory.revise_belief(
        evo_session, agent.agent_uuid, title="b", new_belief="v2",
        evidence_for="e2", evidence_against="c2", confidence_after=0.8,
        heartbeat_id=None, supersedes_id=b1.id,
    )
    assert err is None
    assert b1.superseded and b1.body_json["belief"] == "v1"  # content untouched
    assert b2.supersedes_id == b1.id
    assert b2.body_json["confidence_before"] == 0.5
    assert b2.body_json["confidence_after"] == 0.8
    # double supersession of the same row is rejected
    b3, err = memory.revise_belief(
        evo_session, agent.agent_uuid, title="b", new_belief="v3",
        evidence_for="e", evidence_against=None, confidence_after=0.9,
        heartbeat_id=None, supersedes_id=b1.id,
    )
    assert b3 is None and "already superseded" in err


def test_cross_agent_belief_revision_is_integrity_event(evo_session):
    a, b = _mk_agent(evo_session), _mk_agent(evo_session, surname="Voss")
    belief, _ = memory.revise_belief(
        evo_session, a.agent_uuid, title="b", new_belief="mine",
        evidence_for="e", evidence_against=None, confidence_after=0.5, heartbeat_id=None,
    )
    row, err = memory.revise_belief(
        evo_session, b.agent_uuid, title="b", new_belief="theirs",
        evidence_for="e", evidence_against=None, confidence_after=0.5,
        heartbeat_id=None, supersedes_id=belief.id,
    )
    assert row is None and "another agent" in err
    events = list(
        evo_session.scalars(
            select(em.EvoAuditEvent).where(em.EvoAuditEvent.severity == "integrity")
        )
    )
    assert any(e.agent_uuid == b.agent_uuid for e in events)


def test_experiment_conclusion_write_once(evo_session):
    agent = _mk_agent(evo_session)
    exp = memory.register_experiment(
        evo_session, agent.agent_uuid, hypothesis="H", idem_key="e1",
    )
    again = memory.register_experiment(
        evo_session, agent.agent_uuid, hypothesis="H-different", idem_key="e1",
    )
    assert again.id == exp.id and again.hypothesis == "H"  # hypothesis write-once
    row, err = memory.conclude_experiment(
        evo_session, agent.agent_uuid, exp.id,
        result={"pnl": 1}, confidence=0.7, interpretation="works",
    )
    assert err is None and row.status == "concluded"
    row2, err = memory.conclude_experiment(
        evo_session, agent.agent_uuid, exp.id,
        result={"pnl": 99}, confidence=0.9, interpretation="rewrite!",
    )
    assert row2 is None and "already" in err
    assert exp.result_json == {"pnl": 1}


def test_memory_retrieval_bounded_and_tag_boosted(evo_session):
    agent = _mk_agent(evo_session)
    for i in range(30):
        memory.record_episode(
            evo_session, agent.agent_uuid, f"ep{i}", {"i": i},
            tag1="KXBTC" if i % 2 else None, importance=0.3,
        )
    rows = memory.retrieve(
        evo_session, agent.agent_uuid, kinds=("episode",), tags=("KXBTC",), limit=5
    )
    assert len(rows) == 5
    assert all(r.tag1 == "KXBTC" for r in rows)


# --- budgets ----------------------------------------------------------------


def test_budget_caps_and_force(evo_session, evo_settings):
    au = str(uuidlib.uuid4())
    cohort = ensure_current_cohort(evo_session, evo_settings)
    budgets.ensure_budgets(evo_session, au, cohort.id, evo_settings)
    assert budgets.remaining(evo_session, au, cohort.id, "llm_cost_usd") == 2.0
    assert budgets.spend(evo_session, au, cohort.id, "llm_cost_usd", 1.5)
    assert not budgets.spend(evo_session, au, cohort.id, "llm_cost_usd", 1.0)
    assert budgets.spend(evo_session, au, cohort.id, "llm_cost_usd", 1.0, force=True)
    assert budgets.remaining(evo_session, au, cohort.id, "llm_cost_usd") == 0.0


def test_material_revision_cap_and_cooldown(evo_session, evo_settings):
    from datetime import timedelta

    au = str(uuidlib.uuid4())
    cohort = ensure_current_cohort(evo_session, evo_settings)
    # fixed times inside ONE UTC day so the daily allowance never resets mid-test
    t0 = datetime.now(timezone.utc).replace(hour=1, minute=0, second=0, microsecond=0)
    ok, err = budgets.try_use_material_revision(
        evo_session, au, cohort.id, evo_settings, now=t0
    )
    assert ok
    ok, err = budgets.try_use_material_revision(
        evo_session, au, cohort.id, evo_settings, now=t0 + timedelta(minutes=5)
    )
    assert not ok and "cooldown" in err
    # after the cooldown, the second daily slot is available, then the cap hits
    t1 = t0 + timedelta(minutes=evo_settings.revision_cooldown_minutes + 1)
    ok, err = budgets.try_use_material_revision(
        evo_session, au, cohort.id, evo_settings, now=t1
    )
    assert ok
    t2 = t1 + timedelta(minutes=evo_settings.revision_cooldown_minutes + 1)
    ok, err = budgets.try_use_material_revision(
        evo_session, au, cohort.id, evo_settings, now=t2
    )
    assert not ok and "exhausted" in err


# --- cohorts ----------------------------------------------------------------


def test_cohort_boundary_is_monday_midnight_chicago(evo_settings):
    ts = datetime(2026, 7, 16, 15, 0, tzinfo=timezone.utc)  # a Thursday
    boundary = cohort_boundary_before(ts, evo_settings)
    local = boundary.astimezone(ZoneInfo("America/Chicago"))
    assert local.weekday() == 0 and local.hour == 0 and local.minute == 0
    assert boundary <= ts


def test_ensure_current_cohort_idempotent_and_wildcard_flag(evo_session, evo_settings):
    c1 = ensure_current_cohort(evo_session, evo_settings)
    c2 = ensure_current_cohort(evo_session, evo_settings)
    assert c1.id == c2.id and c1.number == 1
    assert not c1.wildcard_cohort  # 1 % 4 != 0


def test_join_cohort_creates_equal_starting_state(evo_session, evo_settings):
    cohort = ensure_current_cohort(evo_session, evo_settings)
    agent = _mk_agent(evo_session)
    member = join_cohort(evo_session, agent, cohort, evo_settings)
    assert float(member.starting_capital) == 1000.0
    pf = evo_session.scalar(
        select(em.EvoPortfolio).where(
            em.EvoPortfolio.agent_uuid == agent.agent_uuid,
            em.EvoPortfolio.ledger == f"cohort:{cohort.id}",
        )
    )
    assert float(pf.cash_usd) == 1000.0
    # idempotent
    member2 = join_cohort(evo_session, agent, cohort, evo_settings)
    assert member2.id == member.id


# --- lineage ----------------------------------------------------------------


def test_influence_edges_and_tree(evo_session):
    a = _mk_agent(evo_session, surname="Kepler")
    b = _mk_agent(evo_session, surname="Voss")
    child = _mk_agent(evo_session, surname="Kepler", first="Ilya", generation=2, parent=a)
    row, err = lineage.record_influence(
        evo_session, source_uuid=b.agent_uuid, receiving_uuid=a.agent_uuid,
        concept="copied_listener", result="adopted",
    )
    assert err is None and row.source_family == "Voss"
    bad, err = lineage.record_influence(
        evo_session, source_uuid=a.agent_uuid, receiving_uuid=a.agent_uuid,
        concept="copied_listener",
    )
    assert bad is None and "self-influence" in err
    bad, err = lineage.record_influence(
        evo_session, source_uuid=b.agent_uuid, receiving_uuid=a.agent_uuid,
        concept="not_a_concept",
    )
    assert bad is None
    graph = lineage.influence_graph(evo_session)
    assert graph["count"] == 1 and graph["cross_family"] == 1
    tree = lineage.family_tree(evo_session)
    kepler_root = next(r for r in tree["roots"] if r["uuid"] == a.agent_uuid)
    assert [c["uuid"] for c in kepler_root["children"]] == [child.agent_uuid]
    # influence did NOT change surname or parentage
    assert a.surname == "Kepler" and child.parent_uuid == a.agent_uuid


# --- constitution -----------------------------------------------------------


def test_config_versioning(evo_session, evo_settings):
    v1 = constitution.ensure_config_version(evo_session, evo_settings)
    v2 = constitution.ensure_config_version(evo_session, evo_settings)
    assert v1.id == v2.id
    changed = EvoSettings(_env_file=None, fitness_weight_profit=34.0)
    v3 = constitution.ensure_config_version(evo_session, changed)
    assert v3.id != v1.id
    assert len(constitution.constitution_sha256()) == 64


# --- tickets ----------------------------------------------------------------


def test_ticket_dedup_and_support(evo_session):
    a = _mk_agent(evo_session)
    b = _mk_agent(evo_session, surname="Voss")
    t1, err, dedup = tickets.submit_ticket(
        evo_session, agent_uuid=a.agent_uuid, category="paid_data_source",
        capability="access to the Polygon options feed for crypto vol data",
    )
    assert err is None and not dedup
    t2, err, dedup = tickets.submit_ticket(
        evo_session, agent_uuid=b.agent_uuid, category="paid_data_source",
        capability="Polygon options feed access for crypto vol data",
    )
    assert dedup and t2.id == t1.id
    supporters = list(
        evo_session.scalars(
            select(em.EvoTicketSupporter).where(em.EvoTicketSupporter.ticket_id == t1.id)
        )
    )
    assert {s.agent_uuid for s in supporters} == {b.agent_uuid}
    t3, err, dedup = tickets.submit_ticket(
        evo_session, agent_uuid=b.agent_uuid, category="dashboard",
        capability="a per-family drawdown chart in the digest output",
    )
    assert not dedup and t3.id != t1.id
    bad, err, _ = tickets.submit_ticket(
        evo_session, agent_uuid=a.agent_uuid, category="nope", capability="long enough text",
    )
    assert bad is None and "unknown category" in err
    queue = tickets.review_queue(evo_session)
    assert queue[0]["id"] == t1.id and queue[0]["supporters"] == 1
    assert queue[0]["supporting_families"] == 1


# --- data sources -----------------------------------------------------------


def test_data_source_registry_rules(evo_session):
    assert datasources.seed_builtin_sources(evo_session) == 5
    assert datasources.seed_builtin_sources(evo_session) == 0
    a = _mk_agent(evo_session)
    row, err = datasources.register_source(
        evo_session, agent_uuid=a.agent_uuid, name="NOAA tides", cost_note="free",
    )
    assert err is None and row.approved_usage == "exploratory"
    bad, err = datasources.register_source(
        evo_session, agent_uuid=a.agent_uuid, name="bloomberg", cost_note="$2000/mo",
    )
    assert bad is None and "ticket" in err
    bad, err = datasources.register_source(
        evo_session, agent_uuid=a.agent_uuid, name="secret api", auth_required=True,
    )
    assert bad is None and "ticket" in err
    datasources.record_health_event(
        evo_session, "noaa_tides", level="error", kind="outage"
    )
    src = evo_session.scalar(
        select(em.EvoDataSource).where(em.EvoDataSource.name == "noaa_tides")
    )
    assert src.status == "degraded"
    assert datasources.resolve_health_events(evo_session, "noaa_tides") == 1
    assert src.status == "ok"


# --- graveyard --------------------------------------------------------------


def test_graveyard_seed_and_search(evo_session):
    n = graveyard_seed.seed_graveyard(evo_session)
    assert n == len(graveyard_seed.SEED_ENTRIES)
    assert graveyard_seed.seed_graveyard(evo_session) == 0
    hits = graveyard.search(evo_session, query="lead-lag")
    assert any("xgame" in h.slug for h in hits)
    conflicts = graveyard.conflicts_for_strategy(
        evo_session, strategy_family="favorite_buy", keywords=["favorite"]
    )
    assert any(c.slug == "tfav-favorite-buy" for c in conflicts)
