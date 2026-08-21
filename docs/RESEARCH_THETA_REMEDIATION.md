# Research Lab — theta remediation, stages 1–5

Follow-on to `RESEARCH_THETA_TAIL_MODEL_DIAGNOSIS.md`, which found two composing mechanisms: a
tail-shape error in the model and a threshold-selection bias on top. This is the repair
programme. **Paper only. No theta parameter changed, no book re-armed, no live money.**

**Reproduce:** `{"type":"script","name":"theta_tail_refit","args":["--since","2026-08-01",
"--train-end","2026-08-14","--fit-days","5,30,90","--spot-source","coinbase"]}` (run `refit-8`).

---

## 0. The headline, before the detail

| stage | status |
|---|---|
| 1. replace the degenerate 0/1 output | **done and verified** — 90.9% exactly-0 → **0.0%** |
| 2. refit the tail shape, validate out of sample | **run, powered, FAILED its bar, and WORSE than theta4's actual incumbent** — §3.3, §3.7 |
| 3. re-measure calibration + residual selection bias | **done** — bias remains large; §3.8 |
| 4. selection-rule A/B that does not rank on model error | **redesigned**, below; still unregistered |
| 5. telemetry for momentum/regime | **implemented and tested**, below |

**Stage 2 now has a real answer, and the answer is no.** With a 90-day fit window, non-overlapping
blocks and declustered per-tail power, every quote is powered — so the model could finally be
judged, and it does not meet the stated bar. Removing the 0/1 degeneracy did not remove the tail
miss, and repairing the model did not reduce the selection bias. **Checkpoint item 3 is complete
in the sense that a powered out-of-sample calibration exists; it is not a success.**

---

## 1. Why the incumbent output was degenerate — the mechanism

`SpotModel.prob_from_returns` is the raw empirical frequency:

```python
sum(1 for r in rets if r > x) / n
```

An empirical distribution has **no mass beyond its own sample maximum**. Every strike further
out prices at exactly 0.0; every strike inside the minimum at exactly 1.0.

This also explains, mechanically, why `mult=2.0` did not repair it. `vol_mult` rescales the
*threshold* (`x / k`), so it pulls some strikes back inside the support — but where `x / k` still
exceeds `max(rets)` the answer is still exactly zero. Widening a distribution that ends at a hard
edge moves the edge; it does not remove it. Pinned in
`tests/test_theta_tailmodel.py::test_vol_mult_cannot_escape_the_truncation`:

```
old model, strike at 3x the sample max:  mult=1.0 -> 0.0    mult=2.0 -> 0.0
```

That is the whole of theta4's history in one line — a doubling that reduced the miss (R fell from
~4 on the base model's selected set to 1.72 on theta4's traded set) without being able to reach 1.

---

## 2. The replacement model — specification

`kalshi_bot/theta/tailmodel.py`. **Additive: no book prices off it.**

**Construction.** Empirical body below a high quantile; a fitted Generalized Pareto spliced onto
each tail above it. Pickands–Balkema–de Haan says excesses over a high threshold converge to a
GPD whatever the parent, so this is the standard way to extrapolate past the data instead of
asserting the data's edge is the world's edge.

| element | choice | why |
|---|---|---|
| sample | **non-overlapping h-minute blocks** | see §2.1 — this is the correction that mattered most |
| declustering | runs, separation 2 blocks | volatility clusters survive de-overlapping |
| body | empirical, unchanged | no reason to model what can be counted |
| tail | GPD, both directions, fitted **separately** | `greater` and `less` strikes use opposite tails |
| estimator | probability-weighted moments | closed form, no optimiser, no scipy, better than MLE at small counts |
| threshold | `tail_q`, swept and frozen on TRAIN | bias/variance: higher = less biased, more variable |
| fit window | `theta_spliced_fit_days`, **paper only** | see §2.2 |
| power test | **per tail, on declustered exceedances** | see §2.3 |
| floor | `1 / (2·blocks)` | degenerate cases only |
| bounded fits | extrapolate with `max(ξ, 0)` | a fitted ξ<0 asserts a hard maximum move |

