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

## Durable problems

A problem that outlives the session — an anomaly, a suspected defect, an
operational incident, a scientific question, a shared-platform problem — belongs
in an **Experiment OS issue** (`docs/EXPERIMENT_OS_ISSUES.md`), not in prose.

Tickets route work to the **existing** role that owns the problem. There is
deliberately no "Issue Workshop" and no "Fixer" in the menu above: a generic
remediation role would compete with the four that already own these problems, and
would end up holding the tickets nobody else claimed.

A ticket records ownership, evidence, the remedy decision, validation and
resolution. It **never** changes a lifecycle state, a gate, a verdict, an epoch,
a Version, a Platform Revision or real-money exposure — those keep their own
services and approval rules, and the ticket links the canonical record afterwards.

Where uncertainty is real it stays visible: a ticket may sit `UNCLASSIFIED` with
Live Ops for operational diagnosis before anyone can say whether the runtime or
the science is at fault.

## Crossing a boundary

A read-only role that finds a needed write hands off by naming the owning role
and the canonical object — it does not quietly start writing:

```
FROM: Experiment Control Tower
TO: Platform Change Review
OBJECT: PlatformComponent FILL_MODEL / Experiment <key>
ISSUE: XOS-000123 (or: none yet — the receiving role opens it)
FINDING: ...
EVIDENCE: <ops request id / read model>
REQUIRED NEXT ACTION: ...
DO NOT CHANGE: ...
```

