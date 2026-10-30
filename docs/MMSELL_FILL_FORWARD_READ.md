# MMSELL fill forward read — P-SLOW and P-SPREAD on live fills since 2026-10-01

**Status:** READ RULES FIXED 2026-10-29 (Research Lab), before any forward outcome was read by this
session. **Read 2026-10-29 (`rl-ffr-run1-20261029`): P-SLOW NO PASS at its floor; P-SPREAD HOLD
(40/80).** The 3-contract ceiling on the slow cell stands.

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

Ops run `rl-ffr-run1-20261029` on default-branch code `204330e2`.
- **Orders:** 1,103 live buy-NO orders decided since 2026-10-01; 1,017 are on the lineage tags.
- **Scored:** 599 lineage orders filled and settled. 53 orders were unsettled or void.
- **Unclassified types:** 0. **Missing spread context:** 0.

| tag | orders | fill rate | settled fills | ¢/fill |
|---|---|---|---|---|
| Fmmsell10 | 87 | 64.4% | 56 | +5.27 |
| Hmmsell10 | 39 | 43.6% | 17 | +0.59 |
| Jmmsell10 | 325 | 68.0% | 221 | −1.42 |
| Kmmsell10 | 566 | 55.5% | 305 | +0.46 |
| (diag) Nmmsell10 / Vmmsell10 | 69 / 17 | | 34 / 12 | +3.97 / +6.75 |

**P-SLOW — NO PASS (at floor).**
- **Cell:** 104 fills at **+3.00¢**, against REST 495 fills at −0.37¢.
- **Separation:** +3.37¢, but the settlement-date bootstrap 5th percentile is **−1.34¢**. The bar needs
  it above 0.
- **Clauses:** the floor (≥ 100) and the cell mean (≥ +2.0¢) pass; the interval fails.
- **No forward kill clause exists,** so this is NO PASS, not KILL.

**P-SPREAD — HOLD (accrual).**
- **Wide spread (≥ 3¢):** 40 fills at +2.52¢, against 559 narrow-spread fills at +0.05¢.
- **Separation:** +2.47¢, 5th percentile −4.11¢.
- **Floor:** 40 of 80.

**Descriptive only; never decides.**
- **Within the cell:**
  - `event_stat` (mostly rain): 57 fills, +3.53¢, 3.5% losses;
  - `game_prop`: 16 fills, +6.75¢;
  - `mention`: 22 fills, −2.55¢.
- **Largest REST type:** `h2h`, 268 fills at −0.51¢, with a 75.7% fill rate, the highest.
- **By decision week:** the cell was positive in 4 of 5 weeks; all lineage fills were positive in 3 of 5.

**Reading.**
- **The direction holds out of sample, but the interval does not clear.** The cell earns about 3¢
  more per fill than the rest of the book, yet the date-to-date spread is still too wide to rule out
  zero.
- **Under §4:** the ceiling stays at 3 contracts, and no larger size is supported.
- **Don't re-read this sample as it grows.** That would let the stopping point choose the answer.
- **If the owner wants another look:** pre-register a fresh forward window from 2026-10-30 on
  `Kmmsell10`, with the same bars and floor, read once at 100 cell fills (about four to five weeks at
  the current rate of ~20 a week).
- **The fill study adds nothing beyond this.** The study-1 finding stands: realized edge sits in the
  slow cell and in rain, not in the broad book.

## 6. Fresh forward window — P-SLOW v2 (pre-registered 2026-10-30)

Opened at the owner's request (2026-10-30), before any fill in this window exists. It is a new
sample: no fill read in §5 is reused. The bars are identical to P-SLOW.

- **Sample:** live buy-NO orders on `Kmmsell10` (or a successor declared in `docs/DECISIONS.md`)
  decided from **2026-10-30T00:00Z**. Filled, settled yes/no, one contract, settlement from
  Kalshi, settlement-date bootstrap with 5,000 resamples and seed 20261003 (all as in §2).
- **Read once, on a fixed date: 2026-12-04.**
  - **PASS:** ≥ 100 cell fills, cell mean ≥ +2.0¢, separation > 0 and bootstrap 5th percentile > 0.
  - Fewer than 100 cell fills → one extension to **2027-01-08**, read once, the same bars.
  - Otherwise **NO PASS**.
  - A fixed date, not "when it looks good", so the stopping point cannot choose the answer.
- **Command:** `{"type":"script","name":"mmsell_fill_forward_read","args":["--since","2026-10-30T00:00:00+00:00"],"id":"rl-ffr-v2-20261204"}`.
  Read only its P-SLOW block. The lineage tags other than `Kmmsell10` place no orders in this window.
- **A PASS** makes more than 3 contracts on the cell an owner decision through a successor
  package (a hard stop). It is never automatic.
