# Evolutionary Agent System — Design Document

Status: **implemented** (paper/shadow only — no live-trading code path).
Owner: evo subsystem (`kalshi_bot/evo/`).
This document is the design contract for the evolutionary population of autonomous
Kalshi paper-trading agents. Read it together with `docs/EVO_RUNBOOK.md` (operations)
and `docs/EVO_CONFIG.md` (configuration reference).

---

## 1. Existing architecture discovered

The repository is a single-process Railway worker (`Procfile`: `alembic upgrade head &&
python -m kalshi_bot.main`) against one Postgres database.

- **Config** — `kalshi_bot/config.py`: one fail-closed `pydantic-settings` `Settings`
  class; every knob is an env var. `BOT_MODE` selects the cycle
  (`scanner|paper|approval|live|weather|mmsell`).
- **DB** — `kalshi_bot/models.py`: single-file SQLAlchemy 2.0 declarative models.
  Conventions: `BigIntId` (BigInteger/Integer sqlite variant) autoincrement PKs,
  `JSONType` (JSONB/JSON variant), `TS = DateTime(timezone=True)`, `utcnow()` defaults,
  prices in integer cents, money in `Numeric(14,4)`, probabilities in `Float`.
  Migrations in `alembic/versions/` (hand-written, chained revision ids);
  `create_all()` is a safety net, Alembic is the source of truth.
- **Kalshi client** — `kalshi_bot/kalshi/client.py`: RSA-PSS-signed REST client.
  `place_order` self-guards on `bot_mode != "live" or kill_switch` — structurally
  impossible to place a real order from any other mode.
- **Scanner** — `kalshi_bot/scanner/`: order-book metrics (`compute_metrics`),
  deterministic 0–100 scoring, ranked candidates.
- **Paper engine** — `kalshi_bot/paper/engine.py`: one shared engine; one position per
  `(market_ticker, strategy)`; `kalshi_fee()` = `ceil(0.07 · C · P · (1−P))` per trade
  (entry and early exit, never settlement); taker entries at the ask capped by displayed
  depth; conservative exits at the bid; settlement at 0/100. Books are *strategy strings*
  on `paper_trades`/`paper_positions` rows — there is no per-book cash accounting.
- **Trackers** — `weather/ theta/ mmsell/ tfav/ pin15/ wcprop/ xgame/`: per-strategy
  "ride-along" books called from `main.py`'s single loop on monotonic-clock throttles;
  each writes `paper_trades` rows with a strategy tag; the shared engine settles/marks
  them. Failures are contained (never raise into the cycle).
- **Live layer** — `kalshi_bot/live/executor.py`: real-money mirror of allowlisted paper
  entries, inert unless `BOT_MODE=live` + `KILL_SWITCH=false` + `LIVE_ENABLED=true` +
  allowlist. The evo build does not touch it.
- **Research history** — `docs/RESEARCH_JOURNAL.md`, `docs/BOOK_REGISTRY.md`, thesis
  docs, `docs/IDEA_MODEL_*.md`: a rich pre-registered experiment culture. Killed/shelved
  books (tfav, pin15, wcprop, xgame lead-lag, theta family, most weather books, low-temp
  program…) with explicit kill reasons — the seed corpus for the strategy graveyard.
- **Ops channel** — `scripts/ops_runner.py` on the `ops` branch runs allowlisted
  read-only scripts via GitHub Actions; `weather_digest` output is archived to the
  `digest-archive` branch. **There is no web dashboard** — digest-style ops scripts are
  the dashboard mechanism.
- **Tests** — pytest + respx against sqlite (`tests/conftest.py` monkeypatches env and
  uses `sqlite:///tmp` URLs; models carry sqlite variants for exactly this reason).

## 2. Existing components reused

