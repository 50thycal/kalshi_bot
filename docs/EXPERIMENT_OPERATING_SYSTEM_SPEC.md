# Experiment Operating System — Architecture and Migration Specification

Status: **DESIGN CONTRACT — implementation pending**  
Owner: repository-wide research / trading architecture  
Applies to: operator strategies, idea-model promotions, probes, paper books, live canaries, production strategies, and evolutionary-agent proposals  
Primary objective: make every trading idea follow one machine-readable, auditable, falsifiable lifecycle from idea to retirement, while preserving the repo's existing research history and preventing future platform changes from silently contaminating experiments.

---

## 1. Why this specification exists

The repository already has strong experimental practices:

- ideas are screened before expensive implementation;
- promoted ideas are converted into pre-registered probes;
- paper books carry thesis docs and pre-registered gates;
- live books are mirrored by paper twins;
- experiment boundaries are increasingly enforced after fee, taxonomy, pairing, and execution discoveries;
- the strategy loop reconciles trading books against `docs/BOOK_REGISTRY.md`;
- the evolutionary fleet can read operator docs and live book performance;
- failed ideas are retained in scorecards, journals, and the graveyard rather than erased.

The weakness is architectural rather than methodological: these practices live in separate skills, scripts, Markdown files, configuration conventions, and book-specific analysis code. There is no single machine-readable object that says:

> This hypothesis is experiment X, version Y, currently in PAPER, running arms A/B against control C, under platform snapshot P, within epoch E, with gate G, and it may not advance until those exact requirements pass.

As a result, the repo repeatedly learns important experimental invariants only after a production or measurement failure exposes them. Examples include cohort boundaries that originally existed only in prose, live books inheriting mature paper state, fee-model corrections, taxonomy changes, parser-shape changes, and treatment/control differences that were not the intended independent variable.

This specification defines the missing layer: a repository-wide **Experiment Operating System (Experiment OS)**.

The strategy code remains important, but the experiment lifecycle becomes the organizing abstraction.

---

## 2. North-star design rule

Every strategy, experiment, and autonomous research output must be representable as:

```text
Idea / hypothesis
      ↓
Probe
      ↓
Paper validation
      ↓
Live canary
      ↓
Production
      ↓
Pause / retirement / successor experiment
```

Every arrow is a gate.

No stage transition is implied by a code merge, a config change, a good-looking P&L number, or an agent recommendation. A transition is an explicit, audited state-machine event evaluated against the experiment's pre-registered contract.

A clean failure is useful research. The system must make it easier to kill a bad idea than to rationalize one forward.

---

## 3. Scope and non-goals

### In scope

This system owns the lifecycle and evidence contract for:

- human/operator research ideas;
- `kalshi-idea-model` promotions;
- `kalshi-probe-builder` probes;
- paper strategies and experiment arms;
- live-paper twins;
- real-money live canaries;
- production strategies;
- evolutionary-agent strategy proposals, experiments, and paper validation;
- experiment controls and benchmark arms;
- pre-registered promotion and kill gates;
- experiment versions and operating epochs;
- platform dependencies that affect experiment interpretation;
- legacy experiment migration;
- experiment status, metrics, integrity, and audit history;
- generation of human- and bot-readable registry views.

### Not in scope for the foundation release

The first implementation must **not**:

- automatically move real money;
- automatically promote a strategy to live or production;
- replace existing strategy implementations;
- rewrite all historical research into a new format before the system can launch;
- force one statistical test onto every strategy family;
- eliminate strategy-specific deep-dive analysis;
- allow evo agents to bypass operator approval for real-money transitions.

The first goal is consistent identity, state, dependencies, gates, lineage, and measurement. Automation comes after the evidence layer is trustworthy.

---

## 4. Two explicit cutovers

A clean break between the legacy repository and Experiment OS is required. There are therefore **two cutover milestones**, not one ambiguous transition.

### 4.1 Specification cutover

**Specification cutover occurs when this document is merged to the repository's default branch.**

From that point forward:

1. New experimental work must be designed according to this specification.
2. No new bespoke lifecycle convention should be invented if Experiment OS already defines the concept.
3. New theses, probes, or strategy PRs created before code enforcement lands must still carry the fields required by the universal experiment contract in this document.
4. Existing running books are not stopped merely because this specification merged.
5. Existing historical artifacts remain authoritative evidence of what happened before the new system.

This cutover is a **design-policy boundary**, not yet a runtime enforcement boundary.

### 4.2 Enforcement cutover

**Enforcement cutover occurs when the Experiment OS foundation, migration tooling, and new-work enforcement are deployed.** The implementation must record this timestamp/version explicitly rather than relying on an inferred merge time.

After enforcement cutover:

- every newly-created experiment must have a canonical `Experiment` record;
- every new experiment arm must have an `ExperimentArm` record;
- every paper/live order created under the new system must resolve to an experiment, arm, epoch, and deployment;
- no new paper or live strategy may be activated outside the state machine;
- a legacy experiment may continue its grandfathered runtime, but it may not materially change, advance a stage, increase its approved risk envelope, or add a new arm until it is migrated;
- new code must fail closed when it cannot determine which experiment/deployment it belongs to, except for explicitly grandfathered legacy paths during the migration window.

The implementation must support an enforcement mode such as:

```text
OFF      — schema/read path exists; no enforcement
WARN     — violations are recorded and surfaced
NEW_ONLY — all post-cutover work enforced; grandfathered legacy allowed
STRICT   — no trading/research stage can bypass Experiment OS
```

The target steady state is `STRICT`. The repository should spend as little time as practical in `WARN`.

---

## 5. Core terminology

Precise names matter because several current concepts are overloaded.

### 5.1 Hypothesis

The falsifiable claim about why an edge should exist.

Example:

> During an exchange-dark commodity window, Kalshi favorites are underpriced relative to otherwise-comparable open-window favorites.

A hypothesis describes the scientific claim, not one code implementation.

### 5.2 Experiment

The durable research identity testing a hypothesis.

An experiment survives across operating epochs and may contain multiple treatment/control arms.

### 5.3 Experiment Version

An immutable revision of the scientific/strategy contract.

Create a **new Experiment Version** when any of these materially changes:

