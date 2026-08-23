# Research Lab — theta remediation

Follow-on to `RESEARCH_THETA_TAIL_MODEL_DIAGNOSIS.md`, which found two composing mechanisms: a
tail-shape error in the model, and a threshold-selection bias on top of it. This is the repair
programme, and it is the **current** account of it — §6 lists what earlier revisions of this
document claimed and why those claims no longer stand.

**Paper only. No theta parameter changed, no book re-armed, no live money, no lifecycle state
touched.**

**Reproduce** (ops `script` requests, `docs/OPS_RUNBOOK.md`):

| run | request |
|---|---|
| label audit | `{"type":"script","name":"theta_settlement_labels","args":["--since","2026-07-11","--spot-source","coinbase"]}` |
| the refit | `{"type":"script","name":"theta_tail_refit","args":["--since","2026-07-11","--train-end","2026-08-11","--fit-days","5,30,90","--tail-qs","0.90,0.95,0.99","--spot-source","coinbase","--vol-mult","2.0"]}` |
| shadow cost | `PYTHONPATH=. python3 scripts/theta_shadow_bench.py` (local; no database, no network) |

---

## 0. Status

| stage | status |
|---|---|
| 1. replace the degenerate 0/1 output | **done and verified** — 85.3% exactly-0 → **0.0%** |
| 2. refit the tail shape, validate out of sample | **run, powered, label-gated — and it does not beat the incumbent** (§3.6) |
| 3. re-measure calibration + residual selection bias | **done** — bias **established for the spliced model** by a direct contrast; suggestive only for the incumbent (§3.7) |
| 4. selection-rule A/B that does not rank on model error | **redesigned; floors re-derived by replaying BOTH rules over one candidate stream** (§4.2.3). Cadence is now measured, and it says the arm is **not ready to register**: the control rule fires 2.64 times/day, so the binding arm needs 288 days at best and ~3 years if the control regresses to calibration. Unregistered. |
| 5. telemetry for momentum/regime | **implemented, benchmarked and tested** (§5) |

**The headline, before the detail.** With a 30-day-or-wider fit window, non-overlapping blocks, a
coherent marginal EVT construction, declustered per-tail power and **Kalshi's own settled results
as the outcome**, every quote is powered and every label is real, so the replacement model could
finally be judged. Judged **paired**, on proper scores, with the ladder
event as the independent unit and against the incumbent theta4 actually runs, it is **not better
and not worse**: all four comparisons contain zero and the two proper scores disagree in sign.

That is a different answer from the one this document gave a day ago, and the reason is not new
data. It is that the previous comparison read two separately-computed aggregate-R intervals as
though that were a test of a difference, and computed both of them as if 66 markets on one ladder
were 66 independent observations. Correcting those two things dissolves the finding. Nothing has
been established about which model is better, and this document no longer claims otherwise.

**What has not changed:** neither model is acceptable for a short-tail seller, the cause of
theta4's failure is still unidentified, and every theta book stays stood down. The paired verdict
survived the label correction — it held on the derived proxy and holds on real settlement — which
is the strongest support it has.

**What did change under real labels:** the frozen window moved from 30 days to 90 (a fifth-decimal
margin, §3.4), and the deepest bucket's miss grew from ~4× to **8×** because the proxy's
near-strike exclusion had been removing exactly the markets that hit.

**And what the direct test changed:** residual selection bias is **established for the spliced
model** — `log(R_sel/R_rej) = +1.486`, 99% CI **[+0.660, +1.982]**, a 4.42× miss — and
**suggestive only for the incumbent**, whose interval spans zero on 25 markets in 18 events. An
earlier revision read two separately-computed intervals and called both established because they
did not overlap; that is not a test of the contrast (§3.7).

---

## 1. What is being measured, and against what

Three questions had to be settled before any calibration number meant anything. All three had
been got wrong in earlier revisions, and each was wrong in the direction that flatters a result.

### 1.1 The independent unit is the EVENT, not the market

A crypto ladder publishes one market per strike and settles **all of them against one spot
print**. If spot gapped up in the final minutes, every `greater` strike hits together and every
`less` strike misses together. Treating those markets as independent Poisson observations counts
one move as dozens.

`load_quotes` retrieved `event_ticker` and discarded it as `_ev`. It is now carried on every
scored row, and every interval in this document is an **event-cluster bootstrap**
(`scripts/cluster_stats.py`): resample whole ladders with replacement, recompute the statistic,
take percentile bounds. The point estimates are unchanged — R was never wrong, the confidence in
it was.

The cost of the correction is measured rather than assumed. `design_effect` runs the same
statistic through a clustered bootstrap and an iid one and reports the variance ratio, so the
inflation is a number in the output, not an assertion here.

A per-event position cap bounds **exposure**. It does nothing to the statistics.

### 1.2 The outcome label is Kalshi's own settlement print, not a derivation

Every calibration number rests on an outcome. For most of this programme that outcome was
**derived**: the last ladder snapshot's spot before close, compared to the strike. Kalshi settles
off its own index at the close, up to three minutes later, and whatever spot did in those minutes
is unobserved. The derivation is a proxy, and it was treated as one — audited, gated, and
excluded near the strike where the unobserved move could carry a market across.

**It does not clear its own bar, and the replacement is not a looser bar.**

Measured against Kalshi's real results on the **whole population** rather than the 0.33% a book
happened to trade (run `refit-14`): agreement **99.90%** over 67,022 markets, but **54 of 1,000
events** carry at least one disagreement, so the exact event-clustered 99% lower bound is
**92.70%** — below the 97% the design fixed in advance. The proxy is good and not good *enough*.

An earlier revision reported this as `[1.0000, 1.0000]` from a percentile bootstrap and called it
a PASS. **That is withdrawn.** A bootstrap on an all-agreeing sample can never produce a
disagreement — every resample is drawn from observations that all agreed — so `[1, 1]` is a
boundary artefact of the method, not evidence of certainty. The bound is now an exact
Clopper–Pearson one with the **event** as the unit (a cluster fails if any of its markets
disagrees), which stays valid at zero observed failures. Sixty-three clean events buy 92.9%, not
100%; clearing 97% at zero failures takes 152 of them.

**So the derivation is retired.** Kalshi's market-data endpoints are public, serve a `result`
field on settled markets, and cover this universe completely:

| | |
|---|---|
| events read | **1,000 / 1,000** |
| markets with a real settled result | **67,022 / 67,022 (100.00%)** |
| derived label's agreement with them | 99.90% |
| events carrying a disagreement | 54 |

The near-strike exclusion retires with the proxy. It existed only to drop markets whose true side
the derivation could not determine, and a settlement print determines every one of them — so the
scored population is the full covered universe rather than a retained subset.

What the database holds is unchanged and remains inadequate on its own: `markets` and
`market_snapshots` carry **no** crypto ladder rows (checked per run, not assumed), and
`paper_trades.resolved_value` covers 0.33% of the universe and only where a book traded, which is
the selected subset. That is why the audit could never have been more than an audit.

**Two bugs in my own audit, found by running it and disclosed rather than repaired in silence.**
The residual-move scale first ran on the ~5-minute ladder reconstruction and reported a 4-minute
RMS ETH move of **$0.20**; the fix was a denser series, not a different threshold, and a scale
cell built on fewer than 500 observed pairs is now refused. And the first version measured
retained coverage on the 220-market audit overlap instead of the population — a different and
easier question than the bar was written to ask.

