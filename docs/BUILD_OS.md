# Build OS — the development protocol for this repository

The framework block — canonical repository, adopted version, last compatibility check —
lives in `CLAUDE.md`, because that is the file every agent reads at session start. This
document is the rest of the protocol. It lives here rather than in `CLAUDE.md` because that
file is a **router**, and a test enforces it
(`tests/test_session_system.py::test_claude_md_points_at_experiment_os_as_canonical`).

Canonical framework: **`50thycal/build-os`**, adopted at **v0.4**. Build OS is referenced,
never forked — if the protocol is wrong, fix it there and pick the change up at the next
compatibility check.

---

## The authority boundary

`DEC-001`. This is the rule most worth getting right, because violating it is cheap and the
damage is slow.

| question | canonical source |
|---|---|
| What is this experiment's state, evidence, gate verdict, epoch, exposure? | **Experiment OS** |
| What are we designing and building right now, and why does the system work this way? | **Build OS** (`docs/`) |

Build OS **complements** Experiment OS. It does not replace it, wrap it, or mirror it.

**A workstream links; it never copies.** Referencing `XOS-000006`,
`mmsell-type-tight/v1/e1` or `MARKET_TAXONOMY:coverage_2026_08_13` is right. Restating a
standing, a gate verdict, an epoch boundary or a P&L figure is wrong — it is stale the day
after it is written and believed anyway.

**A workstream authorizes nothing.** Its phase and status are development state. Only
Experiment OS's own services register, arm, promote, pause or retire, under their own
approval rules. A Build Card approved by the owner is approval of a *design*.

**Chat is authoritative for neither, and transcripts are never archived.** Persist
conclusions, models, decisions and open questions — not recordings of thinking.

## The three memory layers

| File | Answers | Shape |
|---|---|---|
| `docs/PROJECT_MODEL.md` | How does the system work today? | Overwritten; always current |
| `docs/DECISIONS.md` | Why does it work this way? | Appended; entries never rewritten |
| `docs/workstreams/` | What is being designed/built, and what remains? | Living while active; frozen when done |

`docs/workstreams/ACTIVE.md` is the board. It is what a new session reads first, and
everything about its format should serve being read in fifteen seconds.

**Keep `PROJECT_MODEL.md` architectural and durable.** No current standings, no gate reads,
no changing P&L. The pressure to paste today's numbers into it will be constant; the file
is worth less than nothing the moment it carries something false.

## The workstream lifecycle

```text
IDEA → EXPLORE → MODEL → DECIDE → BUILD_CARD → READY_TO_BUILD → BUILDING → REVIEW → COMPLETE
                                                  ( PAUSED · BLOCKED · ABANDONED )
```

Phase is *where the effort is*; status is *whether it is moving*. A blocked workstream keeps
the phase it was blocked in, because that is where it resumes. Backward movement is normal —
record it rather than hiding it.

`BLOCKED` requires a **named** unblocker in the next step. A blocked workstream with a vague
blocker is an abandoned one nobody has admitted to.

`COMPLETE` means the PR merged **and** `PROJECT_MODEL.md` and `DECISIONS.md` are true again.
A workstream marked complete over a stale model has moved the problem, not finished it.

IDs are stable: assigned in order, never reused, never renumbered.

## Session behaviour

1. **Establish the session role first** (`CLAUDE.md` → *Session role*). It bounds what this
   session may write at all.
2. **Then run the framework compatibility check**, once, before the first substantial piece
   of design/build work — compare `CLAUDE.md`'s adopted version against `VERSION.md` in the
   canonical repository. A typo fix or a one-line answer does not need one. If the project
   is current, say nothing about it.
3. **Orient from the board, not from the operator.** On a continuation, read `ACTIVE.md` and
   the workstream's `Open Decisions`, `Assumptions` and `Next Step`, then continue from the
   unresolved point in a sentence or two. Do not restate the goal to the person who set it.
4. **Checkpoint at meaningful moments** — a new workstream, a materially clearer model, an
   owner decision, a Build Card ready, a spec issued, a PR created, a review finding,
   completion or blocking. Not after every exchange. The test: *if this session ended right
   now, would the repository still contain what we just worked out?*
5. **Never claim persistence that did not happen.** A read-only role that cannot write hands
   its checkpoint to a writing role as a precise repository-update block — exact file, exact
   fields, exact replacement text — and says plainly that state is not yet persisted.

## Implementation and handoff

Features arrive as a **Build Card** (owner-facing, 30–60 seconds) plus a **Build Spec**
(exhaustive, for the implementation agent), belonging to a workstream. Templates:
`docs/templates/`.

Owner decisions in a spec may not be silently changed. Implementation discretion — internal
structure, naming, data structures, algorithms, error mechanics, test layout — is the
implementer's. Escalate product behaviour, not technical uncertainty.

After implementing: run the project's own validation, commit, push the branch, open a PR
ready for review, and write the **Implementation Handoff into the PR body** —
`.github/pull_request_template.md` makes it the default. The PR is the handoff; a chat
summary is not, and duplicating the handoff into chat teaches everyone that chat is where
the real information lives.

Then update project memory **in the same PR**: `PROJECT_MODEL.md` if architecture, flows,
invariants or responsibilities materially changed; a `DECISIONS.md` entry for a consequential
choice; and the workstream file plus `ACTIVE.md` with phase, status, implementation state,
PR and next step.

`Spec Deviations` is load-bearing. Under-reporting there converts a visible disagreement
into an invisible one. If unsure whether something counts, it counts. Write `None`
explicitly when there are none.

Keep the final chat response to a line or two and the PR reference. That does not override a
standing role's identity header, a direct question, or an escalation that needs an answer.

## Project-specific additions

Marked as such so a future upgrade can tell a deliberate local rule from staleness.

- **Ordering:** role first, then the compatibility check. Running the check never grants a
  read-only role a write it does not have.
- **Shared semantics need a Platform Change Review.** A change to fees, fills, the market
  taxonomy, execution, risk, data provenance or a metric definition is a Platform Revision
  with its own impact dispositions. Say so in the spec, and do not merge ahead of the
  revision being registered.
- **Real money.** Anything that expands live exposure needs explicit operator confirmation.
  A PR is not that confirmation. Say so in the handoff.
- **The ops channel runs default-branch code** against a read-only connection. A change on a
  feature branch cannot be exercised against production until it merges — plan measurement
  around that, or recompute from a read-only query and validate the method against a known
  baseline.

## Anti-patterns

| Anti-pattern | Why it hurts here |
|---|---|
| A workstream restating a gate verdict or standing | Two ledgers that disagree; the copy is believed |
| A stale `Current Mental Model` | Worse than empty — it will be trusted |
| `BLOCKED` with "waiting" as the next step | Nothing can unblock it; it is abandoned undeclared |
| A board row that needs a paragraph | It stops being readable in fifteen seconds, so it stops being read |
| `COMPLETE` with `PROJECT_MODEL.md` untouched | The next agent trusts a model that is now false |
| One workstream per idea anyone mentions | The board stops distinguishing real work from noise |
| Claiming a compatibility check that could not be performed | Removes the one signal that says whether anyone looked |
| Committing a transcript | Unreadable at volume; buries the conclusions it was meant to preserve |
