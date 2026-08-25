"""The PROPOSE / ACCEPT boundary, the admission gates, and diversity controls."""

from __future__ import annotations

import pytest

from kalshi_bot.evo.population import diversity, mutation, service
from kalshi_bot.evo.population import genome as g


@pytest.fixture
def program(evo_session):
    return service.create_program(
        evo_session,
        key="mut-test",
        name="mutation gate tests",
        objective="exercise the admission gates",
        dataset="synthetic:proving",
        starting_capital_usd=500.0,
        min_genome_distance=0.02,
    )


def _doc(**kw):
    base = dict(
        name="parent-alpha",
        family="test",
        universe={"series_prefixes": ["KXAAA"], "max_hours_to_close": 48},
        entry={
            "side": "yes",
            "min_price_cents": 20,
            "max_price_cents": 70,
            "size_contracts": 5,
        },
        exit_={"mode": "settlement"},
        risk={"max_concurrent_positions": 10, "max_cost_per_position_usd": 50.0},
    )
    base.update(kw)
    doc, err = g.normalize(g.spec_document(**base))
    assert err is None
    return doc


def _evaluate(program, parent, proposal, existing=None):
    return mutation.evaluate_proposal(
        proposal,
        parent_document=parent,
        program=program,
        existing_documents=existing if existing is not None else [parent],
        allowed_paths=list(g.MUTABLE_PATHS),
    )


# ---------------------------------------------------------------------------
# Proposing
# ---------------------------------------------------------------------------


def test_perturbation_is_deterministic_for_the_same_seed():
    parent = _doc()
    kwargs = dict(
        parent_candidate_uuid="p", parent_genome_id=1, document=parent,
        allowed_paths=list(g.MUTABLE_PATHS), seed=42, index=0,
    )
    a = mutation.propose_perturbation(**kwargs)
    b = mutation.propose_perturbation(**kwargs)
    assert a is not None and a.changes == b.changes


def test_different_attempts_propose_different_changes():
    parent = _doc()
    seen = set()
    for index in range(6):
        p = mutation.propose_perturbation(
            parent_candidate_uuid="p", parent_genome_id=1, document=parent,
            allowed_paths=list(g.MUTABLE_PATHS), seed=1, index=index,
        )
        if p:
            seen.add(tuple(sorted(c["path"] for c in p.changes)))
    assert len(seen) > 1


def test_perturbation_never_touches_a_gene_outside_the_allowed_surface():
    parent = _doc()
    allowed = ["entry.size_contracts"]
    for index in range(10):
        p = mutation.propose_perturbation(
            parent_candidate_uuid="p", parent_genome_id=1, document=parent,
            allowed_paths=allowed, seed=7, index=index,
        )
        if p:
            assert {c["path"] for c in p.changes} <= set(allowed)


def test_perturbation_skips_genes_that_do_not_apply():
    """Stepping take_profit_cents on a settlement-exit genome would record a mutation
    that changes nothing."""
    parent = _doc(exit_={"mode": "settlement"})
    for index in range(20):
        p = mutation.propose_perturbation(
            parent_candidate_uuid="p", parent_genome_id=1, document=parent,
            allowed_paths=["exit.take_profit_cents", "entry.size_contracts"],
            seed=3, index=index,
        )
        if p:
            assert all(c["path"] != "exit.take_profit_cents" for c in p.changes)


def test_an_exit_mode_switch_brings_its_thresholds():
    """A mode change alone leaves the new mode's threshold unset, so it either never
    fires or is refused — either way the exit axis would be unreachable."""
    parent = _doc(exit_={"mode": "settlement"})
    proposal = mutation.propose_sweep(
        parent_candidate_uuid="p", parent_genome_id=1, document=parent,
        path="exit.mode", value="tp_sl",
    )
    # propose_sweep is the explicit path and does not add companions, so this is the
    # case the compatibility gate must catch.
    assert proposal is not None
    doc = proposal.apply_to(parent)
    _, err = g.validate(doc)
    assert err is not None and "never fires" in err

    # The perturbation path does add them, so a mode change is actually reachable.
    found = None
    for index in range(40):
        p = mutation.propose_perturbation(
            parent_candidate_uuid="p", parent_genome_id=1, document=parent,
            allowed_paths=["exit.mode"], seed=11, index=index,
        )
        if p and any(c["path"] == "exit.mode" for c in p.changes):
            mode = next(c["to"] for c in p.changes if c["path"] == "exit.mode")
            if mode != "settlement":
                found = p
                break
    assert found is not None
    assert g.validate(found.apply_to(parent))[1] is None


