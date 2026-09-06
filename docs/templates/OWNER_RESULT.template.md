# Owner Result

<!-- The owner's default reading path. Exactly ONE of the three states below — delete the
     other two. A result that is really two states is a DECISION or a BLOCKED with finished
     work described inside it, never a SHIP with a question attached.

     Generated from current durable state at the moment it is written. Every claim here is
     one some durable artifact already supports.

     There is often no result at all, and that is correct. The three states are terminal, not
     a running status, and everything before the gate's last step is mid-flight: awaiting
     review, in the correction loop, approved with finalization unpushed, or finalized with the
     final head unverified. Say so plainly and carry no marker —

         Awaiting independent review. Nothing needed from you yet.

         Approved and finalized; awaiting the reviewer's verification of the final head.
         Nothing needed from you yet.

     DECISION and BLOCKED are not held back this way — they are reachable at any point,
     because they are the cases where the owner does have something to do.

     A short summary may omit detail. It may not omit material truth.

     SHIP means no hidden tail. Optional follow-ups do not block it and do not travel inside
     it: they are PARKed on the board, DISCARDed, or brought back as a real DECISION. See
     framework/FINITE_WORK.md. -->

**Workstream:** WS-### · **PR:** #<n> · **Date:** YYYY-MM-DD · **Build OS v0.12**

---

## SHIP

Build OS owner result: SHIP

**What changed:** <1–3 plain-language sentences>
**Intent:** <requirements satisfied, or an equivalent concise statement>
**Verification:** <validation + independent review status, in plain language>
**Deviations:** None | <material deviations only>
**Residual risk:** None | <material remaining risk only>
**Next action:** Merge PR #<n> at <verified SHA>

<!-- 100 words or fewer by default; 150 is the ceiling, not the target.

     Lead with the OUTCOME, not with what was attempted or the order it happened in. Plain
     language, no file, branch or function names. Say whether the original ask is done.

     The extra 50 words exist for one reason: a material deviation or residual risk that takes
     a sentence to state properly. A deviation dropped to make the budget is a compressed lie.

     SHIP means every agent and reviewer step is finished and only the owner's merge remains.
     It reports the merge gate; it does not replace it, and writing one approves and merges
     nothing: the agent that wrote the code neither approves nor merges it.

     For SIGNIFICANT work, SHIP requires ALL SIX:
       1. required validation green, and actually run
       2. no unresolved Blocking or Should fix finding
       3. an independent verdict of Approved / Approved with follow-ups
       4. the merge-finalization commit pushed
       5. the final head independently verified by the reviewer, on the PR
       6. no undisclosed material deviation from approved behavior

     4 and 5 are the ones this list exists for. After approval a documentation-only commit is
     still owed, and after that commit the reviewer still has to verify the head it produced —
     both agent-and-reviewer work the owner cannot do. Before 5 holds there is NO terminal
     result: not a SHIP with a caveat, not a SHIP whose Next action names the outstanding step.
     Write the no-result form instead.

     Next action on a significant-work SHIP is the merge and nothing else, naming the verified
     SHA — the commit the reviewer verified and the one the merge must target.

     For SIMPLE work there is no finalization or review to wait on; conditions 3–5 do not
     apply. Verification names the classification as well as the validation:
       "Simple change — full test suite green. No independent review required under
        proportionality."
     That sentence is what makes a misclassification visible to the owner. -->

---

## DECISION

Build OS owner result: DECISION

**Decision:** <one sentence>
**Why now:** <why implementation or review cannot settle this>
**Options:**
- **<Option A>** — <consequence>
- **<Option B>** — <consequence>
**Recommendation:** <preferred option and why, where appropriate>
**Impact:** <what changes once chosen>

<!-- Scarce. For a choice that changes what someone using the system experiences, what the
     business commits to, what data is kept or lost, or what becomes hard to reverse.

     Also where an OWNER DECISION disposition lands (framework/FINITE_WORK.md) — a finding an
     agent may not settle alone. Same scarcity bar: routing a parkable finding through a
     disposition table does not earn it the owner's attention. Where the work asks for more of
     the owner's time, money or attention, list stopping as an option.

     NOT a DECISION: a failing test, a merge conflict, a reviewer finding the implementation
     agent can fix, naming, schema shape, library choice, an ordinary engineering trade-off.
     Those stay inside the agent loop.

     Do not bundle unrelated choices into one DECISION unless they are genuinely coupled. -->

---

## BLOCKED

Build OS owner result: BLOCKED

**Blocker:** <one sentence>
**Why agents cannot resolve it:** <plain language>
**Smallest action needed:** <specific owner or external action>
**Work preserved:** <what remains safely completed>

<!-- Scarcer than DECISION. A missing credential or authority, an unavailable external
     dependency, an action outside the agents' permission, or a conflict that cannot honestly
     be reduced to a choice the owner could just make.

     Difficulty is not a blocker. An agent reaching for BLOCKED because the work got hard has
     mislabelled its own problem as the owner's.

     Work preserved is not optional: without it the owner assumes the worst and the next
     session redoes what was already finished. -->

---

<!-- The final chat response is one or two lines plus a pointer to this result. Never a
     second copy of it. -->
