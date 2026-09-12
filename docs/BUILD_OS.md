# Build OS — the development protocol for this repository

The framework block — canonical repository, adopted version, last compatibility check —
lives in `CLAUDE.md`, because that is the file every agent reads at session start. This
document is the rest of the protocol. It lives here rather than in `CLAUDE.md` because that
file is a **router**, and a test enforces it
(`tests/test_session_system.py::test_claude_md_points_at_experiment_os_as_canonical`).

Canonical framework: **`50thycal/build-os`**, adopted at **v0.12**, operating mode **`solo`**.
Build OS is referenced, never forked — if the protocol is wrong, fix it there and pick the
change up at the next compatibility check.

The project is **current**, so this document no longer carries a section explaining why it is
behind. What the v0.4 → v0.12 delta meant for this repository, and the four decisions the
migration turned on, are the adoption record on
[`WS-001`](workstreams/WS-001-build-os-adoption.md) and `DEC-011`.

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

Close the session in chat with the **brief** — WHAT HAPPENED / GOAL / BLOCKERS / DECISIONS /
NEXT STEPS, under 120 words, then the PR reference (`DEC-012`, `session-brief`). That does not override a
standing role's identity header, a direct question, or an escalation that needs an answer.

## Finite work — discovery does not create work

`DEC-025` in canonical, `framework/FINITE_WORK.md`. **Only admission creates work.** A finding
outside the current mission takes exactly one disposition before anything happens to it:

| Disposition | Use when | Action |
|---|---|---|
| `FIX NOW` | An acceptance check needs it, or it is an immediate material safety, security, data-loss or real-money risk | Fix it in the current workstream and PR |
| `PARK` | Valuable, unrelated, safe to defer | One line in `ACTIVE.md` → `## Parked`. No ticket, branch, PR or session |
| `DISCARD` | Speculative, cosmetic, duplicate, not worth its cost | Drop it |
| `OWNER DECISION` | A genuine product, priority, budget or risk judgement | Return a `DECISION`. Do not open the work pre-emptively |

`FIX NOW` carries the load: **if the original ask cannot honestly ship without it, it was never
out of scope.** The table lets an agent defer adjacent work; it does not let it defer its own
defects.

**Continuation is the session-start default.** A new session resumes the mission on the board.
It does not become a new workstream by being a new chat, or because implementation revealed
something adjacent.

**The parking lot is deliberately impoverished.** One line each, no ID, phase, owner or
estimate; nothing schedules it; no agent starts anything in it; only the owner promotes. A
parking lot pleasant to work from is a backlog. Parked lines are **never rendered as work**.

**The active-work limit is 4** here rather than the canonical default of 3, counting `Active`
rows only — see `ACTIVE.md` for the reason and `DEC-011` for the decision.

**A completed workstream's `Next Step` is `None.`** Not "open a ticket", not "follow up later".
A `COMPLETE` workstream whose next step is anything else is an integrity warning. Existing
files are **not** backfilled: `## Acceptance Checks` and `## Parked` are written on the next
workstream opened, and completed files stay as they are.

## The mission contract, and the owner layer

A workstream states its `Goal`, its `Non-Goals`, the material interrupt risks, and a
`## Acceptance Checks` section saying what counts as finished — **written at the start**,
because a finish condition invented at the end describes where the work stopped.

Each piece of work ends in exactly one **Owner Result** on its PR — `SHIP`, `DECISION` or
`BLOCKED` — and most PRs have none, because the three are terminal rather than a running
status. `SHIP` requires validation green and actually run, no unresolved `Blocking` or
`Should fix` finding, the merge-finalization commit pushed, and no undisclosed material
deviation. Owner results are **100 words or fewer, outcome first**; 150 is a ceiling that
exists for a material deviation or residual risk that takes a sentence to state properly. A
deviation dropped to hit 100 words is a compressed lie.

## Operating mode: `solo`

Declared in `CLAUDE.md`'s framework block, per v0.8, and decided in `DEC-011`. One account,
one GitHub identity, one agent: there is no independent actor, and `reviewed` would leave a
gate that cannot be satisfied — which is not strict, it is inert, and it trains everyone to
merge past it. `WS-001` sat in a false `REVIEW` for eleven days proving exactly that.

What `solo` means in practice:

- Significant work is **accepted by the owner at merge**, recorded as `Owner-accepted` with a
  separate `Accepted head` field naming a full 40-character SHA, and **never described as
  reviewed**. `Accepted head:`, never `Reviewed head:` — a consumer keys on the field name.
- A `SHIP` result must state plainly that **no independent party examined the change.** That
  sentence is the point of the mode, not decoration.
- **`Owner-accepted` is the owner's, and an agent may not issue one.** An agent may *transcribe*
  an acceptance the owner actually gave, naming the channel it came through. Inferring one from
  silence, from approval of something adjacent, or **from a merge** is issuing a verdict, not
  relaying it.