- hypothesis;
- treatment definition;
- control definition;
- independent variable;
- held-constant variables;
- entry rule semantics;
- exit rule semantics;
- sizing logic when sizing is part of the tested proposition;
- primary gate or kill criterion;
- metric definition in a way that changes what success means.

A version change means we are no longer testing exactly the same question.

Historical versions remain immutable and queryable.

### 5.4 Experiment Arm

A treatment, control, benchmark, or other pre-declared branch within an experiment version.

Examples:

- treatment = +1 cent maker offset;
- control = +0 cent maker offset;
- treatment = dark-window favorite;
- control = open-window favorite;
- no-trade control;
- external benchmark.

A trading strategy tag is an implementation/deployment label; it is not necessarily the experiment identity.

### 5.5 Experiment Epoch

An immutable operating interval for the **same Experiment Version** under one compatible environment.

Create a new epoch when the scientific question is unchanged but the world or machinery around it changes in a way that can affect comparability, for example:

- market taxonomy/universe change;
- fee model change that cannot be exactly normalized;
- fill-model or execution-model change;
- data provenance change;
- API semantic change;
- exchange behavior change;
- risk/exposure rule change that changes which opportunities can enter;
- repaired implementation defect where pre-fix observations are not equivalent to post-fix observations.

An epoch is the canonical successor to today's hand-written "cohort boundary" concept for operator experiments.

Do **not** reuse the word cohort for this data model because `evo_cohorts` already has a distinct evolutionary meaning.

### 5.6 Deployment

A concrete running implementation of one or more experiment arms at a lifecycle stage.

Examples:

- a paper deployment;
- a live-canary deployment;
- a production deployment;
- a live-paper twin pair.

Deployments have independent identities and code/config fingerprints.

### 5.7 Platform Revision

An immutable version of a shared system component that can affect many experiments, such as:

- fee model;
- fill model;
- market taxonomy;
- execution engine;
- settlement interpretation;
- risk engine;
- data source/parser;
- metric calculation;
- experiment engine itself.

### 5.8 Platform Snapshot

The complete set of active platform revisions pinned to an experiment epoch/deployment.

Every new experiment must inherit a complete platform snapshot from the central registry. This is how a new experiment automatically receives the current fee model, fill model, taxonomy, and other shared semantics instead of accidentally reimplementing or missing them.

### 5.9 Gate

A pre-registered, executable decision rule controlling whether an experiment:

- advances;
- remains in place (`HOLD`);
- is blocked on integrity/data/platform issues;
- fails and retires.

### 5.10 Evidence

The immutable observations and metric snapshots used to evaluate a gate.

A gate result must always identify the exact experiment version, epoch, arms, evidence window, platform snapshot, metric implementation, and computation timestamp that produced it.

---

## 6. Canonical object graph

The target lineage is:

```text
Hypothesis
   │
   └── Experiment
          │
          ├── Experiment Version 1
          │      │
          │      ├── Arm A (treatment)
          │      ├── Arm B (control)
          │      │
          │      ├── Epoch 1 ── Platform Snapshot P1
          │      │      ├── PAPER Deployment
          │      │      └── evidence / gate results
          │      │
          │      └── Epoch 2 ── Platform Snapshot P2
          │             └── LIVE_CANARY Deployment + paper twin
          │
          └── Experiment Version 2
                 └── changed scientific/strategy contract
```

A trade/decision must eventually be traceable through:

```text
trade / order / observation
    → deployment
    → experiment arm
    → experiment epoch
    → experiment version
    → experiment / hypothesis
    → platform snapshot
```

This lineage is a hard architectural invariant.

---

## 7. Lifecycle state machine

Canonical lifecycle states:

```text
IDEA
PROBE
PAPER
LIVE_CANARY
PRODUCTION
PAUSED
RETIRED
```

`PASS`, `FAIL`, `HOLD`, and `BLOCKED_*` are **gate verdicts**, not lifecycle states.

### 7.1 Legal forward path

```text
IDEA → PROBE → PAPER → LIVE_CANARY → PRODUCTION
```

### 7.2 Exceptional transitions

Any active state may enter `PAUSED` for safety, data, integrity, or platform reasons.

`PAUSED` may return only to the state it paused from when:

- the blocking condition is resolved;
- no material strategy semantics changed;
- the appropriate platform/experiment revision or new epoch has been created when required;
- the resume is explicitly audited.

`RETIRED` is terminal. A revived concept creates a successor Experiment or Experiment Version that references the retired predecessor; it does not reopen and rewrite the old record.

### 7.3 No silent rollback

If a PAPER experiment changes its scientific rules after observing results, do not "go back to PROBE" on the same version and overwrite history. Create a new version or successor experiment.

If only the environment changed, create a new epoch.

---

## 8. Stage contracts

### 8.1 IDEA

Required before leaving IDEA:

- one-sentence falsifiable hypothesis;
- mechanism explaining why the mispricing could exist/persist;
- likely counterparty / source of error;
- market family/universe;
- data/testability assessment;
- correlation to existing strategies;
- rough capacity/economic relevance;
- explicit falsification statement;
- graveyard/prior-art check;
- origin (`operator`, `idea_model`, `evo`, `external_research`, etc.).

Exit gate: idea is sufficiently specific and testable to justify a probe.

### 8.2 PROBE

A probe is a cheap validation instrument, not a trading deployment.

Required:

- pre-registered predictions;
- numeric or otherwise executable pass thresholds;
- kill criteria;
- sample/testability floor;
- provenance/no-lookahead declaration;
- control/benchmark where applicable;
- cost/fee assumptions where relevant;
- probe implementation/version;
- immutable pre-registration hash before results are observed.

Probe outcomes:

- `PASS` → eligible for PAPER build;
- `FAIL` → RETIRED;
- `HOLD` → remain in PROBE until the pre-declared revisit condition is met;
- `BLOCKED_DATA` / `BLOCKED_INTEGRITY` → results may not be interpreted until repaired.

A probe pass is evidence to build a paper experiment, not permission to trade real money.

### 8.3 PAPER

Paper validates forward behavior under live market conditions without real capital.

Required before PAPER entry:

- Experiment Version frozen;
- arms frozen;
- independent variable explicitly declared;
- held-constant variables explicitly declared;
- treatment/control mapping frozen;
- entry/exit/sizing/execution specs fingerprinted;
- current platform snapshot pinned;
- experiment epoch opened;
- paper metrics contract registered;
- minimum sample and promotion/kill gate pre-registered;
- data-health requirements registered;
- book/deployment identity registered;
- experiment visible to operator and evo research reads.

