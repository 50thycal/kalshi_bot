"""Genome schema, canonical hashing, distance and the mutation surface."""

from __future__ import annotations

import pytest

from kalshi_bot.evo.population import genome as g


def _doc(**kw):
    base = dict(name="probe-alpha", family="test")
    base.update(kw)
    return g.spec_document(**base)


def test_normalize_fills_defaults_and_stays_revalidatable():
    norm, err = g.normalize(_doc(entry={"side": "no"}))
    assert err is None
    assert norm["entry"]["style"] == "taker"
    assert norm["exit"]["mode"] == "settlement"
    # A normalized document must still validate: it is what the replay engine runs.
    again, err2 = g.normalize(norm)
    assert err2 is None and again == norm


def test_hash_ignores_labels_but_not_genetics():
    a, _ = g.normalize(_doc(entry={"max_price_cents": 60}))
    renamed = dict(a, name="totally-different", description="new words")
    assert g.genome_hash(a) == g.genome_hash(renamed), (
        "renaming a genome must not make it a different genome, or a duplicate can be "
        "smuggled past the novelty check by relabelling it"
    )
    changed = g.set_path(a, "entry.max_price_cents", 61)
    assert g.genome_hash(a) != g.genome_hash(changed)


def test_condition_order_does_not_change_identity():
    conds = [
        {"metric": "yes_ask", "op": "<", "value": 60},
        {"metric": "spread", "op": "<=", "value": 3},
    ]
    a, _ = g.normalize(_doc(entry={"conditions": conds}))
    b, _ = g.normalize(_doc(entry={"conditions": list(reversed(conds))}))
    assert g.genome_hash(a) == g.genome_hash(b)


def test_set_path_does_not_mutate_the_input():
    a, _ = g.normalize(_doc())
    before = g.genome_hash(a)
    g.set_path(a, "entry.size_contracts", 99)
    assert g.genome_hash(a) == before


@pytest.mark.parametrize(
    "kwargs,fragment",
    [
        (dict(entry={"min_price_cents": 70, "max_price_cents": 20}), "inverted"),
        (
            dict(universe={"min_hours_to_close": 40, "max_hours_to_close": 5}),
            "time-to-close window inverted",
        ),
        (dict(exit_={"mode": "tp_sl"}), "never fires"),
        (dict(exit_={"mode": "timed"}), "never fires"),
        (dict(entry={"style": "taker", "maker_offset_cents": 3}), "inert"),
        (
            dict(
                entry={"size_contracts": 200, "max_price_cents": 90},
                risk={"max_cost_per_position_usd": 10.0},
            ),
            "max_cost_per_position_usd",
        ),
    ],
)
def test_incoherent_genomes_are_refused(kwargs, fragment):
    _, err = g.validate(_doc(**kwargs))
    assert err is not None and fragment in err


def test_distance_is_zero_for_identical_and_scales_with_change():
    a, _ = g.normalize(_doc(entry={"max_price_cents": 50}))
    near = g.set_path(a, "entry.max_price_cents", 52)
    far = g.set_path(a, "entry.max_price_cents", 90)
    assert g.distance(a, a) == 0.0
    assert 0 < g.distance(a, near) < g.distance(a, far)


def test_universe_is_part_of_identity():
    """Two genomes with identical rules over disjoint universes are not the same
    strategy. Without this the population fills with 'duplicates' sharing no markets."""
    a, _ = g.normalize(_doc(universe={"series_prefixes": ["KXAAA"]}))
    b, _ = g.normalize(_doc(universe={"series_prefixes": ["KXBBB"]}))
    both, _ = g.normalize(_doc(universe={"series_prefixes": ["KXAAA", "KXBBB"]}))
    assert g.distance(a, b) > 0
    assert g.distance(a, both) < g.distance(a, b)


def test_inapplicable_genes_are_not_differences():
    """take_profit_cents on a settlement-exit genome is not a real difference."""
    a, _ = g.normalize(_doc(exit_={"mode": "settlement"}))
    b = g.set_path(a, "exit.take_profit_cents", 70)
    assert g.diff(a, b) == []
    assert g.distance(a, b) == 0.0


def test_risk_genes_are_not_independently_mutable():
    """The replay engine enforces no risk cap, so a blind perturbation of one would
    produce a child that provably cannot differ from its parent."""
    risk_genes = [x for x in g.MUTATION_SURFACE if x.path.startswith("risk.")]
    assert risk_genes
    assert not any(x.independent for x in risk_genes)


def test_surface_summary_covers_every_gene():
    assert len(g.surface_summary()) == len(g.MUTATION_SURFACE)
    assert set(g.GENES_BY_PATH) == set(g.MUTABLE_PATHS)
