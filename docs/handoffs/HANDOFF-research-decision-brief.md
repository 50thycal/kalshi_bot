# Handoff — add the `research-decision-brief` skill to 50thycal/build-os

**From:** kalshi_bot, Research Lab session, 2026-08-30
**To:** whoever works next in the build-os repo
**Status:** skill drafted and ready; not yet installed anywhere

## What this is

A skill that turns a stalled or tangled research thread into a plain-language decision
brief for the person who owns the work. It was extracted from a real exchange in the
MARKTANGLE thread: five probe runs had produced no answer, and the operator asked for a
non-technical rundown of what was blocking, what they had to decide, and what results
existed. The reply that worked is what the skill encodes.

**Why it belongs in build-os rather than kalshi_bot:** nothing in it is Kalshi-specific.
The failure mode it addresses — an investigation that keeps making progress on its own
instruments while never touching the actual question, and an owner who cannot tell the
difference from a changelog — is generic to any long research or build thread. It is
process, and build-os is where this project's process lives.

## The file

`docs/handoffs/research-decision-brief.SKILL.md` in this repo is the drafted skill,
carried here only so the handoff is self-contained. It is a single `SKILL.md` with YAML
frontmatter and no bundled resources.

## What to do

1. Create `skills/research-decision-brief/SKILL.md` in build-os (or wherever that repo
   keeps skills — follow its existing convention, don't invent one).
2. Copy the file contents across verbatim. Rename it to `SKILL.md`; the
   `research-decision-brief.SKILL.md` name here is only to keep it out of this repo's
   own skill loader.
3. Check the `description` frontmatter against build-os's other skills. It is
   deliberately written to over-trigger rather than under-trigger, since the cost of a
   brief nobody needed is much lower than the cost of an owner discovering three sessions
   late that no progress was made on their question.
4. If build-os has a skills index or README table, add a row.

## Two things worth a second opinion

**The third-attempt trigger.** The skill says to write a brief when you are about to
attempt the same class of fix for a third time. That number is drawn from one thread —
in MARKTANGLE, attempt three was roughly where the pattern became visible and where
stopping would have saved real effort. It may want to be two in a faster-moving repo.
Worth calibrating against build-os's own history rather than inheriting it unexamined.

**Overlap with existing process docs.** If build-os already has guidance on status
reporting or escalation, this should reference it rather than restate it — the
authority-boundary rule in this project applies to process documents too. Check before
adding.

## What good looks like

An owner reads the brief once, understands what is blocked and why, makes one decision,
and does not have to ask a follow-up question to find out whether anything was actually
learned.
