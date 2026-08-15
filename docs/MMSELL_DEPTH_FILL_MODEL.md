# Depth-aware maker fill model — and why the paper-side route is closed

Built 2026-08-15. Read-only measurement. **No book's behavior changed, `kalshi_bot/evo/fill_model.py`
untouched.**

> ## VERDICT — the proxy FAILED VALIDATION. The paper-side route to a queue-aware fill model is
> closed until we can observe queue position for the orders we are modelling.
>
> **The measured result, on 102 live orders carrying BOTH measures:**
>
> | | TRUE `contracts_ahead` | PROXY `depth_at_best_ask` |
> |---|---|---|
> | median | **28** | **1,082** |
> | at front of queue (== 0) | **42 / 102 (41%)** | **0 / 102 (0%)** |
> | correlation | — | **r = 0.059** |
> | observation lag | — | **6 seconds** |
>
> The two are uncorrelated, differ 39× at the median, and the proxy **never** reports
> front-of-queue where the truth reports it 41% of the time. The 6-second lag rules out staleness:
> re-running the match at 2, 5, 15 and 45-minute windows returns an identical r, because every
> pair was already near-simultaneous.

## What this was trying to do

`docs/MMSELL_FILL_MODEL.md` names maker adverse selection as the entire paper→live gap
(~2¢/contract) but conditions on **entry price alone**. Price is a proxy for the mechanism; the
mechanism is the queue. An order joining a level with 3 contracts ahead and one joining a level
with 2,000 ahead should not share a fill probability.

Queue telemetry (`live_order_queue_ticks`, PRs #210–212) measures the real thing — but only for
**live** orders, and it stopped growing when live mmsell trading ended on 2026-08-14, holding
**71 orders / 12 fill events**. Since the fill model's job is to correct **paper**, and a paper
order was never placed and so never received a queue position, the plan was to use a proxy
observable for paper candidates: `mmsell_candidate_ticks.depth_at_best_ask`, ~92,000 observations
over 23 days and still accruing.

The schema's own comment is what motivated it:

> `depth_at_best_ask` — contracts resting at the best NO bid (== the YES-ask queue). This is what a
> **MAKER entry joins, i.e. how many orders sit ahead of ours at our own price.**

**That description is not what the column empirically contains.** The comment describes the
intent; the measurement above is the test, and it fails.

## Why it fails — and why it is not repairable by better matching

The column is computed as `depth_at_best_ask = best_no[1]` — the size at the best NO-bid level in
the orderbook fetched during the entry scan. That is a real quantity; it is simply not *our queue*.
Splitting the paired orders by where our limit sat relative to that level:

| placement at scan | orders | at front | true p50 | proxy p50 |
|---|---|---|---|---|
| we **LEAD** (price > best no-bid) | 10 | **100%** | 0 | 1,095 |
| we rest **BEHIND** (price < no-bid) | 28 | **68%** | 0 | 449 |
| we **JOIN** (price == no-bid) | 64 | 20% | 400 | 1,109 |

We end up **leading a level far more often than joining one**, and a leading order has nothing
ahead of it by definition. Even in the JOIN bucket — where the proxy should be closest — it
overstates by ~2.8×.

**The structural reason: queue position is a property of the instant your order lands, not of the
instant you decided to trade.** Paper never has a landing instant. The scan-time book is a decision
input, not a queue observation, and no amount of tighter time-matching converts one into the other.

## The consequence that matters most

**The null result this script produces is not a queue result.**

Run against 1,523 usable live orders with a chronological holdout, a price×depth predictor scored
*worse* than price alone (log loss 0.6772 vs 0.6743). Read carelessly that says "queue position
adds nothing beyond price, close the line." It says no such thing. It is a null about
`depth_at_best_ask`, a column we have just shown is not queue position.

Those two readings license opposite decisions — one closes the queue-preservation/`amend` line,
the other leaves it open — which is why `print_proxy_validation` runs **first** and gates every
table beneath it.

## What the run did establish

Worth keeping, independent of the proxy failure:

* **The calibration join works.** 1,546 of 1,668 live orders (92.7%) match a book observation at
  entry, across `mmsell10a` / `mmsell10b` / `mmsell10`. Whatever feature we eventually trust, the
  live-outcome→feature pipeline is in place and powered.
* **`mmsell3` has zero coverage** — it traded 2026-07-13→19, before `mmsell_candidate_ticks`
  existed on 07-23. The *shipped* fill model is calibrated entirely on mmsell3, so any successor
  calibrated on the 10-family is a different measurement, not a refinement of the same one.
* **The censoring trap is real and live in our data.** 23 orders were cancelled by the 2026-08-14
  stand-down drain. They are OUR action, not the market declining us; counting them as non-fills
  biases every fill rate downward. They are censored, and a test pins it.
* **Paper books differ enormously in the queues they enter** (p50 `depth_at_best_ask` from 79 for
  `Wmmsell5` to 3,801 for `Wmmsell3`). That spread is real even if the column is not queue
  position, and it is the first time this dimension has been visible at all.

## What would reopen this

One of:

1. **Arm a live maker book** and let `live_order_queue_ticks` accrue. At the observed ~2.6
   orders/hour for `mmsell10a`, a few hundred fill events is days, not months — and unlike the
   proxy, it is the real quantity. This makes a **live-execution** fill model, not a paper one.
2. **Record the book at the instant of paper entry, from the level we would actually rest at** —
   i.e. persist "contracts ahead at our intended limit price", not "size at the best bid". That is
   a different capture, and it is the only version that could ever apply to paper. It is
   computable from the orderbook the scan already fetches, so the cost is storage and a careful
   definition, not extra API calls.

Option 2 is the one that serves the original goal. **Do not restart this line on the existing
column** — that question is now answered.

## Pre-registered gate for any successor

Before any queue-derived feature is used to project a paper book:

1. **Validate against `live_order_queue_ticks` first**, on paired orders, and require **r ≥ 0.5**
   *and* agreement on the front-of-queue share. That gate is implemented in
   `print_proxy_validation` and is deliberately gating rather than advisory.
2. Only then read the fill/adverse-selection tables as being about queue position.
3. A null at step 2 closes the queue line. A null at step 1 says nothing about queues at all.
