"""The historical proving run, the Control Tower, and the boundaries this layer keeps.

The proving run is expensive (30 candidates × 3 generations), so it runs once per
session and the assertions share it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from kalshi_bot.evo.population import control_tower, proving, proving_run, service
from kalshi_bot.evo.population.models import EvoCandidate, EvoFinding, EvoProgram
from kalshi_bot.models import Base


@pytest.fixture(scope="module")
def proven():
    """One full proving run, shared across this module's assertions."""
    import kalshi_bot.evo.population.models  # noqa: F401  (registers the tables)

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    result = proving_run.run_proving(session, generations=3, cohort=30)
    yield session, result
    session.close()


def _check(result, prefix):
    for key, ok, detail in result["checks"]:
        if key.startswith(prefix):
            return ok, detail
    raise AssertionError(f"no check starting {prefix!r} in {[c[0] for c in result['checks']]}")


# ---------------------------------------------------------------------------
# The ten mechanical goals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prefix",
    ["1 ", "2 ", "3 ", "4 ", "5 ", "6 ", "7 ", "8 ", "9 ", "10 ", "11 "],
)
def test_each_proving_goal_passes(proven, prefix):
    _session, result = proven
    ok, detail = _check(result, prefix)
    assert ok, f"proving goal {prefix.strip()} failed: {detail}"


@pytest.mark.parametrize("prefix", ["A1", "A2", "A3"])
def test_each_adversarial_case_passes(proven, prefix):
    _session, result = proven
    ok, detail = _check(result, prefix)
    assert ok, f"adversarial case {prefix} failed: {detail}"


def test_the_run_is_clean_overall(proven):
    _session, result = proven
    failures = [(k, d) for k, ok, d in result["checks"] if not ok]
    assert result["ok"], f"proving run reported defects: {failures}"


# ---------------------------------------------------------------------------
# Shape of the resulting population
# ---------------------------------------------------------------------------


def test_the_cohort_reached_its_target(proven):
    session, result = proven
    program = result["program"]
    founders = session.execute(
        select(EvoCandidate).where(
            EvoCandidate.program_id == program.id, EvoCandidate.origin == "founder"
        )
    ).scalars().all()
    assert len(founders) == 30


def test_three_generations_ran(proven):
    _session, result = proven
    assert [r.generation.number for r in result["results"]] == [0, 1, 2]
    assert all(r.generation.status == "closed" for r in result["results"])


def test_generations_use_non_overlapping_windows(proven):
    """A child must not be scored on the evidence that ranked its parent."""
    _session, result = proven
    windows = [
        (r.generation.window_start, r.generation.window_end) for r in result["results"]
    ]
    # Deliberately offset: each window is paired with its successor, so the second
    # sequence is one shorter by construction.
    for (_, prev_end), (next_start, _) in zip(windows, windows[1:], strict=False):
        assert next_start >= prev_end


def test_both_reproduction_and_retirement_happened(proven):
    session, result = proven
    program = result["program"]
    states = {
        c.state
        for c in session.execute(
            select(EvoCandidate).where(EvoCandidate.program_id == program.id)
        ).scalars()
    }
    origins = {
        c.origin
        for c in session.execute(
            select(EvoCandidate).where(EvoCandidate.program_id == program.id)
        ).scalars()
    }
    assert "retired" in states and "active" in states
    assert "mutation" in origins


def test_findings_were_raised_and_routed(proven):
    session, result = proven
    rows = session.execute(
        select(EvoFinding).where(EvoFinding.program_id == result["program"].id)
    ).scalars().all()
    assert rows
    assert {r.route_to for r in rows} <= {
        "evo_ticket_workshop", "research_lab", "experiment_os_issue",
        "platform_change_review", "mutation_candidate",
    }


# ---------------------------------------------------------------------------
# The Control Tower
# ---------------------------------------------------------------------------


def test_the_tower_explains_rather_than_asserting(proven):
    session, result = proven
    data = control_tower.collect(session, program=result["program"])
    rendered = control_tower.render(data)
    for token in ("EVO PROGRAM", "TOP", "why:", "DIVERSITY"):
        assert token in rendered
    ranked = [e for e in data["entries"] if e.get("rank")]
    assert ranked and all(e["components"] for e in ranked)


def test_every_rank_can_be_unfolded_into_its_components(proven):
    session, result = proven
    data = control_tower.collect(session, program=result["program"])
    top = next(e for e in data["entries"] if e.get("rank") == 1)
    text = control_tower.explain_candidate(
        session, program=result["program"], label_or_uuid=top["label"]
    )
    assert "GENOME" in text and "HISTORY" in text
    assert "edge_lcb" in text and "drawdown_control" in text


