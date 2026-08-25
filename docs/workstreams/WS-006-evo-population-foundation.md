# WS-006 — Evo population foundation (evolutionary search over strategy genomes)

**Phase:** REVIEW
**Status:** Active
**Created:** 2026-08-25
**Updated:** 2026-08-25

## Goal

A controlled evolutionary research layer on top of Experiment OS: a population of
candidate strategies whose genomes mutate, reproduce and retire under a recorded policy,
proven first on historical replay where every answer is attributable, replayable and
testable. Infrastructure plus a proving run — explicitly not "go evolve us a winner".

## Context

The operator's handoff described building "Evo" as a new system. The repository already
contains one: `kalshi_bot/evo/`, an implemented LLM-agent organism (38 tables, ~500KB,
31 test files) where `EvoAgent` is an autonomous agent with a cognitive genome, heartbeats
and memory, and `EvoCohort` is a wall-clock calendar window.

That system and the handoff's design share vocabulary and almost nothing else. The
handoff describes a program-scoped, deterministic search over structured *strategy
parameters*, evaluated by historical replay. The organism is a set of agents that live in
real time and write their own strategies. Both are legitimate; neither subsumes the other.

The operator chose (2026-08-25) to build the new layer **alongside** the organism in its
own `evo_pop_*` namespace, and to bind to Experiment OS **by reference only**.

## Current Mental Model

```text
EVO PROGRAM            one evolutionary configuration: policy, dataset, capital,
    │                  fitness weights, allowed mutation surface, XOS snapshot
    ▼
EVO GENERATION         one evaluation population over one replay window,
    │                  with a data_cutoff that is the no-look-ahead boundary
    ▼
EVO CANDIDATE          a durable identity. It does not change; its lineage does.
    │                  A parent is never mutated in place.
    ▼
EVO GENOME VERSION     an immutable, content-addressed StrategySpec document.
    │                  Material change ⇒ new version, never an edit.
    ▼
EVO RUN                one genome × one window, with its own virtual ledger
    │                  and a reproducibility fingerprint
    ▼
TRADES + EVIDENCE      a per-trade tape; the ledger and fitness are recomputable
                       from it

decisions:  EvoDecision (continue | reproduce | mutate | retire | hold | escalate)
            — every state change writes one, with evidence and thresholds

mutation:   PROPOSE ─────────────────────► ACCEPT
            sweep / perturbation /         schema → compatibility → risk →
            research / (later) LLM         novelty → provenance → admission
            no authority                   the only path to a genome
```

Three properties everything else rests on:

1. **One replay engine.** `evo/sandbox.run_backtest` is reused, not forked — the same
   loop the LLM organism's backtests and the ops probes run. The proving corpus registers
   itself into it under a `synthetic:` namespace, so the proving run exercises the code
   the real datasets use.
2. **A genome is content-addressed.** `genome_hash` is a SHA-256 of the normalized
   document with labels stripped, so immutability is checkable after the fact and a
   renamed duplicate cannot be smuggled past the novelty floor.
3. **Raw P&L decides nothing.** Fitness is nine persisted components; evidence class
   (`adequate` / `insufficient` / `invalid`) gates whether a candidate is ranked at all.

## Decisions Made

- **New parallel layer, not an extension or a replacement.** The organism keeps
  `EvoAgent`/`EvoCohort`/`EvoGenome`; this layer uses `EvoProgram`/`EvoGeneration`/
  `EvoCandidate`/`EvoGenomeVersion` in `evo_pop_*`. Overloading three live concepts with
  two meanings each would have made both unreadable, and migrating a running subsystem to
  do it was unjustified.
- **Experiment OS binding is by reference only.** The platform-snapshot fingerprint is
  recorded on program, genome and run; XOS metric definitions and the shared fill
  calibration are reused. Evo runs are **not** imported as XOS evidence. That respects
  spec §22.7 (`importer.py`: evo lineage stays out of XOS) and needs no Platform Change
  Review; a candidate that earns advancement enters the normal XOS path later.