| Component | How the evo system uses it |
|---|---|
| `models.py` conventions (`BigIntId`, `JSONType`, `TS`, `utcnow`) | all `evo_*` tables use the same declarative `Base` and type variants |
| `paper.engine.kalshi_fee` | the evo fill simulator imports the exact same fee function |
| `scanner.metrics.compute_metrics` / orderbook parsing | market/orderbook metrics for listener evaluation and fill simulation |
| `KalshiClient` (read-only surface) | market discovery, orderbooks, candles, settlement detection for shadow trading |
| `db.py` engine/session | same engine + `session_scope`; no second connection pool |
| Alembic migration chain | one new migration `a9e0c1d2f3b4_evo_agent_system` on the existing head |
| `logging_config` structured logging | evo components log through the same JSON logger |
| Ops channel (`ops_runner.py` allowlist) | new read-only `evo_digest`, `evo_leaderboard`, `evo_tree` scripts |
| Backfill tables (`backfill_weather_*`, `crypto_*`, snapshot tables) | the sandbox's historical replay/backtest data, provenance-labeled |
| Research docs | parsed into the seeded strategy graveyard (`evo/graveyard_seed.py`) |

## 3. Existing components modified

Deliberately minimal, so existing books and experiments continue untouched:

1. `kalshi_bot/config.py` — adds `"evo"` to `VALID_MODES` (one line in the validator
   tuple). All evo settings live in a separate `EvoSettings` class (`evo/config.py`).
2. `kalshi_bot/main.py` — a small dispatch branch: `BOT_MODE=evo` runs
   `evo.orchestrator.run_evo_loop()` instead of the scanner/weather cycles. No change to
   any existing cycle.
3. `scripts/ops_runner.py` — three script names appended to `ALLOWED_SCRIPTS`.
4. `kalshi_bot/models.py` — untouched; evo models live in `kalshi_bot/evo/models.py`
   importing the same `Base` (they join the same metadata, so `create_all()` and Alembic
   autogenerate see them).

Nothing else in the existing tree changes. The weather/mmsell/theta books, their
data collectors, the live executor, and all analysis scripts are untouched.

## 4. New components

Package `kalshi_bot/evo/`:

| Module | Responsibility |
|---|---|
| `models.py` | all `evo_*` SQLAlchemy models |
| `config.py` | `EvoSettings` (env-driven, fail-closed, all defaults from the spec) |
| `constitution.py` | immutable constitutional layer (frozen, versioned, hashed) |
| `naming.py` | founder/wildcard surnames, child first names, display names |
| `genomes.py` | cognitive + trading-policy genome schemas, validation, versioning |
| `memory.py` | factual/belief/episodic/experiment/journal memory + retrieval |
| `lineage.py` | family tree + influence graph |
| `cohorts.py` | cohort lifecycle, membership, boundaries |
| `budgets.py` | per-agent per-cohort resource budgets + LLM cost ledger |
| `llm.py` | model routing, pricing, Anthropic API calls, hard budget stops |
| `cognition.py` | heartbeat prompt assembly, structured action schema, parsing; `ScriptedCognition` for tests/simulation |
| `heartbeats.py` | heartbeat orchestrator (scheduling, idempotency, lifecycle) |
| `listeners.py` | deterministic listener framework |
| `peers.py` | peer observation views + 6-hour-delayed leaderboard |
| `graveyard.py` / `graveyard_seed.py` | strategy graveyard interface + seed corpus |
| `strategy_spec.py` | declarative trading-strategy DSL (typed, validated) |
| `sandbox.py` | strategy validation, historical replay, backtests, walk-forward |
| `datasources.py` | data-source registry |
| `tickets.py` | capability-request queue (dedup + support) |
| `marketdata.py` | market data access layer (live client or replay source) |
| `paper.py` | per-agent counterfactual fill simulator, portfolios, lifetime + cohort ledgers |
| `fitness.py` | 100-point fitness rubric, historical reliability, selection score |
| `evolution.py` | cohort finalization, retirement, reproduction, wildcards |
| `orchestrator.py` | the `BOT_MODE=evo` worker loop |
| `simulation.py` | deterministic multi-generation simulation + adversarial profiles |
| `audit.py` | audit-event writer |

Scripts: `scripts/evo_digest.py`, `scripts/evo_leaderboard.py`, `scripts/evo_tree.py`
(read-only, ops-channel runnable), `scripts/evo_simulation.py` (local CLI for the
multi-generation sim).

Docs: this file, `docs/EVO_RUNBOOK.md`, `docs/EVO_CONFIG.md`.

## 5. Database-schema changes

One migration adds the `evo_*` tables (nothing existing is altered). All PKs are
`BigIntId` autoincrement; agent identity is a `String(36)` UUID column (unique), because
names must never be identity. Money `Numeric(14,4)`; prices integer cents; JSON payloads
`JSONType`.

