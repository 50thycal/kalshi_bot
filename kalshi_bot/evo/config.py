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

    # --- OpenAI-compatible backend for routine heartbeats ---
    # Optional: route the "routine" alias to any OpenAI-compatible /chat/completions
    # server instead of Anthropic. Two shapes, same code path:
    #   1. A self-hosted server (Ollama / llama.cpp / vLLM) — no api key, leave the
    #      cost rates at 0.0, so compute (token caps) is the only constraint.
    #   2. A hosted inference API/router (OpenRouter, Groq, ...) — set an api key
    #      and the per-Mtok cost rates; real cost is then tracked and the weekly
    #      dollar ceiling is enforced just like the Anthropic path. OpenRouter is
    #      the practical default here: it fronts many providers behind one endpoint
    #      with no low per-request tokens-per-minute ceiling (Groq's free tier caps
    #      a single request well below what a routine heartbeat's ~9-10K-token
    #      context needs, before output is even counted).
    # "deep" heartbeats (reflection/birth/cohort_end/retirement) always stay on
    # Anthropic: low volume, highest stakes, exactly where quality matters most.
    # (The env vars keep the EVO_LOCAL_LLM_* names for continuity even though a
    # hosted provider isn't "local" — they mean "the OpenAI-compatible routine
    # backend".)
    local_llm_enabled: bool = False
    local_llm_base_url: str = ""  # e.g. "http://ollama.railway.internal:11434/v1", "https://openrouter.ai/api/v1", or "https://api.groq.com/openai/v1"
    local_llm_model: str = ""  # model id as the server expects (e.g. "meta-llama/llama-3.1-8b-instruct" on OpenRouter)
    local_llm_timeout_seconds: float = 180.0  # generous: covers slow CPU generation; a hosted GPU/router API returns in seconds
    local_llm_api_key: str = ""  # EVO_LOCAL_LLM_API_KEY — bearer token for a hosted provider; empty for a self-hosted server that needs no auth
    # Per-million-token cost of the routine backend. Both 0.0 => treated as free
    # (self-hosted): no dollar cost recorded, dollar ceiling skipped. Any value > 0
    # => a paid provider: real cost is booked to evo_llm_usage and charged against
    # the weekly llm_cost_usd ceiling. Groq llama-3.1-8b-instant: 0.05 in / 0.08 out.
    local_llm_input_cost_per_mtok: float = 0.0   # EVO_LOCAL_LLM_INPUT_COST_PER_MTOK
    local_llm_output_cost_per_mtok: float = 0.0  # EVO_LOCAL_LLM_OUTPUT_COST_PER_MTOK

    # --- other per-cohort resource budgets ---
    weekly_tool_calls: int = 2000
    weekly_sandbox_runs: int = 50
    weekly_data_reads: int = 300  # inspect_data pulls over our collected data tables
    weekly_market_scans: int = 150  # explore_markets on-demand live Kalshi API discovery

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
    # An order that never fills and can get NO live quote at all for this many hours
    # since submission is treated as unfillable (an invalid/untradable ticker, e.g. a
    # bare series prefix) and auto-rejected with a reason — instead of sitting "open"
    # forever giving the agent zero feedback to learn from. The agent-facing submit
    # gate rejects obviously-unquotable tickers immediately; this is the backstop for
    # anything that slips through or goes permanently dark. Fill-aware: a partially
    # filled order is never auto-rejected, so a real market that briefly loses its
    # book cannot trip it.
    order_no_quote_reject_hours: float = 6.0

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
        "data_reads": float(s.weekly_data_reads),
        "market_scans": float(s.weekly_market_scans),
        "triggered_heartbeats": float(s.triggered_heartbeats_per_week),
    }
