# MARKTANGLE — conditional reversion in recurring binary families

**Experiment (canonical):** `marktangle-conditional-reversion` · v1 frozen at
registration · stage **PROBE** · Experiment OS is the source of truth for its
standing, arms, gates and verdicts. Nothing in this document is a status.

**Package:** `kalshi_bot/experiment_os/marktangle.py`
**Probe:** `scripts/marktangle_probe.py` (ops channel: `{"type":"script","name":"marktangle_probe"}`)
**Workstream:** `docs/workstreams/WS-011-marktangle-conditional-reversion.md`

---

## 1. The claim, and the claim it is not

The idea arrived as two things fused together. They have to be separated before
either can be tested, because one of them is arithmetic and the other is a
falsifiable hypothesis.

**Not the claim.** "Ten YESes in a row mean a NO is due." If the resolutions are
independent then

$$P(N \mid Y^{10}) = P(N)$$

exactly, and no length of history can make that false. The 0.098% prior
probability of ten heads is a statement about the sequence *before* it happened;
once it has happened it is sunk. This is the gambler's fallacy, and a book built
on it would trade a 50/50 market at 50/50 prices and pay fees for the privilege.

**Not the sizing either.** Doubling after a loss — the Martingale — changes no
per-trade expectation. It buys a high probability of a small win with a small
probability of a ruinous one. Ten doublings from a $10 base commit

$$10(2^{10}-1) = \$10{,}230$$

to chase a $10 target. A finite bankroll meeting a long enough streak is not a
risk we would be *measuring*; it is one we would be *waiting for*. Martingale
sizing is a **pre-registered exclusion** on v1's `held_constant`, so adding it
later is a visible contract change and not a retune.

**The claim.** Some recurring binary families exhibit **negative serial
dependence in their resolutions**: the probability that the next event resolves
opposite to the current run rises with run length — by more than the quote
already prices, net of taker fees.

The distinction that the whole experiment turns on:

> A 50% marginal frequency does not imply mean reversion.

Two families can both resolve YES half the time and be completely different
inside:

| | P(rev \| k=1) | k=3 | k=5 | k=7 | marginal YES |
|---|---|---|---|---|---|
| A — memoryless | 50% | 50% | 50% | 50% | ~50% |
| B — conditionally reverting | 51% | 57% | 66% | 76% | ~50% |

A gives us nothing. B, if it survives out of sample and beats its own price, is
the experiment.

## 2. Why we would ever size up on a longer streak

We might — and the reason is the whole point:

* **not** because four attempts have lost and the fifth must recover them;
* because the *estimated edge* is larger. If the modelled reversal probability at
  k=6 is 70% and the reversal side costs 50c, the position is bigger because
  `EV = 100 p − price − fee` is bigger.

That is `mktkelly`'s rule (quarter-Kelly, capped at 4x the flat clip). Every
other arm is flat. No arm's size is a function of any preceding outcome, and a
test asserts it.

## 3. The mechanism, stated so it can be held against results

The streak probably does not *cause* anything. It is more likely a proxy for a
**hidden regime reaching maturity**: a weather system that has delivered five
warm days is a system near the end of its life, not a coin that owes us a tails.
If that is what is happening, two things follow — one encouraging, one
disciplining:

* the dependence is real, and the crude run-length signal can later be replaced
  by the regime variable itself (a genuinely predictive model);
* the dependence is **family-specific and mortal**. It lives as long as the
  regime structure does, which is why the holdout is a time-ordered tail and not
  a random shuffle.

**Counterparty:** flow anchored to the family's marginal frequency, and makers
quoting the ladder without a run-length term.

## 4. Why this is not a revival

Two nearby families are dead, and the graveyard's revival conditions are
explicit. Neither covers this:

* `scanner-ta-books` (momentum / reversion / buy_favorite) — **intraday price
  paths**. Refuted: −5c/contract over 1,000+ trades. Revival condition: "requires
  an information edge, not chart shapes."
* the nine `backfill-structural-probes` — also price paths. "Mean reversion real
  but +0.4c sub-fee ... momentum refuted (autocorr −0.03)." Revival condition:
  "use overdispersion / mean-reversion as MODEL FEATURES inside a multi-signal
  book, never as standalone trades."

MARKTANGLE's unit of observation is not a price path. It is the **resolution of
consecutive events in one recurring family** — a sequence of settlements through
time, one per event — and the entry condition is a modelled conditional
probability tested *against the quote*, which is precisely the "model feature,
never a standalone chart signal" shape the graveyard asks for. That is the
mechanically new premise, and it is the thing to hold against the results.

