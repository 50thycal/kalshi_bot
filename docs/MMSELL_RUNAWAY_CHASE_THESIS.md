# RUNAWAY-CHASE — take the mmsell orders the market runs away from

*Thesis written 2026-09-29, before the probe ran. The predictions, bars and kill criteria are
pre-registered here and are not re-scoped after results. Operator request (Calvin, 2026-09-29):
"build the checks for 1 and 2 first and see how those play out." Status: **PROBE BUILT — not
yet run.** Companion probe: [`MMSELL_FLOW_VETO_THESIS.md`](MMSELL_FLOW_VETO_THESIS.md).*

## One-liner

When a resting mmsell NO bid is leapfrogged by a better NO bid (the market is moving our way and
our maker order will probably never fill), cancel it and buy the one contract at the NO ask, if
the ask is within 2¢ of our limit. That converts the missed winners into fills.

## Why — the gap this targets (measured 2026-09-29, Fmmsell10 epoch from 2026-09-07)

The live book's paper twin, split by what live did with the same market (ops `mmideas-q1b-0929`):

| twin trades | n | win % | ¢/trade |
|---|---|---|---|
| live filled | 674 | 92.9% | +0.14 |
| live rested, **never filled** | 197 | **99.0%** | **+6.56** |
| live never ordered | 240 | 96.3% | +3.14 |

On the 664 markets both sides settled, twin +0.22¢ vs live +0.31¢ (`live_paper_parity`,
`mmideas-parity-0929`). The simulator is right about price. The gap is **which** orders fill.
The rested-but-unfilled bucket is +$12.93 of the twin's +$21.44, and real money holds none of it.
This matches `MMSELL_FILL_MODEL.md` (unfilled +3.77¢ vs filled −0.67¢ on mmsell3) and
`OPS_FMMSELL10_PARITY_DIAGNOSIS.md` (+6.56¢/ct on the 66 unfilled markets of 2026-09-13).

## Mechanism

A post-only NO bid at the best level fills when a YES buyer hits it. Informed YES buying moves
the market against us, so the fills we get skew toward losers. When the market moves **our**
way, other NO bidders step in above us and nothing trades down to our price, so the order sits
until the 4 h timeout. The signal that we have been passed is observable in real time: a better
NO bid appears (`best_yes_ask < our yes-convention price`). The premise is that the orders
passed this way are drawn from the 99%-win bucket, and that crossing the spread at a 1–2¢ worse
price plus the taker fee still leaves most of the +6.56¢.

**Who is on the other side:** the YES bidder at the best YES bid, typically the same retail
longshot buyer the whole mmsell book sells to. We pay them a slightly better price, immediately.

**Why it could fail (honest priors):**

- The trigger may fire on orders that would have filled passively anyway (a better bid can be
  pulled). Each of those costs `(C − L) + fee` versus the passive fill.
- A better NO bid may not mark a winner at all. It may be noise from a quote flickering over a
  1-contract level.
- The pooled taker entry measured −2.64¢ in `MMSELL_TIMING_STUDY.md`. This is a **conditional**
  taker entry (only after our side has been passed), which is mechanically different. But the
  taker graveyard is real, and the prior is **MEDIUM-LOW**.

**Not a duplicate or a revival:**

- The killed 1¢ price-improvement arm (`MMSELL_OFFSET_AB.md`) bid more at *entry*, unconditionally,
  which bought the toxic queue front. This pays up only *after* the market has moved our way.
- Queue-aware early cancel (`MMSELL_QUEUE_AWARE_CANCEL.md`, RETIRED) only cancelled; it never
  took liquidity.
- Taker+endgame (`MMSELL_TIMING_STUDY.md`, proposed, not built) triggers on time to close, not
  on being passed.

## Pre-registered probe (`scripts/mmsell_chase_probe.py`)

**Dataset (provenance: bot DB, WS-019 collector, plus Kalshi public REST for settlement only).**

- Every `live_orders` NO buy of the live tag (default `Fmmsell10`) created since the WS-019
  collector went live (default `2026-09-16T00:00Z`), with a Kalshi order id.
- Its `live_order_queue_ticks` rows that carry `features_json`. The collector derives these from
  the reconstructed order book on every delta, trade and 20 s interval. The fields read are
  `best_yes_bid`, `best_yes_ask`, `our_price` and `book_valid`.
