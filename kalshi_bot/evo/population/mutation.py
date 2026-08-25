"""Mutation: proposing a change, and separately, admitting one.

The split is the point of this module. `propose_*` functions produce a
`MutationProposal` — a description of a change, with a hypothesis and a provenance, and
no power whatsoever. `admit_proposal` is the only thing that can turn a proposal into a
genome, and it does so only after five gates pass. An LLM proposer, when one is added,
plugs in at the `propose` end and inherits every gate for free; it can never reach the
admission path directly, and it can never express a change outside the gene surface
because a proposal is `(path, value)` pairs against `genome.MUTATION_SURFACE`.

Proposals are persisted whether or not they are admitted. A rejection is evidence: it
records that this branch of the search space was tried and why it was refused, which is
what stops the same invalid mutation being reproposed every generation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from . import diversity
from . import genome as genome_mod
from .models import EvoGenomeVersion, EvoMutationProposal, EvoProgram

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
    generator means a proposal depends only on its inputs, so a generation replays
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
    """A proposed change. Carries no authority — `admit_proposal` decides."""

    parent_candidate_uuid: str
    parent_genome_id: int
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
    parent_candidate_uuid: str,
    parent_genome_id: int,
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
        parent_candidate_uuid=parent_candidate_uuid,
        parent_genome_id=parent_genome_id,
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
    parent_candidate_uuid: str,
    parent_genome_id: int,
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
        parent_candidate_uuid=parent_candidate_uuid,
        parent_genome_id=parent_genome_id,
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


def evaluate_proposal(
    proposal: MutationProposal,
    *,
    parent_document: dict,
    program: EvoProgram,
    existing_documents: list[dict],
    allowed_paths: list[str],
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
            f"proposal touches genes outside the program's allowed surface: {illegal}",
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
    risk_err = _risk_errors(norm, program)
    if risk_err:
        return Admission(False, "risk", risk_err)

    # 5. novelty / duplicate
    ok, nearest_d, reason = diversity.novelty_check(
        norm, existing_documents, min_distance=float(program.min_genome_distance)
    )
    if not ok:
        return Admission(False, "novelty", reason, nearest_distance=nearest_d)

    return Admission(
        True,
        document=norm,
        genome_hash=genome_mod.genome_hash(norm),
        nearest_distance=nearest_d,
    )


def _risk_errors(document: dict, program: EvoProgram) -> str | None:
    """The program's risk envelope, applied to a candidate genome.

    A genome whose worst-case deployment cannot fit the virtual account is refused here
    rather than left to breach capital during the run and be caught by the integrity
    component afterwards — a refused proposal costs nothing, a wasted run costs a slot."""
    entry = document.get("entry") or {}
    risk = document.get("risk") or {}
    capital = float(program.starting_capital_usd)

    size = int(entry.get("size_contracts") or 0)
    max_price = int(entry.get("max_price_cents") or 0)
    per_position = size * max_price / 100.0
    concurrent = int(risk.get("max_concurrent_positions") or 1)
    worst_case = per_position * concurrent
    if worst_case > capital:
        return (
            f"worst-case exposure ${worst_case:,.2f} ({size} contracts @ {max_price}c × "
            f"{concurrent} concurrent) exceeds the program's ${capital:,.2f} virtual capital"
        )

    policy = program.policy_json or {}
    max_size = policy.get("max_size_contracts")
    if max_size is not None and size > int(max_size):
        return f"size {size} exceeds the program cap of {max_size}"
    return None


def record_proposal(
    session,
    *,
    program: EvoProgram,
    generation_number: int,
    proposal: MutationProposal,
    admission: Admission,
) -> EvoMutationProposal:
    """Persist the proposal and its verdict — accepted or not."""
    row = EvoMutationProposal(
        program_id=program.id,
        generation_number=generation_number,
        parent_candidate_uuid=proposal.parent_candidate_uuid,
        parent_genome_id=proposal.parent_genome_id,
        source=proposal.source,
        kind=proposal.kind,
        changes_json=proposal.as_changes_json(),
        hypothesis=proposal.hypothesis,
        rationale=proposal.rationale,
        status="accepted" if admission.ok else "rejected",
        reject_stage=admission.stage,
        reject_reason=admission.reason,
        proposed_hash=admission.genome_hash,
        nearest_distance=admission.nearest_distance,
    )
    session.add(row)
    session.flush()
    return row


def admit_proposal(
    session,
    *,
    program: EvoProgram,
    generation_number: int,
    child_candidate_uuid: str,
    parent_genome: EvoGenomeVersion,
    proposal: MutationProposal,
    admission: Admission,
    proposal_row: EvoMutationProposal,
    evidence_cutoff: str | None,
    version: int = 1,
) -> EvoGenomeVersion:
    """Turn an accepted proposal into an immutable genome version.

    The only function in this package that creates a genome from a mutation. It refuses
    an admission that did not pass, so a caller cannot skip the gates by constructing an
    `Admission(ok=True)` from somewhere else and hoping — the document is taken from the
    admission, which only `evaluate_proposal` produces."""
    if not admission.ok or admission.document is None:
        raise ValueError("cannot admit a proposal that did not pass evaluate_proposal")

    changes = genome_mod.diff(parent_genome.document_json or {}, admission.document)
    child = EvoGenomeVersion(
        program_id=program.id,
        candidate_uuid=child_candidate_uuid,
        version=version,
        genome_hash=admission.genome_hash or genome_mod.genome_hash(admission.document),
        document_json=admission.document,
        family=str(admission.document.get("family") or parent_genome.family),
        parent_genome_id=parent_genome.id,
        mutation_source=proposal.source,
        mutation_kind=proposal.kind,
        mutation_diff_json=[c.as_dict() for c in changes],
        proposal_id=proposal_row.id,
        hypothesis=proposal.hypothesis,
        rationale=proposal.rationale,
        universe_json=admission.document.get("universe"),
        risk_json=admission.document.get("risk"),
        born_generation=generation_number,
        evidence_cutoff=evidence_cutoff,
        platform_snapshot=program.platform_snapshot,
        model_revision=parent_genome.model_revision,
        evaluated=False,
    )
    session.add(child)
    session.flush()
    proposal_row.resulting_genome_id = child.id
    session.flush()
    return child


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
    "admit_proposal",
    "evaluate_proposal",
    "propose_perturbation",
    "propose_sweep",
    "record_proposal",
]
