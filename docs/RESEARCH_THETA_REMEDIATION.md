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
| 1. replace the degenerate 0/1 output | **done and verified** — 86.2% exactly-0 → **0.0%** |
| 2. refit the tail shape, validate out of sample | **run, powered, label-gated — and it does not beat the incumbent** (§3.6) |
| 3. re-measure calibration + residual selection bias | **done** — bias suggested, not established (§3.7) |
| 4. selection-rule A/B that does not rank on model error | **redesigned**, floors recomputed on the event (§4); still unregistered |
| 5. telemetry for momentum/regime | **implemented, benchmarked and tested** (§5) |

**The headline, before the detail.** With a 30-day fit window, non-overlapping blocks, a coherent
marginal EVT construction and declustered per-tail power, every quote is powered, so the
replacement model could finally be judged. Judged **paired**, on proper scores, with the ladder
event as the independent unit and against the incumbent theta4 actually runs, it is **not better
and not worse**: all four comparisons contain zero and the two proper scores disagree in sign.

That is a different answer from the one this document gave a day ago, and the reason is not new
data. It is that the previous comparison read two separately-computed aggregate-R intervals as
though that were a test of a difference, and computed both of them as if 66 markets on one ladder
were 66 independent observations. Correcting those two things dissolves the finding. Nothing has
been established about which model is better, and this document no longer claims otherwise.

**What has not changed:** neither model is acceptable for a short-tail seller, the cause of
theta4's failure is still unidentified, and every theta book stays stood down.

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

### 1.2 The outcome label is derived, and had to be qualified before use

Every calibration number rests on an outcome. That outcome is **not** Kalshi's settlement print:
it is the last ladder snapshot's spot before close, compared to the strike. Kalshi settles off
its own index at the close, up to three minutes later, and whatever spot did in those minutes is
unobserved.

`scripts/theta_settlement_labels.py` establishes what that is worth, and it is a gate rather than
a footnote — the refit applies it before anything is scored.

**Recorded settlement cannot replace the derivation.** Every table that could hold a real Kalshi
result was checked rather than assumed empty:

| source | ladder rows | note |
|---|---|---|
| `markets` | 0 | no result column, and the ladder collector does not write here |
| `market_snapshots` | 0 | a terminal price would be a settlement print |
| `backfill_regime_markets` / `backfill_weather_markets` | absent | table/column not present |
| `paper_trades.resolved_value` | 1,275 | real settlement — but only where a book traded |

Of 66,201 markets in the scored universe, **220 (0.33%)** have a recorded settlement. Those exist
only where a book traded, which is the selected subset, so they can **audit** the derivation but
not replace it.

**How wrong the proxy can be, measured from the spot series alone.** RMS absolute move over the
unobserved remainder, from dense Coinbase 1-minute closes (n = 61,321 pairs per lag):

| product | 1m | 2m | 3m | 4m | 5m |
|---|---|---|---|---|---|
| BTC | $30.9 | $43.4 | $52.8 | $61.0 | $67.9 |
| ETH | $1.3 | $1.9 | $2.3 | $2.6 | $2.9 |

Both scale as √t to two figures, which is what a diffusion does and a good sign the estimator is
sound. The first version of this ran on the ladder-snapshot reconstruction, which is ~5-minute
sampled and only near settlement; for ETH almost no (t, t−lag) pair existed and it reported a
4-minute RMS move of **$0.20**. That was a bug in my own instrument, and the fix was a denser
series, not a different threshold. A cell built on fewer than 500 observed pairs is now marked
thin and refused.

**The exclusion rule, fixed from the geometry before the agreement was recomputed.** A market
whose strike sits within **K = 2** residual sigmas of the final observed spot is ambiguous by
construction — the unobserved move is large enough to have carried it across. Two sigma is a
~95% band. It is a property of the measurement, not of which side of the bar the answer lands.

**Result**, against bars fixed before the run (agreement ≥ 97%, retained coverage ≥ 90%):

| population | n | agreement | 99% CI (event-clustered) |
|---|---|---|---|
| all overlapping markets | 220 | 98.18% | [0.9524, 1.0000] |
| near-strike excluded (K=2) | 197 | **100.00%** | [1.0000, 1.0000] |

Disagreements sit exactly where the mechanism predicts and vanish where it predicts:

| distance from strike (σ) | n | disagree | rate |
|---|---|---|---|
| 0 – 0.5 | 5 | 2 | 40.0% |
| 0.5 – 1 | 8 | 0 | 0.0% |
| 1 – 2 | 10 | 2 | 20.0% |
| 2 – 4 | 26 | 0 | 0.0% |
| 4 – ∞ | 171 | 0 | 0.0% |

