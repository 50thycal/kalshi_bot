# Decisions — kalshi_bot

Why does the system work this way? Lightweight ADRs, appended in order. Consequential
decisions only. An accepted entry is never rewritten because architecture later changed —
supersede it with a new one and update only the old status line.

**Build OS v0.12**

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

**Addendum (2026-08-28, operator decisions applied).** The successor was accepted, and its
promotion gate registered with **no evidence floor** — v1's literal contract, which records
no explicit `n`. Two consequences worth carrying forward, because both are general:

* A canary's registered tags must equal what the RUNTIME derives, not what reads well. The
  twin tag is built as `<live_tag><LIVE_PAPER_TWIN_SUFFIX>`, so registering a tag the runtime
  would never produce yields a twin whose rows resolve to no deployment arm and are refused
  at the write path — a canary armed with a silent twin. Derivation is now pinned by test.
* Prefer a per-book knob over a process-wide setting when both can express a cap. The
  config-drift detector watches the book spec (`book_params`) and does not watch process-wide
  settings, so the same limit is *auditable* in one place and invisible in the other.

---

### DEC-005 — Experiment lifecycle gets its own boot transport, and its envelopes name reviewed packages rather than authoring contracts

**Date:** 2026-08-28
**Status:** Accepted

**Context**

Registering a successor Version and arming a live canary are writes. The ops channel is
read-only against Postgres by design — a SELECT-only role, enforced server-side — and the
sandbox cannot reach Railway, so `DEC-004`'s package could only ever be run by an operator on
their own writable connection. That is a real gap rather than a policy: the same shape was
already solved twice, by `EXPERIMENT_OS_ISSUE_COMMAND` and `EXPERIMENT_OS_PLATFORM_COMMAND`,
both of which keep the worker as the only writer and carry a request rather than a connection.

The tempting design is a *generic* transport: an envelope that says "create a version with
these arms and this gate spec". It is wrong, and not marginally. A gate is supposed to have
committed to its thresholds **before** the evidence arrived; an envelope that can author one
lets the contract be written the same afternoon the results came in, in an environment
variable, unreviewed, on a public branch. Pre-registration would survive in name only.

**Decision**

A third transport, `EXPERIMENT_OS_EXPERIMENT_COMMAND`, with a disjoint vocabulary and its own
receipt ledger — three tables, so a ticket cannot arm a canary and a platform revision cannot
freeze a Version, structurally rather than by convention.

Its envelopes **name a reviewed package and cannot author one.** A package is code in the
repository — arms, risk envelope, gate specs and tags as literals someone read in a pull
request. The envelope's whole content is *which* package to run, who is acting, and (for
`ARM_CANARY`) who approved real money. Adding a package is a code change; running one is the
transport. The one payload knob is a promotion-gate evidence floor, and it moves in one
direction only: adding a floor makes a gate stricter and can never make it pass on less.

Two further separations hold: `ARM_CANARY` requires the `LIVE_OPS` role and a named
`approved_by`, and it still places no order, because `LIVE_STRATEGIES` is a switch this
transport cannot reach. A transport that could both arm a canary and open the allowlist would
be one environment variable away from unreviewed exposure.

**Consequences**

*Easier:* the lifecycle work `DEC-004` describes can be driven the same way the enforcement
cutover and the platform revisions already were, with a durable receipt naming the actor, the
role and the approver — instead of a private connection and a shell history.

*Harder:* every new package is a PR. That is the cost being bought deliberately: the review
step is what keeps a scientific contract out of an environment variable.

*Expensive to reverse:* low. The transport is additive and inert while its variable is empty;
the migration creates one table with no foreign keys.

*Revisit if:* packages start being written to satisfy the transport rather than the science —
a package that exists only to make an envelope possible is a sign the vocabulary is too narrow
for a real need, and the answer would be a new action, reviewed, not a generic escape hatch.

---

### DEC-006 — Book definitions are settable through the ops channel, and an activation request is composed rather than typed

**Date:** 2026-08-28
**Status:** Accepted

**Context**

