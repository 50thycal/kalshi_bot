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
- Read it: `docs/EXPERIMENT_OS_FOUNDATION.md`, `_METRICS`, `_ENFORCEMENT`,
  `_PLATFORM_IMPACT`, `_MIGRATION`; spec in
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
- Ops + standing analyses → `docs/OPS_RUNBOOK.md`
- Platform change protocol → `docs/EXPERIMENT_OS_PLATFORM_IMPACT.md`
- Shared skills → `.claude/skills/` (research: `kalshi-idea-model`,
  `kalshi-probe-builder`, `kalshi-strategy`; evo: `evo-ticket-triage`;
  live canary: `live-paper-parallel`; evo readability: `bot-readable-strategy`)
- Evo system → `docs/EVOLUTIONARY_AGENT_SYSTEM.md`, `docs/EVO_RUNBOOK.md`
- Research history → `docs/RESEARCH_JOURNAL.md`, thesis docs in `docs/`
