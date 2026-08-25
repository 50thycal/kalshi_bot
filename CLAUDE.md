# CLAUDE.md — bootstrap and router

Kalshi trading bot (market scanner + paper/live trading) on **Railway** with
**Postgres**. Architecture: `README.md`. Schema: `kalshi_bot/models.py`.

This file is a **router and index**, not an operating diary. It deliberately
does not carry current experiment standings, per-book conclusions, or gate
reads — Experiment OS answers those, live, and anything written here would be
stale within a day.

## North star

**$100/month in realized profit** across all strategies. Not win rate, not book
count, not research volume. Research proving a book is −EV is still a win — it
tells us what to stop trading.

## Source of truth, in order

1. **Experiment OS structured state** — experiments, versions, arms, epochs,
   deployments, gates + results, platform snapshots, integrity events,
   enforcement state, platform-impact actions. **Canonical.**
2. Current Platform Revision / Snapshot state.
3. Durable research evidence — thesis docs, probe results, postmortems,
   graveyard verdicts.
4. Role playbooks in `.claude/sessions/`.
5. Shared skills in `.claude/skills/`.
6. This file.

Historical Markdown, old status branches and prior chat are **not authoritative**
once Experiment OS can answer the same question. `docs/BOOK_REGISTRY.md` is
historical research documentation, **not** a lifecycle database.

**Two ledgers, one boundary (`DEC-001`).** The list above is *experiment truth*. Build
OS (below) is canonical for the **development workflow** — architecture, decisions,
active design/build work, PR handoffs, reviews. A workstream **links** to XOS objects
and never copies a standing, a gate read or a P&L number. Chat is neither.

## Experiment OS is live

Enforcement is **`NEW_ONLY`** in production since **2026-08-16T14:34:42.892897Z**
(cutover `prod-new-only-20260816`). Consequences that bind every session:

- New experimental activity **must** originate in Experiment OS. A tag that is
  not registered to an active deployment arm **cannot trade** — it is refused at
  the write path, not warned about.
- Existing books are **grandfathered**: they continue, but may not silently gain
  arms, change rules/universe/risk, or change stage outside the system. A real
  change is a new epoch (changed world), new version (changed question), or new
  experiment (new question).
