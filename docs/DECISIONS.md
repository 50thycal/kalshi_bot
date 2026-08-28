# Decisions — kalshi_bot

Why does the system work this way? Lightweight ADRs, appended in order. Consequential
decisions only. An accepted entry is never rewritten because architecture later changed —
supersede it with a new one and update only the old status line.

**Build OS v0.4**

**Scope note.** This log records *development and architecture* decisions. It is **not** a
place for experiment verdicts, gate outcomes, promotions, epochs or platform revisions —
Experiment OS records those, with its own approval rules, and a copy here would be a second
ledger that drifts (`DEC-001`).

---

### DEC-001 — Adopt Build OS v0.4, and fix the Build OS / Experiment OS authority boundary

**Date:** 2026-08-24
**Status:** Accepted

**Context**

This project already has an unusually strong durable-state discipline for *experiments*.
Experiment OS is live under `NEW_ONLY` enforcement and is canonical for lifecycle,
evidence, gates, epochs, platform revisions and durable issues. Nothing about that is
missing or weak.

What has no durable home is the layer above it: **the development workflow itself.** How
the system is put together, why it is put together that way, which design threads are
currently live and what each is waiting on, and what an implementation PR actually
delivered — all of that has lived in chat sessions. Sessions end. The next session
reconstructs the picture by reading code and thesis documents and asking the operator what
was decided, which is expensive and lossy, and occasionally wrong in ways nobody notices.

Concretely, at the time of this decision three efforts were in flight at once — a
settlement-taxonomy repair in review, a paper design blocked behind it, and unresolved
gate-addressing and detector-reconciliation work on two historical live canaries whose
proposed successor Versions had been withdrawn — and the only thing holding their
relationship together was one person's memory of which conversation said what.

Build OS (`50thycal/build-os`) is a small, code-free protocol for exactly this: three
durable memory layers, a workstream lifecycle, and a PR-as-handoff rule.

**Decision**

Adopt **Build OS v0.4** as the project's development framework, pinned, with a strict
authority boundary against Experiment OS:

- **Experiment OS remains canonical** for experiments, Versions, epochs, deployments, arms,
  gates and results, platform revisions, impact actions, enforcement state and durable XOS
  issues. Build OS neither replaces nor duplicates any of it.
- **Build OS is canonical** for the human/agent development workflow: project architecture
  (`docs/PROJECT_MODEL.md`), consequential decisions (this file), Build Cards, Build Specs,
  development workstreams (`docs/workstreams/`), PR handoffs and reviews.
- **Workstreams link; they do not copy.** A workstream may reference `XOS-000006`,
  `mmsell-type-tight/v1/e1`, or `MARKET_TAXONOMY:coverage_2026_08_13`. It must not restate a
  standing, a gate verdict, a P&L figure or an epoch boundary.
- **A workstream authorizes nothing.** Its phase and status are development state. Only
  Experiment OS's own services register, arm, promote, pause or retire, under their own
  approval rules.
- **Chat is authoritative for neither, and transcripts are never archived.** Conclusions,
  models, decisions and open questions are persisted; recordings of thinking are not.

Project-specific ordering, recorded in `CLAUDE.md` under a `Project-specific:` marker: a
session establishes its **role** first, then runs the Build OS compatibility check before
substantive design/build work. The role decides what a session may write at all; the
framework check decides which protocol it writes under.

**Rationale**

The two systems answer genuinely different questions, and conflating them is the failure
mode worth spending a decision to prevent.

Experiment OS answers *what is true about this experiment* and is deliberately hostile to
convenience: verdicts must be recorded by a designated evaluator, epochs cut at measured
boundaries, and the read channel cannot write. That rigour is exactly what makes it a bad
place to park "we are still arguing about whether this design is worth 17 months" — which
is not a fact about an experiment, has no evaluator, and would be noise in a ledger built
for evidence.

Build OS is the opposite: cheap to write, meant to be edited, and honest about uncertainty.
Putting development state there costs Experiment OS nothing and gives the next session a
board it can read in fifteen seconds.

Pinning to v0.4 rather than tracking `main` is the framework's own rule and the right one
here: an in-flight design should not change shape because the framework moved underneath
it. The compatibility check is what stops the pin from rotting into staleness.

