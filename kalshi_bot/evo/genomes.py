"""Cognitive and trading-policy genome schemas + immutable versioning (spec §7.2/7.3).

Genomes are JSON documents validated by pydantic models. Every material change
inserts a new immutable evo_genomes revision (hypothesis/evidence/rollback recorded);
rollback inserts a NEW revision copying an older document. The agent row caches the
current revision number.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select

from .audit import audit
from .models import EvoAgent, EvoGenome

MAX_TEXT = 2000  # bound on free-text genome fields (keeps prompts + rows sane)


class _Bounded(BaseModel):
    model_config = {"extra": "forbid"}

    @field_validator("*", mode="before")
    @classmethod
    def _clip_text(cls, v: object) -> object:
        if isinstance(v, str) and len(v) > MAX_TEXT:
            return v[:MAX_TEXT]
        return v


# ---------------------------------------------------------------------------
# Cognitive genome
# ---------------------------------------------------------------------------


class ReviewPolicy(_Bounded):
    heartbeat_procedure: str = (
        "Check triggered listeners first, then open positions and recent fills, then "
        "market state for my universe, then whether any belief is contradicted."
    )
    information_priorities: list[str] = Field(
        default_factory=lambda: ["listener_events", "positions", "markets", "peers", "graveyard"],
        max_length=10,
    )
    peer_review_every_n_heartbeats: int = Field(default=6, ge=1, le=100)
    self_review_every_n_heartbeats: int = Field(default=12, ge=1, le=200)
    graveyard_check_before_new_strategy: bool = True
    wake_conditions: str = (
        "Wake me early for: a position moving >10c against me, a listener firing on my "
        "entry conditions, or a data-health error on a source I depend on."
    )


class ResearchPolicy(_Bounded):
    methodology: str = (
        "Form one falsifiable hypothesis at a time; prefer settled-history replay over "
        "intuition; require pre-registered promotion and kill criteria."
    )
    hypothesis_generation: str = (
        "Start from observed mispricings and graveyard gaps, not from what peers are "
        "currently winning with."
    )
    experiment_selection: str = "Highest expected information per sandbox run first."
    evidence_evaluation: str = (
        "Weight settled outcomes over marks; discount n<20 samples; separate provenance."
    )
    confidence_updating: str = "Move confidence gradually; a single trade never settles a thesis."
    conflict_resolution: str = (
        "When evidence conflicts, prefer the larger, more recent, better-provenance sample."
    )
    retest_failed_ideas: str = (
        "Only with a material difference: new data, new market, regime change, or a "
        "corrected defect. Document the difference against the graveyard entry."
    )
    signal_vs_noise: str = "Require the effect to survive fees, spread, and a date split."


class AdaptationPolicy(_Bounded):
    strategy_change_threshold: str = (
        "Revise only on pre-registered criteria or >=20 settled trades of contrary evidence."
    )
    thrashing_resistance: str = (
        "Never revise on a single loss. Respect the cooldown. Let experiments conclude."
    )
    rollback_policy: str = (
        "Roll back when the revision's own rollback conditions trigger; keep the lesson."
    )
    weakness_identification: str = (
        "Weekly: rank my loss sources by dollars and by decision type; attack the largest."
    )
    improvement_selection: str = "One material improvement at a time; measure before stacking."
    opportunity_cost: str = (
        "Idle capital is a choice: justify no-trade states against declared opportunities."
    )


class MemoryPolicy(_Bounded):
    retrieval: str = (
        "Pull memories tagged to the current market/strategy first, then contradictions, "
        "then recent journals; never load everything."
    )
    compression: str = "Summarize episodes older than a cohort into belief updates."
    retention: str = "Keep all facts forever (system-enforced); prune only derived summaries."


class AttentionPolicy(_Bounded):
    listener_creation: str = (
        "Create a listener for every condition I would otherwise poll for; give each an "
        "expected value and an expiry."
    )
    trigger_prioritization: str = (
        "Spend triggered heartbeats on entry/exit conditions, not curiosity."
    )


class ResourcePolicy(_Bounded):
    llm_budget_allocation: str = (
        "Reserve deep-model calls for reflection and material revisions; keep routine "
        "heartbeats terse."
    )
    tool_call_allocation: str = "Prefer one good query over five vague ones."
    compute_allocation: str = "Backtests before live changes; cap parameter sweeps."


class SocialPolicy(_Bounded):
    peer_observation: str = (
        "Review the delayed leaderboard and peer revisions; look for mechanisms, not ranks."
    )
    copy_vs_ignore: str = (
        "Copy only what I can explain and verify in the sandbox; record the influence "
        "and my modification. Never copy a temporarily lucky peer blindly."
    )
    graveyard_procedure: str = (
        "Before any materially new strategy, search the graveyard and document the "
        "material difference if a similar idea died."
    )


class CognitiveGenome(_Bounded):
    kind: Literal["cognitive"] = "cognitive"
    review: ReviewPolicy = Field(default_factory=ReviewPolicy)
    research: ResearchPolicy = Field(default_factory=ResearchPolicy)
    adaptation: AdaptationPolicy = Field(default_factory=AdaptationPolicy)
    memory_policy: MemoryPolicy = Field(default_factory=MemoryPolicy)
    attention: AttentionPolicy = Field(default_factory=AttentionPolicy)
    resources: ResourcePolicy = Field(default_factory=ResourcePolicy)
    social: SocialPolicy = Field(default_factory=SocialPolicy)
    regime_detection: str = (
        "Track realized vs implied volatility of my markets and my hit rate by regime tag; "
        "declare a regime change only with evidence across multiple markets."
    )
    data_source_evaluation: str = (
        "Score sources on latency, coverage and settled-outcome accuracy before depending "
        "on them; register operational sources."
    )
    failure_analysis: str = (
        "For each loss: was it thesis, execution, data, or variance? Log the class."
    )


# ---------------------------------------------------------------------------
# Trading genome
# ---------------------------------------------------------------------------


class MarketUniverse(_Bounded):
    categories: list[str] = Field(default_factory=list, max_length=12)
    series_prefixes: list[str] = Field(default_factory=list, max_length=24)
    exclude_series_prefixes: list[str] = Field(default_factory=list, max_length=24)
    min_volume: float = Field(default=100.0, ge=0)
    min_open_interest: float = Field(default=0.0, ge=0)
    max_spread_cents: int = Field(default=10, ge=1, le=99)
    min_hours_to_close: float = Field(default=0.0, ge=0)
    max_hours_to_close: float = Field(default=24 * 14, ge=0)


class RiskLimits(_Bounded):
    max_position_cost_usd: float = Field(default=100.0, gt=0)
    max_event_cost_usd: float = Field(default=150.0, gt=0)
    max_category_fraction: float = Field(default=0.5, gt=0, le=1.0)
    max_open_positions: int = Field(default=25, ge=1, le=200)
    max_daily_new_risk_usd: float = Field(default=300.0, gt=0)


class TradingGenome(_Bounded):
    kind: Literal["trading"] = "trading"
    strategy_family: str = "unassigned"
    thesis: str = "No thesis yet; research before trading."
    falsifiable_predictions: list[str] = Field(default_factory=list, max_length=8)
    universe: MarketUniverse = Field(default_factory=MarketUniverse)
    data_dependencies: list[str] = Field(default_factory=list, max_length=12)
    data_freshness_seconds: int = Field(default=600, ge=30)
    forecasting_method: str = "market-implied until a model is validated"
    fair_value_method: str = "none"
    probability_model: str = "none"
    entry_rules: str = "Do not enter until a validated strategy spec is active."
    exit_rules: str = "Hold to settlement unless the strategy spec says otherwise."
    holding_period: str = "to settlement"
    no_trade_rules: str = "No valid signal, stale data, or budget exhausted => no trade."
    order_style: Literal["taker", "maker"] = "taker"
    fill_assumptions: str = "Taker at displayed ask, capped by depth; maker fills need trade-through."
    sizing_rule: str = "fixed_cost"
    sizing_value: float = Field(default=25.0, gt=0)  # dollars of cost per entry
    confidence_threshold: float = Field(default=0.0, ge=0, le=1)
    opportunity_selection: str = "Highest net expected value per dollar of collateral."
    risk: RiskLimits = Field(default_factory=RiskLimits)
    active_strategy_name: str | None = None  # evo_strategies.name currently active
    revision_hypothesis: str | None = None
    rollback_conditions: str | None = None


def validate_genome(kind: str, document: dict) -> tuple[dict | None, str | None]:
    """Validate a genome document. Returns (normalized_doc, None) or (None, error)."""
    try:
        if kind == "cognitive":
            return CognitiveGenome.model_validate(document).model_dump(), None
        if kind == "trading":
            return TradingGenome.model_validate(document).model_dump(), None
        return None, f"unknown genome kind {kind!r}"
    except ValidationError as exc:
        return None, str(exc)[:1500]


def default_cognitive() -> dict:
    return CognitiveGenome().model_dump()


def default_trading() -> dict:
    return TradingGenome().model_dump()


# ---------------------------------------------------------------------------
# Versioning operations
# ---------------------------------------------------------------------------


def current_genome(session, agent_uuid: str, kind: str) -> EvoGenome | None:
    return session.scalar(
        select(EvoGenome)
        .where(EvoGenome.agent_uuid == agent_uuid, EvoGenome.kind == kind)
        .order_by(EvoGenome.revision.desc())
        .limit(1)
    )


def changed_fields(old: dict, new: dict, prefix: str = "") -> list[str]:
    out: list[str] = []
    keys = set(old) | set(new)
    for k in sorted(keys):
        path = f"{prefix}{k}"
        a, b = old.get(k), new.get(k)
        if isinstance(a, dict) and isinstance(b, dict):
            out.extend(changed_fields(a, b, prefix=f"{path}."))
        elif a != b:
            out.append(path)
    return out


def write_genome_revision(
    session,
    agent: EvoAgent,
    kind: str,
    document: dict,
    *,
    hypothesis: str | None = None,
    evidence_for: str | None = None,
    evidence_against: str | None = None,
    expected_benefit: str | None = None,
    potential_downside: str | None = None,
    rollback_conditions: str | None = None,
    heartbeat_id: int | None = None,
    material: bool = True,
    is_rollback: bool = False,
    rollback_of_revision: int | None = None,
) -> tuple[EvoGenome | None, str | None]:
    """Insert the next immutable revision. Returns (row, None) or (None, error)."""
    normalized, err = validate_genome(kind, document)
    if err:
        return None, err
    prev = current_genome(session, agent.agent_uuid, kind)
    prev_rev = prev.revision if prev else 0
    diff = changed_fields(prev.document_json if prev else {}, normalized)
    if prev is not None and not diff:
        return None, "no fields changed"
    row = EvoGenome(
        created_at=datetime.now(timezone.utc),
        agent_uuid=agent.agent_uuid,
        kind=kind,
        revision=prev_rev + 1,
        parent_revision=prev_rev or None,
        document_json=normalized,
        changed_fields_json=diff or None,
        hypothesis=hypothesis,
        evidence_for=evidence_for,
        evidence_against=evidence_against,
        expected_benefit=expected_benefit,
        potential_downside=potential_downside,
        rollback_conditions=rollback_conditions,
        is_rollback=is_rollback,
        rollback_of_revision=rollback_of_revision,
        material=material,
        heartbeat_id=heartbeat_id,
    )
    session.add(row)
    if kind == "cognitive":
        agent.cognitive_genome_rev = row.revision
    else:
        agent.trading_genome_rev = row.revision
    session.flush()
    audit(
        session,
        f"genome_revision_{kind}",
        agent_uuid=agent.agent_uuid,
        heartbeat_id=heartbeat_id,
        revision=row.revision,
        changed=diff[:40],
        material=material,
        rollback=is_rollback,
    )
    return row, None


def rollback_genome(
    session,
    agent: EvoAgent,
    kind: str,
    to_revision: int,
    *,
    reason: str,
    heartbeat_id: int | None = None,
) -> tuple[EvoGenome | None, str | None]:
    """Emergency rollback: a NEW revision that copies an older document."""
    target = session.scalar(
        select(EvoGenome).where(
            EvoGenome.agent_uuid == agent.agent_uuid,
            EvoGenome.kind == kind,
            EvoGenome.revision == to_revision,
        )
    )
    if target is None:
        return None, f"revision {to_revision} not found"
    return write_genome_revision(
        session,
        agent,
        kind,
        target.document_json,
        hypothesis=f"rollback to r{to_revision}: {reason}"[:MAX_TEXT],
        heartbeat_id=heartbeat_id,
        material=False,  # rollbacks are always permitted (emergency path)
        is_rollback=True,
        rollback_of_revision=to_revision,
    )
