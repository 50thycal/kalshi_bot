# QUEUE-DEPTH — are mmsell fills that land behind a deep queue less adversely selected?

*Thesis written 2026-09-30, before the probe ran. The predictions, bars and kill criteria are
pre-registered here and are not re-scoped after results. Operator request (Calvin, 2026-09-30):
keep the live canary open for its fill information and pre-register the one lever the telemetry
itself generated. Origin: [`IDEA_MODEL_20260930_MMSELL.md`](IDEA_MODEL_20260930_MMSELL.md)
follow-up. Status: **KILL (probe run 2026-09-30, Q2) — queue position is closed as a lever on this book; see RESULTS, including the one observation carried forward.** Probe: `scripts/mmsell_queue_depth_probe.py` (#501).*

## One-liner

A post-only NO bid that lands with many contracts already ahead of it at its price fills only
after that queue is consumed, so it is hit by the *tail* of a flow rather than its *head*. The
prediction is that fills behind a deep queue earn more per contract than fills near the front,
and that a **post-then-verify** rule (read the queue immediately after landing; cancel if fewer
than `D` contracts are ahead) raises the book's realized dollars.

## Which bucket it moves, and why the fill does not select against it

It moves the **filled** bucket by refusing the fills that arrive first. Every clean fact on this
book points the same way, from three independent instruments:

| read | finding |
|---|---|
| `MMSELL_OFFSET_AB.md` (real money, 2026-08) | +1¢ priority put 77% of orders at the front of the queue and realized **−3.29¢** per fill vs +0.84¢ at the join |
| `mmideas-q2b-0929` (Fmmsell10 fills by age) | fills inside 3 min −0.67¢, 3–10 min −0.82¢; **10–30 min +1.79¢**, 90–180 min +2.32¢ |
| `MMSELL_QUEUE_AWARE_CANCEL.md` (shadow, 2026-09) | deep-queue orders the cancel rule would have killed filled later 27.5% of the time at **+1.65¢** each |

The front of the queue is where informed flow lands; the back is where the flow that remains
after the informed taker is done lands. This is not a prediction about which market to post in
(three of those were null on 09-29) or about paying for priority (killed). It is the mirror of the
offset kill: instead of buying the front, decline it.

**Not a duplicate.** The queue-aware cancel (retired 09-10) cancelled *deep* queues because they
looked unlikely to fill; this declines *shallow* ones because they look likely to fill badly. The
scan-time `depth_at_best_ask` column is **not** used: `MMSELL_DEPTH_FILL_MODEL.md` showed it is
uncorrelated with the true queue (r = 0.059) and that doc's successor gate requires the true
reading, which is what this probe uses. Spread and depth filters (roadmap §6) were about the YES
side and the spread, not our own queue.

**Why it could fail (honest priors):** (a) a shallow first reading may already be the *after* of
a sweep that then hits us, so cancelling would be too late for exactly the fills that matter;
(b) declining shallow queues cuts fill count, and the product `fills × ¢/fill` may fall even if
¢/fill rises; (c) the fastest fills (inside the first sample) carry no reading at all under the
pre-09-16 cadence, and they are the most toxic, so the measured contrast is biased *against* the
hypothesis. Prior **MEDIUM**: it is the only lever the telemetry itself generated.

## Pre-registered probe (`scripts/mmsell_queue_depth_probe.py`)

**Samples (provenance: bot DB only; settlement from public Kalshi REST, as the 09-29 probes).**

- **Primary:** live NO buys of `mmsell10a`, `mmsell10b`, `Lmmsell10`, `Cmmsell10`, `Dmmsell10`,
  `Emmsell10` created from `2026-08-14T00:00:00Z` (queue sampling began) to
  `2026-09-07T02:03:36Z`. Independent of every read that motivated the idea.
- **Confirmation:** `Fmmsell10` from `2026-09-07T02:03:36Z` to the run instant.
- **Robustness (reported only):** the subset whose first reading is an `at_rest` tick within
  90 s of creation (WS-019 collector, from 2026-09-16), where the reading is closest to the
  landing instant.

**Feature (frozen): `AHEAD`.** `contracts_ahead` on the order's **first** `live_order_queue_ticks`
row with a non-null reading whose `captured_at` is within **600 s** of the order's creation. An
order with no such row has no feature; if it filled, it is **censored-fast** (it filled before it
could be read) and is carried separately, never dropped silently.

**Split (frozen, absolute, outcome-blind):** **DEEP** = `AHEAD ≥ 500` contracts; **NOT-DEEP** =
`AHEAD < 500`. Bands `< 50`, `50–199`, `200–499`, `500–1,999`, `≥ 2,000` are reported for shape
only and decide nothing. The 09-29 telemetry read (`mmideas-telem-0929`) put the median at 962
and the 25th percentile at 36, so both sides are populated by construction, not by outcome.

**Outcome (real money):** per filled order, `(settle_NO − limit) × min(filled, 1)`; fill time and
quantity as in `mmsell_flow_veto_probe.py`; settlement from `GET /markets/{ticker}`.

### Predictions, bars and kill criteria

| id | claim | PASS | KILL / HOLD |
|---|---|---|---|
| **Q0** instrument | `AHEAD` is readable | a reading within 600 s for ≥ 80% of orders in each sample; censored-fast share reported | < 80% → **HOLD (instrument)** |
| **Q1** floor | both sides are readable in primary | ≥ 100 settled DEEP fills **and** ≥ 100 settled NOT-DEEP fills | fewer → **HOLD (accrual)**; the confirmation sample may still KILL under Q3 |
| **Q2** primary | deep fills are less adversely selected | `mean(DEEP) − mean(NOT-DEEP) ≥ +1.0¢` **and** bootstrap 5th percentile `> 0` | separation `≤ 0` at the floor → **KILL**; in between → **HOLD (underpowered)** |
| **Q3** confirmation | the effect holds on the live book | separation `> 0` at ≥ 100 / ≥ 100 settled fills | separation `≤ 0` at the floors → **KILL** |
| **Q4** policy value (confirmation) | declining shallow queues raises dollars | `$(DEEP fills) + $(censored-fast fills) ≥ $(all fills)` | otherwise **HOLD (policy)**: the contrast exists but the gate costs money |

Censored-fast fills count on both sides of Q4 because the policy could not have cancelled them
either. Q4 is also printed at `D ∈ {100, 200, 500, 1000, 2000}` for shape; only `D = 500` decides.

**Decision rule (pre-committed).**

- **PROMOTE** = Q0–Q4 all pass. "Post-then-verify at D = 500" becomes a candidate **execution
  treatment** for a successor live canary: it changes the executor (a cancel on the first queue
  read), so it is an operator hard stop with its own envelope, twin and gate, and it is never
  switched on in the running book.
- **KILL** on Q2 or Q3 closes queue position as a lever on this book. That is the last lever the
  telemetry can see; the idea-model run's stop-investing recommendation then stands unconditionally.
- **HOLD** on Q1 waits on the confirmation sample only if Q3 has not already decided.

**Reported alongside (never decisive):** per-band fill rate, win rate and ¢/fill in both samples;
the robustness subset; fills/day kept under the policy and the implied $/month at size 1 and 3.

## Cost + capacity

- **Fees:** the cancel is free; maker fills bill ~0.01¢. No fee changes.
- **What it costs:** fills. If ~30% of fills are NOT-DEEP, the book keeps ~21 fills/day. At the
  fill-age numbers above (+1.5 to +2¢ on the kept set) that is roughly **$10–13/month at size 1**,
  and it makes CELL-SIZE's question live again for the kept set only if Q4 passes. Stated up
  front: a PASS does not reach $100/month; it would be the first lever on this book to move the
  per-fill number at all.

## Correlation

Same return driver as the live book; a refinement, no diversification credit.

## RESULTS

**Probe run 2026-09-30 (ops `qdepth-20260930-1`, code `078a9480`). Verdict: KILL on Q2.**

| sample | group | orders | fill rate | settled fills | win | realized/fill |
|---|---|---|---|---|---|---|
| primary (6 pre-9/7 books from 08-14, 1,039 orders) | DEEP (≥ 500) | 279 | 45.5% | 127 | 92.9% | −0.06¢ |
| | NOT-DEEP | 556 | 49.6% | 276 | 92.4% | +0.29¢ |
| confirmation (Fmmsell10, 1,033 orders) | DEEP | 458 | 64.6% | 285 | 94.0% | **+1.13¢** |
| | NOT-DEEP | 397 | 63.2% | 238 | 91.2% | **−1.49¢** |
| robustness (`at_rest` reading ≤ 90 s, WS-019) | DEEP | 271 | 63.8% | 162 | 94.4% | +1.59¢ |
| | NOT-DEEP | 167 | 65.9% | 97 | 92.8% | +0.27¢ |

| gate | result |
|---|---|
| Q0 instrument | reading inside 600 s for 80.4% (primary) / 82.8% (confirmation) of orders → PASS at the 80% bar; censored-fast fills 161 of 564 and 178 of 725 |
| Q1 floor | 127 deep / 276 not-deep settled fills → PASS |
| Q2 primary | separation **−0.35¢**, bootstrap 5th percentile −4.93¢ → **KILL** (≤ 0 at the floor) |
| Q3 confirmation | separation +2.62¢, bootstrap 5th percentile **−1.01¢** (would not have cleared a positive-interval bar; not reached under the rule) |
| Q4 policy value | at every D the policy dollars exceed all-fills dollars ($6.42 vs $2.88 at D = 500) (not reached) |

**What it found.** On the independent sample the effect is absent: deep and not-deep fills earn
the same within noise, and the per-book signs flip (Cmmsell10 −0.69¢, Dmmsell10 −1.19¢,
Lmmsell10 +0.39¢). The band shape is not monotone in either sample (primary: 500–1,999 +3.15¢ but
≥ 2,000 −2.01¢; confirmation: 500–1,999 −2.42¢ but ≥ 2,000 +2.38¢). Under the pre-committed rule
that is a KILL, and it stands.

**The observation carried forward, stated as an observation.** On the live book the separation is
+2.62¢ with the whole policy-dollar curve above the all-fills line, and the WS-019 subset (readings
within 90 s of landing, the cleanest measurement) reads +1.32¢. Its bootstrap interval includes
zero at every cut, so it is consistent with the primary null. It is also the only sample whose
readings are close to the landing instant; the primary's readings were up to 600 s late, which
the thesis flagged as a bias against the hypothesis. That is a reason for a **forward** test on
fills the idea has never seen, not a reason to reread this one: a new pre-registration on
Fmmsell10 (or its successor) fills from 2026-10-01 with `at_rest` readings only, floors ≥ 100 per
side, PASS only on a positive bootstrap interval. No such test is scheduled by this document.

**Decision (pre-committed).** Closed. Queue position joins execution (offset, chase, cancel),
selection (flow, volume, series P&L, series age) and sizing as null on this book under
pre-registered bars.
