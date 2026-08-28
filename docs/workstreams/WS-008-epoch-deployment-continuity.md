# WS-008 — An epoch boundary must not silently stop the books

**Phase:** REVIEW
**Status:** Active
**Created:** 2026-08-28
**Updated:** 2026-08-28
**Issue:** XOS-000011 (OPS · LIVE_OPS · HIGH/P1)

## Goal

Make it structurally impossible for an Experiment OS epoch boundary to take a running
book off the board without saying so, and repair the rows where it already did.

## Context

Every mmsell paper book recorded nothing between **2026-08-24 13:56:39Z** and this
workstream — sixteen books, four days, including the `mmsell` wide-control incumbent.
It was found while checking whether the freshly registered `mmsell-price-ceiling` v2/e1
paper deployment was accumulating evidence for its promotion gate. It was not, and the
reason had nothing to do with that registration.

Three defects compose, and all three had to be true:

- **`close_epoch` orphaned its deployments.** It set `ended_at` on the epoch and left
  every deployment under it open. The admission resolver requires *both* to be open, so
  those tags stopped resolving while the deployment row still claimed the book was
  running. Nothing reconciled the two views, and nothing alarmed.
- **An epoch cut opened an EMPTY successor.** `platform_impact.apply_new_epoch` closed
  e1 and opened e2 with no deployments at all, so even a correctly ended predecessor
  left the tags with nowhere to live. An I2 boundary means *same contract, fresh
  evidence* — it has never meant "stop trading".
- **One blocked tag rolled back every book.** `main._run_mmsell_book` runs the whole
  scan in a single `session_scope`. `LineageBlocked` escaped `MmSellTracker.run_once`,
  the scope rolled the transaction back, and every *other* book's entries went with it.
  `LineageBlocked`'s own docstring asserted this could not happen; it was never tested.

The trigger was the `MARKET_TAXONOMY:settlement_repair_2026_08_24` activation, applied
to `mmsell-type-tight` as an I2 at 2026-08-24T14:21:17.571842Z. Exactly one experiment
was classified I2, which is why exactly one dangling deployment exists system-wide —
and why one retired book's four tags were enough to take the whole family dark.

## Current Mental Model

```text
  2026-08-24T14:21:17.571842Z — the I2 boundary
    mmsell-type-tight v1
      e1 CLOSED ────── tmmsell-paper-legacy-1  ended_at: NULL   <- stranded
      e2 OPEN   ────── (nothing)                                <- empty

  the resolver needs BOTH open, so:
      Tmmsell1/2/5/6 -> no active deployment arm -> LineageBlocked

  and the blast radius:
      run_once() raises ─> session_scope() rolls back ─> mmsell, mmsell5..10,
      mmsellA4/A5, Tmmsell*, Lmmsell* all lose the cycle, every cycle
```

The same shape was latent in the canary path. `arm_live_canary` closes the paper epoch
to open the live one, so once `close_epoch` ended its deployments correctly, arming
`mmsell10` would have ended the paper parent's deployment and blocked the very book the
canary was promoted from. Two tests in `test_mmsell10_canary_package.py` fail without
the fix, which is how that was found rather than shipped.

## Decisions Made

- **Closing an epoch ends the deployments in it** (`DEC-007`). The cascade makes the
  record say what the resolver already believed. It removes no evidence: metric scopes
  resolve tags across every deployment in an epoch, ended or not.
- **An epoch cut carries its PAPER deployments forward**, at the boundary instant, with
  the same arms and tags and a derived key. Evidence is not pooled — the epoch is what
  metric scopes window on, which is the whole point of the boundary.
- **A carry-forward refuses `live` and `paper_twin` by name.** `arm_live_canary` is the
  only path that may create live lineage, because it is the only one that proves fresh
  tags, a twin at the same instant and a re-evaluated promotion gate. A boundary cut can
  prove none of that, so it stops and says so rather than minting live rows quietly.
- **A blocked book is skipped, never raised.** `enforcement.tag_admissible` is the
  sanctioned pre-check and already existed; the tracker now uses it once per cycle, with
  a defensive catch at the write for a mid-cycle change. The block is not weakened — the
  tag is still refused, still counted, still logged.
- **The data repair is a reviewed package, not a migration.** A lifecycle write belongs
  on the sanctioned transport so it leaves a receipt naming the actor and the reason; a
  migration would edit experiment state from outside the system that owns it, and would
  run on every deploy rather than once against the rows that are actually broken.

## Open Decisions

- **Is this a Platform Revision?** `EXPERIMENT_ENGINE` is a registered platform
  component, and this changes engine behaviour. The argument that it is not: no measured
  quantity changes, no recorded evidence is reinterpreted, and no gate reads differently
  — what changes is which books are permitted to keep trading. Registering a revision is
  Platform Change Review's write, not a task session's, so this is raised rather than
  performed.

## Non-Goals

- Reinterpreting the four days of lost evidence. It is lost; the books simply did not
  trade. Nothing here backfills or estimates it.
- Making the resolver tolerate inconsistent state. A deployment on a closed epoch stays
  inadmissible — the fix is to stop creating that state, not to start honouring it.
- Any change to gate semantics, metric providers or lifecycle transitions.

## Build Card

Inline: cascade the epoch close to its deployments; carry paper deployments across a
boundary and refuse live ones; isolate a lineage-blocked book to itself; keep the paper
parent registered when a canary arms; and repair the one experiment already broken.

## Implementation State

- `service.close_epoch` — ends the deployments still running in the epoch.
- `service.open_deployments` / `service.carry_deployments_forward` — the shared
  carry-forward, with the live/twin refusal.
- `platform_impact.apply_new_epoch` — captures the open deployments before the close and
  re-registers them on the successor.
- `service.arm_live_canary` — carries the paper parent onto the live epoch.
- `mmsell/tracker.py` — per-cycle admissibility pre-check plus a defensive catch;
  `blocked_books` on the cycle summary.
- `experiment_os/repair_tmmsell_epoch.py` + the `REPAIR_LINEAGE` action — the one-shot
  repair, idempotent, with every precondition checked.
- `tests/test_experiment_os_epoch_continuity.py` (12) and two additions to
  `tests/test_mmsell10_canary_package.py`.

## Review State

The blast-radius test and both canary tests were verified RED against the unfixed code.
Nothing here is registered or armed, and the repair has not been run in production.

## Related Decisions

`DEC-007` (an epoch boundary carries its books or ends them, never both).

## Next Step

Merge, then run `REPAIR_LINEAGE` for `tmmsell-epoch-repair` through the lifecycle
transport and confirm the four Tmmsell books and the whole mmsell family are recording
again. Only then does WS-007's promotion gate have evidence to accumulate.
