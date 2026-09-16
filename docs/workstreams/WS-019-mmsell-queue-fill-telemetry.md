# WS-019 — MMSELL queue / fill telemetry (Phase 1: instrumentation only)

**Phase:** REVIEW
**Status:** Active
**Created:** 2026-09-16
**Updated:** 2026-09-16
**Build OS:** v0.12

## Goal

Make every live MMSELL resting order one traceable observation — what we knew when we placed
it, where it sat in Kalshi's queue and how that evolved, what traded or was pulled ahead of us,
whether and when it filled, and what the market did immediately after — recorded durably and
queryably, with no change to any trading behaviour.

## Context

Paper assumes the resting maker order fills; live does not. `docs/OPS_FMMSELL10_PARITY_DIAGNOSIS.md`
attributed most of the recent paper-vs-live gap to universe composition and left fill selection
as the remaining unexplained term. `WS-015` (queue-aware cancel, RETIRED) showed the queue
endpoint is sound and that the retired rule lacked the context to be right: 5-minute samples,
no book, no trade tape, no exchange fill times. Design, inventories, budget and test plan:
`docs/MMSELL_QUEUE_FILL_TELEMETRY.md`.

## Current Mental Model

```text
tracker → mirror_mmsell_entry ── writes live_orders(pending) + execution_order_context
                                  (decision-time fields captured BEFORE the POST)
reconcile (5 min) ──────────────  fills/status/timeout/queue tick(trigger=reconcile) — unchanged,
                                  plus cancel stamps and WS→REST fill reconciliation
telemetry thread (new, daemon) ─  reads live_orders every 5 s → tracked markets
   ├─ WS orderbook_delta (use_yes_price) + trade per tracked market
   ├─ WS fill + user_orders + market_lifecycle_v2 once per connection
   ├─ REST queue batch: 20 s interval + event-triggered (debounced, ≤30/min)
   └─ post-terminal window 15 min, then unsubscribe
read-only client wrapper: GET methods only — the thread cannot place/amend/cancel
```

## Decisions Made

- **Thread inside the live worker, not a new service.** One credential holder, deployable by
  the merge, fail-soft by construction; a third Railway writer would need config the sandbox
  cannot perform.
- **Reuse `live_order_queue_ticks`** for all queue samples (new `trigger`/`source`/
  `remaining_count`/`features_json` columns) rather than a parallel table — existing readers
  keep working and the executor's per-cycle sample stays as the coverage floor.
- **Raw events first, derived later.** Book, trade, fill, order and lifecycle streams are
  stored verbatim with both clocks; features are recomputable.
- **Default ON** (`EXECUTION_TELEMETRY_ENABLED=true`) so the merge starts evidence; the kill is
  an allowlisted env var.
- **`fills.filled_at` is not changed** (shared semantic); the WS fill stream carries exchange
  time and is reconciled by `trade_id`.

## Open Decisions

- **D1.** Whether to route the `fills.filled_at` provenance fix through Platform Change Review
  now or leave it to Phase 2. Recommendation: Phase 2, once the WS stream has proven
  coverage against REST.

## Assumptions

- The account stays on ADVANCED (300 read tokens/s); the budget in §7 of the design is sized
  against it and re-checkable from `system_events(component='kalshi_limits')`.
- Kalshi's `orderbook_delta` `seq` is monotonic per `sid` and a `get_snapshot` reply is a valid
  baseline (documented; whether `seq` resets on snapshot is unverified — handled either way).
- The `websockets` package installs on Railway's NIXPACKS image (pure Python wheels exist).

## Non-Goals

- Fitting a fill-probability or fill-quality model; freezing any bucket or threshold.
- Any change to entry, cancel, price, size, universe or exposure.
- Archiving markets we hold no order in.
- Changing existing table semantics.

## Acceptance Checks

- Every live order placed after deploy has an `execution_order_context` row written before
  submission, a queue tick with `trigger=at_rest` within the scan interval of resting, interval
  ticks thereafter, and a `terminal` tick attempt.
- For each tracked market, `execution_book_events` contains a snapshot followed by deltas with
  contiguous `seq` per `sid`, or a `seq_gap` collector event and a fresh snapshot.
- Every WS fill for a tracked order has a matching `fills` row by `trade_id` within one
  reconcile, or the ops script reports the discrepancy.
- The collector's client wrapper exposes no write method (test-enforced) and the thread never
  writes `live_orders`.
- The full test suite is green, the migration graph has one head, the ops script is
  allowlisted, the livedash execution routes answer read-only.

## Build Card

Inline: `docs/MMSELL_QUEUE_FILL_TELEMETRY.md` §0, §6, §11 (goal, flow, definition of done).

## Implementation State

PR [#411](https://github.com/50thycal/kalshi_bot/pull/411) open, ready for review (solo mode: owner acceptance at merge).

## Review State

**Verdict:** Not started
**Reviewed head:** —
**Reviewed PR:** —
**Finalization:** —

## Related Decisions

`DEC-014`.

## Related PRs

[#411](https://github.com/50thycal/kalshi_bot/pull/411)

## Parked

- `fills.filled_at` provenance (reconcile time vs exchange time) — Platform Change Review.

## Next Step

Operator merge of #411 (hard stop: live executor diff); then read `execution_telemetry` COVERAGE within the first hour of resting orders.
