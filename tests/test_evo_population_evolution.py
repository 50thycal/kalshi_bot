"""The generation loop end to end: decisions, reproduction, retirement, findings."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from kalshi_bot.evo.population import evolution, findings, knowledge, proving, service
from kalshi_bot.evo.population import genome as g
from kalshi_bot.evo.population.models import (
    EvoCandidate,
    EvoGenomeVersion,
    EvoJournalEntry,
)


@pytest.fixture
def corpus():
    return proving.register()


@pytest.fixture
def program(evo_session, corpus):
    return service.create_program(
        evo_session,
        key="evo-test",
        name="generation loop tests",
        objective="exercise the loop",
        dataset=corpus,
        cohort_target=8,
        starting_capital_usd=500.0,
        min_trades_for_evidence=20,
        rng_seed=7,
    )


def _founders(n=8):
    """Eight mutually distinct founders, two per synthetic series.

    Distinctness is not cosmetic here: the program enforces a novelty floor on founders
    too, so a generator that cycles its options faster than it varies them is refused —
    which is the check doing its job, not a fixture inconvenience."""
    series = ["KXSYNTHA", "KXSYNTHB", "KXSYNTHC", "KXSYNTHD"]
    bands = [(10, 90), (20, 70), (30, 80), (15, 60), (25, 85), (40, 95), (12, 50), (35, 65)]
    exits = [
        {"mode": "settlement"},
        {"mode": "tp_sl", "take_profit_cents": 85, "stop_loss_cents": 20},
        {"mode": "timed", "max_hold_hours": 10.0},
        {"mode": "confirmed_stop", "stop_mid_cents": 25, "confirm_ticks": 4},
    ]
    docs = []
    for i in range(n):
        lo, hi = bands[i % len(bands)]
        docs.append(
            g.spec_document(
                name=f"founder-{i:02d}",
                family=f"fam{i % 4}",
                universe={
                    "series_prefixes": [series[i % len(series)]],
                    "max_spread_cents": 8 + i,
                    "max_hours_to_close": 48,
                    "min_volume": 100.0 * i,
                },
                entry={
                    "side": "yes" if i % 2 == 0 else "no",
                    "min_price_cents": lo,
                    "max_price_cents": hi,
                    "size_contracts": 3 + i,
                },
                exit_=dict(exits[(i // 2) % len(exits)]),
            )
        )
    return docs


@pytest.fixture
def seeded(evo_session, program):
    service.seed_founders(evo_session, program=program, documents=_founders())
    return program


def _advance(session, settings, program, gen=0, span=40):
    start, end = proving.window(gen * span, (gen + 1) * span)
    return service.advance(
        session, settings, program=program,
        window_start=start, window_end=end, data_cutoff=end, number=gen,
    )


# ---------------------------------------------------------------------------
# Program configuration
# ---------------------------------------------------------------------------


def test_paper_and_shadow_modes_are_refused(evo_session, corpus):
    for mode in ("paper", "shadow"):
        with pytest.raises(service.EvoPopulationError, match="reserved"):
            service.create_program(
                evo_session, key=f"p-{mode}", name=mode, objective="x",
                dataset=corpus, mode=mode,
            )


def test_impossible_fractions_are_refused(evo_session, corpus):
    with pytest.raises(service.EvoPopulationError, match="exceeds the whole population"):
        service.create_program(
            evo_session, key="bad-fractions", name="x", objective="x", dataset=corpus,
            reproduce_fraction=0.7, retire_fraction=0.7,
        )


def test_an_unknown_gene_in_the_surface_is_refused(evo_session, corpus):
    with pytest.raises(service.EvoPopulationError, match="unknown genes"):
        service.create_program(
            evo_session, key="bad-surface", name="x", objective="x", dataset=corpus,
            allowed_mutation_surface=["entry.not_a_gene"],
        )


def test_duplicate_program_keys_are_refused(evo_session, program, corpus):
    with pytest.raises(service.EvoPopulationError, match="already exists"):
        service.create_program(
            evo_session, key=program.key, name="x", objective="x", dataset=corpus
        )


def test_advancing_without_founders_is_refused(evo_session, evo_settings, program):
    with pytest.raises(service.EvoPopulationError, match="no active candidates"):
        _advance(evo_session, evo_settings, program)


def test_near_duplicate_founders_are_refused(evo_session, program):
    doc = _founders(1)[0]
    with pytest.raises(service.EvoPopulationError, match="founder #1 refused"):
        service.seed_founders(evo_session, program=program, documents=[doc, dict(doc)])


# ---------------------------------------------------------------------------
# One generation
# ---------------------------------------------------------------------------


def test_every_candidate_gets_its_own_run_and_ledger(evo_session, evo_settings, seeded):
    result = _advance(evo_session, evo_settings, seeded)
    assert len(result.runs) == 8
    assert len({r.candidate_uuid for r in result.runs}) == 8


def test_every_candidate_gets_exactly_one_decision(evo_session, evo_settings, seeded):
    result = _advance(evo_session, evo_settings, seeded)
    per_candidate = {}
    for d in result.decisions:
        per_candidate[d.candidate_uuid] = per_candidate.get(d.candidate_uuid, 0) + 1
    assert per_candidate and set(per_candidate.values()) == {1}


def test_every_decision_records_evidence_and_thresholds(evo_session, evo_settings, seeded):
    result = _advance(evo_session, evo_settings, seeded)
    for d in result.decisions:
        assert d.reason
        assert d.evaluator_revision
        assert d.thresholds_json and "retire_fraction" in d.thresholds_json
        assert d.evidence_json is not None


def test_running_a_genome_freezes_it(evo_session, evo_settings, seeded):
    _advance(evo_session, evo_settings, seeded)
    genomes = list(
        evo_session.execute(
            select(EvoGenomeVersion).where(EvoGenomeVersion.program_id == seeded.id)
        ).scalars()
    )
    assert genomes and all(x.evaluated for x in genomes if x.born_generation == 0)


def test_a_thin_sample_is_held_not_retired(evo_session, evo_settings, seeded):
    result = _advance(evo_session, evo_settings, seeded)
    held = [d for d in result.decisions if d.decision == evolution.DECISION_HOLD]
    for d in held:
        candidate = evo_session.execute(
            select(EvoCandidate).where(EvoCandidate.uuid == d.candidate_uuid)
        ).scalars().one()
        assert candidate.state == "active"
        assert "thin evidence" in d.reason


def test_broken_data_escalates_rather_than_retiring(evo_session, evo_settings, seeded):
    result = _advance(evo_session, evo_settings, seeded)
    escalated = [d for d in result.decisions if d.decision == evolution.DECISION_ESCALATE]
    assert escalated, "the KXSYNTHD founders carry a corrupt corpus"
    for d in escalated:
        candidate = evo_session.execute(
            select(EvoCandidate).where(EvoCandidate.uuid == d.candidate_uuid)
        ).scalars().one()
        assert candidate.state == "active"


# ---------------------------------------------------------------------------
# Reproduction and retirement
# ---------------------------------------------------------------------------


def test_reproduction_leaves_the_parent_intact(evo_session, evo_settings, seeded):
    result = _advance(evo_session, evo_settings, seeded)
    reproduced = [d for d in result.decisions if d.decision == evolution.DECISION_REPRODUCE]
    assert reproduced
    for d in reproduced:
        parent = evo_session.execute(
            select(EvoCandidate).where(EvoCandidate.uuid == d.candidate_uuid)
        ).scalars().one()
        child = evo_session.execute(
            select(EvoCandidate).where(EvoCandidate.uuid == d.child_candidate_uuid)
        ).scalars().one()
        assert parent.state == "active"
        assert parent.current_genome_id != child.current_genome_id
        assert child.parent_uuid == parent.uuid
        assert child.generation_depth == parent.generation_depth + 1


def test_a_child_records_exactly_what_changed(evo_session, evo_settings, seeded):
    result = _advance(evo_session, evo_settings, seeded)
    assert result.children
    for child in result.children:
        genome = evo_session.get(EvoGenomeVersion, child.current_genome_id)
        parent_genome = evo_session.get(EvoGenomeVersion, genome.parent_genome_id)
        assert genome.mutation_diff_json
        assert genome.proposal_id is not None
        assert genome.hypothesis
        recorded = {c["path"] for c in genome.mutation_diff_json}
        actual = {c.path for c in g.diff(parent_genome.document_json, genome.document_json)}
        assert recorded == actual


def test_retirement_stops_a_candidate_being_replayed_again(
    evo_session, evo_settings, seeded
):
    first = _advance(evo_session, evo_settings, seeded, gen=0)
    retired = {
        d.candidate_uuid for d in first.decisions if d.decision == evolution.DECISION_RETIRE
    }
    assert retired
    second = _advance(evo_session, evo_settings, seeded, gen=1)
    assert not (retired & {r.candidate_uuid for r in second.runs})


def test_a_retirement_always_carries_a_reason(evo_session, evo_settings, seeded):
    _advance(evo_session, evo_settings, seeded)
    for candidate in evo_session.execute(
        select(EvoCandidate).where(
            EvoCandidate.program_id == seeded.id, EvoCandidate.state == "retired"
        )
    ).scalars():
        assert candidate.retirement_reason
        assert candidate.retired_generation is not None


def test_child_budget_is_respected(evo_session, evo_settings, seeded):
    start, end = proving.window(0, 40)
    result = service.advance(
        evo_session, evo_settings, program=seeded,
        window_start=start, window_end=end, data_cutoff=end, number=0,
        max_children=1,
    )
    assert len(result.children) <= 1
    spent = [d for d in result.decisions if "child budget" in d.reason]
    assert not result.children or spent or len(result.children) == 1


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


def test_the_journal_separates_observation_from_interpretation(
    evo_session, evo_settings, seeded
):
    _advance(evo_session, evo_settings, seeded)
    kinds = {
        row.kind
        for row in evo_session.execute(
            select(EvoJournalEntry).where(EvoJournalEntry.program_id == seeded.id)
        ).scalars()
    }
    assert "observation" in kinds
    assert "interpretation" in kinds


def test_a_child_inherits_only_heritable_lessons(evo_session, evo_settings, seeded):
    first = _advance(evo_session, evo_settings, seeded, gen=0)
    parent = next(
        (
            evo_session.execute(
                select(EvoCandidate).where(EvoCandidate.uuid == d.candidate_uuid)
            ).scalars().one()
            for d in first.decisions
            if d.decision == evolution.DECISION_REPRODUCE
        ),
        None,
    )
    assert parent is not None
    evo_session.add(
        EvoJournalEntry(
            program_id=seeded.id, candidate_uuid=parent.uuid, generation_number=0,
            kind="lesson", topic="exits", body="confirmation mattered", heritable=True,
        )
    )
    evo_session.add(
        EvoJournalEntry(
            program_id=seeded.id, candidate_uuid=parent.uuid, generation_number=0,
            kind="interpretation", topic="regime", body="longshots are toxic",
            heritable=False,
        )
    )
    evo_session.flush()

    second = _advance(evo_session, evo_settings, seeded, gen=1)
    children = [c for c in second.children if c.parent_uuid == parent.uuid]
    if not children:
        pytest.skip("parent did not reproduce in generation 1")
    inherited = list(
        evo_session.execute(
            select(EvoJournalEntry).where(
                EvoJournalEntry.candidate_uuid == children[0].uuid,
                EvoJournalEntry.inherited_from == parent.uuid,
            )
        ).scalars()
    )
    assert [row.body for row in inherited] == ["confirmation mattered"], (
        "a parent's lesson crosses the boundary; its stale conclusion does not"
    )


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


def test_invalid_candidates_raise_a_critical_finding(evo_session, evo_settings, seeded):
    result = _advance(evo_session, evo_settings, seeded)
    kinds = {f.kind for f in result.findings}
    assert findings.KIND_INVALID_GENOME in kinds
    critical = [f for f in result.findings if f.severity == "critical"]
    assert critical and all(f.status == "open" for f in critical)


def test_findings_deduplicate_across_generations(evo_session, evo_settings, seeded):
    _advance(evo_session, evo_settings, seeded, gen=0)
    _advance(evo_session, evo_settings, seeded, gen=1)
    rows = findings.open_findings(evo_session, program_id=seeded.id)
    keys = [f.dedup_key for f in rows]
    assert len(keys) == len(set(keys))


def test_a_finding_cannot_be_closed_without_a_resolution(evo_session, evo_settings, seeded):
    result = _advance(evo_session, evo_settings, seeded)
    row = result.findings[0]
    with pytest.raises(ValueError, match="concrete resolution"):
        findings.resolve(evo_session, finding_id=row.id, resolution="   ")
    findings.resolve(evo_session, finding_id=row.id, resolution="fixed the corpus")
    assert row.status == "resolved"


def test_routing_a_finding_records_the_owning_role(evo_session, evo_settings, seeded):
    result = _advance(evo_session, evo_settings, seeded)
    row = result.findings[0]
    findings.route(
        evo_session, finding_id=row.id, route_to=findings.ROUTE_RESEARCH_LAB,
        note="needs a real probe",
    )
    assert row.route_to == findings.ROUTE_RESEARCH_LAB and row.status == "routed"


# ---------------------------------------------------------------------------
# Knowledge retrieval
# ---------------------------------------------------------------------------


def test_retrieval_is_scoped_to_the_candidate(evo_session, evo_settings, seeded):
    result = _advance(evo_session, evo_settings, seeded)
    child = result.children[0] if result.children else None
    if child is None:
        pytest.skip("no child produced")
    genome = evo_session.get(EvoGenomeVersion, child.current_genome_id)
    context = knowledge.context_for(
        evo_session, program_id=seeded.id, candidate=child,
        document=genome.document_json, limit=4,
    )
    assert set(context) == {
        "lineage", "similar_genomes", "own_results", "refused_mutations",
        "failure_modes", "lessons",
    }
    assert all(len(v) <= 4 for v in context.values())
    assert context["lineage"], "a child must be able to see its parent"


def test_similar_genomes_are_bounded_by_distance(evo_session, evo_settings, seeded):
    _advance(evo_session, evo_settings, seeded)
    doc = _founders(1)[0]
    norm, _ = g.normalize(doc)
    near = knowledge.similar_genomes(
        evo_session, program_id=seeded.id, document=norm, max_distance=0.01
    )
    far = knowledge.similar_genomes(
        evo_session, program_id=seeded.id, document=norm, max_distance=0.9
    )
    assert len(near) <= len(far)
    assert all(row["distance"] <= 0.9 for row in far)
