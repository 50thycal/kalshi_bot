# WS-015 — mmsell10 queue-aware cancellation: shadow instrument + pre-registered contract

**Phase:** COMPLETE
**Status:** Active
**Created:** 2026-09-07
**Updated:** 2026-09-07 (registered; shadow running)
**Build OS:** v0.12

## Goal

Register a paper-first (shadow) Experiment OS experiment that tests whether cancelling
deep-queue mmsell10 resting orders before the 4 h timeout raises net realized dollars at equal
capital, with the treatment rule frozen from a read-only baseline before any shadow evidence
exists — and without touching any live order.

## Context

mmsell10 rests a $1 maker order for four hours and the open-position cap counts it as open. The
baseline shows the cap binds daily (67–145 `gate:open_cap` refusals/day) and that some orders
sit 5,000+ contracts deep with ≤10% chance of filling before the timeout. Queue telemetry has
existed on every resting live order since 2026-08-14 (`docs/LIVE_QUEUE_POSITION.md`), so the
question is answerable from data rather than from a new collector. Full thesis, baseline and
contract: `docs/MMSELL_QUEUE_AWARE_CANCEL.md`.

## Current Mental Model

```text
reconcile (every cycle, LIVE worker only)
  ├─ resolve orders / fills / positions          (unchanged)
  ├─ 4h TIMEOUT cancel loop                       (unchanged, runs FIRST)
  ├─ sample_queue_positions  -> live_order_queue_ticks (+ this cycle's observations in memory)
  ├─ evaluate_queue_cancellations                 (NEW; LIVE_QUEUE_CANCEL_MODE off|shadow|live)
  │     for each resting order:
  │        decide(FROZEN_RULE, age, remaining timeout, observation, now)
  │           missing/stale/malformed/error telemetry -> telemetry_keep
  │           age < 90min -> too_young ; thin cell -> insufficient_evidence
  │           P(fill|age cp, depth bucket) <= 10% (n>=20) -> queue_cancel
  │        shadow: write audit row, send nothing
  │        live:   send cancel ONLY IF tag in LIVE_QUEUE_CANCEL_TAGS AND tag registered to an
  │                active LIVE treatment arm of mmsell10-queue-aware-cancel, within per-cycle cap
  │        -> live_order_queue_decisions (one row per order per cycle, stamped with XOS lineage)
  └─ drain_stood_down_books                        (unchanged)

Experiment OS: mmsell10-queue-aware-cancel  v1 (frozen)  PROBE
   arms: qac_control (CONTROL, tagless) · qac_treat (TREATMENT, tag qacshadow1 = scope handle)
   e1 deployment: mmsell10-qac-shadow-1 (kind probe)
   gates: shadow_to_paper (PROBE->PAPER; 4 pass clauses, floor 40 would-cancels)
          shadow_kill    (forgone >= 3c/would-cancel | coverage <= 60%)
   metrics: qac_* providers read live_order_queue_decisions by deployment lineage
```

## Decisions Made

- **Shadow before anything else, and shadow is a PROBE.** Paper cannot express the treatment
  (no queue, fills assumed at entry); the only honest treatment run without real-money
  authorization is the frozen rule evaluated on the live book's real resting orders with
  decisions recorded, not sent. An instrument, so `kind=probe` at PROBE.
- **The rule is frozen from the baseline now, not after a telemetry epoch.** Coverage is 100%
  on 18,143 samples / 408 orders and the survival cells the rule opens hold 25–42 orders and are
  stable across adjacent checkpoints. The shadow is the out-of-sample test.
- **The treatment arm carries a tag purely as an evaluator scope handle.** `_arm_scope` resolves
  gate scope through tagged deployment arms; a tagless arm has no scope. No book uses the tag.
- **Rule thresholds are Settings defaults, not env-settable.** `LIVE_QUEUE_CANCEL_MODE` and
  `_TAGS` are allowlisted for the env channel; the thresholds are not — a re-tune is a Version.