**Alternatives considered**

- **Extend Experiment OS to cover development workflow.** Rejected. It would put unreviewed,
  frequently-edited design prose into a system whose whole value is that its contents are
  recorded, evaluated and hard to change. It would also mean every design note needed a
  write path that deliberately does not exist for agent sessions.
- **Keep using thesis documents in `docs/` as the design record.** Rejected as insufficient,
  not wrong. Thesis documents are excellent per-idea evidence and stay. What they cannot do
  is answer "what are the four things currently in flight, and what is each waiting on" —
  there is no board, and nothing tells a reader which documents are live.
- **Adopt Build OS but let workstreams mirror experiment state for convenience.** Rejected
  explicitly, and this is the decision's sharp edge. A mirrored standing is stale the day
  after it is written and is believed anyway. Two ledgers that disagree are worse than one
  ledger and a link.
- **Track `main` of the framework instead of pinning.** Rejected per the framework's own
  guidance: silent drift changes an in-flight effort's shape mid-effort.

**Consequences**

*Easier:* a new session orients from `docs/workstreams/ACTIVE.md` instead of from
transcripts. PR handoffs have a standard shape and a template that makes it the path of
least resistance. Architecture and rationale outlive individual agents.

*Harder / more expensive:* there is now a checkpoint duty. Workstream files must be updated
at meaningful moments or the board becomes a liability — a stale model is worse than no
model, because it will be believed. Someone must also keep `PROJECT_MODEL.md` free of
standings; the pressure to paste "current gate: HOLD" into it will be constant.

*Expensive to reverse:* not very. Build OS adds no dependency, no service and no build step.
Abandoning it means deleting four documents and a template directory. That cheapness is
part of why it is worth trying.

*Revisit if:* workstream files start restating Experiment OS state (the boundary is
failing), or the board goes a month without an update (the checkpoint duty is not being
paid, and an unmaintained board should be deleted rather than left to mislead).

---

### DEC-002 — Build Evo as a parallel population layer, bound to Experiment OS by reference

**Date:** 2026-08-25
**Status:** Superseded by DEC-003 (2026-08-25, in review, before merge). The
`evo/population/` package and `docs/EVO_POPULATION_FOUNDATION.md` referred to below never
merged; they became `evo/search/` and `docs/EVO_SEARCH_CAPABILITY.md`.

**Context**

The operator's handoff described building "Evo": a population of strategy agents whose
genomes mutate, reproduce and retire, proven on historical replay. The repository already
contains a system called Evo — `kalshi_bot/evo/`, an implemented LLM-agent organism with
38 tables and 31 test files, in which `EvoAgent` is an autonomous agent with a cognitive
genome and heartbeats, and `EvoCohort` is a wall-clock calendar window.

The two designs share vocabulary and almost nothing else. The handoff describes a
program-scoped, deterministic search over structured *strategy parameters* scored by
replay; the organism is a set of agents that live in real time and author their own
strategies. Both are legitimate. Neither subsumes the other, and three of the names the
new design needs are already taken with different meanings.

A second question came with it. The handoff asked that Evo treat Experiment OS as the
authoritative substrate, but XOS spec §22.7 deliberately excludes evo lineage
(`experiment_os/importer.py`: "evo strategies are out of scope by design"). Honouring both
statements literally is impossible.

**Decision**

Build the new system as a **parallel layer** in `kalshi_bot/evo/population/`, with its own
`evo_pop_*` namespace and its own object names (`EvoProgram`, `EvoGeneration`,
`EvoCandidate`, `EvoGenomeVersion`, `EvoRun`, `EvoDecision`). The LLM organism is not
migrated, renamed or modified.

Bind to Experiment OS **by reference only**: record the platform-snapshot fingerprint on
program, genome and run; reuse XOS metric definitions and the shared fill calibration;
import nothing into XOS. §22.7 stands. A candidate that earns advancement enters the
normal XOS path through a session with the authority to register it — there is no
`EVO_LIVE` and no promotion call in the layer.

