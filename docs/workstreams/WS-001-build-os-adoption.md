# WS-001 — Build OS adoption

**Phase:** REVIEW
**Status:** Active
**Created:** 2026-08-24
**Updated:** 2026-08-24

## Goal

Make architecture, consequential decisions, active design/build work, implementation
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

PR open — see *Related PRs*. Adds the framework block and development protocol to
`CLAUDE.md`, the three memory layers under `docs/`, four templates under `docs/templates/`,
and wires the PR handoff into `.github/pull_request_template.md`.

## Review State

Awaiting independent review. **Independent approval and merge complete this workstream** —
the adoption is entirely contained in this PR. Worth scrutinising: whether the authority
boundary in `DEC-001` is stated tightly enough to survive contact with a session that wants
to paste a gate read into a workstream, and whether `PROJECT_MODEL.md` has stayed
architectural.

## Related Decisions

- `DEC-001` — Adopt Build OS v0.4, and fix the Build OS / Experiment OS authority boundary.

## Related PRs

- Build OS v0.4 adoption (this workstream's PR).

## Next Step

Independent review of the adoption PR. On approval and merge this workstream is finished:
the next checkpoint sets it `COMPLETE` and removes it from `ACTIVE.md`.

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