PAPER must not advance merely because absolute P&L is positive if the pre-registered question is relative to a control.

### 8.4 LIVE_CANARY

Live canary is a **separate experiment stage**, not the end of paper.

Its purpose is to measure whether paper assumptions survive real exchange execution.

Required before entry:

- PAPER gate PASS;
- explicit operator approval;
- fresh live deployment identity;
- no inherited paper positions allowed to silently suppress live eligibility;
- live-paper twin starts at the same effective boundary;
- tiny approved risk envelope;
- kill switch and orderly drain verified;
- exposure/risk limits registered;
- execution telemetry requirements active;
- live-vs-paper divergence gate pre-registered;
- platform snapshot pinned;
- fresh epoch if the live transition changes any relevant environment semantics.

The live canary must measure at least:

- decision alignment;
- attempted/accepted/rejected orders;
- fill rate and partial fills;
- price/slippage/fees;
- queue/execution telemetry where applicable;
- matched-market paper-twin vs live P&L;
- data/worker health;
- risk/integrity incidents.

A profitable paper strategy that fails live execution realism is a failed live canary until proven otherwise.

### 8.5 PRODUCTION

Production means the strategy has passed the real-money validation stage and may operate within an approved normal risk envelope.

Required:

- LIVE_CANARY gate PASS;
- explicit operator approval;
- no unresolved live-paper accounting/execution gap;
- risk controls proven operational;
- monitoring and kill criteria active;
- approved production sizing envelope;
- production deployment record.

Production does not make the experiment immutable forever. Monitoring can trigger `PAUSED`, a new epoch, or retirement.

A material logic change after promotion is not "just a production tweak"; it follows the version/epoch protocol in this specification.

---

## 9. Universal experiment contract

Every Experiment Version must expose a machine-readable contract containing at least:

```yaml
identity:
  experiment_id: stable-id
  slug: human-readable
  version: 1
  origin: operator | idea_model | evo | external_research
  family: maker | observation_pin | forecast | structural | ...

hypothesis:
  one_liner: ...
  mechanism: ...
  counterparty: ...
  falsification: ...

universe:
  selector: ...
  exclusions: ...

strategy:
  entry_rule: ...
  exit_rule: ...
  sizing_rule: ...
  execution_style: maker | taker | mixed

experiment_design:
  independent_variable: ...
  held_constant_variables: [...]
  control_required: true
  control_exemption_reason: null

arms:
  - id: treatment
    role: treatment
    strategy_tag: ...
  - id: control
    role: control
    strategy_tag: ...

metrics:
  primary: ...
  secondary: [...]
  integrity: [...]

probe_gate: ...
paper_gate: ...
live_canary_gate: ...
production_monitoring_gate: ...

sample:
  minimum_n: ...
  unit: contracts | trades | events | paired_events | ...
  matching_rule: ...

costs:
  fee_basis: platform
  fill_basis: platform
  slippage_basis: ...

risk:
  max_position: ...
  max_exposure: ...
  max_daily_loss: ...
  strategy_specific: ...

provenance:
  required_sources: [...]
  freshness_requirements: ...
  point_in_time_required: true

monitoring:
  cadence: ...
  expected_trade_frequency: ...
  stale_after: ...

docs:
  thesis: docs/...
  studies: [...]
```

The implementation may normalize this into relational tables rather than storing one YAML blob, but the semantics must remain accessible as a single contract.

---

## 10. Independent variable and held-constant invariants

For treatment/control experiments, the system must explicitly know what is allowed to differ.

Example:

```text
Question:
Does paying +1 cent improve maker queue placement enough to improve realizable economics?

Independent variable:
maker price offset

Held constant:
market universe
entry window
price band
max price
order size
exit logic
risk envelope
scanner depth
market taxonomy
platform snapshot
sample window
```

The system should compute fingerprints for held-constant fields. If a supposedly held-constant field changes during an active experiment:

1. flag `EXPERIMENT_INTEGRITY_VIOLATION`;
2. stop gate accumulation for contaminated observations;
3. classify whether the change requires a new epoch or version;
4. never silently pool the contaminated sample into the pre-registered gate.

This mechanism generalizes lessons currently enforced book by book.

---

## 11. Structured gates

Gate definitions must be executable data, not only prose.

Example:

```yaml
paper_to_live_canary:
  sample:
    treatment:
      metric: settled_events
      op: ">="
      value: 150
    control:
      metric: settled_events
      op: ">="
      value: 150

  pass_all:
    - metric: pnl_cents_per_trade
      arm: treatment
      op: ">"
      value: 0

    - metric: delta.pnl_cents_per_trade
      treatment: treatment
      control: control
      op: ">="
      value: 3.0

  fail_any:
    - metric: delta.pnl_cents_per_trade
      treatment: treatment
      control: control
      op: "<="
      value: 0
```

### 11.1 Gate result statuses

Every evaluation returns one of:

```text
PASS
FAIL
HOLD
BLOCKED_DATA
BLOCKED_INTEGRITY
BLOCKED_PLATFORM
```

A gate result includes:

- experiment/version/epoch;
- arms;
- sample definition;
- evidence start/end;
- metrics and confidence intervals where relevant;
- platform snapshot;
- metric implementation/version;
- integrity status;
- computed timestamp;
- immutable evidence reference;
- human-readable explanation.

### 11.2 Gate edits after observation begins

A pre-registered success/kill gate may not be changed in place after eligible observations begin.

If a gate has a logical gap or proves poorly specified, record that fact. Do not rescue the existing result by moving the threshold.

A revised scientific decision rule creates a new Experiment Version or successor experiment.

A metric bug fix that changes how the same declared metric is computed is handled through the Platform Revision protocol, not by rewriting the gate.

---

## 12. Universal measurement layer

Every PAPER, LIVE_CANARY, and PRODUCTION deployment must expose a common scoreboard, with strategy-specific metrics layered on top.

### 12.1 Universal categories

**Sample / exposure**

- decisions;
- attempted entries;
- accepted entries;
- settled trades/contracts/events;
- open positions;
- capital at risk;
- expected vs observed trade frequency.

**Return**