`scripts/theta_settlement_labels.py` still runs the full audit, and
`theta_tail_refit --labels derived` still reproduces the old path, because the record has to be
able to reproduce what earlier runs scored.

### 1.3 Two models are compared PAIRED, on proper scores — never on aggregate R

Both models priced the same markets, so their errors share every source of market-to-market
variation: which strike, which hour, how far spot happened to travel. Differencing per market
removes all of it.

Reading two separately-computed intervals and concluding a difference because one excludes some
reference value and the other does not **is not a test**. Overlapping intervals routinely hide a
real paired difference, and separated ones can manufacture one.
`tests/test_cluster_stats.py::TestPairedComparison` builds both cases explicitly.

And aggregate R is the wrong instrument for the choice regardless. R = observed/expected rewards
a model for predicting **exactly zero**: a zero costs nothing in the denominator whatever
happens. A model that declares most of its universe impossible can post an excellent aggregate R
while being useless on the markets it refuses to price — which is precisely the incumbent's
shape. The comparison is therefore mean Bernoulli log loss and Brier score, per prediction, on a
common population fixed by the **market's own price**, reported market-weighted and
event-weighted, with event-clustered intervals on the **paired difference**.

---

## 2. The replacement model — the frozen specification

`kalshi_bot/theta/tailmodel.py`. **Additive: no book prices off it.**

**Construction.** Empirical body below a high quantile; a fitted Generalized Pareto spliced onto
each tail above it. Pickands–Balkema–de Haan says excesses over a high threshold converge to a
GPD whatever the parent, so this is the standard way to extrapolate past the data instead of
asserting the data's edge is the world's edge.

| element | choice | why |
|---|---|---|
| horizon | **snapped to a fixed grid** (10, 15, 20, 25, 30, 35) | §2.6 — the runtime and the validation must fit the same horizon |
| refit anchor | **top of the hour**, strictly backward-looking | §2.6 |
| sample | **non-overlapping h-minute blocks** | §2.1 — the correction that mattered most |
| declustering | runs, separation 2 blocks — **power only** | §2.3 |
| body | empirical, unchanged | no reason to model what can be counted |
| tail | GPD, both directions, fitted **separately** | `greater` and `less` strikes use opposite tails |
| estimator | probability-weighted moments | closed form, no optimiser, no scipy, better than MLE at small counts |
| threshold | `theta_spliced_tail_q`, swept and frozen on TRAIN | bias/variance: higher = less biased, more variable |
| fit window | `theta_spliced_fit_days` = **90**, frozen by `refit-13`, **paper only** | §2.2, §3.4 |
| power | **≥20 declustered exceedances on the ACTIVE tail** | §2.3 |
| completeness | **≥90% block coverage of the requested window** | §2.5 |
| floor | `1 / (2·blocks)` | degenerate cases only |
| bounded fits | extrapolate with `max(ξ, 0)` | a fitted ξ<0 asserts a hard maximum move |

### 2.1 Non-overlapping blocks, not an `n_eff` disclaimer

`SpotModel.returns` emits an h-minute return from **every minute**, so consecutive samples share
h−1 of their h minutes. At h=35 the overlap factor is exactly **35×** (7,165 overlapping returns
against 205 blocks over the same window). One shock therefore enters an overlapping fit as ~35
neighbouring extremes, and the exceedance count, the scale **and** the shape are all estimated as
though a single move were dozens of independent ones.

An earlier revision fitted overlapping returns and reported an `n_eff = n/h` disclaimer beside
the result. That was not a correction — **a denominator in the metadata does not correct a fitted
shape.** The fit consumes non-overlapping blocks in time order, and
`tests/test_theta_tailmodel.py::TestDeclustering` pins that a single ten-block storm reads as
**one** exceedance, not ten.

### 2.2 The fit window is separate from BOTH retention and the incumbent's window

An earlier revision raised retention to 90 days and then still fitted on 5, because the shadow
called `model.returns(...)` on a `SpotModel` built with `theta_trail_days=5`. **Every claim it
made about what 90-day retention bought was therefore false.** Three now-distinct settings:

| setting | value | who reads it |
|---|---|---|
| `theta_trail_days` | 5 | the **incumbent**, unchanged, still prices exactly as before |
| `theta_spot_retention_days` | **100** | the pruner only — must EXCEED the fit window |
| `theta_spliced_fit_days` | **90** | the **paper** replacement model only; the frozen value |

Retention was 90 while the fit window was 90, which leaves the pruner deleting the oldest closes
the fit needs. It is 100 now. Retention and the fit window are different questions and the config
says so where each is defined.

The fix was a second **load** (`_refresh_shadow_spot`), not a second argument: a 5-day object
cannot yield a 90-day fit however it is asked. Widening the fit window cannot touch a live
decision — `_refresh_spot` still loads only `trail_days` of closes for the incumbent, and no book
prices off the replacement.

### 2.3 Power is per tail, depends on `tail_q`, and does not use the declustered sample

Two separate corrections live here.

**The bar.** A fixed block count is wrong twice over: 400 blocks give ~40 exceedances at
`tail_q=0.90` and **~4** at `tail_q=0.99`, and a model whose upper tail is well evidenced and
lower tail is not still hands out an unsupported number on every `less` strike. The bar is
**≥20 declustered exceedances on the tail the strike actually prices off**.

**Coherence.** An earlier revision fitted the GPD severity to declustered **cluster maxima**
while leaving `zeta`, the exceedance frequency, a **marginal** per-block rate. Multiplying those
two produces the survival function of nothing: the frequency describes one distribution and the
severity another. Both halves are marginal now. Declustering measures how much *independent*
evidence a tail carries — it gates power — and no longer touches the estimate.
`tests/test_theta_tailmodel.py::TestMarginalCoherence` pins it.

### 2.4 Stated limitations, not buried

- PWM is consistent only for **ξ < 0.5**; against a heavier tail it *saturates* (true ξ=2 returns
  ~0.95). The bias runs **downward**, the dangerous direction for a seller, so an ξ near 0.5 must
  read as "at least this heavy", never as a point estimate.
- No SOL candle feed exists, so a SOL strike is priced off BTC returns.
- Runs-declustering with a fixed separation is a convention, not a fact about the market; the
  separation travels with every fit so a reader can see what was assumed.

### 2.5 Window completeness is not window span

`fit_days` records the distance from the oldest stored minute to the newest. **A span cannot
express completeness**: a window that reaches ninety days back and is missing a third of its
minutes has the same span as a full one. The holes are not random either — they are collector
restarts and deploys, which cluster around exactly the market conditions a tail model exists to
price, so a gappy fit is not merely smaller, it is biased in an unknown direction.

`block_sample` therefore returns, beside the sample: how many block slots the requested window
contains, how many were usable, and the longest contiguous hole. Coverage below **90%** means the
row carries metadata and **no probability** — `emittable_for(strike_type)` is `powered_for` AND
coverage, and it is the same predicate on both sides of §5.3.

### 2.6 The horizon grid and the refit anchor are part of the spec, not conveniences

Two parameters that used to differ silently between the validation and the runtime:

- **Horizon.** The runtime fitted at whatever integer minutes-to-close a market happened to show;
  the harness fitted on a 10–35 grid. So the probability being validated was never the
  probability that would trade. Both now call `tailmodel.h_bucket`.
- **Refit cadence.** The runtime refitted every cycle; the harness refitted hourly and called it
  an approximation. A 90-day window cannot be moved by one 5-minute cycle, so refitting every
  cycle bought nothing and cost an order of magnitude. Both now anchor to
  `tailmodel.refit_anchor` — the top of the hour at or before the decision, strictly
  backward-looking, so anchoring can only make a fit staler, never let it see a minute after the
  decision it prices.