### 2.1 Non-overlapping blocks, not an `n_eff` disclaimer

`SpotModel.returns` emits an h-minute return from **every minute**, so consecutive samples share
h−1 of their h minutes. At h=35 the overlap factor is exactly **35×** (verified:
7,165 overlapping returns against 205 blocks over the same window). One shock therefore enters an
overlapping fit as ~35 neighbouring extremes, and the exceedance count, the scale **and** the
shape are all estimated as though a single move were dozens of independent ones.

The previous revision fitted overlapping returns and reported an `n_eff = n/h` disclaimer beside
the result. That was not a correction — **a denominator in the metadata does not correct a fitted
shape.** The fit now consumes `SpotModel.block_returns` (step h, time-ordered), and
runs-declustering collapses exceedances within 2 blocks of each other into one cluster
represented by its maximum. `tests/test_theta_tailmodel.py::TestDeclustering` pins that a single
ten-block storm reads as **one** exceedance, not ten.

### 2.2 The fit window is separate from BOTH retention and the incumbent's window

The previous revision raised retention to 90 days and then still fitted on 5, because the shadow
called `model.returns(...)` on a `SpotModel` built with `theta_trail_days=5` and the harness
hard-coded `TRAIL_DAYS = 5.0`. **Every claim it made about what 90-day retention bought was
therefore false.** Three now-distinct settings:

| setting | value | who reads it |
|---|---|---|
| `theta_trail_days` | 5 | the **incumbent**, unchanged, still prices exactly as before |
| `theta_spot_retention_days` | 90 | the pruner only |
| `theta_spliced_fit_days` | 90 | the **paper** replacement model only |

Widening the fit window cannot touch a live decision: `_refresh_spot` still loads only
`trail_days` of closes for the incumbent, and no book prices off the replacement.

### 2.3 Power is per tail and depends on `tail_q`

The previous bar was a fixed block count, which is wrong twice over. 400 blocks give ~40
exceedances at `tail_q=0.90` and **~4** at `tail_q=0.99` — the old rule would have called the
second one powered. And a model whose upper tail is well evidenced and lower tail is not still
hands out an unsupported number on every `less` strike.

So: **≥20 declustered exceedances on the tail the strike actually prices off**. Every fit reports
blocks, upper and lower declustered exceedance counts, per-side fit/fallback status, and the
chosen window and threshold. `powered_for(strike_type)` is what callers ask.

### 2.4 Stated limitations, not buried

- PWM is consistent only for **ξ < 0.5**; against a heavier tail it *saturates* (true ξ=2 returns
  ~0.95). The bias runs **downward**, the dangerous direction for a seller, so an ξ near 0.5 must
  read as "at least this heavy", never as a point estimate.
- No SOL candle feed exists, so a SOL strike is priced off BTC returns.
- Runs-declustering with a fixed separation is a convention, not a fact about the market; the
  separation travels with every fit so a reader can see what was assumed.

## 3. Baseline calibration — powered, out of sample, and the model does not pass

Run **`refit-8`**: 30,960 decision quotes, 2026-08-01 → 08-21, TRAIN/TEST split at 08-14, spot
from Coinbase's public 1-minute feed. Cited in preference to the earlier `refit-5` because that
run's configuration was frozen by a selection statistic since found broken (§3.5) — it happened
to pick the same configuration, but a defensible number has to come from the run whose freezing
rule is defensible.

### 3.1 The window, not the estimator, was the constraint — and it is now lifted

Quotes whose **active** tail cleared the power bar, per configuration:

| fit window | tail_q | powered | coverage |
|---|---|---|---|
| 5 days | 0.90 | 1,767 | 5.0% |
| 5 days | 0.95 | 14 | 0.0% |
| 5 days | 0.99 | **0** | **0.0%** |
| 30 days | 0.90 / 0.95 | 30,960 | 100% |
| 30 days | 0.99 | 35 | 0.1% |
| **90 days** | 0.90 / 0.95 / 0.99 | **30,960** | **100%** |