Entities (≈ spec §32):

- `evo_config` — versioned system configuration snapshots (constitution hash, fitness
  weights, population parameters). A row is written whenever the effective config
  changes; cohorts record the config version they ran under.
- `evo_agents` — identity: `agent_uuid`, first/last name, display name, founder uuid,
  parent uuid, generation, birth cohort, status
  (`active|retired|suspended`), current genome revision ids, historical name (permanent).
- `evo_cohorts` — number, start/end (birth-anchored: start = creation time, end =
  start + `cohort_days`), status (`open|finalizing|finalized`), config version, rng
  seed, wildcard flag.
- `evo_cohort_members` — (cohort, agent) membership: starting capital, group assignment
  (`top|middle|bottom`), final rank/scores, carried-position scaling factor.
- `evo_genomes` — immutable versions, `kind` = `cognitive|trading`; JSON document,
  parent revision, hypothesis/evidence/rollback fields, activation timestamp, heartbeat
  attribution. Unique `(agent_uuid, kind, revision)`.
- `evo_memories` — `kind` = `fact|belief|episode|experiment|journal`; `immutable` flag;
  belief revisions chain via `supersedes_id` (old row is never updated); importance,
  tags, confidence-before/after for beliefs; unique idempotency key.
- `evo_influences` — horizontal idea-transfer edges (source/receiving agent + family,
  concept, evidence, modifications, result).
- `evo_heartbeats` — one row per heartbeat: type
  (`routine|reflection|triggered|birth|cohort_end|retirement|recovery`), scheduled slot,
  status, unique `idempotency_key` (**a worker restart can never run one twice**),
  model used, tokens, cost, actions summary.
- `evo_listeners` — owner, purpose, declarative condition JSON, cooldown, expiration,
  expected value, trigger/success counters, status.
- `evo_listener_events` — individual firings (deduped by `(listener, dedup_key)`).
- `evo_budgets` — per (agent, cohort, resource): allocated/used for
  `llm_cost_usd, tokens, tool_calls, sandbox_runs, triggered_heartbeats,
  material_revisions_day:<date>`.
- `evo_llm_usage` — per-call ledger (agent, heartbeat, model alias, in/out tokens,
  cached tokens, cost USD).
- `evo_model_prices` — versioned model-alias pricing (no hardcoded price constants).
- `evo_data_sources` — data-source registry entries (schema per spec §17).
- `evo_data_health_events` — staleness/outage records; strategies fail closed on them.
- `evo_strategies` — versioned declarative strategy artifacts (spec JSON, validation
  report, status, attribution).
- `evo_orders` — paper-trade intents/orders: agent, ledger, market, side, action,
  order style (`taker|maker`), limit price, quantity, status, unique
  `idempotency_key` (**a retry can never double an order**), ex-ante declared
  opportunity id.
- `evo_fills` — simulated fills (may be partial): order, price, qty, fee, liquidity
  assumption, market-data snapshot reference.
- `evo_positions` — per (agent, ledger, market, side): qty, avg cost, status, marks.
- `evo_position_transfers` — cohort-boundary carryovers (marked value, scaling factor)
  and retirement liquidations.
- `evo_portfolio_snapshots` — daily + boundary NAV snapshots per ledger.
- `evo_opportunities` — ex-ante opportunity log (listener/strategy-declared), the basis
  for opportunity-use scoring (prevents post-hoc "that trade was invalid" gaming).
- `evo_fitness` — per (agent, cohort): every component, raw + winsorized + relative
  values, penalties, final scores, and `visible_after` (the 6-hour delay gate for the
  peer-visible copy).
- `evo_transitions` — cohort finalization audit; **unique on cohort id** — a cohort can
  never finalize twice; contains ranked groups, seeds, and outcome JSON.
- `evo_births` — child/wildcard creation: unique `(cohort_id, parent_uuid)` (and a
  wildcard slot key), inheritance snapshot id, divergence plan JSON — **a retry can
  never create a child twice**.
- `evo_inheritance_snapshots` — frozen parent bundle (genome revisions, belief snapshot,
  memory summary refs, final reflection).
- `evo_retirements` — retirement records + final reflection journal ref.
- `evo_tickets` / `evo_ticket_supporters` — capability queue (+ unique
  (ticket, agent) support rows).
