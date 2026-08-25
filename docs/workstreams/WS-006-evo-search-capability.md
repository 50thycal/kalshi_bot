# WS-006 — Evo historical search capability

**Phase:** REVIEW
**Status:** Active (re-submitted after the 2026-08-25 architectural correction)
**Created:** 2026-08-25
**Updated:** 2026-08-25

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

- **D1.** Which real dataset should the first non-synthetic proving run use —
  `backfill_weather` (largest settled corpus) or `mmsell` (live tick tape, calibrated maker
  fills)? Recommendation: `backfill_weather` for breadth first, then `mmsell` to exercise
  the fill-model correction.
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

## Assumptions

- The synthetic proving corpus answers *mechanical* questions only. It says nothing about
  whether any strategy family has an edge, and the report says so.
- Deterministic replay holds because the engine has no wall-clock or RNG dependence: the
  maker-fill gate is a hash of the market key. Verified by re-running and comparing
  fingerprints, not assumed.
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

[#261](https://github.com/50thycal/kalshi_bot/pull/261) on `claude/evo-foundation-build-d7bp2b`.

## Next Step

Owner review of the corrected shape. Then **D1**: a proving run on a real settled dataset,
which is the prerequisite before any prospective (paper) extension is proposed.
