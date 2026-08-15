# Claude Session System — Roles, Routing, and Legacy Cleanup Specification

Status: **DESIGN CONTRACT — implementation pending**  
Owner: repository-wide Claude Code operating model  
Depends on: `docs/EXPERIMENT_OPERATING_SYSTEM_SPEC.md`  
Applies to: every new Claude Code session working in this repository

---

## 1. Purpose

The Experiment Operating System standardizes how trading ideas move from research to production. This specification standardizes the **human/Claude operating layer above it**.

The repository is now complex enough that several Claude Code sessions commonly run in parallel. Today, different sessions can load different historical skills, status loops, branch-state files, and strategy-specific instructions and reach different conclusions about what is current. The repository has accumulated multiple generations of status-checking workflows (`full-update`, two Kalshi loop checkers, `mm_check_1`, seasonal checks, live/paper parity, and strategy-specific runbooks), many of which were correct when created but overlap with responsibilities that Experiment OS is intended to centralize.

The goal of this system is:

> A brand-new Claude session should know what role it is playing, what it may change, which shared skills it should load, which source of truth it should query first, and what a standard output looks like — without relying on chat history or accidentally following a superseded workflow.

Session roles do **not** create parallel sources of truth. Experiment OS remains the canonical lifecycle and evidence system. Session files are operating playbooks layered on top of it.

---

## 2. Design principles

### 2.1 Experiment OS is the source of truth

A session role must never maintain its own independent interpretation of experiment state.

The source-of-truth order is:

1. **Experiment OS structured state** — experiments, versions, arms, epochs, deployments, gates, platform snapshots, transition/audit history.
2. **Current Platform Revision / Platform Snapshot state.**
3. **Durable research evidence** — thesis docs, probe results, postmortems, and graveyard/history.
4. **Session role playbooks** — how a Claude session should interact with the above.
5. **Shared Claude skills** — reusable procedures invoked by one or more roles.
6. **`CLAUDE.md`** — bootstrap/router plus repository-wide invariants and pointers.

Historical status branches, stale summary Markdown, old loop snapshots, and prior chat output are not authoritative once Experiment OS can answer the same question.

### 2.2 Roles define operating posture, not ownership of experiments

There is no "FREEZE session," "mmsell session," or "experiment builder session."

Any task-specific engineering session may work on an experiment while obeying Experiment OS. Roles exist for recurring operating modes where consistency is valuable: monitoring, evo health, ticket work, platform changes, research, migration, and live incidents.

### 2.3 Shared logic belongs in shared skills

A role file should not duplicate 500 lines of SQL or strategy logic.

A role file says:

- what to load;
- what shared skills to invoke;
- what queries/read models to request;
- what the session may modify;
- what output to produce;
- when to hand off to another role.

Reusable procedures live in skills or code.

### 2.4 New sessions must identify their role

A session that has not established its operating role is not allowed to silently assume one.

### 2.5 Old session-facing workflows must be removed after replacement

Do not leave multiple active skills with descriptions that all claim to be the current status loop. A `DEPRECATED` heading is not sufficient if the skill can still auto-trigger or be found by a new session.

The migration sequence is:

1. extract any still-valid unique behavior;
2. reproduce it in the new session/shared-skill system;
3. verify parity or intentional replacement;
4. update references and scheduled consumers;
5. **delete the superseded session-facing skill from the default branch**;
6. freeze/retire its dedicated status state;
7. leave historical evidence in Git history or an explicitly historical archive, not in the active instruction surface.

---

## 3. Session-role selection at startup

### 3.1 Default behavior

On a brand-new Claude Code session in this repository, if the user has **not already specified a role or explicitly said the work is task-specific**, Claude must ask which operating role to use before beginning substantive repo work.

The implementation should determine the most reliable place for this bootstrap instruction. `CLAUDE.md` is the expected candidate because Claude Code loads it automatically, but this specification deliberately defines the **behavioral requirement**, not the exact implementation location. If Claude Code provides a better supported project-level bootstrap mechanism, use it and leave `CLAUDE.md` as the visible index/pointer.

Suggested prompt:

```text
Which session role should I follow?

1. Experiment Control Tower — read-only experiment status, gates, and recommendations
2. Evo Control Tower — read-only evolutionary fleet health and research activity
3. Evo Ticket Workshop — triage and implement fleet capability requests
4. Platform Change Review — shared-system changes and experiment impact analysis
5. Research Lab — new ideas, probes, and evidence-backed experiment creation
6. Legacy Migration — temporary role for bringing old experiments into Experiment OS
7. Live Ops — operational incidents, real-money safety, and live-system health
8. Task-specific — no standing role; follow the explicit task while obeying Experiment OS
```

### 3.2 Do not ask when the role is already explicit

Examples that should proceed immediately:

- "Run as Experiment Control Tower."
- "Start the Evo Ticket Workshop."
- "This is a Platform Change Review for the fee model."
- "Task-specific: implement PR 2 from the Experiment OS spec."

### 3.3 Role remains sticky for the session

Once selected, the role applies until the user explicitly changes it.

Do not ask for the role again on every turn.

### 3.4 Crossing a role boundary must be visible

Read-only roles may investigate and recommend but must not silently turn into implementation sessions.

Example:

- Experiment Control Tower finds a broken fill model.
- It may diagnose and recommend a Platform Change Review.
- It must not quietly start modifying the fill engine under the Control Tower identity.

The user may explicitly switch roles or choose task-specific work in the same conversation.

### 3.5 Task-specific is an escape hatch, not an eighth standing role

Many sessions begin with a concrete engineering task: fix a parser, implement an Experiment OS PR, update a migration, or review a PR. These do not need to be forced into one of the seven recurring roles.

A task-specific session still must:

- obey Experiment OS lifecycle rules;
- detect whether its requested change is a Platform Revision candidate;
- preserve live-money safeguards;
- use current shared skills rather than retired workflows;
- avoid inventing independent status state.

---

## 4. Standard session file layout

Target structure:

```text
.claude/
  sessions/
    README.md
    experiment-control-tower.md
    evo-control-tower.md
    evo-ticket-workshop.md
    platform-change-review.md
    research-lab.md
    experiment-migration.md
    live-ops.md

  skills/
    ...shared reusable skills only...
```

`README.md` is the human-readable menu and routing index.

Each session file should use the same schema.

### Required sections

```text
ROLE
PURPOSE
DEFAULT MODE / PERMISSIONS

LOAD FIRST
- canonical docs/specs
- Experiment OS read models
- shared skills

STARTUP ROUTINE
- first queries/checks
- integrity checks
- platform snapshot check

STANDARD WORKFLOW

STANDARD OUTPUT

HANDOFF / ROLE-CHANGE RULES

MAY MODIFY

MUST NOT MODIFY

SHARED SKILLS

CLEANUP / SESSION END
```

Role files should be short enough to load routinely. Detailed reusable mechanics belong in shared skills or code.

---

## 5. Universal session identity header

Every standing-role session should make its identity obvious in its first substantive report.

Recommended compact header:

```text
SESSION: Experiment Control Tower
MODE: READ ONLY
EXPERIMENT OS: <schema/version>
PLATFORM SNAPSHOT: <id/version>
AS OF: <timestamp America/Chicago>
```

For write-capable roles:

```text
SESSION: Platform Change Review
MODE: WRITE — platform-impact protocol required
...
```

The header is primarily for humans running many sessions in parallel. A user should be able to scroll into an old Claude window and immediately know what that session is responsible for.

---

# CORE SESSION ROLES

## 6. Experiment Control Tower

### Purpose

The canonical read-only operating view of every open experiment across the lifecycle.

Primary question:

> What experiments are running, where are they in the state machine, what does the current evidence say, and what requires attention?

### Default mode

**READ ONLY.**

It reports, diagnoses, and recommends. It does not change experiment state, code, config, risk, or live settings.

### Startup routine

Always begin by reading the Experiment OS rather than reconstructing status from strategy tags or old status files.

Request/report all non-terminal experiments grouped by state:

- IDEA — where appropriate if already registered;
- PROBE;
- PAPER;
- LIVE_CANARY;
- PRODUCTION;
- PAUSED.

RETIRED is summarized only when recently changed or specifically requested.

For each active experiment retrieve, where available:

- experiment + version identity;
- origin: operator / idea-model / evo / external;
- current state;
- current epoch;
- platform snapshot;
- treatment and control arms;
- deployment(s);
- primary gate status;
- minimum-N progress;
- current primary metric(s);
- absolute and relative/control performance where relevant;
- data/integrity/platform blocks;
- open positions / capital at risk for live states;
- most recent transition and evidence timestamp.

### Standard report structure

```text
SESSION HEADER

SYSTEM / INTEGRITY ANOMALIES

PROBE
  experiment | progress | gate | result | waiting on

PAPER
  experiment | arms/control | n | primary metric | gate | status

LIVE_CANARY
  experiment | live deployment | twin | n | realized | parity | gate | exposure

PRODUCTION
  experiment | realized period P&L | risk/exposure | health | alerts

PAUSED / BLOCKED
  experiment | reason | required action

READY / DUE
  promotion candidates
  kill/retire candidates
  gates newly cleared
  stale/inconclusive experiments

RECOMMENDED NEXT ACTIONS
```

### Required behavior