def test_a_sweep_for_an_unknown_gene_or_a_no_op_returns_nothing():
    parent = _doc()
    assert mutation.propose_sweep(
        parent_candidate_uuid="p", parent_genome_id=1, document=parent,
        path="entry.not_a_gene", value=1,
    ) is None
    assert mutation.propose_sweep(
        parent_candidate_uuid="p", parent_genome_id=1, document=parent,
        path="entry.size_contracts", value=5,
    ) is None


# ---------------------------------------------------------------------------
# The admission gates
# ---------------------------------------------------------------------------


def test_a_proposal_outside_the_allowed_surface_is_refused(program):
    parent = _doc()
    proposal = mutation.propose_sweep(
        parent_candidate_uuid="p", parent_genome_id=1, document=parent,
        path="entry.size_contracts", value=9,
    )
    admission = mutation.evaluate_proposal(
        proposal, parent_document=parent, program=program,
        existing_documents=[parent], allowed_paths=["entry.side"],
    )
    assert not admission.ok and admission.stage == "schema"


def test_an_incoherent_child_is_refused(program):
    parent = _doc()
    proposal = mutation.propose_sweep(
        parent_candidate_uuid="p", parent_genome_id=1, document=parent,
        path="entry.min_price_cents", value=95,  # above the 70c ceiling
    )
    admission = _evaluate(program, parent, proposal)
    assert not admission.ok and "inverted" in admission.reason


def test_a_child_breaching_the_risk_envelope_is_refused(program):
    parent = _doc()
    proposal = mutation.propose_sweep(
        parent_candidate_uuid="p", parent_genome_id=1, document=parent,
        path="entry.size_contracts", value=500,
    )
    admission = _evaluate(program, parent, proposal)
    assert not admission.ok
    # The genome's own per-position cost cap catches it before the program envelope does.
    assert admission.stage == "compatibility"
    assert "max_cost_per_position_usd" in admission.reason


def test_worst_case_exposure_above_capital_is_refused(program):
    parent = _doc(
        entry={
            "side": "yes", "min_price_cents": 20, "max_price_cents": 70,
            "size_contracts": 20,
        },
        risk={"max_concurrent_positions": 100, "max_cost_per_position_usd": 900.0},
    )
    proposal = mutation.propose_sweep(
        parent_candidate_uuid="p", parent_genome_id=1, document=parent,
        path="entry.max_price_cents", value=90,
    )
    admission = _evaluate(program, parent, proposal)
    assert not admission.ok and admission.stage == "risk"
    assert "exceeds the program's" in admission.reason


def test_an_exact_duplicate_is_refused(program):
    parent = _doc()
    twin = g.set_path(parent, "entry.size_contracts", 6)
    proposal = mutation.propose_sweep(
        parent_candidate_uuid="p", parent_genome_id=1, document=parent,
        path="entry.size_contracts", value=6,
    )
    admission = _evaluate(program, parent, proposal, existing=[parent, twin])
    assert not admission.ok and admission.stage == "novelty"
    assert "identical" in admission.reason


def test_a_near_duplicate_is_refused_below_the_program_floor(program):
    parent = _doc()
    program.min_genome_distance = 0.5  # nothing short of a redesign is novel enough
    proposal = mutation.propose_sweep(
        parent_candidate_uuid="p", parent_genome_id=1, document=parent,
        path="entry.size_contracts", value=6,
    )
    admission = _evaluate(program, parent, proposal)
    assert not admission.ok and admission.stage == "novelty"
    assert "below the program floor" in admission.reason


def test_a_proposal_that_normalizes_to_the_parent_is_refused(program):
    """Changing an inapplicable gene changes the document but not the strategy."""
    parent = _doc(exit_={"mode": "settlement"})
    proposal = mutation.MutationProposal(
        parent_candidate_uuid="p", parent_genome_id=1,
        source=mutation.SOURCE_SWEEP, kind=mutation.KIND_EXPLOIT,
        changes=[{"path": "exit.take_profit_cents", "label": "tp", "from": None, "to": 70}],
    )
    admission = _evaluate(program, parent, proposal)
    assert not admission.ok and admission.stage == "compatibility"
    assert "nothing on the gene surface" in admission.reason


