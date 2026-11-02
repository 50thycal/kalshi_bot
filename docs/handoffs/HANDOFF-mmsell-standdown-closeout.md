# HANDOFF — close out the seven stood-down MMSELL live experiments

- **From:** Research Lab, 72-hour research review (`docs/research_reviews/2026-11-01.md`).
- **To:** a **Live Ops** session. These are live experiments, and the path touches the live
  worker's config and record.
- **Owner approval:** Calvin, 2026-11-01, in reply to the review: "Yep close those". Use
  `approved_by: "Calvin"` with the reason below. Approval covers retiring these seven and
  nothing else.
- **Reason (for every envelope):** "Stood down; superseded by mmsell-price-ceiling-cell-size-2 /
  mmsell-win-set-3 (Xmmsell10 / Ymmsell10). No live orders; closed on owner direction
  2026-11-01."

## The seven

| experiment | live deployment | live tag | stood down (last live order) |
|---|---|---|---|
| `mmsell-price-ceiling` | `mmsell-ceiling-live-1` | `Cmmsell10` | see `live_orders` |
| `mmsell-price-ceiling-capacity` | `mmsell-capacity-live-1` | `Dmmsell10` | see `live_orders` |
| `mmsell-price-ceiling-contest-cap` | `mmsell-contestcap-live-2` | `Fmmsell10` | see `live_orders` |
| `mmsell-price-ceiling-size-split` | `mmsell-sizesplit-live-2` | `Jmmsell10` | see `live_orders` |
| `mmsell-price-ceiling-cell-size` | `mmsell-cellsize-live-1` | `Kmmsell10` | 2026-10-30 13:32Z |
| `mmsell-win-set` | `mmsell-winset-live-1` | `Nmmsell10` | 2026-10-27 07:34Z |
| `mmsell-win-set-2` | `mmsell-winset2-live-1` | `Vmmsell10` | 2026-10-30 13:32Z |

All seven read `EXPERIMENT_EXECUTION_STOOD_DOWN` in the Control Tower (2026-11-01 14:21 CST):
none of the tags is in `LIVE_STRATEGIES`.

## What makes this more than one envelope

1. **`STAND_DOWN` refuses an experiment with an open LIVE deployment** (`OPS_RUNBOOK.md`,
   "refused on an open LIVE deployment"). Ending those rows first needs a reviewed
   `REPAIR_LINEAGE` package. The precedent is `experiment_os/repair_dark_live_canaries.py`
   (XOS-000012): literal targets; refusals if a tag is back in `LIVE_STRATEGIES` or has a live
   order after the measured last one; idempotent.
