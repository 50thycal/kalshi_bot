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
(a taxonomy repair in review, a paper design blocked behind it, two live-canary gates with
no path to a verdict) and their relationship existed only in one person's memory.

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

- **D1.** Should `docs/BOOK_REGISTRY.md` and the thesis documents be indexed from
  `PROJECT_MODEL.md`, or left discoverable only through `CLAUDE.md`? Leaving them out keeps
  the model architectural; pulling them in risks the model becoming a catalogue. Deferred
  until the board has been used for a few weeks and the actual friction is known.
- **D2.** What is the checkpoint cadence in practice for a project whose sessions are
  role-bounded and often read-only? Build OS assumes a design agent that can write. A
  read-only role here must hand its checkpoint to a writing role as a repository-update
  block. No cadence has been exercised yet.

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

Awaiting independent review. Worth scrutinising: whether the authority boundary in
`DEC-001` is stated tightly enough to survive contact with a session that wants to paste a
gate read into a workstream, and whether `PROJECT_MODEL.md` has stayed architectural.

## Related Decisions

- `DEC-001` — Adopt Build OS v0.4, and fix the Build OS / Experiment OS authority boundary.

## Related PRs

- Build OS v0.4 adoption (this workstream's PR).

## Next Step

Operator review of the adoption PR.

---

## Appendix — efforts considered and deliberately not made workstreams

Recorded so the next session does not re-derive the judgment, and so the omissions are
visible rather than silent. Inventory source: read-only `xos control-tower`, 2026-08-24.

| effort | why not a workstream |
|---|---|
| **XOS-000004** — series-addressed books can see zero markets silently | Its fix has shipped and it is in `VALIDATING` with LIVE_OPS. Operational validation inside XOS's own loop; a workstream would duplicate the ticket, not add a design thread. |
| **`theta-tail-sell` successor A/B** | The experiment is `PAUSED` and the successor is documented as *not ready to register*. No active design work; a workstream would be a bookmark. |
| **Retired experiments** (`mmsell-first-cohort`, `mmsell-scan-depth`, `pin15`, `wcprop`, `xgame`, weather families, …) | Concluded. History lives in Experiment OS and thesis docs. |
| **Grandfathered paper books currently accruing evidence** (`mmsell-price-ceiling`, `mmsell-variants-2026-07`, `mmsell-anchor-*`, `mmsell-wide-control`, `mmsell-type-tight`) | Running experiments, not design threads. Their state is Experiment OS's to report and would be exactly the mirrored standing `DEC-001` forbids. |