- **A finalization commit never writes a verdict it does not yet have.** It sets phase, status,
  implementation state, related PRs, next step and `Finalization: pushed`, and leaves the
  verdict at whatever was true when it was written.
- **No history is upgraded.** Acceptances are not retrofitted onto merged history — an
  acceptance written after the fact records a decision nobody made at the time. `solo` is a
  fallback, not a preference: the moment a second actor exists, the project moves to `reviewed`.

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
- **Standing authorizations** (`DEC-012`, `docs/STANDING_AUTHORIZATIONS.md`). The operator's
  request is the approval for every step inside its scope: a paper-scope chain — package PR,
  merge, `REGISTER_PACKAGE`, paper env append — runs end to end off one message when
  `xos package-preflight` says GO. In `solo` mode the merge of such a PR transcribes the
  request as the owner's acceptance, naming the session; it is still never described as
  reviewed, and it never extends to the hard-stop tier (real money, safeguards, a Platform
  Revision activation, the ops workflow), where the operator answers in that session.
- **The ops channel runs default-branch code** against a read-only connection. A change on a
  feature branch cannot be exercised against production until it merges — plan measurement
  around that, or recompute from a read-only query and validate the method against a known
  baseline.
- **Two owner-facing surfaces, one boundary** (`DEC-011`). The session identity header
  (`SESSION: … / MODE: … / ENFORCEMENT: … / AS OF: …`) opens a *session* and says **who is
  speaking**; the Owner Result closes a *piece of work* and says **where it landed**. They do
  different jobs and must not merge into one: they never appear in the same block, the header
  never carries a result, and a result never carries a session state. The header stays a
  chat-level thing and the Owner Result lives on the PR — so a standing role's first
  substantive report is unchanged by adopting the owner layer.
- **`SHIP` reports the development gate only** (`DEC-011`). In this repository a merge is
  frequently not the end: an XOS registration, an arm, a promotion or an impact acceptance
  often follows. `SHIP`'s `Next action` is **the merge**. An Experiment OS action that follows
  is named as a **guard for the operator** — never as a Build OS next step, and never as
  something the PR authorized, because `DEC-001` says a workstream authorizes nothing.
  `WS-002`'s next step — *"Merge guard: verify in XOS that the revision is registered +
  impacts accepted"* — is already exactly this shape.
- **A safety floor under proportionality** (`DEC-011`). v0.6 lets *simple* work skip the
  workstream, the Build Card and independent review. **No change touching real-money exposure,
  the arming path, a live safeguard, a gate, or the ops channel is ever classified simple**,
  regardless of diff size. Classification only ratchets up and unclear work is significant;
  this names the repository's specific tripwires rather than relying on judgment at the moment
  judgment is worst.

## What this project deliberately does not take

Recorded so a later compatibility check can tell a considered omission from staleness. Each
extends `DEC-011`'s *what was deliberately not taken* rather than reopening it.

| Not taken | Why | Revisit if |
|---|---|---|
| **The Design Room** (canonical adoption step 3) — one ChatGPT Project per repository, seeded with `templates/CHATGPT_PROJECT_INSTRUCTIONS.template.md`, where design conversations happen | Design intent here arrives through the **session-role router**, which already establishes what a session may write before it writes anything. A second front door would duplicate that, and the roles carry constraints — Experiment OS authority, the real-money invariants — a generic Design Room does not know about | Design work starts arriving without a role behind it, or design conversations need a home that is not a coding session |
| **`OWNER_PLAN.template.md`** and the owner-approval flow (v0.6) | Same reason: the plan is an approval surface the role router already provides | As above |
| **`REVIEW_SUMMARY.template.md`** (canonical adoption step 4) | `solo` mode has no independent reviewer, so nothing would ever write one. Vendoring it would advertise a step this project cannot perform — the inert-gate failure `DEC-011` exists to stop | The project moves to `reviewed`, in which case take it with the mode change |
| **CI or tooling over framework artifacts** | Build OS ships none deliberately; the authority boundary is enforced by discipline. `DEC-001` records the real revisit condition — a board a month without an update should be deleted rather than left to mislead | Never, on current reasoning |

The pattern in the first three is one decision, not three: **this repository replaced the
owner-approval surface with the session-role system**, and the artifacts that serve that
surface are therefore surplus rather than skipped.

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
| A new workstream opened for something found along the way | Discovery is not admission; the board grows monotonically from the agent's side |
| A completed workstream whose `Next Step` defers something | A deferral written into a record claiming to be finished — nothing schedules it and nobody reads it |
| `Owner-accepted` inferred from a merge | An agent issuing a verdict under another name; `solo` moved acceptance to the owner, not to the agent |
| A parked line surfaced beside the board as work | Rebuilds the backlog the parking lot exists instead of |
