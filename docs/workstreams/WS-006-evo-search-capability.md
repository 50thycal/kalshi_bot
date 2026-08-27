# WS-006 — Evo historical search capability

**Phase:** BUILDING
**Status:** Active (implementation merged; D1 proving harness correction in progress)
**Created:** 2026-08-25
**Updated:** 2026-08-27

## Goal

Give the existing Evo agents a way to ask a question they currently cannot: *"around the
strategy I am running, over this window of settled history, what would these variants have
done?"* Deterministic replay, a constrained genome, gated mutation proposals, a per-run
virtual ledger and component-wise scoring — returned to the agent as **evidence**, with the
decision left entirely to the agent.

Explicitly not "go evolve us a winner", and explicitly not a second population.

## Context

The operator's handoff described building "Evo": a population of strategy agents whose
genomes mutate, reproduce and retire, proven on historical replay. The repository already
contains a system called Evo — `kalshi_bot/evo/`, an implemented LLM-agent organism where
`EvoAgent` is an autonomous agent with a cognitive genome, memory, heartbeats and peer
learning, and `EvoCohort` is a wall-clock calendar window with real selection.

The first attempt (`DEC-002`) built the handoff literally, as a parallel layer with its own
`evo_pop_*` lifecycle. Review rejected that shape: the machinery was right, its placement
was wrong. A `StrategySpec` is the **trading-policy portion of an organism**, not an
organism; wrapping a backtest search in generations, reproduction and retirement produced a
second answer to "who is alive" and a second definition of fitness. `DEC-003` records the
correction.

## Current Mental Model

```text
EvoAgent  (kalshi_bot/evo/ — unchanged, authoritative)
  cognitive genome · memory/beliefs · heartbeats · research · peer learning
  cohort fitness + selection · reproduction · retirement
  trading genome + active strategy (evo_strategies)
      │  invokes, from its own heartbeat, against its own sandbox budget
      ▼
  historical search  (kalshi_bot/evo/search/)
      replay the base spec and a bounded neighbourhood around it
      gate every proposal · score what survived · rank the adequate ones
      │  returns EVIDENCE — a dict, no authority
      ▼
  the AGENT reasons, and may then save_strategy / activate_strategy / revise
```

Durable artifacts, three tables, one question each:

```text
evo_search_runs        one invocation: which agent, which cohort, which heartbeat,
    │                  which trading-genome revision, which strategy, which window
    ▼
evo_search_candidates  the base (idx 0) and each neighbourhood point around it,
    │                  admitted or refused, with the reason either way
    ▼
evo_search_trades      that point's replayed tape — so "why did this do better?"
                       is answerable with trades, not with a number
```

Five properties everything else rests on:

1. **One replay engine.** `evo/sandbox.run_backtest` is reused, not forked. The proving
   corpus registers into it under a `synthetic:` namespace, so the proving run exercises
   the code the real datasets use.
2. **A genome is content-addressed.** `genome_hash` is a SHA-256 over the normalized
   document with labels stripped, so a renamed duplicate cannot pass the novelty floor.
3. **Raw P&L decides nothing.** Nine persisted components; an evidence class
   (`adequate` / `insufficient` / `invalid`) decides whether a variant is ranked at all.
4. **The search writes no genome.** Every public entry point in `mutation.py` is pure —
   no `session` parameter — so there is no writer to bypass.
5. **Search scoring is not agent fitness.** It never reaches `evo_fitness`, and the
   `insufficient → unranked` rule is documented, in code, as a property of measuring
   strategies that must never become an agent-selection rule.

---

## Review redirect (2026-08-25) — how the shape changed

