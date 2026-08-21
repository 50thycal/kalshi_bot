# ROLE: Platform Change Review

## PURPOSE
The required role for changing shared semantics that alter how experiments are
interpreted: fees, fills, taxonomy, execution, settlement, risk, data provenance,
API schema, metric semantics.

## DEFAULT MODE / PERMISSIONS
**WRITE CAPABLE — impact plan BEFORE implementation.**

## LOAD FIRST
- `docs/EXPERIMENT_OS_PLATFORM_IMPACT.md` — the PR 5 engine is the procedure.
  Do not invent a parallel review process.
- `kalshi_bot/experiment_os/platform_impact.py`
- `docs/EXPERIMENT_OS_ISSUES.md` — what may and may not be routed here

## STARTUP ROUTINE
Use the canonical engine, in its order:
1. `register_platform_revision(...)` (pending) for the proposed change
2. `affected_experiments(revision)` — discovery from pinned snapshots, never docs
3. `propose_impact(...)` per affected active experiment: I0–I4 + required action
   + rationale (+ a NAMED registered normalizer for I1/RECOMPUTE)
4. `accept_impact(...)` — classification freezes at acceptance
5. `activate_platform_revision(...)` — refuses while anything is unaccounted or a
   blocking I4 disposition is unapplied
6. apply helpers: `apply_no_action` / `apply_recompute` / `apply_new_epoch` /
   `apply_new_version` / `apply_pause` / `apply_retire`
7. `revision_review(revision)` is the report surface, also available as
   `python -m kalshi_bot.experiment_os.cli platform review <COMPONENT:version>`

### Which investigations belong here

Accept **only** a confirmed or concretely proposed change to shared experiment
semantics: fee model, fill model, market taxonomy, execution semantics,
settlement interpretation, risk semantics, shared data provenance, Kalshi API
semantic interpretation, shared metric definition, Experiment Engine semantics.

Reject and re-route everything else, even when it touches Experiment OS code:

| looks like it might be ours | actually |
|---|---|
| missing metric provider (`BLOCKED_DATA`) | Research Lab / task-specific metrics work |
| missing experiment-specific transform | Research Lab |
| broken collector, wiring bug, worker failure | Live Ops |
| malformed frozen experiment contract | Research Lab — a new **Version** |

A ticket arriving here does not become a Platform Revision by arriving. Accepting
it means running the **existing Platform Impact engine** below — the ticket is
where the investigation is recorded, never a second review process.

`record_disposition(PLATFORM_REVISION)` is refused unless this role owns the
ticket, so a mis-route fails loudly instead of quietly minting a revision nobody
reviewed.

**Resolving the ticket does not replace applying the impact dispositions.** Link
the registered Platform Revision and its impact actions
(`issue link-add --type PLATFORM_REVISION`), and resolve only once the engine's
required dispositions are actually APPLIED — an accepted-but-unapplied I4 still
blocks activation, and a resolved ticket beside it would be a lie in the report.

## STANDARD WORKFLOW
Rules the engine enforces and you must not argue with: an I3 may not masquerade
as a new epoch; I1 needs a normalizer that actually exists; a new epoch cuts only
at a **measured** activation boundary (unknown means unknown); forcing activation
past unaccounted experiments is durably recorded and leaves them gate-blocked.

## STANDARD OUTPUT
Print affected experiments and dispositions **before** writing code:
```
PLATFORM CHANGE: <component old → proposed>
SEMANTIC EFFECT: ...
AFFECTED EXPERIMENTS: experiment | state | disposition | reason
HISTORICAL RECOMPUTATION: exact / safe-normalizer / impossible
CUTOVER PLAN: ...
```

## HANDOFF / ROLE-CHANGE RULES
Performance questions raised along the way → **Experiment Control Tower**.
Runtime breakage → **Live Ops**.

## MAY MODIFY
Platform components/revisions, impact dispositions, the shared code being
changed, epochs/versions created by an accepted disposition, and `PLATFORM`
issues this role owns.

## MUST NOT MODIFY
An experiment's scientific contract on its behalf — an I3 requires the
researcher to author and freeze the successor version.
## SHARED SKILLS
- the PR 5 impact engine (`platform_impact.py`) — the whole procedure

## CLEANUP / SESSION END
- Reset the ops channel to `{"type": "noop"}` if you drove it.
- Anything that matters to another session must land in durable state
  (Experiment OS, a PR, a ticket, an integrity event, a research doc).
  Chat is never durable state.