## 5. The arms

Five, and the count is argued rather than assumed. The question has two
independent halves and one arm separates neither.

| arm | role | rule |
|---|---|---|
| `mktrev3` | treatment | reversal side at run ≥ 3, entered only when modelled reversal probability beats the taker price by ≥ 3c net of worst-case fees; flat size |
| `mktrev5` | treatment | same at run ≥ 5 — where on the streak axis the edge lives |
| `mktkelly` | treatment | streak-agnostic; enter on edge alone, size ∝ edge, quarter-Kelly capped at 4× the flat clip |
| `mktcont` | **control** | the mirror: same universe, cadence and sizing as `mktrev3`, entered on the **continuation** side |
| `mktnaive` | benchmark | the gambler's-fallacy arm: reversal at run ≥ 3 with **no** price comparison |

* `mktcont` answers **does the streak carry direction?** If the treatment cannot
  beat its own mirror, whatever it earned came from a side-bias in the family or
  from noise — and the pre-registered gate KILLS the family on that condition,
  not merely holds it.
* `mktnaive` answers **does the edge gate do the work?** If the treatment cannot
  beat the un-gated fallacy, the price test is decoration.

Only `mktrev3` can promote. `mktrev5` and `mktkelly` are read against it and
cannot: a gate that promotes whichever of three arms looks best is a three-way
search wearing a p-value. There is no sixth arm — every additional arm splits the
same settlement cadence, and the 200-settled-trade floor is already what decides
how long this takes to say anything.

## 6. Phase A — the probe (pre-registered, frozen on v1)

`scripts/marktangle_probe.py`, read-only against the public Kalshi API. It runs
three stages in the order that can kill the idea cheapest.

**SEQUENCE.** Per family — `SERIES|market-suffix`, so each rung of a ladder is
its own recurring binary and rungs are never pooled — the base rate P(Y), the
transition matrix P(Y\|Y) / P(N\|N), and the run-length-conditioned reversal rate
with exact counts and a **Wilson 95% one-sided lower bound**. Wilson, not Wald:
at these sample sizes the approximation has zero width at a perfect record, which
would rank 10/10 above 610/1150. Reported for TRAIN and HOLDOUT separately, never
pooled. Markets closing at the same instant are dropped, not ordered by guess.

**HOLDOUT.** Each family's history splits 70/30 by close time. The threshold `k*`
is the **smallest** run length whose TRAIN reversal rate has a lower bound above
50% on ≥ 30 observations — smallest, not best-looking, because a maximum over 15
candidates is a 15-way search and its winner's bound is a bound on nothing. The
holdout only ever grades.

**PRICE.** For holdout survivors, the taker cost of the reversal side at
T−60 min (the offer we would have lifted, never a mid or a last trade), and

$$EV = 100\,p_{\text{model}} - \text{price} - \text{fee}, \qquad
\text{fee} = \lceil 7p(1-p) \rceil \text{ cents}$$

with `p_model` from TRAIN. Scoring against the holdout's own realized rate would
be grading the rule with its own answers.

### The verdict rule — frozen, and not re-read after results

| verdict | condition |
|---|---|
| **PASS** | ≥ 1 family with ≥ 100 holdout entries at run ≥ k*, holdout reversal Wilson lower bound > 50%, and mean net edge ≥ **+3.0c/contract** on ≥ 100 priced holdout entries |
| **FAIL** | every holdout survivor is priced at or through its edge |
| **HOLD** | no family reaches the 100-entry holdout floor. Thin sample is not a negative result; it is no result |

A family that misses the bar is not promoted on a narrower slice of itself.

**What a PASS authorizes: nothing automatic.** PROBE → PAPER is an operator
transition justified by this document once the verdict is recorded here and in
`docs/RESEARCH_JOURNAL.md`. The probe's evidence is an offline scan of public
settlement history, which no canonical metric provider can read — registering a
gate against a metric the engine cannot compute would put a permanent
`BLOCKED_DATA` in the portfolio and call it pre-registration. The bar is instead
frozen inside v1's immutable contract (`sample_json.probe`), which is the same
guarantee without the false verdict.

## 7. Phase B — paper, and the gates that decide it

Registered on v1 **at IDEA, before any evidence of any kind existed** — the
strongest pre-registration available here. Evidence has deliberately not been
started on either gate: no arm has a tag yet, and starting the clock now would
floor every future evidence window at a boundary predating the book.