- **`StrategySpec` is the genome.** A second strategy representation would have meant a
  second answer to "what does this strategy do" and a forked replay engine.
- **Only `replay` mode is enabled.** `paper` and `shadow` are reserved and refused at
  `create_program`, so prospective cohorts cannot start by accident.
- **Risk genes are not independently mutable.** The replay visits markets sequentially
  and enforces no risk cap, so perturbing one produces a child that provably cannot
  differ from its parent. They stay in the genome, in distance and in the envelope check.

## Open Decisions

- **D1.** Which real dataset should the first non-synthetic proving cohort use —
  `backfill_weather` (largest settled corpus) or `mmsell` (live tick tape, calibrated
  maker fills)? Recommendation: `backfill_weather` for breadth first, then `mmsell` to
  exercise the fill-model correction.
- **D2.** Should the replay engine be taught to enforce `risk.max_concurrent_positions`
  and per-position cost, so risk genes become mutable and the virtual ledger's capital
  constraint becomes binding rather than measured after the fact? This is a change to a
  shared engine, so it is Platform Change Review work, not a workstream decision.
- **D3.** What universe vocabulary should a research/LLM proposer draw from when moving
  `universe.series_prefixes`? Currently only explicit sweeps can move it.

## Assumptions

- The synthetic proving corpus answers *mechanical* questions only. It says nothing about
  whether any strategy family has an edge, and the proving report says so explicitly.
- Deterministic replay holds because the engine has no wall-clock or RNG dependence: the
  maker-fill gate is a hash of the market key. Verified by re-running and comparing
  fingerprints, not assumed.
- The LLM organism is unaffected: `DATASETS` (the tuple `cognition.py` validates against)
  is deliberately not extended, so no evo agent can backtest against a synthetic corpus.

## Non-Goals

- Live trading, real-money deployments, or any arming path. There is none in the layer.
- Auto-promotion of an Evo winner into Experiment OS, or an `EVO_LIVE` state.
- LLM-authored code mutations. A proposer emits `(gene, value)` pairs against an
  allowlist and cannot express anything else.
- Prospective paper cohorts. Designed (Phase 14) but not enabled; awaiting approval.
- Replacing, migrating or modifying the LLM-agent organism.

## Build Card

Delivered — see Implementation State.

## Implementation State

PR open. `kalshi_bot/evo/population/` (13 modules), migration `b1d4f6a80c93` (12 tables,
created from the ORM objects so the DDL cannot drift), 5 test files (~120 tests), an
ops-runnable Control Tower script, and a CLI.

Three additive changes to shared code, all default-off:
- `evo/sandbox.py`: `return_trades`, `register_dataset`, crossed-book detection.
- `scripts/ops_runner.py`: one allowlist entry.
- `docs/EVO_POPULATION_FOUNDATION.md`: the design record.

## Review State

Historical proving run: **14/14 checks CLEAN** — 30 candidates, 3 generations,
non-overlapping windows, reproduction and retirement enabled.

Two defects the proving run surfaced and that were fixed rather than documented around:
- Mutating a `risk.*` gene produced children with byte-identical trade tapes to their
  parents. Root cause: the replay enforces no risk cap. Fixed by removing those genes
  from independent mutation and adding an inert-mutation detector (check 11).
- `exit.mode` switches were inert or refused because the new mode's threshold was unset,
  making the whole exit axis unreachable by perturbation. Fixed with companion genes.

## Related Decisions

`DEC-002` (see `DECISIONS.md`).

## Related PRs

`claude/evo-foundation-build-d7bp2b`.

## Next Step

Operator approval for the first prospective paper cohort, or for a real-dataset
historical cohort (D1). The exact configuration and commands are in
`docs/EVO_POPULATION_FOUNDATION.md` § "The first prospective paper cohort"; nothing
starts without it.
