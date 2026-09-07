# Handoff — a live order rested 11+ hours past its 4h timeout because the cancel keeps failing silently

**From:** kalshi_bot, task-specific session, 2026-09-07 (WS-015, queue-aware cancellation shadow)
**To:** a **Live Ops** session — this is the live-order execution path, not an experiment question
**Status:** nothing done. Read-only diagnosis only. No write, no cancel, no config change.
**Money at stake:** trivial (~$0.93 on the one observed order). **What is actually at stake:** the
4-hour timeout is a *safeguard*, and it is currently not enforcing on at least one order while
reporting nothing an operator can read.

---

## Read this first

Every runtime fact below was read from production between **13:21Z and 13:27Z on 2026-09-07** and
is timestamped where it matters. **Re-verify before acting anyway.** The last handoff in this
directory (`HANDOFF-perpv1-collector-shutdown.md`) was written from workstream prose that
contradicted an ops receipt, and a Live Ops session correctly threw the whole brief away at step 1.
Do the same to this one if production disagrees.

This was found incidentally: the queue-aware cancellation shadow (`WS-015`) records a decision row
for every resting order every cycle, and one order kept coming back as `normal_timeout` — the code
saying *"this is past the timeout, the ordinary timeout path owns it."* It kept saying that for
hours. **The shadow did not cause this and does not touch it**; it only writes audit rows. The
order predates the shadow by 9.5 hours.

---

## The observation

One live order, still `resting` at **679 minutes** (11.3 h) old. Its 4-hour timeout
(`LIVE_ORDER_TIMEOUT_SECONDS`, default 14400) should have cancelled it at **~06:03:52Z**.

| field | value |
|---|---|
| `live_orders.id` | 5679 |
| `kalshi_order_id` | `01a0799b-8b40-7b41-b051-7363555c04aa` |
| `client_order_id` | `a25e9e0d-3d1b-47f5-b996-06dfda4f5c34` |
| strategy | `Fmmsell10` (the contest-cap live canary) |
| market / event | `KXBTCD-26SEP1117-T85999.99` / `KXBTCD-26SEP1117` |
| side / action / price / qty | `no` / `buy` / 93¢ / 1 |
| created | 2026-09-07 **02:03:52.423107+00** |
| status / cancel_reason | `resting` / **NULL** |
| fills | **0** |

## What is established, and how

1. **The order is genuinely alive on Kalshi.** Not a stale local row. Its last
   `live_order_queue_ticks` sample was **13:26:25Z** (288 samples total, max `contracts_ahead`
   3,271), so Kalshi's queue-position endpoint was still returning a live queue position for it
   minutes before this was written. Independently, `raw_order_json` — which reconcile overwrites
   with the exchange's own row — carries Kalshi's payload reporting
   `"status": "resting"`, `"action": "sell"`, `"side": "yes"`. Both the queue endpoint and the
   orders feed agree it is resting.
2. **The timeout loop is reaching it and the cancel is failing.** Production logs carry
   `WARN live cancel failed` at **13:26:16Z** and **13:26:25Z** — i.e. still firing, ~11.4 h after
   the order was created. That warning is emitted from exactly one place: the `except` around
   `cancel_events_order` in the reconcile timeout loop.
3. **Therefore the row is stuck by construction.** In
   `LiveExecutor.reconcile` the cancel and the status write are in the same `try`:

   ```python
   try:
       if row.kalshi_order_id:
           self.client.cancel_events_order(row.kalshi_order_id)
       repo.update_live_order_status(session, row, status="canceled", cancel_reason="timeout")
       self.summary.timed_out_canceled += 1
   except AuthError:
       raise
   except Exception:            # noqa: BLE001 — likely already filled/gone; next cycle resolves
       logger.warning("live cancel failed", extra={"extra_fields": {
           "kalshi_order_id": row.kalshi_order_id}})
   ```

   The cancel raises, so the status write never runs, so the row stays `resting`, so the next
   cycle tries again — forever. The comment says *"next cycle resolves"*; for this failure mode it
   demonstrably does not.

## What is NOT established — do not assume it

