# WS-018 — Spot/perp funding carry within Calvin's capital budget

**Build OS:** v0.12 · **Phase:** REVIEW · **Status:** Blocked
**Related PR:** [#402](https://github.com/50thycal/kalshi_bot/pull/402)

## Goal / Build Card

Establish readable, interpretable funding data and test same-asset spot/perp carry at
$2,000 total cash, with $1,000/$4,000 comparisons; report honest dollar results or an exact
data blocker. Calvin explicitly promoted this parked research on 2026-09-13. No live funds.

## Non-Goals

Live trades, transfers, new accounts, restarting PERP-V1, retuning the failed reversion
probe, changing shared execution semantics, or exceeding $4,000 including reserves.

## Build Spec

[SPOT_PERP_CARRY_CENSUS.md](../SPOT_PERP_CARRY_CENSUS.md). The first runnable stage fetches
the documented market-rate history and tests total-capital cost hurdles; it cannot yet
produce a complete carry backtest without units, payment completeness, quotes and fees.

## Acceptance Checks

- Frozen window, raw payment provenance, validation and unknown-is-not-zero tests pass.
- Capital arithmetic counts spot, collateral and reserves once; both assets share the budget.
- New module is allowlisted only after the explicit ops-runner merge hard stop is satisfied.
- Run through approved transport; record data and result or named blocker durably.
- If testable, continue to the capital cash-flow test in this same workstream; no fabricated
  returns, retrospective funding selection or autonomous trading registration.

## Assumptions / Interrupt Risks

Funding-unit and account-fee documentation must be verified before yield calculations.
HTTP403 in scratch is an access failure, not evidence of zero funding. Thin history is HOLD.
The first stage has no book or XOS lifecycle mutation; Control Tower must be refreshed
before any later registration. No existing active work is silently paused; this blocked
review row does not consume an Active slot on the already over-limit board.

## Open Decisions / Review State

Calvin merged #402; verified merge SHA `ed33c037a2d360125d28898363587f02a0fcf637`.
The named ops-runner merge hard stop is satisfied. Approved census request
`spot-carry-20260913-1` completed successfully, with 90 observations / 30 days per asset,
no errors and eight-hour gaps throughout. C1 PASS; overall HOLD for units/scaling,
executable prices and actual account fees. Raw result is linked in the Build Spec.
The owned ops request is reset to noop. No live funds or XOS registration.
Implementation actor: Codex / spot-carry-20260913. Solo mode; no independent review claimed.
Framework checked: canonical v0.12 on 2026-09-13, matches project.
Local verification: 12 tests, ruff and compilation pass. Capital-only mode ran; direct
public-data mode returned HTTP403 for both histories and HOLD. No funding returns measured.
Control Tower `carry-ct-20260913-1` returned integrity clear, IDEA 0 / PROBE 0 at its
recorded snapshot. Refresh before any later XOS registration; this census registers none.
Finalization: census evidence recorded in solo mode; full funding test remains blocked
on named data/interpretation inputs, no longer on merge approval.

## Next Step

Obtain authoritative API funding-unit and contract-multiplier definitions; verify payment
schedule, then obtain synchronized executable quotes and applicable account fee tier.
Continue the frozen capital cash-flow test only when C2/C3 inputs are verified.

## Parked

Purchased-tail MMSELL hedge remains on ACTIVE.md. No other work is opened.
