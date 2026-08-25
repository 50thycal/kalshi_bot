# Evo population foundation — design record

Status: **implemented (historical replay only)**. No live path, no prospective paper
cohorts, no Experiment OS promotion.
Package: `kalshi_bot/evo/population/`. Workstream: [WS-006](workstreams/WS-006-evo-population-foundation.md).
Decision: `DEC-002` in [DECISIONS.md](DECISIONS.md).

---

## 1. What this is, and what it is not

This is a controlled evolutionary **search** over strategy genomes: a population of
candidates whose parameters mutate, reproduce and retire under a recorded policy, scored
by deterministic replay over settled history.

It is **not** the evolutionary agent system in `kalshi_bot/evo/`
([EVOLUTIONARY_AGENT_SYSTEM.md](EVOLUTIONARY_AGENT_SYSTEM.md)). That is an LLM organism:
autonomous agents with cognitive genomes, heartbeats, memory, budgets and prospective
paper trading in wall-clock time. The two share a package root, a replay engine and a fee
model, and nothing else.

| | LLM organism (`evo/`) | Population layer (`evo/population/`) |
|---|---|---|
| What evolves | an agent's cognitive + trading policy | a strategy parameter vector |
| Who writes strategies | the agent, via an LLM | a mutation proposer, from an allowlist |
| Time | wall-clock, prospective | historical windows, replayed |
| `EvoAgent` / `EvoCohort` / `EvoGenome` | **its** meanings | not used — `EvoCandidate` / `EvoGeneration` / `EvoGenomeVersion` |
| Tables | `evo_*` | `evo_pop_*` |

The naming split is deliberate. Overloading three live concepts with two meanings each
would have made both systems unreadable, and migrating a running subsystem to free the
names was unjustified for a layer that had not yet proven itself.

## 2. Object model

```text
EvoProgram        one evolutionary configuration
  └─ EvoGeneration      one population × one window, with a data_cutoff
       └─ EvoCandidate       a durable identity (lineage, not genome)
            └─ EvoGenomeVersion   immutable, content-addressed
                 └─ EvoRun             one genome × one window
                      ├─ EvoRunTrade        the per-trade tape
                      └─ EvoCandidateLedger  that candidate's own virtual account
```

Alongside: `EvoFitness` (components + derived score), `EvoDecision` (the evolutionary
record), `EvoMutationProposal` (accepted *and* rejected), `EvoJournalEntry` (memory),
`EvoFinding` (work items).

Twelve tables, migration `b1d4f6a80c93`, created from the ORM objects so the DDL cannot
drift from `models.py` — the same technique as the evo base and announcements migrations.

## 3. The genome

A genome **is** a `StrategySpec` document (`evo/strategy_spec.py`) — the typed DSL the
sandbox already replays. Reusing it avoids a second answer to "what does this strategy
do" and a forked replay engine.

What the population layer adds:

- **A mutation surface.** 23 genes, each with a type, range, step, distance weight, an
  `independent` flag, and a compatibility rule saying when it applies at all. Nothing
  outside it is mutable. Print it with `python -m kalshi_bot.evo.population.cli surface`.
- **Canonical normalization + hash.** `genome_hash` is SHA-256 over the normalized
  document with `name`/`description` stripped and entry conditions sorted. Renaming a
  genome does not make it a new genome; reordering its conditions does not either.
  Immutability is therefore *checkable*: a mutated row stops matching its hash.
- **A distance metric.** Weighted mean per-gene distance in [0, 1]. Numeric genes
  normalize by span; enums are 0/1; `universe.series_prefixes` uses Jaccard, because two
  genomes with identical rules over disjoint universes are not the same strategy.
  A gene that applies to neither genome contributes nothing — `take_profit_cents` on a
  settlement-exit genome is not a real difference.

Three genes are in the surface but **not independently mutable**:

- `risk.max_concurrent_positions`, `risk.max_per_event`, `risk.max_cost_per_position_usd`.
  `sandbox.run_backtest` visits markets one at a time and holds at most one position per
  market, so it enforces none of them. Perturbing one produces a child whose tape is
  provably identical to its parent's. They remain part of identity, distance and the
  program's risk envelope, and an explicit proposal may still set them.
- `universe.series_prefixes` / `categories`: no vocabulary to step through, so blind
  perturbation would invent prefixes matching no market — a zero-trade run that reads as
  "no edge".

## 4. Replay

Delegates to `evo.sandbox.run_backtest`. What this layer owns around it:

- **No look-ahead, by refusal.** A window reaching past its generation's `data_cutoff` is
  refused, not trimmed. Trimming would mean two candidates asking for different windows
  quietly get the same one, and the run rows would claim coverage the evidence lacks.
- **Determinism.** The engine has no wall-clock or RNG dependence — the maker-fill gate
  is a hash of the market key (`evo/fill_model.py`). Each run records an
  `outcome_fingerprint`; re-running must reproduce it.
