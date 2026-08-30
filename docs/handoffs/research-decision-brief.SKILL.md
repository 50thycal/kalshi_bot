---
name: research-decision-brief
description: Turn a stalled, long-running or tangled research/build thread into a plain-language decision brief for the person who owns it — what is actually blocking, the decision only they can make (costed, with "stop" as a real option), a recommendation, and the results honestly graded. Use this whenever someone asks "where are we", "what's the status", "what's holding this up", "what do I need to decide", "give me the rundown", "explain it plainly / non-technically", "what have we actually learned", or when a thread has produced several rounds of work without a clear answer and you are about to write a status update. Also use it proactively before asking an owner to authorize more spend on an investigation, and whenever you notice you are about to attempt the same class of fix a third time.
---

# Research decision brief

A long investigation accumulates two very different things: facts about **the world**
(does the edge exist? is the drug effective? is the migration safe?) and facts about
**your own instruments** (the API is shallow, the parser drops rows, the test fixture
was wrong). Progress on the second feels like progress and reads like progress, and
after a few rounds the owner can be several sessions deep believing a question is
being answered when it has not yet been asked.

The brief exists to break that. It tells the owner, in their language, what is known,
what is not, what decision is theirs, and what it costs.

## When it is the right move

Reach for this when the thread has gone several rounds without a clean answer, when the
owner asks any variant of "where are we", or — most importantly — **when you are about
to attempt the same class of fix for the third time.** That third attempt is the signal.
It means the thing you keep fixing is probably not the thing that is wrong, and the
person paying for the work should get to weigh in before you spend more of their budget.

It is *not* the right move for a thread that is simply in progress and going fine. A
brief that says "still working, no decisions needed" wastes the owner's attention and
trains them to skim the next one.

## The shape

Five parts, in this order. The order matters: it front-loads the honest headline so an
owner who reads only the first paragraph is still correctly informed.

### 1. The honest headline

Two sentences. State plainly what is known and what is **not**, including the
uncomfortable version.

> "Nothing is broken, and nothing has been decided about your idea yet. We have a
> working scanner and zero data about whether the strategy works."

The instinct is to lead with effort — five runs, four fixes, three PRs. Resist it.
Effort is not a finding. If the answer to "did we learn anything about the actual
question" is no, that sentence goes first, before anything that might soften it.

### 2. What is holding it up

Explain the blocker in the owner's domain, not yours. No function names, no endpoint
paths, no error strings. Analogy is fine when it is *accurate* — a wrong analogy that
lands is worse than a technical sentence that doesn't.

The test: could the owner repeat your explanation to a colleague and be right? If it
needs a class name to make sense, it is not translated yet.

Say how many attempts have gone into this blocker, and how many of those were your own
mistakes versus genuine properties of the world. Owners calibrate on that ratio, and
hiding it corrupts every judgement they make afterwards.

### 3. The decision

One decision if you can manage it. Two if you must. More than two and you are asking
the owner to do your thinking.

Give each option a cost and a downside, in a table if there are three or more. Options
should be genuinely different in kind, not three speeds of the same thing.

**Always include the option to stop.** An owner who does not see "stop" as a listed,
respectable choice will infer that you have already assumed the work continues, and
will find it socially harder to kill something they should kill.

### 4. The recommendation

Give one. A menu without a recommendation offloads a judgement the owner is paying you
to make.

If the evidence has moved since your last recommendation, **say that you are changing
your advice and why.** This is where briefs most often go quietly wrong: consistency
with your own earlier position feels like integrity, and it is the opposite. State the
old advice, the new advice, and the fact that changed.

> "I'd now go with B, and I'm changing my earlier advice. Last time I leaned toward A
> because hand-picking pre-selects the answer. I think that was wrong, and here's why…"

### 5. The results

Everything you can actually put a number on, each one graded for whether it can be
acted upon. Three categories are usually present, and mixing them is how owners get
burned:

- **Leads** — suggestive, under-powered. Give the number, the sample size, and an
  explicit "not enough to act on". If your project has a history of small-sample
  mirages, name it: prior burns are the most persuasive possible framing.
- **Solid findings** — things you now know for certain, even if they are unglamorous.
  "Nine in ten candidates are unusable, and here's the one that proves it" is a real
  result that narrows all future work.
- **The cost bar** — what the thing has to clear to matter. Owners routinely
  under-weight fees, latency, headcount, error rates. State the bar and what it
  disqualifies, ideally as a concrete example of a result that *sounds* good and isn't.

Every number carries its denominator. A percentage without an n is not a result.

## What separates a good brief from a status update

**Distinguish world-facts from instrument-facts, explicitly.** This is the single most
valuable thing the brief does. "We learned the API only exposes recent history" and "we
learned the edge isn't there" feel similar in a changelog and are worlds apart in
decision value. Label them.

**Own your errors in the summary, not just the commit log.** If two of five failures
were your mistakes, that belongs in the brief, because it changes how much the owner
should trust your remaining estimates. It is also the fastest way to earn the latitude
to keep going.

**Never let a thin number sit next to a solid one without a grade.** An owner scanning
a results section will weight everything equally unless you tell them not to. "n=79,
nowhere near enough to act on" costs six words and prevents the whole failure mode.

**Name the cost of not deciding.** Threads left half-open rot: context evaporates,
branches drift, the next session re-derives what this one knew. A sentence on what
"leave it for now" actually costs helps the owner choose deliberately rather than by
default.

**Match the register to the reader.** If they asked for plain language, that governs
everything — including the results section, which is where technical vocabulary creeps
back in. Percentages, sample sizes and dollars are fine; jargon nouns are not.

## Length

Long enough to decide from, short enough to read once. Usually under a page of prose
plus one table. If it is running longer, the usual cause is that you are narrating the
journey rather than stating the position — cut the chronology, keep the conclusions.

The chronology belongs in the durable record (the run log, the journal, the workstream
file). The brief points at that record; it does not reproduce it.

## Before you send

Check the brief against these, since each maps to a way owners get misled:

- Could someone read only the first two sentences and still be correctly informed?
- Is every number accompanied by its sample size and a verdict on actionability?
- Is there exactly one recommendation, and does it survive contradicting your own
  earlier advice if the evidence moved?
- Is "stop" a listed option?
- Have you separated what you learned about the world from what you learned about
  your tools?
- Would the owner's explanation of the blocker, repeated back, be correct?
