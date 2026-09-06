# WS-### — <Title>

<!-- One meaningful design/build thread. Readable in two minutes at any phase.
     Update the changed sections at meaningful checkpoints — not after every exchange. -->

**Phase:** IDEA
<!-- IDEA · EXPLORE · MODEL · DECIDE · BUILD_CARD · READY_TO_BUILD · BUILDING · REVIEW · COMPLETE -->
**Status:** Active
<!-- Active · Paused · Blocked · Abandoned -->
**Created:** YYYY-MM-DD
**Updated:** YYYY-MM-DD
**Build OS:** v0.12
<!-- The protocol this workstream runs under. Omit to inherit the project's adopted version.
     From v0.5 this is what puts the workstream under the merge gate — never the presence of the
     review fields below, which would make the gate escapable by deleting them. -->

## Goal

<What outcome are we trying to produce? One or two sentences.>

## Context

<Why does this workstream exist? What prompted it, what is wrong today.>

## Current Mental Model

<!-- The highest-value section in this file — it is what a new conversation reads to become
     useful. Prefer a diagram, state flow, or concise systems description. Keep it current;
     a stale model is worse than none. -->

```text

```

## Decisions Made

<!-- Owner-level decisions already resolved, with one line of reasoning each. -->

-

## Open Decisions

<!-- Questions still requiring product/design judgment. This is what makes the workstream
     resumable — write them down before the session ends. -->

- **D#.** <Question?> <options / consequences / recommendation>

## Assumptions

<!-- Re-read these at every checkpoint. A falsified assumption is usually why a design
     stopped making sense. -->

-

## Non-Goals

<What this effort deliberately does not cover.>

-

## Acceptance Checks

<!-- What must be true for this workstream to be finished, written so someone other than its
     author could tell whether it holds. Together with Goal and Non-Goals this is the mission
     contract (framework/FINITE_WORK.md): outcome, exclusions, material interrupt risks, and
     the finish condition.

     Write these when the workstream is established. Checks invented at the end describe where
     the work stopped, not what it was for. -->

-

## Build Card

Not ready.

<!-- Before the BUILD_CARD phase, leave as `Not ready`. From then on, either inline the
     current Build Card or link to it. -->

## Implementation State

None.

<!-- None / spec ready / building / PR reference / merged -->

## Review State

**Verdict:** Not started
**Reviewed head:** —
**Reviewed PR:** —
**Finalization:** —

<!--
Verdict: Not started | In review | Changes required | Approved | Approved with follow-ups
         | Owner-accepted

Owner-accepted belongs to `solo` projects only — those declaring that no independent actor
exists. It records that the OWNER accepted a change nobody else reviewed, names its head in
`Accepted head` rather than `Reviewed head`, and is never counted as an approval. Only the
owner writes it; an agent writing it would be approving its own work under another name.
See framework/REVIEW_PROTOCOL.md -> Operating modes.
Reviewed head: the full 40-character SHA of the last commit reviewed IN FULL, or — if none.
An abbreviation is not accepted: it cannot prove which commit was reviewed.
Reviewed PR: which PR this verdict is about. Omit when the workstream has one PR.
Finalization: `pushed` once the documentation-only merge-finalization commit is on the PR.

Approved without a reviewed head does not clear the merge gate.
If the PR head moves after approval and finalization is not declared, the approval is stale —
re-review the new head.

A finalization commit cannot contain its own SHA, so Reviewed head never names it. Keep the
last fully-reviewed head here, set Finalization: pushed, and let the reviewer record the final
head on the PR itself.

A finalization commit NEVER writes a verdict it does not yet have. The reviewer records it
afterwards (`reviewed` mode), or the owner records it at merge (`solo` mode) — either way, after
the commit exists. Leave the verdict at whatever is true when you write it, exactly as you leave
Reviewed head at the last head reviewed in full. A row briefly behind is a far smaller problem
than a row confidently wrong, and only one of the two can be spotted by reading it.

A workstream spanning several PRs uses one row per PR instead of the fields above — a verdict
never applies to a PR it does not name:

| PR | Verdict | Reviewed head | Finalization |
|---|---|---|---|
| #84 | Approved | <40-char SHA> | pushed |
| #91 | In review | — | — |

Findings and follow-ups go below, in prose.
-->

Not started.

## Related Decisions

<!-- DEC-n IDs from DECISIONS.md. Link rather than restate. -->

None yet.

## Related PRs

None yet.

## Parked

<!-- Deferred candidates from this workstream, one line each — the PARK disposition. At most
     three on a completed workstream. Mirrored on the board's parking lot in ACTIVE.md.

     Not a backlog: nothing here is scheduled, and an agent may not start any of it. The owner
     promotes a candidate, or it stays where it is. Anything an acceptance check depends on was
     never parkable — it is FIX NOW, in this workstream. -->

None.

## Next Step

<!-- The single most useful next action. One action, not a plan. If it takes three
     sentences, something unnamed is blocking this.

     On a COMPLETE workstream this is `None.` and nothing else — never "open a ticket",
     "start the next job" or "follow up later". A completed mission holds no tail; deferred
     ideas live in Parked. See framework/FINITE_WORK.md. -->

<next action>