- **Isolation.** Each run builds its own ledger from its own tape. No shared wallet.
- **Three quantities kept apart**, per the handoff:
  - *theoretical opportunity* — gross, before costs;
  - *paper execution* — net of Kalshi fees, what the replay banked;
  - *realizable* — the same trades projected through the measured maker-fill
    calibration (`kalshi_bot/fill_calibration.py`), which carries the adverse-selection
    correction. Collapsing these is how a paper edge that only exists in fills we would
    never receive gets mistaken for a real one.

### Additive changes to the shared engine

All default-off, so no existing caller changes behaviour:

| Change | Why |
|---|---|
| `run_backtest(return_trades=True)` | the ledger needs the per-trade tape; the agent path only reads aggregates, and the tape is never persisted into `EvoSandboxRun.result_json` |
| `_trade()` carries entry/exit times, prices, fees | concurrency and exposure cannot be reconstructed without them |
| `register_dataset("synthetic:*", …)` | the proving corpus replays through *this* loop. `DATASETS` (what `cognition.py` validates against) is deliberately **not** extended, so no evo agent can backtest a synthetic corpus and read the result as evidence |
| crossed-book detection | a quote with bid ≥ ask is corrupt. The step is skipped (fail closed) and counted, so a run built on such data is flagged rather than silently believed. This applies to the real datasets too |

### The virtual ledger

Per candidate, per generation. Starting capital, realized/unrealized P&L, fees, gross,
turnover, peak exposure, max concurrent positions, drawdown, contracts, markets,
settled vs open, concentration (top family + HHI), and a capital-breach flag.

Drawdown is computed on the equity curve ordered by **exit time**, not by the order the
replay visited markets. The replay iterates market by market, so its own trade order is
not chronological; a drawdown read off that order would be an artifact of iteration, and
would badly misrank a candidate whose losses clustered in time — which is exactly what
the drawdown component exists to catch.

Capital is **measured, not enforced**: the engine has no concurrency cap, so a breach is
recorded on the ledger and penalised through the integrity component rather than
prevented mid-run. See D2 in the workstream.

## 5. Fitness

Nine components, each persisted with its raw measurement, its normalized score, its
weight and its contribution — so a rank can be unfolded rather than asserted.

| Component | Default weight | What it catches |
|---|---|---|
| `edge_lcb` | 0.30 | a lucky run: the lower confidence bound on per-contract edge, fill-adjusted where the calibration covers the trades |
| `return_on_capital` | 0.10 | scale relative to the virtual account |
| `drawdown_control` | 0.20 | a reckless run |
| `tail_control` | 0.15 | worst-decile conditional loss, not the single worst trade |
| `stability` | 0.10 | Laplace-smoothed sign consistency across subwindows |
| `exposure_efficiency` | 0.05 | return per turnover dollar |
| `concentration` | 0.04 | one-event dependence |
| `breadth` | 0.03 | distinct market families |
| `integrity` | 0.03 | capital breach, concurrency over cap, uncalibrated fills |

Weights and scales are **program configuration**, renormalized to sum to 1 so ranks stay
comparable across programs.

**Evidence class gates ranking.** `adequate` candidates are ranked against each other;
`insufficient` (below the program's trade minimum) and `invalid` (failed run, truncated
replay, corrupt data) get a fitness row so the Tower can explain them, but no rank. That
separation is what stops a thin sample winning on noise and what stops a data defect
being quietly retired as a bad strategy.

## 6. Evolution

```text
open → run every active candidate → evaluate → rank → decide
     → retire bottom, continue middle, reproduce from top → scan findings → journal → close
```

Ordering is enforced in `service.advance`, not documented and hoped for: nothing is
decided before everything is evaluated, or an early retirement would change the
denominator the later ranks were computed against.

Default policy: 30 candidates, top 30% reproduce, middle 40% continue, bottom 30% retire
— configuration, not scientific truth. With 30 candidates that is 9/12/9; the middle
absorbs the remainder so neither acting group overruns the other.

Every state change writes an `EvoDecision` carrying evidence, thresholds, evaluator
revision, rank and reason. `continue | reproduce | mutate | retire | hold | escalate`.

**Reproduction never mutates the parent.** The parent keeps its identity, genome and
ledger history and stays eligible next generation; the child is a new row with
`parent_uuid` set and its genome's `parent_genome_id` pointing at the parent's.

## 7. Mutation: PROPOSE is not ACCEPT

```text
propose_perturbation / propose_sweep / (later) an LLM
        │  a MutationProposal — a description, with no authority
        ▼
evaluate_proposal:  surface legality → schema → compatibility → not-a-no-op
                    → risk envelope → novelty/duplicate
        │
        ▼
admit_proposal      the ONLY path to a genome; refuses an admission that did not pass
```

