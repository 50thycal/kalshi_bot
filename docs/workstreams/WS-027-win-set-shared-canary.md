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
owner hard stops (the new revision's CUTOVER, then ARM_CANARY + env swap).

**2026-10-27 03:01Z — #676 merged by the owner before the football cutover**; the worker has served
the new taxonomy since 03:02:20Z. Football #13 can no longer cut over; `trumpmention_2026_10_27`
supersedes it and carries its rows. Revised sequence, the drift window and one owner decision
(`mmsell-type-tight` stays blocked by #13's unapplied NEW_EPOCH): the impact record's addendum.
Still blocked on the token.

**2026-10-27 03:20Z — token refreshed by the owner; steps 2–3 done.** `tm-register-20261027`
SUCCEEDED (revision #15, PENDING); `tm-impacts-20261027` SUCCEEDED (15 rows, records #54–#68; the
three I2 rows accepted, not yet applied). Side effect until CUTOVER: the three I2 experiments read
BLOCKED_PLATFORM, `Kmmsell10`'s `live_canary_keep` among them. That gate is a recorded verdict, not
an automatic stop; `max_daily_loss` still covers every live book. Drift window measured 10:56Z
(`tm-drift-1`): **zero** KXTRUMPMENTION live orders or paper trades since 03:02:20Z. Default branch
fingerprint unchanged (`cfbd6ba5`). **Next: owner hard stop — CUTOVER `tm-cutover-20261027`**
(expect `9b3b8873…`, the three NEW_EPOCH keys), then REGISTER_PACKAGE, then ARM_CANARY + env swap.

**2026-10-27 11:18–11:39Z — cut over, registered, armed (owner confirmed each hard stop in session).**
- `tm-cutover-20261027` **REJECTED**: `carry_deployments_forward` refuses live deployments across
  an epoch boundary (`mmsell-cellsize-live-1/twin-1`); only `arm_live_canary` may create live
  lineage. Rolled back. Size-split's `mmsell-sizesplit-live-2` would refuse the same way.
- `tm-cutover-20261027b` SUCCEEDED with `new_epoch_experiments=["mmsell-type-tight"]`: revision #15
  ACTIVE at **11:23:05Z**, type-tight epoch 45. Cell-size (#54) and size-split (#56) stay ACCEPTED,
  unapplied: their gates read BLOCKED_PLATFORM until each is restarted on fresh tags.
- `REGISTER_PACKAGE mmsell-win-set-2-canary` SUCCEEDED (preflight GO); `ws2-arm-canary-1`
  SUCCEEDED (approved_by Calvin): `mmsell-winset2-live-1` + `twin-1`, epoch 2, **11:35:22Z**.
- Env act `ws2-envswap-1` VERIFIED (computed from the live values, never retyped): `Nmmsell10`'s
  `MMSELL_VARIANTS` entry replaced in place by `Vmmsell10` (still after `Kmmsell10`), `Qmmsell10`
  appended, `LIVE_STRATEGIES=Kmmsell10,Alimm1,Vmmsell10`,
  `MMSELL_LIVE_SHARED_MARKET_PAIRS=Kmmsell10+Vmmsell10:6:6.00`. `xos tag Vmmsell10` → LIVE_CANARY
  e2. `Nmmsell10`/`Pmmsell10` deployments stay open; `Nmmsell10` drains on the record.

**Owner decision (2026-10-27): Kmmsell10's restart waits for the size-up decision**, and
Kmmsell10 and Vmmsell10 restart together then. Reason: `Vmmsell10`'s registered drift baseline names
`Kmmsell10+Vmmsell10:6:6.00`, so a Kmmsell10 successor on a fresh tag now would read as
EXPERIMENT_CONFIG_DRIFT on the book just armed. Until then `Kmmsell10`'s keep gate is
BLOCKED_PLATFORM (a recorded verdict, not an automatic stop; `max_daily_loss` still binds), and a
daily routine in this session (`trig_01JKWHkT2HqxJVp5yK2X4SmK`) reads its live realized P&L against
its keep contract and tells the owner on a breach. It changes nothing itself.

Open: the 24h silent-arm check for `Vmmsell10`, and the first settled shared market's
reconciliation (§6 line 6).

**2026-10-27 13:43Z — swap defect fixed.** Replacing `Nmmsell10`'s `MMSELL_VARIANTS` entry put
`mmsell-win-set` into BLOCKED_INTEGRITY: drift #28, because a stood-down book's `book_params` are
compared in full (`enforcement._explained_by_stand_down`). The stood-down precedent (`Cmmsell10`,
`Dmmsell10`, `Fmmsell10`, `Jmmsell10`) keeps the entry. Env act `ws1-restore-1` VERIFIED
re-appended the exact registered `Nmmsell10` spec, with the tag not in `LIVE_STRATEGIES`. #28 was
reclassified as an intentional stand-down, and #29 EXPERIMENT_EXECUTION_STOOD_DOWN is open, as
recorded state. **Next swap: keep the old entry, append the new one.**

**2026-10-28 11:48Z — 24h silent-arm check: PASS** (`v24-1028`):
- **Live orders since the 11:38Z swap:**
  - `Vmmsell10`: 8 live orders on 7 markets, 2 of them on KXTRUMPMENTION.
  - `Kmmsell10`: 20 orders, 2 on KXTRUMPMENTION.
  - `Nmmsell10`: no live orders.
- **Paper side:** the twin `Vmmsell10_pt4` row is open, and twin and `Qmmsell10` paper each have 8 trades.
- **Pair:** `gate:dedup` worked on both tags. One `gate:held` on `Vmmsell10`, on `KXRAIN-26OCT27-DAL`, which draining `Nmmsell10` also holds. That is correct: `Nmmsell10` is outside the pair now.
- Kmmsell10's daily stop read (`kstop-1027-pnl`) found +$3.72 on 272 settled markets since 10-11. The worst market was -$2.82, which is the maximum loss of one 3-lot at 94c and so can never cross the $3.00 bound.

Open: the first settled shared market's reconciliation (§6 line 6). KXWCMENTION has had no
markets since 2026-07-20; drop it from the universe at the Kmmsell10+Vmmsell10 restart.

**2026-10-29 — owner decision: restart Kmmsell10 without sports and other, and Vmmsell10 with it.**
Built as `mmsell-cell-size-2-canary` (`Xmmsell10`, paper `Smmsell10`) and `mmsell-win-set-3-canary`
(`Ymmsell10`, paper `Zmmsell10`), with the pair `Xmmsell10+Ymmsell10:6:6.00` registered in both.
They arm after about a day of paper. Plan and sequence: `docs/MMSELL_WIN_SET_CANARY.md` §10.