`DEC-005`'s transport shipped and the `mmsell10-canary` package registered cleanly. Auditing
the *next* step against production turned up a defect of the same class as the one that broke
`EXPERIMENT_OS_EXPERIMENT_COMMAND`: seven of the variables the approved activation sets were
not in `railway_env.ALLOWED_VARS`, so the sanctioned channel would have refused the procedure
partway through — after the settable half had already been applied and a redeploy triggered.

The blocking one is `MMSELL_VARIANTS`. A live mmsell book is an ordinary entry in that string
(`Lmmsell8` and `Lmmsell10` both are), so `LIVE_STRATEGIES=Cmmsell10` without it names a book
that does not exist: no orders, and `book_params[Cmmsell10]` absent against the deployment's
declared value, recorded as `EXPERIMENT_CONFIG_DRIFT` and taking the keep gate to
BLOCKED_INTEGRITY. The other six are mmsell's concentration safeguards and the quote
pre-filter, which production leaves unset — so they hold whatever `config.py` currently
defaults to, and today those defaults happen to equal what the envelope declares.

**Decision**

All seven go on the allowlist, and the activation request is **derived, never written down.**

On the widening: `LIVE_STRATEGIES`, `MAX_TOTAL_EXPOSURE`, `MAX_DAILY_LOSS`, `MAX_ORDER_SIZE`,
`LIVE_ENABLED` and `KILL_SWITCH` have always been settable here. The channel is already
trusted with the switches that decide whether real money moves; `MMSELL_VARIANTS` defines the
book one of those switches then names, which is the same authority rather than a new one. It
is also the *safer* direction for a registered book: enforcement recomputes `book_params` for
every registered live tag at boot, so an edit through this door is detected as drift, while a
`config.py` default silently moving underneath an unset variable is not detected at all.
Pinning the six explicitly is what makes a pre-registered envelope true of the running process
rather than merely equal to today's defaults.

On composing: `MMSELL_VARIANTS` is one ~800-character string holding every book, and retyping
it to add one entry is how a running book gets dropped — silently, because a missing book
simply stops appearing. `canary_mmsell10.variants_with_live_book` appends to the running
value, is idempotent, and **refuses** a conflicting definition of the live tag rather than
overwriting it. `scripts/mmsell10_canary.py activate` prints the exact request and applies
nothing: no database connection, no Railway credentials, `--execute` ignored.

**Consequences**

*Easier:* the arming procedure is executable end to end through the sanctioned channel, and
the operator pastes a value derived from what the service is actually running.

*Harder:* a package must now declare `activation_vars`, and CI asserts every name clears the
allowlist. A package whose activation the channel would refuse fails in CI instead of in front
of an operator with a write already submitted.

*Expensive to reverse:* low. Seven allowlist entries and one composer; nothing depends on them
until an activation is attempted.

*Revisit if:* the allowlist starts being widened to make a *specific* activation convenient
rather than to let a pre-registered envelope assert itself. The test to apply is the one used
here — does the channel already hold this authority through another variable? If not, the
answer is a narrower envelope, not a wider list.

---

### DEC-007 — An epoch boundary carries its books forward or ends them, never both

**Date:** 2026-08-28
**Status:** Accepted

**Context**

Every mmsell paper book recorded nothing for four days, and nothing in the system said so.
An I2 platform boundary closed `mmsell-type-tight` v1/e1 and opened v1/e2; the cut left
`tmmsell-paper-legacy-1` open on the CLOSED epoch and registered nothing on the new one.
The admission resolver requires the deployment AND its epoch to be open, so four tags
stopped resolving while their deployment row still claimed the books were running.

Two views of the same fact disagreed and nothing reconciled them. That is what made it
silent: an unregistered tag produces no integrity event, no config-drift record and no gate
verdict, because as far as the system is concerned it was simply never registered. The
Control Tower saw only `experiment.zero_evidence`, rated LOW/P2 — a symptom four days
downstream of the cause.

**Decision**

An epoch boundary is a fork with exactly two branches, and both are now taken explicitly.

`close_epoch` ends every deployment still running in the epoch. An epoch is the operating
interval; a deployment that outlives it is not "still running" in any sense the resolver
honours. The cascade makes the record say what the resolver already believed, and it hides
no evidence — metric scopes resolve tags across every deployment in an epoch, ended or not.

