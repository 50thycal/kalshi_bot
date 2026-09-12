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
OS (below) is canonical for the **development workflow**. A workstream **links** to XOS
objects and never copies a standing, a gate read or a P&L number. Chat is neither.

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
- Problems are durable state too: an anomaly, defect, incident or open question
  belongs in an **Experiment OS issue** (`docs/EXPERIMENT_OS_ISSUES.md`), routed
  to the role that owns it (there is no fixer role). A ticket never changes a
  lifecycle state, gate, verdict, epoch, Version, Revision or exposure.
- Read it: `docs/EXPERIMENT_OS_FOUNDATION.md`, `_METRICS`, `_ENFORCEMENT`,
  `_PLATFORM_IMPACT`, `_GATE_RESULTS`, `_ISSUES`, `_MIGRATION`; spec in
  `docs/EXPERIMENT_OPERATING_SYSTEM_SPEC.md`.

## Session role — establish this first

**Infer the role from the request when it is unambiguous** (a paper tape, probe
or thesis → Research Lab; an incident, collector or real money → Live Ops; a
fee/fill/taxonomy/metric semantic → Platform Change Review; "what is running" →
Experiment Control Tower), state it in the identity header, and proceed. Only
when the request is genuinely ambiguous **ask before substantive repo work**:

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
AS OF: …`). A read-only role that finds a needed write **recommends the owning
role**; it does not quietly become a write session. Menu and handoff format:
`.claude/sessions/README.md`.

## Standing authorizations and the closing brief (`DEC-012`)

**The request is the approval for every step inside its scope.** A paper-scope
request (a new paper tape, a probe, a paper-only env append, the session's own
paper PR merge) runs **end to end off one message** when the criteria hold —
`xos package-preflight` computes them — and reports once. A session asks only at
a **hard stop** (`ARM_CANARY`, `LIVE_STRATEGIES`/`LIVE_ENABLED`/`KILL_SWITCH`,
anything expanding real-money exposure or weakening a safeguard, a Platform
Revision activation, the `ops` workflow file) or when a criterion fails, which
is a `DECISION`, never a workaround. Tiers, chain and rules:
`docs/STANDING_AUTHORIZATIONS.md`.

**Close long sessions with the brief, not a narrative**: WHAT HAPPENED / GOAL /
BLOCKERS / DECISIONS / NEXT STEPS, plain words, under 120 words, links after
(`/session-brief`). Detail lives in the PR, the workstream and Experiment OS.

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

Session role **first**, then the compatibility check (the check grants no write). The
authority boundary is a hard rule: Experiment OS stays canonical for every XOS object — a
workstream **links**, never restates. A workstream authorizes nothing and `SHIP` reports only
the development gate; an XOS action that follows a merge is a **guard for the operator**.
The identity header (who is speaking) and the Owner Result on the PR (where work landed)
never share a block. Nothing touching real-money exposure, the arming path, a live
safeguard, a gate or the ops channel is ever **simple**. No transcripts — persist
conclusions, decisions and open questions, never logs. Full text: `docs/BUILD_OS.md`.

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
worker's next boot. Send a whole workflow as ONE array of envelopes, and expect a
`REFUSED` verdict if another session's envelope is still unconsumed — that guard
is protecting their work. **Never force-refresh `ops`**: the runner executes
default-branch code, so a merge is live on the next request; `refs/heads/ops` is
protected, a workflow-file change follows the maintenance procedure, and `ops` is
never merged into the default branch. Full mechanism: **`docs/OPS_RUNBOOK.md`**.

## Pointers

- Session roles → `.claude/sessions/README.md`
- Active design/build board → `docs/workstreams/ACTIVE.md`
- How the system works today → `docs/PROJECT_MODEL.md`
- Why it works this way → `docs/DECISIONS.md`
- Development protocol + templates → `docs/BUILD_OS.md`, `docs/templates/`
- Ops + standing analyses → `docs/OPS_RUNBOOK.md`
- Standing authorizations + closing brief → `docs/STANDING_AUTHORIZATIONS.md`
- Platform change protocol → `docs/EXPERIMENT_OS_PLATFORM_IMPACT.md`
- Investigation / issue workflow → `docs/EXPERIMENT_OS_ISSUES.md`
- Shared skills → `.claude/skills/` (process: `finite-work-handoff`, `session-brief`; research:
  `kalshi-idea-model`, `kalshi-probe-builder`, `kalshi-strategy`; evo: `evo-ticket-triage`;
  live canary: `live-paper-parallel`; evo readability: `bot-readable-strategy`)
- Evo fleet → `docs/EVOLUTIONARY_AGENT_SYSTEM.md`, `docs/EVO_RUNBOOK.md`, `docs/EVO_SEARCH_CAPABILITY.md`
- Research history → `docs/RESEARCH_JOURNAL.md`, thesis docs in `docs/`