- **Why the cancel fails.** The exception type and message are **invisible in production**: they
  go into `extra_fields`, and Railway's log endpoint returns only the message string and drops
  structured fields. This is the *exact* trap already documented in this same file for the queue
  sampler ("the same trap that made the 429 counters write-only until the status was moved into
  the message string") — the fix was applied there and never to the cancel path.
- **How many orders are affected.** One was observed. There were 7 resting orders at 13:22Z; only
  this one was past its timeout. Whether this recurs, or is specific to this market type, is
  unmeasured.
- **Whether two failures ~9 s apart (13:26:16 and 13:26:25) mean two attempts in one cycle or two
  distinct callers.** Cycles run ~2.4 min apart, so 9 s apart inside one cycle is odd and worth
  explaining rather than waving through.

## Candidate hypotheses, ranked

1. **The v2 events cancel does not work for this order or market type.** `KXBTCD-…-T85999.99` is a
   Bitcoin *threshold/bucket* market, and this repository already documents a v1/v2 split for
   exactly that family: *"the v2 endpoint rejects those closes"* for range-bucket markets, which is
   why `create_v1_order` exists for closing them. If `DELETE /portfolio/events/orders/{id}` refuses
   bucket-market orders the same way, every cancel on this order fails permanently and no amount of
   retrying helps. **Check this first** — it predicts a stable 4xx with a specific error code.
2. **A permission/state refusal**, e.g. the order is in a state Kalshi will not accept a cancel for,
   or `_ensure_can_cancel` is raising locally before any HTTP call. `_ensure_can_cancel` requires
   `BOT_MODE=live` and deliberately ignores the kill switch; `BOT_MODE=live` and
   `KILL_SWITCH=false` were both confirmed at 13:29Z, so this is unlikely but cheap to rule out.
3. **A transient/network class of error that happens to be permanent here.** Least likely given it
   has failed continuously for >7 hours.

## Two defects, and they compound

Whatever the cancel's cause turns out to be, these are independently worth fixing and are the
reason a $0.93 order went unnoticed for 11 hours:

- **The failure is undiagnosable in production.** The error class and message must be in the log
  *message*, as they already are in `sample_queue_positions`. Without that, nobody can tell
  hypothesis 1 from hypothesis 3 without a code change and a redeploy.
- **The timeout cancel has no attempt bound and no counter.** The *drain* path next to it has both
  (`_DRAIN_MAX_ATTEMPTS = 5`, and `drain_failed` in the cycle summary). The timeout path has
  neither, so a permanently-failing cancel retries forever and never appears in the cycle summary
  that an operator reads. `LiveCycleSummary.timed_out_canceled` only counts *successes*, so a book
  where every timeout cancel fails looks identical to one with nothing to cancel.

## Suggested first moves (all read-only)

1. Re-verify the order is still stuck and still failing:
   ```jsonc
   {"type":"db","sql":"SELECT kalshi_order_id, status, cancel_reason, created_at, round(EXTRACT(EPOCH FROM (now()-created_at))/60) AS age_min FROM live_orders WHERE status='resting' ORDER BY created_at","max_rows":50,"id":"tc-1"}
   {"type":"logs","limit":400,"filter":"live cancel failed","id":"tc-2"}
   ```
2. Establish the blast radius — has this happened before, on which markets?
   ```jsonc
   {"type":"db","sql":"SELECT market_ticker, strategy, count(*) AS n, min(created_at)::text AS first, max(created_at)::text AS last FROM live_orders WHERE status='resting' AND created_at < now() - interval '5 hours' GROUP BY 1,2 ORDER BY 3 DESC","max_rows":50,"id":"tc-3"}
   ```
   and the historical shape: orders that *did* reach `canceled/timeout` vs ones that sat past the
   timeout, grouped by market family (`KXBTCD` and other bucket markets vs the rest).
3. **Only then** decide the fix. The likely shape is *three* small changes, and the middle one is
   the one that matters most: put the error into the log message; bound the retries and count the
   failures into the cycle summary; and, if hypothesis 1 holds, route bucket-market cancels the way
   closes are already routed.

## Scope boundaries

- **This is not a WS-015 problem and must not be fixed inside it.** WS-015 is COMPLETE; its shadow
  only observes. Do not widen it.
- **This is a live-execution safeguard**, so under this repo's own rule nothing here is ever
  classified `simple`, whatever the diff size.
- **The durable home for this problem is an Experiment OS issue**, not this file and not chat
  (`CLAUDE.md`: an anomaly or suspected defect belongs in an XOS issue, owned by the role that owns
  the problem — here **LIVE_OPS**). Opening that ticket should be the first act of whoever picks
  this up; this document is the evidence to paste into it, not a substitute for it.
- **Cancelling can only reduce exposure**, so a manual cancel of this one order is safe under
  existing kill-switch semantics — but it treats the symptom and destroys the live reproduction.
  Prefer diagnosing while it is still stuck.