- realized P&L;
- cents/trade;
- cents/contract;
- gross vs net where relevant;
- win rate;
- tail statistics;
- drawdown where meaningful.

**Execution**

- accepted/rejected/canceled;
- maker/taker;
- fill rate;
- partial fill rate;
- time to fill;
- execution price gap/slippage;
- queue telemetry when applicable.

**Experiment comparison**

- treatment N;
- control N;
- matched-window N;
- treatment-control delta;
- uncertainty/confidence interval;
- gate status.

**Costs**

- fee model/version;
- actual fees;
- modeled fees;
- spread/slippage;
- fill-model/version.

**Integrity**

- Experiment Version;
- epoch;
- code/config fingerprint;
- platform snapshot;
- contamination warnings;
- legacy/migrated status.

**Health**

- required source freshness;
- missing/null coverage;
- parser failures;
- API/schema warnings;
- worker health;
- measurement coverage.

### 12.2 Strategy-specific studies remain valid

Deep studies such as queue analysis, weather model calibration, exit replays, seasonal supply forecasts, and pairing audits remain valuable.

They should consume the canonical identity/epoch/platform information rather than each inventing their own boundary logic.

---

## 13. Experiment identity versus strategy tags

Current strategy tags are useful operational join keys but should no longer carry the entire research identity.

Target relationship:

```text
Experiment
  └── Version
       └── Arm
            └── Deployment
                 └── one or more implementation strategy tags
```

A strategy tag may change between paper and live canary for operational reasons without changing the scientific arm identity.

This is particularly important when a fresh live tag is needed to avoid inheriting mature paper state.

The system must preserve both:

- stable experiment/arm identity for research;
- concrete strategy tag for execution/debugging.

---

## 14. Required lineage on new observations and trades

After strict enforcement, every new paper/live order, fill, position, and trade produced by an Experiment OS-managed book should be attributable to:

```text
experiment_id
experiment_version_id
experiment_arm_id
experiment_epoch_id
deployment_id
platform_snapshot_id
decision_id / opportunity_id when available
```

Legacy rows may remain null for these fields, but no post-enforcement new-system row may silently omit them.

The implementation may initially add lineage through mapping tables rather than altering every historical table at once, but the query path must be lossless.

---

## 15. Fingerprints and immutable pre-registration

The system must compute reproducible fingerprints for material strategy/deployment semantics.

Suggested components:

```text
universe selector
entry rule
exit rule
sizing rule
execution rule
arm parameters
control definition
risk envelope
platform snapshot
code/config revision
```

At every stage entry, store the relevant fingerprint.

If the active fingerprint changes:

- compare the changed fields against declared independent/held-constant variables;
- classify the change;
- create a new epoch or Experiment Version as required;
- block sample pooling until the change is resolved.

Pre-registration documents/gate specs should also receive immutable hashes at the time a stage begins. Later editorial documentation may continue, but the system retains which exact thesis/gate revision governed the experiment when evidence started accumulating.

---

## 16. Platform dependency registry

This is the mechanism that prevents a future fee-model, fill-model, taxonomy, parser, or execution change from being buried somewhere that a new experiment misses.

### 16.1 Platform components

Create a central registry for at least:

```text
FEE_MODEL
FILL_MODEL
MARKET_TAXONOMY
EXECUTION_ENGINE
SETTLEMENT_ENGINE
RISK_ENGINE
DATA_PROVENANCE
KALSHI_API_SCHEMA
METRICS_ENGINE
EXPERIMENT_ENGINE
```

Additional components can be added without changing the experiment lifecycle.

### 16.2 PlatformRevision

Each revision is immutable and should include:

```text
component
version
status: pending | active | retired
code/config fingerprint
created_at
activated_at
description
reason
backward_compatibility
normalization/transform availability
safety classification
PR/commit reference
```

### 16.3 PlatformSnapshot

A Platform Snapshot is a complete bundle of active revisions.

New experiment epochs do not manually choose a fee version from memory; they resolve the active snapshot from this registry.

That makes "current fee model" a system property, not a convention copied into every strategy.

---

## 17. Systemic change protocol

Every shared change that could affect experiment economics, selection, interpretation, or safety must follow this protocol.

### Step 1 — Declare the platform revision in the change

The same PR that changes shared behavior must declare:

- component being changed;
- old revision;
- new revision;
- exact semantic difference;
- expected effective/deploy boundary;
- impact class;
- whether historical observations can be exactly transformed;
- affected experiment query/result;
- required action for each affected active experiment.

A systemic change is incomplete without its experiment-impact declaration.

### Step 2 — Automatically identify affected experiments

Because each Experiment Epoch pins a Platform Snapshot, the system can query all active experiments depending on the changed component.

The author should not manually remember every book.

### Step 3 — Assign an impact action

Each affected experiment receives one of:

```text
NO_ACTION
RECOMPUTE
NEW_EPOCH
NEW_EXPERIMENT_VERSION
PAUSE
RETIRE
```

No affected active experiment may be left unclassified.

### Step 4 — Activate the revision at a real runtime boundary

Prefer a measured `activated_at` timestamp from the deployed worker/database over a guessed merge timestamp.

If activation timing is uncertain and the change can affect sample comparability, gate evaluation must become `BLOCKED_PLATFORM` until the boundary is established.

### Step 5 — Stamp future evidence with the new snapshot

New observations/orders/metrics after activation must resolve to the new Platform Snapshot and appropriate epoch/version.

### Step 6 — Verify after deployment

The change must have a read/health check confirming:

- the new revision is active;
- affected experiments received their intended action;
- no mixed-version observations are being silently pooled;
- expected metrics/data are still populated.

---

## 18. Platform change impact classes

A common classification makes future changes predictable.

### I0 — OBSERVABILITY_ONLY

The change does not alter strategy decisions, economics, eligible universe, metric meaning, or safety.

Examples:

- adding a diagnostic field;
- improving a log message;
- persisting raw payload alongside already-correct parsed data.

Default action: `NO_ACTION`.

If an observability change reveals that prior data was wrong, the discovered defect is classified separately based on its actual impact.

### I1 — EXACTLY_NORMALIZABLE

The change affects reported economics/metrics, but every affected historical observation can be transformed exactly to the new semantics from stored raw data.

Default action: `RECOMPUTE`.

