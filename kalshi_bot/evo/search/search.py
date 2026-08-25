"""The historical search capability an Evo agent invokes.

One call answers one question: *"around the strategy I am running, over this window of
settled history, what would these variants have done?"* It replays the base genome and a
bounded neighbourhood around it, gates every proposed variant, scores what survived, and
returns **evidence**.

What this module deliberately does not do:

* It does not decide anything. No retirement, no promotion, no reproduction, no cohort
  ranking. The agent reads the evidence and may then adopt a variant through the
  organism's `save_strategy` / `activate_strategy` — or not. `run_search` returns a
  dict; it never changes an agent's state.
* It does not write `evo_fitness`. The score on a candidate ranks *strategy documents
  against each other inside one search*. An agent's authoritative fitness ranks
  *organisms across a cohort* and includes dimensions no replay can see — adaptive
  intelligence, opportunity capture, historical reliability. Conflating them would let a
  backtest decide who survives.
* It does not adapt on a generation boundary. A search is a heartbeat-time action; an
  agent may run one whenever it has a question, and revise whenever the evidence
  justifies it. Nothing here waits for a cohort to end.

Parameter perturbation is a bounded tool the agent can point at a dimension. The
hypothesis is the agent's.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from sqlalchemy import select

from ..config import EvoSettings
from ..models import EvoGenome, EvoStrategy
from . import fitness as fitness_mod
from . import genome as genome_mod
from . import mutation, replay
from .models import EvoSearchCandidate, EvoSearchRun, EvoSearchTrade

SEARCH_REVISION = "search-1"

#: Default neighbourhood size. Small on purpose: a search is a heartbeat action competing
#: with the agent's other budget, and thirty variants of one genome is not thirty times
#: more informative than eight.
DEFAULT_NEIGHBOURHOOD = 8
MAX_NEIGHBOURHOOD = 24

#: Virtual capital a search run scores against. This is a yardstick for comparing
#: variants inside one run, not the agent's paper account — the organism's own portfolio
#: is the only thing that tracks what an agent actually has.
SEARCH_CAPITAL_USD = 500.0


class SearchRefused(Exception):
    """The search was not attempted: a bad window, an unknown dataset, an invalid base."""


@dataclass
class Evidence:
    """What comes back to the agent. Plain data, no authority."""

    run_id: int
    base: dict
    candidates: list[dict] = field(default_factory=list)
    refused: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "base": self.base,
            "candidates": self.candidates,
            "refused": self.refused,
            "summary": self.summary,
        }


@dataclass
class OwnStrategy:
    """What the agent is searching around, and where it came from."""

    document: dict | None
    strategy_name: str | None = None
    genome_revision: int | None = None


def current_strategy(session, agent_uuid: str) -> OwnStrategy:
    """The strategy spec this agent would search around, read from the organism.

    An agent's `TradingGenome` is policy prose — thesis, sizing rule, risk limits — and
    its schema forbids extra keys, so there are no replayable strategy parameters inside
    it to search. The executable artifact is an `evo_strategies` row: the one the trading
    genome names as active, else the agent's most recent active/validated spec. That is
    the thing a replay can run and a mutation can move.

    `genome_revision` is the agent's trading-genome revision at the moment of the search
    — the attribution the run is stamped with, so a search is always locatable against
    the version of the agent that asked for it."""
    genome = session.execute(
        select(EvoGenome)
        .where(EvoGenome.agent_uuid == agent_uuid, EvoGenome.kind == "trading")
        .order_by(EvoGenome.revision.desc())
        .limit(1)
    ).scalars().first()
    genome_revision = genome.revision if genome is not None else None
    named = (genome.document_json or {}).get("active_strategy_name") if genome else None

    def _latest(*conditions):
        return session.execute(
            select(EvoStrategy)
            .where(EvoStrategy.agent_uuid == agent_uuid, *conditions)
            .order_by(EvoStrategy.revision.desc(), EvoStrategy.id.desc())
            .limit(1)
        ).scalars().first()

    # The strategy the trading genome names, then whatever is deployed, then the most
    # recent one that at least validates. Anything drafted, rejected or deactivated is
    # not what this agent is running, so it is not what it should be searching around.
    row = (
        (_latest(EvoStrategy.name == str(named)) if named else None)
        or _latest(EvoStrategy.status == "active")
        or _latest(EvoStrategy.status == "validated")
    )
    if row is None:
        return OwnStrategy(None, None, genome_revision)
    return OwnStrategy(dict(row.spec_json or {}), row.name, genome_revision)


def _score(
    *, replayed: replay.ReplayResult, weights: dict, scales: dict, min_trades: int
) -> tuple[dict, float | None, str, str]:
    """Score one replayed variant. Returns (components, score, evidence_class, note)."""
    outcome, led = replayed.outcome, replayed.ledger
    evidence_class, note = fitness_mod.classify_evidence(
        run_status="completed",
        integrity=replayed.integrity,
        n_trades=int(outcome.get("n_trades") or 0),
        min_trades=min_trades,
    )
    if evidence_class == fitness_mod.EVIDENCE_INVALID:
        return {}, None, evidence_class, note
    comps, score = fitness_mod.compute(
        outcome=outcome,
        ledger={
            "realized_pnl_usd": led.realized_pnl_usd,
            "turnover_usd": led.turnover_usd,
            "max_drawdown_usd": led.max_drawdown_usd,
            "concentration_hhi": led.concentration_hhi,
            "concentration_top_family": led.concentration_top_family,
            "return_on_capital": led.return_on_capital,
        },
        integrity=replayed.integrity,
        trade_cents=[
            float(t["cents_per_contract"])
            for t in replayed.trades
            if t.get("cents_per_contract") is not None
        ],
        starting_capital_usd=SEARCH_CAPITAL_USD,
        weights=weights,
        scales=scales,
    )
    return fitness_mod.components_payload(comps), score, evidence_class, note


def _persist_candidate(
    session,
    *,
    run: EvoSearchRun,
    idx: int,
    is_base: bool,
    document: dict | None,
    replayed: replay.ReplayResult | None,
    proposal: mutation.MutationProposal | None = None,
    admission: mutation.Admission | None = None,
    distance_from_base: float | None = None,
    components: dict | None = None,
    score: float | None = None,
    evidence_class: str | None = None,
    evidence_note: str | None = None,
    persist_trades: bool = True,
) -> EvoSearchCandidate:
    row = EvoSearchCandidate(
        run_id=run.id,
        idx=idx,
        is_base=is_base,
        genome_hash=genome_mod.genome_hash(document) if document else None,
        document_json=document,
        distance_from_base=distance_from_base,
        mutation_source=proposal.source if proposal else ("base" if is_base else None),
        mutation_kind=proposal.kind if proposal else None,
        mutation_diff_json=proposal.as_changes_json() if proposal else None,
        hypothesis=proposal.hypothesis if proposal else None,
        admitted=bool(admission.ok) if admission is not None else is_base,
        reject_stage=admission.stage if admission is not None else None,
        reject_reason=admission.reason if admission is not None else None,
        nearest_distance=admission.nearest_distance if admission is not None else None,
        outcome_json=replayed.outcome if replayed else None,
        ledger_json=(
            {
                "realized_pnl_usd": replayed.ledger.realized_pnl_usd,
                "fees_usd": replayed.ledger.fees_usd,
                "turnover_usd": replayed.ledger.turnover_usd,
                "max_drawdown_usd": replayed.ledger.max_drawdown_usd,
                "peak_exposure_usd": replayed.ledger.peak_exposure_usd,
                "max_concurrent_positions": replayed.ledger.max_concurrent_positions,
                "concurrency_coverage": replayed.ledger.concurrency_coverage,
                "contracts": replayed.ledger.contracts,
                "markets": replayed.ledger.markets,
                "return_on_capital": replayed.ledger.return_on_capital,
            }
            if replayed
            else None
        ),
        n_trades=int((replayed.outcome.get("n_trades") if replayed else 0) or 0),
        net_pnl_usd=replayed.ledger.realized_pnl_usd if replayed else None,
        max_drawdown_usd=replayed.ledger.max_drawdown_usd if replayed else None,
        search_score=score,
        score_components_json=components,
        evidence_class=evidence_class,
        evidence_note=evidence_note,
    )
    session.add(row)
    session.flush()

    if replayed is not None and persist_trades:
        for t in replayed.trades:
            session.add(
                EvoSearchTrade(
                    candidate_id=row.id,
                    market_ticker=str(t.get("ticker") or "")[:128],
                    event_root=replay._event_root(str(t.get("ticker") or ""))[:64],
                    month=str(t.get("month") or "")[:7] or None,
                    side=str(t.get("side") or "")[:4] or None,
                    style=str(t.get("style") or "")[:8] or None,
                    quantity=int(t.get("quantity") or 0),
                    entry_price_cents=t.get("entry_price_cents"),
                    exit_price_cents=t.get("exit_price_cents"),
                    entered_at=replay._ts(t.get("entered_at")),
                    exited_at=replay._ts(t.get("exited_at")),
                    exit_time_exact=bool(t.get("exit_time_exact", True)),
                    fees_usd=float(t.get("fees") or 0.0),
                    pnl_usd=float(t.get("pnl") or 0.0),
                    cents_per_contract=t.get("cents_per_contract"),
                    maker_yes_c=t.get("maker_yes_c"),
                    exit_reason=str(t.get("exit") or "")[:32] or None,
                    settled=bool(t.get("settled")),
                    win=bool(t.get("win")),
                )
            )
        session.flush()
    return row


def run_search(
    session,
    settings: EvoSettings,
    *,
    agent_uuid: str,
    base_spec: dict | None = None,
    dataset: str = "backfill_weather",
    window_start: str | None = None,
    window_end: str | None = None,
    data_cutoff: str | None = None,
    dimensions: list[str] | None = None,
    neighbourhood: int = DEFAULT_NEIGHBOURHOOD,
    explore_fraction: float = 0.5,
    min_trades: int = 30,
    min_distance: float = 0.02,
    seed: int = 0,
    cohort_id: int | None = None,
    heartbeat_id: int | None = None,
    genome_revision: int | None = None,
    persist_trades: bool = True,
) -> Evidence:
    """Replay the base genome and a bounded neighbourhood, and return the evidence.

    `base_spec` defaults to the agent's own active strategy. `dimensions` narrows
    the search to particular genes, which is how an agent points the tool at the question
    it actually has ("does my entry band matter?") rather than at the whole surface."""
    started = time.monotonic()

    base_strategy_name = None
    if base_spec is None:
        own = current_strategy(session, agent_uuid)
        genome_revision = (
            genome_revision if genome_revision is not None else own.genome_revision
        )
        base_strategy_name = own.strategy_name
        base_spec = own.document
        if base_spec is None:
            raise SearchRefused(
                "you have no saved strategy to search around — save_strategy first, or "
                "pass an explicit spec"
            )

    base_doc, err = genome_mod.validate(base_spec)
    if err or base_doc is None:
        raise SearchRefused(f"invalid base genome: {err}")

    allowed = [
        p for p in (dimensions or genome_mod.MUTABLE_PATHS)
        if p in genome_mod.GENES_BY_PATH
    ]
    if dimensions and not allowed:
        raise SearchRefused(f"none of {dimensions} are genes on the mutation surface")
    n = max(1, min(int(neighbourhood), MAX_NEIGHBOURHOOD))

    weights = fitness_mod.resolve_weights(None)
    scales = fitness_mod.resolve_scales(None)

    # --- the base run: everything else is measured against this --------------
    base_replay = replay.replay(
        session,
        settings,
        document=base_doc,
        dataset=dataset,
        window_start=window_start,
        window_end=window_end,
        data_cutoff=data_cutoff,
        starting_capital_usd=SEARCH_CAPITAL_USD,
    )
    run = EvoSearchRun(
        agent_uuid=agent_uuid,
        cohort_id=cohort_id,
        heartbeat_id=heartbeat_id,
        genome_revision=genome_revision,
        base_genome_hash=genome_mod.genome_hash(base_doc),
        base_strategy_name=base_strategy_name,
        dataset=dataset,
        provenance=base_replay.reproducibility.get("provenance"),
        window_start=base_replay.reproducibility.get("window_start"),
        window_end=base_replay.reproducibility.get("window_end"),
        data_cutoff=base_replay.reproducibility.get("data_cutoff"),
        policy_json={
            "dimensions": allowed,
            "neighbourhood": n,
            "explore_fraction": explore_fraction,
            "min_trades": min_trades,
            "min_distance": min_distance,
            "seed": seed,
            "search_capital_usd": SEARCH_CAPITAL_USD,
        },
        reproducibility_json=dict(
            base_replay.reproducibility, search_revision=SEARCH_REVISION
        ),
        integrity_json=base_replay.integrity,
    )
    session.add(run)
    session.flush()

    comps, score, ev_class, note = _score(
        replayed=base_replay, weights=weights, scales=scales, min_trades=min_trades
    )
    base_row = _persist_candidate(
        session, run=run, idx=0, is_base=True, document=base_doc,
        replayed=base_replay, components=comps, score=score,
        evidence_class=ev_class, evidence_note=note, persist_trades=persist_trades,
    )

    # --- the neighbourhood ---------------------------------------------------
    seen: list[dict] = [base_doc]
    admitted_rows: list[EvoSearchCandidate] = []
    refused: list[dict] = []
    proposals_made = 0

    for index in range(n):
        kind = (
            mutation.KIND_EXPLORE
            if (index / max(1, n)) >= (1.0 - explore_fraction)
            else mutation.KIND_EXPLOIT
        )
        proposal = mutation.propose_perturbation(
            agent_uuid=agent_uuid,
            base_revision=int(genome_revision or 0),
            document=base_doc,
            allowed_paths=allowed,
            kind=kind,
            seed=seed,
            index=index,
            max_genes=2,
        )
        if proposal is None:
            continue
        proposals_made += 1

        admission = mutation.evaluate_proposal_document(
            proposal,
            parent_document=base_doc,
            existing_documents=seen,
            allowed_paths=allowed,
            min_distance=min_distance,
        )
        if not admission.ok or admission.document is None:
            refused.append(
                {
                    "changes": proposal.as_changes_json(),
                    "stage": admission.stage,
                    "reason": admission.reason,
                }
            )
            _persist_candidate(
                session, run=run, idx=index + 1, is_base=False, document=None,
                replayed=None, proposal=proposal, admission=admission,
            )
            continue

        variant = admission.document
        seen.append(variant)
        try:
            replayed = replay.replay(
                session, settings, document=variant, dataset=dataset,
                window_start=window_start, window_end=window_end,
                data_cutoff=data_cutoff, starting_capital_usd=SEARCH_CAPITAL_USD,
            )
        except replay.ReplayRefused as exc:
            refused.append(
                {"changes": proposal.as_changes_json(), "stage": "replay", "reason": str(exc)}
            )
            _persist_candidate(
                session, run=run, idx=index + 1, is_base=False, document=variant,
                replayed=None, proposal=proposal,
                admission=mutation.Admission(False, "replay", str(exc)),
            )
            continue

        comps, score, ev_class, note = _score(
            replayed=replayed, weights=weights, scales=scales, min_trades=min_trades
        )
        admitted_rows.append(
            _persist_candidate(
                session, run=run, idx=index + 1, is_base=False, document=variant,
                replayed=replayed, proposal=proposal, admission=admission,
                distance_from_base=genome_mod.distance(base_doc, variant),
                components=comps, score=score, evidence_class=ev_class,
                evidence_note=note, persist_trades=persist_trades,
            )
        )

    # --- rank, inside this run only -----------------------------------------
    # Adequate variants rank against each other. Thin or invalid ones stay unranked
    # because a three-trade sample cannot order a STRATEGY — this is a property of the
    # measurement, and it is not, and must not become, an agent-selection rule.
    rankable = [
        r for r in admitted_rows
        if r.evidence_class == fitness_mod.EVIDENCE_ADEQUATE and r.search_score is not None
    ]
    rankable.sort(key=lambda r: -(r.search_score or 0.0))
    for position, row in enumerate(rankable, start=1):
        row.rank = position
    run.proposals_made = proposals_made
    run.proposals_admitted = len(admitted_rows)
    run.candidates_replayed = len(admitted_rows) + 1
    run.elapsed_ms = int((time.monotonic() - started) * 1000)
    session.flush()

    return Evidence(
        run_id=run.id,
        base=_candidate_view(base_row),
        candidates=[_candidate_view(r) for r in rankable]
        + [
            _candidate_view(r)
            for r in admitted_rows
            if r.evidence_class != fitness_mod.EVIDENCE_ADEQUATE
        ],
        refused=refused,
        summary=_summarize(base_row, admitted_rows, rankable, run),
    )


def _candidate_view(row: EvoSearchCandidate) -> dict:
    """One candidate as the agent sees it: what changed, what it did, and why it scored
    that way. The component breakdown is included so the agent can reason about the
    trade-off rather than defer to a number."""
    return {
        "candidate_id": row.id,
        "is_base": row.is_base,
        "rank": row.rank,
        "genome_hash": (row.genome_hash or "")[:16],
        "changes": [
            f"{c.get('path')} {c.get('from')} → {c.get('to')}"
            for c in (row.mutation_diff_json or [])
        ],
        "hypothesis": row.hypothesis,
        "distance_from_base": row.distance_from_base,
        "n_trades": row.n_trades,
        "net_pnl_usd": float(row.net_pnl_usd) if row.net_pnl_usd is not None else None,
        "max_drawdown_usd": (
            float(row.max_drawdown_usd) if row.max_drawdown_usd is not None else None
        ),
        "per_trade_cents_per_contract": (row.outcome_json or {}).get(
            "per_trade_cents_per_contract"
        ),
        "realizable_cents_per_contract": (row.outcome_json or {}).get(
            "realizable_cents_per_contract"
        ),
        "search_score": row.search_score,
        "evidence_class": row.evidence_class,
        "evidence_note": row.evidence_note,
        "why": fitness_mod.explain(row.score_components_json),
        "document": row.document_json,
    }


def _summarize(base_row, admitted_rows, rankable, run) -> dict:
    best = rankable[0] if rankable else None
    base_score = base_row.search_score
    return {
        "dataset": run.dataset,
        "window": [run.window_start, run.window_end],
        "proposals_made": run.proposals_made,
        "proposals_admitted": run.proposals_admitted,
        "ranked": len(rankable),
        "held_thin_evidence": sum(
            1 for r in admitted_rows if r.evidence_class == fitness_mod.EVIDENCE_INSUFFICIENT
        ),
        "invalid": sum(
            1 for r in admitted_rows if r.evidence_class == fitness_mod.EVIDENCE_INVALID
        ),
        "base_search_score": base_score,
        "best_search_score": best.search_score if best else None,
        "best_beats_base": (
            bool(best and base_score is not None and best.search_score > base_score)
            if best
            else False
        ),
        # The one sentence the agent most needs, stated as a finding and not a
        # recommendation: the tool measured, the agent decides.
        "finding": _finding(base_row, best),
    }


def _finding(base_row, best) -> str:
    if best is None:
        return (
            "No variant produced adequate evidence over this window. That is a fact about "
            "the window or the neighbourhood, not about the base genome."
        )
    if base_row.search_score is None:
        return (
            f"The base genome had {base_row.evidence_class} evidence here "
            f"({base_row.evidence_note}); {best.search_score:.4f} was the best variant, "
            "but there is no base to compare it against."
        )
    delta = best.search_score - base_row.search_score
    if delta <= 0:
        return (
            f"No variant beat the base genome ({base_row.search_score:.4f}); best was "
            f"{best.search_score:.4f}. The neighbourhood searched offers no improvement "
            "on this window."
        )
    changes = ", ".join(
        f"{c.get('path')} {c.get('from')} → {c.get('to')}"
        for c in (best.mutation_diff_json or [])
    )
    return (
        f"Best variant scores {best.search_score:.4f} vs the base's "
        f"{base_row.search_score:.4f} (+{delta:.4f}) by changing {changes}, on "
        f"n={best.n_trades} trades. One window is one window — decide whether that is a "
        "reason to revise."
    )


def recent_searches(session, agent_uuid: str, *, limit: int = 5) -> list[dict]:
    """This agent's recent searches, for the heartbeat context — so it can see what it
    has already asked rather than asking again."""
    rows = session.execute(
        select(EvoSearchRun)
        .where(EvoSearchRun.agent_uuid == agent_uuid)
        .order_by(EvoSearchRun.id.desc())
        .limit(limit)
    ).scalars()
    return [
        {
            "run_id": r.id,
            "dataset": r.dataset,
            "window": [r.window_start, r.window_end],
            "dimensions": (r.policy_json or {}).get("dimensions"),
            "admitted": r.proposals_admitted,
            "genome_revision": r.genome_revision,
        }
        for r in rows
    ]


__all__ = [
    "DEFAULT_NEIGHBOURHOOD",
    "Evidence",
    "MAX_NEIGHBOURHOOD",
    "OwnStrategy",
    "SEARCH_CAPITAL_USD",
    "SEARCH_REVISION",
    "SearchRefused",
    "current_strategy",
    "recent_searches",
    "run_search",
]