- `evo_audit_events` — append-only consequential-action audit trail.
- `evo_graveyard` — searchable failed/retired strategy corpus (seeded + accreting).

Indexing follows the existing style (`ix_<table>_<cols>` composite indexes on the hot
query paths: per-agent time series, per-cohort rankings, listener due-scans).

## 6. Agent lifecycle

```
birth (founder | child | wildcard)
  └─ birth heartbeat: divergence plan (children) / founder thesis (founders, wildcards)
active weeks: routine heartbeats ×6/day, daily reflection, triggered heartbeats,
  listeners, research, sandbox runs, paper trading, peer learning, tickets
cohort end: cohort-end heartbeat → fitness → rank
  ├─ bottom 30% → retirement heartbeat → retired (liquidated, searchable forever)
  └─ survivors → position carryover + capital normalization → next cohort
      └─ top 30% → may produce a child (inheritance snapshot + child birth)
suspension: only for integrity violations (constitution enforcement), audited
```

Retired = permanently inactive, never deleted: identity, names, memory, genomes,
trades, fitness history and retirement reason all remain queryable by active agents.

## 7. Cohort lifecycle

- Cohorts run for `cohort_days` (7) **from when the cohort is born** — birth-anchored,
  so every cohort gets a full week rather than a stub cut short by a fixed calendar
  boundary (configurable via `EVO_COHORT_DAYS`).
- `cohorts.ensure_current_cohort()` is idempotent; the orchestrator calls it each cycle.
- At the boundary the finalization pipeline (§ Evolution) runs inside one transaction
  guarded by the unique `evo_transitions.cohort_id` row (double-finalize impossible;
  concurrent workers race on the insert and exactly one proceeds).
- Population target 30; bottom 9 retired / middle 12 / top 9 reproduce (configurable
  fractions 30/40/30); every 4th cohort one child slot is replaced by a wildcard founder
  and the lowest-ranked top parent skips reproduction (configurable).
- No incubation exemption: every active agent, including week-old children, is subject
  to bottom-30% retirement. Low activity earns low evidence/opportunity credit, not
  immunity.

## 8. Heartbeat architecture

A heartbeat is a bounded session: **wake → load → retrieve → reason (one LLM call) →
act → journal → sleep.** The orchestrator computes due heartbeats from schedule slots:

- 6 routine slots/day/agent, jitter-spread across the day (deterministic per agent+date,
  so restarts recompute identical slots).
- 1 daily deep reflection (stronger model).
- Triggered heartbeats from listener firings, drawn from a weekly per-agent pool.
- Birth / cohort-end / retirement heartbeats around transitions.
- Recovery heartbeat after detected worker gaps (>1 missed slot) — replaces, never
  duplicates, missed slots.

Idempotency: the heartbeat row's key is `(agent_uuid, type, slot_id)`; execution flow is
claim (insert `running` row; unique key makes double-claim impossible) → execute →
finalize. A crashed `running` heartbeat older than a timeout is marked `abandoned` and
the slot is not re-run with side effects (its actions may have partially applied; the
journal records what completed — conservative, auditable).

Each execution follows the spec's 25-step lifecycle: the context pack loads identity,
constitution, both genomes, working state, portfolio, budgets, retrieved memory,
listener triggers, performance summary, relevant markets/external data, peer + graveyard
summaries; the LLM (or `ScriptedCognition` in tests) returns a **structured action
document** (JSON, schema-validated); permitted actions execute inside the same session;
a decision-journal memory row is always written, plus token/cost accounting.

Actions the cognition layer may return (all validated, all budget-checked):
`revise_belief, revise_cognitive_genome, revise_trading_genome, rollback_genome,
create_listener, update_listener, remove_listener, run_backtest, register_experiment,
conclude_experiment, submit_trade_intent, cancel_order, record_influence,
submit_ticket, support_ticket, register_data_source, note_episode, schedule_trigger,
no_action`.

## 9. Listener architecture

Listeners are **deterministic** — evaluated by the orchestrator every cycle against
fresh market data with zero LLM involvement. A listener is a declarative condition doc:

```json
{"all": [
  {"metric": "yes_ask", "op": "<=", "value": 30, "ticker": "KXHIGHCHI-..."},
  {"metric": "spread", "op": "<=", "value": 3}
]}
```

