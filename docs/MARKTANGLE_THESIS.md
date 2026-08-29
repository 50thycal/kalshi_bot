# MARKTANGLE — conditional reversion in recurring binary families

**Experiment (canonical):** `marktangle-conditional-reversion` · v1 frozen at
registration · stage **PROBE** · Experiment OS is the source of truth for its
standing, arms, gates and verdicts. Nothing in this document is a status.

**Package:** `kalshi_bot/experiment_os/marktangle.py`
**Probe:** `scripts/marktangle_probe.py` (ops channel: `{"type":"script","name":"marktangle_probe"}`)
**Workstream:** `docs/workstreams/WS-010-marktangle-conditional-reversion.md`

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

## 9. What would make us abandon this

Any one of the three, stated before the data:

1. conditional reversal does not rise with run length out of sample;
2. it rises but is already inside the quote after fees;
3. it beats the quote but not the continuation mirror.

The third is the one worth watching for, because it is the one that would look
like success in a P&L column.
