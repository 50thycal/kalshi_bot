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
