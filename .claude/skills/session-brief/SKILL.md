---
name: session-brief
description: Render the five-line closing brief — what happened, is the original goal complete, blockers, decisions for the operator, next steps — in plain non-technical language. Use at the end of any long work session as the DEFAULT closing message, and whenever the user asks "what happened", "where are we", "is it done", "what do you need from me", or invokes /session-brief. Never answer those questions with a long technical explanation.
---

# Session brief

The operator's standing question after every long session is: *what happened, is the
original goal complete, what are the blockers, what do I need to decide, what happens next
— briefly.* This skill makes that the default closing shape (`docs/STANDING_AUTHORIZATIONS.md`
→ *The closing brief*, `DEC-012`). It replaces the narrative, not the durable record: the
PR body, the workstream and Experiment OS still carry the detail, and the brief links to them.

## Shape

Five headings, in this order, one to three short lines each. Plain words. No code, no
identifiers the operator did not use, no numbers unless one changes the decision.

```text
WHAT HAPPENED   — the outcome, in one or two sentences
GOAL            — DONE / PARTIAL / NOT DONE — one clause on what is missing, if anything
BLOCKERS        — "none", or each with its NAMED unblocker (never "waiting")
DECISIONS       — what only the operator can decide, each with a one-line recommendation
NEXT STEPS      — one or two actions, and who does each (you / operator)
```

Then, on one line, the links: PR, workstream, XOS objects touched. Nothing after that.

## Rules

- **Outcome first.** If the goal is not done, the first line says so.
- **A hard stop is a DECISION line**, stated as the exact action and its consequence
  ("arm the canary — real money, $15 budget"), with a recommendation.
- **Blockers name an unblocker** that is a person, a merge, a receipt or a measurement.
- **Under 120 words** for the five sections. If it needs more, the detail belongs in the
  PR or workstream and the brief should link to it.
- **Never restate Experiment OS state** as fact from memory — say what the last read
  showed and when (`DEC-001`).
- **This is chat.** It is not the Owner Result (that lives on the PR) and it is not the
  session identity header (that opens a session). It never carries either.

## Example

```text
WHAT HAPPENED   Registered the Rmmsell paper tape end to end: package merged, envelope
                consumed, both tags live on an active arm, books appended to production.
GOAL            DONE.
BLOCKERS        none.
DECISIONS       none — the gate floor is 60 settlement days; nothing to decide until then.
NEXT STEPS      you: nothing. me: Control Tower check tomorrow for a silent arm.
PR #401 · WS-017 · mmsell-reviewed-universe/v1/e1
```