An LLM proposer plugs in at the `propose` end and inherits every gate. It cannot reach
admission directly, and it cannot express a change outside the gene surface, because a
proposal is `(path, value)` pairs against `MUTATION_SURFACE`. That is the structural
reason it can never rewrite production code as a mutation.

Proposals are persisted **whether or not** they are admitted. A rejection records that a
branch of the space was tried and why it was refused, which is what stops the same
invalid mutation being reproposed every generation
(`knowledge.refused_mutations`).

Mode switches carry **companion genes**: changing `exit.mode` to `tp_sl` without
thresholds produces a rule that never fires, so the perturbation path seeds them. Without
this the whole exit axis is unreachable.

## 8. Diversity

- Duplicate refusal (exact hash) and a near-duplicate floor (`min_genome_distance`).
  Novelty is checked against **every genome the program ever admitted**, not just the
  living population: a genome retired for being bad is not a good idea again just because
  nothing currently resembles it.
- Measured per generation: mean/min pairwise distance, distinct genomes, family shares,
  parent shares. The Control Tower warns on collapse, duplicates, or ≥70% family / ≥50%
  parent concentration.
- Deliberately **not** novelty search. No bonus steers selection toward strangeness; a
  novelty pressure tuned before we can measure whether the loop works would be untestable.

## 9. Memory and shared knowledge

`EvoJournalEntry` keeps four registers apart — `observation`, `interpretation`,
`hypothesis`, `decision` (plus `lesson`, `failure_mode`). Conflating them is how a child
inherits "longshots are toxic" as a *fact* when what happened was one cohort, one window,
one measurement.

Only entries flagged `heritable` cross a generation boundary, stamped with
`inherited_from`, so a child can tell what it learned from what it was told.

Retrieval (`knowledge.py`) is **scoped**, never "everything": by lineage, by strategy
family, by genome distance, by failure mode, by candidate. `context_for()` returns a
bounded bundle — that is the whole retrieval budget. The project's research library is
reachable through the organism's existing `evo.knowledge` reader, as second-hand context,
never as evidence about a genome.

## 10. Evo Control Tower

Read-only, structurally: it imports no service and opens no transaction.

```
python -m kalshi_bot.evo.population.cli tower   --program <key> [--generation N] [--json]
python -m kalshi_bot.evo.population.cli explain --program <key> agent-017
python -m kalshi_bot.evo.population.cli lineage --program <key>
python -m kalshi_bot.evo.population.cli findings --program <key>
```

Via the ops channel: `{"type":"script","name":"evo_pop_tower","args":["--program","<key>"],"id":"..."}`.

`explain` unfolds a rank into every component's score × weight = contribution, with the
raw measurement behind each. That is the answer to "why did agent X outperform agent Y?"
that a leaderboard cannot give.

## 11. Evo findings (Ticket Workshop)

`EvoFinding` rows, deduplicated on a stable key so a condition persisting across four
generations is one problem seen four times, not four problems.