- Group by Experiment OS state, not by historical strategy family.
- Honor pre-registered gates exactly.
- Show `HOLD` when sample is insufficient.
- Surface `BLOCKED_DATA`, `BLOCKED_INTEGRITY`, and `BLOCKED_PLATFORM` before P&L interpretation.
- Never treat a good raw P&L number as promotion when the gate has not passed.
- Never blend epochs that Experiment OS declares non-poolable.
- If a platform snapshot changed, state the impact disposition.
- For LIVE_CANARY, always surface paper-twin/parity health and current real-money exposure.

### Shared skills / services

The implementation should expose a generic Experiment OS status/read skill or query command. The Control Tower may invoke specialist analysis when a gate explicitly depends on it, but those specialists must not become alternate state registries.

### Handoffs

- broken runtime / stuck orders / real-money anomaly → **Live Ops**;
- fee/fill/taxonomy/data-semantics change → **Platform Change Review**;
- new hypothesis or follow-up experiment → **Research Lab**;
- missing legacy mapping → **Legacy Migration** during transition;
- evo health issue → **Evo Control Tower**.

---

## 7. Evo Control Tower

### Purpose

Read-only health and behavior review of the evolutionary research system itself.

Primary question:

> Is the evolutionary fleet healthy, learning, using its capabilities, and producing useful research without getting stuck or violating its constraints?

This is intentionally separate from Experiment Control Tower.

Experiment Control Tower owns detailed lifecycle/performance reporting for experiments regardless of origin. Evo Control Tower owns the **research organism** that creates and studies some of those experiments.

### Default mode

**READ ONLY.**

### Startup routine

Retrieve at minimum:

- current cohort id, age, and boundary;
- active / suspended / retired agent counts;
- agent heartbeat freshness;
- failed/recovery heartbeats;
- LLM/token/tool/sandbox budget utilization;
- research budget saturation;
- current agent strategies and strategy activation/deactivation state;
- sandbox/backtest activity by dataset;
- paper participation and exposure where applicable;
- listeners and listener firing health;
- fitness distribution and ranking health;
- controls/benchmark arms;
- reproduction / child / wildcard behavior at last cohort boundary;
- current ticket counts by status/category/supporters;
- data source health relevant to evo;
- recent experiment proposals emitted into Experiment OS;
- graveyard/repeated-idea integrity warnings;
- audit/integrity violations.

### Standard output

```text
SESSION HEADER

FLEET HEALTH
COHORT
AGENTS
BUDGETS
RESEARCH / DATASET USE
FITNESS / CONTROLS
EXPERIMENTS ORIGINATED BY EVO
TICKETS SUMMARY
INTEGRITY / STUCK-BEHAVIOR WARNINGS
NEXT ACTIONS
```

Detailed experiment economics should link/defer to Experiment Control Tower rather than being recomputed independently.

### Handoffs

- actionable capability tickets → **Evo Ticket Workshop**;
- agent-created experiment performance → **Experiment Control Tower**;
- broken shared data/model semantics → **Platform Change Review**;
- runtime incident → **Live Ops** if it touches shared/live infrastructure.

---

## 8. Evo Ticket Workshop

### Purpose

Own the evolutionary fleet's capability-request queue from request to resolution.

Primary question:

> What is the fleet asking for that is still genuinely unresolved, and which request should we implement or reject next?

### Default mode

**WRITE CAPABLE.**

This role may investigate, implement, test, open PRs, and complete the ticket-resolution loop.

### Shared skill

The current `evo-ticket-triage` skill contains valuable logic and should be retained/refactored as a shared skill used by this role rather than exposed as a competing top-level session workflow.

### Startup routine

1. Read unresolved tickets and supporter counts.
2. Group likely duplicates.
3. Check shipped-capability registry / actual available actions before calling anything unresolved.
4. Relate each ticket to any blocked evo experiment or research line.
5. Classify each ticket:
   - already shipped;
   - worth building;
   - reject;
   - genuinely pending.
6. Rank actionable work by research value, number of supporters, urgency, experiment blocked, and cost.

### Standard output before implementation

```text
OPEN TICKETS: N
ACTIONABLE: N
PENDING: N
ALREADY SHIPPED / NEED AUTO-CLOSE FIX: N
REJECT RECOMMENDATIONS: N

TOP ACTIONABLE
1. ...
```

### Build loop

For an accepted ticket:

```text
ticket
→ inspect actual limitation
→ design capability
→ build + test
→ PR
→ add/update shipped-capability auto-resolution mapping
→ verify agents can actually use capability
→ resolve ticket with concrete result
```

Never leave "implemented in code" while the ticket remains logically open.

### Must not

- grant live-real-money capability to evo agents;
- let the ticket queue become the roadmap automatically;
- close tickets merely because they are old;
- close a ticket for a capability that does not actually exist.

---

## 9. Platform Change Review

### Purpose

The required write-capable role for changes to shared semantics that can alter experiment interpretation.

Primary question:

> If this shared system changes, which experiments become incomparable, recomputable, blocked, or invalid?

### Trigger examples

- fee model;
- fill model;
- paper engine semantics;
- live execution semantics;
- risk framework;
- settlement logic;
- taxonomy / universe classification;
- API payload interpretation;
- data source semantics;
- data schema meaning;
- metric/gate calculation semantics;
- timestamp/provenance behavior;
- order sizing semantics;
- shared market filtering.

### Default mode

**WRITE CAPABLE, but impact plan before implementation.**

### Startup routine

Before touching code:

1. identify the proposed semantic change;
2. locate the current Platform Revision component/version;
3. query Experiment OS for every active experiment that depends on it;
4. determine whether historical values can be reconstructed exactly;
5. produce an impact plan with one disposition per affected experiment:
   - `NO_ACTION`;
   - `RECOMPUTE`;
   - `NEW_EPOCH`;
   - `NEW_EXPERIMENT_VERSION`;
   - `PAUSE`;
   - `RETIRE`;
6. register the intended Platform Revision / successor snapshot according to Experiment OS protocol;
7. only then implement.

### Standard pre-code output

```text
PLATFORM CHANGE: <component old → proposed>
SEMANTIC EFFECT: ...

AFFECTED EXPERIMENTS
experiment | current state | dependency | disposition | reason

HISTORICAL RECOMPUTATION
exact / approximate / impossible

CUTOVER PLAN
...
```

### Acceptance behavior

A shared change is not done because tests pass. It is done when:

- the new Platform Revision is registered;
- affected experiments have explicit dispositions;
- required new epochs/versions exist;
- recomputations are complete or explicitly pending;
- blocked experiments cannot accidentally advance;
- docs/read models identify the active snapshot.

---

## 10. Research Lab

### Purpose

Explore what to test next and carry promising ideas through idea screening and probes into properly registered Experiment OS objects.

Primary question:

> What new evidence-backed hypothesis is worth testing, given everything the system has already learned?

### Default mode

**WRITE CAPABLE for research/probe/paper setup. No autonomous live promotion.**

### Shared skills

The following are expected to remain as reusable capabilities, updated to use Experiment OS rather than legacy registries as the canonical state:

- `kalshi-idea-model`;
- `kalshi-probe-builder`;
- `kalshi-strategy`.

They are **skills**, not separate session roles.

### Startup routine

Read:

- current Experiment OS open experiments;
- recent verdicts;
- retired/graveyard families;
- idea-model base rates;
- available current datasets and freshness;
- current Platform Snapshot;
- evo-originated ideas/tickets where relevant;
- unresolved research gaps.

Before generating ideas, prevent duplication:

- do not propose an experiment already open;
- do not revive a killed family without a mechanically new premise and explicit reason;
- do not create variants simply because an existing experiment is still accumulating sample;
- prefer cheap probe/census validation before paper capital/time.

### Research lifecycle

```text
idea
→ screen against history/testability
→ pre-register probe
→ run probe
→ verdict
→ only PASS creates/advances the Experiment OS strategy path
→ paper according to Experiment OS
```

### Must not

- bypass pre-registration;
- backfill a gate after results;
- silently alter an active experiment to incorporate a new idea;
- promote to real money without operator-controlled transition.

---

# TEMPORARY / OPERATIONAL ROLES

## 11. Legacy Migration

### Purpose

Temporary transition role for importing pre-Experiment-OS research and active books into the new canonical model without fabricating history.

### Default mode

**WRITE CAPABLE to migration tooling/data and documentation.**

### Responsibilities

For each legacy artifact:

1. find all available evidence;
2. identify experiment/thesis identity;
3. map known arms/controls;
4. map the historical state and current running state;
5. identify known boundaries/epochs;
6. map deployments/tags;
7. assign the migration integrity grade defined by Experiment OS;
8. preserve unknown fields as unknown;
9. import raw history without rewriting it;
10. verify Experiment OS reproduces the correct current interpretation.

### Completion / retirement condition

This role is intentionally temporary.

Once every active or decision-relevant legacy experiment has been migrated and strict Experiment OS enforcement is enabled:

- remove this role from the default startup picker;
- retain its migration code/docs only if needed for audit or disaster recovery;
- otherwise archive/delete the active session playbook so it cannot be mistaken for normal workflow.

---

## 12. Live Ops

### Purpose

Operational health, incidents, and real-money safety.

Primary question:

> Is the live system functioning correctly and is unexpected real-money exposure being created or left unmanaged?

### Default mode

**WRITE CAPABLE for operational safety/fixes, with existing real-money confirmations preserved.**

### Startup routine

Check:

- live worker health;
- current live deployments from Experiment OS;
- kill-switch/live-enabled state;
- current real-money positions and capital at risk;
- resting/stuck orders;
- recent order failures/rejections;
- drain/cancel health;
- data collector freshness needed by live deployments;
- API/parser anomalies;
- settlement/marking anomalies;
- live-paper twin existence and epoch alignment;
- Platform Snapshot alignment between expected and deployed configuration;
- recent deployment changes.

### Scope rule

Live Ops restores trustworthy operation and reduces unexpected exposure.

It does **not** decide that an experiment passed because the incident investigation exposed an attractive P&L slice. Scientific interpretation returns to Experiment Control Tower after operational integrity is restored.

### Emergency posture

Existing safety rules remain: actions that only reduce exposure may be permitted under kill-switch semantics where designed, while any action that expands real-money exposure requires the repository's explicit operator confirmation path.

---

# 13. Session handoff protocol

Roles should hand off work by reference to canonical objects, not prose copied between chats.

A good handoff contains:

```text
FROM: Experiment Control Tower
TO: Platform Change Review
OBJECT: Experiment <id/version> / Platform Component <id>
FINDING: ...
EVIDENCE: query/read-model reference
REQUIRED NEXT ACTION: ...
DO NOT CHANGE: ...
```

The receiving session re-reads canonical state. It does not trust the previous session's cached numbers as current.

---

# 14. Parallel-session concurrency rules

The session system exists partly because many Claude sessions run simultaneously. Concurrency must therefore be explicit.

### 14.1 Chat is never durable state

A decision that matters to another session must land in Experiment OS, a Platform Revision, a ticket, a PR, an audit event, or durable research documentation.

### 14.2 Every ops request uses a unique id

Never rely on shared `ops/result.txt` as the durable result when multiple sessions may be active. Use the existing per-request result files.

### 14.3 Read-only sessions do not maintain shadow state branches

Experiment Control Tower and Evo Control Tower should query current canonical state each run. Do not recreate the pattern where each checker persists a second interpretation on a dedicated status branch.

### 14.4 Write sessions use isolated feature branches

Do not have two unrelated Claude sessions mutating one feature branch.

Before a write session starts implementation:

- refresh default branch state;
- inspect open PRs touching the same subsystem;
- identify whether the requested change is already in flight;
- create/use a task-specific branch.

### 14.5 Material experiment changes are state-machine events

A code merge alone does not mean an experiment changed state. The canonical transition must be recorded explicitly.

### 14.6 Role identity does not grant stale authority

A Control Tower session left open for six hours must re-query before answering "where are we now?"

---

# 15. CLAUDE.md target state

The current `CLAUDE.md` has grown into a mixture of:

- repository bootstrap;
- ops-channel instructions;
- standing commands;
- historical strategy interpretations;
- specific book/gate narratives;
- live/paper procedures;
- research notes.

This was useful during rapid iteration, but it creates unnecessary context and stale-instruction risk for every new session.

The implementation of this specification should **shrink `CLAUDE.md` materially**.

Target responsibility of `CLAUDE.md`:

1. repository identity and north-star objective;
2. non-negotiable global safety/integrity rules;
3. Experiment OS is canonical;
4. session-role router / startup behavior;
5. pointer to `.claude/sessions/README.md`;
6. pointer to the ops runbook/mechanism;
7. pointer to Platform Change protocol;
8. pointer to current shared skills;
9. concise source-of-truth hierarchy.

It should not be the place where current experiment samples, current winning/losing books, hardcoded old gate reads, or historical mmsell/weather conclusions are maintained.

Detailed ops mechanics may remain in `CLAUDE.md` if that proves materially safer for Claude Code execution, but the implementing Claude should evaluate whether they belong in a dedicated `docs/` runbook with a short bootstrap pointer. The requirement is **one current authoritative instruction path**, not a predetermined file split.

---

# 16. Active skill cleanup and retirement matrix

This section is a starting inventory from the repository at specification time. The implementing Claude must verify the current default branch before deleting anything, because Experiment OS implementation may have changed these files in the meantime.

## 16.1 `full-update`

Current role: broad whole-bot status review using weather digest/PnL plus hardcoded strategy interpretations.

Disposition: **RETIRE after Experiment Control Tower reaches parity.**

Why:

- overlaps directly with Control Tower;
- contains historical strategy lists and assumptions that go stale;
- reconstructs experiment state outside Experiment OS.

Migration:

- preserve any useful north-star dollar reporting in Control Tower / Experiment OS portfolio view;
- preserve operational anomaly checks in Live Ops;
- delete `.claude/skills/full-update/` once replacement output is verified;
- remove `full update` references from active bootstrap docs.

## 16.2 `kalshi_Loop_checker`

Current role: old recurring paper/data status loop, persisting `STRATEGY_LOOP_STATUS.md` on `strategy-loop-status`.

Disposition: **RETIRE.**

Replaced by: Experiment Control Tower + canonical Experiment OS metrics.

Migration:

- extract any collector-health checks not represented elsewhere;
- stop writing a separate strategy-loop interpretation;
- delete the skill after replacement;
- freeze/retire its scheduled trigger/consumer;
- archive or import decision-relevant status history if needed, then retire the `strategy-loop-status` branch as active state.

## 16.3 `kalshi_loop_checker_phase_3`

Current role: newer/larger generation of the same loop, including live P&L, active experiment gates, idea-model queue, FREEZE rechecks, and family-specific tables.

Disposition: **RETIRE after feature extraction.**

This file is particularly important to mine before deletion because it contains later lessons not present in the older checker.

Map its still-valid behavior into:

- active experiment/gate sweep → Experiment Control Tower;
- live real-money health → Live Ops / Control Tower LIVE_CANARY section;
- gate-blocked ideas → Experiment OS state/query rather than suggestion-list memory;
- listing/testability triggers → Experiment OS gate/data dependency or Research Lab;
- collector freshness → shared health read model.

Do not keep both loop generations around once the replacement works.

## 16.4 `mm_check_1`

Current role: mmsell-specific standing status session with many hardcoded cohort, fill-model, exit-study, and anchor-set reads.

Disposition: **RETIRE as a top-level/session-facing skill after unique analysis is extracted.**

Keep:

- underlying read-only analysis scripts that are still scientifically useful;
- experiment-specific metrics/gates that Experiment OS intentionally references;
- historical boundary evidence in durable docs.

Remove:

- independent mmsell status branch as a competing canonical snapshot;
- top-level trigger language suggesting this is how to determine current experiment state;
- duplicate gate logic once gates are executable in Experiment OS.

If some mmsell diagnostics remain too specialized for generic metrics, convert them into a narrowly named **analysis skill** callable by Experiment Control Tower, not a separate status system.

## 16.5 `mmsell-seasonal-check`

Current role: scheduled capture-health and future-supply monitoring with its own status branch.

Disposition: **REVIEW / ABSORB, not blindly delete.**

The capability may remain useful, but it should not remain a parallel session identity.

Possible target:

- collector/history freshness → Live Ops/shared data-health service;
- future supply/testability trigger → Experiment OS dependency/gate or Research Lab watcher;
- regime study → specialist research skill invoked when due.

If a recurring automation remains justified, point it at canonical state and use a narrow watcher, not a shadow experiment-status branch.

## 16.6 `live-paper-parallel`

Current role: arm/audit a live book with paper twin.

Disposition: **KEEP FUNCTION, REFACTOR TO EXPERIMENT OS LIVE_CANARY.**

This contains current safety-critical knowledge.

Target changes:

- Live Canaries and twin deployments become first-class Experiment OS deployment/epoch objects;
- no parallel registration in prose-only book plans;
- parity output feeds canonical experiment metrics/integrity status;
- retuning creates the Experiment OS consequence specified by the Platform/Experiment rules;
- likely rename to a clearer shared skill such as `experiment-live-canary` once migration is complete.

Do not delete until the replacement is proven.

## 16.7 `bot-readable-strategy`

Current role: make operator strategy docs + live scoreboard visible to evo.

Disposition: **TRANSITIONAL — retire once Experiment OS → evo read path is canonical.**

The underlying requirement remains essential: evo needs premise + current evidence. But Experiment OS should become the structured channel rather than maintaining a special publication convention as the long-term architecture.

Do not remove until evo can read the equivalent experiment thesis/state/metrics directly.

## 16.8 `evo-ticket-triage`

Current role: fleet ticket queue procedure.

Disposition: **KEEP / REFACTOR AS SHARED SKILL.**

Primary consumer: Evo Ticket Workshop.

Reduce broad session-like trigger language once routing is enforced so a random new session does not accidentally become the ticket workshop without role selection.

## 16.9 `kalshi-idea-model`

Disposition: **KEEP AS SHARED RESEARCH SKILL**, updated to create/reference Experiment OS objects and current research history.

Primary consumer: Research Lab and task-specific research sessions.

## 16.10 `kalshi-probe-builder`

Disposition: **KEEP AS SHARED RESEARCH SKILL**, updated so probe status/verdict is recorded in Experiment OS rather than a parallel scorecard being the lifecycle source of truth.

## 16.11 `kalshi-strategy`

Disposition: **KEEP AS SHARED BUILD SKILL**, but refactor its lifecycle assumptions to the new state machine.

It should not independently define a second Phase 0–6 lifecycle once Experiment OS enforcement exists. It should implement the work required by the current Experiment OS transition/gate.

---

# 17. Status branch cleanup

Several legacy checkers persist state on dedicated branches. These branches made sense when chat and paper-trade rows were the only durable state available. Experiment OS should make them unnecessary.

Candidates include:

- `strategy-loop-status`;
- `mmsell-check-status`;
- `mmsell-seasonal-status`.