This is blockers 1 and 2 in one table. At theta's own 5-day window a 99th-percentile fit is
powered **nowhere**, which is precisely the `tail_q` dependence a fixed block-count bar could not
express. At 30 days and beyond every quote is powered at the thresholds that matter.

Fit health at the frozen configuration: **1,440 non-overlapping blocks** (p50), **95 declustered
upper-tail exceedances** and **93 lower** (p50), upper ξ +0.061, lower ξ +0.094. A real fit, on
both sides, for the first time.

### 3.2 Degeneracy — fixed

| model | n | exactly 0 | in (0,1) |
|---|---|---|---|
| incumbent (empirical) | 30,960 | **28,100 (90.8%)** | 2,860 (9.2%) |
| spliced EVT | 30,960 | **0 (0.0%)** | **30,960 (100.0%)** |

### 3.3 Out-of-sample calibration — the replacement FAILS its acceptance bar

Configuration frozen on TRAIN (`fit_days=30, tail_q=0.90`, deviance/quote 0.00040), then TEST
read **once**: 10,722 powered quotes.

| modeled P | spliced R | 99% CI | incumbent R | 99% CI |
|---|---|---|---|---|
| 0.000–0.002 | **7.00** | [3.11, 13.41] | **21.34** | [7.93, 45.67] |
| 0.002–0.005 | 9.80 | [2.85, 23.98] | 2.41 | [0.12, 11.16] |
| 0.005–0.010 | 4.62 | [0.78, 14.56] | 3.71 | [0.80, 10.49] |
| 0.010–0.020 | 6.66 | [2.62, 13.79] | 3.09 | [1.15, 6.61] |
| 0.020–0.050 | 3.53 | [1.71, 6.40] | 2.40 | [1.03, 4.70] |
| 0.050–0.100 | 2.10 | [1.02, 3.80] | 2.27 | [1.15, 3.99] |
| 0.100–0.200 | 1.41 | [0.74, 2.41] | 1.69 | [0.96, 2.75] |
| 0.200–0.350 | 0.80 | [0.36, 1.54] | 1.04 | [0.55, 1.78] |
| 0.350–0.500 | 0.69 | [0.24, 1.53] | 0.75 | [0.22, 1.83] |
| **ALL** | **1.79** | [1.39, 2.27] | **1.71** | [1.32, 2.17] |

> **The stated bar was: "a model that looks calibrated near 10–50% and materially understates
> 1–5% events is not acceptable for a short-tail strategy." The spliced model is exactly that
> model.** Calibrated from 10% up (R = 1.41, 0.80, 0.69); understating the 1–5% region by **3.5×
> to 9.8×**. **It does not pass. Stage 2 is complete as a measurement and negative as a result.**

What it does buy is real but narrow: in the deepest bucket the incumbent misses by **21.3×** and
the spliced model by **7.0×** — because the incumbent assigns ~zero there and cannot even be wrong
by a ratio until it stops doing so. Overall the two are indistinguishable (1.79 vs 1.71).

**Removing the truncation did not remove the tail miss.** The degeneracy was a real defect and is
fixed; it was not the cause of R ≈ 4. Something else misprices the deep tail, and a GPD splice
over realized spot returns does not capture it.

### 3.4 Stage 3 — selection bias survives the repair, and grows

Measured on all 30,960 powered quotes:

| | SELECTED R | 99% CI | REJECTED R | 99% CI | ratio |
|---|---|---|---|---|---|
| incumbent | 5.44 | [3.04, 8.92] | 1.18 | [0.94, 1.46] | **4.6×** |
| spliced | 6.54 | [3.56, 10.94] | 1.08 | [0.86, 1.33] | **6.1×** |

**Repairing the model did not reduce the selection bias — it slightly increased it.** This is the
review's point 4 confirmed with data: a residual ranking finds whatever error remains, so a better
model does not stop it selecting on that error. It is the strongest available argument that the
selection RULE, not the probability model, is what has to change.

### 3.5 Two corrections to the statistic that freezes a configuration

Both were found by running the sweep, and each would have frozen the wrong thing:

1. **`|log(observed/expected)|` is undefined when nothing was observed.** All three 90-day
   candidates went unscored and a 30-day window was frozen by default — the statistic silently
   discarded exactly the configurations whose deep tail behaved best. Replaced by the Poisson
   deviance `2·(o·ln(o/e) − (o − e))`, defined at o = 0 (`= 2e`).
2. **Raw deviance rewards a configuration that powers almost nothing**, because it is an absolute
   quantity and less data means a smaller sum. Run `refit-7` froze `fit_days=5, tail_q=0.90` on a
   deviance of 0.778 while powering **1,766 of 30,923 quotes (5.7%)**. Scoring is now the **mean
   deviance per deep-tail quote**, and a configuration must power **≥90% of TRAIN quotes** to be
   a candidate at all. Under that rule `refit-8` rejects three configurations outright and freezes
   `fit_days=30, tail_q=0.90` on merit.

Both are pinned by tests, and the coverage gate is the reason the numbers above can be compared
across configurations at all.

### 3.7 Against theta4's ACTUAL incumbent (`mult=2.0`) the replacement is WORSE

`refit-8` scored the replacement against the **base** model (`vol_mult=1.0`). theta4 does not run
the base model — it runs `mult=2.0`. Run **`refit-t4b`** repeats the comparison against the book's
real incumbent. The two are reported separately and must not be pooled.

| overall R, historical validation | spliced | incumbent |
|---|---|---|
| vs **base** model (`mult=1.0`, `refit-8`) | 1.79 [1.39, 2.27] | 1.71 [1.32, 2.17] |
| vs **theta4's** model (`mult=2.0`, `refit-t4b`) | 1.79 [1.39, 2.27] | **0.96 [0.75, 1.22]** |

> **theta4's fattened model is essentially calibrated in aggregate (R = 0.96, interval spanning
> 1.0), and the replacement is not (R = 1.79, interval excluding 1.0).** Against the incumbent
> that actually trades, the spliced model is a regression, not an improvement.

That is the opposite of what the base-model comparison suggested, and it is the comparison that
matters. It also disposes of any remaining case for adopting the replacement as it stands.

**But aggregate R is not a sufficient statistic, and this is the clearest demonstration in the
whole programme.** The `mult=2.0` incumbent still prices **85.5%** of quotes at exactly zero.
Those quotes carry ~0 expected hits, so they cost almost nothing in an aggregate ratio — the
aggregate is dominated by the mid-range buckets, where fattening happens to calibrate well. Read
per bucket, the incumbent's deepest bucket is 2 observed against 0.42 expected on 8,869 quotes:
it is not calibrated there, it is *silent* there.

So the two models fail differently:

- the **incumbent** declares 85.5% of its universe impossible and is well calibrated on what
  remains;
- the **replacement** produces a probability everywhere and is 1.8× miscalibrated across the
  board, badly so in the 1–5% region.

Neither is acceptable for a short-tail seller, and picking between them on aggregate R would pick
the one that refuses to answer. This is why the frozen scoring rule is a **proper per-prediction
score** on a common population (§3.5) rather than a ratio of counts.

### 3.8 The selection comparison is NOT like-for-like, and is reported as such

| | SELECTED n | SELECTED R | REJECTED R |
|---|---|---|---|
| incumbent `mult=1.0` | 105 | 5.44 [3.04, 8.92] | 1.18 [0.94, 1.46] |
| incumbent `mult=2.0` | **15** | 2.85 [0.32, 10.43] | 0.67 [0.54, 0.82] |
| spliced | 117 | 6.79 [3.70, 11.37] | 1.10 [0.88, 1.36] |

Fattening shrinks `excess = mid − 100·P_model`, so `mult=2.0` selects **15** quotes where the
spliced model selects **117**. Those are different populations, and a ratio between them is not a
comparison.

An earlier revision claimed selection bias "grew from 4.6× to 6.1×". **Retracted** — that
compared arms drawn from different populations with no uncertainty on the ratio. The supported
statement is narrower and still decisive:

> **Residual selection bias remains large under the replacement probabilities** (SELECTED 6.79
> [3.70, 11.37] against REJECTED 1.10 [0.88, 1.36], intervals disjoint).

Whether it is *larger* than under the incumbent is not established by this evidence and is not
claimed.

---

### 3.6 August is historical validation, not a pristine holdout — and a forward holdout is reserved

Run `refit-7` exposed results from the August test period **before** the selection statistic and
the coverage rule were changed for `refit-8`. A window that informed the scoring design cannot
also certify it, however the split is drawn afterwards. So §3.3 is **historical validation**, and
it is labelled that way in the harness output too.

The negative finding stands regardless — it is a rejection, and seeing the window can only have
made the model look *better*, not worse. What the window can no longer do is certify a *positive*
result.

**Reserved:** the model specification, the scoring rule (mean Bernoulli log loss on the common
`mid ≤ 20¢` population), the coverage gate and the frozen configuration are now fixed. The next
period of ladder data — from the merge of this work forward — is a genuine one-look holdout, and
is not to be inspected until the specification has been unchanged across it.

---

## 4. Stage 4 — the selection-rule A/B (design; not registered, not running)

### 4.1 Why the earlier recommendation was withdrawn

The previous revision recommended **split-sample residual ranking**: fit on one half of the
trailing window, price on the other. That was wrong, and the objection is decisive — it still
ranks candidates by `mid − P_model`. Disjoint fitting samples do not give independent errors when
both halves share the same market quote, the same regime, the same model family and the same
structural miss. The dominant term in the residual is not sampling noise in the fit; it is the
tail-shape error §2 measured, and that error is common to both halves by construction. Splitting
the sample attacks the smallest component of the problem.

### 4.2 The replacement: rank on price, veto on model

**Selection must not be a function of model disagreement at all.**

| element | specification |
|---|---|
| **Candidate eligibility** | crypto ladder markets, `minutes_to_close` ∈ [10, 35], yes mid ∈ **[3, 20]¢**, `volume ≥ 100`, two-sided book. Identical in both arms — eligibility is not the treatment. |
| **Control selection score** | `excess = mid − 100·P_model`, descending. Today's rule, unchanged. |
| **Treatment selection score** | `mid` **ascending** — the cheapest eligible tail first. Exogenous: the market's own price, which the model does not produce. |
| **Treatment veto** | take only if `P_model ≤ 0.10`. The model may *refuse* a candidate; it may never *promote* one. A veto removes candidates rather than ordering them, so it cannot generate winner's curse. |
| **Tie handling** | equal `mid` broken by the deterministic ticker hash already used by `abarm` (`docs/MMSELL_OFFSET_AB.md`) — reproducible and identical across arms. |
| **Per-event cap** | `theta_max_per_event = 3`, both arms. |
| **Independent unit** | the **settled market**. |

### 4.2.1 Primary estimand — treatment versus control, not an arm in isolation

An earlier revision named "treatment-arm R ≤ 1.5" as the primary metric. **That is not an A/B
estimand at all** — it describes one arm and never compares them. Corrected:

> **Primary:** `log(R_T / R_C)`, with a two-sided 99% interval from the Poisson counts of both
> arms. Promotion requires the **upper** bound of `log(R_T / R_C)` to be **< 0**, i.e. the
> treatment's tail miss is smaller than the control's by more than sampling error.

`log` of the ratio rather than the raw ratio because the sampling distribution of a ratio of
counts is badly skewed and its normal interval misbehaves near zero; on the log scale the two
directions are symmetric, which is what a comparison needs.

### 4.2.2 Absolute safety clause — the bound direction, corrected

An earlier revision wrote "R ≤ 1.5 with the 99% lower bound below it". **That is backwards**: a
lower bound below 1.5 is satisfied by a model that misses by 10×. Establishing that a quantity is
SMALL requires bounding it from ABOVE.

> **Safety:** the one-sided 99% **UPPER** confidence bound on `R_T` must be **≤ 1.5**.

This is a `fail_any`-style clause, not a promotion clause: it can stop the arm on its own, and
satisfying it does not by itself promote anything. Both conditions must hold to promote.

