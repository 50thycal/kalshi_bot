# Evolutionary Agent System — Configuration Reference

All settings are env vars with prefix `EVO_` (class `kalshi_bot/evo/config.py:EvoSettings`).
Every change is snapshotted into `evo_config_versions` and cohorts record the version
they ran under. Defaults are the spec's initial system defaults.

## Population & cohorts

| Env var | Default | Meaning |
|---|---|---|
| `EVO_POPULATION_SIZE` | `30` | active-agent target per cohort |
| `EVO_MAX_ACTIVE_AGENTS` | `0` | **ops throttle** (not a spec parameter): cap how many active agents run live work per cycle — heartbeats (LLM spend), strategy execution (paper trades), snapshots, interim fitness. `0` = no cap. Set e.g. `3` to shrink the live footprint for end-to-end testing without retiring anyone; the capped-out agents stay in the cohort, dormant, and resume the instant the cap is lifted. Deterministic (lowest-id agents run) |
| `EVO_COHORT_DAYS` | `7` | cohort length; runs exactly this long **from when the cohort is born** (birth-anchored — no fixed calendar boundary, so every cohort gets a full week) |
| `EVO_COHORT_TIMEZONE` | `America/Chicago` | display/reporting only (cohort timing is birth-anchored, not calendar-snapped) |
| `EVO_BOTTOM_FRACTION` | `0.30` | retired each boundary |
| `EVO_MIDDLE_FRACTION` | `0.40` | survive |
| `EVO_TOP_FRACTION` | `0.30` | survive + reproduce |
| `EVO_STARTING_CAPITAL_USD` | `1000.0` | per agent per cohort (normalized) |
| `EVO_WILDCARD_EVERY_N_COHORTS` | `4` | wildcard founder cadence (`0` disables) |

## Heartbeats & adaptation

Heartbeats run in **three tiers**, cheapest and most frequent first. The tier is
derived from the heartbeat `kind` (see `cognition.alias_for_kind` — the single
source of truth) and selects both the model alias and the output-token ceiling:

| Tier | Alias | Kinds | Cadence | Default backend |
|---|---|---|---|---|
| 1 | `routine` | `routine`, `triggered` | `EVO_ROUTINE_HEARTBEATS_PER_DAY` (24 = hourly) | OpenAI-compatible |
| 2 | `deep` | `reflection` | `EVO_DEEP_REFLECTIONS_PER_DAY` (2 = every ~12h) | OpenAI-compatible |
| 3 | `strategic` | `strategic`, `birth`, `cohort_end`, `retirement` | `EVO_STRATEGIC_REVIEW_HOURS` (48h) **+ every lifecycle event** | Anthropic |

Tier 3 is the top layer: a periodic strategic review *plus* every high-stakes,
irreversible lifecycle beat. It keeps its Anthropic tie-in by default (it is not
in `EVO_LOCAL_LLM_ALIASES`) precisely because those beats are the expensive ones
to get wrong. **It does not decide who gets cut** — retirement is still the
deterministic bottom-fraction selection computed from realized fitness at cohort
finalization; the strategic beat changes the *trajectory* that lands an agent
there, and writes the birth/cohort-end/retirement reflections.

Tier-1/2 slots are spread across the UTC day with deterministic per-agent jitter.
Tier-3 slots are anchored to fixed epoch periods (`floor(epoch / interval)`)
rather than to a day, because a 48h cadence spans days — so the schedule is
stable across restarts and never double-fires. Each scheduler also looks back one
day (one period for tier 3) so a worker that was down doesn't silently skip a slot.

| Env var | Default | Meaning |
|---|---|---|
| `EVO_ROUTINE_HEARTBEATS_PER_DAY` | `6` | tier-1 slots/day (set `24` for hourly) |
| `EVO_DEEP_REFLECTIONS_PER_DAY` | `2` | tier-2 slots/day (`2` = every ~12h) |
| `EVO_STRATEGIC_REVIEW_HOURS` | `48` | tier-3 review interval in hours (`0` disables the periodic beat; lifecycle beats still run) |
| `EVO_TRIGGERED_HEARTBEATS_PER_WEEK` | `20` | listener-triggered pool per agent |
| `EVO_MATERIAL_REVISIONS_PER_DAY` | `2` | material genome revisions per day |
| `EVO_REVISION_COOLDOWN_MINUTES` | `240` | between material revisions |
| `EVO_HEARTBEAT_STALE_MINUTES` | `30` | running → abandoned timeout |

## LLM routing & budgets

