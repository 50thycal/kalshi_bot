# Session roles

Experiment OS is live in production under `NEW_ONLY`. It is the canonical source
of truth for experiment lifecycle, evidence, gates, platform state and lineage.
These playbooks are **operating postures on top of it** — they never hold their
own copy of experiment state.

| # | Role | File | Default mode |
|---|------|------|--------------|
| 1 | Experiment Control Tower | `experiment-control-tower.md` | READ ONLY |
| 2 | Evo Control Tower | `evo-control-tower.md` | READ ONLY |
| 3 | Evo Ticket Workshop | `evo-ticket-workshop.md` | WRITE (no live-money capability) |
| 4 | Platform Change Review | `platform-change-review.md` | WRITE after impact plan |
| 5 | Research Lab | `research-lab.md` | WRITE research/probe/paper; no live promotion |
| 6 | Legacy Migration | `experiment-migration.md` | WRITE migration only (transitional) |
| 7 | Live Ops | `live-ops.md` | WRITE operational safety |
| 8 | Task-specific | *(no file)* | as the task requires, bounded by Experiment OS |

## How routing works

`.claude/settings.json` runs a `SessionStart` hook that prints `ROUTER.txt` into
every new session, and `CLAUDE.md` carries the same rule (it is auto-loaded).
If the opening message names a role, start immediately. Otherwise ask once. The
role is sticky for the session.

Roles are deliberately **not** implemented as skills: skills auto-trigger on
description match, and a session must never *become* the Ticket Workshop by
accident. Role selection is explicit.

## Session identity header

Every standing role opens its first substantive report with:

```
SESSION: <role>
MODE: <READ ONLY | WRITE ...>
ENFORCEMENT: <mode> since <cutover>
PLATFORM SNAPSHOT: <fingerprint>
AS OF: <timestamp America/Chicago>
```

The Control Tower emits this automatically (`control-tower` renders it).

## Crossing a boundary

A read-only role that finds a needed write hands off by naming the owning role
and the canonical object — it does not quietly start writing:

```
FROM: Experiment Control Tower
TO: Platform Change Review
OBJECT: PlatformComponent FILL_MODEL / Experiment <key>
FINDING: ...
EVIDENCE: <ops request id / read model>
REQUIRED NEXT ACTION: ...
DO NOT CHANGE: ...
```

