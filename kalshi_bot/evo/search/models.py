"""SQLAlchemy models for the historical search capability (`evo_search_*` tables).

This is a **capability the existing Evo agents invoke**, not a population. There is no
candidate, no generation, no cohort and no reproduction here: `evo_agents`, `evo_cohorts`,
`evo_genomes`, `evo_fitness`, `evo_births` and `evo_retirements` already own the organism's
lifecycle and remain authoritative. Every row below is an *artifact* of one agent asking
one question about its own strategy, and is attributable to that agent, that strategy
and the revision of the agent that asked.

Three tables, deliberately:

    EvoSearchRun         one invocation, by one agent, from one trading-genome revision
      └─ EvoSearchCandidate   the base genome and each neighbourhood point around it
           └─ EvoSearchTrade      that point's replayed trade tape

What the search returns is **evidence**. The agent reads it, reasons about it, and may
then adopt a variant through the organism's own `save_strategy` / `activate_strategy`.
Nothing here retires, promotes, ranks or reproduces an agent, and nothing here writes to
`evo_fitness` — the score on a candidate is search scoring, and it is not comparable with
an agent's authoritative cohort fitness (which is broader: adaptive intelligence,
historical reliability, opportunity capture).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...models import TS, Base, BigIntId, JSONType, utcnow

UUID_LEN = 36
HASH_LEN = 64


class EvoSearchRun(Base):
    """One agent's invocation of the historical search capability.

    `agent_uuid`, `genome_revision` and `base_strategy_name` are the attribution: this run
    exists because that agent, at that revision of itself, asked a question about that
    strategy. `heartbeat_id` and `cohort_id` place it in the organism's own timeline, so a
    search is visible in the same context as the agent's other actions rather than in a
    parallel one."""

    __tablename__ = "evo_search_runs"
    __table_args__ = (
        Index("ix_evo_search_runs_agent", "agent_uuid", "created_at"),
        Index("ix_evo_search_runs_heartbeat", "heartbeat_id"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)

    # --- attribution to the organism ---------------------------------------
    agent_uuid: Mapped[str] = mapped_column(String(UUID_LEN), nullable=False)
    cohort_id: Mapped[int | None] = mapped_column(BigIntId)
    heartbeat_id: Mapped[int | None] = mapped_column(BigIntId)
    #: The agent's `evo_genomes.revision` (kind='trading') when it asked. Attribution to
    #: the version of the agent, not the thing being searched — a trading genome is policy
    #: prose; the searchable spec is the `evo_strategies` row named below.
    genome_revision: Mapped[int | None] = mapped_column(Integer)

    # --- the question ------------------------------------------------------
    base_genome_hash: Mapped[str] = mapped_column(String(HASH_LEN), nullable=False)
    #: The `evo_strategies.name` the search started from, when the agent searched around
    #: its own saved strategy rather than a spec it passed in. The hash above is the
    #: exact content; this is the human-readable link back into the organism.
    base_strategy_name: Mapped[str | None] = mapped_column(String(48))
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    provenance: Mapped[str | None] = mapped_column(String(64))
    window_start: Mapped[str | None] = mapped_column(String(10))
    window_end: Mapped[str | None] = mapped_column(String(10))
    data_cutoff: Mapped[str | None] = mapped_column(String(10))
    #: Allowed gene surface, neighbourhood size, exploit/explore mix, seed.
    policy_json: Mapped[dict | None] = mapped_column(JSONType)

    # --- what happened -----------------------------------------------------
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="completed"
    )  # completed | refused | failed
    error: Mapped[str | None] = mapped_column(Text)
    proposals_made: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    proposals_admitted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidates_replayed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    reproducibility_json: Mapped[dict | None] = mapped_column(JSONType)
    integrity_json: Mapped[dict | None] = mapped_column(JSONType)
    elapsed_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class EvoSearchCandidate(Base):
    """One point in the neighbourhood — including the base genome itself, at index 0.

    A candidate is a *measurement*, not an organism. It has no state, no lifecycle and no
    successor: it records what one strategy document would have done over one window, and
    whether the mutation that produced it was even admissible.

    Refused proposals are kept alongside admitted ones. "We tried that and the gate said
    no, for this reason" is evidence about the search space, and without it the agent
    reproposes the same invalid mutation forever."""

    __tablename__ = "evo_search_candidates"
    __table_args__ = (
        Index("ix_evo_search_candidates_run", "run_id", "idx"),
        Index("ix_evo_search_candidates_hash", "genome_hash"),
    )

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("evo_search_runs.id"), nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    is_base: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # --- the strategy ------------------------------------------------------
    genome_hash: Mapped[str | None] = mapped_column(String(HASH_LEN))
    document_json: Mapped[dict | None] = mapped_column(JSONType)
    distance_from_base: Mapped[float | None] = mapped_column(Float)

    # --- how it was proposed, and whether the gates admitted it ------------
    mutation_source: Mapped[str | None] = mapped_column(String(32))
    mutation_kind: Mapped[str | None] = mapped_column(String(16))
    mutation_diff_json: Mapped[list | None] = mapped_column(JSONType)
    hypothesis: Mapped[str | None] = mapped_column(Text)
    admitted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reject_stage: Mapped[str | None] = mapped_column(String(24))
    reject_reason: Mapped[str | None] = mapped_column(Text)
    nearest_distance: Mapped[float | None] = mapped_column(Float)

    # --- what the replay measured -----------------------------------------
    outcome_json: Mapped[dict | None] = mapped_column(JSONType)
    ledger_json: Mapped[dict | None] = mapped_column(JSONType)
    n_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    net_pnl_usd: Mapped[float | None] = mapped_column(Numeric(14, 4))
    max_drawdown_usd: Mapped[float | None] = mapped_column(Numeric(14, 4))

    # --- search scoring, NOT agent fitness ---------------------------------
    #: Deliberately not written to `evo_fitness` and not comparable with it. This scores
    #: a strategy document over a replay window; an agent's authoritative fitness scores
    #: an organism over a cohort and includes dimensions no replay can see.
    search_score: Mapped[float | None] = mapped_column(Float)
    score_components_json: Mapped[dict | None] = mapped_column(JSONType)
    evidence_class: Mapped[str | None] = mapped_column(String(16))
    evidence_note: Mapped[str | None] = mapped_column(Text)
    rank: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, nullable=False)


class EvoSearchTrade(Base):
    """One replayed trade in a candidate's tape.

    The tape is what makes a search result explainable rather than merely rankable: an
    agent asking "why did this variant do better?" gets the trades, not a number."""

    __tablename__ = "evo_search_trades"
    __table_args__ = (Index("ix_evo_search_trades_candidate", "candidate_id", "id"),)

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(
        BigIntId, ForeignKey("evo_search_candidates.id"), nullable=False
    )
    market_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    event_root: Mapped[str | None] = mapped_column(String(64))
    month: Mapped[str | None] = mapped_column(String(7))
    side: Mapped[str | None] = mapped_column(String(4))
    style: Mapped[str | None] = mapped_column(String(8))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    entry_price_cents: Mapped[float | None] = mapped_column(Float)
    exit_price_cents: Mapped[float | None] = mapped_column(Float)
    entered_at: Mapped[datetime | None] = mapped_column(TS)
    exited_at: Mapped[datetime | None] = mapped_column(TS)
    #: False when `exited_at` is a lower bound rather than the real exit time — a
    #: settlement trade, where the replay only knows the last candle it observed.
    #: Concurrency and exposure accounting excludes these; see `replay.build_ledger`.
    exit_time_exact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    fees_usd: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    pnl_usd: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False, default=0)
    cents_per_contract: Mapped[float | None] = mapped_column(Float)
    maker_yes_c: Mapped[float | None] = mapped_column(Float)
    exit_reason: Mapped[str | None] = mapped_column(String(32))
    settled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    win: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


__all__ = ["EvoSearchCandidate", "EvoSearchRun", "EvoSearchTrade"]