### 4.2.3 Floors, from stage 2's measured variance

Stage 2 is powered, so these are derived rather than deferred. From `refit-8`, the control-arm
tail rate on the eligible population is **R_C ≈ 5.4** against an expected count of 4.60 over 105
selected markets — about **0.044 expected tail events per selected market**.

To resolve `log(R_T / R_C)` at the minimum useful effect (a halving, `log 0.5 = −0.69`) with 80%
power at a two-sided 99% interval, each arm needs roughly `2·(z_{.995}+z_{.80})² / (0.69²·λ)`
markets, with λ = 0.044 expected events per market:

| quantity | value |
|---|---|
| expected tail events per market (λ) | 0.044 |
| **promotion evidence floor** | **≈ 1,050 settled markets per arm** |
| **maximum evidence horizon** (inclusive, #247) | **1,600 settled markets per arm** |
| **early-failure floor** (`fail_any`) | **300 settled markets per arm** |

At theta4's observed cadence (~48 live orders over five days ≈ 10 markets/day/arm) the promotion
floor is ~105 days per arm. **That is the honest cost, and it is stated before the arm exists.**

### 4.2.4 Coverage — candidate stream, not twins

Calling two independently-selecting paper arms "twins" was wrong. A twin mirrors another book's
decisions; these arms *choose differently on purpose*, and a market taken by one and not the
other **is the treatment effect**, not missing data.

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

**Still unregistered** — but no longer for want of numbers. The floors in §4.2.3 are derived and
fixed; what is missing is the operator's decision to run it, and the forward holdout in §3.6 that
the model specification now needs. Registering it today would put a paper arm on a candidate
stream whose scoring rule has not yet faced an untouched window.

## 5. Stage 5 — telemetry (implemented and tested)

All additive. **No existing semantic changes**: no probability, entry, fill or gate reads any new
column, and `_refresh_spot` still loads exactly `trail_days` of closes for the incumbent.

### 5.1 Decision-time context

`crypto_ladder_snapshots`, migration `b1d5e9f3a7c2`:

| column | what it answers |
|---|---|
| `trailing_vol_15m/60m/240m` | realized vol at the decision, bps/minute — the regime the trade entered |
| `trailing_move_15m/60m` | **signed** trailing move, bps — a tail sold into a rally is not the same trade as one sold into a selloff, and the diagnosis could only bucket on \|move\| |

### 5.2 Replacement-model shadow, with tail-CORRECT metadata

The first draft stored `spliced_upper_xi` beside every probability. **That is the wrong tail for
a `less` strike**, which prices off the lower one. Corrected, and everything needed to interpret a
stored probability now travels with it:

`spliced_model_p`, `spliced_active_xi` (upper for `greater`, lower for `less`, NULL for
`between`), `spliced_upper_xi`, `spliced_lower_xi`, `spliced_active_clusters` (declustered
exceedances backing the **active** tail), `spliced_blocks`, `spliced_fit_days`, `spliced_tail_q`,
`spliced_underpowered`.

Without the fit settings, two probabilities produced under different windows or thresholds are
indistinguishable in storage; without `spliced_underpowered`, a resolution floor pools with an
estimate and is read as evidence.

### 5.3 Subsequent spot path — a tested research product, not a retention promise

Retention makes the decision→candle join possible. It does not make it complete, and a forward-path
feature set with silent gaps is worse than none: the missing markets cluster around feed outages,
so dropping them silently selects on the regime being studied.

`scripts/theta_forward_path.py` is the product and the proof. Per decision snapshot, deterministic
from retained 1-minute closes:

- forward log return at **+5m, +15m, +30m and market close**, in basis points;
- **maximum favourable and adverse excursion** over the hold, oriented by the side the book SOLD —
  for a sold `greater` strike a rising market is adverse, for a sold `less` strike it is
  favourable;
- **coverage per offset, reported before any economics**, with a stated ~95% usability bar.

Tests pin the orientation in both directions (a single sign error would invert MFE/MAE for half
the book), that a missing offset stays `None` rather than becoming a number, and that the
close offset follows `minutes_to_close`.

### 5.4 Backfill, so the window exists now rather than in three months

`crypto_spot_candles` was pruned at `trail_days + 1` = 6 days, and the first draft's "90 days of
retention" would have taken 90 days of wall-clock to mean anything. Probe `cb-probe-5`
(2026-08-21) measured Coinbase serving 1-minute candles **at least 365 days back** for both
products, so `_backfill_spot` extends the stored history *backward* toward the retention horizon
at `theta_spot_backfill_requests_per_cycle` requests per cycle (default 12), filling ~90 days over
a few hours instead of hammering a public endpoint in one pass. Best-effort and never fatal: a
failed backfill leaves the incumbent's own gap-filled 5-day window untouched.

## 6. A precision correction to the diagnosis

`RESEARCH_THETA_TAIL_MODEL_DIAGNOSIS.md` §2.2–§2.4 and §2.6 read
`crypto_ladder_snapshots.model_p`, which the tracker writes at `vol_mult = 1.0` — the **base**
model. §1's per-book table reads `paper_trades.model_probability`, which for theta4 carries
`mult=2.0`. The two are different objects and the doc should have said so.

No conclusion changes. The SELECTED-vs-REJECTED contrast is within one model either way, and the
z-monotonicity is a property of the base model that `mult=2.0` demonstrably failed to remove —
theta4's traded R of 1.72 against the base model's selected 4.03 is the size of what doubling
bought, and §1 of this document explains mechanically why it could not buy more.

---

## 7. The record, stated plainly

**Degeneracy was a real defect. Removing it improved the deepest probabilities. Historical
validation still rejects the replacement model, and does not establish degeneracy as the cause of
theta4's failure.**

Concretely, and without hedging in either direction:

- The incumbent priced **90.8%** of ladder quotes at exactly 0.0, which is not a probability. The
  spliced model prices none of them there. That is a genuine repair.
- In the deepest bucket the miss fell from **21.3× to 7.0×** — the incumbent could not even be
  wrong by a ratio there, because it assigned ~zero.
- **Overall the two are indistinguishable** (R 1.79 [1.39, 2.27] against 1.71 [1.32, 2.17]), and
  the replacement still understates 1–5% events by **3.5×–9.8×**, which is the failure mode the
  acceptance bar names as unacceptable for a short-tail book.
- **Residual selection bias remains large** under the replacement probabilities (SELECTED 6.79
  against REJECTED 1.10, intervals disjoint). Whether it is *larger* than under the incumbent is
  not established and is not claimed.
- Against theta4's **actual** `mult=2.0` incumbent the replacement is a **regression**: R 1.79
  (interval excluding 1.0) against 0.96 (interval spanning it). The incumbent achieves that while
  calling 85.5% of its universe impossible, so neither model is acceptable — but the replacement
  is not the better of the two.

An earlier revision of this section said "no refitted tail is claimed as validated", which was
true when written and became false once §3 reported a powered validation. Both statements cannot
stand: a refitted tail **was** fitted and **was** validated, and it **failed**. That is the claim.

### 7.1 What is still unproven

The cause of theta4's R ≈ 4 is **not identified**. Degeneracy is ruled out as a sufficient
explanation, and the deep-tail miss survives a coherent EVT refit over a 30–90 day window — so
whatever misprices theta's tail is not captured by an extreme-value model of realized spot
returns. The momentum/regime hypothesis remains untested (§5.1 supplies the telemetry; §5.3
supplies the forward path), and the selection-rule A/B in §4 tests a different mechanism again.

### 7.2 What was deliberately not done

`mult` was not increased — the evidence says the failure is not a scale error, and a second
doubling would repeat a settled mistake. No theta parameter changed at all; `SpotModel` is
untouched and every book prices exactly as before. No book re-armed, retired or created; no gate
written or re-interpreted; the 50-market early-failure floor not moved. No live restart. The
stage-4 design is a proposal with derived floors, not a registration. `SERIES_TYPES` is untouched
and the taxonomy repair is routed to Platform Change Review.