- Fill time and quantity come from `execution_fill_events` (exchange ms). The fallback is
  `fills.raw_fill_json.created_time`, and failing that `fills.filled_at − 300 s`. The early shift
  is deliberate: the reconcile timestamp is up to 5 min late, and a late fill time would let the
  trigger fire after we had already filled.
- Cancel time is the first `execution_order_events` row with status canceled.
- The settlement result is `GET /markets/{ticker}` → `result`, used for **scoring only**.

**Trigger (frozen; the primary arm is K = 2¢, persistence 15 s).**

1. Walk the order's ticks in time order, and keep only ticks that satisfy all of the following:
   - taken before the first fill and before the cancel;
   - `trigger != 'terminal'`;
   - `book_valid` is true;
   - `remaining_count` is NULL or > 0;
   - both best prices are present.
2. Let `p = our_price` (yes convention, = 100 − limit NO price). A tick satisfies the condition
   when `best_yes_ask < p` (a better NO bid exists) **and** `p − K ≤ best_yes_bid < p` (the NO
   ask, `100 − best_yes_bid`, is within K¢ of our limit).
3. The trigger fires at the first tick where the condition has held on every consecutive valid
   tick for ≥ 15 s. At that tick the chase cost is `C = 100 − best_yes_bid` ¢.

**Counterfactual, per triggered order, one contract.**

- `policy = settle_NO − C − fee(C)`. The taker fee is `fee(C) = ceil(0.07·P·(1−P)·100)` with
  `P = C/100`, which is 1¢ across the 90–97¢ band.
- `actual = (settle_NO − L) × filled_qty`, where L is the resting limit. A maker fee of about
  0.01¢ is ignored. The actual fill includes any passive fill after the trigger time, which is
  exactly the fill the policy would have given up.
- `Δ = policy − actual`. Orders that are not triggered are unchanged, so their Δ is 0.

**No lookahead.** The trigger reads only ticks captured before the decision instant. The
settlement result prices nothing before it happened.

### Predictions, bars and kill criteria

| id | claim | PASS | KILL |
|---|---|---|---|
| **C0** instrument | the telemetry covers the population | ≥ 70% of orders have ≥ 1 usable tick | < 70% → **HOLD (instrument)**, not a kill |
| **C1** floor | enough triggered, settled orders | n ≥ 40 | n < 40 → **HOLD**: re-run when the floor can be met |
| **C2** primary | chasing beats resting on the triggered set | mean Δ ≥ **+2.0¢** per triggered order **and** the bootstrap 5th percentile of mean Δ (2,000 resamples, seed 20260929) is **> 0** | mean Δ **≤ 0** at n ≥ 40 |
| **C3** chase set is +EV on its own | the policy side makes money | mean `policy` > 0 | mean `policy` ≤ 0 at n ≥ 40 |

**Decision rule.**

- **PROMOTE** = C0, C1, C2 and C3 all pass. That makes the chase rule a candidate treatment arm
  for a successor live canary, pending operator approval. Arming is a hard stop, and a probe
  PASS does not authorize it.
- **KILL** = either kill cell trips. Close the family.
- **HOLD** = anything else.

**Reported but not decision-driving:**

- K = 1 and K = 3 sensitivity, and persistence of 0 s and 60 s;
- the share of triggered orders that later filled passively, and their outcomes;
- chase-set win rate against the mean chase-cost breakeven;
- Δ by series.

Only the frozen K = 2 / 15 s row decides.

**Known limits, stated before the run.**

- The collector subscribes a market only once our order exists, so a trigger cannot be observed
  before placement. That is fine: the rule only acts while the order rests.
- The book is our WS reconstruction. A 1-contract ask may be gone by the time a taker order
  lands, so the probe treats the fill at the observed ask as optimistic.
- A freed slot could hold another order, and that value is not counted.

## Cost, capacity and value to the $100/month goal

At today's volume (~197 unfilled of 871 orders over 22 days), a +3.5¢ chase on half of them is
about +$4–5/month at 1 contract. The rule's value is that it lifts the book's per-trade edge
toward the twin's +1.93¢. Size is what scales it (idea #3, not built).

## Correlation

This is the same book and the same return driver as Fmmsell10. It is an **execution** change,
not a new edge, and adds no diversification.

## RESULTS

*(not yet run)*
