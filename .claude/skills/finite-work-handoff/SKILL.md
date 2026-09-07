---
name: finite-work-handoff
description: Carry one approved build or investigation across sessions until its acceptance checks reach SHIP, DECISION, BLOCKED or an explicit stop — without adjacent findings becoming new work. Use when resuming a workstream, picking up a handoff or a cloud session, checkpointing, closing out a task, writing an owner result, or whenever findings are opening follow-ups faster than work is closing.
---

# Finite-work handoff

The canonical rules are `docs/BUILD_OS.md` → *Finite work — discovery does not create work*
and *The mission contract, and the owner layer*, under Build OS v0.12 (`DEC-011`). **That
document is canonical; this skill is the procedure for applying it here.** Where the two
appear to disagree, the document wins and the disagreement is a defect in this file.

**Discovery does not create work. Only admission creates work.**

---

## Before anything: the role

This repository bounds sessions by **role** before it bounds them by protocol
(`CLAUDE.md` → *Session role*). Establish the role first; it decides what this session may
write at all, and it is sticky for the whole session. The mission contract below decides what
this session is *for* — a narrower question, and a later one.

A read-only role that finds a needed write **recommends the owning role**. It does not quietly
become a write session, and it does not claim persistence that did not happen: hand the
checkpoint over as a precise repository-update block (exact file, exact fields, exact
replacement text) and say plainly that the state is not yet persisted.

## At session start

1. **Read the durable state, not the chat.** `docs/workstreams/ACTIVE.md`, the workstream file
   (its `Open Decisions`, `Assumptions`, `Next Step`), the Build Card or Spec if one exists,
   and the PR. Continue from the unresolved point in a sentence or two; do not restate the goal
   to the person who set it.
2. **State the mission contract** in the workstream or the PR handoff: the outcome, the
   `## Acceptance Checks`, the non-goals, the material interrupt risks, and the finish
   condition. Write it **now**, at the start — a finish condition invented at the end describes
   where the work stopped rather than what was owed.
3. **Resume the existing workstream.** Continuation is the default. A new chat is not a new
   workstream, and neither is an adjacent thing implementation revealed.
4. **Never copy Experiment OS into the contract.** Link the experiment, Version, epoch,
   deployment, gate, revision or issue; ask Experiment OS for its state. A restated standing,
   gate verdict or P&L figure is stale the day after it is written and believed anyway
   (`DEC-001`). Where a contract needs one, write it as a **guard to check before acting**.

## While working — the dispositions

Every finding outside the current mission takes exactly one disposition, **before** anything
happens to it:

| Disposition | Use when | Action |
|---|---|---|
| `FIX NOW` | An acceptance check needs it, or it is an immediate material safety, security, data-loss or real-money risk | Fix it in the current workstream and PR |
| `PARK` | Valuable, unrelated, safe to defer | One line in `ACTIVE.md` → `## Parked`. No ticket, branch, PR or session |
| `DISCARD` | Speculative, cosmetic, duplicate, not worth its cost | Drop it |
| `OWNER DECISION` | A genuine product, priority, budget or risk judgement | Return a `DECISION` with a recommendation. Do not open the work pre-emptively |

**`FIX NOW` carries the load.** If the original ask cannot honestly ship without it, it was
never out of scope. The table lets you defer *adjacent* work; it never lets you defer your own
defect. A follow-up is not a substitute for correcting something in scope.

**Where stopping is a real option, list it.** An owner who does not see it listed infers the
agents have assumed the work continues.

### The fifth destination, and the one most often got wrong

A finding about a **problem** — an anomaly, a suspected defect, an operational incident, a
scientific question, a shared-platform problem — is an **Experiment OS issue**
(`docs/EXPERIMENT_OS_ISSUES.md`), not a `PARK`. The parking lot holds deferred *development*
candidates that nothing schedules; an issue is durable workflow state for a problem, and it
**routes to the existing role that owns it**. There is no fixer role.

Parking a real defect buries it somewhere nothing schedules and nobody reads. That is the
failure this distinction exists to prevent.

An issue changes nothing by existing: opening, routing, accepting or resolving one never moves
a lifecycle state, a gate, a verdict, an epoch, a Version, a Platform Revision or exposure.
That restriction is what makes it cheap enough to open on a suspicion.

### Two things that are never `simple`

Proportionality lets *simple* work skip the workstream, the Build Card and review. It does not
apply to anything touching **real-money exposure, the arming path, a live safeguard, a gate or
the ops channel** — those are never simple, whatever the diff size (`DEC-011`). Classification
only ratchets up, and genuinely unclear work is significant.

## Checkpointing

At meaningful moments — a materially clearer model, an owner decision, a spec issued, a PR
created, a review finding, completion or blocking. Not after every exchange. The test: *if this
session ended right now, would the repository still contain what we just worked out?*

Persist conclusions, models, decisions and open questions. **Never a transcript.**

## At session end — exactly one state

Update the durable state first, then end in one of:

- **Continue** the same workstream, with its single next action.
- **`SHIP`** — acceptance checks met and the merge gate complete.
- **`DECISION`** — owner judgement is genuinely required.
- **`BLOCKED`** — responsible progress cannot continue. Name the unblocker; "waiting" is not
  one, and a blocked workstream with a vague blocker is an abandoned one nobody has admitted to.
- **`ABANDONED`** — the owner chose to stop.

Most PRs have **no** owner result, and that is correct: the states are terminal, not a running
status. A PR awaiting review says so and carries no marker.

### Writing the result here

- **100 words or fewer, outcome first.** 150 is a ceiling and exists only for a material
  deviation or residual risk that needs a sentence. A deviation dropped to hit 100 words is a
  compressed lie.
- **This project runs `solo`** (`DEC-011`). A `SHIP` must state plainly that **no independent
  party reviewed the change.** Never write `Owner-accepted` yourself, and never infer one from
  a merge — you may only *transcribe* an acceptance the owner actually gave, naming the channel
  it came through.
- **`SHIP` reports the development gate only.** Its `Next action` is **the merge**. An
  Experiment OS action that follows — a registration, an arm, a promotion, an impact acceptance
  — is named as a **guard for the operator**, never as a next step and never as something the
  PR authorized.
- **The owner result and the session identity header are different surfaces.** The header
  (`SESSION: … / MODE: … / ENFORCEMENT: … / AS OF: …`) opens a session and says who is
  speaking; the result closes a piece of work on the PR and says where it landed. Never in the
  same block.
- A completed workstream's `Next Step` is **`None.`** Not "open a ticket", not "follow up
  later". Deferred ideas live in the parking lot and nowhere else.

## The final check

Before ending, ask:

> **Did I complete the original ask, repair a genuine blocker, or merely notice something else?**

Only the first two justify ongoing active work without the owner admitting it.

And on anything parked: nothing in `## Parked` is started by an agent. **Only the owner
promotes**, at which point it enters the board under the active-work limit.