`apply_new_epoch` and `arm_live_canary` capture the open deployments BEFORE the close and
re-register them on the successor at the boundary instant, same arms, same tags, derived
key. An I2 cut means *same contract, fresh evidence*; it has never meant "stop trading".
Evidence still does not pool, because the epoch is what metric scopes window on.

A carry-forward refuses `live` and `paper_twin` deployments **by name**. It can prove none
of what `arm_live_canary` proves — fresh tags, a twin at the same instant, a re-evaluated
promotion gate — so a platform boundary stops and asks rather than minting live lineage.

Separately, a lineage refusal is scoped to the book that earned it. `LineageBlocked`
escaping `MmSellTracker.run_once` let the caller's single `session_scope` roll back every
other book's entries; the tracker now uses the pre-check that already existed
(`enforcement.tag_admissible`) once per cycle. The block itself is unchanged — the tag is
still refused, still counted, still logged. Only the blast radius changed.

**Consequences**

*Easier:* a platform boundary no longer takes an experiment off the board as a side effect,
and one misconfigured book cannot cost a whole family its cycle.

*Harder:* an I2 cut on a LIVE experiment now raises instead of proceeding. That is the
point — it was previously "succeeding" while breaking the live book's lineage silently.

*Expensive to reverse:* moderate. These are shared engine semantics; the deployment rows a
carry-forward creates are real lineage that later evidence binds to.

*Open:* whether this warrants a Platform Revision. `EXPERIMENT_ENGINE` is a registered
component. The argument against: no measured quantity changes, no recorded evidence is
reinterpreted, no gate reads differently — what changes is which books may keep trading.
Registering a revision is Platform Change Review's write, so it is raised, not performed.

*Revisit if:* a carry-forward is ever wanted for a live deployment. The answer is not to
relax the refusal but to stand the book down and re-arm it through the canary path, which
is the only place the structural proofs live.

---

<!-- Copy the block above for each new decision. IDs are stable: never reused, never
     renumbered. -->

---

### DEC-008 — Three perp mechanisms race as arms of one experiment, not as three experiments

**Date:** 2026-08-29
**Status:** Accepted

**Context**

Kalshi's crypto perpetual futures open a research surface unlike anything this repository
has traded: the instrument is tethered to a published reference index by an explicit
8-hourly funding payment, so the tradeable question becomes *where is risk priced
differently across two instruments on the same underlying* rather than *what is the true
probability*. Three candidate mechanisms presented themselves at once — premium reversion,
cross-sectional funding carry, and perp microstructure leading the crypto event-contract
ladder — and the operator asked for them to be raced against each other.

The obvious shape is three experiments, one per mechanism, each with its own arms. It is
also the shape that quietly makes the race meaningless.

**Decision**

One experiment, `perp-v1`, with three treatment arms and a matched random-direction control
(`perpctl`), registered together and frozen together on version 1. Each treatment arm gets
its **own** PROBE→PAPER gate rather than the version carrying one gate over `arm: "*"`.

Three separate experiments would each have chosen their own universe, cost model, sample
unit, measurement instrument and headline metric. Comparing their outputs afterwards would
then rest on an *assumption* of comparability rather than on a shared frozen contract — the
same class of mistake that makes `mmsell-anchor-vol-entry`'s cross-experiment delta
`BLOCKED_PLATFORM` today when the two epochs pin different snapshots. Registering the three
under one contract makes the shared quantities load-bearing and pre-registered instead of
coincidental.

Per-arm gates are the other half. A single `arm: "*"` promotion gate makes all three arms
promote together or none of them, which is not a horse race; it is a portfolio bet on the
weakest arm. The rule that goes with per-arm gates — the paper deployment carries only the
arms whose own gate PASSed — is written into the thesis because Experiment OS cannot
enforce it.

**Consequences**

*Easier:* the comparison the operator asked for is a property of the contract rather than
an argument made after the fact. One collector, one cost model and one metric serve all
three arms, so a defect in the measurement layer is one defect and not three.

