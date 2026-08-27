"""Genome representation: a constrained, validated, content-addressed strategy.

A genome *is* a `StrategySpec` document (`evo/strategy_spec.py`) — the same typed,
validated DSL the sandbox already replays and the LLM organism already writes. Reusing
it is deliberate: a second strategy representation would mean a second answer to "what
does this strategy do", and the replay engine would have to be forked to run it.

What this module adds on top of the spec is what evolution needs and the spec does not
provide:

* an explicit **mutation surface** — the allowlist of dimensions evolution may touch,
  each with a type, a valid range, a mutation step, whether it is independently
  mutable, and the compatibility rule that decides when it applies at all;
* a **canonical normalization** and a deterministic **hash**, so genome identity is
  content-addressed and immutability is checkable after the fact;
* a **distance metric** over that surface, so "these two are the same idea" is a
  measurable claim rather than an opinion.

Nothing outside the surface is mutable. An LLM proposer, when one is added, produces
`(path, value)` pairs against these genes and cannot express anything else — which is
the structural reason it can never rewrite production code as a mutation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Any

from ..strategy_spec import EXIT_MODES, StrategySpec, validate_spec

# Bumped when normalization or the surface changes in a way that alters a hash or a
# distance. Recorded on every genome so an old row is never silently compared with a
# new one under different rules.
GENOME_SCHEMA_REVISION = "gen-1"


# ---------------------------------------------------------------------------
# Gene descriptors — the mutation surface
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Gene:
    """One independently addressable dimension of the genome.

    `step` is the mutation increment for numeric genes (an exploit step; explore
    multiplies it). `span` is the range used to normalize distance so genes measured in
    different units contribute comparably. `applies_when` is the compatibility rule:
    a gene that does not apply is neither mutated nor counted in distance, because
    `take_profit_cents` on a settlement-exit genome is not a real difference."""

    path: str
    kind: str  # int | float | enum | bool | list_enum
    label: str
    span: float = 1.0
    step: float = 1.0
    lo: float | None = None
    hi: float | None = None
    choices: tuple[Any, ...] = ()
    independent: bool = True
    optional: bool = False  # may be None
    applies_when: Callable[[dict], bool] | None = None
    weight: float = 1.0

    def applies(self, doc: dict) -> bool:
        return True if self.applies_when is None else bool(self.applies_when(doc))


def _exit_mode(doc: dict) -> str:
    return str(((doc.get("exit") or {}).get("mode")) or "settlement")


def _is_maker(doc: dict) -> bool:
    return str(((doc.get("entry") or {}).get("style")) or "taker") == "maker"


#: The complete allowlist. Evolution may change these and nothing else.
MUTATION_SURFACE: tuple[Gene, ...] = (
    # --- universe / taxonomy -------------------------------------------------
    # Which markets a genome trades is the largest single thing about it, so it carries
    # the heaviest distance weight: two genomes with identical rules over disjoint
    # universes are not the same strategy, and a distance metric that could not see
    # that would let the population fill up with "duplicates" that share nothing.
    #
    # `independent=False` keeps it out of blind perturbation. Stepping a ticker prefix
    # has no meaning without a universe vocabulary to step through, and a proposer that
    # invented prefixes would mostly produce genomes that match no market at all — a
    # zero-trade run that reads as "no edge". A research or LLM proposer can still move
    # it deliberately through `mutation.propose_sweep`.
    Gene("universe.series_prefixes", "set", "universe", independent=False, weight=3.0),
    Gene("universe.categories", "set", "categories", independent=False, weight=1.0),
    Gene("universe.min_volume", "float", "min volume", span=5000.0, step=250.0,
         lo=0.0, hi=100_000.0),
    Gene("universe.max_spread_cents", "int", "max spread", span=20.0, step=1.0,
         lo=1, hi=99),
    Gene("universe.min_hours_to_close", "float", "min hours to close", span=48.0,
         step=1.0, lo=0.0, hi=720.0),
    Gene("universe.max_hours_to_close", "float", "max hours to close", span=168.0,
         step=6.0, lo=0.0, hi=720.0),
    # --- entry rule ----------------------------------------------------------
    Gene("entry.side", "enum", "side", choices=("yes", "no", "cheap", "expensive"),
         weight=2.0),
    Gene("entry.style", "enum", "style", choices=("taker", "maker"), weight=2.0),
    Gene("entry.min_price_cents", "int", "entry price floor", span=30.0, step=2.0,
         lo=1, hi=99),
    Gene("entry.max_price_cents", "int", "entry price ceiling", span=30.0, step=2.0,
         lo=1, hi=99),
    Gene("entry.maker_offset_cents", "int", "maker offset", span=5.0, step=1.0,
         lo=0, hi=10, applies_when=_is_maker),
    # --- sizing (within the virtual budget) ----------------------------------
    Gene("entry.size_contracts", "int", "size", span=25.0, step=2.0, lo=1, hi=500),
    # --- exit / hold rule ----------------------------------------------------
    Gene("exit.mode", "enum", "exit mode", choices=EXIT_MODES, weight=2.0),
    Gene("exit.take_profit_cents", "int", "take profit", span=30.0, step=2.0, lo=1,
         hi=99, optional=True, applies_when=lambda d: _exit_mode(d) == "tp_sl"),
    Gene("exit.stop_loss_cents", "int", "stop loss", span=30.0, step=2.0, lo=1, hi=99,
         optional=True, applies_when=lambda d: _exit_mode(d) == "tp_sl"),
    Gene("exit.max_hold_hours", "float", "max hold hours", span=48.0, step=2.0, lo=0.5,
         hi=720.0, optional=True,
         applies_when=lambda d: _exit_mode(d) in ("timed", "tp_sl")),
    Gene("exit.stop_mid_cents", "int", "confirmed-stop level", span=30.0, step=2.0,
         lo=1, hi=99, optional=True,
         applies_when=lambda d: _exit_mode(d) == "confirmed_stop"),
    Gene("exit.confirm_ticks", "int", "confirm ticks", span=6.0, step=1.0, lo=1, hi=20,
         applies_when=lambda d: _exit_mode(d) == "confirmed_stop"),
    Gene("exit.vol_window_ticks", "int", "vol window", span=12.0, step=2.0, lo=2, hi=50,
         applies_when=lambda d: _exit_mode(d) == "volatility_exit"),
    Gene("exit.vol_range_cents", "int", "vol range", span=20.0, step=2.0, lo=1, hi=99,
         optional=True, applies_when=lambda d: _exit_mode(d) == "volatility_exit"),
    # --- risk ----------------------------------------------------------------
    # All three are `independent=False`, and the reason is a property of the replay
    # engine rather than a preference: `sandbox.run_backtest` visits markets one at a
    # time and holds at most one position per market, so it never enforces a concurrency,
    # per-event or per-position cost cap. A blind perturbation of one of these therefore
    # produces a variant whose trade tape is provably identical to the base's — a
    # replay spent on a hypothesis the engine cannot test.
    #
    # They stay in the surface because they are still part of the genome's identity, are
    # still counted in distance, and are still checked against the risk envelope in
    # `mutation._risk_errors`. A research or LLM proposer may set them
    # deliberately through `propose_sweep`; what is refused is picking them at random and
    # calling the result an experiment.
    Gene("risk.max_concurrent_positions", "int", "max concurrent", span=20.0, step=2.0,
         lo=1, hi=200, independent=False),
    Gene("risk.max_per_event", "int", "max per event", span=4.0, step=1.0, lo=1, hi=10,
         independent=False),
    Gene("risk.max_cost_per_position_usd", "float", "max cost/position", span=100.0,
         step=10.0, lo=1.0, hi=1000.0, independent=False),
)

GENES_BY_PATH: dict[str, Gene] = {g.path: g for g in MUTATION_SURFACE}
MUTABLE_PATHS: tuple[str, ...] = tuple(g.path for g in MUTATION_SURFACE)


# ---------------------------------------------------------------------------
# Path access
# ---------------------------------------------------------------------------


def get_path(doc: dict, path: str) -> Any:
    node: Any = doc
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def set_path(doc: dict, path: str, value: Any) -> dict:
    """Return a deep-ish copy of `doc` with `path` set. Never mutates the input — a
    genome is immutable and an in-place setter is the easiest way to violate that."""
    out = json.loads(json.dumps(doc))
    parts = path.split(".")
    node = out
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value
    return out


# ---------------------------------------------------------------------------
# Normalization, validation, hashing
# ---------------------------------------------------------------------------


def normalize(doc: dict) -> tuple[dict | None, str | None]:
    """Validate against StrategySpec and return the canonical, still-runnable document.

    Canonical means every field present with its default filled in and entry conditions
    sorted, so two documents describing the same strategy normalize identically. The
    result keeps `name`/`description`, so it can be revalidated and replayed as-is;
    `identity_document` is what strips them for hashing."""
    spec, err = validate_spec(doc)
    if err or spec is None:
        return None, err or "invalid genome"
    out = spec.model_dump(mode="json")
    # Conditions are a set of constraints, not a sequence — order must not change identity.
    conds = out.get("entry", {}).get("conditions") or []
    out["entry"]["conditions"] = sorted(
        conds, key=lambda c: (str(c.get("metric")), str(c.get("op")), float(c.get("value", 0)))
    )
    return out, None


def identity_document(doc: dict) -> dict:
    """The genetics of a genome, with the labels removed.

    `name` and `description` are how a genome is displayed, not what it does. Two
    genomes that differ only in their name are the same strategy, and the duplicate
    check has to see that — otherwise renaming a genome is enough to smuggle a clone
    back into the population."""
    out = {k: v for k, v in doc.items() if k not in ("name", "description")}
    return out


def compatibility_errors(doc: dict) -> list[str]:
    """Constraints that are valid per-field but incoherent together.

    StrategySpec already rejects a path-dependent exit mode with no threshold. These are
    the cross-field rules evolution can trip that a single-field validator cannot see."""
    errs: list[str] = []
    entry = doc.get("entry") or {}
    exit_ = doc.get("exit") or {}
    universe = doc.get("universe") or {}
    risk = doc.get("risk") or {}

    lo, hi = entry.get("min_price_cents"), entry.get("max_price_cents")
    if isinstance(lo, int) and isinstance(hi, int) and lo > hi:
        errs.append(f"entry price band inverted: min {lo} > max {hi}")

    h_lo, h_hi = universe.get("min_hours_to_close"), universe.get("max_hours_to_close")
    if isinstance(h_lo, (int, float)) and isinstance(h_hi, (int, float)) and h_lo > h_hi:
        errs.append(f"time-to-close window inverted: min {h_lo} > max {h_hi}")

    if exit_.get("mode") == "tp_sl" and not (
        exit_.get("take_profit_cents") or exit_.get("stop_loss_cents")
    ):
        errs.append("tp_sl exit with neither take_profit_cents nor stop_loss_cents never fires")

    if exit_.get("mode") == "timed" and not exit_.get("max_hold_hours"):
        # Without a horizon a timed exit never triggers, so the genome is settlement
        # under a different name: a child claiming to test an exit rule that cannot fire.
        errs.append("timed exit with no max_hold_hours never fires")

    if entry.get("style") == "taker" and int(entry.get("maker_offset_cents") or 0) > 0:
        errs.append("maker_offset_cents is set but style is taker — the offset is inert")

    # Sizing must fit the per-position risk cap, or the risk cap is decorative.
    size = entry.get("size_contracts")
    cap = risk.get("max_cost_per_position_usd")
    if isinstance(size, int) and isinstance(cap, (int, float)) and isinstance(hi, int):
        worst = size * hi / 100.0
        if worst > float(cap):
            errs.append(
                f"size {size} @ {hi}c costs ${worst:.2f}, above max_cost_per_position_usd "
                f"${float(cap):.2f}"
            )
    return errs


def canonical_json(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), default=str)


def genome_hash(doc: dict) -> str:
    """Deterministic content address of a *normalized* document.

    Hashes the identity document, so the label fields cannot change it, and includes
    the schema revision, so a hash computed under different normalization rules can
    never collide with one computed under these."""
    payload = f"{GENOME_SCHEMA_REVISION}|{canonical_json(identity_document(doc))}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate(doc: dict) -> tuple[dict | None, str | None]:
    """Schema then cross-field compatibility. Returns (normalized, None) or (None, why).

    Surface legality — "was this search allowed to change that gene?" — is checked in
    `mutation.evaluate_proposal_document`, where the base genome is known and a change
    can be attributed. A genome on its own carries no evidence of what changed."""
    norm, err = normalize(doc)
    if err or norm is None:
        return None, err or "invalid genome"
    errs = compatibility_errors(norm)
    if errs:
        return None, "; ".join(errs)
    return norm, None


# ---------------------------------------------------------------------------
# Diff and distance
# ---------------------------------------------------------------------------


@dataclass
class GeneChange:
    path: str
    label: str
    before: Any
    after: Any

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "label": self.label,
            "from": self.before,
            "to": self.after,
        }

    def describe(self) -> str:
        return f"{self.path} {self.before} → {self.after}"


def diff(a: dict, b: dict) -> list[GeneChange]:
    """Gene-level differences between two normalized genomes, restricted to the
    mutation surface. This is what gets persisted as a mutation diff — a change that
    does not appear here did not happen to a gene."""
    out: list[GeneChange] = []
    for gene in MUTATION_SURFACE:
        av, bv = get_path(a, gene.path), get_path(b, gene.path)
        if av == bv:
            continue
        # A gene that applies to neither side is not a real difference.
        if not gene.applies(a) and not gene.applies(b):
            continue
        out.append(GeneChange(gene.path, gene.label, av, bv))
    return out


def _gene_distance(gene: Gene, a: dict, b: dict) -> float | None:
    """Per-gene normalized distance in [0, 1], or None when the gene applies to
    neither genome (so it cannot contribute either similarity or difference)."""
    a_applies, b_applies = gene.applies(a), gene.applies(b)
    if not a_applies and not b_applies:
        return None
    av, bv = get_path(a, gene.path), get_path(b, gene.path)
    if a_applies != b_applies:
        # One side has the gene switched on and the other does not: a structural
        # difference, scored at full distance rather than compared numerically.
        return 1.0
    if av is None and bv is None:
        return 0.0
    if av is None or bv is None:
        return 1.0
    if gene.kind == "set":
        # Jaccard distance: two universes that overlap partially are partially
        # different, which is what an equality test could not express.
        a_set, b_set = set(av or ()), set(bv or ())
        if not a_set and not b_set:
            return 0.0
        union = a_set | b_set
        return round(1.0 - len(a_set & b_set) / len(union), 6) if union else 0.0
    if gene.kind in ("enum", "bool", "list_enum"):
        return 0.0 if av == bv else 1.0
    try:
        delta = abs(float(av) - float(bv))
    except (TypeError, ValueError):
        return 0.0 if av == bv else 1.0
    span = gene.span if gene.span > 0 else 1.0
    return min(1.0, delta / span)


def distance(a: dict, b: dict, *, paths: Collection[str] | None = None) -> float:
    """Weighted mean per-gene distance in [0, 1].

    0.0 means identical. Normalization is what makes that meaningful: without it, two
    genomes differing only in whitespace would read as novel.

    `paths` restricts the measurement to a subset of the surface, and the two readings
    answer different questions. Over the whole surface, "how different are these two
    strategies?" — that is what a run records as `distance_from_base`, and it stays
    comparable across runs because the denominator is fixed. Over the axes a search is
    actually varying, "is this variant far enough from one we already measured for the
    difference in their results to be attributable to the change?" A targeted search
    down one gene needs the second reading: against the full 23-gene denominator, every
    single-gene step reads as a near-duplicate and the whole neighbourhood is refused."""
    surface = (
        MUTATION_SURFACE
        if paths is None
        else tuple(g for g in MUTATION_SURFACE if g.path in set(paths))
    )
    total = 0.0
    weight = 0.0
    for gene in surface:
        d = _gene_distance(gene, a, b)
        if d is None:
            continue
        total += d * gene.weight
        weight += gene.weight
    return round(total / weight, 6) if weight else 0.0


def nearest(
    doc: dict, others: Sequence[dict], *, paths: Collection[str] | None = None
) -> tuple[float, int | None]:
    """Smallest distance from `doc` to any of `others`, with its index."""
    best, idx = 1.0, None
    for i, other in enumerate(others):
        d = distance(doc, other, paths=paths)
        if d < best:
            best, idx = d, i
    return best, idx


# ---------------------------------------------------------------------------
# Building and describing a genome
# ---------------------------------------------------------------------------


def spec_document(
    *,
    name: str,
    family: str,
    universe: dict | None = None,
    entry: dict | None = None,
    exit_: dict | None = None,
    risk: dict | None = None,
    description: str = "",
) -> dict:
    """Build a raw spec document from parts, ready for `normalize`."""
    return {
        "name": name,
        "family": family,
        "description": description,
        "universe": universe or {},
        "entry": entry or {},
        "exit": exit_ or {},
        "risk": risk or {},
    }


def describe(doc: dict) -> str:
    """One-line human summary of a genome, for a refusal reason or a candidate view."""
    entry = doc.get("entry") or {}
    exit_ = doc.get("exit") or {}
    band = f"{entry.get('min_price_cents')}-{entry.get('max_price_cents')}c"
    mode = exit_.get("mode")
    tail = ""
    if mode == "tp_sl":
        tail = f" tp={exit_.get('take_profit_cents')} sl={exit_.get('stop_loss_cents')}"
    elif mode == "timed":
        tail = f" hold<={exit_.get('max_hold_hours')}h"
    elif mode == "confirmed_stop":
        tail = f" stop<={exit_.get('stop_mid_cents')}c x{exit_.get('confirm_ticks')}"
    elif mode == "volatility_exit":
        tail = f" range>={exit_.get('vol_range_cents')}c/{exit_.get('vol_window_ticks')}"
    return (
        f"{doc.get('family', '?')} {entry.get('side')}/{entry.get('style')} {band} "
        f"x{entry.get('size_contracts')} exit={mode}{tail}"
    )


def surface_summary() -> list[dict]:
    """The mutation surface as data — used by the Control Tower and the docs so the
    allowlist is never described twice and never drifts from the code."""
    return [
        {
            "path": g.path,
            "label": g.label,
            "kind": g.kind,
            "range": (
                list(g.choices)
                if g.kind in ("enum", "list_enum")
                else ([] if g.kind == "set" else [g.lo, g.hi])
            ),
            "step": g.step,
            "independent": g.independent,
            "optional": g.optional,
            "conditional": g.applies_when is not None,
            "weight": g.weight,
        }
        for g in MUTATION_SURFACE
    ]


__all__ = [
    "GENOME_SCHEMA_REVISION",
    "Gene",
    "GeneChange",
    "MUTATION_SURFACE",
    "GENES_BY_PATH",
    "MUTABLE_PATHS",
    "StrategySpec",
    "canonical_json",
    "compatibility_errors",
    "describe",
    "diff",
    "distance",
    "genome_hash",
    "get_path",
    "nearest",
    "normalize",
    "set_path",
    "spec_document",
    "surface_summary",
    "validate",
]
