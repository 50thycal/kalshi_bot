---
name: evo-ticket-triage
description: Triage the evolutionary fleet's capability-request queue (evo_tickets) — read what the agents have asked for, action or reject each request, and CLOSE the ones already delivered so the queue reflects reality. Use when the user asks to "check the bot requests", "what are the bots asking for", "triage the tickets", "review the ticket queue", or on a schedule; and ALWAYS as the last step after shipping any capability an agent requested.
---

# Evo ticket triage — read what the fleet asked for, then close the loop

The agents file capability requests (`submit_ticket`) when they hit something they cannot do.
That queue is the **only channel where the fleet sets the agenda instead of the operator**, so it
is the highest-signal input we have about what to build — and it is worthless if nobody reads it
or closes it.

**Why this skill exists.** For the queue's entire life there was no resolution path at all:
`EvoTicket.status` carried `open|in_review|approved|rejected|implemented|duplicate` and nothing
ever wrote it. The fleet filed ~25 tickets asking for an off switch between 2026-07-31 and
2026-08-08; `deactivate_strategy` shipped 2026-08-08; five days later every one of those tickets
was still `open`, burying the four live requests (a CPI backtest corpus) under delivered ones. A
write-only queue actively misleads: it made the fleet look like it was begging for something it
already had.

**North star (`CLAUDE.md`):** $100/month realized. A ticket is worth actioning when it unblocks an
agent-generated *edge* hypothesis. Requests for more comfort, more dashboards, or more permissions
are not automatically worth building — reject them explicitly rather than leaving them open.

---

## The loop

### 1. Read the queue (ops channel, read-only)

```jsonc
{"type":"db","id":"tickets-<slug>","max_rows":80,"sql":"select category, status, count(*) as n, min(created_at)::date::text as first_asked, max(created_at)::date::text as last_asked, left(string_agg(distinct capability, ' | '), 700) as capabilities from evo_tickets group by category, status order by n desc"}
```

Then drill into whatever looks live (never trust the rollup alone):

```jsonc
{"type":"db","id":"tickets-detail","max_rows":60,"sql":"select id, created_at::date::text as d, category, urgency, left(capability,120) as capability, left(problem,300) as problem from evo_tickets where status in ('open','in_review') order by created_at desc"}
```

Supporter counts matter — `review_queue()` orders by them, and N agents converging on one request
is far stronger evidence than one agent asking once.

### 2. Classify every open ticket into exactly one of four

- **ALREADY SHIPPED** → it should have auto-closed. If it didn't, the registry is missing an
  entry: add one (step 4). Do not hand-close it and move on — the next phrasing will slip through.
- **WORTH BUILDING** → an agent-generated hypothesis with a concrete data or capability ask.
  These are the point of the queue. Build it, then close it with what shipped.
- **REJECT** → conflicts with an invariant (PAP-4 paper-only, no real orders), or is not worth the
  build. Close it as `rejected` with the *reason*, so the fleet stops re-filing it.
- **GENUINELY PENDING** → real but not now. Leave open, and say in the report what it is waiting
  on. This should be a short list; anything sitting here for weeks is really a REJECT you haven't
  admitted to.

Read the request as a hypothesis, not a feature ask. "Add settled CPI corpus as a backtest
dataset" is an agent telling you it has a non-weather thesis and naming the data it needs.

### 3. Close what you resolved

The ops DB role is **SELECT-only**, so you cannot close tickets with a query. Closure runs in
worker code — `tickets.resolve_ticket(session, id, status=..., decision=..., result=...)`:

- `status="implemented"` + `result=` what actually shipped (name the action or dataset, and when).
- `status="rejected"` + `decision=` why, in terms the fleet can act on.

A closed ticket without a reason is silence with extra steps. Safety valve: `find_duplicate` only
matches `open|in_review|approved`, so a wrongly-closed request can be re-filed as a NEW ticket
rather than being folded into the closed one.

### 4. Whenever you ship a requested capability, add a registry entry

This is the step that keeps the queue honest without anyone remembering. In
`kalshi_bot/evo/tickets.py`:

```python
ShippedCapability(
    action="deactivate_strategy",        # must be in constitution.PERMITTED_ACTIONS
    shipped_on="2026-08-08",
    note="shipped as the `deactivate_strategy` action; ...",
    all_of=(frozenset({"deactivate", "deactivation", ...}),
            frozenset({"strategy", "strategies"})),
)
```

`auto_resolve_shipped()` runs every cycle in the orchestrator and closes matching open tickets.

**Match conservatively.** A wrong auto-close silently destroys an agent's request; being too shy
only leaves a ticket for a human to read. Cover the phrasings the fleet *actually used* (read them
out of the queue — they will include plurals, nominalizations like `strategy_deactivation`, and
prose like "Ability to deactivate an active strategy"), and make sure adjacent requests that merely
share a word (`strategy_execution`, `strategy_management`) do **not** match. Add a test with the
real phrasings, and one asserting the near-misses survive —
`tests/test_evo_ticket_resolution.py` is the pattern.

Every registry entry names an action that must genuinely exist in `PERMITTED_ACTIONS`; a test
enforces this, because an entry pointing at a non-existent action closes tickets for something
agents still cannot do.

### 5. Report

Lead with **what the fleet is asking for that we haven't built** — that is the decision-relevant
part. Then: what auto-closed, what you closed by hand and why, what you rejected and why, and
what remains genuinely pending. Give counts before prose.

If nothing is actionable, say so plainly rather than manufacturing work — a quiet queue after a
build wave is the system working.

---

## Guardrails

- **Never close a ticket you did not resolve.** "Old" is not "done". If it is not worth building,
  reject it with a reason — that is a decision, not a cleanup.
- **PAP-4 is not negotiable.** Requests for live order placement / real money get `rejected` citing
  the invariant, every time, however many supporters they gather.
- **Don't let the queue become the roadmap by default.** Agents ask for what unblocks them, which
  is not always what earns money. Weigh each against the $100/month north star.
- **Read the queue before proposing research.** If you are picking hypotheses while agent-generated
  ones sit unactioned, you are doing the fleet's job and ignoring its output.