Reuse rather than reimplement wherever the concept already exists: `StrategySpec` is the
genome, `sandbox.run_backtest` is the replay engine, `kalshi_fee` and the maker-fill
calibration are unchanged. The three additive changes to `sandbox.py` are default-off.

**Consequences**

*Easier:* the organism keeps running untouched, and the new layer is independently
testable and independently deletable. One replay engine means the proving run exercises
the code the real datasets use, so a clean proving run says something about production.
Reference-only binding needs no Platform Change Review and no reversal of §22.7.

*Harder / more expensive:* two systems now share the word "Evo", and a session has to know
which one it is in. `docs/EVO_POPULATION_FOUNDATION.md` opens with a table of the
differences for exactly that reason, and the session-role files name both. There is also
duplication of *shape* — both have cohorts, fitness and lineage — that a future
consolidation might want to collapse.

*Expensive to reverse:* moderate. The layer is additive (12 new tables, one migration, no
existing table touched), so removing it is a migration and a package deletion. What would
be expensive is the opposite direction: merging the two namespaces later, which is why the
names were kept distinct now rather than overloaded.

*Revisit if:* the population layer proves out on real datasets and the organism's agents
would benefit from proposing into it — at that point one mutation-proposal interface
serving both is worth the consolidation. Or if XOS decides evo evidence should be
first-class, which is a Platform Change Review decision and would supersede the
reference-only half of this entry.

---

### DEC-003 — Evo historical search is a capability the agents invoke, not a second organism

**Date:** 2026-08-25
**Status:** Accepted
**Supersedes:** DEC-002

**Context**

DEC-002 proposed a parallel population layer: `evo_pop_*` tables with their own
`EvoProgram`, `EvoGeneration`, `EvoCandidate`, `EvoGenomeVersion`, `EvoRun` and
`EvoDecision`, its own generations, its own reproduction and retirement, its own Control
Tower and CLI. It was built and put up for review. Review rejected the shape.

The objection was not about the machinery, which works; it was that the machinery had
been wrapped in a **second lifecycle**. The repository already has one: `evo_agents`,
`evo_cohorts`, `evo_genomes`, `evo_fitness`, `evo_births` and `evo_retirements`, with
selection, reproduction and retirement that have been running against live agents. A
second one meant two answers to "who is alive", two definitions of fitness, two places a
strategy could be born, and a standing invitation to conflate a backtest ranking with an
organism's survival.

Three specific consequences made that concrete. Twelve new tables restated concepts the
organism already owned. `evo_pop_fitness` was a fitness table that was not the fitness
table. And the population layer's `insufficient → unranked` rule — correct when
*measuring a strategy*, since a six-trade sample cannot order two strategies — would have
become, inside a lifecycle, an **immunity from selection**: an agent that never trades is
never ranked and therefore never retired. The organism's own evaluator deliberately says
the opposite ("no incubation: a no-trade agent scores near 0").

**Decision**

Keep the deterministic replay, the constrained genome, the gated mutation proposals, the
per-run virtual ledger, the component-wise scoring and the proving run. **Delete the
lifecycle around them.** What remains is a *capability* an existing `EvoAgent` invokes
from its own heartbeat, through a new `search_strategy_space` action:

    agent asks → search replays base + bounded neighbourhood → returns EVIDENCE
              → the AGENT reasons → the agent may revise its own strategy

Concretely:

* `kalshi_bot/evo/search/` replaces `kalshi_bot/evo/population/`. Three tables replace
  twelve: `evo_search_runs`, `evo_search_candidates`, `evo_search_trades`. Each is an
  artifact of one question, attributable to an existing agent, its cohort, its heartbeat
  and its trading-genome revision.
* `EvoProgram`, `EvoGeneration`, `EvoCandidate`, `EvoDecision`, `EvoGenomeVersion`,
  reproduction, retirement, the population Control Tower and the population CLI are gone.
  `evo_agents`/`evo_cohorts`/`evo_genomes`/`evo_fitness` are the only lifecycle.
* Search scoring is structurally separated from agent fitness: it lives on the candidate
  row, is never written to `evo_fitness`, and its module docstring states that
  `insufficient → unranked` is a property of measuring strategies and must never become
  an agent-selection rule.