The owner reviewed [#261](https://github.com/50thycal/kalshi_bot/pull/261) and rejected the
architecture while keeping the implementation: **this should be a capability the existing
Evo agents invoke, not a second evolutionary organism.**

### Concept mapping, and what happened to each

| `evo_pop_*` (first attempt) | Existing owner of the concept | Outcome |
|---|---|---|
| `evo_pop_programs` | fleet config (`evo_config_versions`) | **Dropped** — the fleet is the program |
| `evo_pop_generations` | `evo_cohorts` | **Dropped** — duplicative lifecycle |
| `evo_pop_candidates` | `evo_agents` | **Dropped** — duplicative identity |
| `evo_pop_genomes` | `evo_genomes` / `evo_strategies` | **Dropped** — a search reads the agent's own spec and writes none |
| `evo_pop_runs` | `evo_sandbox_runs` | **Rescoped** → `evo_search_runs`, owned by an agent |
| `evo_pop_run_trades` | — | **Kept** → `evo_search_trades` |
| `evo_pop_ledgers` | `evo_portfolios` is *prospective*; no replay ledger existed | **Kept**, folded onto the candidate row |
| `evo_pop_fitness` | `evo_fitness` | **Demoted** → `search_score` on the candidate, never agent selection |
| `evo_pop_decisions` | `evo_births` / `evo_retirements` / `evo_transitions` | **Dropped** |
| `evo_pop_journal` | `evo_memories` | **Dropped** — agents already have memory |
| `evo_pop_findings` | `evo_tickets` + XOS issues | **Dropped** |

Twelve tables became three. Migration `b1d4f6a80c93` had not merged, so it was rewritten
rather than followed by a drop migration.

### The intent mismatch worth naming

Intent mismatch #1 was not a preference; it was a regression against a decision this
codebase already made. `evo/fitness.py` component 4 reads *"evidence & opportunity use — no
incubation: a no-trade agent scores near 0"*, and the profit/risk components carry a comment
explaining that scoring no-exposure agents generously once made *opting out of trading rank
above trading and losing, which inverts the north star*.

Low evidence there is a **scoring penalty**. The first attempt's `insufficient → unranked`
would have been an **exemption from selection**. It survives as a search-tool convention — a
six-trade sample genuinely cannot order two *strategies* — and the code says so where
someone would be tempted to reuse it.

## Decisions Made

- **A capability, not a layer.** `DEC-003`. No program, generation, candidate, decision,
  reproduction or retirement. `evo_agents` / `evo_cohorts` / `evo_genomes` / `evo_fitness`
  remain the only lifecycle in the repository.
- **The attach point is the action protocol.** `search_strategy_space` sits beside
  `run_backtest` in `evo/cognition.py`, charges `neighbourhood + 1` against the same
  sandbox-run budget, and returns evidence into the same loop. No new orchestrator.
- **A refused search costs nothing.** Affordability is checked against the worst case,
  but the charge is the replays that actually ran. `run_search` validates the whole call
  — no saved strategy, an unknown gene, a no-op proposal, an incoherent base — before
  replaying anything, so "refused before anything is written" holds in the budget ledger
  too, not only in `evo_search_*`.
- **A search resolves to the revision the agent is running.** The named-strategy lookup
  is constrained to a runnable status, because `evo_strategies` versions by (agent, name,
  revision) and the newest row under a name can be one the agent never deployed.
- **The agent names what it wants tested.** `proposals` (`{path, value, hypothesis}`) are
  measured first and carry the agent's hypothesis into the result; `dimensions` steers the
  *automatic* perturbation for when the agent knows the axis but not the value. Both go
  through the same five gates. Deterministic perturbation is the fallback, not the
  intelligence — the dependency the first review said was reversed.
- **A search defaults to the agent's active `evo_strategies` spec.** A `TradingGenome` is
  policy prose and its schema forbids extra keys, so there are no replayable parameters
  inside it to search around. The trading-genome revision is still recorded, as attribution.
- **Experiment OS binding is by reference only** (unchanged from `DEC-002`). Search runs are
  not imported as XOS evidence; spec §22.7 stands. A strategy that earns advancement enters
  the normal XOS path through a session with the authority to register it.
- **`StrategySpec` is the genome.** A second strategy representation would have meant a
  second answer to "what does this strategy do" and a forked replay engine.
- **Risk genes are not independently mutable.** The replay visits markets sequentially and
  enforces no risk cap, so perturbing one produces a variant that provably cannot differ
  from the base. They stay in the genome, in distance and in the envelope check.
- **The novelty floor is measured over the axes under search.** Against the full 23-gene
  surface every single-gene step reads as a near-duplicate, so a targeted search — the
  documented use of `dimensions` — would refuse its whole neighbourhood. The recorded
  `distance_from_base` stays whole-surface, so it remains comparable between runs.

## Open Decisions

- **D1.** Use `backfill_weather` first, on the fixed target-date window
  `2026-08-01..2026-08-03`; then use `mmsell` separately to exercise the fill-model
  correction. The dataset choice is made. D1 remains open only until the fixed window
  produces two identical, non-empty, untruncated fingerprints for both the taker and maker
  specs. The unwindowed 2026-08-27 attempts are diagnostic evidence, not the proving run.
- **D2.** Should the replay engine enforce `risk.max_concurrent_positions` and per-position
  cost, so risk genes become mutable and the ledger's capital constraint becomes binding
  rather than measured after the fact? A change to a shared engine, so Platform Change
  Review work, not a workstream decision.
- **D3.** What universe vocabulary should a research or LLM proposer draw from when moving
  `universe.series_prefixes`? Only explicit sweeps can move it today.
- **D6.** Should agents be nudged toward the search in the heartbeat prompt, or left to
  discover it? Currently documented in the action protocol and otherwise unprompted, so its
  first use is the agents' own choice — which also makes early usage a signal.

*(D4 and D5 are closed: the search got its own three tables, and the whole refactor landed
on this branch.)*

## D1 real-dataset proving — diagnostic and pre-registration

The first production-DB attempts on 2026-08-27 used the existing read-only
`evo_backtest_probe` with `persist=False` and `charge_budget=False`. They proved that
the `backfill_weather` adapter reaches real settled markets with
`provenance=kalshi_rest_backfill`, but they did **not** prove reproducibility:

| evidence | taker | maker | disposition |
|---|---|---|---|
| `ops/results/ws6-d1-weather-20260827-1.txt` | 21,350 rows / 583 trades | 22,430 rows / 580 trades | diagnostic only; both truncated |
| `ops/results/ws6-d1-weather-20260827-2.txt` | 200,013 rows / 5,354 trades | 200,013 rows / 5,047 trades | diagnostic only; both truncated |

The difference is explained by the shared sandbox bounds: a replay stops at the earlier of
`sandbox_max_seconds=60` and `sandbox_max_rows=200000`. The first run hit the
machine-time boundary; the second hit the row boundary. The resulting market sets and P&L
cannot be compared as identical evidence. This is a proving-harness defect, not evidence for
or against either strategy.

The fixed-window run is pre-registered before seeing its result:

- dataset `backfill_weather`, target dates `2026-08-01..2026-08-03`;
- the existing broad taker and maker specs, unchanged;
- two repetitions per spec, over the same read-only database snapshot available to the job;
- PASS only when both specs are non-empty, neither repetition is truncated, and the stable
  result fingerprint matches between repetitions;
- expected provenance `kalshi_rest_backfill`;
- no strategy or edge verdict. P&L is output for reconciliation only and authorizes nothing.

`scripts/evo_backtest_probe.py` now exposes `--date-from`, `--date-to`, `--repeat`
and `--require-complete`, strips `elapsed_ms` before hashing, and exits non-zero when the
pre-registered conditions fail. The ops runner executes default-branch code, so the final
run happens only after this follow-up PR merges.

## Assumptions

- The synthetic proving corpus answers *mechanical* questions only. It says nothing about
  whether any strategy family has an edge, and the report says so.
- Replay outcomes are deterministic only over an explicit corpus that finishes before the
  sandbox guards. The maker-fill gate is a hash of the market key, but an unwindowed run can
  stop at the 60-second wall-clock guard and therefore select a machine-speed-dependent
  prefix. D1 requires a fixed window, no truncation and matching fingerprints.
- Agents will not be *worse* off for having the tool: it costs the same budget a
  hand-written backtest costs, and the protocol tells them a higher score over one window
  is one window of evidence.

## Non-Goals

- Live trading, real-money deployments, or any arming path. There is none in the package.
- Auto-promotion of a search winner into Experiment OS, or an `EVO_LIVE` state.
- LLM-authored code mutations. A proposer emits `(gene, value)` pairs against an allowlist
  and cannot express anything else.
- Prospective paper cohorts (handoff Phase 14): designed, not enabled, awaiting approval.
- Replacing, migrating or modifying the LLM-agent organism.

## Build Card

Delivered — see Implementation State.

## Implementation State

`kalshi_bot/evo/search/` — 9 modules: `genome`, `replay`, `fitness`, `mutation`,
`diversity`, `models`, `search`, `proving`, `proving_run`. Migration `b1d4f6a80c93` creates
three tables from the ORM objects, so the DDL cannot drift from `models.py`; verified
upgrade/downgrade against Postgres 16 locally.

Wiring into the organism:
- `evo/cognition.py`: the `search_strategy_space` action and its protocol documentation.
- `evo/constitution.py`: one entry in `PERMITTED_ACTIONS`.

Three additive changes to shared code, all default-off:
- `evo/sandbox.py`: `return_trades`, `register_dataset`, `skip_crossed_quotes`
  (crossed-quote *counting* is unconditional and inert; *skipping* is opt-in).

Tests: `test_evo_search_{genome,replay,fitness,mutation,proving,agent_invocation}.py`.

## Review State

**PR #261 merged 2026-08-27** at reviewed head
`0330a855744400acaa8621fc17deac508178b56d`. The historical-search capability and its
13/13 synthetic proving run are on the default branch. The post-merge D1 attempt exposed
that the existing real-data smoke probe did not distinguish a valid fixed-corpus proof from
a wall-clock-truncated prefix; this follow-up returns the workstream to BUILDING for the
bounded proving harness only.

**Owner review 2026-08-25 (round 1): architectural correction required.** Addressed by the
refactor above and recorded as `DEC-003`. Three concrete code blockers were raised in the
same review and remain fixed:

| Blocker | Fix, and where it stands now |
|---|---|
| `Admission` was forgeable — a caller could construct `Admission(ok=True)` and skip every gate | The writer that trusted it no longer exists. `mutation.py` has no `session` parameter on any public entry point and cannot write a genome; a test asserts that absence rather than asserting a re-check |
| Crossed-book skip changed shared replay semantics for every caller | Skipping is opt-in (`skip_crossed_quotes`, default off). Counting stays unconditional and inert, so the defect is visible without changing anyone's numbers. Making it the default is Platform Change Review work |
| Settlement `exited_at` was a synthesized last-candle timestamp, then used for exact concurrency/exposure | Settlement exits are flagged `exit_time_exact=False`; the sweep line uses exact exits only and reports `concurrency_coverage`. Drawdown still uses every trade, since ordering tolerates a lower bound |

Proving run: **13/13 CLEAN** — ten capability checks plus three adversarial cases, run in
CI by `tests/test_evo_search_proving.py`.

Defects the proving run surfaced, fixed rather than documented around:
- Mutating a `risk.*` gene produced variants with byte-identical tapes to the base. Root
  cause: the replay enforces no risk cap. Fixed by removing those genes from independent
  mutation and adding an inert-mutation detector.
- `exit.mode` switches were inert or refused because the new mode's threshold was unset,
  making the whole exit axis unreachable by perturbation. Fixed with companion genes.
- Adversarial case A1 first passed by ~9e-6, because the synthetic "steady" and "reckless"
  profiles did not match their names. Retuned so reckless earns *more* ($262 vs $219) and
  scores *clearly lower* (0.649 vs 0.857), on drawdown and tail control. A check that passes
  by rounding error is not evidence the evaluator distinguishes the cases.

Defect found by writing the agent-invocation test:
- The novelty floor was measured over the whole 23-gene surface, so a search narrowed to
  one gene refused its entire neighbourhood. Scoped to the axes under search.

## Related Decisions

`DEC-002` (superseded), `DEC-003`.

## Related PRs

[#261](https://github.com/50thycal/kalshi_bot/pull/261) (merged) and this D1 proving-harness
follow-up PR.

## Next Step

Merge the fixed-window proving-harness follow-up, then run:

```json
{"type":"script","name":"evo_backtest_probe","args":["--dataset","backfill_weather","--date-from","2026-08-01","--date-to","2026-08-03","--repeat","2","--require-complete"],"id":"ws6-d1-weather-fixed-20260827"}
```

If both stable fingerprints match and both specs are non-empty and untruncated, record D1
clean and complete WS-006. Otherwise keep the workstream active and investigate the exact
failed condition. Do not start a prospective cohort; D2 and explicit operator approval
remain separate prerequisites.
