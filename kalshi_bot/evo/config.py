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
    # Ops control: the REAL size of the living population. 0 = no cap (use
    # population_size). Set e.g. EVO_MAX_ACTIVE_AGENTS=3 to run a small fleet.
    #
    # This was originally a display-order "throttle" that only filtered which
    # agents ran each cycle, leaving all `population_size` agents active in the
    # DB. That silently broke evolution: the filter took the LOWEST-id agents,
    # but reproduction creates children with the HIGHEST ids, so offspring could
    # never enter the live set. Observed live at cap=3 — all 9 generation-2
    # children sat at zero heartbeats and zero orders three days after birth,
    # while the same 3 founders ran forever. Selection was equally meaningless:
    # finalization ranked all 30 cohort members when only 3 had any recent
    # activity, so 27 were scored on nothing and the bottom 30% retired were
    # essentially arbitrary.
    #
    # It is now the population target itself: cohorts.reconcile_population()
    # keeps exactly this many agents `active`, so retirement/reproduction
    # fractions scale off the real number (cap=3 -> retire 1, keep 2, clone 1).
    max_active_agents: int = 0
    cohort_days: int = 7  # a cohort runs exactly this long from when it is born
    cohort_timezone: str = "America/Chicago"  # display/reporting only
    bottom_fraction: float = 0.30
    middle_fraction: float = 0.40
    top_fraction: float = 0.30
    starting_capital_usd: float = 1000.0
    wildcard_every_n_cohorts: int = 4  # 0 disables wildcards

    # --- heartbeats (three tiers, cheapest/most frequent first) ---
    # tier 1 "routine"   — small, hourly-ish: read the book, act on ready evidence.
    # tier 2 "deep"      — medium, every ~12h: reflection/introspection.
    # tier 3 "strategic" — large, every ~48h, plus every high-stakes lifecycle event
    #                      (birth / cohort_end / retirement). Stays on Anthropic.
    routine_heartbeats_per_day: int = 6
    deep_reflections_per_day: int = 2  # 2/day == every ~12h
    strategic_review_hours: float = 48.0  # 0 disables the periodic strategic beat
    triggered_heartbeats_per_week: int = 20
    material_revisions_per_day: int = 2
    revision_cooldown_minutes: int = 240  # conservative default between material revisions
    heartbeat_stale_minutes: int = 30  # running->abandoned timeout

    # --- LLM routing / cost (spec §11, §20) ---
    # One model alias per tier. Anthropic model ids here are only used when the
    # alias is NOT routed to the OpenAI-compatible backend (see local_llm_aliases).
    model_routine: str = "claude-haiku-4-5-20251001"
    model_deep: str = "claude-sonnet-5"
    model_strategic: str = "claude-sonnet-5"  # top tier stays on Anthropic by default
    weekly_llm_ceiling_usd: float = 2.0  # per active agent
    # Raised 6400->22000 to match heartbeat_max_input_tokens after live routine
    # heartbeats on deepseek/deepseek-v4-flash (a reasoning model) were observed
    # reporting completion_tokens up to 15384 — 2.4x the old 6400 cap — while
    # still finishing normally (status=completed, not truncated). The provider
    # is not treating our max_tokens as a strict ceiling on reasoning tokens, so
    # this number was never actually bounding generation; 22000 gives enough
    # room that observed behavior sits inside it with real headroom instead of
    # silently exceeding it. See LlmResult.stop_reason / EvoLlmUsage.stop_reason
    # for how enforcement is now verified going forward.
    heartbeat_max_output_tokens: int = 22000
    # Bumped 6000->16000: with the input-cap fix live, a reflection heartbeat on
    # z-ai/glm-5.2 (a reasoning model) finally dispatched for real and then burned
    # its ENTIRE 6000-token budget on internal reasoning before ever writing a
    # JSON answer — output_tokens landed at exactly 6000 and content came back
    # empty ("no JSON object in output"). Headroom here is for reasoning tokens
    # PLUS the actual answer, not just a verbose answer.
    reflection_max_output_tokens: int = 16000
    # Bumped 8000->16000 for the same reason, observed on the top tier once the
    # Anthropic key was valid again: EVERY strategic heartbeat landed on
    # output_tokens == 8000 exactly — the cap — including the one that recorded
    # "completed" (it only survived because the truncated-JSON recovery salvaged
    # a prefix, so even that beat lost content). A tier-3 beat reviews the whole
    # lineage/graveyard/peer context and writes the longest answer, so it needs
    # at least as much room as a tier-2 reflection, not half.
    strategic_max_output_tokens: int = 16000
    # Input-size pre-flight caps, ALSO per tier: tiers 2 and 3 include extra context
    # (graveyard + peer roster on top of everything tier 1 gets — see
    # cognition.is_enriched_kind), so their real context is legitimately bigger.
    # Discovered live: reflection heartbeats were regularly ~12.5-13.5K tokens and
    # got rejected by a single shared 12000 cap meant for tier 1 — every tier-2
    # heartbeat degraded with "input too large" even though nothing was actually
    # wrong. Sized with real headroom above what's been observed, not just the
    # observed max, since a bigger cohort/genome naturally grows this over time.
    # Re-sized again after the SAME failure recurred on tier 1: 16 of 17 routine
    # heartbeats in a 6-hour window degraded with "input too large (~15614
    # tokens)" against the 14000 cap — the highest-frequency tier was almost
    # entirely down. The cause is not a bug, it is growth: every capability we
    # add (open orders, YOUR STRATEGIES, recent data reads, explored markets)
    # and every new announcement lands in the SHARED base context that tier 1
    # also carries, so tier 1's prompt now sits near where tier 2's used to.
    # Lesson from both incidents: a cap sized just above the observed max (14000
    # vs ~12.1K, ~15% headroom) is breached within days. These are sized ~40%
    # above what is observed now, and are a pre-flight guard against a runaway
    # prompt — not a cost control (the weekly token + dollar budgets are that).
    heartbeat_max_input_tokens: int = 22000  # tier 1 (routine) — observed max ~15.6K
    reflection_max_input_tokens: int = 28000  # tier 2 (deep) — base + graveyard/peers
    strategic_max_input_tokens: int = 32000  # tier 3 — richest context, but rare enough that a generous cap costs little
    weekly_token_budget: int = 1_500_000  # per agent, input+output
    # Raised 120->240: two consecutive strategic (tier-3, Anthropic) heartbeats
    # landed within 0.4s of the old 120s cap — one timed out (ReadTimeout at
    # 120.16s), the next completed at 119.79s. That is not one slow outlier;
    # it is tier 3's real generation time (32000-token input cap, 16000-token
    # output cap) sitting right at the wall. 240s gives real headroom instead
    # of a coin flip on every strategic call.
    llm_timeout_seconds: float = 240.0

    # --- OpenAI-compatible backend (routes whichever tiers you point at it) ---
    # Optional: route one or more model aliases to any OpenAI-compatible
    # /chat/completions server instead of Anthropic. Two shapes, same code path:
    #   1. A self-hosted server (Ollama / llama.cpp / vLLM) — no api key, leave the
    #      cost rates at 0.0, so compute (token caps) is the only constraint.
    #   2. A hosted inference API/router (OpenRouter, Groq, ...) — set an api key
    #      and the per-Mtok cost rates; real cost is then tracked and the weekly
    #      dollar ceiling is enforced just like the Anthropic path. OpenRouter is
    #      the practical default here: it fronts many providers behind one endpoint
    #      with no low per-request tokens-per-minute ceiling (Groq's free tier caps
    #      a single request well below what a routine heartbeat's ~9-10K-token
    #      context needs, before output is even counted).
    # (The env vars keep the EVO_LOCAL_LLM_* names for continuity even though a
    # hosted provider isn't "local" — they mean "the OpenAI-compatible backend".)
    local_llm_enabled: bool = False
    local_llm_base_url: str = ""  # e.g. "http://ollama.railway.internal:11434/v1", "https://openrouter.ai/api/v1", or "https://api.groq.com/openai/v1"
    local_llm_timeout_seconds: float = 180.0  # generous: covers slow CPU generation; a hosted GPU/router API returns in seconds
    local_llm_api_key: str = ""  # EVO_LOCAL_LLM_API_KEY — bearer token for a hosted provider; empty for a self-hosted server that needs no auth
    # Which model aliases route here (comma-separated). "strategic" is deliberately
    # NOT in the default: the top tier keeps its Anthropic tie-in, because it runs
    # the high-stakes lifecycle beats (birth / cohort_end / retirement) where a
    # quality regression is expensive and irreversible. Put it here anyway if you
    # explicitly want the whole fleet off Anthropic.
    local_llm_aliases: str = "routine,deep"
    # --- tier 1 "routine" model + rates on the OpenAI-compatible backend ---
    local_llm_model: str = ""  # model id as the server expects (e.g. "qwen/qwen3-coder-next" on OpenRouter)
    # Per-million-token cost. Both 0.0 => treated as free (self-hosted): no dollar
    # cost recorded, dollar ceiling skipped. Any value > 0 => a paid provider: real
    # cost is booked to evo_llm_usage and charged against the weekly ceiling.
    local_llm_input_cost_per_mtok: float = 0.0   # EVO_LOCAL_LLM_INPUT_COST_PER_MTOK
    local_llm_output_cost_per_mtok: float = 0.0  # EVO_LOCAL_LLM_OUTPUT_COST_PER_MTOK
    # --- tier 2 "deep" model + rates (a stronger/pricier model than routine) ---
    # Each falls back to the routine value when unset, so a single-model setup
    # still works unchanged.
    local_llm_deep_model: str = ""
    local_llm_deep_input_cost_per_mtok: float = 0.0
    local_llm_deep_output_cost_per_mtok: float = 0.0

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

    def effective_population_size(self) -> int:
        """How many agents should actually be alive.

        `max_active_agents` (when set) IS the population, not a filter over a
        larger one — every population-sized decision (how many founders to
        create, how many compete, how many retire, how many are cloned) must
        derive from this single number so they can never disagree."""
        cap = self.max_active_agents
        return min(self.population_size, cap) if cap and cap > 0 else self.population_size

    def local_alias_set(self) -> frozenset[str]:
        """Model aliases routed to the OpenAI-compatible backend (parsed from the
        comma-separated EVO_LOCAL_LLM_ALIASES). Unknown names are harmless — they
        simply never match a tier."""
        return frozenset(
            part.strip().lower()
            for part in (self.local_llm_aliases or "").split(",")
            if part.strip()
        )


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
