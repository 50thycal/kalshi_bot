# WS-027 — WIN-set live canary beside Kmmsell10, with shared-market accounting

**Phase:** REVIEW
**Status:** Active
**Created:** 2026-10-16
**Updated:** 2026-10-16
**Build OS:** v0.12

## Goal

Run a live mmsell canary restricted to the categories that won on paper and live, ALONGSIDE
`Kmmsell10`. Where both want one market, both may hold it; each book's P&L is attributed from its
own fills; combined size stays inside a registered per-market bound.

## Context

Owner decision 2026-10-15 (session `01Gzz3T2R2Uq1ckeUEK6RgXY`), design only. Evidence:
`docs/MMSELL_LIVE_CANARY_PATTERNS.md` (PR #636). Three things blocked running both: the
XOS-000040 held-market gate, the live provider's contested-market exclusion, and the absence of
a combined per-market bound.

## Current Mental Model

- Phase 1 (Platform Change Review): `docs/LIVE_FILL_ATTRIBUTION.md`. Live metrics priced per
  fill, per tag; dormant until `METRICS_ENGINE:live_fill_attribution_2026_10` is ACTIVE.
- Phase 2 (build): `MMSELL_LIVE_SHARED_MARKET_PAIRS` (default empty) + `gate:shared_bound` +
  reduce-only closeout by tag; the `mmsell-win-set-canary` package.
- Phase 3 (Live Ops): the preflight in `docs/MMSELL_WIN_SET_CANARY.md` §6.

## Decisions Made

- Platform Change Review rulings (2026-10-16, this session): I1/RECOMPUTE for the six live
  canaries, I0/NO_ACTION for the nine others; mismatches are non-blocking integrity events;
  `Kmmsell10` keeps its epoch (queue priority by variant order), with a pre-registered tripwire.
  `DEC-037`.
- Universe = the seven graduated, unpaused WIN series; `KXYTVIEWSHIGH` excluded as a
  `category_of` artifact.

## Open Decisions

- **D1.** Per-book live budgets (parked as a prerequisite for a second live canary): arm without
  them, guarded by the preflight breaker read? Recommendation: yes.
- **D2.** Clip size 3 (registered) or 1. Recommendation: 3.
- Hard stops still ahead, each needing the owner's words: merge (touches a live safeguard), the
  revision activation, `ARM_CANARY` with its env act.

## Assumptions

- Kalshi keeps reporting fill `count_fp` and settlement `value` as measured on 2026-10-16.
- The account breakers stay non-binding at the current footprint (re-read before arming).

## Non-Goals

- No global loosening of `gate:held`, the tier bar, the series pauses or any loss gate.
- No change to Kmmsell10's spec, epoch or Version. No per-book budget build (parked).
- No ops workflow change; Postgres stays read-only from the ops channel.

## Acceptance Checks

1. Fill attribution reconciles to `positions.realized_pnl` on settled shared markets — two tags,
   different clips and prices, YES and NO (`tests/test_live_fill_attribution.py`). Production
   pre-check: 16/16 shared, 2,490/2,491 single-tag within 1¢.
2. Pair list empty → every live test unchanged; XOS-000040's double clip refused
   (`tests/test_mmsell_shared_market_pair.py`, `tests/test_mmsell_live.py`).
3. Pair listed → both hold one market; combined contracts and dollars never exceed the bound
   (boundary tested both sides, partner's working order counted).
4. Closeout on a shared market never exceeds the selling tag's own filled quantity.
5. The revision's impact on every live gate is listed before activation
   (`scripts/live_attribution_impact.py`, run after merge, recorded in the review §4).
6. The book trades only WIN-set series that clear the tier and skip bars; a sports-total market
   is refused (`tests/test_mmsell_win_set_canary.py`).

## Next Step

Owner merges the WS-027 PR (hard stop). Then Platform Change Review: `REGISTER_REVISION`, run the
impact report, `RECORD_IMPACTS`; the owner confirms activation; `CUTOVER`. In parallel,
`REGISTER_PACKAGE mmsell-win-set-canary` and the paper append of `Pmmsell10`.
