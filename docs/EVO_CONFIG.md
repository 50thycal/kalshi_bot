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

| Env var | Default | Meaning |
|---|---|---|
| `EVO_ROUTINE_HEARTBEATS_PER_DAY` | `6` | guaranteed routine heartbeats |
| `EVO_DEEP_REFLECTIONS_PER_DAY` | `1` | deep reflection (stronger model) |
| `EVO_TRIGGERED_HEARTBEATS_PER_WEEK` | `20` | listener-triggered pool per agent |
| `EVO_MATERIAL_REVISIONS_PER_DAY` | `2` | material genome revisions per day |
| `EVO_REVISION_COOLDOWN_MINUTES` | `240` | between material revisions |
| `EVO_HEARTBEAT_STALE_MINUTES` | `30` | running → abandoned timeout |

## LLM routing & budgets

| Env var | Default | Meaning |
|---|---|---|
| `EVO_MODEL_ROUTINE` | `claude-haiku-4-5-20251001` | routine heartbeats |
| `EVO_MODEL_DEEP` | `claude-sonnet-5` | reflection / birth / cohort-end / retirement |
| `EVO_WEEKLY_LLM_CEILING_USD` | `2.0` | hard per-agent weekly cost stop |
| `EVO_HEARTBEAT_MAX_OUTPUT_TOKENS` | `2000` | routine output cap |
| `EVO_REFLECTION_MAX_OUTPUT_TOKENS` | `4000` | deep output cap |
| `EVO_HEARTBEAT_MAX_INPUT_TOKENS` | `12000` | prompt-size guard |
| `EVO_WEEKLY_TOKEN_BUDGET` | `1500000` | per-agent weekly tokens (in+out) |
| `EVO_LLM_TIMEOUT_SECONDS` | `120` | API timeout |
| `EVO_WEEKLY_TOOL_CALLS` | `2000` | per-agent action budget |
| `EVO_WEEKLY_SANDBOX_RUNS` | `50` | per-agent backtest/probe budget |

Prices per model live in the `evo_model_prices` table (seeded on first run; update
rows there — cost math never hardcodes a price).

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