The block construction itself was a *copy* in the harness, kept in step by a test. A copy kept in
step by a test is still a copy; there is now one definition and both call it.

---

## 3. The refit of record

One run, one command, one commit. Nothing below is combined with an earlier run's selection
rationale — see §6 for what those runs said and why it no longer stands.

```
{"type":"script","id":"refit-14","name":"theta_tail_refit","args":[
  "--since","2026-07-11","--train-end","2026-08-11","--fit-days","90","--tail-qs","0.90",
  "--spot-source","coinbase","--vol-mult","2.0","--labels","kalshi"]}
```

**`refit-14` is the run of record** for every number below except the configuration sweep. It
ran the frozen configuration only, on a slightly larger population (1,000 events / 67,022
markets, eight more ladders having settled), to add the direct selection contrast in §3.7 without
paying for a nine-configuration sweep a second time. Every conclusion it shares with `refit-13`
is unchanged. **`refit-13` remains the sweep of record** (§3.4), because `refit-14` did not run
one.

`--vol-mult 2.0` is deliberate: **theta4 does not run the base model.** Scoring against
`mult=1.0` would compare the replacement to a model that never traded. `--labels kalshi` is the
correction in §1.2: real settled results, not the derivation.

The cluster bootstrap is seeded (`seed=20260822`) and reproduces its interval endpoints exactly —
checked rather than asserted: `refit-10` and `refit-11`, thirteen minutes apart on identical
inputs, differ in exactly one line, the count of Coinbase minutes fetched.

### 3.1 Outcome labels — Kalshi's own, at 100% coverage

1,000 of 1,000 events read; **67,022 of 67,022 markets** carry a real settled result. **PASS.**
The derivation this replaces agrees 99.90% but puts its exact event-clustered 99% lower bound at
**92.70%** — below the 97% bar it had to clear. Why that matters, and the two bugs found in the
audit of it, is §1.2.

### 3.2 Evidence structure — 67,022 markets are worth about 8,000–17,000 observations

| quantity | value |
|---|---|
| scored markets | 67,022 |
| distinct events | 1,000 |
| markets per event (mean / p50 / p90 / max) | 67.0 / 52 / 98 / 114 |
| Kish effective events | 895.5 |
| largest single event's share | 0.17% |

| statistic | design effect | effective n |
|---|---|---|
| hit indicator | 3.9 | 17,173 |
| spliced log loss | **8.0** | **8,372** |
| incumbent log loss | 6.0 | 11,108 |

An interval computed as though these rows were independent is **2–3× too narrow** — that is the
size of the error the previous revisions' Poisson intervals carried. The design effect is itself
noisy between runs (5.0 on `refit-13`, 8.0 here, same construction) because it is a variance
ratio estimated from a bootstrap; the order of magnitude is the finding, not the digit.

### 3.3 Degeneracy — fixed, and it is the one unambiguous success

| model | n | exactly 0 | exactly 1 | in (0,1) |
|---|---|---|---|---|
| incumbent (empirical, `mult=2.0`) | 67,022 | 56,924 (**84.9%**) | 0 | 10,098 (15.1%) |
| spliced EVT | 67,022 | 0 (**0.0%**) | 0 | 67,022 (100%) |

`SpotModel.prob_from_returns` is a raw empirical frequency, so it has no mass beyond its own
sample maximum. `vol_mult` rescales the *threshold* (`x / k`), which pulls some strikes back
inside the support but cannot escape a hard edge — pinned in
`test_vol_mult_cannot_escape_the_truncation`. Doubling the volatility left 84.9% of this universe
priced at exactly zero.

### 3.4 The window mattered from 5 to 30, and not beyond

| fit window | tail_q | powered share of TRAIN | log loss | Brier |
|---|---|---|---|---|
| 5 d | 0.90 | **3.0%** | — | — |
| 5 d | 0.95 / 0.99 | 0.1% / 0% | — | — |
| 30 d | 0.90 | 100% | 0.006674 | **0.00156** |
| 30 d | 0.95 | 100% | 0.00669 | 0.00156 |
| 30 d | 0.99 | 0.2% | — | — |
| **90 d** | **0.90** | 100% | **0.006670** | 0.00157 |
| 90 d | 0.95 | 100% | 0.00667 | 0.00157 |
| 90 d | 0.99 | 100% | 0.00670 | 0.00157 |

Selection is on TRAIN only, by mean Bernoulli log loss on the common population (`mid ≤ 20c`),
with configurations below 90% powered coverage refused.

**FROZEN on TRAIN: `fit_days=90`, `tail_q=0.90`** — and the margin is honest about itself. 30 and
90 differ in the **fifth decimal** of the log loss, and Brier marginally prefers 30. What the
sweep establishes is not that 90 beats 30; it is that at theta's own five-day window a
peaks-over-threshold fit powers **3%** of quotes and at thirty days it powers all of them. A
future freeze could reasonably pick 30 on cost — it halves the rows the shadow loads. It has not,
so the runtime runs 90 (§5.3).

### 3.5 Historical validation — 16,091 quotes, 258 events

Aggregate R, spliced **1.27 [0.82, 2.00]**; incumbent **0.76 [0.49, 1.20]**. Read as two separate
descriptions, not a comparison. Deep tail, spliced:

| modeled P | n | events | expected | observed | R | 99% CI |
|---|---|---|---|---|---|---|
| 0.000–0.002 | 14,292 | 256 | 1.72 | 14 | 8.13 | [0.00, 25.69] |
| 0.002–0.005 | 449 | 165 | 1.48 | 6 | 4.05 | [0.00, 12.69] |
| 0.005–0.010 | 287 | 162 | 2.09 | 5 | 2.39 | [0.00, 6.06] |
| 0.010–0.020 | 241 | 155 | 3.49 | 6 | 1.72 | [0.00, 4.48] |
| 0.020–0.050 | 273 | 156 | 9.00 | 17 | 1.89 | [0.76, 3.31] |

The point estimate in the deepest bucket is **8.1×** on real labels — worse than the derivation
implied, because the derivation's near-strike exclusion removed exactly the near-money markets
that hit. Its interval still contains 1. **The miss is large and it is not established.**

### 3.6 The paired comparison — what actually decides between the models

Common population: powered quotes with `mid ≤ 20c`, fixed by the market's own price, identical
for both models. **n = 15,884 markets across 258 events**.

| weighting | model | mean log loss | mean Brier |
|---|---|---|---|
| market | incumbent | 0.02119 | 0.00475 |
| market | spliced | 0.02240 | 0.00449 |
| event | incumbent | 0.03162 | 0.00750 |
| event | spliced | 0.03560 | 0.00742 |

Paired difference, spliced − incumbent (negative favours the spliced model), event-clustered 99%:

| weighting | statistic | difference | 99% CI | favours |
|---|---|---|---|---|
| market | log loss | +0.00121 | [−0.00372, +0.01183] | neither |
| market | Brier | −0.00025 | [−0.00057, +0.00016] | neither |
| event | log loss | +0.00397 | [−0.00576, +0.02378] | neither |
| event | Brier | −0.00008 | [−0.00081, +0.00093] | neither |

> **The models fail differently; superiority is not established.**

The two proper scores disagree even in sign — log loss slightly prefers the incumbent, Brier
slightly prefers the spliced model — and every interval contains zero on both weightings. **This
conclusion has now survived three separate corrections**, which is the strongest thing that can
be said for it: it held under the derived labels (`refit-12`), under Kalshi's real settled
results (`refit-13`), and on the larger population here.