- **The 4 h timeout runs before the rule, in every mode.** The rule can only shorten a rest,
  never extend one, and only cancel — never place, re-price or re-size.

## Open Decisions

- **D1. RESOLVED 2026-09-07.** The operator authorized both acts; the package registered
  (`qac-register-20260907-2`) and the shadow is running. No decision outstanding.
- **D2.** Should the horizon of the rule be `min(4h, time to market close)`? ~40% of cancels are
  exchange-side at ~1.5 h (market close). Deferred to a later Version once the shadow shows how
  often a would-cancel is pre-empted by close; the remaining-timeout figure is on every row.

## Assumptions

- The slot, not the capital, is the scarce resource at a $1 clip; the cap keeps binding.
- Late fills stay winners (baseline: 29 settled after 90 min, 0 losses). If the shadow finds
  otherwise the forgone-profit clause is the one that moves.
- Kalshi's `queue_position_fp` keeps meaning contracts ahead; the parser is shape-tolerant and
  a shape change surfaces as `malformed` rows (kept, never cancelled).

## Non-Goals

- Any change to entry universe, price offset, sizing, exits, or the existing risk caps.
- Cancel-and-repost to reset queue priority (explicitly forbidden; the retry path is unchanged).
- A live canary of the treatment. That is a separate Version, envelope and approval.
- Price-offset, sizing or new-signal research.

## Acceptance Checks

- [x] Queue telemetry collection exists for live resting orders and the paper case is named
      as unavailable rather than simulated.
- [x] Read-only baseline answers the six research questions (`docs/MMSELL_QUEUE_AWARE_CANCEL.md` §3).
- [x] A statement of whether existing data supports a frozen threshold (§4: yes, with limit).
- [x] Frozen rule written into code, Settings and the registered contract, pinned by a test.
- [x] Package registers experiment, arms, epoch, probe deployment and both gates through
      `service.*` under NEW_ONLY; gates resolve against the canonical registry; HOLD on no evidence.
- [x] Unit tests: payload parsing (existing), missing/stale/error telemetry keeps, price/size
      untouched, auditable reason + telemetry snapshot, 4 h timeout still applies, queue step
      fail-soft, unregistered tag refused at the write path and at the cancel path.
- [x] Baseline/shadow report script allowlisted on the ops channel.
- [x] Operator registered the package (`qac-register-20260907-2`, SUCCEEDED) and started the shadow
      (`LIVE_QUEUE_CANCEL_MODE=shadow`); first decision rows land stamped with the probe arm's lineage.

## Build Card

Inline: the handoff brief of 2026-09-07 is the card; scope = telemetry + rule + shadow +
registration package + baseline report + tests + docs. No live behaviour change.

## Implementation State

PR reference — see Related PRs. Migration `a0bd7f9c48de` (`live_order_queue_decisions`).

## Review State

| PR | Verdict | Accepted head | Finalization |
|---|---|---|---|
| #364 | Owner-accepted | 4292de8fb7d132b6db52b3c9248015269aca0e34 | merged 2026-09-07 07:56Z |
| #365 | Owner-accepted | b1e98a83f3ccad65013c654a6d61ddcc474a625c | merged 2026-09-07 11:28Z |

Solo mode: the owner accepted both at merge. No independent party reviewed either; that is
recorded as acceptance, never as an approval (`DEC-011`).

## Related Decisions

`DEC-001` (experiment truth lives in Experiment OS; this file links and never restates a
standing), `DEC-011` (solo mode; active-work limit counts `Active` rows only).

## Related PRs

The PR opened from branch `claude/mmsell10-queue-aware-cancel-y0psq6`.

## Parked

None.

## Next Step

None.

<!-- The build is finished and the instrument is running. What remains — evidence accrual and a
gate verdict — is Experiment OS's, not Build OS's (`DEC-001`); this workstream holds no tail. -->