The implementing Claude must inventory actual current consumers before retirement.

Retirement protocol:

1. identify scheduled jobs/sessions/workflows that read or write the branch;
2. migrate decision-relevant current state into Experiment OS or canonical health state;
3. preserve any unique historical evidence that is worth keeping;
4. stop writers;
5. remove active bootstrap/skill references;
6. mark/freeze or delete the remote branch according to audit value;
7. verify a fresh Claude session does not depend on it.

`ops` is infrastructure and is **not** a status branch to retire.

`digest-archive` may remain a historical operational archive if it still has value; it must not be treated as current experiment state.

---

# 18. Historical documentation cleanup

Do not delete experiment history merely because a strategy is retired.

Differentiate:

### Durable historical evidence — KEEP

- thesis documents;
- probe results;
- postmortems;
- measured failure explanations;
- raw/captured data lineage;
- graveyard verdicts;
- important API/measurement discoveries.

### Active operating instructions — CLEAN UP

- old status loop skills;
- duplicate current-state summaries;
- old "standing commands" that claim to be authoritative when Experiment OS supersedes them;
- obsolete hardcoded current book lists;
- stale live-arm procedures after Experiment OS replacement;
- old bootstrap references.

Where a durable historical doc contains a section that sounds like current operating policy but has been superseded, either update that section to point to the current system or add a clear historical/superseded marker. Do not rewrite the historical result itself.

---

# 19. Session-specific shared skill map

Initial expected mapping:

| Session role | Typical shared skills / capabilities |
|---|---|
| Experiment Control Tower | Experiment OS status/read model; gate evaluator; portfolio/current P&L view; specialist experiment diagnostics only when referenced by gate |
| Evo Control Tower | evo digest/leaderboard/tree/health; Experiment OS read for evo-originated experiments |
| Evo Ticket Workshop | `evo-ticket-triage`; implementation/testing/publish workflow |
| Platform Change Review | Platform Revision impact analyzer; Experiment OS dependency query; migration/recompute utilities |
| Research Lab | `kalshi-idea-model`; `kalshi-probe-builder`; `kalshi-strategy`; Experiment OS create/transition proposal helpers |
| Legacy Migration | Experiment OS importer/auditor; legacy registry/history readers |
| Live Ops | live health/digest; order/drain diagnostics; live-canary parity; deployment/platform snapshot checks |

The implementation should prefer a few composable shared skills over role files that duplicate procedures.

---

# 20. New-session freshness rules

A new session must avoid stale cached conclusions.

### Standing-role startup

Every Control Tower / Live Ops report uses fresh DB/read-model data.

### Research startup

Research Lab reads current open experiments and recent verdicts before proposing work.

### Platform change startup

Platform Change Review queries current active dependencies before producing impact dispositions.

### Ticket startup

Ticket Workshop re-reads unresolved tickets and shipped capability state before acting.

### Evo startup

Evo Control Tower reads current cohort/heartbeats rather than relying on last session's report.

---

# 21. Permission model summary

| Role | Default permission |
|---|---|
| Experiment Control Tower | read-only |
| Evo Control Tower | read-only |
| Evo Ticket Workshop | code/data-model writes needed to fulfill tickets; no live-money capability grants |
| Platform Change Review | shared-system writes after impact plan |
| Research Lab | research/probe/paper implementation; no autonomous live promotion |
| Legacy Migration | migration writes; preserve raw history |
| Live Ops | operational safety/fix writes; real-money expansion remains explicitly confirmed |
| Task-specific | as required by task, bounded by repo safety + Experiment OS |

Session files must state these permissions near the top.

---

# 22. Standard output consistency

The purpose of roles is partly to make repeated sessions predictable.

Implementing Claude should make output formats stable but not excessively rigid.

At minimum:

- Control Tower always groups experiments by state;
- Evo Control Tower always separates fleet health from experiment economics;
- Ticket Workshop always leads with unresolved actionable requests;
- Platform Change Review always prints affected experiments before coding;
- Research Lab always shows why a new idea is not a duplicate/revival of killed work;
- Live Ops always leads with real-money/integrity anomalies;
- all standing roles include the session identity header.

---

# 23. Implementation plan

This specification should be implemented as a dedicated cleanup/refactor PR or small sequence if needed. Prefer completing cleanup in the same logical migration so the repository does not sit for long with both systems advertised as current.

## Phase A — inventory current state

Before editing:

- read the latest Experiment OS implementation status;
- inventory every `.claude/skills/*` entry;
- search every reference to the legacy skill names;
- inspect `CLAUDE.md` for embedded old workflows;
- inspect scheduled workflows/tasks documented in repo that invoke the old loops;
- inspect dedicated status branches and their consumers;
- inspect ops allowlisted scripts so useful diagnostics are not accidentally removed with session instructions.