Neither failure mode is acceptable for a short-tail seller. The incumbent declares 85% of the
universe impossible and is roughly calibrated on the rest; the spliced model answers everywhere
and misses its deepest bucket by 8× at the point estimate. **Choosing on aggregate R would pick
the one that refuses to answer**, since a zero costs nothing in a denominator. That is why the
frozen rule is a per-prediction proper score.

### 3.7 Selection bias after the repair — tested DIRECTLY

The estimand is **`log(R_selected / R_rejected)`**, and it is now tested as such. An earlier
revision computed a 99% interval for each group separately and called the effect established
because they did not overlap. **That is not a test of the contrast.** The two groups come from
the same events, their errors covary, and marginal intervals discard exactly that covariance.
Non-overlap is **not necessary** for a ratio to exclude 1, and it is only reliably *sufficient*
when the two estimates are independent — which these are not, since the split partitions the same
ladders. Whether the dependence makes the marginal comparison conservative or anti-conservative
depends on a covariance the marginal intervals never computed, so the honest position is that
disjointness answers a different question. Whole events are resampled from the **combined** population and both groups recomputed
inside each replicate.

Degeneracy handling, predeclared: a replicate with **zero expected** in either group makes the
ratio undefined, so it is invalid, dropped and counted; **zero observed** is handled by a
Haldane–Anscombe correction of 0.5 applied **uniformly** — to every replicate and to the point
estimate, never only where a zero appears, since correcting selectively would delete exactly the
tail of the sampling distribution the interval exists to describe.

| model | group | n | events | expected | observed | R |
|---|---|---|---|---|---|---|
| incumbent | SELECTED | 25 | 18 | 1.64 | 4 | 2.43 |
| incumbent | REJECTED | 66,997 | 1,000 | 568.27 | 343 | 0.60 |
| spliced | SELECTED | 135 | 68 | 6.13 | 25 | 4.08 |
| spliced | REJECTED | 66,887 | 1,000 | 342.33 | 322 | 0.94 |

| model | log(R_sel / R_rej) | uncorrected | 99% CI | valid replicates | verdict |
|---|---|---|---|---|---|
| incumbent | +1.510 | +1.394 | **[−0.764, +2.534]** | 2,000/2,000 | **suggestive only** |
| spliced | +1.486 | +1.467 | **[+0.660, +1.982]** | 2,000/2,000 | **established** |

> **Under the spliced model, residual selection bias IS established**: the selected set misses by
> **4.42×** its complement and the direct interval excludes zero. Repairing calibration did not
> remove it, which is what stage 4 exists to act on.
>
> **Under the incumbent it is suggestive only** — 25 markets across 18 events, and the interval
> spans zero. Nothing about the incumbent's selection is established here.

Event coverage behind those intervals: 68 selected events against 1,000 rejected for the spliced
model, 18 against 1,000 for the incumbent. The Haldane correction moves the spliced estimate by
0.02 and the incumbent's by 0.12 — the difference being that the incumbent's selected set is
small enough for a continuity correction to matter, which is itself a statement about how little
evidence it carries.

The two SELECTED sets are **different populations of different sizes** — 135 markets against 25.
An earlier draft of this paragraph explained that backwards, saying a fattened tail shrinks
`excess` and therefore selects less; that would predict the spliced model selecting *fewer*, and
it selects more. What §3.1 actually measures is that the incumbent's output is **bimodal** —
exactly zero on 84.9% of markets and comparatively large on the 15.1% where it is not — so it
rejects a market on its own large estimate, while the spliced model's smooth small probabilities
leave `excess` above the 6¢ threshold on quotes the incumbent declines. The direction of the
mechanism is inferred from that degeneracy table, not separately measured; what is *not* inferred
is the consequence, which is the only thing the analysis rests on: **a ratio between the two
SELECTED sets would not be a comparison, and none is drawn.** Each is compared only to its own
complement, which is what the contrast does.

### 3.8 Fit health

| quantity | p10 | p50 | p90 |
|---|---|---|---|
| non-overlapping blocks | 3,687 | 4,302 | 4,318 |
| expected block slots | 3,702 | 4,320 | 4,320 |
| **block coverage** | 0.996 | 0.996 | 1.000 |
| longest window gap (min) | 0.0 | 420.0 | 420.0 |
| upper-tail declustered exceedances | 256 | 298 | 303 |
| lower-tail declustered exceedances | 252 | 299 | 305 |
| upper ξ | 0.138 | 0.164 | 0.219 |
| lower ξ | 0.166 | 0.205 | 0.227 |

All 67,022 quotes have a powered active tail; **0** have a powered tail on an incomplete window.
A 90-day window carries ~300 declustered exceedances a side, fifteen times the bar. The 420-minute
median gap is a real hole in Coinbase's series and coverage still reads 0.996, which is the point
of measuring completeness separately from span. **0.0%** of active tails are bounded.

---

## 4. Stage 4 — the selection-rule A/B (design; not registered, not running)

### 4.1 Why the earlier recommendation was withdrawn

An earlier revision recommended **split-sample residual ranking**: fit on one half of the trailing
window, price on the other. That was wrong, and the objection is decisive — it still ranks
candidates by `mid − P_model`. Disjoint fitting samples do not give independent errors when both
halves share the same market quote, the same regime, the same model family and the same
structural miss. The dominant term in the residual is not sampling noise in the fit; it is the
tail-shape error, and that error is common to both halves by construction. Splitting the sample
attacks the smallest component of the problem.

### 4.2 The replacement: rank on price, veto on model

**Selection must not be a function of model disagreement at all.**

| element | specification |
|---|---|
| **Candidate eligibility** | crypto ladder markets, `minutes_to_close` ∈ [10, 35], yes mid ∈ **[3, 20]¢**, `volume ≥ 100`, two-sided book. Identical in both arms — eligibility is not the treatment. |
| **Control selection score** | `excess = mid − 100·P_model`, descending. Today's rule, unchanged. |
| **Treatment selection score** | `mid` **ascending** — the cheapest eligible tail first. Exogenous: the market's own price, which the model does not produce. |
| **Treatment veto** | take only if `P_model ≤ 0.10`. The model may *refuse* a candidate; it may never *promote* one. A veto removes candidates rather than ordering them, so it cannot generate winner's curse. |
| **Tie handling** | equal `mid` broken by the deterministic ticker hash already used by `abarm` (`docs/MMSELL_OFFSET_AB.md`) — reproducible and identical across arms. |
| **Per-event cap** | `theta_max_per_event = 3`, both arms. Note this caps *exposure*; it is not what makes the evidence independent. |
| **Independent unit** | the **event**, not the settled market — §4.2.3. |

### 4.2.1 Primary estimand — treatment versus control, not an arm in isolation

An earlier revision named "treatment-arm R ≤ 1.5" as the primary metric. **That is not an A/B
estimand at all** — it describes one arm and never compares them. Corrected:

> **Primary:** `log(R_T / R_C)`, with a two-sided 99% **event-clustered bootstrap** interval over
> both arms. Promotion requires the **upper** bound to be **< 0**, i.e. the treatment's tail miss
> is smaller than the control's by more than sampling error.

`log` of the ratio rather than the raw ratio because the sampling distribution of a ratio of
counts is badly skewed and its normal interval misbehaves near zero; on the log scale the two
directions are symmetric, which is what a comparison needs. The interval is the same machinery as
§1.1 — `scripts/cluster_stats.py`, seeded, resampling whole ladders.

