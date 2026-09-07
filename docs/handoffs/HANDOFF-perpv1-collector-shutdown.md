# Handoff — turn the PERP-V1 tape collector off

> # ⛔ CLOSED — DO NOT ACT ON THIS BRIEF
>
> **The collector was already off when this was written.** `PERPS_COLLECTOR_ENABLED=false` on
> the main worker since **2026-09-02 13:28:26Z**, turned off by `WS-010`'s own closing session
> and confirmed stopped five minutes later. A Live Ops session ran the brief on 2026-09-07,
> found that at step 1, and correctly made no write. The parked line is gone from
> `ACTIVE.md`; there is no work here.
>
> **The record, with the evidence:**
> [`WS-010` § *The collector is off*](../workstreams/WS-010-perp-v1-pre-registration.md#the-collector-is-off).
>
> This file is kept only because *how the mistake happened* is worth reading: everything below
> the line was written from a workstream's `Next Step` prose that contradicted an ops receipt
> the same workstream had produced four days earlier, and nothing between the two re-checked
> production. `DEC-001` names that failure mode; this is what it looks like at 123 lines.
> **Read what follows as a case study, never as instructions.** The runtime facts in it are
> wrong — including "still `true`" and "~26k rows/day", which was really ~29.5k rows in total.

**From:** kalshi_bot, task-specific session, 2026-09-06 (the Build OS v0.12 migration, PR #358)
**To:** a **Live Ops** session — *picked up and closed 2026-09-07, no action needed*
**Status:** ~~nothing done. This is the brief, not the work.~~ **Closed. The work was already
done before this was written.**
**Board state:** ~~parked~~ **removed from the parking lot 2026-09-07.**

---

## Read this first

*Superseded by the banner above — the item is closed and this section's instruction to seek
the owner's promotion no longer applies to anything. Retained as written.*

This handoff **authorizes nothing**. The item is in the parking lot, and Build OS v0.12 is
explicit that nothing there is work until the **owner promotes it** — no agent starts it
(`DEC-011`, `framework/FINITE_WORK.md`). If you are reading this without the owner having said
"go do the collector shutdown", the correct action is to say so and stop.

It also touches the live worker, and that is the whole reason it is Live Ops rather than
bookkeeping. Read *The hazard* before touching anything.

---

## What this is

`WS-010` (PERP-V1, a research surface for Kalshi perpetual futures) closed on **2026-09-02 on a
COST finding**. All three arms are done: Arm A `FAIL` — premium reversion is real (+5.63
bps/trade pre-fee, 913 obs, against a −10.13 control) and unreachable, because tier-0 taker is
24 bps round trip, 2.7× the whole bid-ask; Arm B `BLOCKED_DATA`, no funding source; Arm C
`NO-GO`, null at 300 s.

**The collector never stopped.** `PERPS_COLLECTOR_ENABLED` is still `true` on the main worker
and the tape is accumulating at **~26k rows/day against three closed arms**. That is ongoing
Postgres spend and request budget bought for a surface with no open question on it.

The experiment was **never registered in production**, so there is no Experiment OS record to
consult — `docs/workstreams/WS-010-perp-v1-pre-registration.md` and `docs/PERP_V1_THESIS.md`
are the record.

## The outcome

`PERPS_COLLECTOR_ENABLED=false` on the main worker, the worker healthy afterwards, and the live
book on it demonstrably still trading.

## The hazard — why this is Live Ops

**Setting `PERPS_COLLECTOR_ENABLED` redeploys the worker, and the worker is where the live
books run.** `scripts/railway_env.py` says so at the allowlist entry, in those words. The
collector itself risks nothing — it places no orders, holds no position, registers no strategy
tag — but the *redeploy it causes* interrupts a process carrying real money.

At the time of writing, **`WS-007`'s mmsell10 Stage-1 live canary is armed on that worker**
(armed 2026-08-28T14:20:35Z; envelope $1/order, 1 contract, $5 daily stop, $15 budget; the
`live_canary_keep` gate stays `BLOCKED_DATA` until 150 settled contracts). **Verify that is
still true before you act** — do not trust this paragraph, it is a copy and copies go stale
(`DEC-001`). Ask Experiment OS.

Consequences to plan around, not to discover:

- A redeploy mid-session means in-flight quotes and any open position on the live book ride
  through a process restart. Pick the moment deliberately.
- A restart that fails to come back leaves the live book down, silently, until someone looks.
  **The verification step below is not optional.**
- This is a **reduction** of activity, not an expansion of exposure, so it does not need the
  real-money confirmation that arming does. It still needs the owner's promotion off the
  parking lot, which is a different gate.

## The decision that may come first

Turning it off is the default and the reason it was parked. **Keeping it is defensible only if
a maker variant of the perp work is actually intended** — Arm A's finding was that the edge is
real and the *taker* cost eats it, so a maker approach is the one live successor thesis. If the
owner wants that, the tape has value and the answer is "keep it, and say why in `WS-010`". If
not, it is pure spend.

That is an owner call. Do not make it by acting.

## Doing it

The Claude sandbox cannot reach Railway or Postgres, so this runs through the ops channel
(`docs/OPS_RUNBOOK.md` is canonical for the mechanism; `CLAUDE.md` carries the minimal recipe).

1. **Read the current state first.** `{"type":"env","id":"perp-off-read-1"}` — confirm
   `PERPS_COLLECTOR_ENABLED` is actually `true` before writing. If it is already `false`,
   somebody did this; update `WS-010` and the parking lot and stop.
2. **Confirm the live book's state in Experiment OS**, not from this document.
3. **Write it:**
   `{"type":"env","action":"set","values":{"PERPS_COLLECTOR_ENABLED":"false"},"id":"perp-off-set-1"}`
   Remember the shared-`ops`-branch push race — retry with rebase, per the recipe.
4. **Verify the redeploy landed and the worker is healthy** — a `logs` request, and re-read the
   env. Do not stop at "the request succeeded"; `DEC-009` is the decision that says a
   production change is verified rather than assumed.
5. **Verify the live book is trading again.** This is the step that catches a bad restart.
6. **Reset the channel** to `{"type":"noop"}`.

## Afterwards

- Record it in `docs/workstreams/WS-010-perp-v1-pre-registration.md` — the file is `CLOSED ·
  Done`, and this is a closing act on it, not a reopening. Do not change its phase or status.
- **Remove the line from `ACTIVE.md` → *Parked*.** A parked line that has been done and left in
  place is how a parking lot becomes a backlog.
- Do **not** open a workstream for this. It is one operational act with a named owner.

## Explicitly not in scope

- **The retrospective Experiment OS registration question.** That is the *other* parked line
  and it belongs to **Experiment Control Tower**, not Live Ops. `docs/OPS_RUNBOOK.md` already
  carries a `CLOSE_OUT_RETROSPECTIVE` envelope shape for package `perp-v1` if that call is ever
  made — its existence is not a decision that it should be.
- **Reopening any PERP-V1 arm.** The verdicts are recorded and are not re-litigated by turning
  off a tape.
- **Deleting the collected tape.** Nobody has asked for that, it is irreversible, and the
  coverage finding (`D5` — nothing sharing the worker's scan loop can meet an 80% coverage
  floor) is inherited by any future high-cadence research and worth keeping the evidence for.
- **Any other env var** in the same write. One variable, one redeploy, one thing to verify.

## Sources

- `docs/workstreams/WS-010-perp-v1-pre-registration.md` — the workstream, its verdicts, and
  the three items that left it
- `docs/PERP_V1_THESIS.md` — the scientific contract
- `scripts/railway_env.py` — the allowlist entry and the redeploy warning, in the code
- `docs/OPS_RUNBOOK.md` — the channel, the retry discipline, the verification requirement
- `docs/DECISIONS.md` — `DEC-009` (a production change is verified, not assumed), `DEC-011`
  (the parking lot, and that no agent starts from it)
