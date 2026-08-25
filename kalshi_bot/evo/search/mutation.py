"""Mutation proposals for the search capability: bounded, gated, and powerless.

`propose_*` produces a `MutationProposal` — a description of a change, with a hypothesis
and a provenance, and no authority. `evaluate_proposal_document` runs the five gates and
decides whether that change is even coherent enough to be worth replaying.

**Nothing in this module writes a genome.** That is the whole point of where this now
sits: the search capability measures variants and hands the evidence back; the *agent*
adopts one through the organism's own `save_strategy` / `activate_strategy`, under the
organism's own budgets and audit. A search cannot change an agent, so there is no writer
here to bypass.

An LLM proposer plugs in at the `propose` end and inherits every gate for free. It cannot
express a change outside the gene surface, because a proposal is `(path, value)` pairs
against `genome.MUTATION_SURFACE` — which is the structural reason it can never rewrite
production code as a mutation. Deterministic perturbation is one bounded operator the
agent can point at a dimension; the hypothesis is the agent's.

Refused proposals are surfaced alongside admitted ones. "We tried that and the gate said
no, for this reason" is evidence about the search space, and hiding it means the same
invalid mutation is reproposed forever.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from . import diversity
from . import genome as genome_mod

MUTATION_ENGINE_REVISION = "mut-1"

SOURCE_SWEEP = "sweep"
SOURCE_PERTURBATION = "perturbation"
SOURCE_RESEARCH = "research"
SOURCE_LLM = "llm"
SOURCE_CROSSOVER = "crossover"

KIND_EXPLOIT = "exploit"
KIND_EXPLORE = "explore"


# ---------------------------------------------------------------------------
# Deterministic randomness
# ---------------------------------------------------------------------------


def _draw(*parts: object) -> float:
    """A stable pseudo-uniform in [0, 1) from a key.

    Same pattern as `evo/fill_model.py`: hashing the key rather than drawing from a
    generator means a proposal depends only on its inputs, so a search replays
    identically without threading RNG state through the whole call chain."""
    key = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:7], "big") / float(1 << 56)


def _pick(seq, *parts: object):
    if not seq:
        return None
    return seq[int(_draw(*parts) * len(seq)) % len(seq)]


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------


@dataclass
class MutationProposal:
    """A proposed change. Carries no authority — the gates decide, and even they
    only decide whether it is worth replaying."""

    #: Who asked, and from which revision of their strategy. Attribution only — a
    #: proposal carries no authority regardless of who made it.
    agent_uuid: str
    base_revision: int
    source: str
    kind: str
    changes: list[dict] = field(default_factory=list)  # {path, from, to}
    hypothesis: str = ""
    rationale: str = ""

    def apply_to(self, document: dict) -> dict:
        out = document
        for change in self.changes:
            out = genome_mod.set_path(out, str(change["path"]), change["to"])
        return out

    def describe(self) -> str:
        if not self.changes:
            return "no-op"
        return ", ".join(
            f"{c['path']} {c['from']} → {c['to']}" for c in self.changes
        )

    def as_changes_json(self) -> list:
        return [dict(c) for c in self.changes]


def _mutate_gene(gene: genome_mod.Gene, current, *, kind: str, seed_parts: tuple) -> object | None:
    """One gene's new value, or None if it cannot be moved."""
    if gene.kind == "set":
        # No vocabulary to draw from here — a set gene moves only through an explicit
        # proposal that names the members it wants (`propose_sweep`).
        return None
    if gene.kind in ("enum", "list_enum", "bool"):
        choices = [c for c in gene.choices if c != current]
        return _pick(choices, *seed_parts, gene.path, "enum")
    step = gene.step * (3.0 if kind == KIND_EXPLORE else 1.0)
    direction = 1.0 if _draw(*seed_parts, gene.path, "dir") < 0.5 else -1.0
    base = current
    if base is None:
        # An optional gene that is currently unset: seed it at the middle of its range
        # rather than stepping from nothing.
        if gene.lo is None or gene.hi is None:
            return None
        base = (float(gene.lo) + float(gene.hi)) / 2.0
    try:
        value = float(base) + direction * step
    except (TypeError, ValueError):
        return None
    if gene.lo is not None:
        value = max(float(gene.lo), value)
    if gene.hi is not None:
        value = min(float(gene.hi), value)
    value = int(round(value)) if gene.kind == "int" else round(value, 4)
    return None if value == current else value


