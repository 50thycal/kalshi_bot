# WS-007 — A fresh mmsell10 live canary with an exact paper twin

**Phase:** REVIEW
**Status:** Blocked
**Created:** 2026-08-28
**Updated:** 2026-08-28

## Goal

Put the `mmsell10` arm of `mmsell-price-ceiling` on real money as a Stage-1 canary with a
fresh paper twin created at the same instant — built, tested and reviewable in one PR, and
stopping short of every action that expands real-money exposure.

## Context

The brief asked for a canary registered against `mmsell-price-ceiling` at its *current*
version and epoch, using the `mmsell10` arm alone. Experiment OS refuses that shape, for two
independent structural reasons that were read off production and are now reproduced as tests
rather than asserted:

- `arm_live_canary` requires a pre-registered risk envelope on the version (`risk_json`).
  v1 has none and froze on 2026-08-16; the flush guard refuses every edit to a frozen
  version, because the approved envelope is part of the contract.
- `arm_live_canary` requires the live and twin tag maps to equal the declared arm set
  exactly. v1 declares `mmsell9` alongside `mmsell10`, so a canary on v1 would have to put
  the negative-paper arm on real money too.

A changed arm set is a new Version by the system's own rule, and a risk envelope can only be
pre-registered on one. So the successor Version is not a workaround — it is what registering
this canary means here. Its cost is real and is the blocking decision below: evidence windows
floor at the epoch start, so v2's promotion gate restarts at n=0.

Separately, this workstream builds the measurement contract the canary is judged on. Five
keep/stop quantities the brief requires had no canonical provider (fill rate, open exposure,
worst realized loss, tail-loss count, risk-gate blocks), plus total realized live P&L, which
is the only unit a loss *budget* can be denominated in.

## Current Mental Model

```text
  mmsell-price-ceiling                        (state: PAPER)
    v1 [FROZEN 2026-08-16]  arms {mmsell9, mmsell10}   risk_json: NONE
      e1  snapshot 5c3720fca2fe36f0 (MARKET_TAXONOMY coverage_2026_08_13)
        mmsell-ceiling-paper-legacy-1  -> mmsell9, mmsell10
        paper_to_live_canary: PASS recorded 2026-08-23  (n=1588 on mmsell10)
                             ^ cannot authorize: wrong arm set, no envelope

  ── the package registers ───────────────────────────────────────────────

    v1/e1  mmsell-ceiling-paper-legacy-1 ENDED at T
           mmsell-ceiling-paper-mmsell9-1 -> mmsell9        (keeps that book alive)
    v2 [FROZEN at T]  arms {mmsell10}     risk_json: Stage-1 envelope
      e1  snapshot 4f9adf15daa64035 (the ACTIVE one)
        mmsell-ceiling-paper-2      -> mmsell10             (evidence restarts here)
        paper_to_live_canary  (v1's bar + a sample floor)
        live_canary_keep      (pre-registered, every clause kind='live')

  ── then, on a separate approval ────────────────────────────────────────

      e2 [I2]  arm_live_canary at ONE instant:
        mmsell-ceiling-live-1  kind=live        -> Cmmsell10
        mmsell-ceiling-twin-1  kind=paper_twin  -> Cmmsell10_pt   twin_of -> live

  ── and only then, separately again ─────────────────────────────────────

      LIVE_STRATEGIES=Cmmsell10   <- the switch that lets an order reach Kalshi
```

The `mmsell10` tag hand-over is the part most easily got wrong: a tag resolving to two
ACTIVE deployment arms is refused as ambiguous, so leaving the v1 two-arm deployment active
alongside a v2 deployment on the same tag would have stopped the paper book. Ending a
deployment does not orphan its evidence — metric scopes resolve tags over every deployment in
the epoch, ended or not; only the enforcement resolver reads `ended_at`.

## Decisions Made

- **A successor Version, not an epoch.** Forced by the two refusals above, both reproduced in
  `tests/test_mmsell10_canary_package.py`. Recorded in `change_reason` on v2.
- **The arm is carried across verbatim.** `lo=5, hi=10, maxyes=7`, same universe, entry
  timing, sizing, settlement, fee model and order type. A test asserts v2's params equal v1's.
