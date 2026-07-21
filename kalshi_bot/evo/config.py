"""Evo system configuration (env-driven, fail-closed, all spec defaults).

Separate from the main Settings class so the legacy worker's config surface is
untouched. Every important value here is configurable and versioned: the effective
config (plus the constitution hash and fitness weights) is snapshotted into
evo_config_versions whenever it changes, and cohorts record the version they ran
under (see constitution.ensure_config_version).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class EvoSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_prefix="EVO_",
    )

    # --- population / cohort (spec §4) ---
    population_size: int = 30
    # Ops throttle (NOT a spec parameter): cap how many active agents actually run
    # live work per cycle — heartbeats (LLM spend), strategy execution (paper
    # trades), snapshots and interim fitness. 0 = no cap (all active agents run).
    # Set e.g. EVO_MAX_ACTIVE_AGENTS=3 to shrink the live footprint for end-to-end
    # testing without retiring anyone; the capped-out agents stay in the cohort,
    # dormant, and resume the moment the cap is lifted. Deterministic: the lowest-id
    # (earliest-created) agents run.
    max_active_agents: int = 0
    cohort_days: int = 7  # a cohort runs exactly this long from when it is born
    cohort_timezone: str = "America/Chicago"  # display/reporting only
    bottom_fraction: float = 0.30
    middle_fraction: float = 0.40
    top_fraction: float = 0.30
    starting_capital_usd: float = 1000.0
    wildcard_every_n_cohorts: int = 4  # 0 disables wildcards

    # --- heartbeats ---
    routine_heartbeats_per_day: int = 6
    deep_reflections_per_day: int = 1
    triggered_heartbeats_per_week: int = 20
    material_revisions_per_day: int = 2
    revision_cooldown_minutes: int = 240  # conservative default between material revisions
    heartbeat_stale_minutes: int = 30  # running->abandoned timeout

    # --- LLM routing / cost (spec §11, §20) ---
    model_routine: str = "claude-haiku-4-5-20251001"
    model_deep: str = "claude-sonnet-5"
    weekly_llm_ceiling_usd: float = 2.0  # per active agent
    heartbeat_max_output_tokens: int = 6400  # doubled to give headroom for batching multiple backtests/actions in one heartbeat
    reflection_max_output_tokens: int = 6000  # reflections are verbose; 4000 truncated mid-JSON
    heartbeat_max_input_tokens: int = 12000
    weekly_token_budget: int = 1_500_000  # per agent, input+output
    llm_timeout_seconds: float = 120.0

    # --- local (CPU) LLM backend for routine heartbeats ---
    # Optional: route the "routine" alias to a self-hosted OpenAI-compatible server
    # (Ollama / llama.cpp / vLLM) instead of Anthropic — zero marginal cost, so
    # compute becomes the binding constraint instead of the dollar ceiling. "deep"
    # heartbeats (reflection/birth/cohort_end/retirement) always stay on Anthropic:
    # low volume, highest stakes, exactly where quality matters most.
    local_llm_enabled: bool = False
    local_llm_base_url: str = ""  # e.g. "http://ollama.railway.internal:11434/v1"
    local_llm_model: str = ""  # model tag as the local server expects
    local_llm_timeout_seconds: float = 180.0  # CPU generation is slow

    # --- other per-cohort resource budgets ---
    weekly_tool_calls: int = 2000
    weekly_sandbox_runs: int = 50

    # --- leaderboard delay (spec §12) ---
    leaderboard_delay_hours: float = 6.0

    # --- listeners ---
    max_listeners_per_agent: int = 25
    listener_min_cooldown_seconds: int = 60

    # --- paper execution realism (spec §19) ---
    fill_latency_ms: int = 1500  # simulated intent->evaluation latency
    maker_adverse_selection_haircut: float = 0.25  # fraction of trade-through fills forfeited
    max_position_fraction: float = 0.25  # max cost basis per market as fraction of NAV
    stale_data_seconds: int = 600  # market data older than this fails closed

    # --- sandbox limits (spec §15) ---
    sandbox_max_rows: int = 200_000
    sandbox_max_seconds: float = 60.0
    strategy_spec_max_bytes: int = 40_000

    # --- fitness (spec §23) — weights must sum to 100 ---
    fitness_weight_profit: float = 35.0
    fitness_weight_consistency: float = 20.0
    fitness_weight_risk: float = 15.0
    fitness_weight_evidence: float = 10.0
    fitness_weight_forecast: float = 10.0
    fitness_weight_adaptive: float = 10.0
    selection_current_weight: float = 0.80
    selection_reliability_weight: float = 0.20
    reliability_decay: float = 0.75  # per-cohort recency decay of historical scores
    shrinkage_trades: float = 10.0  # Bayesian shrinkage pseudo-count on per-trade EV
    winsor_pct: float = 0.05

    # --- orchestrator ---
    cycle_seconds: int = 60
    markets_per_cycle: int = 150  # bound on market/orderbook fetches per cycle
    bootstrap_rng_seed: int = 20260718

    # --- ops safety ---
    enabled: bool = True  # master gate for the evo loop (not a performance kill switch:
    #                       infrastructure pause only, per spec §20)


@lru_cache
def get_evo_settings() -> EvoSettings:
    return EvoSettings()


def resource_allocations(s: EvoSettings) -> dict[str, float]:
    """Per-agent per-cohort budget rows created at cohort join (equal for everyone)."""
    return {
        "llm_cost_usd": s.weekly_llm_ceiling_usd,
        "tokens": float(s.weekly_token_budget),
        "tool_calls": float(s.weekly_tool_calls),
        "sandbox_runs": float(s.weekly_sandbox_runs),
        "triggered_heartbeats": float(s.triggered_heartbeats_per_week),
    }
