# Standing authorizations — what a request authorizes, and where a session stops

`DEC-012`. This document exists because the operating system was correct and slow: every
step of a paper-tape registration was a separate confirmation, and a request that met every
criterion still took five replies to finish. The fix is not fewer checks. It is that **the
request is the approval for every step inside its scope**, the criteria are computed rather
than asked, and a session asks a question only at a **hard stop** or when a criterion fails.

Nothing here weakens a live safeguard. Every hard stop below is the same real-money rule
`CLAUDE.md` already carries; this document names what sits on the other side of it.

## The three tiers

| Tier | Meaning | Examples |
|---|---|---|
| **Free** | Do it; never ask; report it in the brief | Reads of any kind, `xos` reads, the ops channel, tests, branches, commits, PRs, `PARK`/`DISCARD` dispositions, opening or advancing an XOS issue this role owns, scratch files |
| **Request-authorized** | The operator's request *is* the approval, provided the criteria below hold. Proceed through the whole chain and report once | `REGISTER_PACKAGE` for a paper contract; appending a **paper** book to `MMSELL_VARIANTS`; merging the session's own paper-scope PR once CI is green; `STAND_DOWN`/`RETIRE_ON_GATE_FAIL` **when the request named that experiment and that outcome**; recording an evaluator run (`EXPERIMENT_OS_EVALUATE_GATES`) |
| **Hard stop** | Stop, state the exact action and its consequence, wait. Never inferred from a request, never batched into one | `ARM_CANARY`; any write to `LIVE_STRATEGIES`, `LIVE_ENABLED`, `BOT_MODE`, `KILL_SWITCH`, `LIVE_MAX_ORDER_DOLLARS`, `MAX_ORDER_SIZE`, `MMSELL_LIVE_*`; any change that **expands real-money exposure** or weakens a live safeguard; a Platform Revision activation; an edit to the `ops` workflow file; a global mmsell knob that re-scopes other books (`live-paper-parallel` §3b); a merge whose diff touches the arming path, a live safeguard, a gate on a live book, or the ops runner |

A hard stop is answered by the operator in that session, in words that name the action.
"Go ahead" after a block that names `ARM_CANARY` is a confirmation; a request that merely
implies arming is not.

## What "meets the criteria" means

For a **new paper tape**, the criteria are mechanical and `xos package-preflight <package>`
computes them:

1. the package is reviewed code on the default branch with a `register` verb;
2. the experiment is not `RETIRED` (terminal), and if it exists the package registers a
   successor version rather than re-registering;
3. no declared strategy tag is carried by another experiment's deployment arm;
4. a complete active platform snapshot exists;
5. no experiment command is mid-execution.

Plus the two research criteria the preflight cannot compute and the Research Lab playbook
owns: **not a duplicate of an open experiment, and not a revival of a killed family without
a mechanically new premise.** The session states both in one line each, with the Control
Tower read they came from.

`GO` on all seven → proceed without asking. Any failure → **`DECISION`**, with the failing
criterion, what would clear it, and a recommendation. A session never argues a criterion
into passing and never patches one in flight.

## The one-request paper tape

The whole chain, in order, from "I want a new paper tape for X" to a trading book. Ask at
none of these steps; report at the end, or at a failed criterion.

```text
 0  role: Research Lab (inferred from the request — say so in the header)
 1  Control Tower read → duplicate/revival check (the two research criteria)
 2  thesis doc + package module (arms, gate, tags as literals) + tests + package entry
 3  PR, CI green, PR body is the handoff; merge  ← request-authorized for paper scope
 4  xos package-preflight <package> → GO   (runs default-branch code, so after the merge)
 5  send the printed envelope; approved_by = the operator's name; reason = the request
 6  receipt terminal (SUCCEEDED) → xos show <key>: every tag on an ACTIVE arm
 7  runtime step if the package declares activation_vars: read the LIVE value, append the
    new paper book(s), set — never retype MMSELL_VARIANTS (the 2026-09-05 silent-arm defect)
 8  readback (enforcement, readiness) and, after SILENT_ARM_HOURS, control-tower: no silent arm
 9  closing brief (below)
```

Steps 3 and 7 are the two that used to be asked. Under this document they are authorized by
the request **because the scope is paper**: the transport arms nothing, the env append adds
a book that cannot place a real order, and both are reversible by `STAND_DOWN` or by removing
the entry. The moment any step would touch a hard-stop item, the chain stops there.

**`approved_by` names the operator, not the agent.** The request came from a person; the
receipt records that person. The `reason` field carries the request in one line so the
audit row explains itself a year later.

**Merging is the owner's acceptance, transcribed.** In `solo` mode (`DEC-011`) an agent never
writes `Owner-accepted` on its own authority. Under this document the originating request
is the acceptance the agent transcribes: the PR body's owner result names the channel
("standing authorization, paper scope, request in session <url>") and states, as always,
that no independent party reviewed the change. A merge is **never** transcribed as an
acceptance for anything in the hard-stop tier.

## The same principle for the other roles

- **Live Ops** — reducing exposure was already free under kill-switch semantics. Expanding it
  is a hard stop and stays one. Collector restarts, log pulls, evaluator runs and issue
  writes this role owns are request-authorized.
- **Platform Change Review** — `register_platform_revision` (pending), `affected_experiments`,
  `propose_impact` and `accept_impact` are request-authorized when the request named the
  change; `activate_platform_revision` is a hard stop, because it moves every pinned
  experiment.
- **Evo Ticket Workshop** — triage, reject, close and implement are request-authorized;
  raising the fleet's LLM ceiling is a budget decision and a hard stop.
- **Control Towers** stay read-only. Nothing here grants a read-only role a write.

## Role inference

The role menu is asked only when the request is ambiguous. A request that names a paper
tape, a probe or a thesis is Research Lab; one that names an incident, a collector or real
money is Live Ops; one that names a fee, fill, taxonomy or metric semantic is Platform
Change Review; one asking what is running is the Control Tower. The session **states the
inferred role in its identity header** and proceeds; the operator corrects it in one word if
it is wrong. The role stays sticky either way.

## The closing brief

After any long work session, or whenever the operator asks "what happened", the default
closing message is the brief — not a technical narrative. Five headings, one to three short
lines each, nothing else:

```text
WHAT HAPPENED   — the outcome in plain words
GOAL            — DONE / PARTIAL / NOT DONE, one clause on what is missing
BLOCKERS        — none, or each with its named unblocker
DECISIONS       — what only the operator can decide, each with a recommendation
NEXT STEPS      — the one or two actions that follow, and who does them
```

The detail lives where it is durable: the PR body, the workstream, the XOS objects. The
brief links to them; it does not repeat them. `/session-brief` renders it on demand.