*Harder:* **arms freeze together.** Changing one arm's entry rule is a new Version for all
three, and evidence restarts for all three. This is the real cost and it was accepted
deliberately rather than discovered later.

*Expensive to reverse:* moderate. Splitting the experiment later means new experiments with
their own versions; the probe evidence gathered under `perp-v1` would be `context_only` to
them, not poolable.

*Open:* whether a perp book could ever be live without a Platform Revision. Leverage,
liquidation and a recurring funding cash flow are semantics no `FEE_MODEL`, `FILL_MODEL` or
risk component in this repository describes. That is Platform Change Review's question, and
it is raised here rather than answered — `perp-v1` stays at PROBE, registers no strategy tag
and no deployment, and its package has no `arm` function at all.

*Revisit if:* one arm clears its bar and the other two are still accumulating. The answer is
not to unfreeze the version but to register the paper deployment for the arms that passed
and let the rest keep gathering evidence under the same contract.

---

### DEC-009 — An ops request declares its intent, and a production change is verified rather than assumed

**Date:** 2026-08-30
**Status:** Accepted

**Context**

The ops channel is the only path by which a session reads production and, within a tight
allowlist, changes the running configuration of a live trading worker. Four properties of
the pre-existing contract were failures waiting to be found rather than defects yet to be
fixed.

A **failed request left the run green**: the runner's exit status was discarded by
`| tee … || true` so the error could still be published, and the Actions run then reported
success. Anything reading run status — a person scanning the list, a future automation —
learned nothing.

**Authority was invisible in the request.** `{"type":"env"}` reads configuration;
`{"type":"env","set":{…}}` changes a live trading worker and redeploys it. One JSON key
apart, identical in the result, and the result said neither.

**A mutation reported an intention, not an outcome.** `set + redeploy requested` is a
statement about what this process asked Railway for. It cannot distinguish a landed change
from an upsert that lost a race, or from a redeploy Railway declined.

**The channel's real surface existed only in prose.** That is XOS-000005 exactly: two
commands the runbook advertised and the runner refused, for weeks, indistinguishable from
commands that never existed.

**Decision**

Four rules, each held by code and by a test rather than by convention.

1. **A request's classification is the first thing in its result.** `ops_meta.classify`
   decides READ or MUTATING, and a MUTATING result opens with a banner naming the variables
   before any output. New callers say `{"action":"set","values":{…}}`; the legacy `set`
   spelling keeps working; an AMBIGUOUS request — `action:"get"` carrying values,
   `action:"set"` carrying none — is refused. The only safe reading of "I cannot tell
   whether you meant to change production" is to stop.
2. **A mutation ends in a verdict about the system, not about the request.** Record the
   before state, apply, report the redeploy outcome, read the state back:
   `VERIFIED` / `APPLIED_BUT_UNVERIFIED` / `FAILED`. Where the change touched Experiment OS
   or live-strategy state, the canonical `enforcement` and `readiness` reads run afterwards
   and are printed with it — the runner asks the canonical readers and shows what they said;
   it never forms a health opinion of its own.
3. **A failed request turns the run red, after its output is published.** Both requirements
   at once: the status is captured, the result and receipt are published, and the workflow
   re-raises the failure as its last step. Publication failure is a separate loud failure.
4. **The channel describes itself from its own allowlists.** `capabilities` is generated
   from `ops_runner`'s and `railway_env`'s data structures; `doctor` and `incident` compose
   canonical readers; the docs-parity tests assert against the same generator. A capability
   cannot be documented into existence, and a request type cannot be added invisibly.

Receipts make all of this legible after the fact: every run writes
`ops/results/<id>.receipt.json` — type, classification, provenance, timestamps, serving code
SHA, target service, exit status, and for a mutation the before/after and the verdict.
Receipts for changes to real-money capability, the risk envelope around it, or the three
Experiment OS write transports are additionally appended to the long-lived `ops-audit`
branch, because `ops/results` is 80-file scratch and a live arm can fall off the end of it
in an afternoon.

**Consequences**