The same epoch may remain usable only when the transformation is mathematically exact and does not change which opportunities entered the sample.

### I2 — SAMPLE_BOUNDARY

The scientific question remains the same, but the opportunity population, execution environment, data semantics, or selection path changed so pre/post observations are not safely poolable.

Default action: `NEW_EPOCH`.

Examples:

- taxonomy/universe expansion;
- repaired pairing logic;
- execution behavior that changes which orders are eligible/fillable;
- fee change without exact historical recomputation;
- data-source behavior change that alters available signals.

### I3 — EXPERIMENT_DEFINITION

The treatment/control relationship, hypothesis, gate, entry/exit semantics, or independent variable changed.

Default action: `NEW_EXPERIMENT_VERSION`.

The old evidence remains attached to the old version.

### I4 — SAFETY_CRITICAL

The change or discovered defect means continuing to trade before remediation is unsafe or exposure is not controlled as intended.

Default action: `PAUSE` affected live/production deployments immediately, then create the required revision/epoch/version before resuming.

Safety outranks experimental continuity.

---

## 19. Worked protocol: future fee-model change

Suppose Kalshi changes fees or we discover our fee calculation is wrong.

The change must not be implemented only inside a helper function and documented later.

### Required flow

1. Create `FEE_MODEL` revision, e.g. `fee_model:v3`.
2. Describe old formula, new formula, affected liquidity roles, rounding semantics, and activation evidence.
3. Query active Experiment Epochs whose snapshot includes the old fee revision.
4. Determine whether historical observations can be exactly re-fee'd.

If every trade stores enough raw information to recompute the true fee exactly:

```text
impact = I1 EXACTLY_NORMALIZABLE
action = RECOMPUTE
```

The metrics engine must:

- retain originally-recorded accounting;
- compute normalized net metrics under `fee_model:v3`;
- identify the normalization version in every gate result;
- prove relative gates remain or do not remain invariant;
- never silently rewrite the raw historical transaction record.

If exact recomputation is impossible, or the fee change changes which orders would have been economical/eligible:

```text
impact = I2 SAMPLE_BOUNDARY
action = NEW_EPOCH
```

The old evidence remains valid evidence from the old environment, but new promotion gates use the new epoch unless a gate explicitly supports a cross-epoch model.

If a strategy's actual entry rule uses fee-adjusted edge and therefore begins selecting different markets under the new formula, the change may rise to I2 or I3 depending on whether the strategy definition itself changed.

This classification is made before the new sample is interpreted.

---

## 20. Worked protocol: taxonomy/universe change

A market-taxonomy expansion changes which opportunities an arm can see.

Even if the strategy rule text is byte-identical, its offered population changes.

Therefore:

```text
component = MARKET_TAXONOMY
impact = I2 SAMPLE_BOUNDARY
action = NEW_EPOCH
```

No additive cents correction can turn the old sample into the new universe.

Gate evaluation must floor the evidence window at the new epoch boundary and compare treatment/control over compatible windows.

This generalizes the repo's existing hard-floor discipline into system behavior.

---

## 21. Worked protocol: metric/parser bug

If an API/parser defect caused a metric to be missing but never affected trade selection or economics:

```text
impact may be I0 or I1
```

depending on whether history can be reconstructed.

If the parser defect changed what markets were selected, how a signal was computed, or how a treatment/control population formed:

```text
impact = I2 or I3
```

The system must classify by **semantic consequence**, not by how small the code diff looks.

---

## 22. Legacy migration policy

The existing repository is valuable research history and must not be discarded. Migration must also avoid pretending historical data has metadata it never recorded.

The migration principle is:

> Preserve history exactly, reconstruct only what can be justified, and create a clean new-system boundary whenever comparability is uncertain.

### 22.1 Legacy classes

Every legacy strategy/experiment should eventually be classified as:

```text
ACTIVE_LIVE
ACTIVE_PAPER
ACTIVE_PROBE_OR_HOLD
RETIRED_OR_KILLED
HISTORICAL_UNTRACKED
EVO_LEGACY
```

### 22.2 Active live migration

Existing real-money books may continue running during the migration window to avoid creating risk merely for architectural cleanliness.

Migration steps:

1. create Experiment/Hypothesis records from the strongest existing thesis/registry evidence;
2. create Experiment Version representing the current live semantics;
3. create arms and identify controls/twins;
4. fingerprint current code/config;
5. create a grandfathered deployment record;
6. create a Platform Snapshot matching the current runtime;
7. establish a migration epoch boundary;
8. attach pre-migration performance as `legacy_evidence`;
9. verify current open orders/positions map to the intended book;
10. mark migration integrity level.

Until verified, the book may continue its existing approved runtime but may **not**:

- increase approved size;
- add a new arm;
- change entry/exit logic;
- change its control;
- promote to a different lifecycle state.

If historical comparability can be proven, legacy evidence may be referenced in analysis. The default promotion sample after migration should still be the clean new epoch unless the migration explicitly certifies the historical window.

### 22.3 Active paper migration

For an active paper book:

1. import the thesis/gate/strategy identity;
2. define arms/control;
3. reconstruct configuration/dependency versions where possible;
4. open a migration epoch;
5. classify historical rows as either `certified_legacy_evidence` or `context_only`.

Do not pool old and new automatically merely because the strategy tag matches.

### 22.4 Probe/HOLD migration

Legacy ideas and probes do not need bulk runtime migration.

If revived after enforcement cutover:

- create a new Experiment record/version or import the old hypothesis;
- attach old results as prior evidence;
- re-evaluate testability and platform assumptions under the current snapshot;
- do not grandfather an old PASS into PAPER automatically if the environment materially changed.

### 22.5 Retired/killed migration

Do not spend engineering time fully normalizing every dead historical book.

Bulk-import or map enough metadata to preserve:

- identity/family;
- thesis or rationale;
- verdict;
- kill reason;
- references to source docs/trades;
- graveyard semantics.

Detailed arm/epoch reconstruction is performed only if a future experiment needs to compare against that history.

### 22.6 Historical untracked books

If a trading tag exists without enough evidence to reconstruct a valid experiment contract:

- preserve it as `HISTORICAL_UNTRACKED`;
- never invent a thesis or gate after the fact;
- allow its P&L to remain queryable as historical operations data;
- do not treat it as clean experimental evidence.