Detected automatically: invalid genomes, sample-starved cohorts, diversity collapse,
duplicate genomes, risk breaches, proposal-refusal storms, and **inert mutations** (a
child whose tape is identical to its parent's).

Routes to the role that owns the problem — `evo_ticket_workshop`, `research_lab`,
`experiment_os_issue`, `platform_change_review`, `mutation_candidate`. There is no fixer
role. A finding **authorizes nothing**: it never changes a lifecycle state, gate, verdict,
epoch or exposure. Closing one requires a concrete resolution; an empty close raises.

Distinct from `evo_tickets` (the LLM fleet's capability requests, triaged by
`evo-ticket-triage`) and from Experiment OS issues (anomalies in registered experiments).

## 12. Relationship to Experiment OS

Reference-only, by decision (`DEC-002`).

**Reused:** the platform-snapshot fingerprint (recorded on program, genome and run for
provenance), XOS metric definitions, the shared maker-fill calibration, statistical bounds
conventions.

**Not done:** Evo runs are not imported as XOS evidence, no experiment is registered, no
lifecycle state moves, no gate is evaluated. XOS spec §22.7 keeps evo lineage out of XOS
by design (`experiment_os/importer.py`), and reversing that is a shared-semantic change —
Platform Change Review work, not this workstream's.

A candidate that earns formal advancement enters the **normal** XOS path through a session
with the authority to register it. There is no `EVO_LIVE`, and `service.py` has no arm,
promote or deploy call.

## 13. The historical proving run

```
python -m kalshi_bot.evo.population.cli proving-run --program proving-1 --generations 3 --cohort 30
```

30 candidates, 3 generations, three non-overlapping 40-day windows over a deterministic
synthetic corpus, reproduction and retirement enabled.

The corpus is synthetic **on purpose**. Every question the proving run asks is about the
machinery, not about whether a strategy makes money, and synthetic history answers those
better because the right answer is known in advance and the adversarial cases can be
constructed rather than hoped for. It registers into the shared replay engine, so it
exercises the same loop the real datasets use.

### Result: 14/14 CLEAN

| # | Check | Evidence |
|---|---|---|
| 1 | genomes valid and immutable | 40 genomes, 0 hash mismatches, 0 invalid |
| 2 | runs reproduce | 5 re-run, 0 fingerprint mismatches |
| 3 | rankings are explainable | 14 ranked, 0 whose components fail to reconstruct their score |
| 4 | parents and children correct | 10 children, 0 lineage defects, 0 overwritten parents |
| 5 | retirement works | 13 retired with 13 decisions, 0 resurrected |
| 6 | ledgers reconcile | 88 ledgers, 0 that fail to tie to their own tape |
| 7 | no look-ahead | 0 trades past window end; an over-reaching window is refused |
| 8 | mutations have exact provenance | 10 children, 0 whose recorded diff ≠ actual diff |
| 9 | diversity observable | mean pairwise distance 0.366, 27 distinct of 27 |
| 10 | Control Tower explains | 69 lines, 29 candidates, each with a component breakdown |
| 11 | inert mutations detected | 2 inert children, 0 undetected, 0 structurally inert |
| A1 | reckless does not outrank steady | steady 0.7182 beats reckless 0.5262 — reckless banked **$72.07 against steady's $43.17** and still ranked lower |
| A2 | lucky is held, not crowned | n=3 → `insufficient`, rank `None` |
| A3 | broken data is invalid, not retired | `invalid`, fitness `None`, escalation recorded, state stays `active` |

A2 is worth stating plainly: the lucky candidate scores the **highest raw fitness in the
cohort** (0.864 in isolation). It is not ranked first because it is not ranked at all —
the evidence gate, not the score, is what holds it.

### Two defects the proving run found

Both were fixed, not documented around:

1. **Inert risk mutations.** Children mutated on a `risk.*` gene produced byte-identical
   trade tapes to their parents. Root cause: the replay enforces no risk cap, so those
   genes cannot affect a run. Fix: removed from independent mutation (§3), plus an
   inert-mutation detector that raises a finding whenever any child replays identically.
2. **Unreachable exit axis.** `exit.mode` switches were either inert (a `timed` exit with
   no horizon never fires) or refused (a `tp_sl` with no thresholds). Fix: companion
   genes on the perturbation path, and a compatibility rule rejecting a `timed` exit with
   no `max_hold_hours`.

---

## The first prospective paper cohort

**Not started. This section is the proposal; it needs operator approval.**

`service.create_program` refuses `mode="paper"` today, so nothing can start by accident.

### What must be built first

1. **A generation scheduler** with hard time boundaries. In replay a window is a pair of
   dates; prospectively it is a wall-clock interval, and a child born mid-generation must
   not accrue evidence from before its birth. `EvoGenomeVersion.evidence_cutoff` already
   carries the boundary; the scheduler has to honour it.
2. **A paper execution path.** Replay reads settled history; a prospective cohort needs
   live quotes and open positions, marked and settled over time. The organism's
   `evo/paper.py` already does this per agent — the question (D-new) is whether the
   population layer reuses it or keeps its own ledger writer.
3. **Capital enforcement.** Measured-not-enforced (§4) is defensible for replay, where
   every candidate is scored on the same tape. Prospectively a candidate that breaches
   its account is trading capital it does not have.

### Proposed configuration, once those exist

```bash
python -m kalshi_bot.evo.population.cli create \
    --program paper-1 \
    --objective "First prospective paper cohort: does the replay-selected population's edge survive live quotes and real fills?" \
    --dataset mmsell \
    --cohort-target 30 \
    --capital 500 \
    --min-trades 30 \
    --platform-snapshot "$(xos platform snapshot --current)"
```

- Seed from the **replay-proven** survivors of a real-dataset historical program, not
  from fresh founders — the point is to test whether replay selection transfers.
- Generation length: 14 days, so a 30-candidate cohort reaches the 30-trade evidence
  minimum on the mmsell tape rather than spending its first generations entirely `held`.
- Reproduction **off** for generation 0. Prove that prospective evidence accrues and
  reconciles before letting it drive selection.
- Kill condition, pre-registered: if ≥50% of candidates are `insufficient` after two
  generations, the cohort is mis-sized for the window and stops rather than being widened
  after seeing results.

### Before it runs

- D1 (which real dataset) and D2 (risk-cap enforcement) in
  [WS-006](workstreams/WS-006-evo-population-foundation.md) resolved.
- A real-dataset historical proving run completed and clean — the synthetic run proves the
  machinery, not that the machinery says anything true about Kalshi.
- Operator approval, explicitly, in this repository. Nothing here starts a cohort.
