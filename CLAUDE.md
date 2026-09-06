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
- Adopted version: v0.12
- Last compatibility check: v0.12 on 2026-09-06
- Operating mode: `solo` — no independent actor exists, so significant work is **owner-accepted
  at merge**, never called reviewed. An agent never writes or infers one (`DEC-011`).
- Active-work limit: **4**, counting `Active` rows only — `Blocked` (which requires a named
  external unblocker) and `Paused` do not consume it (`DEC-011`).

Before substantial design work, compare the adopted version against `VERSION.md` in the
canonical repository and act on the delta (`framework/FRAMEWORK_SYNC.md`). Project memory lives
in `docs/`: `PROJECT_MODEL.md` (how it works today), `DECISIONS.md` (why), `workstreams/ACTIVE.md`
(in flight, plus a `## Parked` list nothing schedules and no agent may start from). The PR body is the
handoff, never chat. **Discovery does not create work** — a finding outside the mission is `FIX
NOW`, `PARK`, `DISCARD` or an `OWNER DECISION`, never a new workstream. **Full protocol:
`docs/BUILD_OS.md`** — this file is a router, not the protocol.

### Project-specific: additions to Build OS

- **Ordering.** Session role **first** (above), then the compatibility check. The role decides
  what a session may write at all; the check only decides which protocol, and grants no write.
- **The authority boundary is a hard rule.** Experiment OS stays canonical for experiments,
  Versions, epochs, deployments, arms, gates, platform revisions, impact actions, enforcement
  and XOS issues. A workstream restating any of those is malformed — link.
- **A workstream authorizes nothing, and `SHIP` reports only the development gate.** Phase and
  status are development state; only Experiment OS's services register, arm, promote, pause or
  retire. `SHIP`'s `Next action` is the merge — an Experiment OS action that follows is a
  **guard for the operator**, never a Build OS next step and never something the PR authorized.
- **Two owner-facing surfaces, one boundary.** The identity header opens a *session* (who is
  speaking); the Owner Result closes a *piece of work* on the PR (where it landed). Never in
  the same block; the header never carries a result, the result never a session state.
- **A safety floor on proportionality.** Nothing touching real-money exposure, the arming path,
  a live safeguard, a gate or the ops channel is ever **simple**, whatever its diff size.
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
git add -A && git commit -q -m "ops: control tower"
# `ops` is SHARED — several sessions push to it, so a bare push loses races.
# Always retry; a rebase is safe (your request commit touches one file):
for i in 1 2 3 4 5 6; do git fetch origin ops -q
  git rebase origin/ops -q 2>/dev/null || git rebase --abort 2>/dev/null
  git push -q origin ops 2>/dev/null && break; sleep $((i * 3)); done
# Then poll for YOUR OWN per-id result (never the shared pointer). Budget
# MINUTES, not seconds: Actions queues runs behind other sessions', and a
# missing file means "not yet", not "lost" — check `git log origin/ops` first.
for i in $(seq 1 20); do sleep 15; git fetch origin ops -q
  git show FETCH_HEAD:ops/results/ct-1.txt 2>/dev/null && break; done
```

**Always set a unique `id`.** Request types: `xos` (canonical Experiment OS read
CLI — `control-tower`, `list`, `show`, `scoreboard`, `enforcement`, `readiness`,
`platform`, `tag`), `db` (one read-only statement), `logs`, `script`
(allowlisted analyses), `env` (allowlisted vars; setting redeploys the worker),
`noop`. Reset to `{"type":"noop"}` when finished. `ops/results/` keeps only the
newest 80 files — read yours promptly and persist what matters elsewhere.

**The `EXPERIMENT_OS_*_COMMAND` transports are single-slot**, consumed at the
worker's next boot. Send a whole ticket workflow as ONE array of envelopes (one
boot, not one per command), and expect a `REFUSED` verdict if another session's
envelope is still unconsumed — that guard is protecting their work, not
malfunctioning. Details: `docs/OPS_RUNBOOK.md`, `docs/EXPERIMENT_OS_ISSUES.md`.

**Never force-refresh `ops`.** The runner already checks the default branch out
separately and executes only that code, so a merge to the default branch is live
on the next request — there is nothing to refresh. `refs/heads/ops` is protected
against force pushes and deletion; ordinary request and result commits are
unaffected. A genuine workflow-file change follows the deliberate maintenance
procedure in `docs/OPS_RUNBOOK.md` ("Protecting the `ops` branch"). Never merge
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
- Evo historical search (agent capability, replay-proven) → `docs/EVO_SEARCH_CAPABILITY.md`
- Research history → `docs/RESEARCH_JOURNAL.md`, thesis docs in `docs/`
