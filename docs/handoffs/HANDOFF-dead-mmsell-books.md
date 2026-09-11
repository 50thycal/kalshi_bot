# Handoff — five mmsell books are configured and cannot trade

**Scope: one finite investigation.** Classify five tags, then act on what the classification
says. Not a licence to re-register anything.

Found 2026-09-11 by a task-specific session doing unrelated work (the reviewed-universe tapes,
#383/#387). Surfaced by a worker redeploy, **not caused by it** — see *Not caused by the deploy*.

> **No real money in the investigation itself.** Reading is free. Two of the five tags are
> LIVE-prefixed, so an *action* on those may not be. Nothing below authorizes an arm, an
> env change, or a lifecycle move.

---

## 1. The finding

Five tags appear in production's `MMSELL_VARIANTS` but carry **no active Experiment OS
deployment arm**. Under `NEW_ONLY` the write path refuses them, so each book is constructed
every cycle, evaluates candidates, and has every entry rejected.

```
2026-09-11 07:48:25Z  experiment_os_enforcement
  BLOCKED (precheck): tag 'Lmmsell10' is not registered to any active Experiment OS
  deployment arm; register the experiment/deployment (or classify the tag) before it may trade
```

…and identically for **`Lmmsell8`**, **`mmsellA5`**, **`mmsellA4`**, **`mmsell9`**.

| tag | shape | where it came from |
|---|---|---|
| `mmsell9` | paper, `lo=5,hi=12,only=TOTAL+SPREAD+ASG+HRDERBY+BTCD+ETH,maxyes=7` | the 2x2 variant cohort (`docs/MMSELL_VARIANTS_THESIS.md`) |
| `mmsellA4` | paper, `volw=6,volv=6` | anchor set — vol entry gate (`docs/MMSELL_ANCHOR_SET.md`) |
| `mmsellA5` | paper, `strangle=1` | anchor set — strangle leg (same doc) |
| `Lmmsell8` | **live-prefixed**, `lo=5,hi=12,only=BTCD+ETH+ASG+HRDERBY` | early live canary line (`docs/MMSELL10_CANARY_PLAN.md`) |
| `Lmmsell10` | **live-prefixed**, `lo=5,hi=10,maxyes=7` | early live canary line (same doc) |

Reproduce:

```jsonc
{"type":"db","id":"<unique>","sql":"SELECT created_at, component, left(message,180) AS message FROM system_events WHERE level IN ('error','critical') AND created_at > TIMESTAMPTZ '2026-09-11 07:40:00+00' ORDER BY created_at DESC LIMIT 25"}
```

## 2. The whole question

**Are these deliberately retired books whose env entries nobody deleted, or books that went dark
by accident?** The two look identical from outside — that is the entire reason this needs a
session — and they want opposite responses:

- **Retired on purpose** → config hygiene. Five stale entries, a little wasted scan work, five
  error lines per worker boot that train everyone to ignore that log. Remove them from
  `MMSELL_VARIANTS`, record why, done.
- **Dark by accident** → an experiment has been silently accruing no evidence, possibly for
  weeks, while its deployment row still claims it is running. That is a **defect with lost
  evidence**, and it has happened here before.

### The precedent, and why it is not a repeat

`WS-008` / **XOS-000011** (2026-08-28, resolved): *sixteen* mmsell books recorded nothing for
four days because `close_epoch` orphaned its deployments, an epoch cut opened an empty
successor, and one blocked tag rolled back every other book's entries in the shared
`session_scope`.

**Those three defects are fixed.** Today's symptom is the *residual* one: a tag with nowhere to
live still logs a block, and nothing reconciles `MMSELL_VARIANTS` against the set of tags that
can actually trade. Four of these five tags appear in WS-008's own file, which is suggestive but
**not** proof they are the same rows — verify, do not assume.

The fix also means this is **not currently harming other books**: `LineageBlocked` no longer
escapes and rolls back the cycle. Confirmed 2026-09-11 — `Rmmsell1`/`Rmmsell2` took entries in
the same cycles these five were blocked.

## 3. How to investigate

Read-only, through the ops channel (`docs/OPS_RUNBOOK.md`). Unique `id` per request; check the
current occupant has a result file before taking the slot; reset to `{"type":"noop"}` when done.

1. **What does Experiment OS say each tag's lineage is?**
   `{"type":"xos","command":"tag","args":["mmsell9"]}` — repeat per tag. A tag with a *closed*
   deployment under a *closed* epoch reads differently from one that was never registered at all.
2. **Is any experiment holding an open deployment with a closed epoch (or vice versa)?** That is
   the WS-008 shape, and its recurrence is the serious case.
   `{"type":"xos","command":"control-tower"}` reports silent arms.
3. **When did each tag last record a trade?**
   `SELECT strategy, max(created_at), count(*) FROM paper_trades WHERE strategy = ANY(ARRAY['mmsell9','mmsellA4','mmsellA5','Lmmsell8','Lmmsell10']) GROUP BY 1`
   A tag that stopped on a date matching a known epoch cut is the accident case; one that stopped
   when its experiment was deliberately retired is the hygiene case.
4. **For the two LIVE-prefixed tags only** — are they in `live_strategies`, and do they hold any
   open position? `{"type":"env"}` and `count_live_book_open` semantics via the
   `live_book_truth` script. **Read `docs/EXPERIMENT_OS_ISSUES.md` XOS-000031 first: never read
   real-money P&L for a live tag out of `paper_trades`.**

## 4. What the answer authorizes

| finding | response |
|---|---|
| retired on purpose | Remove the entries from `MMSELL_VARIANTS` (operator confirmation — an env change **redeploys production**). Record the removal and why. |
| dark by accident, paper only | Experiment OS issue, owner Platform Change Review or the experiment's own role. The repair is a lineage repair package, reviewed in a PR — same shape as `repair_tmmsell_epoch`. |
| dark by accident, a LIVE tag with real exposure | **Live Ops, immediately.** Do not wait for the paper half of the investigation. |
| a live tag holding open real-money positions it cannot manage | Live Ops, treat as an incident. |

### Hard boundaries

- **Never register an arm to "fix" a blocked tag.** If a book was deliberately stood down, giving
  it an arm silently restarts an experiment nobody approved. The block is the system working.
- **Never edit `MMSELL_VARIANTS` without explicit operator confirmation.** Setting it redeploys
  the worker, and the value must be *appended to*, never replaced — replacing it deletes every
  other book including both live arms.
- **A handoff is not durable state.** Once the classification is known, file the Experiment OS
  issue; its severity and owner depend on which row above is true, which is exactly why this
  document does not file one in advance.
- Two of five tags are live-prefixed, so **do not take this as a read-only role and then act**.
  Classify read-only; hand the write to the owning role.

## 5. Suggested role

Start as **Experiment Control Tower** (read-only) to classify all five. Hand off to **Live Ops**
the moment a live tag turns out to hold or have held real exposure, and to **Platform Change
Review** if the WS-008 shape has recurred.

## 6. Deliberately not in scope

- The reviewed-universe tapes (`Rmmsell1`/`Rmmsell2`) — registered and trading normally
  2026-09-11T07:40Z. They are unaffected and are not this investigation's business.
- `Gmmsell2`, still inert pending its own handoff (`HANDOFF-gmmsell2-registration.md`).
- XOS-000030 (`KXYTVIEWS*` taxonomy sweep) and XOS-000032 (registry prefix-inheritance leaks).
- The one-off `psycopg.OperationalError` seen on an ops read at 2026-09-11T14:08Z, which
  succeeded on retry 30 seconds later. Noted so nobody re-discovers it as a lead; chase it only
  if it recurs.
