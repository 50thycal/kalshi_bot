"""System-level test: the deterministic multi-generation simulation (spec §36.3).

Runs the whole machine — 30 founders with adversarial profiles, real listeners/
strategies/paper engine/fitness/evolution — over two simulated cohorts, twice,
and asserts the evolutionary invariants plus seed reproducibility. (The full
5-cohort run incl. the wildcard is exercised by scripts/evo_simulation.py; the
wildcard path itself is unit-tested in test_evo_evolution.py.)"""

from __future__ import annotations

import pytest

from kalshi_bot.evo.simulation import FOUNDER_PROFILES, Simulation


@pytest.fixture(scope="module")
def sim_results():
    r1 = Simulation(seed=1234, cohorts=2, cycles_per_day=4).run()
    r2 = Simulation(seed=1234, cohorts=2, cycles_per_day=4).run()
    return r1, r2


def test_simulation_is_deterministic(sim_results):
    r1, r2 = sim_results
    assert r1["determinism_digest"] == r2["determinism_digest"]
    assert r1["by_profile"] == r2["by_profile"]


def test_population_and_lineage_invariants(sim_results):
    r1, _ = sim_results
    assert r1["cohorts_finalized"] == 2
    assert r1["population_active"] == 30  # restored to exactly 30 after each boundary
    assert r1["founder_surnames"] == 30  # thirty unique founder surnames
    assert r1["total_surnames"] == 30  # children inherit; no wildcard in 2 cohorts
    assert r1["retired_total"] == 18  # bottom 9 per boundary
    assert r1["children_born"] == 18  # nine new slots per boundary
    assert r1["generations_max"] >= 2
    assert r1["retirement_reasons"] == ["bottom_group"]
    for t in r1["transitions"]:
        assert len(t["retired"]) == 9
        assert len(t["survivors"]) == 21
        assert len(t["children"]) == 9


def test_selection_favors_the_right_traits(sim_results):
    r1, _ = sim_results
    by = r1["by_profile"]
    # the roster covers every profile
    assert set(by) == set(FOUNDER_PROFILES)

    def survival(profile: str) -> float:
        row = by[profile]
        return row["active"] / row["agents"] if row["agents"] else 0.0

    # agents with a real edge + sound process outlive the pathological ones
    good = (survival("consistent") + survival("adapter")) / 2
    for bad in ("thrasher", "hog", "stale", "rewriter", "inactive"):
        assert good > survival(bad), (bad, by[bad])
    # integrity violators and budget wasters do not thrive
    assert by["rewriter"]["active"] <= 1
    assert by["hog"]["active"] <= 1
    # copying a good strategy is LEGAL and may pay short-term (spec §12); the
    # invariant is that copiers never overtake the originator lines in aggregate
    originals = by["consistent"]["active"] + by["adapter"]["active"]
    assert by["copier"]["active"] < originals
