# WS-016 — Standing authorizations: one-request paper tapes, hard stops, the closing brief

**Phase:** REVIEW
**Status:** Active
**Created:** 2026-09-12
**Updated:** 2026-09-12
**Build OS:** v0.12

## Goal

A paper-scope request ("I want a new paper tape for X") runs end to end off one message
when it meets the criteria, with the criteria computed rather than asked; real-money and
safeguard actions stay explicit hard stops; long sessions close with a five-line brief.

## Context

Every step of a registration was a separate operator confirmation, and the playbooks did
not distinguish the confirmations that protect real money from the ones that were ceremony
for a paper book. The operator asked for the same five closing questions after every long
session. Decision: `DEC-012`.

## Current Mental Model

```text
request ──► role inferred ──► criteria (xos package-preflight + duplicate/revival read)
                                   │ GO                              │ NO-GO
                                   ▼                                 ▼
             package PR → merge → envelope → receipt → env append   DECISION (one message)
             → readback → BRIEF
   hard stop anywhere in the chain (ARM_CANARY, LIVE_*, KILL_SWITCH, revision activation,
   ops workflow) ──► stop, name the action, wait for words that name it
```

## Decisions Made

- Three tiers (free / request-authorized / hard stop), written in one document every role
  points at — `DEC-012`.
- The merge of a paper-scope PR transcribes the request as the owner's acceptance in `solo`
  mode; never for the hard-stop tier — `DEC-012` §3.
- Preflight is a read on the canonical CLI, allowlisted on the ops channel, not a script —
  so it cannot drift from Experiment OS the way the retired checkers did.

## Open Decisions

- **D1.** The board is at five `Active` rows with this one; the limit is four. Which of
  `WS-004` / `WS-006` / `WS-009` pauses? Recommendation: `WS-009` — its one remaining item
  needs the operator in a browser, not an agent.

## Assumptions

- A paper book cannot place a real order and a registration is reversible by `STAND_DOWN`;
  that is what makes the paper chain request-authorized.

## Non-Goals

- No change to `arm_live_canary`, the transports' vocabulary, the ops channel's read-only
  boundary, or any real-money invariant.
- No automatic scheduled brief (proposed to the operator as a Routine; not created here).

## Acceptance Checks

- [x] `docs/STANDING_AUTHORIZATIONS.md` names the three tiers, the one-request chain and the
  brief; `CLAUDE.md`, the router, the sessions README and every affected playbook point at it.
- [x] `xos package-preflight` exists, is allowlisted, is read-only under test, and prints the
  envelope on `GO`.
- [x] `/session-brief` skill exists and the handoff skill, Build OS and the project model
  name the brief as the default close.
- [x] `tests/test_session_system.py` guards the new surfaces; the full suite is green.

## Build Card

Inline: the Goal, the model above and `DEC-012` are the card; the change is documentation
plus one read-only CLI command.

## Implementation State

PR open (this branch).

## Review State

**Verdict:** Not started
**Reviewed head:** —
**Finalization:** —

Operating mode is `solo`: no independent party will review this. Acceptance is the owner's
at merge.

## Related Decisions

`DEC-012`, `DEC-011`, `DEC-001`.

## Related PRs

This PR.

## Parked

None.

## Next Step

Owner merges; then answer D1 on the board.
