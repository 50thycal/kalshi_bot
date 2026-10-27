# WS-027 — WIN-set live canary beside Kmmsell10, with shared-market accounting

**Phase:** REVIEW
**Status:** Active
**Created:** 2026-10-16
**Updated:** 2026-10-16 (revision ACTIVE; WIN-set paper accruing; arming with the owner present)
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

- ~~D1~~ / ~~D2~~ **decided by the owner, 2026-10-16, in session
  `01D3m7Af5FnpLDG5eDQ6JJRL`** ("I am good with you building that recommendations for both d one
  and d two"): D1 — arm without per-book budgets, the arming preflight re-reads the account
  breakers; D2 — 3-contract clip, as registered.
- ~~Revision activation~~ — confirmed by the owner and done 2026-10-16 09:34Z.
- `ARM_CANARY` with its env act — the owner wants it as soon as possible; it runs with the owner
  present once v1's promotion gate passes (see Next Step).

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
   (`xos attribution-impact`, run after merge, recorded in the review §4).
6. The book trades only WIN-set series that clear the tier and skip bars; a sports-total market
   is refused (`tests/test_mmsell_win_set_canary.py`).

## Next Step

**2026-10-16 09:34:11Z — `METRICS_ENGINE:live_fill_attribution_2026_10` ACTIVE** (owner confirmed by
name in this session; `CUTOVER` `ws027-metrics-cutover-1`; 6 RECOMPUTE + 8 NO_ACTION applied). Control
Tower after: cell-size and size-split gates back to their normal reads; nothing newly blocked.

**Arming.** The owner asked (2026-10-16) to arm the WIN-set canary now rather than after the paper
bar. Two routes were refused by the session's safety layer and NOT taken: (a) a v2 Version whose
only change lowered the promotion bar to "trading its universe" — refused as weakening a
safeguard; (b) an unattended routine that would arm when the bar passes — refused as an
unattended real-money transaction. So the canary arms **with the owner present**, through v1's
registered gate (≥ 30 settled paper trades on `Pmmsell10`, positive ¢/contract). Measured pace:
WIN-series paper settles ~5/day (`ws027-paper-1`), so the gate should pass around **2026-10-22**.
Then: preflight (`docs/MMSELL_WIN_SET_CANARY.md` §6) → `ARM_CANARY` (owner) → the one env act.
If the owner wants to arm sooner, that is a v2 promotion-bar change the owner must make or
approve explicitly, as a reviewed PR.

**2026-10-16, later — the owner approved exactly that** ("you can arm it now … as I give explicit
permission"; the session's auto mode was switched off by the owner so the change could be
written). v2 = `mmsell-win-set-owner-waiver`: the promotion bar only (paper not down more than
$3.00 since v2), every live stop verbatim; `docs/MMSELL_WIN_SET_CANARY.md` §8. After merge:
REGISTER_PACKAGE → ARM_CANARY (approved_by Calvin) → the env act → readback.

**2026-10-16 14:11Z — ARMED.** PR #649 merged by the owner; `ws027-waiver-register-1` SUCCEEDED
(v2); `ws027-arm-canary-2` SUCCEEDED (`ws027-arm-canary-1` was REJECTED on the approver string's
format, nothing written) — `mmsell-winset-live-1` + `mmsell-winset-twin-1` at one instant, epoch 2,
I2. Env act `ws027-env-arm-1` VERIFIED: `Nmmsell10` appended after `Kmmsell10` in
`MMSELL_VARIANTS` (live value read, not retyped), `LIVE_STRATEGIES=Kmmsell10,Alimm1,Nmmsell10`,
`MMSELL_LIVE_SHARED_MARKET_PAIRS=Kmmsell10+Nmmsell10:6:6.00`; worker redeployed 14:14:58Z.
`xos tag Nmmsell10` → LIVE_CANARY v2 e2. Open: the 24h silent-arm check and the first settled
shared market's reconciliation (§6 line 6).

**Side effect to route:** registering `mmsell-win-set` added it to the unaccounted list of the two
other pending revisions (`EXECUTION_ENGINE:shared_primary_ownership_20260921`,
`MARKET_TAXONOMY:football_coverage_2026_10_12`); whoever cuts those over must include it.
**Routed and recorded 2026-10-20:** I0/NO_ACTION on both revisions (#47 and #53, applied). The
win-set gates are unaffected. See `docs/EXPERIMENT_OS_PLATFORM_IMPACT.md`, "Six new experiments
on two pending revisions".

**2026-10-27 — restart with KXTRUMPMENTION (owner decision, this session).** Successor
`mmsell-win-set-2` (`Vmmsell10`, paper `Qmmsell10`) and the MARKET_TAXONOMY revision
`trumpmention_2026_10_27` are built (`docs/MMSELL_WIN_SET_CANARY.md` §9,
`docs/mmsell_taxonomy_repair/PLATFORM_IMPACT_20261027_TRUMPMENTION.md`). **Blocked** on the ops
channel's Railway token, which now returns "Not Authorized" on every env read and write: the
owner-confirmed football cutover (`fbtx-cutover-20261027`) set nothing. Unblocker: the owner
refreshes the `RAILWAY_TOKEN` GitHub secret. Then the §Sequence of the impact record, with two
owner hard stops (the new revision's CUTOVER, then ARM_CANARY + env swap). Do not merge the
successor PR before the football cutover has run.
