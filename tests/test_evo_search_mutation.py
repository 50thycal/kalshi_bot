"""The PROPOSE / ACCEPT boundary, the admission gates, and diversity controls."""

from __future__ import annotations

from kalshi_bot.evo.search import diversity, mutation
from kalshi_bot.evo.search import genome as g


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


def _evaluate(parent, proposal, existing=None, **kw):
    return mutation.evaluate_proposal_document(
        proposal,
        parent_document=parent,
        existing_documents=existing if existing is not None else [parent],
        allowed_paths=list(g.MUTABLE_PATHS),
        **kw,
    )


# ---------------------------------------------------------------------------
# Proposing
# ---------------------------------------------------------------------------


def test_perturbation_is_deterministic_for_the_same_seed():
    parent = _doc()
    kwargs = dict(
        agent_uuid="p", base_revision=1, document=parent,
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
            agent_uuid="p", base_revision=1, document=parent,
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
            agent_uuid="p", base_revision=1, document=parent,
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
            agent_uuid="p", base_revision=1, document=parent,
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
        agent_uuid="p", base_revision=1, document=parent,
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
            agent_uuid="p", base_revision=1, document=parent,
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
        agent_uuid="p", base_revision=1, document=parent,
        path="entry.not_a_gene", value=1,
    ) is None
    assert mutation.propose_sweep(
        agent_uuid="p", base_revision=1, document=parent,
        path="entry.size_contracts", value=5,
    ) is None


# ---------------------------------------------------------------------------
# The admission gates
# ---------------------------------------------------------------------------


def test_a_proposal_outside_the_allowed_surface_is_refused():
    parent = _doc()
    proposal = mutation.propose_sweep(
        agent_uuid="p", base_revision=1, document=parent,
        path="entry.size_contracts", value=9,
    )
    admission = mutation.evaluate_proposal_document(
        proposal, parent_document=parent,
        existing_documents=[parent], allowed_paths=["entry.side"],
    )
    assert not admission.ok and admission.stage == "schema"


def test_an_incoherent_child_is_refused():
    parent = _doc()
    proposal = mutation.propose_sweep(
        agent_uuid="p", base_revision=1, document=parent,
        path="entry.min_price_cents", value=95,  # above the 70c ceiling
    )
    admission = _evaluate(parent, proposal)
    assert not admission.ok and "inverted" in admission.reason


def test_a_child_breaching_the_risk_envelope_is_refused():
    parent = _doc()
    proposal = mutation.propose_sweep(
        agent_uuid="p", base_revision=1, document=parent,
        path="entry.size_contracts", value=500,
    )
    admission = _evaluate(parent, proposal)
    assert not admission.ok
    # The genome's own per-position cost cap catches it before the program envelope does.
    assert admission.stage == "compatibility"
    assert "max_cost_per_position_usd" in admission.reason


def test_worst_case_exposure_above_capital_is_refused():
    parent = _doc(
        entry={
            "side": "yes", "min_price_cents": 20, "max_price_cents": 70,
            "size_contracts": 20,
        },
        risk={"max_concurrent_positions": 100, "max_cost_per_position_usd": 900.0},
    )
    proposal = mutation.propose_sweep(
        agent_uuid="p", base_revision=1, document=parent,
        path="entry.max_price_cents", value=90,
    )
    admission = _evaluate(parent, proposal)
    assert not admission.ok and admission.stage == "risk"
    assert "the search scores against" in admission.reason


def test_an_exact_duplicate_is_refused():
    parent = _doc()
    twin = g.set_path(parent, "entry.size_contracts", 6)
    proposal = mutation.propose_sweep(
        agent_uuid="p", base_revision=1, document=parent,
        path="entry.size_contracts", value=6,
    )
    admission = _evaluate(parent, proposal, existing=[parent, twin])
    assert not admission.ok and admission.stage == "novelty"
    assert "identical" in admission.reason


def test_a_near_duplicate_is_refused_below_the_novelty_floor():
    parent = _doc()
    proposal = mutation.propose_sweep(
        agent_uuid="p", base_revision=1, document=parent,
        path="entry.size_contracts", value=6,
    )
    # Nothing short of a redesign is novel enough at this floor.
    admission = _evaluate(parent, proposal, min_distance=0.5)
    assert not admission.ok and admission.stage == "novelty"
    assert "below the novelty floor" in admission.reason