**`paper_to_live_canary`** (PAPER → LIVE_CANARY), floors at 200 settled trades on
`mktrev3` and `mktcont`, horizon 1,500:

* `pnl_cents_per_trade(mktrev3) > 0`
* `delta.pnl_cents_per_trade(mktrev3 − mktcont) ≥ 3.0`
* `delta.pnl_cents_per_trade(mktrev3 − mktnaive) ≥ 1.0`
* **fail** if `delta.pnl_cents_per_trade(mktrev3 − mktcont) ≤ 0`

**`paper_keep`** (kill), floor 400, with early stops that carry their own lower
floors so an obviously dead book does not run to 400: fail at ≤ −3.0c/trade after
150 trades, or at a non-positive mirror delta after 250.

The fill model is **not** read on this experiment. It is calibrated for resting
maker orders in the mmsell cheap band; every arm here is a taker.

## 8. Cost model

Worst-case Kalshi taker fee, `ceil(7 · p · (1−p))` cents per contract, charged on
entry; settlement is free. At 50c that is 2c per contract — which is why the edge
bar is 3c and not 1c, and why a family whose conditional reversal beats 50% by a
point or two is not a strategy.

## 8b. Phase-A run log

**2026-08-29 · run 1 (ops `mkt-probe-1`) · exchange-wide sweep · NO VERDICT.**
6,270 settled binaries, **0 families** reaching the 40-resolution floor. This is
not a result about Kalshi. The un-restricted `status=settled` listing returns a
shallow RECENT window spread across hundreds of series x strikes, so every family
gets a handful of rows.

**2026-08-29 · run 2 (ops `mkt-diag-1`) · per-series diagnostic.** The identical
code path restricted to `KXBTCD,KXHIGHNY,KXHIGHCHI` returned 20,816 markets and
**198 families**. So the family-keying was never the problem — discovery and
history are two different queries, and the probe was making one of them.

The run also exposed a clause this document had as prose and the script did not
have at all: **"whose unconditional outcome is roughly balanced."** Of those 198
families, the overwhelming majority are KXBTCD strikes resolving 0% or 100% YES —
strikes permanently out of or in the money. They are not memoryless, they are
*constant*: no conditional structure exists to find, `P(Y|Y)` is undefined on one
side, and they bury the handful of families that could carry a signal.

Both are now instrument fixes, not contract changes — the hypothesis, the arms,
the gates and the verdict rule are untouched:

* **two-stage fetch** — sweep the exchange to enumerate series (the universe is
  still not pre-selected, only *enumerated*), then pull each series' own history
  deeply;
* **`BASE_RATE_BAND` = 25-75% yes-rate**, with constant and merely-lopsided
  families counted separately and reported. A screen nobody can see is
  indistinguishable from a bug.

**2026-08-30 · run 3 (ops `mkt-probe-3`) · re-run on the fixed instrument · STILL NO
VERDICT.** The two fixes worked and the next layer became the constraint.

What worked: the balance screen did its job — 6 families, 0 constant, 3 lopsided
screened out, 3 kept. The per-series fetch pulled real depth (9,066 markets from
one series alone).

What did not: **discovery found only 3 series.** Enumerating from pages of the
un-restricted settled listing is dominated by whichever series has the most
*closed* markets — 6,000 markets of listing surfaced three tickers. So the
universe was never enumerated; it was sampled, badly.

The diagnosed fix is to enumerate from `/events` (`status=open`) rather than from
settled-market pages. **Operator decision 2026-08-30: build it.** Shipped —
`discover_series` now reads the live board, where every listing series appears
exactly once regardless of how many markets it has closed, ordered by concurrent
open events (a series with more of them recurs more often, which is what the
hypothesis needs). Volume is deliberately not filtered at discovery: `min_vol`
belongs to the history query, and applying it here would drop a thin-but-live
series before its history was ever looked at.

**One lead, explicitly not a result.** `KXUSLTOTAL|3` (a soccer total-goals rung)
shows `P(Y|Y)` 45.5% against `P(N|N)` 60.0% — asymmetric persistence, the shape
the thesis is looking for. At n=79 it is far under every floor, no `k*` was
fitted, and it is recorded as a place to look, not as evidence.

**2026-08-30 · run 4 (ops `mkt-probe-4`) · the enumerator works; the RANKING was
wrong · STILL NO VERDICT.**