### 22.7 Evo legacy migration

Existing evo agents/strategies remain valid historical evo artifacts.

The foundation should not rewrite all evo history.

Instead:

- map future evo proposals into Experiment OS immediately after evo integration;
- migrate active/important evo strategy artifacts when they need shared operator visibility;
- retain evo-specific cohort/fitness lineage alongside, not instead of, Experiment OS lineage.

---

## 23. Migration integrity levels

Migration should record how trustworthy the reconstruction is.

Suggested levels:

```text
A — VERIFIED
    exact strategy/config, stage, control, boundary, and platform semantics reconstructed

B — PARTIAL
    experiment identity and main rules reconstructed, but one or more historical dependencies/boundaries uncertain

C — CONTEXT_ONLY
    historical performance exists but cannot be certified for a promotion gate

D — UNTRACKED
    insufficient evidence to reconstruct a legitimate experiment contract
```

No migration tool may silently upgrade an uncertain legacy record to VERIFIED.

---

## 24. Migration contingency plan

Migration failures must not block the entire cutover.

### If a legacy experiment cannot be reconstructed cleanly

- leave the runtime grandfathered if safe;
- mark historical evidence `CONTEXT_ONLY`;
- open a fresh Experiment Epoch at migration;
- let clean evidence accumulate from that boundary.

### If current code/config cannot be fingerprinted reliably

- do not claim historical equivalence;
- establish the current fingerprint as the new baseline;
- create a fresh epoch.

### If the active control is ambiguous

- block promotion with `BLOCKED_INTEGRITY`;
- allow safe paper collection if useful;
- do not manufacture a control relationship retrospectively.

### If a legacy live book must change for safety before migration finishes

Safety change is allowed immediately.

Record the emergency revision, pause when necessary, and accept that it creates a new migration/experiment boundary. Experimental cleanliness may never prevent a necessary risk reduction.

---

## 25. Documentation contract after Experiment OS

Markdown remains important for mechanism, interpretation, research narrative, and agent context, but it stops being the only source of factual lifecycle state.

### 25.1 `BOOK_REGISTRY.md`

Target state: generated from or validated against Experiment OS.

It remains the concise human/bot index, but fields such as:

- current state;
- arm/tag mapping;
- sample/gate status;
- epoch;
- control;
- platform snapshot;

must come from structured state rather than manually drifting prose.

### 25.2 Thesis/study docs

Remain the durable explanation of:

- mechanism;
- pre-registration;
- studies;
- interpretation;
- verdict narrative.

The governing revision/hash is pinned when a stage begins.

### 25.3 `IDEA_MODEL_SCORECARD.md`

Retain the valuable base-rate and family-learning narrative.

Over time, status/count fields should be generated from structured experiment state while qualitative lessons remain authored prose.

### 25.4 `RESEARCH_JOURNAL.md`

Remain a qualitative learning log, not the canonical source for state transitions.

### 25.5 Graveyard

Retirement/kill results flow automatically into the shared graveyard with links to the Experiment/version/gate result that caused retirement.

---

## 26. Evo integration

The evolutionary fleet must use the same research operating system rather than creating a parallel experimental universe.

### 26.1 Evo as an originator

Evo should be able to:

```text
list_experiments
inspect_experiment
inspect_metrics
read_thesis
read_history
propose_experiment
propose_variant
propose_probe
request_data_source
submit_probe_result
```

Evo should not initially be able to:

```text
promote_to_live
promote_to_production
increase_real_money_risk
bypass_gate
rewrite_pre_registration
```

### 26.2 Shared lifecycle

Long-term target:

```text
EVO IDEA
  → PROBE
  → PAPER
  → operator-reviewed LIVE_CANARY
  → operator-reviewed PRODUCTION
```

Evo can eventually autonomously operate the low-risk IDEA/PROBE/PAPER portions if the common gate/integrity machinery is mature.

### 26.3 Daily research digest

Provide evo a structured daily/heartbeat feed from Experiment OS:

```text
new experiments
stage transitions
new verdicts
new platform revisions
new epochs/boundaries
retirements
open experiments approaching gates
blocked integrity/data issues
new research docs
capability requests
```

Agents should receive the same factual experiment state the operator sees.

### 26.4 Shared premise + scoreboard

Preserve the repo's successful current principle:

> premise without performance invites cargo culting; performance without premise invites number copying.

Experiment OS should make both channels first-class.

---

## 27. Authority and approval model

### Autonomous/system-owned

The system may automatically:

- register observations;
- compute metrics;
- evaluate gates;
- mark gate results PASS/FAIL/HOLD/BLOCKED;
- create alerts;
- create a required new epoch when a declared platform revision mandates it;
- prevent invalid transitions;
- retire a paper-only experiment if its pre-registered hard kill rule is explicitly configured for automatic retirement (future option).

### Operator-owned initially

The operator must approve:

- PAPER → LIVE_CANARY;
- LIVE_CANARY → PRODUCTION;
- any increase in real-money risk envelope;
- any override of a blocked/failed gate;
- exceptional resurrection of a retired idea via a successor experiment.

An override must never rewrite the gate result. It creates an explicit audited override record with reason.

---

## 28. Safety and risk as platform dependencies

Global risk controls are not incidental implementation details. They are Platform Components.

Experiment-specific risk specs inherit global controls and may only become stricter unless explicitly approved.

A risk change is classified by impact:

- purely stronger emergency protection may be I4 and applied immediately;
- a risk rule that changes which opportunities can enter can also create an I2 sample boundary;
- a sizing rule that is part of the tested hypothesis can require I3 Experiment Version.

Cancels/drains that only reduce exposure must remain possible under emergency kill conditions.

---

## 29. Data health as a gate prerequisite

A profitable number produced from stale, missing, misparsed, or structurally different data is not a PASS.

Each experiment declares required sources and freshness/coverage thresholds.

If violated:

```text
gate = BLOCKED_DATA
```

The system must distinguish:

- meaningful zero;
- missing/null;
- parser failure;
- no opportunity;
- collector down;
- censored outcome.

These distinctions must not be reconstructed from truthiness checks after the fact.

---

## 30. Production invariants

The finished system should make the following questions answerable for every active experiment without strategy-specific archaeology:

1. What hypothesis is being tested?
2. What lifecycle state is it in?
3. What version of that hypothesis/rule set is active?
4. What changed at the current epoch boundary?
5. Which arm is treatment and which is control?
6. What is the independent variable?
7. What must remain constant?
8. What Platform Snapshot is active?
9. What evidence counts toward the current gate?
10. What evidence is excluded and why?
11. What is the current gate verdict?
12. What blocks promotion?
13. What real-money risk is approved?
14. Which code/config deployment produced each trade?
15. What happens if the fee/fill/taxonomy/execution model changes tomorrow?

If any of these require reading several unrelated docs and guessing, the migration is incomplete.

---

## 31. Suggested foundation data model

Exact SQLAlchemy names may change during implementation, but the concepts should be first-class.

Suggested entities:

```text
experiments
experiment_versions
experiment_arms
experiment_epochs
experiment_deployments
experiment_gates
experiment_gate_results
experiment_state_transitions
experiment_integrity_events
experiment_legacy_evidence
platform_components
platform_revisions
platform_snapshots
platform_snapshot_items
platform_impact_actions
```

Optional later entities:

```text
experiment_metric_snapshots
experiment_decisions
experiment_overrides
experiment_doc_revisions
experiment_migration_records
```

All consequential transitions/revisions are append-only/audited. Current state may be cached, but history is never rewritten.

---

## 32. Read API / service contract

Build one shared service instead of letting each script reinterpret state.

Representative operations:

```python
create_experiment(...)
create_experiment_version(...)
add_arm(...)
open_epoch(...)
register_deployment(...)
transition_experiment(...)
evaluate_gate(...)
get_experiment(...)
get_experiment_metrics(...)
get_active_platform_snapshot(...)
register_platform_revision(...)
assess_platform_impact(...)
record_integrity_event(...)
import_legacy_experiment(...)
```

Read models should support:

```text
all active experiments
experiments by lifecycle stage
experiments blocked on data/integrity/platform
experiments nearing a sample gate
all deployments using a platform revision
all trades belonging to an experiment/arm/epoch
all historical versions/epochs of a hypothesis
```

The strategy status loop, dashboards/scripts, generated registry, and evo should consume this common read path.

---

## 33. CI and runtime enforcement targets

Once implementation matures, add checks that make invalid experiment structure difficult to merge/deploy.

Potential checks:

- new paper strategy tag without Experiment/Arm/Deployment mapping → fail;
- new live strategy without LIVE_CANARY state and operator approval → fail;
- live canary without a paper twin → fail;
- active treatment/control arms with differing held-constant fingerprints → fail/block;
- gate changed after evidence start without new version → fail;
- systemic platform change with unclassified active experiments → fail;
- active platform revision missing from new Experiment Epoch snapshot → fail;
- evidence written under two platform snapshots to one epoch → integrity alert/block;
- post-cutover trade with missing experiment lineage → fail/alert according to enforcement mode;
- unknown legacy book appearing after cutover → UNTRACKED + block new entries in STRICT mode.

---

## 34. Implementation sequence and PR boundaries

Do not build this as one giant behavior-changing PR.

### PR 0 — This specification

Documentation only.

No trading behavior changes.

### PR 1 — Foundation models + read-only state machine

Build:

- core tables/models;
- lifecycle enums and legal transition validator;
- Experiment/Version/Arm/Epoch/Deployment identity;
- Platform Component/Revision/Snapshot identity;
- structured gate schema storage;
- append-only state-transition/audit records;
- read-only CLI/script/API to inspect state.

Do **not** alter existing books yet.

Acceptance: represent at least three materially different experiment shapes in tests/fixtures.

Recommended proving shapes:

1. multi-arm treatment/control paper experiment;
2. live-canary + paper twin experiment;
3. retired historical experiment.

### PR 2 — Legacy importer + cutover baseline

Build:

- migration classification;
- integrity levels A/B/C/D;
- import mapping from `BOOK_REGISTRY`, thesis docs, `paper_trades`, live strategy config where available;
- central baseline Platform Snapshot representing current deployed semantics;
- grandfathered runtime records;
- migration report listing unmapped/uncertain books.

No automatic stage promotions.

### PR 3 — Universal metrics + gate evaluator

Build:

- common scoreboard;
- structured gate execution;
- matched-window/control support;
- evidence window/epoch enforcement;
- PASS/FAIL/HOLD/BLOCKED results;
- immutable gate-result snapshots.

Convert representative existing experiment reads first before broad migration.

### PR 4 — New-work enforcement + trade lineage

Build:

- `NEW_ONLY` enforcement;
- experiment lineage on new paper/live decisions/orders/trades;
- registration requirement for new paper books;
- stage transition service;
- live-canary fresh-deployment + twin enforcement.

At this point Enforcement Cutover can occur.

### PR 5 — Platform revision impact engine

Build:

- component/revision registration workflow;
- affected-experiment query;
- impact classification/action records;
- activation boundary tracking;
- automatic new-epoch requirement;
- `BLOCKED_PLATFORM` behavior;
- tests using fee and taxonomy changes as canonical examples.

### PR 6 — Strategy loop/docs integration

Move:

- strategy-status loop;
- `BOOK_REGISTRY` status facts;
- general experiment result views;

onto the shared Experiment OS read path.

Preserve narrative docs but eliminate duplicated mutable factual state.

### PR 7 — Evo integration

Expose Experiment OS read/proposal actions to agents.

Provide daily research digest.

Make new evo experiments use the common lifecycle for probe/paper work while retaining evo's own cohort/fitness lineage.

### PR 8+ — Strict mode and cleanup

After active legacy experiments are migrated:

- move enforcement to `STRICT`;
- remove obsolete bespoke lifecycle logic;
- retain historical scripts/docs where they carry unique analysis value;
- do not delete graveyard/history.

---

## 35. Foundation acceptance criteria

Experiment OS is not ready for enforcement until all of these are true:

1. A new experiment can be created and moved through legal non-money states with immutable transitions.
2. Illegal transitions are rejected.
3. Experiment Version versus Experiment Epoch behavior is tested and documented.
4. A treatment/control experiment can declare independent and held-constant variables.
5. A structured gate can be stored and evaluated without parsing Markdown prose.
6. A Platform Snapshot is complete and pinned to an epoch/deployment.
7. A platform revision can list every active experiment it affects.
8. A fee-model example correctly chooses RECOMPUTE vs NEW_EPOCH based on exact normalizability.
9. A taxonomy example correctly forces a NEW_EPOCH.
10. A treatment/control semantic change forces a NEW_EXPERIMENT_VERSION.
11. A legacy experiment can be imported without inventing missing history.
12. Migration uncertainty is visible through integrity level.
13. New evidence can be traced to experiment/version/arm/epoch/deployment/platform snapshot.
14. Existing trading behavior remains unchanged until the explicit enforcement PR.
15. Evo can eventually consume the same factual state without bot-only duplicated docs.

---

## 36. Test philosophy

Tests should target false conclusions, not just happy-path CRUD.

At minimum the eventual implementation must test:

- zero versus null remains distinct;
- a gate cannot count pre-epoch rows after an I2 boundary;
- an I1 exact normalization does not silently mutate raw historical accounting;
- an uncertain activation boundary blocks interpretation;
- changing a held-constant field contaminates the current experiment;
- changing only the declared independent variable across arms does not trigger a false integrity violation;
- a post-observation gate edit is rejected without a new version;
- a retired experiment cannot be reopened in place;
- a paused strategy cannot resume across a material change without the required epoch/version;
- a live canary cannot inherit paper positions as eligible live evidence;
- a live canary cannot exist without a twin unless an explicit future exemption mechanism exists;
- a legacy CONTEXT_ONLY sample cannot satisfy a clean new-system promotion gate;
- an untracked post-cutover book cannot begin trading in STRICT mode;
- a new experiment always receives the current active platform snapshot;
- a platform revision cannot activate while affected active experiments are unclassified;
- a safety-critical revision can pause exposure even if doing so creates an experimental boundary.

Production-shaped fixtures are preferred wherever a prior real failure exposed a schema or lifecycle assumption.

---

## 37. Example: multi-arm structural experiment

A structural experiment with treatment and open-window control might look conceptually like:

```text
Experiment: FREEZE-style dark-window mispricing
Version: 1
State: PAPER

Arm A: dark-window, >=3c discount          (treatment)
Arm B: dark-window, >=8c discount          (secondary)
Arm C: open-window, same favorite bar      (CONTROL)
Arm D: dark-window, max-entry cap           (secondary)

Independent variable:
source dark vs open condition

Primary gate:
A - C >= declared delta AND A > 0 at declared N

Epoch 1:
Platform Snapshot P17
Paper deployment D1
```

If the fee model is exactly corrected, metrics may be normalized under I1.

If the market-family classifier expands, a new epoch begins under I2.

If the control changes from open-window favorite to a different market family, that is I3 and creates a new Experiment Version.

---

## 38. Example: maker offset A/B

```text
Experiment: maker offset queue/economics
Version: 1

Arm A: +0c
Arm B: +1c

Independent variable:
price offset

Held constant:
universe, band, size, entry/exit, risk, scanner depth, taxonomy, epoch window

Primary live-canary question:
does +1c improve realizable economics after execution, not merely fill rate?
```

A queue parser change that only adds correct telemetry can be I0.

A live arming defect that causes one arm to inherit 87 suppressed paper positions while another does not is an integrity break / I2 boundary. The clean remedy is a fresh deployment/epoch rather than pretending the prior arm comparison remained matched.

---

## 39. Example: evo-created strategy

```text
origin = evo
state = IDEA
```

Agent submits:

- hypothesis;
- mechanism;
- proposed universe;
- proposed probe;
- proposed control;
- falsification criterion;
- expected value/capacity;
- relation to graveyard/current experiments.

If accepted into PROBE, it receives the same Experiment ID and Platform Snapshot machinery as an operator idea.

If it passes to PAPER, its performance is visible in the same universal metrics layer.

The evo cohort/agent attribution remains attached as origin/lineage; it does not replace experiment identity.

---

## 40. Decisions intentionally frozen by this spec

Unless a later architecture ADR explicitly supersedes this document, implementation should treat these as decisions rather than open-ended suggestions:

1. There is one common lifecycle for operator, idea-model, and evo experimental work.
2. `LIVE_CANARY` and `PRODUCTION` are distinct states.
3. `PASS/FAIL/HOLD/BLOCKED` are gate verdicts, not lifecycle states.
4. Experiment Version and Experiment Epoch are distinct concepts.
5. Material shared dependencies are centrally versioned as Platform Revisions.
6. Every Experiment Epoch pins a complete Platform Snapshot.
7. Systemic changes require affected-experiment impact classification.
8. New experiments inherit the active shared platform baseline; they do not independently copy fee/fill/taxonomy assumptions.
9. Historical raw evidence is never rewritten to manufacture consistency.
10. Legacy migration may preserve context without certifying it for promotion gates.
11. Retired experiments are terminal; revival creates a successor.
12. Pre-registered gates cannot be edited in place after evidence begins.
13. Treatment/control held constants must be machine-checkable where practical.
14. Real-money promotion requires operator approval initially.
15. Markdown remains important research context but is not the sole canonical source of mutable lifecycle state.
16. The strategy status loop and evo ultimately consume the same Experiment OS facts.
17. Safety-critical actions may interrupt an experiment immediately; capital protection outranks sample continuity.
18. The enforcement cutover is explicit and recorded, not inferred from this spec's date.

---

## 41. Definition of done for the overall migration

The repository has completed the Experiment OS migration when:

- every new experiment since enforcement cutover follows this lifecycle;
- every active legacy paper/live experiment has been migrated or explicitly grandfathered with visible integrity status;
- every active real-money strategy resolves to Experiment → Version → Arm → Epoch → Deployment → Platform Snapshot;
- new platform changes cannot be deployed without identifying affected active experiments;
- experiment gates are evaluated from structured state over explicit compatible evidence windows;
- the strategy loop no longer has to reverse-engineer what an active book is trying to prove;
- evo sees current experiments, verdicts, platform changes, and live performance through the same system;
- `BOOK_REGISTRY` and other summary docs can no longer silently disagree with runtime state;
- a future engineer can answer "what changed, when, what evidence still counts, and why is this strategy allowed to trade?" from one coherent lineage.

At that point the repository is no longer a collection of sophisticated but separate strategy workflows. It is a research-and-trading system whose experiments are explicit, versioned, comparable, auditable, and difficult to accidentally misinterpret.
