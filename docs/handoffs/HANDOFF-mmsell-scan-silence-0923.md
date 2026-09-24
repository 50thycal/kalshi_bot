# Handoff — mmsell scan loop has gone silent for ~19h, worker otherwise appears up

**Scope: one finite investigation.** Diagnose why the shared `mmsell_scan` cycle stopped
producing `system_events`, then act on what the diagnosis says. Not a licence to touch
lifecycle state, gates, or the `MMSELL_VARIANTS` env directly unless the fix requires it.

Found 2026-09-24T03:05Z by a task-specific session doing unrelated work (the reviewed-universe
tapes, `mmsell-reviewed-universe`, #383/#387/#391). The tapes' `Rmmsell1`/`Rmmsell2` are two of
many mmsell books riding the same shared scan loop — this is not specific to them.

> **No real money in the investigation itself.** Reading is free. This monitoring session did
> **not** start the investigation and did not touch env, deploys, or lifecycle state — this
> handoff is the full extent of what it did.

---

## 1. The finding

`system_events` for the `mmsell_scan` / `mmsell_quote_parity` components — which fire together
roughly every 30 minutes during normal operation — show a clean cadence through
**2026-09-22 08:19:33Z**, then:

- a ~13-hour gap
- two more paired firings at **2026-09-23 08:04** and **08:35Z**
- **nothing since**, confirmed again at 2026-09-24T03:12Z — **~18.6 hours of silence** at time
  of writing, and the gap is still open as this is written.

No `ERROR`/`CRITICAL` line names either component or either `Rmmsell` tag in that window. This
looks like **absence, not a crash**: the loop stopped producing events rather than producing an
error. The one nearby log line of note is a `WARNING` at 09-23 08:02Z —
`twin Fmmsell10_pt4 params drifted from epoch snapshot` — which is a **different book's** twin
check and, on its face, unrelated (no error, no retry storm visible around it), but it is the
last non-scan log before the second-to-last pair of scans, so it's worth ruling in or out early
rather than assumed irrelevant.

**The operator separately reports the Railway/Vercel dashboards show the service as online.**
That is consistent with the *process* being alive while the *scan loop inside it* is hung,
wedged, or has exited its own scheduling path without crashing the container — i.e. this may be
a liveness-without-progress failure, not a liveness failure. Worth checking early: is the mmsell
scan loop still literally scheduled/running inside the worker, or did its task silently die
while the rest of the process (HTTP server, other loops) kept the container looking healthy?

Reproduce the read:

```jsonc
{"type":"db","id":"<unique>","sql":"SELECT to_char(created_at,'MM-DD HH24:MI') AS at, level, component, left(message,180) AS message FROM system_events WHERE created_at >= '2026-09-22' ORDER BY created_at DESC LIMIT 60"}
```

## 2. Downstream effect, observed on two tapes (illustrative, not the only books affected)

`Rmmsell1` / `Rmmsell2` (paper, `mmsell-reviewed-universe`) took their last entries at
2026-09-23T08:03–08:04Z — consistent with the last scan pair — and nothing since:

```jsonc
{"type":"db","id":"<unique>","sql":"SELECT strategy, status, count(*) AS n, max(created_at) AS last_entry FROM paper_trades WHERE strategy IN ('Rmmsell1','Rmmsell2') GROUP BY strategy, status ORDER BY strategy, status"}
```

As of 03:12Z: 3 open / 437 settled on the control, 3 open / 286 settled on the treatment,
neither count moving. Since this is a **shared** scan loop, not book-specific config, expect
every mmsell book — paper and live — to be equally stalled. This has NOT been verified against
other books' tables in this session; that verification is the first useful step for whoever
picks this up.

## 3. The whole question

**Is the scan loop hung, dead, or just quiet because there is nothing to scan?** Those look
different from outside only if you check:

- **Genuinely no candidates in this window** → nothing to fix, but then the 30-minute *scan
  funnel* log itself (which fires even on an empty scan, per its cadence through 09-22) should
  still be firing on schedule. Its absence is the tell that this is NOT simply a quiet market.
- **The scan task died/deadlocked inside a live process** → a defect, likely wants a restart to
  clear plus a root cause (unhandled exception swallowed somewhere, a lock never released, an
  await that never resolves). Check whether a worker restart between 09-23 08:35Z and now would
  explain the silence (a restart during a bad deploy could itself have paused things) or whether
  the process has been continuously up the whole time (in which case restart-to-clear is a
  reasonable containment step while root-causing separately).
- **The worker crash-looped and is now stuck restarting without completing init** → dashboards
  can still show "online" between restart attempts; check actual container restart count/uptime,
  not just current status.

## 4. Why this is Live Ops' and not this session's

This session is a task-specific reviewed-universe monitor with no standing write access and no
mandate to touch shared infrastructure. The scan loop is shared across every mmsell book,
several of which are live-prefixed — a live-money surface — so investigation and any fix belongs
with **Live Ops** (real-money safety, runtime incidents, collector/worker health per
`.claude/sessions/README.md`), not with a paper-tape monitoring session.

**File an Experiment OS issue** (`docs/EXPERIMENT_OS_ISSUES.md`) alongside picking this up — the
stall means the `paper_keep` gate's 60-settlement-day clock is not advancing for every mmsell
paper book while this is open, which is durable state worth recording even after the scan loop
resumes.

## 5. What NOT to do

- Don't restart-and-close without root-causing — a wedged loop that comes back on a restart and
  wedges again in another 24h burns another day of every mmsell experiment's clock.
- Don't touch `MMSELL_VARIANTS`, any deployment arm, or any lifecycle state as part of this
  diagnosis — none of that is implicated by the finding above.
- Don't assume this is the known five-tag `BLOCKED`-registration issue from
  `HANDOFF-dead-mmsell-books.md` (#388, merged) — that produces `BLOCKED` **error** lines per
  cycle for five specific tags; this is a **silent absence** of the scan cycle itself, affecting
  (as far as observed) every book. Different failure shape, likely different cause. Worth a
  quick check that the two aren't secretly connected (e.g. a boot-time exception in the same
  registration path also killing the scan scheduler) but treat as a separate defect until proven
  otherwise.

## 6. Status when handed off

Still open. Last confirmed silent at 2026-09-24T03:12Z (~18.6h). Not investigated beyond the
`system_events` and `paper_trades` reads above. Another session was seen touching the ops slot
around 03:05Z with `limm-catchup-*` position-reconciliation queries — possibly already aware of
and working a related worker issue; worth checking in on before duplicating effort.