#: Genes a mode switch has to bring with it. Changing `exit.mode` alone leaves the new
#: mode's thresholds unset, and a mode whose threshold is unset either never fires (so
#: the child is its parent wearing a different label) or is refused outright by the
#: compatibility gate. Either way the exit dimension would be unreachable by
#: perturbation, which would quietly remove a whole axis from the search.
_MODE_COMPANIONS: dict[str, dict[str, object]] = {
    "settlement": {},
    "tp_sl": {"exit.take_profit_cents": 80, "exit.stop_loss_cents": 25},
    "timed": {"exit.max_hold_hours": 12.0},
    "confirmed_stop": {"exit.stop_mid_cents": 30, "exit.confirm_ticks": 3},
    "volatility_exit": {"exit.vol_range_cents": 14, "exit.vol_window_ticks": 6},
}


def _companions_for(path: str, new_value, document: dict) -> list[dict]:
    """The additional changes a gene move requires to be coherent."""
    if path != "exit.mode":
        return []
    out: list[dict] = []
    for comp_path, default in _MODE_COMPANIONS.get(str(new_value), {}).items():
        gene = genome_mod.GENES_BY_PATH.get(comp_path)
        current = genome_mod.get_path(document, comp_path)
        if current is not None or gene is None:
            continue
        out.append(
            {"path": comp_path, "label": gene.label, "from": current, "to": default}
        )
    return out


def propose_perturbation(
    *,
    agent_uuid: str,
    base_revision: int,
    document: dict,
    allowed_paths: list[str],
    kind: str = KIND_EXPLOIT,
    seed: object = 0,
    index: int = 0,
    max_genes: int = 1,
) -> MutationProposal | None:
    """Step one (exploit) or a few (explore) genes deterministically.

    Only genes that *apply* to this genome are eligible: stepping `take_profit_cents` on
    a settlement-exit genome would record a mutation that changes nothing, and a child
    whose diff is inert is indistinguishable from its parent while claiming to be a
    test of something."""
    eligible = [
        g
        for g in genome_mod.MUTATION_SURFACE
        if g.path in set(allowed_paths) and g.independent and g.applies(document)
    ]
    if not eligible:
        return None
    n_genes = 1 if kind == KIND_EXPLOIT else min(max_genes, 1 + int(_draw(seed, index, "n") * 2))

    changes: list[dict] = []
    working = document
    for slot in range(n_genes):
        remaining = [g for g in eligible if g.path not in {c["path"] for c in changes}]
        gene = _pick(remaining, seed, index, slot, "gene")
        if gene is None:
            break
        current = genome_mod.get_path(working, gene.path)
        new_value = _mutate_gene(gene, current, kind=kind, seed_parts=(seed, index, slot))
        if new_value is None:
            continue
        changes.append(
            {"path": gene.path, "label": gene.label, "from": current, "to": new_value}
        )
        working = genome_mod.set_path(working, gene.path, new_value)
        for companion in _companions_for(gene.path, new_value, working):
            changes.append(companion)
            working = genome_mod.set_path(working, companion["path"], companion["to"])
    if not changes:
        return None

    return MutationProposal(
        agent_uuid=agent_uuid,
        base_revision=base_revision,
        source=SOURCE_PERTURBATION,
        kind=kind,
        changes=changes,
        hypothesis=_default_hypothesis(changes, kind),
        rationale=(
            "deterministic perturbation of the parent's best-performing genome; "
            f"{'single-gene exploit step' if kind == KIND_EXPLOIT else 'multi-gene explore step'}"
        ),
    )


def propose_sweep(
    *,
    agent_uuid: str,
    base_revision: int,
    document: dict,
    path: str,
    value,
    hypothesis: str = "",
    rationale: str = "",
) -> MutationProposal | None:
    """An explicit, named change — the interface a research finding or an LLM uses.

    There is deliberately no free-form variant: a proposer names a gene and a value, or
    it cannot propose at all."""
    gene = genome_mod.GENES_BY_PATH.get(path)
    if gene is None:
        return None
    current = genome_mod.get_path(document, path)
    if current == value:
        return None
    changes = [{"path": path, "label": gene.label, "from": current, "to": value}]
    return MutationProposal(
        agent_uuid=agent_uuid,
        base_revision=base_revision,
        source=SOURCE_SWEEP,
        kind=KIND_EXPLOIT,
        changes=changes,
        hypothesis=hypothesis or _default_hypothesis(changes, KIND_EXPLOIT),
        rationale=rationale or "explicit sweep over a named gene",
    )


def _default_hypothesis(changes: list[dict], kind: str) -> str:
    moves = ", ".join(f"{c['label']} {c['from']} → {c['to']}" for c in changes)
    intent = (
        "a small step in a direction the parent's evidence supports"
        if kind == KIND_EXPLOIT
        else "a larger step into a region the parent has not sampled"
    )
    return f"{moves} improves the parent's risk-adjusted edge: {intent}."