def test_a_good_proposal_is_admitted(program):
    parent = _doc()
    proposal = mutation.propose_sweep(
        parent_candidate_uuid="p", parent_genome_id=1, document=parent,
        path="entry.max_price_cents", value=85,
    )
    admission = _evaluate(program, parent, proposal)
    assert admission.ok and admission.document is not None
    assert admission.genome_hash == g.genome_hash(admission.document)


# ---------------------------------------------------------------------------
# ACCEPT is closed to anything that did not pass
# ---------------------------------------------------------------------------


def test_admit_refuses_an_admission_that_did_not_pass(evo_session, program):
    """A caller cannot fabricate an Admission and skip the gates: the document comes
    from the admission, which only evaluate_proposal produces."""
    parent = _doc()
    proposal = mutation.propose_sweep(
        parent_candidate_uuid="p", parent_genome_id=1, document=parent,
        path="entry.max_price_cents", value=85,
    )
    bad = mutation.Admission(ok=False, stage="risk", reason="nope")
    row = mutation.record_proposal(
        evo_session, program=program, generation_number=0,
        proposal=proposal, admission=bad,
    )
    with pytest.raises(ValueError, match="did not pass"):
        mutation.admit_proposal(
            evo_session, program=program, generation_number=0,
            child_candidate_uuid="child", parent_genome=None, proposal=proposal,
            admission=bad, proposal_row=row, evidence_cutoff=None,
        )


def test_rejected_proposals_are_persisted_as_evidence(evo_session, program):
    parent = _doc()
    proposal = mutation.propose_sweep(
        parent_candidate_uuid="p", parent_genome_id=1, document=parent,
        path="entry.min_price_cents", value=95,
    )
    admission = _evaluate(program, parent, proposal)
    row = mutation.record_proposal(
        evo_session, program=program, generation_number=0,
        proposal=proposal, admission=admission,
    )
    assert row.status == "rejected" and row.reject_reason
    assert row.changes_json


# ---------------------------------------------------------------------------
# Diversity
# ---------------------------------------------------------------------------


def _member(doc, family="test", parent=None):
    return {"document": doc, "family": family, "parent_uuid": parent,
            "hash": g.genome_hash(doc)}


def test_a_homogeneous_cohort_is_flagged():
    base = _doc()
    members = [
        _member(g.set_path(base, "entry.size_contracts", 5 + (i % 2)))
        for i in range(10)
    ]
    report = diversity.measure(members)
    assert report.mean_pairwise_distance < diversity.COLLAPSE_MEAN_DISTANCE
    assert any("diversity collapsing" in w for w in report.warnings)


def test_a_varied_cohort_is_not_flagged():
    members = [
        _member(_doc(universe={"series_prefixes": [f"KX{i}"]}), family=f"fam{i}")
        for i in range(8)
    ]
    report = diversity.measure(members)
    assert not any("collapsing" in w for w in report.warnings)


def test_family_and_parent_concentration_are_flagged():
    base = _doc()
    members = [
        _member(g.set_path(base, "entry.max_price_cents", 40 + i * 5),
                family="one-family", parent="same-parent")
        for i in range(8)
    ]
    report = diversity.measure(members)
    assert report.top_family_share == 1.0
    assert any("family concentration" in w for w in report.warnings)
    assert any("parent concentration" in w for w in report.warnings)


def test_duplicate_hashes_in_the_population_are_flagged():
    base = _doc()
    members = [_member(base) for _ in range(4)]
    report = diversity.measure(members)
    assert report.distinct_genomes == 1
    assert any("duplicates are in the population" in w for w in report.warnings)


def test_novelty_check_accepts_the_first_genome():
    ok, dist, reason = diversity.novelty_check(_doc(), [], min_distance=0.02)
    assert ok and reason is None and dist == 1.0


def test_empty_population_measures_cleanly():
    report = diversity.measure([])
    assert report.n == 0 and report.warnings == []
