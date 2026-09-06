# WS-001 — Build OS adoption, and the v0.4 → v0.12 migration

**Phase:** COMPLETE
**Status:** Done
**Created:** 2026-08-24
**Updated:** 2026-09-06

One thread, two acts. **Act 1** adopted Build OS v0.4 (PR
[#258](https://github.com/50thycal/kalshi_bot/pull/258), merged 2026-08-24T14:26:24Z) and was
never finalized — the artifacts reached the default branch while this file went on saying the
adoption was awaiting an independent review. **Act 2**, below, migrates the project to v0.12
and finalizes the workstream once. It is deliberately not a new `WS-###`: the workstream
already existed and is exactly this thread.

## Goal

**Act 2 (2026-09-06).** kalshi_bot runs under Build OS v0.12, with its project-specific
additions and the Experiment OS authority boundary intact, and its board honestly reflects
what is in flight.

**Act 1 (2026-08-24).** Make architecture, consequential decisions, active design/build work, implementation
handoffs and independent reviews persist in GitHub rather than in chat history — without
disturbing Experiment OS, which stays canonical for experiment truth.

## Context

Experiment OS already holds experiment state rigorously. The layer above it had no durable
home: how the system fits together, why it was built that way, which design threads are
live, and what each is waiting on all lived in chat sessions that end.

The cost was visible at the moment of adoption: three efforts were in flight simultaneously
(a taxonomy repair in review, a paper design blocked behind it, and unresolved
gate-addressing and detector-reconciliation work on two historical live canaries whose
successor plans had been withdrawn) and their relationship existed only in one person's
memory.

## Current Mental Model

```text
                     ┌──────────────────────────────────────┐
   experiment truth  │           EXPERIMENT OS              │  canonical
   ───────────────►  │  versions · epochs · deployments     │  recorded, evaluated,
                     │  gates · platform revisions          │  hard to change
                     │  enforcement · XOS issues            │
                     └──────────────────┬───────────────────┘
                                        │  linked, never copied
                     ┌──────────────────┴───────────────────┐
   development       │             BUILD OS  (docs/)        │  canonical
   workflow          │  PROJECT_MODEL · DECISIONS           │  cheap to write,
   ───────────────►  │  workstreams/ · templates/           │  meant to be edited
                     │  PR handoff · review                 │
                     └──────────────────────────────────────┘

   chat  ──►  authoritative for NEITHER.  Transcripts are never archived.
```

Three durable memory layers, per Build OS v0.4:

| file | answers | shape |
|---|---|---|
| `docs/PROJECT_MODEL.md` | how does the system work today? | overwritten; always current |
| `docs/DECISIONS.md` | why does it work this way? | appended; never rewritten |
| `docs/workstreams/` | what is being designed/built, and what remains? | living while active |

## Decisions Made

- **Adopt v0.4, pinned** — not `main`. An in-flight design must not change shape because
  the framework moved underneath it. `DEC-001`.
- **The authority boundary is explicit and one-directional.** Workstreams link to XOS
  objects; they never restate standings, gate reads or P&L. `DEC-001`.
- **Role first, then the framework check.** The session role bounds what may be written at
  all; the framework check only decides which protocol it is written under. Recorded in
  `CLAUDE.md` under a `Project-specific:` marker so a future upgrade can tell a deliberate
  local addition from staleness.
- **Seed the board from a read-only Experiment OS inventory, not from ticket count.** Five
  workstreams, not one per XOS ticket or per historical experiment.

## Open Decisions

None. Nothing is outstanding that adoption depends on: the framework is adopted by the
contents of this PR, and the two questions below are usage questions that can only be
answered by using it.

## Deferred questions — none of these block completion

Recorded so they are not re-derived. Neither changes what adoption delivers, and neither is
a precondition for moving this workstream to `COMPLETE`.

- **Q1.** Should `docs/BOOK_REGISTRY.md` and the thesis documents be indexed from
  `PROJECT_MODEL.md`, or left discoverable only through `CLAUDE.md`? Leaving them out keeps
  the model architectural; pulling them in risks the model becoming a catalogue. Answerable
  once the board has been used for a few weeks and the actual friction is known.
- **Q2.** What is the checkpoint cadence in practice for a project whose sessions are
  role-bounded and often read-only? Build OS assumes a design agent that can write. A
  read-only role here must hand its checkpoint to a writing role as a repository-update
  block. No cadence has been exercised yet, and exercising it is how it gets answered.

## Acceptance Checks

Written at the start of Act 2, per v0.12. All six hold.

1. `CLAUDE.md` → *Build OS* reads `Adopted version: v0.12`, `Last compatibility check: v0.12 on
   2026-09-06`, and declares an operating mode. ✅
2. `docs/BUILD_OS.md` no longer carries a *Canonical is ahead* section, because the project is
   not behind; what it said about the delta is the adoption record below. ✅
3. The board carries only workstreams actually in flight, and states the limit this project
   chose and why. ✅ — eight rows, four `Active`, at a declared limit of 4.
4. Vendored templates in `docs/templates/` match canonical v0.12, and its `README.md` says
   v0.12. ✅ — plus `OWNER_RESULT` and `ACTIVE_WORK`, newly vendored.
5. Every rule this migration adds that conflicts with an existing project-specific rule is
   resolved explicitly in `CLAUDE.md`, not left for a session to discover. ✅ — the owner-layer
   split, the `SHIP` boundary, and the proportionality floor.
6. A decision record captures the migration: what was adopted, what was decided, what was
   deliberately not taken. ✅ — `DEC-011`. (The handoff brief said `DEC-002`; the log already
   ran to `DEC-010`, so the next free number is 11.)

## Adoption record — what v0.4 → v0.12 meant here

Moved from `docs/BUILD_OS.md` → *Canonical is ahead*, which is deleted: a project that is
current does not carry a section explaining why it is behind. Eight releases; four changed how
sessions here behave.

| Version | What it added | Bearing on this repository |
|---|---|---|
| **v0.5** | Capture Only, the Design Handoff PR, the reviewed-head merge gate, merge finalization | **Material.** The merge gate is the big one — a significant PR needs a verdict naming its current head as a full SHA, and no self-approval. Superseded in practice by the `solo` mode below |
| **v0.6** | The owner layer: Intent Intake, entry-point neutrality, the Owner Plan, `SHIP`/`DECISION`/`BLOCKED`, proportionality, the closed reviewer→implementer loop | **Material, and it interacts with the session-role system.** Resolved by decision 2 in `DEC-011`; the Owner Plan was deliberately not taken |
| **v0.7** | `SHIP` narrowed: only the owner's merge may remain | Refines v0.6. Resolved by decision 4 in `DEC-011` — `SHIP` reports the development gate, and an XOS action that follows is a guard |
| **v0.8** | Operating modes — `reviewed` vs `solo` — and the `Owner-accepted` verdict | **Material, and the most useful of the eight.** This repository is one account, which is the case `solo` exists for. Decision 1 in `DEC-011` |
| **v0.9** | `skills/`, an agent-invokable surface, and the rule that a framework document stays canonical where both apply | Relevant — this repo already has `.claude/skills/`. No skill copied; the boundary rule is adopted |
| **v0.10** | A finalization commit never writes a verdict it does not yet have; `Owner-accepted` comment form | Small, correct regardless of the pin, and directly relevant under `solo` |
| **v0.11** | An agent may relay an acceptance the owner gave elsewhere, naming the channel | Small, and the limit that matters: relaying is not inferring |
| **v0.12** | Finite work (`FIX NOW`/`PARK`/`DISCARD`/`OWNER DECISION`); a mission contract; continuation as the session-start default; a parking lot; a default limit of three; `Next Step: None.` on completion; owner results 100 words, outcome-first | **Material.** A 13-row board was the exact shape these rules were written against. This repo had already invented the parking lot under another name — see the appendix and `ACTIVE.md` → *What is deliberately not on this board* — so v0.12 mostly renamed a mechanism rather than introducing one |

**What the pin protected, and still does.** A v0.4 pin covered work done under it. Later
versions do not reach back: completed workstreams are not re-judged, PRs merged under v0.4 are
not retroactively reported as ungated, and PR #258 is not retro-accepted.

**The authority boundary was unaffected by all eight.** `DEC-001` stands unchanged.

## Assumptions

- The operator wants a board, not a process. If maintaining `ACTIVE.md` costs more than it
  returns, deleting it beats letting it rot — a stale board is worse than none.
- Build OS stays code-free. Adoption adds no dependency, no service, no build step, and
  nothing in CI validates these files.
- Existing session roles and safety invariants are unchanged by adoption. Build OS governs
  workflow, not permissions.

## Non-Goals

- Replacing, wrapping, or mirroring Experiment OS.
- Adding CI, linting, or tooling over framework artifacts.
- Rewriting existing research documents, `BOOK_REGISTRY.md`, or thesis docs.
- Changing runtime behavior, schema, environment, or any live configuration.

## Build Card

Approved by the operator as the Build Card in the session brief of 2026-08-24. Summary:
adopt Build OS v0.4 so development state persists in GitHub; keep Experiment OS canonical
for experiment truth; seed the board from a read-only inventory; touch no runtime.

## Implementation State

**Act 1 — merged.** PR [#258](https://github.com/50thycal/kalshi_bot/pull/258), merged
2026-08-24T14:26:24Z: the framework block and development protocol in `CLAUDE.md` and
`docs/BUILD_OS.md`, the three memory layers under `docs/`, four templates under
`docs/templates/`, and the PR handoff wired into `.github/pull_request_template.md`.

**Act 2 — this PR.** Framework block to v0.12 with `Operating mode: solo` and a declared
active-work limit; three project-specific rules added to `CLAUDE.md` (the owner-layer split,
the `SHIP` boundary, the proportionality floor); `docs/BUILD_OS.md` gains finite work, the
mission contract and the `solo` mechanics and loses *Canonical is ahead*; six templates
re-vendored from canonical v0.12 and the PR template rebuilt on the v0.12 handoff; the board
cleaned to eight rows with a `## Parked` list and the limit stated; `DEC-011`.

## Review State

**Act 1 was never finalized, and that is the failure worth recording.** This file and the board
both said `REVIEW · Active · "Independent review; approval + merge completes it"` from
2026-08-24 to 2026-09-06 while every artifact it described was already on the default branch.
Two causes, both now fixed: the post-merge bookkeeping was never done, and — the deeper one —
the awaited verdict could not exist, because no mode was declared and Build OS therefore
defaulted to `reviewed` on a project with one account and one agent. `DEC-011` decision 1
replaces the unsatisfiable gate with a stated condition.

Act 2 runs under `solo`. **No independent party reviewed this migration.** Acceptance is the
owner's at merge; this file does not, and may not, record one on their behalf, and none is
retrofitted onto #258.

**Finalization:** pushed.

## Related Decisions

- `DEC-001` — Adopt Build OS v0.4, and fix the Build OS / Experiment OS authority boundary.
  Unchanged by this migration.
- `DEC-011` — Migrate to Build OS v0.12: `solo` mode, the owner-layer split, an active-work
  limit of 4, and `SHIP` as a development-gate report.

## Related PRs

- [#258](https://github.com/50thycal/kalshi_bot/pull/258) — Build OS v0.4 adoption (Act 1,
  merged 2026-08-24).
- This PR — the v0.4 → v0.12 migration (Act 2).

## Parked

The two orphans from closing `WS-010`, and one carried past `WS-012`, are registered in
`ACTIVE.md` → *Parked* rather than here, because that is the list the owner reads. Nothing
schedules them and no agent may start them.

## Next Step

None.

---

## Appendix — efforts considered and deliberately not made workstreams

Recorded so the next session does not re-derive the judgment, and so the omissions are
visible rather than silent. Inventory source: a read-only `xos control-tower` read on
2026-08-24. The reasons below are deliberately written without lifecycle states, verdicts or
evidence counts — those move, and a copy of one here would be believed after it stopped
being true (`DEC-001`). Query Experiment OS for any object's current state.

| effort | why not a workstream |
|---|---|
| **`theta-tail-sell` successor A/B** | The successor is documented as *not ready to register*, and no design work is in flight on it. A workstream would be a bookmark. |
| **Retired experiments** (`mmsell-first-cohort`, `mmsell-scan-depth`, `pin15`, `wcprop`, `xgame`, weather families, …) | Concluded. History lives in Experiment OS and thesis docs. |
| **Grandfathered paper books** (`mmsell-price-ceiling`, `mmsell-variants-2026-07`, `mmsell-anchor-*`, `mmsell-wide-control`, `mmsell-type-tight`) | Experiments, not design threads. Their state is Experiment OS's to report, and a row here would be exactly the mirrored standing `DEC-001` forbids. |