Optional provenance (`actor`, `purpose`, `workstream`, `issue`) is carried into the header
and the receipt. It is a **label and never a credential**: the allowlists decide what is
permitted, not who claims to be asking. Nothing here widens authority — no arbitrary shell,
no arbitrary Railway access, no writable database credential, no settable secret, no
Experiment OS write path against Postgres. `doctor` and `incident` are reads that compose
existing readers, and the fact that the runner can now say more about a mutation does not
mean it may perform more of them.

The executing workflow file lives on the `ops` transport branch, so the exit-status fix, the
receipt publication and the audit archive reach production by a fast-forward commit onto
`ops` in an idle window — not by merging this decision. The runner's own code continues to
come from the default branch on every run.

*Revisit if:* a fifth request family needs authority that is neither "read production" nor
"set an allowlisted variable". That is not a new request type; it is a question about
whether this channel should hold that authority at all.

---

### DEC-010 — The evo fleet's research reaches Experiment OS by a manual Research Lab act, not an automatic proposal

**Date:** 2026-09-03
**Status:** Accepted

**Context**

Spec §22.7 says evo trades stay in `evo_*` tables under evo lineage, and that future evo
proposals should be mapped into Experiment OS "immediately after evo integration". The
first half is built and correct: `importer.py:371` holds it, and NEW_ONLY does not refuse
evo trades because they never touch the `paper_trades` write path. The second half has
never been built. A read-only check-in on 2026-09-02 established the gap concretely — a
`grep` for any Experiment OS symbol across `kalshi_bot/evo/` returns zero hits, the
coupling runs one way only, and no experiment has ever carried `origin='evo'` even though
the column has accepted the value since `service.py:615`.

Meanwhile the fleet has accumulated a substantial parallel research ledger in
`evo_experiments` — hypotheses, falsifiable predictions, conclusions — none of it visible
to the Control Tower and none of it gated. The consequence is a structural ceiling: an
agent can conclude any number of experiments and none can become a registered Experiment
with a pre-registered gate, so no evo finding can be promoted and the fleet cannot
contribute to the $100/month north star however good it gets.

The design question was where the bridge should sit. An automatic path would propose each
concluded `evo_experiment` into Experiment OS at `IDEA` stage for a human to register.
That ends the dead end and preserves the invariant that only a recorded evaluator PASS
authorizes a transition, since a proposal authorizes nothing. It also produces volume: 448
concluded experiments already exist, most of them not worth an operator's attention, and a
queue nobody reads is indistinguishable from no bridge at all.

**Decision**

The bridge is a **manual Research Lab act**. The operator reads the fleet's strategies,
judges which are worth pursuing further, and Research Lab registers the survivor by hand
as an ordinary Experiment OS experiment. No automatic proposal, not even into `IDEA`.

The shape of the mismatch is why this is the right cut rather than merely the cautious one.
An `evo_experiment` carries `promotion_criteria` and `kill_criteria` as free text. A
registered Experiment needs a gate spec, an arm, a control and an epoch. Nothing
mechanical turns the first into the second — the translation *is* the scientific judgment,
and a pipeline that appeared to perform it would be manufacturing a contract out of prose.
Pre-registration would survive in form and not in substance.

**Consequences**

`origin='evo'` stays a valid value and is what a hand-registered experiment gets stamped
with, so the lineage back to the originating agent stays legible in Experiment OS.

The fleet's value is now explicitly *hypothesis generation for a human reader*, not
autonomous contribution to the portfolio. That makes the readability of what agents produce
the thing that matters, and makes an unreadable strategy a real defect rather than a
cosmetic one.

Nothing here obliges the operator to review on a cadence. The ledger accumulates; it is
read when the operator chooses to read it.

Fleet size is held at 6 (`EVO_MAX_ACTIVE_AGENTS=6`) while the system is still under test.
That is an operator ruling of the same date and is recorded in
[WS-014](workstreams/WS-014-evo-fleet-health-and-xos-bridge.md), not here — it is a
parameter, not an architecture.

*Revisit if:* the operator finds themselves reading the evo ledger regularly enough that
the absence of a triage surface is the bottleneck. That is an argument for a *reading* tool
— a ranked, filtered view over `evo_experiments` — before it is an argument for an
automatic write path into Experiment OS.