- **No crypto exclusion.** It would be a different universe and could not inherit this arm's
  evidence. Crypto is a reported monitoring slice only.
- **Full order book stays authoritative for `maxyes`.** The quote pre-filter stays disarmed;
  `tests/test_mmsell_orderbook_authoritative.py` proves a 41c-wrong inline quote cannot admit
  a market the book refuses, and that an armed pre-filter silently drops real candidates.
- **The tail-loss stop is structural, not invented.** Under a one-contract clip a settled
  market cannot lose more than ~$1, so `live_max_realized_loss_usd > 1.0` is a stand-down;
  cumulative tail cost is bounded by the loss budget. No tail-count threshold is registered,
  because there is no evidence from which to choose one.

## Open Decisions

- **D1. Accept the successor Version, and with it the n=0 restart?** v2's promotion gate
  cannot inherit the 2026-08-23 PASS. At mmsell10's observed rate (~144 settled/day) a
  300-trade floor is roughly two days. The alternatives are: arm both arms on v1 (puts the
  negative-paper arm on real money — not recommended), or do not arm at all.
  **Recommendation: accept.**
- **D2. Promotion sample floor.** Proposed **300** settled trades. v1 registered no explicit
  n and its PASS rested on n=1316. Alternatives: 0 (v1's literal contract, but then v2 could
  pass on a handful of trades) or 1316 (reproduce v1's evidence base, ~9 days).
- **D3. Win-rate stand-down at 5.0pp.** The registered 1.0pp is a *promotion* bar and is used
  as one; no precedent exists for a stand-down trigger.
- **D4. Decision-overlap hold at 50% and fill-rate hold at 25%.** No precedent. Lmmsell10
  observed a 61.2% fill rate.
- **D5. The dollar caps** — $15 total budget, $5 daily stop, $40 portfolio exposure. The last
  is portfolio-wide and shared with ~$17 of held positions on the two stood-down canaries.
- **D6. mmsell-type-tight's control.** Its `paper_keep` gate names
  `mmsell-price-ceiling/v1/e1/mmsell10` as an external control and resolves it through
  *latest version*. Registering v2 moves that reference to v2/e1, changing its block from
  BLOCKED_PLATFORM to BLOCKED_DATA until v2 accumulates evidence. Declared, not hidden;
  it authorizes nothing either way.

## Assumptions

- The applied I0/NO_ACTION disposition for `mmsell-price-ceiling` on
  `MARKET_TAXONOMY:settlement_repair_2026_08_24` still stands at arming time, so the
  synchronous re-evaluation is not refused for snapshot staleness.
- mmsell10's paper book keeps trading at roughly its observed rate, so the v2 floor is a
  matter of days.
- Legacy held exposure on the stood-down canaries continues to drain.

## Non-Goals

- Changing shared metric semantics to make a gate pass. Nothing here is a Platform Revision;
  the new providers implement quantities the registry did not yet have.
- Reviving `mmsell-scheduled-settle-live` or `theta4-fat-tail`, whose successor contracts were
  withdrawn on 2026-08-21 (`#251`). That withdrawal turned on treatment and control differing
  in universe, entry band and settle mode at once — a deconfounding problem a single-arm
  canary does not have.
- Arming the pre-filter, adding a crypto exclusion, or touching the runtime allowlist.

## Build Card

Inline: register a single-arm successor contract with a pre-registered Stage-1 envelope and a
pre-registered keep/stop gate; implement the six missing live providers; prove the order book
is authoritative for the price ceiling; hand over the `mmsell10` tag without ambiguity; and
stop before arming.

## Implementation State

PR open. `kalshi_bot/experiment_os/canary_mmsell10.py` (contract, envelope, gates, registration
and arming), six new providers in `metrics.py`, `scripts/mmsell10_canary.py` (operator entry
point, dry-run by default), `scripts/mmsell_canary_slices.py` (crypto monitoring, allowlisted
read-only).

## Review State

Awaiting operator review. Nothing is registered and nothing is armed; the runtime live
allowlist is empty and the ops channel is `noop`.

## Related Decisions

`DEC-001` (the authority boundary). New entry proposed in this PR for the successor-Version
finding.

## Related PRs

This PR.

## Next Step

Operator answers D1–D5; on D1 accept, run `scripts/mmsell10_canary.py register --execute`.