2. **The paper books still trade.** In the 7 days to 2026-11-01, `paper_trades` shows
   `Cmmsell10`, `Dmmsell10`, `Fmmsell10`, `Jmmsell10` and `Kmmsell10` at about 430 to 1,000
   trades each, and `Nmmsell10` and `Vmmsell10` at about 30 each, all through today. Their
   `MMSELL_VARIANTS` entries were kept on purpose: removing one put `mmsell-win-set` into
   BLOCKED_INTEGRITY on 10-27 (drift #28, WS-027). So decide, and verify on one book first,
   whether `RETIRED` (which closes the epoch and cascades its deployments) plus removing the
   entries is clean. Removing an entry is an `env` write. Read the live value and compose it;
   never retype it.
3. **Pending platform impacts** (accepted `I2/NEW_EPOCH`, not applied) sit on
   `-price-ceiling`, `-capacity`, `-contest-cap`, `-cell-size` and `-size-split`, and
   `-size-split` has an unresolved `EXPERIMENT_CONFIG_DRIFT` integrity event. Check whether
   either blocks `STAND_DOWN` (Platform Change Review owns the impacts).
4. **Do not touch** `Xmmsell10`, `Ymmsell10`, their twins (`*_pt4`), `Smmsell10` or
   `Zmmsell10`, or the `MMSELL_LIVE_SHARED_MARKET_PAIRS` value.
5. **P-SLOW v2 (DEC-040) reads `Kmmsell10` and `Vmmsell10`'s `live_orders` and `fills`
   directly.** Retiring in Experiment OS does not change those rows. Do not delete anything.

## Acceptance

- `xos list` shows all seven `RETIRED`.
- `xos control-tower` shows no new integrity event or anomaly.
- The seven tags write no new `paper_trades` (24 h readback).
- `X` and `Y` keep trading.
- The open XOS issues scoped only to these experiments are re-pointed or closed with a reason.

One transport envelope array at a time (single slot), each receipt read before the next. Close
with the brief.

## Outcome (Live Ops, 2026-11-01 23:35Z)

**Five retired 2026-11-01; the last two retired 2026-11-02 on the owner's decision (below). All seven RETIRED.**

| experiment | result | receipt |
|---|---|---|
| `mmsell-price-ceiling-capacity` (D) | RETIRED, e2 closed (verified first, alone) | `lo-mmclose-sd-capacity` |
| `mmsell-price-ceiling-contest-cap` (F) | RETIRED, e3 closed | `lo-mmclose-sd-contest-cap` |
| `mmsell-price-ceiling-size-split` (J) | RETIRED, e3 closed | `lo-mmclose-sd-size-split` |
| `mmsell-win-set` (N) | RETIRED, e2 closed | `lo-mmclose-sd-mmsell-win-set` |
| `mmsell-win-set-2` (V) | RETIRED, e2 closed | `lo-mmclose-sd-mmsell-win-set-2` |
| `mmsell-price-ceiling` (C) | **held**: live row ended, still LIVE_CANARY | — |
| `mmsell-price-ceiling-cell-size` (K) | **held**: live row ended, still LIVE_CANARY | — |

**What was done, in order:**

1. **Repair package.** `mmsell-standdown-live-repair` was added in #715 and #716 (precedent `repair_dark_live_canaries`):
   - targets are literals;
   - it refuses on the allowlist, by prefix;
   - it refuses if a tag has a live order after its own measured last order;
   - it is all-or-nothing and idempotent.

   The first run (`lo-mmclose-repair-1`) was REJECTED with `TAG_STRANDED` by the XOS-000033 transport guard, and rolled back. That guard refuses to end a tag's arm while the worker still constructs it.
2. **The fix for drift #28.** #716 declares the seven tags as `retires_tags`. One env act (`lo-mmclose-repair-2`) then set two things together:
   - `MMSELL_VARIANTS` minus the seven live-tag books, composed from the live value;
   - the repair envelope.

   The boot hook runs the command (`main.py:290`) before `runtime_config_check` (`main.py:360`). So the live rows ended before the drift check ran, and the check reads OPEN live rows only. No drift event was recorded.
3. **`STAND_DOWN` → RETIRED**, capacity first. The check that followed found no new integrity event, and base paper books resumed at 23:24Z. Then the other four followed. Pending platform impacts did not block `STAND_DOWN`, which does not check them.
4. **Env act `lo-mmclose-env-pq`.** It removed `Pmmsell10` and `Qmmsell10`, the win-set paper arms, which had no arm after retirement. It also cleared the command slot.

**Untouched:**
- `LIVE_STRATEGIES` (`Xmmsell10,Alimm1,Ymmsell10`);
- `MMSELL_LIVE_SHARED_MARKET_PAIRS`;
- X/Y/S/Z and their twins;
- `mmsell9` and `mmsell10`;
- no `live_orders` or `fills` row was deleted.

**Readback** (`lo-mmclose-q9`): no integrity event since 22:15Z. No paper write from C/D/F/J/K/N/V/P/Q since 23:02Z.

### DECISION (owner): the two held experiments — resolved 2026-11-02, option (a)

Retiring either one also stops a paper book that is not on the approved list:

- `mmsell-price-ceiling` holds `mmsell9` (about 560 paper trades in 7 days). It sits on v1 epoch 2, which `STAND_DOWN` does not close, so retiring would also leave a dangling open deployment.
- `mmsell-price-ceiling-cell-size` holds base `mmsell10` (about 1,000 in 7 days), its only open arm.

Options:
- (a) retire anyway, which stops `mmsell9` and `mmsell10`;
- (b) re-home `mmsell9` and `mmsell10` to an experiment of their own first, then retire;
- (c) leave both as they are.

The live rows are already ended either way, so nothing here touches real money.

### Still open
- The 24-hour readback: no paper writes from the retired tags, and X/Y still trading.
- Ten open XOS issues are linked to these experiments:
  - XOS-000013, -014, -016, -020, -022, -024, -038, -039, -040, -041.
  - Most are owned by Research Lab.
  - Several describe mechanisms the X/Y successors inherit (contest cap, live-mirror cap, cutover stacking).
  - Each owner should re-point or close them. Nothing was closed here.
- The accepted platform impacts (`I2/NEW_EPOCH`) on the retired experiments are Platform Change Review's to resolve.

## Outcome part 2 (Live Ops, 2026-11-02 14:52Z): the held two retired

**Owner decision (Calvin, 2026-11-02):** "Retire them anyway, mmsell9 and mmsell10 can stop" (option a).

1. **Stale epoch.** `mmsell-price-ceiling` also had a stale open v1/e2 holding `mmsell9`, and `STAND_DOWN` closes only the latest version's epoch. So #719 added `mmsell9-stale-epoch-repair`. It closes v1/e2 and declares `mmsell9` retired. Env act `lo-mmclose-m9-1` removed `mmsell9` from `MMSELL_VARIANTS` and ran the repair in the same boot: SUCCEEDED.
2. **Retirements.** `STAND_DOWN` → RETIRED for `mmsell-price-ceiling` (`lo-mmclose-sd2-mmsell-price-ceiling`) and `mmsell-price-ceiling-cell-size` (`lo-mmclose-sd2-mmsell-price-ceiling-cell-size`). Each closed e2 and ended its paper and twin rows.
3. **Cleanup.** Env act `lo-mmclose-env-m10` removed `mmsell10` from `MMSELL_VARIANTS` and cleared the command slot.

**Readback** (`lo-mmclose-q11`):
- All seven are RETIRED, with no open epoch or deployment left on any of them.
- No integrity event since 2026-11-01 22:15Z.
- `Xmmsell10`/`Ymmsell10` placed live orders and wrote paper trades today, and so did `Smmsell10`/`Zmmsell10`.
- `LIVE_STRATEGIES` and `MMSELL_LIVE_SHARED_MARKET_PAIRS` are unchanged.

The 24-hour readback is scheduled (routine `trig_01G5Kcz5kRqoktWxmMzVDor6`, 2026-11-02 23:40Z).

## 24-hour readback (2026-11-02 23:42Z): PASS

Ops results: `lo-mmclose-q12`, `lo-mmclose-q13`.

1. **The closed books stay closed.**
   - No `paper_trades` since 2026-11-01 23:02Z for C/D/F/J/K/N/V/P/Q`mmsell10`.
   - None since 2026-11-02 14:50Z for `mmsell9` and `mmsell10`.
2. **The successors keep trading.** In the last 24 hours, `Xmmsell10` placed 6 live orders and 6 paper trades, and `Ymmsell10` placed 5 and 5. `Smmsell10` and `Zmmsell10` wrote 6 and 5 paper trades. Their last write was at 13:26Z.
   - Their universe is narrow, about 20 trades a week.
   - The mmsell loop itself is healthy: the other books (`mmsell`, G/T/Temp) wrote through 23:30Z.
3. **No new integrity event** since 2026-11-01 22:15Z.
4. **All seven RETIRED**, with zero open epochs or deployments.

The close-out is complete. The ten linked XOS issues and the accepted platform impacts on the retired experiments remain with their owning roles: Research Lab, Live Ops and Platform Change Review.