| Env var | Default | Meaning |
|---|---|---|
| `EVO_MODEL_ROUTINE` | `claude-haiku-4-5-20251001` | tier-1 model (Anthropic path only) |
| `EVO_MODEL_DEEP` | `claude-sonnet-5` | tier-2 model (Anthropic path only) |
| `EVO_MODEL_STRATEGIC` | `claude-sonnet-5` | tier-3 model — the top layer's Anthropic tie-in |
| `EVO_WEEKLY_LLM_CEILING_USD` | `2.0` | hard per-agent weekly cost stop |
| `EVO_HEARTBEAT_MAX_OUTPUT_TOKENS` | `6400` | tier-1 output cap |
| `EVO_REFLECTION_MAX_OUTPUT_TOKENS` | `6000` | tier-2 output cap |
| `EVO_STRATEGIC_MAX_OUTPUT_TOKENS` | `8000` | tier-3 output cap |
| `EVO_HEARTBEAT_MAX_INPUT_TOKENS` | `14000` | tier-1 prompt-size guard (observed max ~12.1K) |
| `EVO_REFLECTION_MAX_INPUT_TOKENS` | `20000` | tier-2 prompt-size guard — richer context (graveyard + peer roster) observed ~12.5-13.5K |
| `EVO_STRATEGIC_MAX_INPUT_TOKENS` | `24000` | tier-3 prompt-size guard — richest context, rare enough that a generous cap costs little |
| `EVO_WEEKLY_TOKEN_BUDGET` | `1500000` | per-agent weekly tokens (in+out) |
| `EVO_LLM_TIMEOUT_SECONDS` | `120` | API timeout |
| `EVO_WEEKLY_TOOL_CALLS` | `2000` | per-agent action budget |
| `EVO_WEEKLY_SANDBOX_RUNS` | `50` | per-agent backtest/probe budget |

Prices per model live in the `evo_model_prices` table (seeded on first run; update
rows there — cost math never hardcodes a price).

## OpenAI-compatible LLM backend

Optional: route one or more **tiers** to any OpenAI-compatible `/chat/completions`
server instead of Anthropic. `EVO_LOCAL_LLM_ALIASES` (default `routine,deep`)
decides which — tier 3 (`strategic`) is deliberately excluded so the top layer
keeps its Anthropic tie-in. Each routed tier can name its own model and rates, so
tier 1 can be a cheap fast model while tier 2 is a stronger one; anything left
unset falls back to the tier-1 value, so a single-model setup still works. Two
shapes, same code path (the env vars keep the `EVO_LOCAL_LLM_*` names even for a
hosted provider — they mean "the OpenAI-compatible backend"):

- **Self-hosted (free)** — a server on your own infra (Ollama / llama.cpp / vLLM),
  no API key, cost rates left at `0`. `evo_llm_usage` records `cost_usd=0` and the
  weekly `$` ceiling is skipped for that tier, so each tier's `*_MAX_INPUT_TOKENS` /
  `*_MAX_OUTPUT_TOKENS` (generation time) are the only constraint. A
  CPU box is often too slow to finish a full heartbeat inside the read timeout.
- **Hosted API (paid)** — set an API key and the per-Mtok cost rates. A
  `Authorization: Bearer` header is sent, real `cost_usd` is booked to
  `evo_llm_usage`, and the weekly `EVO_WEEKLY_LLM_CEILING_USD` is projected before
  the call and charged after — exactly like the Anthropic path. GPU-class latency
  at a small fraction of Haiku's cost, with no server to operate.
  - **OpenRouter** (recommended default) — fronts many providers behind one
    OpenAI-compatible endpoint with no low per-request tokens-per-minute ceiling.
    `EVO_LOCAL_LLM_BASE_URL=https://openrouter.ai/api/v1`, model ids are
    `provider/model` (e.g. `meta-llama/llama-3.1-8b-instruct`). The client also
    sends OpenRouter's optional `HTTP-Referer` / `X-Title` identification headers
    automatically whenever the base URL is `openrouter.ai` — harmless no-ops on
    any other provider, not required for requests to succeed.
  - **Groq** — same mechanics, but its free `on_demand` tier caps a single
    request at 6000 tokens-per-minute (input + reserved `max_tokens` combined),
    which a routine heartbeat's ~9-10K-token context already exceeds before any
    output is counted. Only viable on a Groq paid tier with a higher TPM cap.