Enumeration is fixed, decisively: **2,441 series** discovered, against 3 from the
settled listing. That part of the diagnosis was right.

The ranking was not, and it was my claim that the data falsified. The fix ranked
series by **concurrent open events**, on the stated reasoning that "a series
carrying more of them recurs more often". That is false, and obviously so in
hindsight: many concurrent open events means a broad ONE-SHOT ladder — 50 states,
32 teams, every SCOTUS case — not a fast-recurring family. The top 40 by that
measure returned almost no settled history at all:

```
KXMIDTERMVOTETURN: 0 settled     KXNFLWINS:  0 settled
KXNCAAFWINS:       0 settled     KXNBAWINS:  0 settled
KXSCOTUSCASE:      0 settled     KXGDPYEAR:  0 settled
```

1,000 markets, 0 families at the floor, nothing even reaching the balance screen.

**Recurrence is not concurrency.** What the hypothesis needs is a series with many
events SETTLED THROUGH TIME. The quantity that measures it is the one the settled
listing is biased toward — which is why that listing is a poor enumerator and a
good *recurrence ranker*.

**D3 answered (operator, 2026-08-30): rank by settled frequency.** Each query is
now used for what it is actually good at:

| query | answers |
|---|---|
| `/events?status=open` | WHICH series exist, and are still tradeable |
| settled listing | HOW OFTEN each of them settles |

A series absent from the settled sample keeps its enumerated position at the back
rather than being dropped: absence means "did not appear in this sample", not
"never settles", and discarding it would quietly narrow the universe on weak
evidence. It has no history to pull, so a `--max-series` prefix never reaches it.
Conversely the listing never *adds* a series: a retired family with deep history
but nothing currently listed is not tradeable, and the enumerator is the gate.

Neither of the first two runs produced a `k*`, nor the third, nor the fourth, so
none is a PASS, a FAIL or even the pre-registered HOLD: the HOLD verdict is about a thin *holdout* at a fitted
threshold, and no threshold was ever reached. The honest label is **instrument
not yet capable**, and it is recorded here rather than being quietly retried
until it said something.

**2026-08-30 · run 6 (ops `mkt-probe-6`) · the recurrence ranking had NO EFFECT ·
STILL NO VERDICT · STOPPING.**

The output is **byte-identical to run 4's**. The ranking silently did nothing,
and the proof is in the list it produced:

```
 5. KXARTISTSTREAMSY: 288 settled markets
17. KXVOTEPRIMARY:    598 settled markets
```

Those two have by far the most settled history of the forty, and a working
settled-frequency ranking would have put them at #1 and #2. They stayed at 5 and
17 — their enumerated positions — which means `freq` was **zero for every series
in the list** and the tie-break preserved the enumerated order untouched.

**Why: the settled listing and the open-events board barely intersect.** The
listing sample is dominated by KXMVECROSSCATEGORY / KXLIGAMXSPREAD / KXUSLTOTAL
(run 3 saw only those three in 6,000 markets); essentially none of the 2,441
enumerated series appear in it at all. So a settled-frequency ranking computed
from a *sample* assigns zero to nearly everything and cannot order the set. The
per-series query finds 288 and 598 for those two families perfectly well — the
sample sweep simply never sees them.

That is a real property of the data, not a coding slip: **recurrence cannot be
ranked from a sample of the listing.** It needs a per-series count, which is the
option costed at one query per candidate and not taken.

**Stopping here, deliberately.** Five runs, five acquisition- or selection-layer
findings, and no `k*` ever fitted. A fifth patch at this layer is the pattern
this log exists to prevent, and the pre-committed guard was explicit: report
rather than patch. What MARKTANGLE has produced so far is a well-tested
instrument, four diagnosed data-access facts about Kalshi's API, and zero
evidence about the hypothesis. Whether that is worth more investment is the
operator's call, not another quiet iteration.

**2026-08-30 · run 8 (ops `mkt-probe-8`) · option B, hand-picked shortlist ·
FIRST REAL VERDICT: HOLD — and a directional finding against the thesis.**

Operator decision: test the families where the mechanism should be strongest
rather than keep fighting enumeration. Eleven series, 102,786 settled markets,
816 families at the floor. The instrument finally reached the pre-registered
verdict rule.

**The balance screen earned its place.** 702 of 816 families were constant (0% or
100% YES) and 76 more were outside the band. 38 survived. Without that screen the
38 would have been invisible.

**Crypto daily thresholds are momentum machines, not coin flips.**