Supported condition metrics: price/bid/ask/spread/depth/volume/OI moves (absolute and
delta-over-window), market status transitions, new-market matches (series prefix),
hours-to-close bands, position P&L thresholds, drawdown, data-source staleness, peer
genome-revision events, ticket status changes, strategy-performance deterioration.
Effects: write a `listener_event`, update working state, request a triggered heartbeat
(consumes the weekly pool), schedule a future heartbeat, or surface a trade opportunity
for agent review. Cooldown + expiration + per-cycle dedup keys prevent storms. Full
ownership metadata + trigger/success history per the spec.

## 10. Cognitive-genome architecture

A versioned JSON document validated by `genomes.CognitiveGenome` (pydantic). It contains
the ~30 structured fields of spec §7.2 grouped as: `review` (heartbeat procedure,
info-retrieval priorities, peer/graveyard cadence), `research` (methodology,
hypothesis generation, experiment selection, evidence evaluation, retest criteria),
`adaptation` (strategy-change threshold, thrashing resistance, rollback policy,
weakness identification), `memory_policy` (retrieval/compression/retention),
`attention` (listener creation, trigger prioritization, wake conditions), `resources`
(LLM/tool/compute allocation split), `social` (copy-vs-ignore policy, peer review
cadence). Free-text fields are bounded; numeric fields are range-validated.

Every material change creates a new immutable revision (old row preserved), with
hypothesis, evidence for/against, expected benefit, rollback conditions, changed-field
diff, heartbeat attribution. Children inherit the parent's current revision as their
revision 1 (provenance recorded in the inheritance snapshot).

## 11. Trading-policy-genome architecture

Same versioning machinery, `kind='trading'`, validated by `genomes.TradingGenome`:
strategy family, thesis + falsifiable predictions, market universe (category/series
filters), liquidity/spread/volume requirements, data dependencies + freshness (fail
closed when stale), feature definitions (over approved operators), forecasting/
fair-value method, probability model reference, entry/exit/no-trade rules (declarative,
`strategy_spec` DSL), order-style + fill assumptions, sizing + capital-at-risk +
concentration limits, confidence thresholds, listeners, artifact references, revision
hypothesis + rollback conditions. Activation requires passing sandbox validation.

## 12. Memory architecture

One table, five kinds, different mutability rules enforced in `memory.py` (and by
convention — agents have no SQL access; every write goes through the API which refuses
updates to immutable kinds):

- `fact` — immutable; trade intents/orders/fills/settlements, observations used,
  resource usage, decisions, scores, incidents. Written by the system, never by
  free-form agent output.
- `belief` — editable **by supersession**: a revision inserts a new row pointing at the
  old (`supersedes_id`), carrying previous/new belief, evidence for/against, confidence
  before/after, heartbeat id. History is never destroyed.
- `episode` — notable events (big wins/losses, missed opportunities, outages,
  rollbacks, regime changes…).
- `experiment` — hypothesis registered **before** result (post-hoc hypothesis edits are
  impossible: concluding writes result fields on a new linked row; a revised hypothesis
  is a new experiment).
- `journal` — the structured per-heartbeat decision record (spec §8.5 fields).

Retrieval (`memory.retrieve`) is task-scoped, never load-everything: filters by kind,
tags (market/strategy/regime), recency, importance, confidence, contradiction flags,
inheritance priority; returns a bounded token budget of summarized entries with source
row ids preserved. Summarization writes derived rows; sources are never deleted.

## 13. Internal peer-learning architecture

`peers.py` exposes read-only views: active/retired rosters, family + generation,
high-level thesis + strategy family, markets traded, data sources, current experiments,
recent genome revisions (metadata, not private working state), listener categories,
**delayed** performance summaries and fitness (see below), historical cohort scores,
adaptations, retirement reasons, ticket-support counts, data-source health.

The delayed leaderboard: `evo_fitness` rows carry `visible_after = computed_at + 6h`;
the peer API only serves rows past that gate plus coarse delayed portfolio summaries.
The real-time internal ranking never crosses the peer API. Agents cannot modify one
another (there is no write path that accepts another agent's id; every write API takes
the acting agent from the heartbeat context, and cross-agent writes are rejected +
audited). Copying is legal and recorded as influence edges (§ lineage); influence never
changes surname or parentage.

