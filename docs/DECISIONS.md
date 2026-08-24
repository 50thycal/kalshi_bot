# Decisions — kalshi_bot

Why does the system work this way? Lightweight ADRs, appended in order. Consequential
decisions only. An accepted entry is never rewritten because architecture later changed —
supersede it with a new one and update only the old status line.

**Build OS v0.4**

**Scope note.** This log records *development and architecture* decisions. It is **not** a
place for experiment verdicts, gate outcomes, promotions, epochs or platform revisions —
Experiment OS records those, with its own approval rules, and a copy here would be a second
ledger that drifts (`DEC-001`).

---

### DEC-001 — Adopt Build OS v0.4, and fix the Build OS / Experiment OS authority boundary

**Date:** 2026-08-24
**Status:** Accepted

**Context**

This project already has an unusually strong durable-state discipline for *experiments*.
Experiment OS is live under `NEW_ONLY` enforcement and is canonical for lifecycle,
evidence, gates, epochs, platform revisions and durable issues. Nothing about that is
missing or weak.

What has no durable home is the layer above it: **the development workflow itself.** How
the system is put together, why it is put together that way, which design threads are
currently live and what each is waiting on, and what an implementation PR actually
delivered — all of that has lived in chat sessions. Sessions end. The next session
reconstructs the picture by reading code and thesis documents and asking the operator what
was decided, which is expensive and lossy, and occasionally wrong in ways nobody notices.

Concretely, at the time of this decision three efforts were in flight at once — a
settlement-taxonomy repair in review, a paper design blocked behind it, and unresolved
gate-addressing and detector-reconciliation work on two historical live canaries whose
proposed successor Versions had been withdrawn — and the only thing holding their
relationship together was one person's memory of which conversation said what.

Build OS (`50thycal/build-os`) is a small, code-free protocol for exactly this: three
durable memory layers, a workstream lifecycle, and a PR-as-handoff rule.

**Decision**

Adopt **Build OS v0.4** as the project's development framework, pinned, with a strict
authority boundary against Experiment OS:

- **Experiment OS remains canonical** for experiments, Versions, epochs, deployments, arms,
  gates and results, platform revisions, impact actions, enforcement state and durable XOS
  issues. Build OS neither replaces nor duplicates any of it.
- **Build OS is canonical** for the human/agent development workflow: project architecture
  (`docs/PROJECT_MODEL.md`), consequential decisions (this file), Build Cards, Build Specs,
  development workstreams (`docs/workstreams/`), PR handoffs and reviews.
- **Workstreams link; they do not copy.** A workstream may reference `XOS-000006`,
  `mmsell-type-tight/v1/e1`, or `MARKET_TAXONOMY:coverage_2026_08_13`. It must not restate a
  standing, a gate verdict, a P&L figure or an epoch boundary.
- **A workstream authorizes nothing.** Its phase and status are development state. Only
  Experiment OS's own services register, arm, promote, pause or retire, under their own
  approval rules.
- **Chat is authoritative for neither, and transcripts are never archived.** Conclusions,
  models, decisions and open questions are persisted; recordings of thinking are not.

Project-specific ordering, recorded in `CLAUDE.md` under a `Project-specific:` marker: a
session establishes its **role** first, then runs the Build OS compatibility check before
substantive design/build work. The role decides what a session may write at all; the
framework check decides which protocol it writes under.

**Rationale**

The two systems answer genuinely different questions, and conflating them is the failure
mode worth spending a decision to prevent.

Experiment OS answers *what is true about this experiment* and is deliberately hostile to
convenience: verdicts must be recorded by a designated evaluator, epochs cut at measured
boundaries, and the read channel cannot write. That rigour is exactly what makes it a bad
place to park "we are still arguing about whether this design is worth 17 months" — which
is not a fact about an experiment, has no evaluator, and would be noise in a ledger built
for evidence.

Build OS is the opposite: cheap to write, meant to be edited, and honest about uncertainty.
Putting development state there costs Experiment OS nothing and gives the next session a
board it can read in fifteen seconds.

Pinning to v0.4 rather than tracking `main` is the framework's own rule and the right one
here: an in-flight design should not change shape because the framework moved underneath
it. The compatibility check is what stops the pin from rotting into staleness.

**Alternatives considered**

- **Extend Experiment OS to cover development workflow.** Rejected. It would put unreviewed,
  frequently-edited design prose into a system whose whole value is that its contents are
  recorded, evaluated and hard to change. It would also mean every design note needed a
  write path that deliberately does not exist for agent sessions.
- **Keep using thesis documents in `docs/` as the design record.** Rejected as insufficient,
  not wrong. Thesis documents are excellent per-idea evidence and stay. What they cannot do
  is answer "what are the four things currently in flight, and what is each waiting on" —
  there is no board, and nothing tells a reader which documents are live.
- **Adopt Build OS but let workstreams mirror experiment state for convenience.** Rejected
  explicitly, and this is the decision's sharp edge. A mirrored standing is stale the day
  after it is written and is believed anyway. Two ledgers that disagree are worse than one
  ledger and a link.
- **Track `main` of the framework instead of pinning.** Rejected per the framework's own
  guidance: silent drift changes an in-flight effort's shape mid-effort.

**Consequences**

*Easier:* a new session orients from `docs/workstreams/ACTIVE.md` instead of from
transcripts. PR handoffs have a standard shape and a template that makes it the path of
least resistance. Architecture and rationale outlive individual agents.

*Harder / more expensive:* there is now a checkpoint duty. Workstream files must be updated
at meaningful moments or the board becomes a liability — a stale model is worse than no
model, because it will be believed. Someone must also keep `PROJECT_MODEL.md` free of
standings; the pressure to paste "current gate: HOLD" into it will be constant.

*Expensive to reverse:* not very. Build OS adds no dependency, no service and no build step.
Abandoning it means deleting four documents and a template directory. That cheapness is
part of why it is worth trying.

*Revisit if:* workstream files start restating Experiment OS state (the boundary is
failing), or the board goes a month without an update (the checkpoint duty is not being
paid, and an unmaintained board should be deleted rather than left to mislead).

---

<!-- Copy the block above for each new decision. IDs are stable: never reused, never
     renumbered. -->