```
KXBTCD|T78499.99   n=135  yes 58.5%   P(Y|Y) 94.9%   P(N|N) 92.9%
KXETHD|T2464.99    n= 83  yes 49.4%   P(Y|Y) 97.5%   P(N|N) 97.6%
KXSOLD|T105.9999   n= 84  yes 27.4%   P(Y|Y) 86.4%   P(N|N) 93.4%
```

Read that carefully, because it is the sharpest evidence the experiment has
produced. `KXETHD|T2464.99` resolves YES 49.4% of the time — a near-perfect coin
on the marginal frequency — and repeats its previous outcome **97%** of the time.
This is exactly the Series A / Series B distinction the thesis is built on, and
it lands on the wrong side: a 50/50 marginal frequency with near-total
persistence. Reversal probability is 3-15% and does not rise with run length; it
cannot, because these are level crossings on a slow-moving price, not fresh draws.

That is a real answer for this market class: **daily crypto threshold families
are the opposite of what MARKTANGLE looks for.** If anything they would favour
`mktcont`, the continuation mirror — momentum on a level crossing, which is
almost certainly priced. They should be excluded from the universe as a
mis-specified market TYPE, not screened on a statistic.

**The genuinely coin-like families are untested, not refuted.**

```
KXUSLTOTAL|3     n=88  yes 42.0%   P(Y|Y) 41.7%   P(N|N) 58.8%   holdout 27
KXUSLTOTAL|2     n=86  yes 61.6%   P(Y|Y) 65.4%   P(N|N) 45.5%   holdout 26
KXHIGHMIA|B92.5  n=42  yes 47.6%   P(Y|Y) 55.0%   P(N|N) 61.9%   holdout 13
```

Sports totals and weather buckets sit near 50/50 with mild asymmetric
persistence — the shape worth testing. Their holdouts are 13-27 against a
pre-registered floor of 100, so nothing was fitted and nothing was graded.

**VERDICT: HOLD**, by the frozen rule, and for the first time it is the actual
pre-registered HOLD rather than "instrument not capable": *no family reaches the
100-entry holdout floor — thin sample is not a negative result, it is no result.*

The binding constraint has moved from our tooling to Kalshi's history depth.
`KXHIGHNY` and its siblings expose 408 settled markets each — about 13 months
spread across 11 ladder rungs, ~37 per rung. No amount of engineering creates
more past.

## 8c. CLOSED FOR NOW — operator decision, 2026-08-30

MARKTANGLE is **PAUSED at PROBE**, not retired. Paused rather than retired because
the decision was explicitly "for now": PAUSED records `paused_from`, so the
experiment can only resume to PROBE or be deliberately retired later, and that
provenance is worth more than the one command it costs to keep.

**Nothing about the evidence changes.** The recorded verdict stays the
pre-registered HOLD. The contract, the five arms, both gates and the run logs
stand exactly as written. Pausing is a statement about our attention, not about
the data.

**What we know, and it is not nothing:**

* Daily crypto threshold families are refuted **as a market type** — near-total
  persistence (up to 97.5%) on a coin-flip marginal frequency. Whatever they are,
  they are not fresh draws, and MARKTANGLE should never look at them again.
* Roughly 86% of "recurring binary families" (702 of 816) are constant — a
  permanently in- or out-of-the-money ladder rung. The real candidate pool on
  Kalshi is far smaller than the raw family count suggests.
* The families with the right shape — sports totals, weather buckets — sit at
  n=42-88 with holdouts of 13-27 against a floor of 100. **Untested, not
  refuted.** That distinction is the whole reason this is paused rather than
  killed.

**What resuming would take.** Not a rebuild — forward collection. Kalshi exposes
about 408 settled markets per weather series (~13 months across 11 ladder rungs,
~37 per rung), and no amount of engineering creates more past. A family needs
roughly 100 more holdout observations to be gradeable, which is months of waiting
at a daily cadence. Resume by collecting, then re-running
`scripts/marktangle_probe.py` unchanged.

The lifecycle move itself is `scripts/marktangle_pause.py`, run by an operator on
a writable connection — the ops channel is read-only by design and the
experiment-command vocabulary has no "move a lifecycle state" verb, deliberately.

## 9. What would make us abandon this

Any one of the three, stated before the data:

1. conditional reversal does not rise with run length out of sample;
2. it rises but is already inside the quote after fees;
3. it beats the quote but not the continuation mirror.

The third is the one worth watching for, because it is the one that would look
like success in a P&L column.
