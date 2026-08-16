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
changed, epochs/versions created by an accepted disposition.

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