Produce a concrete keep/refactor/delete matrix before deletion.

## Phase B — add session routing and role files

Create `.claude/sessions/` with the seven role files and README.

Implement startup role selection in the mechanism that reliably affects new Claude Code sessions.

If this is `CLAUDE.md`, keep the prompt/router concise.

## Phase C — build canonical Control Tower reads

Experiment Control Tower must query Experiment OS and report by lifecycle state.

Evo Control Tower must query current evo health.

Do not simply wrap the old status Markdown.

## Phase D — refactor shared skills

Update the retained skills to consume/produce Experiment OS state where required.

Especially:

- `kalshi-strategy` lifecycle;
- probe/idea handoffs;
- live-canary/twin handling;
- evo-readable experiment path;
- ticket workshop routing.

## Phase E — parity and retirement

For every old session-facing skill:

- list its unique responsibilities;
- show where each responsibility now lives;
- run one comparison where practical;
- migrate/freeze current durable state;
- remove old triggers/references;
- delete superseded skill directories.

Do not leave two current instructions for the same job.

## Phase F — simplify `CLAUDE.md`

Remove stale current-state narratives and duplicate procedures after the replacement is live.

The final `CLAUDE.md` should be a router/invariant index, not a historical operating diary.

## Phase G — cleanup guards

Add a lightweight regression check so future contributors do not accidentally reintroduce the retired system.

Possible checks:

- required session files exist;
- bootstrap points to the session router;
- deleted old skill paths do not exist;
- active docs do not reference retired status skills as current;
- no new session status file claims independent lifecycle authority;
- all retained Experiment OS-aware skills reference the canonical lifecycle.

A grep/static test is sufficient if that is the simplest reliable mechanism.

---

# 24. Acceptance criteria

The implementation is complete when all are true:

1. A fresh Claude Code session with no role specified asks which session role to follow.
2. If a role is explicitly supplied in the opening prompt, Claude does not ask redundantly.
3. The seven role playbooks exist under one discoverable directory.
4. Role files state permissions, startup routine, shared skills, standard output, and handoff rules.
5. Experiment Control Tower reports all active experiments from Experiment OS grouped by lifecycle state.
6. Evo Control Tower reports current fleet health independently of detailed experiment economics.
7. Evo Ticket Workshop uses the ticket-resolution loop and can build/close requests correctly.
8. Platform Change Review requires an experiment-impact plan before shared semantic changes.
9. Research Lab uses current Experiment OS/history and does not duplicate open or killed work casually.
10. Legacy Migration has a defined end-of-life condition.
11. Live Ops leads with live integrity and real-money exposure.
12. `CLAUDE.md` no longer carries stale strategy standings that canonical state can answer.
13. Superseded status-loop/session skills have been removed from the active `.claude/skills` surface after their unique behavior is migrated.
14. Dedicated legacy status branches are no longer active sources of truth or active writers.
15. Useful underlying analysis scripts are preserved where still needed.
16. Historical evidence is preserved rather than rewritten.
17. A static/regression check prevents obvious reintroduction of retired session workflows.
18. Current tests remain green and no trading behavior changes merely as a side effect of instruction cleanup.

---

# 25. Implementation warnings

### Do not delete first and reconstruct later

Several legacy skills contain hard-earned production lessons. Extract them before removal.

### Do not keep deprecated auto-trigger skills "for reference"

Git history is the reference. An active skill directory is an instruction surface.

### Do not make role files another source of strategy truth

A role should query Experiment OS, not hardcode the current FREEZE/A5/mmsell state.

### Do not create a second experiment lifecycle in `kalshi-strategy`

Experiment OS owns lifecycle semantics.

### Do not turn every specialist analysis into a session role

Session roles are recurring operator modes. A fill model study or seasonal supply script is a capability invoked by a role.

### Do not force all normal coding into a standing role

Task-specific work remains supported.

### Do not remove live safety knowledge during cleanup

`live-paper-parallel` contains safety-critical behavior and should be migrated into Experiment OS live-canary procedures before any deletion/rename.

---

# 26. Intended end state

A user opens a new Claude Code session.

Claude asks:

```text
Which session role should I follow?
```

The user selects `Experiment Control Tower`.

Claude loads a short role playbook, queries canonical Experiment OS state, and produces the same kind of lifecycle report regardless of which chat window is being used.

Another window runs `Evo Ticket Workshop`; another runs a task-specific implementation; another is `Live Ops` during an incident. They may all work simultaneously, but they share the same canonical experiment/platform/ticket state and do not maintain competing status interpretations.

The repository contains one current instruction path. Old checker generations are gone from the active skill surface. Historical lessons remain preserved as evidence, while current operations are driven by Experiment OS plus role-specific playbooks.

That is the operating model this specification requires.
