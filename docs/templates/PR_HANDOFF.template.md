# Implementation Handoff

<!-- This PR body is the authoritative handoff. Every section is required; sections with
     nothing to report say `None`. Keep it current if the PR changes. -->

**Workstream:** WS-### · **Build Card:** <link> · **Build Spec:** <link> · **Build OS v0.12**

## Goal

> After this change, the system should <quoted from the Build Card>.

## Implemented

<What behavior actually exists — not what was attempted.>

## Architecture / Flow

<How a request or event moves through the change. Which components participate, where
state changes, what happens on the unhappy path.>

```text

```

## Major Areas Changed

<!-- The shape of the change, not a file dump. The diff already lists every file. -->

- `<area>` — <what changed and why it matters>

## Design Decisions

<Technical decisions made during implementation, with reasoning.>

-

## Spec Deviations

<!-- Any departure from approved behavior or requirements, referencing OD-n / R-n / AC-n.
     Write `None` explicitly when there are none. If unsure whether something counts,
     it counts. -->

None

## Tests / Validation

<What was run, with actual results. What is covered and what is not.>

- `<command>` — <result>
- New tests: <what they cover>
- Not covered: <gaps>

## Known Risks / Limitations

<Concrete. Unhandled edges, behavior under load, assumptions that could prove wrong,
deploy risk.>

-

## Recommended Review Focus

<Where an independent reviewer should scrutinize. Name specific places.>

-

## Framework

<!-- Omit for small PRs. Never record a check that did not happen. -->

Framework:
- Project adopted: v0.x
- Canonical checked: v0.x
- Compatibility: current
<!-- or: upgrade required / Migration performed: <what> -->

## Review Gate

Implementation actor: <stable identifier for the agent or person who implemented this>

Review gate: Pending independent review
<!-- or, in a `solo` project: Solo mode — pending owner acceptance -->

<!--
Written by the implementation agent, which never claims an approval it did not receive.
Until a reviewer records a verdict, this line reads exactly `Pending independent review`.

`Implementation actor` names who did the work, as distinct from the GitHub account that pushed
it — in a single-account repository they are not the same thing, and several actors share one
login. It is what lets a comment verdict be recognised as self-review rather than independent
approval. Omitting it does not make a review look independent; it makes every comment verdict
non-gate-clearing, because independence can no longer be established either way.

**Re-state the head on every push.** `Head at time of writing` is not written once: it is
updated in the same act as pushing, every time, or the handoff quietly starts describing code
that is no longer there. This is the same failure as a verdict drifting from its commit — slower,
easier to forgive, and just as capable of sending a reviewer at the wrong diff. A reviewer who
opens a PR should be able to trust that its body describes the head they are about to read.

Once reviewed, repeat the verdict and the full 40-character head it was reached against:

    Review gate: Approved · reviewed head <40-char SHA>
    Head at time of writing: <40-char SHA> (current)

If the PR has moved since the verdict, say so — the approval is stale.

Before merge, push the **merge-finalization** commit to this same PR: documentation only —
the workstream file, `ACTIVE.md`, `Review State`, and where the workstream completes,
`PROJECT_MODEL.md` and `DECISIONS.md`. It sets them to what becomes true when this PR lands.
Any executable, test, dependency, config, or behavior-documentation change in that commit
reopens full review.

That commit cannot contain its own SHA, so it does not try to: `Reviewed head` keeps naming the
last fully-reviewed commit and gains `Finalization: pushed`. It cannot write the **verdict**
either — the reviewer records that afterwards, or in `solo` mode the owner records it at merge,
so leave the verdict at whatever is true when the commit is authored. Say here that it is pushed; the
reviewer then verifies the head it produced and approves on the PR, and the merge targets that
exact SHA.

This agent does not approve this PR and does not merge it.
-->

## Workstream

<!-- ID, phase before → after, and whether this PR completes it. `None` if not applicable. -->

WS-### — <title>. <PHASE> → <PHASE>. <Completes / does not complete> the workstream.

## Follow-up Work

<Intentional deferrals, each carrying its disposition and reason. Every line is PARK or
DISCARD. An in-scope defect is FIX NOW and belongs in the diff; an owner judgement is a
DECISION result, not a bullet here. A PARK line claims a matching line exists on the board's
parking lot, and that must be true when the PR is opened. `None` is a common, correct answer.
See framework/FINITE_WORK.md.>

- PARK — <one line>. Parked on the board.
- DISCARD — <one line>, and why it is not worth doing.

## Owner Result

<!-- The owner's default reading path, and usually the only section they read. Exactly ONE
     of the three below — delete the other two. Plain language, no file or function names,
     nothing restated from the sections above.

     FOR MOST OF A PR'S LIFE, delete all three and write the no-result form instead:

         Awaiting independent review. Nothing needed from you yet.

         Approved and finalized; awaiting the reviewer's verification of the final head.
         Nothing needed from you yet.

     The three states are terminal, not a running status. First push, the correction loop,
     approved-but-unfinalized, and finalized-but-unverified all still owe work by an agent or
     a reviewer, so none of them has a result. SHIP is written when that work is done — not
     when coding stops, and not when review passes.

     This is the handoff's only owner-facing section. Do not keep an Owner Summary beside it.

     Full rules: framework/OWNER_INTERFACE.md · Template: templates/OWNER_RESULT.template.md -->

### SHIP

Build OS owner result: SHIP

**What changed:** <1–3 plain-language sentences>
**Intent:** <requirements satisfied, or an equivalent concise statement>
**Verification:** <validation + independent review status, in plain language>
**Deviations:** None | <material deviations only>
**Residual risk:** None | <material remaining risk only>
**Next action:** Merge PR #<n> at <verified SHA>

<!-- ~150 words max.

     SHIP means every agent and reviewer step is finished and only the owner's merge remains.
     It reports the merge gate; it does not replace it, and writing one approves and merges
     nothing.

     For SIGNIFICANT work it requires ALL SIX: green validation actually run; no unresolved
     Blocking or Should fix finding; an independent approved verdict; the merge-finalization
     commit pushed; the final head independently verified on the PR; and no undisclosed
     material deviation.

     The last two are yours and the reviewer's. Before they are done there is NO result —
     delete all three blocks and write the no-result form above. A SHIP whose Next action asks
     for anything but the merge is a no-result state wearing the wrong name.

     For SIMPLE work there is no finalization or review to wait on, and Verification names the
     classification too:
       "Simple change — full test suite green. No independent review required under
        proportionality." -->

### DECISION

Build OS owner result: DECISION

**Decision:** <one sentence>
**Why now:** <why implementation or review cannot settle this>
**Options:** <2–4 concise choices>
**Recommendation:** <preferred option and why, where appropriate>
**Impact:** <what changes once chosen>

<!-- Scarce: a choice that changes what users experience, what the business commits to, what
     data is kept or lost, or what becomes hard to reverse. An unresolved owner decision is a
     DECISION, never a caveat inside a SHIP. -->

### BLOCKED

Build OS owner result: BLOCKED

**Blocker:** <one sentence>
**Why agents cannot resolve it:** <plain language>
**Smallest action needed:** <specific owner or external action>
**Work preserved:** <what remains safely completed>

<!-- Scarcer still. Difficulty is not a blocker, and neither is a failing test, a merge
     conflict, or a reviewer finding this agent could fix. -->