---

### DEC-011 — Migrate to Build OS v0.12: `solo` mode, a stated owner-layer split, an active-work limit of 4, and `SHIP` as a development-gate report

**Date:** 2026-09-06
**Status:** Accepted
**Extends:** `DEC-001` (unchanged — see *What is deliberately unchanged*)

**Context**

`DEC-001` adopted Build OS v0.4 and pinned it deliberately, so an in-flight design could not
change shape because the framework moved underneath it. That pin held for eight releases.
Canonical reached v0.12 on 2026-09-06, and the compatibility check on 2026-09-02 had already
recorded that three of the intervening releases would change how sessions here behave.

The pin had also started to cost something concrete. Build OS defaults to `reviewed` when no
operating mode is declared, and `reviewed` requires an independent verdict naming a full SHA
from an actor who is not the implementer. This repository is one account, one GitHub identity,
one agent: no such actor exists. `WS-001` sat in `REVIEW` from 2026-08-24 to 2026-09-06 waiting
for a verdict that could not be given, while every artifact it was waiting to deliver was
already merged on the default branch (PR #258, merged 2026-08-24T14:26:24Z). A gate that cannot
be satisfied is not strict — it is inert, and it trains everyone to merge past it.

Alongside that, the board had accumulated five rows that Build OS has said since v0.2 should
have left it: one complete, three closed, and `WS-001` itself. Thirteen rows is the exact shape
v0.12's finite-work rules were written against.

**Decision**

Adopt **v0.12**, with four decisions the migration turned on. Each was put to the operator
explicitly rather than settled by an agent, because each is a genuine judgement about how this
project works rather than a mechanical upgrade.

**1. Operating mode is `solo`.** Significant work is accepted by the owner at merge, recorded
as `Owner-accepted` with a separate `Accepted head` field naming a full 40-character SHA, and
**never described as reviewed**. Every `SHIP` result must state plainly that no independent
party examined the change — that sentence is the whole point of the mode.

Three limits come with it and are easy to get wrong. `Owner-accepted` is **reserved to the
owner**; an agent may transcribe an acceptance the owner actually gave, naming the channel it
came through, but **inferring one from a merge is issuing a verdict, not relaying one**, and an
agent may not do that. A finalization commit never writes a verdict it does not yet have. And
no history is upgraded: acceptances are not retrofitted onto merged work, so PR #258 is
**not** retro-accepted by this decision — its merge stands as the historical record it is.

`solo` is a fallback, not a preference. The moment a second actor exists — a colleague, a
second GitHub identity, a review agent under a separate account — the project moves to
`reviewed`.

**2. The owner layer and the session identity header coexist, with the split written down.**
v0.6 gives each piece of work one terminal owner-facing result, and Build OS treats two
owner-facing surfaces on one change as an anti-pattern because they drift within a week. This
repository already has an owner-facing surface: the identity header
(`SESSION: … / MODE: … / ENFORCEMENT: … / AS OF: …`) that standing roles open their first
substantive report with, so an operator holding many windows knows what each one owns.

They do genuinely different jobs — one is *who is speaking*, the other is *where the work
landed* — so both stay, with the boundary stated rather than discovered: the header opens a
**session** and lives in chat; the Owner Result closes a **piece of work** and lives on the PR.
They never appear in the same block, the header never carries a result, and a result never
carries a session state. The session-role system is therefore unchanged by adopting the owner
layer, which is why the third option considered — subsuming the header into the result format —
was rejected: it would have disrupted the role system to solve a problem Build OS does not
have.

**3. The active-work limit is 4, counting `Active` rows only.** v0.12 defaults to three
owner-attention workstreams and explicitly permits a project to declare a different limit with
a reason. The reason here is that `Blocked` in this repository means something specific — Build
OS requires a *named* unblocker, not "waiting" — and a blocked research thread is waiting on
external evidence, not on the operator's attention. `Paused` likewise. Counting only `Active`
rows, the board holds exactly four after the cleanup (`WS-004`, `WS-006`, `WS-007`, `WS-009`),
so it sits **at** the declared limit rather than pretending to be under an unenforced one. A
fifth `Active` row requires completing, pausing or blocking one of the four, and that
transaction is the point: the cost of starting is paid visibly, by the owner, at the moment
they start.

**4. `SHIP` reports the development gate only.** v0.7 narrowed `SHIP` to mean every agent step
is finished and only the owner's merge remains. In this repository a merge is frequently not
the end — an XOS registration, an arm, a promotion or an impact acceptance often follows — and
`DEC-001` is emphatic that a workstream authorizes nothing. A naive `SHIP` here would therefore
be either premature or would imply Build OS authorized an Experiment OS action, which it
cannot. The rule that resolves it: **`SHIP`'s `Next action` is the merge; an Experiment OS
action that follows is named as a guard for the operator, never as a Build OS next step and
never as something the PR authorized.** This formalizes what the repository already does —
`WS-002`'s "Merge guard: verify in XOS that the revision is registered + impacts accepted" is
exactly this shape.

**One further project-specific addition,** not an owner call but a floor that has to exist
alongside the others: v0.6's proportionality lets *simple* work skip the workstream, the Build
Card and independent review. **No change touching real-money exposure, the arming path, a live
safeguard, a gate or the ops channel is ever classified simple**, regardless of diff size.
Build OS already says classification only ratchets up and that unclear work is significant;
this names the repository's tripwires explicitly rather than relying on judgment at the moment
judgment is worst.

**What is deliberately unchanged**

- **`DEC-001`'s authority boundary.** Experiment OS stays canonical for experiments, Versions,
  epochs, deployments, arms, gates, platform revisions, impact actions, enforcement and XOS
  issues; Build OS stays canonical for the development workflow; a workstream links and never
  copies. Nothing in v0.5 through v0.12 touches it, and it was not renegotiated here.
- **No runtime, schema, environment or live-configuration change.** This migration is
  documentation. `WS-007` is live with real money inside its Stage-1 envelope, and the arming
  path, the ops channel and every safeguard were untouched.
- **Completed workstreams and thesis documents are not rewritten** to look as though they ran
  under v0.12. A v0.4 pin covers work done under it; later versions do not reach back. Existing
  workstream files gain no retroactive `## Acceptance Checks` section — v0.12 is explicit that
  they need none, and new workstreams write one at the start.
- **The vendored templates are copies, not a fork.** They were re-copied from canonical v0.12
  verbatim. If the protocol is wrong, it is fixed in `50thycal/build-os`.

**What was deliberately not taken**

- **`OWNER_PLAN.template.md` and the owner-approval flow.** Intent in this project arrives
  through the session-role router, which already establishes what a session may write before it
  writes anything. Adding a second approval surface would duplicate that, and the owner did not
  ask for it. Revisit if design work starts arriving without a role.
- **CI or tooling over framework artifacts.** Build OS ships none deliberately, and the
  authority boundary is enforced by discipline. `DEC-001` already records that a board going a
  month without an update should be deleted rather than left to mislead; that remains the
  revisit condition, not a linter.
- **A retroactive `Owner-accepted` on PR #258**, per the `solo` limits above.

**Consequences**

*Better:* the review gate now describes something that can actually happen, so a row in
`REVIEW` means work is genuinely outstanding rather than that nobody could ever satisfy it.
The board carries eight rows instead of thirteen, and the limit gives the ninth a visible
price. A finding outside a mission has four dispositions and none of them is "open a
workstream", which is the mechanism that let the board grow monotonically from the agent's
side.

*Worse, honestly:* `solo` removes an independent check rather than passing it. Every `SHIP`
from here carries a sentence saying so, and that sentence is load-bearing — under-reporting a
deviation in `Spec Deviations` now has nothing downstream to catch it.

*Unchanged:* everything Experiment OS owns.

*Revisit if:* a second actor becomes available, in which case the project moves to `reviewed`
and past acceptances are **not** converted into approvals; or if the four-row limit turns out
to be describing the board rather than constraining it, which would be an argument for the
canonical three and a decision about what pauses.

### DEC-012 — Standing authorizations: the request is the approval inside its scope; hard stops stay explicit; long sessions close with a brief

**Date:** 2026-09-12
**Status:** Accepted
**Extends:** `DEC-011` (`solo` acceptance at merge), `DEC-009` (verified ops changes). `DEC-001` unchanged.

**Context**

The operating system was correct and slow. A new paper tape — a package module, a PR, a
merge, a `REGISTER_PACKAGE` envelope, an `MMSELL_VARIANTS` append, a verification read —
took five or six operator replies even when every criterion was met, because each step was
written as a separate confirmation and no surface said which steps a request had already
authorized. The same shape recurred in every role: the confirmations were real safeguards
in one place (arming, `LIVE_STRATEGIES`) and pure ceremony everywhere else (a paper book
that cannot place an order), and the playbooks did not distinguish them.

Separately, every long session ended with the operator asking the same five questions —
what happened, is the goal done, what blocks, what must I decide, what is next — because
the default closing message was a technical narrative that answered none of them first.

**Decision**

1. **Three tiers, written down** (`docs/STANDING_AUTHORIZATIONS.md`). *Free* actions are
   never asked about. *Request-authorized* actions take their approval from the operator's
   request itself, provided the criteria hold: a paper-scope chain — package PR, merge,
   `REGISTER_PACKAGE`, paper env append — runs end to end off one message and reports once.
   *Hard stops* — `ARM_CANARY`, `LIVE_STRATEGIES`/`LIVE_ENABLED`/`BOT_MODE`/`KILL_SWITCH`,
   anything expanding real-money exposure or weakening a safeguard, a Platform Revision
   activation, the `ops` workflow file — are answered by the operator in that session, in
   words that name the action, and are never inferred from a request that implies them.
2. **The criteria are computed, not asked.** `xos package-preflight <package>` is a
   read-only CLI command, allowlisted on the ops channel, that checks the mechanical
   criteria (package known and registers, experiment not RETIRED, no declared tag carried by
   another experiment, complete active snapshot, no command mid-execution) and prints the
   exact envelope on `GO`. Packages declare `strategy_tags` from their own constants for the
   collision check. A `NO-GO`, a duplicate or a revival is a `DECISION`, never a workaround.
3. **The merge of a paper-scope PR transcribes the request as the owner's acceptance.** In
   `solo` mode an agent still never issues `Owner-accepted` on its own authority; here it
   transcribes one the owner gave, naming the channel (the request in a named session), and
   still states that no independent party reviewed the change. This never extends to the
   hard-stop tier, where the merge stays the owner's.
4. **Role inference.** An unambiguous request selects its role; the session states it in
   the identity header and proceeds. The menu is the fallback for ambiguity, and the role is
   sticky either way.
5. **The closing brief is the default.** WHAT HAPPENED / GOAL / BLOCKERS / DECISIONS / NEXT
   STEPS, plain words, under 120 words, links after. `/session-brief` renders it on demand.
   The Owner Result on the PR and the identity header are unchanged and never merge into it.

**Consequences**

*Better:* a paper tape is one request and one report. Ceremony no longer dilutes the
confirmations that matter, so a hard stop reads as one. "Meets the criteria" is a program
output with a receipt-ready envelope attached, which also removes the retyping that produced
the Gmmsell2-era failure modes.

*Worse, honestly:* the agent now merges its own paper-scope PRs and sends registration
envelopes without a pause. The mitigations are structural — the transport arms nothing, a
paper book cannot place a real order, `STAND_DOWN` reverses a registration, and the hard-stop
list is short and literal — but a mis-scoped request will be executed faithfully rather than
questioned. The preflight is mechanical; the duplicate and revival criteria are still
judgment, stated in one line each with the Control Tower read they came from.

*Unchanged:* every real-money invariant in `CLAUDE.md`; `arm_live_canary`'s refusals; the
ops channel's read-only boundary; `DEC-001`.

*Revisit if:* a request-authorized chain ever touches a hard-stop item without stopping
(that is a defect in the tier list, fixed by widening it), or the brief starts hiding
material deviations (the 120-word cap is a ceiling on narrative, not on truth).