On the **population** the rule discards 490 of 66,201 markets — retained coverage **99.26%** —
because theta sells deep strikes, which are far from spot by construction.

**VERDICT: labels usable on the retained population.** Every number in §3 is computed on exactly
that population.

Two corrections to my own audit, both found by running it and both disclosed rather than quietly
repaired: the ETH scale above, and a first version that computed retained coverage on the
220-market **audit overlap** instead of the population. The overlap is the traded subset; asking
what share of it survives is a different and easier question than the bar was written to ask.

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
| fit window | `theta_spliced_fit_days`, **paper only** | §2.2 |
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
| `theta_spot_retention_days` | 90 | the pruner only |
| `theta_spliced_fit_days` | 90 | the **paper** replacement model only |

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
{"type":"script","id":"refit-12","name":"theta_tail_refit","args":[
  "--since","2026-07-11","--train-end","2026-08-11","--fit-days","5,30,90",
  "--tail-qs","0.90,0.95,0.99","--spot-source","coinbase","--vol-mult","2.0"]}
```

`--vol-mult 2.0` is deliberate: **theta4 does not run the base model.** Scoring against
`mult=1.0` would compare the replacement to a model that never traded.

The cluster bootstrap is seeded (`seed=20260822`) and the same seed reproduces the same
interval endpoints exactly. An analysis whose uncertainty moves between runs cannot be a frozen
result.

### 3.1 Label quality — the gate, passed

571 of 66,201 markets fall inside the near-strike exclusion (retained coverage **99.14%**, bar
90%). On the retained overlap the derived label agrees with recorded Kalshi settlement
**100.00%** of the time across 193 markets in 63 events (bar 97%, event-clustered). **PASS.**
Everything below is scored on that retained population: 65,630 markets.

### 3.2 Evidence structure — 65,630 markets are worth about 12,000 observations

| quantity | value |
|---|---|
| scored markets | 65,630 |
| distinct events | 988 |
| markets per event (mean / p50 / p90 / max) | 66.4 / 52 / 97 / 113 |
| Kish effective events | 886.4 |
| largest single event's share | 0.17% |

| statistic | design effect | effective n |
|---|---|---|
| hit indicator | 4.6 | 14,414 |
| spliced log loss | 5.4 | 12,182 |
| incumbent log loss | 5.2 | 12,544 |

An interval computed as though these rows were independent is about **√5 ≈ 2.3× too narrow**.
That is the size of the error the previous revisions' Poisson intervals carried.

### 3.3 Degeneracy — fixed, and it was the one unambiguous success

| model | n | exactly 0 | exactly 1 | in (0,1) |
|---|---|---|---|---|
| incumbent (empirical, `mult=2.0`) | 65,630 | 56,569 (**86.2%**) | 0 | 9,061 (13.8%) |
| spliced EVT | 65,630 | 0 (**0.0%**) | 0 | 65,630 (100%) |

`SpotModel.prob_from_returns` is a raw empirical frequency, so it has no mass beyond its own
sample maximum. `vol_mult` rescales the *threshold* (`x / k`), which pulls some strikes back
inside the support but cannot escape a hard edge — pinned in
`test_vol_mult_cannot_escape_the_truncation`. Doubling the volatility left 86.2% of this
universe priced at exactly zero.

### 3.4 The window, not the estimator, was the constraint

| fit window | tail_q | powered share of TRAIN |
|---|---|---|
| 5 d | 0.90 | 2.8% |
| 5 d | 0.95 | 0.0% |
| 5 d | 0.99 | 0% |
| 30 d | 0.90 / 0.95 | 100% |
| 30 d | 0.99 | 0.1% |
| 90 d | 0.90 / 0.95 / 0.99 | 100% |

At theta's own five-day window a peaks-over-threshold fit is powered essentially **nowhere**.
Selection, on TRAIN only, by mean Bernoulli log loss on the common population (`mid ≤ 20c`), with
configurations below 90% coverage refused:

| fit_days | tail_q | coverage | common n | log loss | Brier |
|---|---|---|---|---|---|
| 30 | **0.90** | 100% | 50,365 | **0.00374** | 0.00074 |
| 30 | 0.95 | 100% | 50,365 | 0.00375 | 0.00074 |
| 90 | 0.90 | 100% | 50,365 | 0.00382 | 0.00075 |
| 90 | 0.95 | 100% | 50,365 | 0.00382 | 0.00075 |
| 90 | 0.99 | 100% | 50,365 | 0.00385 | 0.00075 |

**FROZEN on TRAIN: `fit_days=30`, `tail_q=0.90`.** The spread across eligible configurations is
under 3% of the score; the window mattered, the threshold barely did.

### 3.5 Historical validation — 15,067 quotes, 246 events

Aggregate R, spliced **1.10 [0.37, 2.21]**; incumbent **0.48 [0.16, 0.95]**. Read as two separate
descriptions, not a comparison: the incumbent over-predicts hits by about 2× in aggregate while
declaring 12,564 of these 15,067 quotes impossible, and the spliced model's aggregate R now
contains 1 — but its deep buckets do not.

Deep tail, spliced, out of sample:

| modeled P | n | events | expected | observed | R | 99% CI |
|---|---|---|---|---|---|---|
| 0.000–0.002 | 13,959 | 246 | 2.77 | 11 | 3.96 | [0.00, 13.78] |
| 0.002–0.005 | 336 | 153 | 1.10 | 4 | 3.64 | [0.00, 14.15] |
| 0.005–0.010 | 183 | 131 | 1.33 | 2 | 1.50 | [0.00, 6.25] |
| 0.010–0.020 | 138 | 109 | 1.99 | 6 | 3.02 | [0.00, 7.14] |
| 0.020–0.050 | 169 | 136 | 5.36 | 9 | 1.68 | [0.37, 3.82] |

Every deep-bucket interval now **contains 1**. The point estimates still say the model
understates 0.2–2% events by 3–4×, and that is the shape a short-tail seller cannot afford — but
once the shared settlement print is respected, **none of it is established**. The previous
revision's disjoint deep-bucket intervals were an artefact of counting one ladder as a hundred
observations.

### 3.6 The paired comparison — what actually decides between the models

Common population: powered quotes with `mid ≤ 20c`, fixed by the market's own price, identical
for both models. **n = 14,941 markets across 246 events** (Kish effective events 210.3).

| weighting | model | mean log loss | mean Brier |
|---|---|---|---|
| market | incumbent | 0.01197 | 0.00223 |
| market | spliced | 0.01307 | 0.00205 |
| event | incumbent | 0.01977 | 0.00415 |
| event | spliced | 0.02280 | 0.00414 |

Paired difference, spliced − incumbent (negative favours the spliced model), event-clustered 99%:

| weighting | statistic | difference | 99% CI | favours |
|---|---|---|---|---|
| market | log loss | +0.00110 | [−0.00422, +0.01106] | neither |
| market | Brier | −0.00018 | [−0.00049, +0.00029] | neither |
| event | log loss | +0.00303 | [−0.00672, +0.02016] | neither |
| event | Brier | −0.00001 | [−0.00078, +0.00127] | neither |

> **The models fail differently; superiority is not established.**

The two proper scores disagree even in sign — log loss slightly prefers the incumbent, Brier
slightly prefers the spliced model — and every interval contains zero on both weightings. That
is a genuine result, not a null from thin data: 14,941 markets is not a small sample, it is a
sample worth ~2,900 independent observations once the ladder structure is respected.

The two failure modes are not symmetric, and neither is acceptable for a short-tail seller. The
incumbent declares 84% of this population impossible and is roughly calibrated on the remainder;
the spliced model answers everywhere and understates its deepest buckets by 3–4× at the point
estimate. **Choosing on aggregate R would pick the one that refuses to answer**, since a zero
costs nothing in a denominator. That is why the frozen rule is a per-prediction proper score.

### 3.7 Selection bias after the repair — suggested, not established

| model | set | n | events | expected | observed | R | 99% CI |
|---|---|---|---|---|---|---|---|
| incumbent | SELECTED | 22 | 15 | 1.32 | 2 | 1.51 | [0.00, 6.64] |
| incumbent | REJECTED | 65,608 | 988 | 404.35 | 149 | 0.37 | [0.23, 0.53] |
| spliced | SELECTED | 148 | 79 | 4.95 | 17 | **3.44** | [0.84, 7.54] |
| spliced | REJECTED | 65,482 | 988 | 197.87 | 134 | **0.68** | [0.43, 0.96] |

Under the spliced model the selected set still misses by 3.4× against 0.68 on its complement —
but the intervals **overlap** in [0.84, 0.96] once clustered, so at 99% this is a strong
suggestion rather than a finding. The incumbent's selected set is 22 markets across 15 events and
says nothing at all.

The two SELECTED sets are **different populations of different sizes** — fattening the tails
shrinks `excess`, so the models select 148 and 22 markets respectively. A ratio between them is
not a comparison, and none is drawn.

### 3.8 Fit health

| quantity | p10 | p50 | p90 |
|---|---|---|---|
| non-overlapping blocks | 1,234 | 1,440 | 1,440 |
| expected block slots | 1,234 | 1,440 | 1,440 |
| **block coverage** | 1.000 | 1.000 | 1.000 |
| longest window gap (min) | 0.0 | 0.0 | 0.0 |
| upper-tail declustered exceedances | 85 | 95 | 102 |
| lower-tail declustered exceedances | 88 | 95 | 107 |
| upper ξ | 0.045 | 0.129 | 0.245 |
| lower ξ | 0.046 | 0.170 | 0.251 |

All 65,630 quotes have a powered active tail; **0** have a powered tail on an incomplete window.
Coverage is 1.000 throughout because this run reads Coinbase, which has no gaps — the coverage
gate exists for the **live** shadow, where the close set is the bot's own collection and the
holes are its own restarts. 1.0% of active tails are bounded (ξ<0) and extrapolate with max(ξ, 0);
a fitted ξ<0 asserts a hard maximum move, which is the claim that started this programme.

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

### 4.2.3 Floors, recomputed with the event as the unit

The previous floors were derived from an **iid** count of settled markets. That was the same
error as §1.1 in a different place, and it made the arm look about 2.6× cheaper than it is.

From §3.7, the spliced rule's selected set is 148 markets across **79 events**, carrying 4.95
expected tail events — λ ≈ **0.0334** expected events per selected market. Its design effect is
**not** the population's 4.6: selection spreads thinly across ladders, 1.87 markets per event, so
the worst-case inflation (ρ = 1, every selected market on a ladder sharing its outcome) is 1.87.

To resolve `log(R_T / R_C)` at the minimum useful effect — a halving, `|log 0.5| = 0.693` — with
80% power at a two-sided 99% interval:

| quantity | value |
|---|---|
| expected tail events per selected market (λ) | 0.0334 |
| iid requirement per arm | ≈ 1,450 settled markets |
| design effect inside the selected set | 1.87 |
| **promotion evidence floor** | **≈ 2,725 settled markets per arm** |
| **maximum evidence horizon** (inclusive, #247) | **4,100 settled markets per arm** |
| **early-failure floor** (`fail_any`) | **300 settled markets per arm**, unchanged |

The early-failure floor is deliberately **not** inflated. It is a stopping clause: requiring less
evidence to stop errs toward stopping a good arm, which is the safe direction, and inflating it
would slow the detection of a bad one. Its power is correspondingly lower under clustering and
that is accepted.

**The calendar cost is not yet measurable, and is stated as unmeasured.** The previous revision
put it at ~105 days per arm from a live-order cadence of ~10 markets/day. That number came from
theta4's live order count, not from this candidate stream, and the two are not the same
population. On the snapshot universe the *spliced* rule selects 3.5 markets/day and the incumbent
rule 0.5/day — but the treatment rule (cheapest eligible tail, model veto) selects from a broader
set than either and its cadence has not been measured. **Measuring the treatment rule's candidate
cadence is a prerequisite to registering this arm**, because a 2,725-market floor at 3.5
markets/day is over two years and at 10 markets/day is nine months, and the difference decides
whether the experiment is worth running at all.

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

**Still unregistered.** Three things are missing, and none of them is the operator's patience:
the treatment rule's candidate cadence (§4.2.3), a forward holdout the scoring rule has not seen
(§6), and the operator's decision to spend the horizon.

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

### 5.3 Runtime/offline parity — the same model, proved

`tests/test_theta_tailmodel.py::TestRuntimeOfflineParity` runs identical candles through the live
shadow path (`ThetaTracker._spliced`) and the offline harness (`FitCache`) at a decision time
deliberately **not** on the hour, across five horizons, and asserts that `p_yes` agrees to 1e-12
for `greater`, `less` and `between`, and that `n`, `horizon_min`, `tail_q`, `expected_blocks`,
`max_gap_min`, both ξ and both cluster counts are identical.

That test would have failed before this revision, three times over: different horizon, different
refit cadence, different tail quantile (the runtime silently used the module default while the
harness used whichever the sweep was scoring). It also pins that the runtime reads
`theta_spliced_tail_q` rather than a default, that fits are reused across cycles inside one hour,
that BTC and ETH do not share a fit, and that the cycle budget withholds rather than delays.

### 5.4 What the shadow costs a trading cycle

`scripts/theta_shadow_bench.py`, at 480 markets/cycle over a full 90-day window:

| quantity | value |
|---|---|
| closes per load (both products) | 259,200 |
| close-set loads over 24 cycles | **6** (was 48) |
| distinct GPD fits | **36** for 11,520 quotes priced |
| fit cache hit rate | 99.7% |
| shadow ms per cycle, p50 / p90 / max | **0.0 / 0.0 / 1,806** |
| peak memory, one product's close set | 14.1 MB |

Three mechanisms bound it, in order of how much they matter: the close set is **held for its
refit anchor** rather than reloaded every cycle (this is the dominant cost, and it is database
work on the trading loop's thread, not arithmetic); the horizon grid caps fits at 6 per product
per anchor, so a 240-strike ladder costs the same as a 6-strike one; and
`theta_spliced_budget_ms` is the backstop for a pathological window.

The budget is **3,000 ms**, set from this measurement. The first draft said 750 ms, which is
*below* the measured maximum — a backstop that fires during normal operation is not a backstop,
it would have silently gapped the research series every hour. Against a 300,000 ms scan interval
the worst cycle is 0.6%.

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
| `refit-8` is the defensible current freeze | **superseded** | ran before the event was the evidence unit, before the label gate, and on a one-month window. Every interval it reported was ~2.3× too narrow. Replaced by §3. |
| Configuration selection by deviance per deep quote | **superseded** | three defects, each found by running it: `\|log(o/e)\|` is undefined at zero observed and silently discarded every 90-day candidate; raw Poisson deviance rewarded configurations that powered almost nothing (an earlier run froze a window covering 5.7% of quotes); and both were aggregate counts on a set each configuration defined for itself. Replaced by mean Bernoulli log loss on a market-price-defined common population, gated at ≥90% coverage. |
| "TEST is read once" | **false as written** | the August window had already been reported on before the scoring rule was fixed. It is labelled **historical validation** throughout, and a forward one-look holdout is reserved. |
| Selection bias "grew from 4.6× to 6.1×" | **retracted** | fattening the tails shrinks `excess`, so the two models select different populations of different sizes. A ratio between them is not a comparison. |
| Calibration validated while the settlement-label bar was failing at 96.9% | **retracted** | a calibration computed against labels that fail their own quality bar is not validated. The bar is now a gate that runs before anything is scored (§1.2, §3.1), and it passes on the retained population — but it passes *because* the audit was repaired, not because the bar moved. |
| "Against theta4's actual incumbent the replacement is a **regression**" | **withdrawn** | that rested on two separate unclustered aggregate-R intervals over a one-month window. The paired proper-score comparison on a longer window (§3.6) returns **neither**, on both weightings and both scores. The correct statement is that the models fail differently and superiority is not established. |
| "No refitted tail is claimed as validated" | **false once §3 existed** | a tail was fitted and was validated. What that validation says has changed; that it happened has not. |

---

## 7. The record, stated plainly

**Degeneracy was a real defect and removing it worked. Nothing else in this programme has been
established, and the cause of theta4's failure remains unidentified.**

Concretely, and without hedging in either direction:

- The incumbent prices **86.2%** of this universe at exactly 0.0, which is not a probability, and
  it does that *at `mult=2.0`* — the doubling theta4 actually ran. The spliced model prices none
  of them there. That is a genuine repair and it is the only unambiguous success here.
- **Neither model is better than the other.** All four paired differences — two proper scores ×
  two weightings — contain zero, and the two scores disagree even in sign.
- **Neither model is acceptable for a short-tail seller.** One declares most of its universe
  impossible and calibrates on the remainder; the other answers everywhere and understates its
  deepest buckets by 3–4× at the point estimate. Choosing between them on aggregate R would pick
  the one that refuses to answer.
- **The deep-tail miss is no longer statistically established.** Every deep bucket's interval
  contains 1 once the shared settlement print is respected. The point estimates still describe a
  model that would lose money on the tail; the evidence no longer rules out chance.
- **Residual selection bias is suggested, not established**: SELECTED 3.44 [0.84, 7.54] against
  REJECTED 0.68 [0.43, 0.96], overlapping in [0.84, 0.96].
- **The outcome labels are usable**, at 100% agreement on the retained population — but only after
  two bugs in my own audit were found and disclosed (§1.2).

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
