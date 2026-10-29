# MMSELL fill forward read — P-SLOW and P-SPREAD on live fills since 2026-10-01

**Status:** READ RULES FIXED 2026-10-29 (Research Lab), before any forward outcome was read by this
session. Verdict: _pending — §5 after the run._

Follow-up to the selection-bias audit (`docs/MMSELL_SELECTION_BIAS_AUDIT.md` §8), which found that the
MMSELL edge depends on which resting orders fill. The owner asked for a fill study next (2026-10-29).
Experiment OS is canonical for every experiment object; this records a probe read and authorizes
nothing.

## 1. Why a forward read, not a new fill study

Most fill-side levers on this book are already pre-registered and closed:
- killed: flow veto, queue depth, reactive cancel, young series;
- null: offset, chase and queue-aware cancel;
- dropped after looking: fill speed (it flips by month) and the 1¢-spread skip (it failed out of sample).

A fresh search for "what makes a fill lose" on the same ~1,500 live fills would be another post-hoc split.

Two fill tests were frozen on 2026-10-03 (`docs/MMSELL_REPLAY_PROBES_20261003.md`, *Pre-registration*,
verbatim) with forward legs on live orders from **2026-10-01T00:00Z**, and neither has been read since:

- **P-SLOW**: the slow-information cell (`event_stat, mention, exact_score, outright, game_prop`) against
  every other classified type. DEC-033 sized this cell at 3 contracts *before* the floor, and says going
  larger needs P-SLOW to pass.
- **P-SPREAD**: decision-time YES spread ≥ 3¢ against ≤ 2¢.

The bars below are copied from that document unchanged.

| test | PASS | KILL | else |
|---|---|---|---|
| P-SLOW (forward) | ≥ 100 cell fills, cell mean ≥ +2.0¢, separation > 0 with bootstrap 5th pct > 0 | none pre-registered for the forward leg | HOLD (below floor) or NO PASS (at floor) |
| P-SPREAD | ≥ 80 fills with spread ≥ 3, mean(≥3) − mean(≤2) ≥ +3.0¢, bootstrap 5th pct > 0 | separation ≤ 0 at the floor | HOLD |

## 2. Read rules fixed now

These are the details the 10-03 text leaves to the reader, settled before reading.

- **Book lineage.** The forward legs name `Fmmsell10` "or a registered successor of the same book".
  The successors are declared in `docs/DECISIONS.md`: `Fmmsell10` → `Hmmsell10` (DEC-025) →
  `Jmmsell10` (DEC-027) → `Kmmsell10` (DEC-033). The sample is live buy-NO orders on these four tags.
  Every other live tag, `Vmmsell10` among them, is printed as a diagnostic and never decides.
- **Order and fill.** The order's decision instant (`execution_order_context.decided_at`, else
  `live_orders.created_at`) must be ≥ 2026-10-01T00:00Z. Filled means filled quantity > 0 (WS fill
  events, else REST fills).
- **Outcome.** `settle_NO − limit` in cents at one contract, whatever the clip size. Settlement comes
  from Kalshi's public `result`; anything that is not yes/no is excluded and counted. This replaces the
  10-03 text's "settlement from the paper record" for the same quantity, because the paper record does
  not exist for every live ticker.
- **Spread.** `execution_order_context.spread` joined by `live_order_id`. Orders without context are
  excluded from P-SPREAD and counted.
- **Bootstrap.** Settlement-date blocks, 5,000 resamples, seed 20261003, as on 10-03.
- **Known exposure.** DEC-033 saw the cell's first 25 settled forward markets on `Jmmsell10`
  (+7.05¢). That does not move a bar. It is stated so the read is not presented as blind.

## 3. Probe

`scripts/mmsell_fill_forward_read.py`, which is read-only and ops-allowlisted. It also prints, as
descriptive output only: fill rate and realized ¢ per fill by market type, by tag, and by week.

## 4. What a result means

- **P-SLOW PASS** makes the cell eligible for an owner decision on more than 3 contracts. That is a
  real-money step through a successor package and a hard stop.
- **NO PASS or HOLD** keeps 3 as the ceiling.
- **P-SPREAD PASS** would nominate a spread filter for its own canary. **KILL** closes it.

## 5. Results

_Pending._