### 4.2.2 Absolute safety clause — the bound direction, corrected

An earlier revision wrote "R ≤ 1.5 with the 99% lower bound below it". **That is backwards**: a
lower bound below 1.5 is satisfied by a model that misses by 10×. Establishing that a quantity is
SMALL requires bounding it from ABOVE.

> **Safety:** the one-sided 99% **UPPER** confidence bound on `R_T` must be **≤ 1.5**.

This is a `fail_any`-style clause, not a promotion clause: it can stop the arm on its own, and
satisfying it does not by itself promote anything. Both conditions must hold to promote.

### 4.2.3 Floors, recomputed by REPLAYING BOTH RULES over one candidate stream

Two revisions of this section were wrong in the same way, and the second was wrong after the
first was fixed. The first sized the arm from an **iid** count of settled markets. The second
fixed the unit but kept sizing both arms from the **spliced model's historical selected set** —
148 markets, 79 events, λ ≈ 0.0334 — and that set is what an **excess-ranked** rule picked. The
treatment ranks on `mid` ascending behind a model veto. It draws from a different pool, at a
different rate, with a different loss rate. **Floors derived from one rule cannot size an
experiment that runs another**, and the 2,725/4,100 figures are withdrawn rather than adjusted.

What replaces them is a replay: both rules **as specified in §4.2**, over **one common eligible
candidate stream**, on Kalshi's own settled results. `scripts/theta_ab_replay.py`, run `ab-3`.

**The common stream.** Eligibility is identical in both arms and is not the treatment: mid ∈
[3, 20]¢, volume ≥ 100, minutes-to-close ∈ [10, 35], two-sided book.

| quantity | value |
|---|---|
| eligible candidates | **1,187** |
| distinct events | **532** |
| markets per event | 2.23 (max 13, Kish 296.6) |
| span | 2026-07-11 → 2026-08-23, 44 days, all with data |
| candidate cadence | **27.0 eligible candidates/day** |

Both arms are scored under the **same** probability model — the frozen spliced fit. The A/B
isolates the selection *rule*; letting the arms differ in their model too would confound the two
changes and answer neither.

**The two arms, replayed.**

| arm | n | events | mkt/ev | expected | observed | R | exp/market | exp/event | candidates/day | deff |
|---|---|---|---|---|---|---|---|---|---|---|
| **control** (excess ≥ 6¢, ranked by excess) | 116 | 68 | 1.71 | 5.85 | 23 | **3.93** | 0.0504 | 0.0860 | **2.64** | 1.65 |
| **treatment** (veto P ≤ 0.10, ranked by mid ↑) | 510 | 288 | 1.77 | 23.26 | 39 | **1.68** | 0.0456 | 0.0808 | **11.59** | 1.93 |

**Overlap: 32 markets are taken by both arms** — 5.4% of the union, 27.6% of the control, 6.3% of
the treatment. Overlap is not contamination; a market both rules pick is a market the treatment
does not change. It is, though, why the arms are **not independent samples**, and why the contrast
has to be bootstrapped over both arms together rather than as two intervals
(`cluster_stats.arm_contrast_ci`, which resamples the union of events and lets a market
contribute to both arms).

For the record, the same control rule under the model theta4 runs **today** (incumbent,
`mult=2.0`) selects only 23 markets across 18 events at 0.52/day, R = 2.51. The substitution to
the spliced model is visible rather than hidden.

**The replay's own contrast — descriptive, and NOT a result.** This is the sizing sample. The
rules were chosen after seeing this window, so its effect estimate is contaminated by exactly the
selection the A/B exists to remove, and nothing here promotes, rejects or registers anything. It
is reported because a sizing exercise that hides its own effect estimate invites someone to
rediscover it later and call it news.

> `log(R_treatment / R_control)` = **−0.861** (uncorrected −0.853), 99% CI **[−1.442, −0.170]**,
> 2,000/2,000 valid replicates over 288 events in the union.

The interval excludes zero, and the point estimate is past the −0.693 a halving would need. On a
sample the rules were chosen against, **that is a reason to run the experiment, not a substitute
for running it.**

**The requirement, derived from that replay.** To resolve `log(R_T / R_C)` at a halving
(`|log 0.5| = 0.693`) with 80% power at a two-sided 99% interval, required SE = 0.2028, so
`1/obs_T + 1/obs_C = 0.0411`. Each arm carries **its own** expected-loss rate — the error the
previous revision made was using one:

| quantity | value |
|---|---|
| observed losses needed | control 78, treatment 35 |
| iid requirement per arm | **394** settled markets |
| design effect (larger of the two arms) | **1.93** |
| **promotion evidence floor** | **760 settled markets per arm** |
| **maximum evidence horizon** (inclusive, #247) | **1,141 settled markets per arm** |
| **early-failure floor** (`fail_any`) | **300 settled markets per arm**, unchanged |

The early-failure floor stays uninflated for the reason it always did: it is a stopping clause,
and requiring less evidence to stop errs toward stopping a good arm, which is the safe direction.

**THE FLOOR IS CONDITIONAL ON THE CONTROL'S MISS, and one number would hide that.** It is sized
from the control's replayed R = 3.93. A control that regresses toward calibration produces fewer
losses per market and therefore needs more markets:

| if the control's R settles at | floor/arm | horizon/arm | slower arm |
|---|---|---|---|
| **3.93** (replayed) | 760 | 1,141 | 288 days |
| 2.00 | 1,496 | 2,244 | 567 days |
| **1.00** (calibrated) | **2,992** | **4,487** | **1,135 days** |

That bottom row is worth pausing on: **2,992 / 4,487 is within about 10% of the withdrawn
2,725 / 4,100.** The old figures were not arbitrary — they implicitly assumed a *calibrated*
control. They were wrong about the population, not about the arithmetic, and the corrected
derivation reproduces them almost exactly under the assumption they were silently making. Both
are recorded rather than one, and **the registered floor should be the conservative row.**

**Calendar time, now measured rather than deferred.** At the replayed cadence the treatment arm
reaches 760 markets in **66 days**; the control arm needs **288 days** for the same count, because
its rule fires 2.64 times a day against the treatment's 11.59. Both arms must reach the floor for
the comparison to be powered, so **the control arm is the binding constraint** — the slow arm is
the incumbent rule, not the new one.

> **VERDICT: the A/B is PROPOSED and NOT READY TO REGISTER.** Cadence is no longer unmeasured —
> it is measured, and it is the answer. Even on the flattering assumption that the control keeps
> missing by 3.93×, the binding arm needs **288 days**; on the conservative assumption it needs
> **over three years**. Neither is an experiment worth starting as specified.
>
> What would change that is a design change, not a footnote: widen the control arm's candidate
> stream (its 6¢ edge threshold is what starves it), or raise the minimum useful effect above a
> halving, or accept a lower confidence level. **Each is a change to a pre-registered design and
> belongs in a new pre-registration**, evaluated before results are seen — not chosen now, from
> this page, with the replay's own numbers in view.

### 4.2.4 Coverage — candidate stream, not twins

Calling two independently-selecting paper arms "twins" was wrong. A twin mirrors another book's
decisions; these arms *choose differently on purpose*, and a market taken by one and not the other
**is the treatment effect**, not missing data.

What must be verified instead is that both arms saw the same **candidate stream**:

> **Candidate-stream coverage:** the share of eligible candidates evaluated by both arms in the
> same cycle. Below **95%**, the comparison is `BLOCKED_DATA` — at that point the arms are being
> offered different opportunities, which is a scan defect rather than a rule difference.

Divergence in what each arm *takes* from a shared stream is the measurement. Divergence in what
each arm is *offered* is a bug.

### 4.3 What this design buys, and what it cannot

Ranking on `mid` ascending makes the selected set a function of the **market's** price, so the
model can no longer find its own errors. The veto still uses the model — deliberately, because
some filter is needed and refusing is safe in a way promoting is not.

It is not free of selection: the cheapest tail is cheap for a reason, and adverse selection may
simply move from "the model is most wrong here" to "the market prices this lowest because it
knows something". That is a **different**, measurable mechanism rather than a self-inflicted one,
and the arms are constructed so the two can be told apart.

**Still unregistered — and now for a reason that is measured rather than missing.** An earlier
revision of this paragraph listed the treatment rule's candidate cadence as one of the things
still unknown. It is no longer unknown: §4.2.3 measures it by replaying both rules over one common
candidate stream, and the measurement is what settles the question. The control rule produces
**2.64 candidates/day** against the treatment's 11.59, so the *incumbent* arm is the binding
constraint, and it needs **288 days** to reach the promotion floor under the replay's favourable
assumption that the control keeps missing by 3.93× — **more than three years** under the
conservative assumption that it regresses to calibration. **The design is impractical as
specified.** That is a finding, not a gap.

What remains genuinely open is narrower: a forward holdout the scoring rule has not seen (§6), and
the operator's decision about whether any version of this experiment is worth its horizon.

**Anything that would make it practical is a SUCCESSOR DESIGN, not an amendment.** Widening the
candidate stream (the control's 6¢ edge threshold is what starves it), raising the minimum useful
effect above a halving, or lowering the confidence level would each change what the experiment can
conclude and how likely it is to conclude it wrongly. Each therefore requires a **fresh
pre-registration** — written, and its gates fixed, *before* results are seen. None of them may be
chosen from this page, with this replay's numbers already in view: that is precisely the
after-the-fact re-interpretation the pre-registration discipline exists to prevent.

---

## 5. Stage 5 — telemetry (implemented and tested)

All additive. **No existing semantic changes**: no probability, entry, fill or gate reads any new
column, and `_refresh_spot` still loads exactly `trail_days` of closes for the incumbent.

### 5.1 Decision-time context

`crypto_ladder_snapshots`, migration `b1d5e9f3a7c2`:

| column | what it answers |
|---|---|
| `trailing_vol_15m/60m/240m` | realized vol at the decision, bps/minute — the regime the trade entered |
| `trailing_move_15m/60m` | **signed** trailing move, bps — a tail sold into a rally is not the same trade as one sold into a selloff, and the diagnosis could only bucket on \|move\| |

### 5.2 Replacement-model shadow, with tail-correct metadata and window completeness

The first draft stored `spliced_upper_xi` beside every probability. **That is the wrong tail for a
`less` strike**, which prices off the lower one. Corrected, and everything needed to interpret a
stored probability now travels with it:

`spliced_model_p`, `spliced_active_xi` (upper for `greater`, lower for `less`, NULL for
`between`), `spliced_upper_xi`, `spliced_lower_xi`, `spliced_active_clusters`, `spliced_blocks`,
`spliced_fit_days`, `spliced_requested_fit_days`, `spliced_tail_q`, `spliced_underpowered`, plus
migration `c4f7a2b8e1d9`: `spliced_expected_blocks`, `spliced_block_coverage`,
`spliced_max_gap_min`, `spliced_horizon_min`.

Without the fit settings, two probabilities produced under different windows or thresholds are
indistinguishable in storage. Without `spliced_underpowered`, a resolution floor pools with an
estimate. Without `spliced_expected_blocks`, a window that is a third holes is indistinguishable
from a full one (§2.5). Without `spliced_horizon_min`, a stored probability cannot be matched to
the horizon that produced it.

**A probability is written only when `emittable_for(strike_type)` holds** — powered active tail
AND ≥90% block coverage. The metadata is written either way, so a refusing cycle is visible as
such rather than absent.

### 5.3 Runtime/offline parity — the same model, proved, and it runs the frozen one

`tests/test_theta_tailmodel.py::TestRuntimeOfflineParity` runs identical candles through the live
shadow path (`ThetaTracker._spliced`) and the offline harness (`FitCache`) at a decision time
deliberately **not** on the hour, across five horizons, and asserts that `p_yes` agrees to 1e-12
for `greater`, `less` and `between`, and that `n`, `horizon_min`, `tail_q`, `expected_blocks`,
`max_gap_min`, both ξ and both cluster counts are identical.

That test would have failed before this revision, three times over: different horizon, different
refit cadence, different tail quantile (the runtime silently used the module default while the
harness used whichever the sweep was scoring).

**It also asserts the shipped defaults ARE the frozen specification.** The tracker in that test is
built from the real default `Settings` with nothing configured into agreement, and
`test_the_shipped_defaults_ARE_the_frozen_specification` pins
`theta_spliced_fit_days == tailmodel.FROZEN_FIT_DAYS == 90`, `theta_spliced_tail_q == 0.90`, and
`theta_spot_retention_days == 100 > 90 > theta_trail_days`. A shadow fitted outside the frozen
window produces probabilities no verdict covers, and this catches the drift in both directions:
the default was 90 while the sweep was open, went to 30 when `refit-12` froze that under the
derived labels, and is 90 again now that `refit-13` froze it under real settlement. It tracks the
freeze, not a preference.

The rest of the class pins that the runtime reads `theta_spliced_tail_q` rather than a default,
that fits are reused across cycles inside one hour, that BTC and ETH do not share a fit, that a
failed shadow load does not break the cycle, and that the budget withholds rather than delays.

### 5.4 What the shadow costs a trading cycle

**The production number does not exist yet, and this section does not pretend otherwise.**

`scripts/theta_shadow_bench.py` measures the **arithmetic and memory** half — fits, cache
behaviour, close-set materialisation — with no database and no network. At 480 markets/cycle over
a full frozen window:

| quantity | value |
|---|---|
| close-set loads over 24 cycles | **6** (was 48) |
| distinct GPD fits | **36** for 11,520 quotes priced |
| fit cache hit rate | 99.7% |
| shadow ms per cycle, p50 / p90 / max | **0.0 / 0.0 / ~1,800** |

**That is a lower bound.** The "load" line is in-process object construction; production adds the
Postgres round trip and row decode, and that is the dominant term. The synthetic figure cannot
measure it.

Three mechanisms bound the cost, in order of how much they matter: the close set is **held for its
refit anchor** rather than reloaded every cycle; the horizon grid caps fits at 6 per product per
anchor, so a 240-strike ladder costs the same as a 6-strike one; and `theta_spliced_budget_ms` is
the backstop.

**The budget is a SOFT cycle budget.** That phrasing is deliberate and it is a correction: two
earlier revisions of this section claimed a strict bound on elapsed time, and the mechanism does
not provide one. Three defects, in the order they were found:

1. It gated **fit time** — the cheap half — while the expensive half, the close-set load, was
   reported after the fact and could not be stopped. Elapsed accounting now spans the whole
   shadow: statement, row transfer, decode, model construction and fits.
2. It then handed the **full configured budget to every load**, so two products could authorise
   two full budgets. Each load now receives the **remainder** — `budget − already_spent`,
   computed immediately before it — and a load does not start with less than
   `MIN_LOAD_BUDGET_MS` (50 ms) left. The invariant is `spent + authorised ≤ budget` at every
   load. That bounds **authorised statement time**, which is not the same thing as elapsed time —
   see the honest statement below.
3. The remainder is enforced by Postgres, because a Python-side deadline cannot interrupt a query
   already issued — and enforcing it there brings two hazards a bare `SET LOCAL` plus `try/except`
   does not handle. **A statement timeout aborts the transaction**, so catching the exception left
   the shared session unusable and the trading loop's own writes would have failed with
   `InFailedSqlTransaction`; and **`SET LOCAL` outlives a savepoint release**, so on the success
   path the shadow's research budget silently became a timeout on those writes for the rest of the
   cycle. Both are confined by `repo.bounded_statement`: a savepoint whose rollback restores the
   enclosing transaction, and an explicit reset before release.

Over budget, or a timed-out load, means metadata without a probability and the scan continues.

#### What the budget guarantees, and what it does not

Stated plainly, because two earlier revisions overclaimed it and a safety property that is
believed but false is worse than one that is absent:

| | |
|---|---|
| **hard** | PostgreSQL **statement execution** is cut off at the authorised remainder. The database enforces it; a Python-side deadline cannot interrupt a query already issued. |
| **hard** | Cumulative **authorised** timeout across a cycle cannot exceed `theta_spliced_budget_ms`. |
| **hard** | The trading loop's transaction survives a timeout — savepoint-confined, proved on real PostgreSQL. |
| **measured** | Elapsed accounting spans the **whole** shadow — statement, row transfer, decode, `SpotModel` construction, fits — not just the fit. |
| **enforced** | No **further** load or fit starts once the measured budget is spent. |
| **NOT guaranteed** | A strict ceiling on elapsed wall-clock time. |

The gap is specific and worth naming exactly. The statement timeout ends the *statement*. Once
PostgreSQL returns, client-side row transfer, materialisation into dicts and `SpotModel`
construction are ordinary Python on the trading loop's thread, and nothing interrupts them. A load
that clears its statement deadline with 10 ms left can still spend far longer than that
materialising ~43,000 rows. So the budget stops the *next* unit of work; it cannot shorten one
already running, and a cycle can overrun.

How large that overrun can be is **unmeasured in production**, which is the honest reason the
`theta: shadow cost` telemetry below is an obligation rather than a nicety.

**If a strict wall-clock ceiling is ever required**, the mechanism is not a tighter timeout — it is
moving the close-set load and model construction **off the trading-loop thread**, so the scan can
abandon a slow shadow instead of waiting for it. That is a real change to a live path (the shadow
shares the loop's SQLAlchemy session, which is not thread-safe, so it would need its own
connection), and it is deliberately **not** in this PR: every book is stood down, no book prices
off the shadow, and the failure mode it would prevent is a late quote in a paper book. Until then
this is a soft cycle budget and is described as one.

**Proved against real PostgreSQL**, not a mock — `tests/test_theta_shadow_postgres.py`. A fake
loader that raises can never exhibit transaction abort, which is the behaviour that matters. The
tests force a real timeout and assert the outer transaction still executes afterwards, that the
timeout does not leak past the bounded block, that successive loads see a shrinking remainder, and
that `spent + authorised ≤ budget` holds at each one.

All seven pass on PostgreSQL 16, and they are **not vacuous**: replacing `bounded_statement` with
the naive version — bare `SET LOCAL`, no savepoint — fails exactly the three transaction-recovery
tests, each with `psycopg.errors.InFailedSqlTransaction` on the statement standing in for the
trading loop's next write. That is the production defect, reproduced on demand. CI runs the whole
suite against a `postgres:16` service, so these execute on every PR rather than skipping.

**Outstanding, and stated as such:** `theta_spliced_budget_ms = 3000` is derived from the
synthetic benchmark, not from production. The worker now logs one `theta: shadow cost` line per
cycle carrying `theta_shadow_ms` (total), `theta_shadow_load_ms`, `theta_shadow_loads` and
`theta_shadow_fits`. **After this merges and deploys**, collect it over several hourly reloads —

```
{"type":"logs","service":"main","filter":"theta: shadow cost"}
```

— and report p50, p90, p99 and maximum before calling the backstop production-derived. Until then
it is a synthetic figure with a database timeout behind it, and the claim in an earlier revision
that it was measured in production is withdrawn.

### 5.5 Subsequent spot path — a tested research product, not a retention promise

Retention makes the decision→candle join possible. It does not make it complete, and a
forward-path feature set with silent gaps is worse than none: the missing markets cluster around
feed outages, so dropping them silently selects on the regime being studied.

`scripts/theta_forward_path.py` is the product and the proof. Per decision snapshot, deterministic
from retained 1-minute closes: forward log return at **+5m, +15m, +30m and market close**;
**maximum favourable and adverse excursion** oriented by the side the book SOLD; and **coverage
per offset, reported before any economics**, with excursions withheld below 90% path completeness.
`between` strikes are excluded from directional MAE/MFE and counted, because `max(up, −dn)` is
wrong from inside the band and meaningless from outside it.

### 5.6 Backfill, so the window exists now rather than in three months

`crypto_spot_candles` was pruned at `trail_days + 1` = 6 days, and a "90 days of retention" that
takes 90 days of wall-clock to mean anything is not a fit window. Probe `cb-probe-5` (2026-08-21)
measured Coinbase serving 1-minute candles **at least 365 days back** for both products, so
`_backfill_spot` extends the stored history *backward* at
`theta_spot_backfill_requests_per_cycle` requests per cycle (default 12), filling ~90 days over a
few hours instead of hammering a public endpoint in one pass. Best-effort and never fatal.

---

## 6. Superseded — what earlier revisions of this document claimed

Kept because the corrections are the useful part of the record, not because the claims are.
**None of the following stands.**

| claim | status | why |
|---|---|---|
| `refit-8` is the defensible current freeze | **superseded** | ran before the event was the evidence unit, before the label gate, and on a one-month window. Every interval it reported was ~2.5× too narrow. Replaced by §3. |
| Configuration selection by deviance per deep quote | **superseded** | three defects, each found by running it: `\|log(o/e)\|` is undefined at zero observed and silently discarded every 90-day candidate; raw Poisson deviance rewarded configurations that powered almost nothing (an earlier run froze a window covering 5.7% of quotes); and both were aggregate counts on a set each configuration defined for itself. Replaced by mean Bernoulli log loss on a market-price-defined common population, gated at ≥90% coverage. |
| "TEST is read once" | **false as written** | the August window had already been reported on before the scoring rule was fixed. It is labelled **historical validation** throughout, and a forward one-look holdout is reserved. |
| Selection bias "grew from 4.6× to 6.1×" | **retracted** | the two models select different populations of different sizes (135 markets against 25), so a ratio between them is not a comparison. The *reason* first given here — that fattening the tail shrinks `excess` and so selects less — is itself withdrawn: it predicts the spliced model selecting FEWER, and it selects more. What §3.1 measures is that the incumbent is bimodal, exactly zero on 84.9% of markets and comparatively large on the rest, so it rejects on its own large estimate (§3.7). |
| Calibration validated while the settlement-label bar was failing at 96.9% | **retracted** | a calibration computed against labels that fail their own quality bar is not validated. The bar is now a gate that runs before anything is scored (§1.2, §3.1), and it passes on the retained population — but it passes *because* the audit was repaired, not because the bar moved. |
| Selection bias "established" because the SELECTED and REJECTED intervals were disjoint | **withdrawn as a TEST** | two marginal intervals are not a test of `log(R_sel/R_rej)`: the groups partition the same ladders, so their errors covary, and non-overlap is neither necessary for the ratio to exclude 1 nor reliably sufficient once the estimates are dependent. Replaced by a direct event-clustered contrast (§3.7). The spliced model's finding survives it; the incumbent's does not. |
| `theta_spliced_budget_ms` handed in full to every load | **fixed** | N loads authorised N budgets, so authorisation was unbounded in the number of products. Each load now receives `budget − already_spent` (§5.4). |
| The shadow budget "bounds total elapsed time" / "no load can push the cycle past its bound" | **withdrawn** | the PostgreSQL statement timeout bounds statement EXECUTION. Client-side row transfer, materialisation and `SpotModel` construction run afterwards, on the trading loop's thread, and nothing interrupts them — so a load already in flight can overrun. It is a **soft** cycle budget: hard on statement time and on cumulative authorisation, enforced on the *next* unit of work, not a wall-clock ceiling (§5.4). A strict ceiling needs the load moved off-thread. |
| A bare `SET LOCAL statement_timeout` around the shadow read | **fixed** | a statement timeout ABORTS the transaction, so catching the exception left the shared session unusable for the trading loop's own writes; and `SET LOCAL` outlives a savepoint release, so on the success path the research budget silently became a timeout on those writes. Both now confined by `repo.bounded_statement` and proved against real Postgres. |
| Stage-4 floors of 2,725 / 4,100 markets per arm | **withdrawn** | sized from the spliced model's *historical excess-ranked selected set*, which is not the treatment arm — the treatment ranks on `mid` behind a veto and draws a different pool at a different rate. Replaced by a replay of both rules over one common candidate stream (§4.2.3): **760 / 1,141** at the control's replayed R = 3.93, **2,989 / 4,483** if the control regresses to calibration. The old pair turns out to be almost exactly the calibrated-control row — right arithmetic, wrong population. |
| Stage-4 calendar cost "not yet measurable" | **resolved, and the answer is bad** | the candidate cadence is now measured on the replayed stream: control 2.64/day, treatment 11.59/day. The CONTROL is the binding arm at 288 days to the floor, not the treatment. The arm is not registrable as specified (§4.2.3). |
| `refit-12`'s freeze at `fit_days=30` | **superseded** | frozen under the DERIVED labels, which do not clear their own agreement bar. Under Kalshi's real settled results the selector picks 90 — by a fifth-decimal margin (§3.4). The runtime tracks the current freeze. |
| Settlement-label agreement reported as `[1.0000, 1.0000]`, 99% | **withdrawn** | a percentile bootstrap on an all-agreeing sample cannot produce a disagreement, so the interval is a boundary artefact of the method. The exact clustered bound on the same evidence is 92.6%. |
| The derived outcome labels PASS their quality bar | **withdrawn** | they do not, under a bound valid at zero failures. They are replaced rather than argued with: Kalshi's own results cover 100% of the universe (§1.2). |
| `theta_spliced_budget_ms` described as production-derived | **withdrawn** | it comes from a synthetic benchmark that cannot measure PostgreSQL. The production telemetry exists in the code and has not been collected (§5.4). |
| Taxonomy evidence reported as "100% of N texts" | **withdrawn** | one rule document copied onto N markets is one observation, not N. See `RESEARCH_MMSELL_2X2_PAPER_DESIGN.md` §2A.1b. |
| "Against theta4's actual incumbent the replacement is a **regression**" | **withdrawn** | that rested on two separate unclustered aggregate-R intervals over a one-month window. The paired proper-score comparison on a longer window (§3.6) returns **neither**, on both weightings and both scores. The correct statement is that the models fail differently and superiority is not established. |
| "No refitted tail is claimed as validated" | **false once §3 existed** | a tail was fitted and was validated. What that validation says has changed; that it happened has not. |

---

## 7. The record, stated plainly

**Degeneracy was a real defect and removing it worked. Nothing else in this programme has been
established, and the cause of theta4's failure remains unidentified.**

Concretely, and without hedging in either direction:

- The incumbent prices **85.3%** of this universe at exactly 0.0, which is not a probability, and
  it does that *at `mult=2.0`* — the doubling theta4 actually ran. The spliced model prices none
  of them there. That is a genuine repair and it is the only unambiguous success here.
- **Neither model is better than the other.** All four paired differences — two proper scores ×
  two weightings — contain zero, and the two scores disagree even in sign.
- **Neither model is acceptable for a short-tail seller.** One declares most of its universe
  impossible and calibrates on the remainder; the other answers everywhere and understates its
  deepest buckets by 3–4× at the point estimate. Choosing between them on aggregate R would pick
  the one that refuses to answer.
- **The deep-tail miss is not statistically established.** Every deep bucket's interval contains
  1 once the shared settlement print is respected, though the deepest point estimate is **8.4×**.
  The point estimates describe a model that would lose money on the tail; the evidence does not
  rule out chance.
- **Residual selection bias is established for the spliced model, by a direct test of the
  contrast**: `log(R_sel/R_rej) = +1.486`, 99% CI [+0.660, +1.982] — a 4.42× miss that survives
  the calibration repair, which is what stage 4 exists to act on. **For the incumbent it is
  suggestive only**: 25 markets, 18 events, interval spanning zero.
- **Stage 4 is designed, sized, and NOT worth registering as specified.** Replaying both proposed
  rules over one common candidate stream (§4.2.3) measures the thing every earlier revision
  assumed: the control rule produces only **2.64 candidates/day** against the treatment's 11.59,
  so the *incumbent* arm is the binding constraint. Even sized on the control's flattering
  replayed R = 3.93 the slower arm needs **288 days**; sized on a control that regresses to
  calibration it needs **over three years**. The floors are **760 / 1,141** and **2,992 / 4,487**
  respectively — and the second pair almost reproduces the withdrawn 2,725 / 4,100, which is what
  those numbers were silently assuming.
- **The outcome labels are Kalshi's own settled results, at 100% coverage.** The derivation they
  replace agrees 99.90% but cannot clear its own 97% bar under a bound valid at zero failures
  (92.70%, 54 of 1,000 events) — and two bugs in my own audit of it were found and disclosed
  (§1.2).

### 7.1 What is still unproven

The cause of theta4's R ≈ 4 is **not identified**. Degeneracy is ruled out as a sufficient
explanation, and no extreme-value model of realized spot returns fitted over 30–90 days
distinguishes itself from the incumbent. The momentum/regime hypothesis remains untested (§5.1
supplies the telemetry, §5.5 the forward path). The selection-rule A/B (§4) tests a different
mechanism again and is not registered.

There is no forward holdout. The August window informed the scoring rule and cannot also certify
it, so the next honest test of this specification is a period that starts **after** this document
is merged — which is exactly what the live shadow (§5.2) is accumulating.

### 7.2 What was deliberately not done

`mult` was not increased — the evidence says the failure is not a scale error, and a second
doubling would repeat a settled mistake. No theta parameter changed at all; `SpotModel` is
untouched and every book prices exactly as before. No book re-armed, retired or created; no gate
written or re-interpreted; the 50-market early-failure floor not moved; no threshold changed
because a result was inconvenient. No live restart. The stage-4 design is a proposal with derived
floors, not a registration. `SERIES_TYPES` is untouched and the taxonomy repair is routed to
Platform Change Review (`docs/RESEARCH_MMSELL_2X2_PAPER_DESIGN.md`).