## 14. Strategy sandbox

**Design decision (documented assumption):** agent strategies are *declarative
specifications interpreted by vetted engine code*, not agent-authored arbitrary Python.
The `strategy_spec` DSL covers universes, filters, features (approved operator set:
arithmetic, comparisons, rolling stats, lags, spreads/mids/depth, ensemble stats,
time-to-close, observation extremes…), entry/exit/no-trade predicates, sizing rules.
This satisfies every sandbox requirement (isolation, no credentials, no network, CPU/
time bounds, static + interface validation, versioning, attribution, reproducibility)
*by construction* — the "generated code" is data, executed by the same interpreter for
every agent. Bounds still enforced: spec size caps, operator whitelist, per-run row/
time limits, deterministic evaluation. A future extension behind a ticket could add
vetted plugins; free-form `exec()` of model output is deliberately not implemented.

Sandbox capabilities: validate spec → probe (single-day replay) → backtest (historical
tables, provenance-labeled, no-lookahead construction enforced by the replay cursor) →
walk-forward (rolling train/test splits) → activate as the agent's paper strategy
(auto-permitted when validation passes, data available, within budget) → else ticket.

## 15. Paper-trading architecture

`evo/paper.py` is a **new, per-agent counterfactual engine** (the existing shared
engine's one-position-per-(market,strategy) model cannot host 30 independent
portfolios; it remains untouched for the legacy books).

- Every agent gets an independent portfolio; agents may hold the same, or opposite,
  positions; no netting; no cross-agent liquidity interaction (each is simulated
  independently against the same recorded book — fair comparison).
- Fills: taker orders cross the spread and are capped by displayed depth (partial
  fills), walk deeper levels with per-level pricing; maker (passive) orders use a
  conservative queue model — a resting limit fills only when the market *trades
  through* the price (touch alone never fills), plus an adverse-selection haircut;
  simulated latency between intent and evaluation snapshot; limit orders expire;
  cancel/replace supported; market closure and settlement handled; stale data ⇒ fail
  closed (no fill, order rejected, data-health event).
- Fees: exact `kalshi_fee` on entries and early exits; settlement free. Spread cost and
  slippage are recorded per fill.
- Accounting: cash, reserved collateral (fully-collateralized YES at cost / NO at cost;
  no leverage, no negative cash, no borrowing, no transfers), realized/unrealized P&L,
  drawdown, turnover, concentration; per spec §18 metrics.
- Two ledgers per agent (§ lifetime vs cohort, below).
- Secondary **aggregate capacity analysis**: per cohort, the sum of the fleet's
  simulated consumption vs displayed liquidity per market/day, reported in fitness
  context and the digest (a deployment-realism signal, not a fill constraint).

## 16. Fitness methodology

100-point current-cohort score, weights configurable + versioned in `evo_config`:

- **Profitability & capital efficiency (35)** — net P&L (after fees/spread/slippage),
  return on normalized capital, return on deployed capital, conservative
  lower-confidence-bound return (bootstrap over per-trade P&L), turnover-aware EV;
  conservative marks for unresolved positions. Bayesian shrinkage toward zero with
  small n — lottery outcomes get limited credit.
- **Consistency (20)** — daily/trade/market dispersion, largest-trade and best-day
  dependence, expected-vs-realized-edge stability, boom-bust penalty.
- **Risk quality (15)** — max drawdown, downside vol, tail loss, concentration
  (position/event/category/settlement), virtual-ruin proximity.
- **Evidence & opportunity use (10)** — effective sample size (correlated trades
  down-weighted via same-event/same-day clustering), valid opportunities captured
  (from the **ex-ante** `evo_opportunities` log: declared universes + listener records
  — post-hoc redefinition impossible), justified no-trades.
- **Forecast & execution quality (10)** — calibration (Brier vs settlement), estimated
  vs realized edge, data freshness, invalid-intent rate, fill realism awareness.
- **Adaptive intelligence (10)** — pre-registration rate, post-revision improvement,
  rollback quality, thrashing penalty (revision count, field reversals,
  single-loss reactivity, abandoned-before-evaluable hypotheses, copy-of-transient-
  leader rate), listener usefulness, peer-learning quality, memory/journal quality
  (structural completeness — machine-checkable).