# ---------------------------------------------------------------------------
# Admission — the only path to a genome
# ---------------------------------------------------------------------------


@dataclass
class Admission:
    ok: bool
    stage: str | None = None
    reason: str | None = None
    document: dict | None = None
    genome_hash: str | None = None
    nearest_distance: float | None = None


def evaluate_proposal_document(
    proposal: MutationProposal,
    *,
    parent_document: dict,
    existing_documents: list[dict],
    allowed_paths: list[str],
    min_distance: float = 0.02,
    capital_usd: float = 500.0,
    max_size_contracts: int | None = None,
) -> Admission:
    """Run every gate. Pure: decides, writes nothing.

    The gates run in this order because each one's error message is only meaningful if
    the earlier ones passed — "too close to an existing genome" is misleading advice
    about a document that does not validate."""
    # 1. surface legality — did the proposal touch only genes it was allowed to?
    illegal = [
        c["path"] for c in proposal.changes if str(c["path"]) not in set(allowed_paths)
    ]
    if illegal:
        return Admission(
            False, "schema",
            f"proposal touches genes outside the allowed surface: {illegal}",
        )
    unknown = [c["path"] for c in proposal.changes if c["path"] not in genome_mod.GENES_BY_PATH]
    if unknown:
        return Admission(False, "schema", f"unknown genes: {unknown}")

    # 2. schema, then cross-field compatibility — reported separately, because the two
    #    tell a proposer different things. A schema failure means the value is not a
    #    legal value for that gene; a compatibility failure means the value is legal but
    #    incoherent with the rest of the genome, which is the fixable one.
    candidate_doc = proposal.apply_to(parent_document)
    norm, err = genome_mod.normalize(candidate_doc)
    if err or norm is None:
        return Admission(False, "schema", str(err))
    compat = genome_mod.compatibility_errors(norm)
    if compat:
        return Admission(False, "compatibility", "; ".join(compat))

    # 3. the mutation must actually change something on the surface
    changed = genome_mod.diff(parent_document, norm)
    if not changed:
        return Admission(
            False, "compatibility",
            "the proposal normalizes to the parent genome — nothing on the gene surface "
            "actually changed",
        )

    # 4. risk envelope
    risk_err = _risk_errors(norm, capital_usd=capital_usd, max_size_contracts=max_size_contracts)
    if risk_err:
        return Admission(False, "risk", risk_err)

    # 5. novelty / duplicate — scoped to the axes this search is varying. The recorded
    #    `distance_from_base` stays whole-surface, because that number has to stay
    #    comparable between runs; the gate is about whether THIS run can attribute a
    #    difference in results to THIS change.
    ok, nearest_d, reason = diversity.novelty_check(
        norm,
        existing_documents,
        min_distance=float(min_distance),
        paths=list(allowed_paths),
    )
    if not ok:
        return Admission(False, "novelty", reason, nearest_distance=nearest_d)

    return Admission(
        True,
        document=norm,
        genome_hash=genome_mod.genome_hash(norm),
        nearest_distance=nearest_d,
    )


def _risk_errors(
    document: dict, *, capital_usd: float, max_size_contracts: int | None = None
) -> str | None:
    """The risk envelope, applied to a candidate document.

    A variant whose worst-case deployment cannot fit the virtual account the search
    scores against is refused here rather than left to breach capital during the replay
    and be caught by the integrity component afterwards — a refused proposal costs
    nothing, a wasted replay costs the agent budget."""
    entry = document.get("entry") or {}
    risk = document.get("risk") or {}
    capital = float(capital_usd)

    size = int(entry.get("size_contracts") or 0)
    max_price = int(entry.get("max_price_cents") or 0)
    per_position = size * max_price / 100.0
    concurrent = int(risk.get("max_concurrent_positions") or 1)
    worst_case = per_position * concurrent
    if worst_case > capital:
        return (
            f"worst-case exposure ${worst_case:,.2f} ({size} contracts @ {max_price}c × "
            f"{concurrent} concurrent) exceeds the ${capital:,.2f} the search scores against"
        )

    if max_size_contracts is not None and size > int(max_size_contracts):
        return f"size {size} exceeds the search cap of {max_size_contracts}"
    return None



__all__ = [
    "Admission",
    "KIND_EXPLOIT",
    "KIND_EXPLORE",
    "MUTATION_ENGINE_REVISION",
    "MutationProposal",
    "SOURCE_CROSSOVER",
    "SOURCE_LLM",
    "SOURCE_PERTURBATION",
    "SOURCE_RESEARCH",
    "SOURCE_SWEEP",
    "evaluate_proposal_document",
    "propose_perturbation",
    "propose_sweep",
]
