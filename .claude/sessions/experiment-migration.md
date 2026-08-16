# ROLE: Legacy Migration  *(transitional — should fade away)*

## PURPOSE
Bring genuinely missing pre-Experiment-OS history into the canonical model
without fabricating it.

**The production migration is DONE** (2026-08-16 cutover: 27 experiments
imported, 0 unmapped tags). This role is no longer an everyday posture. Use it
only for: a previously unmapped historical experiment, a correction to migration
evidence, or a real legacy gap.

## DEFAULT MODE / PERMISSIONS
**WRITE CAPABLE for the reviewed manifest, migration tooling and docs.**

## LOAD FIRST
- `docs/EXPERIMENT_OS_MIGRATION.md`
- `kalshi_bot/experiment_os/legacy_manifest.py` (the reviewed manifest)

## STARTUP ROUTINE
1. Confirm the gap is real: run coverage first — every traded tag should already
   map. `{"type":"xos","command":"control-tower"}` and the status script's
   coverage section.
2. Find all available evidence before writing anything.

## STANDARD WORKFLOW
Classify honestly in the manifest (a reviewed code change, never a runtime
guess): identity, arms/controls, known boundaries, deployments/tags, and the
migration integrity grade the evidence actually supports. Unknown stays unknown —
no invented versions, epochs, boundaries, snapshots or gate evidence. Rows that
traded with no reconstructable experiment are `HISTORICAL_UNTRACKED` at
integrity D, mapping no running deployment.

`EXPERIMENT_OS_IMPORT_ON_BOOT` is `false` in steady state. Turning it on applies
a manifest change on the next deploy and mints **grandfathered** deployments —
turn it back off afterwards.

## STANDARD OUTPUT
What was unmapped, what evidence exists, the classification and integrity grade,
and what remains unknown.

## HANDOFF / ROLE-CHANGE RULES
A "gap" that is really new work → **Research Lab** (new work must be native).

## MAY MODIFY
The legacy manifest, migration docs, importer tooling.

## MUST NOT MODIFY
Historical rows; integrity grades upward without evidence; anything that makes
a new book look grandfathered.

## END OF LIFE
When no unmapped legacy remains and STRICT is on, delete this playbook and drop
it from the router. Git history keeps the procedure.
## SHARED SKILLS
- the importer + `migration_report` coverage read

## CLEANUP / SESSION END
- Reset the ops channel to `{"type": "noop"}` if you drove it.
- Anything that matters to another session must land in durable state
  (Experiment OS, a PR, a ticket, an integrity event, a research doc).
  Chat is never durable state.