* Nothing in the package writes a genome. The mutation module is pure — no `session`
  parameter on any public entry point — so there is no writer to bypass and no forgeable
  admission. An agent adopts a variant by putting the returned document through
  `save_strategy` / `activate_strategy`, under its own budgets and audit.
* A search defaults to the agent's own active `evo_strategies` spec. Its `TradingGenome`
  is policy prose whose schema forbids extra keys, so there are no replayable parameters
  inside it to search.

**Consequences**

*Easier:* one lifecycle, one fitness, one place an agent can change. The search is now
something an agent can use whenever it has a question rather than something that runs on
a generation boundary. The three sandbox changes stay default-off and the reference-only
Experiment OS binding from DEC-002 is unchanged and still stands.

*Harder:* the search cannot explore a region no agent is interested in — there is no
autonomous population sweeping the space on its own. That is the intended trade: an
unattended sweep with its own reproduction is exactly what was rejected. Broad exploration
now costs an agent's sandbox budget, which is the same currency every other question
costs.

*Expensive to reverse:* low, and deliberately so. The package is additive, the migration
creates only the three artifact tables, and no existing table was touched.

*Revisit if:* real-dataset searches show agents converging on the same narrow
neighbourhood, which would be evidence that a broader, un-agent-directed exploration is
worth its own design — as a proposal into the existing organism, not as a second one.

---

### DEC-004 — A live canary's arm set and risk envelope live on the Version, so narrowing either is a successor Version

**Date:** 2026-08-28
**Status:** Accepted

**Context**

Putting `mmsell-price-ceiling`'s `mmsell10` arm on real money looked like a stage change:
same hypothesis, same universe, same `lo=5,hi=10,maxyes=7`, and a recorded promotion PASS
already standing. It is not one. `service.arm_live_canary` refuses it twice over — the
version carries no pre-registered `risk_json`, and its declared arm set is
`{mmsell9, mmsell10}` while the canary is one arm. Both facts live on a **frozen** Version,
and the flush guard refuses every edit to one.

The tempting readings were all wrong in the same direction. Adding `risk_json` to v1 is an
edit to a frozen contract. Arming both arms puts the arm with negative observed paper
economics on real money to satisfy a structural check. Relaxing the arm-set equality in
`arm_live_canary` weakens the rule that a deployment matches its pre-registration, on a
real-money path, to make one task easier.

**Decision**

Treat the arm set and the risk envelope as what Experiment OS already says they are:
parts of the scientific contract, carried on the Version. Narrowing either is a **successor
Version**, registered through the ordinary path with a `change_reason`, and its epoch
restarts evidence. `kalshi_bot/experiment_os/canary_mmsell10.py` is the worked instance;
the two refusals are reproduced as tests against the real service rather than described.

The same registration performs the tag hand-over that a successor implies. A strategy tag
resolving to two ACTIVE deployment arms is refused as ambiguous, so the predecessor's
deployment is ended and replaced — in one call — by one carrying the arms that stay behind.
Ending a deployment does not orphan its evidence: metric scopes resolve tags over every
deployment in the epoch, ended or not, and only the enforcement resolver reads `ended_at`.

**Consequences**

*Easier:* a canary's envelope is pre-registered, immutable and auditable rather than a
Railway variable someone remembers setting; a single-arm live test cannot silently drag a
second arm onto real money; and the "why can't we just arm it" question has a mechanical
answer that fails loudly if it ever stops being true.

*Harder:* the successor's promotion gate restarts at n=0, because evidence windows floor at
the epoch start. The recorded PASS on the predecessor cannot be inherited, and the canary
waits for a fresh sample. There is no shape that keeps both the old evidence and a narrowed
arm set — that is the trade, not an implementation gap.

*Expensive to reverse:* moderate. A frozen version, a registered gate and a recorded
transition are append-only by design; unwinding a successor means retiring it, not deleting
it. The code is additive and default-inert, so not *registering* one costs nothing.

*Revisit if:* narrowing an arm set becomes routine rather than exceptional. A recurring need
would be evidence that arms are being over-declared at freeze time — the fix would be
declaring fewer arms per Version, not making the arm set mutable.

---

<!-- Copy the block above for each new decision. IDs are stable: never reused, never
     renumbered. -->