def test_the_tower_reports_held_and_invalid_candidates(proven):
    session, result = proven
    data = control_tower.collect(session, program=result["program"])
    rendered = control_tower.render(data)
    assert "WARNINGS" in rendered
    classes = {e["evidence_class"] for e in data["entries"]}
    assert "insufficient" in classes or "invalid" in classes


def test_the_lineage_tree_shows_parents_above_children(proven):
    session, result = proven
    tree = control_tower.lineage_tree(session, program=result["program"])
    lines = tree.splitlines()
    assert lines
    assert any(line.startswith("  ") for line in lines), "children are indented"
    assert any(line.startswith("×") for line in lines), "retired candidates are marked"


def test_explaining_an_unknown_candidate_says_so(proven):
    session, result = proven
    text = control_tower.explain_candidate(
        session, program=result["program"], label_or_uuid="agent-999"
    )
    assert "no candidate" in text


def test_the_tower_handles_a_program_with_no_generations(evo_session):
    program = service.create_program(
        evo_session, key="empty-prog", name="empty", objective="x",
        dataset=proving.register(),
    )
    data = control_tower.collect(evo_session, program=program)
    assert data["generation"] is None
    assert "No generations have run yet." in control_tower.render(data)


# ---------------------------------------------------------------------------
# Boundaries this layer must keep
# ---------------------------------------------------------------------------


def test_the_layer_writes_only_its_own_namespace(proven):
    """Evo population must not touch the LLM organism's tables or Experiment OS's."""
    session, _result = proven
    from sqlalchemy import inspect as sa_inspect

    engine = session.get_bind()
    touched = set()
    for table in Base.metadata.tables:
        if not table.startswith("evo_pop_"):
            continue
        touched.add(table)
    assert touched, "the population layer defines its own tables"

    inspector = sa_inspect(engine)
    present = set(inspector.get_table_names())
    for foreign in ("evo_agents", "evo_cohorts", "evo_genomes", "experiments"):
        if foreign not in present:
            continue
        count = session.execute(
            select(__import__("sqlalchemy").func.count()).select_from(
                Base.metadata.tables[foreign]
            )
        ).scalar_one()
        assert count == 0, (
            f"{foreign} was written by the population layer — the LLM organism and "
            "Experiment OS own those rows"
        )


def test_no_module_in_the_layer_can_place_an_order():
    """There is no live path here: not omitted, absent."""
    import pkgutil

    import kalshi_bot.evo.population as pkg

    banned = ("place_order", "KalshiClient", "live_enabled", "arm_live")
    offenders = []
    for module in pkgutil.iter_modules(pkg.__path__):
        source = (
            __import__("pathlib").Path(pkg.__path__[0]) / f"{module.name}.py"
        ).read_text()
        for token in banned:
            if token in source:
                offenders.append((module.name, token))
    assert not offenders, f"live-trading surface referenced in the evo population layer: {offenders}"


def test_the_program_records_its_provenance(proven):
    _session, result = proven
    program: EvoProgram = result["program"]
    assert program.engine_revision and program.evaluator_revision
    for r in result["results"]:
        prov = r.generation.provenance_json or {}
        assert prov["engine_revision"] == program.engine_revision
        assert prov["evaluator_revision"] == program.evaluator_revision
        assert "genome_schema_revision" in prov
        assert prov["policy"]["min_trades_for_evidence"] == program.min_trades_for_evidence


def test_every_run_carries_reproducibility_metadata(proven):
    _session, result = proven
    for r in result["results"]:
        for run in r.runs:
            if run.status != "completed":
                continue
            repro = run.reproducibility_json or {}
            assert repro["outcome_fingerprint"]
            assert repro["genome_hash"] == run.genome_hash
            assert repro["dataset"] == run.dataset
            assert repro["data_cutoff"] == r.generation.data_cutoff


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_the_surface_command_renders_every_gene(capsys):
    """Prints the complete allowlist — the answer to 'what may evolution change?'."""
    from kalshi_bot.evo.population import cli
    from kalshi_bot.evo.population import genome as g

    assert cli.main(["surface"]) == 0
    out = capsys.readouterr().out
    for gene in g.MUTATION_SURFACE:
        assert gene.path in out
    assert "not blindly mutable" in out, "risk genes are flagged as perturbation-exempt"


def test_the_cli_parser_covers_every_command():
    from kalshi_bot.evo.population import cli

    parser = cli.build_parser()
    for argv in (
        ["tower", "--program", "x"],
        ["explain", "--program", "x", "agent-001"],
        ["lineage", "--program", "x"],
        ["findings", "--program", "x"],
        ["surface"],
        ["create", "--program", "x", "--objective", "o", "--dataset", "d"],
        ["advance", "--program", "x", "--window-start", "2026-01-01",
         "--window-end", "2026-02-01"],
        ["proving-run"],
    ):
        args = parser.parse_args(argv)
        assert callable(args.func)