| Env var | Default | Meaning |
|---|---|---|
| `EVO_LOCAL_LLM_ENABLED` | `false` | master switch; every tier falls back to Anthropic when false or when base_url/model are unset |
| `EVO_LOCAL_LLM_BASE_URL` | `""` | OpenAI-compat base URL, e.g. `http://ollama.railway.internal:11434/v1`, `https://openrouter.ai/api/v1`, or `https://api.groq.com/openai/v1` (the client POSTs `<base_url>/chat/completions`) |
| `EVO_LOCAL_LLM_ALIASES` | `routine,deep` | which tiers route here. Add `strategic` only if you deliberately want the top tier off Anthropic too |
| `EVO_LOCAL_LLM_API_KEY` | `""` | bearer token for a hosted provider; empty for a keyless self-host |
| `EVO_LOCAL_LLM_TIMEOUT_SECONDS` | `180` | read timeout; connect is bounded to ≤10s separately so an unreachable host fails fast instead of freezing the loop |
| `EVO_LOCAL_LLM_MODEL` | `""` | **tier-1** model id the server expects, e.g. `qwen2.5:7b-instruct` (self-host) or `qwen/qwen3-coder-next` (OpenRouter) |
| `EVO_LOCAL_LLM_INPUT_COST_PER_MTOK` | `0.0` | tier-1 USD per 1M input tokens (`> 0` marks a paid provider). Check the model's current price on the provider's site — rates change. |
| `EVO_LOCAL_LLM_OUTPUT_COST_PER_MTOK` | `0.0` | tier-1 USD per 1M output tokens. Same caveat — verify current pricing. |
| `EVO_LOCAL_LLM_DEEP_MODEL` | `""` | **tier-2** model id; falls back to `EVO_LOCAL_LLM_MODEL` when unset |
| `EVO_LOCAL_LLM_DEEP_INPUT_COST_PER_MTOK` | `0.0` | tier-2 input rate; falls back to the tier-1 rate when `0` |
| `EVO_LOCAL_LLM_DEEP_OUTPUT_COST_PER_MTOK` | `0.0` | tier-2 output rate; falls back to the tier-1 rate when `0` |

No `evo_model_prices` row is needed for this backend — cost comes from the
`_COST_PER_MTOK` rates (all `0` = free), recorded directly on `evo_llm_usage`.
The startup probe checks that **every** routed tier's model is actually present on
the server, so a typo'd tier-2 model id surfaces at boot rather than at the first
12-hourly reflection.

Everything above except the **API key** is readable and settable through the ops
channel (`{"type":"env","service":"evo"}`), so a mis-set tier can be diagnosed and
corrected without a deploy. The key stays UI-only — the ops tool must never be
able to read or rewrite a credential.

## Market realism & listeners

| Env var | Default | Meaning |
|---|---|---|
| `EVO_LEADERBOARD_DELAY_HOURS` | `6.0` | peer-visible fitness delay |
| `EVO_MAX_LISTENERS_PER_AGENT` | `25` | active listener cap |
| `EVO_LISTENER_MIN_COOLDOWN_SECONDS` | `60` | listener re-fire floor |
| `EVO_FILL_LATENCY_MS` | `1500` | simulated intent→evaluation latency floor |
| `EVO_MAKER_ADVERSE_SELECTION_HAIRCUT` | `0.25` | maker fill quantity haircut |
| `EVO_MAX_POSITION_FRACTION` | `0.25` | default per-market cost cap (of NAV) |
| `EVO_STALE_DATA_SECONDS` | `600` | older data fails closed (no fills) |

## Sandbox bounds

| Env var | Default | Meaning |
|---|---|---|
| `EVO_SANDBOX_MAX_ROWS` | `200000` | rows per backtest |
| `EVO_SANDBOX_MAX_SECONDS` | `60` | wall-clock per backtest |
| `EVO_STRATEGY_SPEC_MAX_BYTES` | `40000` | declarative spec size cap |

## Fitness (weights must sum to 100)

| Env var | Default |
|---|---|
| `EVO_FITNESS_WEIGHT_PROFIT` | `35` |
| `EVO_FITNESS_WEIGHT_CONSISTENCY` | `20` |
| `EVO_FITNESS_WEIGHT_RISK` | `15` |
| `EVO_FITNESS_WEIGHT_EVIDENCE` | `10` |
| `EVO_FITNESS_WEIGHT_FORECAST` | `10` |
| `EVO_FITNESS_WEIGHT_ADAPTIVE` | `10` |
| `EVO_SELECTION_CURRENT_WEIGHT` | `0.80` |
| `EVO_SELECTION_RELIABILITY_WEIGHT` | `0.20` |
| `EVO_RELIABILITY_DECAY` | `0.75` (per-cohort recency decay) |
| `EVO_SHRINKAGE_TRADES` | `10` (Bayesian pseudo-count on per-trade EV) |
| `EVO_WINSOR_PCT` | `0.05` |

## Orchestrator / ops

| Env var | Default | Meaning |
|---|---|---|
| `EVO_CYCLE_SECONDS` | `60` | informational; the loop interval is the service's `SCAN_INTERVAL_SECONDS` |
| `EVO_MARKETS_PER_CYCLE` | `150` | market/orderbook fetch bound per cycle |
| `EVO_BOOTSTRAP_RNG_SEED` | `20260718` | reproducible naming/cohort seeds |
| `EVO_ENABLED` | `true` | infrastructure pause switch (never a performance one) |

Service-level (not `EVO_`-prefixed, on the evo Railway service): `BOT_MODE=evo`,
`SCAN_INTERVAL_SECONDS=60`, `ANTHROPIC_API_KEY=...`.