Pipeline per spec §24: absolute metrics → conservative statistical adjustment →
winsorization → cohort-percentile blend → weights → preserved raw + relative components.
`selection_score = 0.80 · cohort_score + 0.20 · historical_reliability`; reliability is
recency-weighted (exponential decay), risk-adjusted, and children/wildcards start at a
neutral prior with zero inherited credit. Integrity failures (§25) apply automatic
severe penalties/zeroing/suspension with permanent audit records, distinguished from
shared-infrastructure failures (which are neutralized cohort-wide, not attributed).

## 17. Retirement and reproduction

Retirement: a final retirement heartbeat writes the terminal reflection (why it failed,
what it would change, lessons for descendants; cannot alter completed scores). Lifetime
ledger conservatively liquidated at executable value (bid side, depth-aware). Agent row
flips to `retired`; everything remains searchable; graveyard entry created.

Reproduction: each top-30% parent (except the lowest-ranked in wildcard cohorts)
produces one child. `evo_births` is idempotent on `(cohort, parent)`. The child gets:
new UUID + first name, parent surname, generation+1, a frozen inheritance snapshot
(cognitive + trading genome revisions, belief snapshot, experiment history refs,
strengths/weaknesses, listeners (successful *and* failed), research habits, regime
knowledge, graveyard knowledge, final reflection), `$1,000`, neutral reliability, and a
**birth-divergence plan** produced in the birth heartbeat answering the 13 questions of
spec §28 (validated schema; stored on the birth row). Parents continue unchanged as
evolutionary controls.

## 18. Naming and lineage

`naming.py` holds curated first-name and surname pools (deterministically drawn under
the cohort RNG seed; collision-checked against all ever-used names). Founders get
unique surnames; wildcards get new unique surnames; children inherit surname + new
first name. Display name `Mara Kepler · Generation 3 · KEP-G3-018` (surname prefix,
generation, zero-padded agent ordinal). Uniqueness enforced on active display names;
retired agents keep names forever; all FKs use UUIDs, never names.

Family tree (vertical, parent→child) and influence graph (horizontal, adoption edges)
are separate structures with separate ops-script renderings.

## 19. Capability-request queue

`tickets.py` per spec §16: all listed categories; full field set; dedup by normalized
`(category, capability)` similarity (token-set Jaccard above a configurable threshold ⇒
the submitting agent is converted to a supporter of the existing ticket); unique
supporter rows; the human review surface is the `evo_digest` ops script's ticket
section (supporters, families, expected value, cost, age, status ordered for review).

## 20. LLM routing and cost controls

- Aliases, not hardcoded models: `EVO_MODEL_ROUTINE` (default `claude-haiku-4-5`),
  `EVO_MODEL_DEEP` (default `claude-sonnet-5` class) — used for deep reflection, major
  revisions, birth/cohort-end/retirement heartbeats. Pricing lives in
  `evo_model_prices` (seeded, updatable) — cost math never assumes a price.
- Hard ceilings enforced *before* each call: weekly per-agent cost (`$2` default),
  per-heartbeat token cap, per-agent weekly token cap. Exceeded ⇒ the heartbeat runs in
  `no_llm` degraded mode (deterministic bookkeeping only, journaled as such).
- One LLM call per heartbeat (no agent-side loops ⇒ no runaway recursion by
  construction); prompt caching via stable system-prompt prefixes; retrieved context
  only. Per-agent/family/cohort accounting rolls up from `evo_llm_usage`.
- No API key configured ⇒ cognition fails closed: listeners, paper marks, settlements
  and audits keep running; heartbeats are journaled as skipped. (The operator turns the
  population "on" by setting `ANTHROPIC_API_KEY` in Railway.)
- Resource efficiency is measured and visible; it feeds adaptive-intelligence scoring
  but cannot rescue an unprofitable agent by itself.

## 21. Security boundaries

- **No live-order path**: evo runs under `BOT_MODE=evo`; `KalshiClient.place_order`
  hard-refuses outside `live` mode + kill-switch-off (existing guard). Evo code never
  imports the live executor; a unit test asserts the evo package has no reference to
  `place_order`. Generated strategy content is data, not code.