def test_a_proposal_that_normalizes_to_the_parent_is_refused():
    """Changing an inapplicable gene changes the document but not the strategy."""
    parent = _doc(exit_={"mode": "settlement"})
    proposal = mutation.MutationProposal(
        agent_uuid="p", base_revision=1,
        source=mutation.SOURCE_SWEEP, kind=mutation.KIND_EXPLOIT,
        changes=[{"path": "exit.take_profit_cents", "label": "tp", "from": None, "to": 70}],
    )
    admission = _evaluate(parent, proposal)
    assert not admission.ok and admission.stage == "compatibility"
    assert "nothing on the gene surface" in admission.reason


def test_a_good_proposal_is_admitted():
    parent = _doc()
    proposal = mutation.propose_sweep(
        agent_uuid="p", base_revision=1, document=parent,
        path="entry.max_price_cents", value=85,
    )
    admission = _evaluate(parent, proposal)
    assert admission.ok and admission.document is not None
    assert admission.genome_hash == g.genome_hash(admission.document)


# ---------------------------------------------------------------------------
# There is no writer to bypass
# ---------------------------------------------------------------------------


def test_this_module_cannot_write_a_genome():
    """The gate is closed structurally rather than by trusting a passed-in verdict.

    An earlier draft had `admit_proposal(session, ..., admission)` check the `ok` flag of
    an `Admission` it was handed. `Admission` is a plain dataclass, so any caller could
    build `Admission(ok=True, document=...)` and walk past every gate. The answer is not
    a more careful writer: the search capability writes no genome at all. It measures
    variants and returns evidence; the *agent* revises its own trading genome through the
    organism's own action path, under the organism's budgets and audit.

    So the property to hold is an absence, and it is checked as one."""
    import inspect

    source = inspect.getsource(mutation)
    for forbidden in ("admit_proposal", "EvoGenome", "session.add", "session.commit"):
        assert forbidden not in source, f"{forbidden} is back in mutation.py"
    assert not hasattr(mutation, "admit_proposal")
    # Every public entry point is pure: it takes documents and returns a verdict.
    for name in mutation.__all__:
        obj = getattr(mutation, name)
        if inspect.isfunction(obj):
            assert "session" not in inspect.signature(obj).parameters, name


def test_a_refusal_carries_a_reason_the_proposer_can_act_on():
    """A rejection is evidence about the search space. Dropped silently, the same
    invalid mutation is reproposed forever."""
    parent = _doc()
    proposal = mutation.propose_sweep(
        agent_uuid="p", base_revision=1, document=parent,
        path="entry.min_price_cents", value=95,
    )
    admission = _evaluate(parent, proposal)
    assert not admission.ok
    assert admission.stage and admission.reason
    assert admission.document is None


# ---------------------------------------------------------------------------
# Diversity
# ---------------------------------------------------------------------------


def _member(doc, family="test", parent=None):
    return {"document": doc, "family": family, "parent_uuid": parent,
            "hash": g.genome_hash(doc)}


def test_a_homogeneous_neighbourhood_is_flagged():
    base = _doc()
    members = [
        _member(g.set_path(base, "entry.size_contracts", 5 + (i % 2)))
        for i in range(10)
    ]
    report = diversity.measure(members)
    assert report.mean_pairwise_distance < diversity.COLLAPSE_MEAN_DISTANCE
    assert any("diversity collapsing" in w for w in report.warnings)


def test_a_varied_neighbourhood_is_not_flagged():
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


def test_duplicate_hashes_in_the_measured_set_are_flagged():
    base = _doc()
    members = [_member(base) for _ in range(4)]
    report = diversity.measure(members)
    assert report.distinct_genomes == 1
    assert any("duplicates are in the measured set" in w for w in report.warnings)


def test_novelty_check_accepts_the_first_genome():
    ok, dist, reason = diversity.novelty_check(_doc(), [], min_distance=0.02)
    assert ok and reason is None and dist == 1.0


def test_an_empty_set_measures_cleanly():
    report = diversity.measure([])
    assert report.n == 0 and report.warnings == []