- Lifecycle moves are recorded transitions, never a side effect of merging code.
- Gate verdicts are **recorded**, by the designated evaluator, on a bounded
  cadence. Automatic evaluation is allowed; automatic promotion never is. A dry
  run (including the Control Tower's) authorizes nothing.
- Problems are durable state too. An anomaly, suspected defect, incident,
  scientific question or shared-platform problem belongs in an **Experiment OS
  issue** (`docs/EXPERIMENT_OS_ISSUES.md`), not in prose. A ticket routes work to
  the existing role that owns the problem — there is no fixer role — and it never
  changes a lifecycle state, gate, verdict, epoch, Version, Platform Revision or
  exposure as a side effect.
- Read it: `docs/EXPERIMENT_OS_FOUNDATION.md`, `_METRICS`, `_ENFORCEMENT`,
  `_PLATFORM_IMPACT`, `_GATE_RESULTS`, `_ISSUES`, `_MIGRATION`; spec in
  `docs/EXPERIMENT_OPERATING_SYSTEM_SPEC.md`.

## Session role — establish this first

If the opening message has not named a role (or said the work is
task-specific), **ask before substantive repo work**:

```
Which session role should I follow?
1. Experiment Control Tower   5. Research Lab
2. Evo Control Tower          6. Legacy Migration
3. Evo Ticket Workshop        7. Live Ops
4. Platform Change Review     8. Task-specific
```

Then read `.claude/sessions/<role>.md` and follow it. **The role is sticky** —
never ask twice in one session. Standing roles open their first substantive
report with the identity header (`SESSION: … / MODE: … / ENFORCEMENT: … /
AS OF: …`) so a user with many windows open knows what each one owns. A
read-only role that finds a needed write **recommends the owning role**; it does
not quietly become a write session. Menu and handoff format:
`.claude/sessions/README.md`.

## Build OS

- Canonical framework: 50thycal/build-os
- Adopted version: v0.4
- Last compatibility check: v0.4 on 2026-08-24

Before substantial design or architectural work, compare the adopted version against
`VERSION.md` in the canonical repository and act on the delta (`framework/FRAMEWORK_SYNC.md`).
Project memory lives in `docs/`: `PROJECT_MODEL.md` (how it works today), `DECISIONS.md`
(why), `workstreams/ACTIVE.md` (what is in flight). The PR body is the handoff, never chat.
**Full development protocol: `docs/BUILD_OS.md`** — kept there, not here, because this file
is a router (see the length invariant in `tests/test_session_system.py`).

### Project-specific: additions to Build OS

- **Ordering.** Establish the session role **first** (above), then run the compatibility
  check before substantive design/build work. The role decides what a session may write at
  all; the check only decides which protocol it writes under, and grants no write.
- **The authority boundary is a hard rule.** Experiment OS stays canonical for experiments,
  Versions, epochs, deployments, arms, gates, platform revisions, impact actions,
  enforcement and XOS issues. A workstream restating any of those is malformed — link.
- **A workstream authorizes nothing.** Phase and status are development state; only
  Experiment OS's services register, arm, promote, pause or retire.
- **No transcripts.** Persist conclusions, models, decisions, open questions — never logs.

## Universal safety invariants

- **Real money.** Actions that expand real-money exposure need explicit operator
  confirmation. Reducing exposure follows existing kill-switch semantics.
- **Never weaken a live safeguard to make a task easier.**
- A live canary arms **only** through `service.arm_live_canary` — fresh tags with
  no inherited paper state, a twin at the same instant, a pre-registered risk
  envelope. (The 2026-08-15 Lmmsell failure is why this is structural.)
- **Gates decide promotions, not P&L.** A good number with a failing or
  un-evaluated gate is not a promotion. `HOLD` on thin sample is correct.
- Only a **recorded evaluator** PASS authorizes a transition; a hand-written or
  stale PASS never does.
- Never pool evidence across epochs Experiment OS declares non-poolable, and
  never re-interpret a pre-registered gate after seeing results.
- The ops channel is **read-only** against Postgres by design. Do not add a
  writable path; the worker is the only writer.
- Chat is never durable state.

## Ops channel — the minimal recipe

Railway and Postgres are unreachable from the Claude sandbox, so work is driven
through GitHub Actions by pushing a request to the **`ops` branch**:

```bash
git fetch origin ops && git worktree add /tmp/ops ops   # once
cd /tmp/ops && git fetch origin ops -q && git reset --hard -q origin/ops
echo '{"type":"xos","command":"control-tower","id":"ct-1"}' > ops/request.json
git add -A && git commit -q -m "ops: control tower" && git push -q origin ops
# ~30-90s, then read YOUR OWN result (a concurrent session may overwrite the
# shared pointer, never your per-id file):
git fetch origin ops && git show FETCH_HEAD:ops/results/ct-1.txt
```

**Always set a unique `id`.** Request types: `xos` (canonical Experiment OS read
CLI — `control-tower`, `list`, `show`, `scoreboard`, `enforcement`, `readiness`,
`platform`, `tag`), `db` (one read-only statement), `logs`, `script`
(allowlisted analyses), `env` (allowlisted vars; setting redeploys the worker),
`noop`. Reset to `{"type":"noop"}` when finished.

If `scripts/` or the runner change on the default branch, refresh `ops` from it:
`git checkout -B ops origin/<default> && git push -f origin ops`. Never merge
`ops` into the default branch.

Full mechanism, standing analysis commands and gotchas: **`docs/OPS_RUNBOOK.md`**.

## Pointers

- Session roles → `.claude/sessions/README.md`
- Active design/build board → `docs/workstreams/ACTIVE.md`
- How the system works today → `docs/PROJECT_MODEL.md`
- Why it works this way → `docs/DECISIONS.md`
- Development protocol + templates → `docs/BUILD_OS.md`, `docs/templates/`
- Ops + standing analyses → `docs/OPS_RUNBOOK.md`
- Platform change protocol → `docs/EXPERIMENT_OS_PLATFORM_IMPACT.md`
- Investigation / issue workflow → `docs/EXPERIMENT_OS_ISSUES.md`
- Shared skills → `.claude/skills/` (research: `kalshi-idea-model`,
  `kalshi-probe-builder`, `kalshi-strategy`; evo: `evo-ticket-triage`;
  live canary: `live-paper-parallel`; evo readability: `bot-readable-strategy`)
- Evo agent fleet → `docs/EVOLUTIONARY_AGENT_SYSTEM.md`, `docs/EVO_RUNBOOK.md`
- Evo population layer (genome search, replay-proven) → `docs/EVO_POPULATION_FOUNDATION.md`
- Research history → `docs/RESEARCH_JOURNAL.md`, thesis docs in `docs/`