- Agents never see credentials (prompts are assembled from DB + market data only);
  constitutional text is system-controlled; agent-visible external content (public
  data) is marked untrusted in prompts, with provenance.
- Prompt-injection containment: cognition output is schema-validated actions with
  budget + permission checks; free text can only land in bounded journal/belief fields;
  no action can modify the constitution, fitness config, capital rules, another agent,
  or raw history (there are no such API paths, and attempts are audited as integrity
  events).
- DB access for analysis scripts stays read-only via the existing `DATABASE_URL_RO`
  role; the sandbox interpreter takes bounded rows through repository functions, never
  raw SQL from agents.

## 22. Testing strategy

- Unit suites per module against sqlite (existing conftest pattern): naming, genome
  validation/versioning/immutability, memory rules (immutable protection, belief
  supersession, journal structure), lineage/influence, heartbeat scheduling +
  idempotency, listener conditions/cooldowns, budgets + cost ledger + model pricing,
  fill simulator (fees, partials, maker conservatism, staleness fail-closed), ledgers +
  normalization + carryover + retiree liquidation, fitness components (shrinkage,
  winsorization, lottery/thrashing/low-activity behavior, deterministic rounding),
  finalization idempotency, reproduction/wildcard naming, tickets dedup, data registry.
- Integration suites: full routine/triggered/reflection heartbeats with
  `ScriptedCognition`, peer queries + 6h delay, graveyard, sandbox validate→backtest→
  activate, opposing/identical trades, cohort boundary end-to-end, inheritance,
  retry/restart idempotency, outage handling, security violations.
- The deterministic multi-generation simulation (§ below) doubles as the system-level
  integration test, seed-reproducible.

## 23. Rollout plan

Stages 1–6 exactly as the spec orders them (foundations → heartbeats → sandbox → paper
competition → evolution → shadow readiness). All stages ship in this build; activation
is operational: deploy a second Railway service with `BOT_MODE=evo` (same repo/DB), set
`ANTHROPIC_API_KEY`, run `evo_bootstrap` (founder creation) — see `docs/EVO_RUNBOOK.md`.
Shadow readiness = the orchestrator running against live market data with zero order
paths, latency + fill-quality measurement built into the fill simulator's snapshot
references.

## 24. Migration plan

One additive Alembic migration on the current head; no existing table is touched;
`alembic upgrade head` runs automatically on deploy (existing Procfile). Rollback =
`alembic downgrade -1` (drops only `evo_*` tables). Founder bootstrap is a guarded
idempotent command, not part of the migration (so deploys never create agents
implicitly).

## 25. Known limitations

- Maker-fill realism is conservative-by-model (trade-through + haircut), not
  queue-position-accurate — Kalshi's public data cannot support true queue modeling.
- The orchestrator polls (Railway worker loop); listener latency is bounded by the
  cycle interval (default 60s), not tick-level.
- Peer/graveyard summaries are structured text; semantic dedup of tickets is
  token-set-similarity, not embedding-based (no new paid dependency).
- LLM-driven cognition quality depends on model + prompt budget; the routine model may
  produce conservative/no-op decisions — measured, and visible in the digest.
- The declarative DSL bounds the strategy space (documented tradeoff, §14); truly novel
  mechanisms route through capability tickets.
- Fitness begins meaningfully differentiating only after agents accumulate trades;
  cohort 1 rankings will be noisy — by design, the conservative statistics handle it.

## 26. Assumptions

1. ~$260/month LLM ceiling is authorized (spec provides it); actual spend is expected
   well below (routine heartbeats on a Haiku-class model with tight token caps).
2. A second Railway service (same repo, `BOT_MODE=evo`) is acceptable; the ride-along
   pattern was rejected to keep LLM latency out of the trading loop.
3. Live Kalshi market data via the existing credentials' read-only endpoints is
   authorized for shadow use (already the norm for every existing book).
4. `America/Chicago` cohort boundaries; UTC storage everywhere.
5. The existing shared paper books remain the property of the legacy system; evo agents
   trade only through `evo_orders` (no writes to `paper_trades`).
6. Agent LLM outputs are untrusted; every action is schema-validated and permission-
   checked server-side.
7. Wildcards + founders draw from curated name pools sized far beyond expected
   population-years; exhaustion falls back to deterministic suffixing.
