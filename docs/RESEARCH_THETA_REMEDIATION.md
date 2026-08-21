# Research Lab — theta remediation, stages 1–5

Follow-on to `RESEARCH_THETA_TAIL_MODEL_DIAGNOSIS.md`, which found two composing mechanisms: a
tail-shape error in the model and a threshold-selection bias on top. This is the repair
programme. **Paper only. No theta parameter changed, no book re-armed, no live money.**

**Reproduce:** `{"type":"script","name":"theta_tail_refit","args":["--since","2026-08-15",
"--train-end","2026-08-19","--spot-source","candles"]}` (run id `refit-3`).

---

## 0. The headline, before the detail

| stage | status |
|---|---|
| 1. replace the degenerate 0/1 output | **done and verified** — 80.3% exactly-0 → **0.0%** |
| 2. refit the tail shape, validate out of sample | **blocked on data, not on modelling** |
| 3. re-measure calibration + residual selection bias | **blocked by the same limit** |
| 4. selection-rule A/B that does not rank on model error | **designed, below** |
| 5. telemetry for momentum/regime | **implemented, below** |

The blocker is the finding. **theta's 5-day spot window cannot support a tail estimate at all** —
not the incumbent's, not a GPD's, not any. That is why stage 5 turns out to be a *prerequisite*
for stage 2 rather than a task beside it.

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
| body | empirical, unchanged | there is no reason to model what can be counted |
| tail | GPD, both directions | `greater` and `less` strikes need opposite tails |
| estimator | probability-weighted moments | closed form, no optimiser, no scipy, better than MLE at ~20 excesses |
| threshold | `tail_q`, default 0.90, swept | bias/variance: higher = less biased, more variable |
| floor | `1 / (2 · n_eff)` | degenerate cases only — see below |
| bounded fits | extrapolate with `max(ξ, 0)` | a fitted ξ<0 asserts a hard maximum move |

**Two design decisions that took a failed attempt each, and are pinned by regression tests:**

1. **The floor must not be a blanket clamp.** A first draft floored every probability. Past the
   sample maximum the GPD's estimate is far below one resolution step, so it returned the *same*
   number for a strike 1×, 1.5× and 2× beyond the data — shape destroyed, deep tail overstated
   ~40×. The floor now catches only non-finite and non-positive results.
2. **A bounded fit must still extrapolate.** ξ<0 gives a finite endpoint past which the GPD says
   exactly zero — reinstating "impossible", merely relocated from the sample maximum to a fitted
   endpoint. **49.4% of production fits are bounded**, so this was not hypothetical.
   Extrapolation therefore uses `max(ξ, 0)`: the exponential is the least-committal unbounded
   tail. ξ is kept unmodified for reporting, so a bounded fit stays visible.

**Stated limitations, not buried:**

- PWM is consistent only for **ξ < 0.5**. Against a heavier tail it *saturates* — a Pareto with
  true ξ=2 returns ~0.95. The bias runs **downward**, the dangerous direction for a seller, so an
  ξ near 0.5 must read as "at least this heavy", never as a point estimate.
- `n_eff = n / h`. Overlapping h-minute returns drawn every minute are ~97% shared at h=35; the
  raw count is not evidence.
- No SOL candle feed exists, so a SOL strike is priced off BTC returns.

---

## 3. Baseline calibration of the replacement model

### 3.1 Degeneracy — fixed

| model | n | exactly 0 | exactly 1 | in (0,1) |
|---|---|---|---|---|
| incumbent (empirical) | 7,209 | **5,792 (80.3%)** | 0 | 1,417 (19.7%) |
| spliced EVT | 7,209 | **0 (0.0%)** | **0 (0.0%)** | **7,209 (100.0%)** |

Stage 1 passes on its own terms: every output is now a probability.

### 3.2 Calibration — NOT estimable, and here is the proof

Of 7,209 scored quotes, **5 clear the independent-observation bar**. The other 7,204 are fitted on
too little to mean anything, and the model says so (`SplicedReturnModel.underpowered`) rather than
returning a floor dressed as an estimate.

Fit health over the production window:

| | p10 | p50 | p90 |
|---|---|---|---|
| `n_eff` (independent blocks) | 34 | **116** | 208 |
| upper ξ | −0.451 | +0.045 | +0.429 |

The bar is 400 — roughly 20 independent excesses at the 95th percentile. **Even the best fits sit
at half of it.** The arithmetic: 5 days = 7,200 minutes; at h=35 that is 7,200/35 ≈ **205
independent blocks**, of which ~10 are tail excesses. No estimator recovers a tail shape from ten
observations.

The `tail_q` sweep confirms it is sample size and not threshold choice:

| tail_q | train R | test R | \|log R\| deep, train | \|log R\| deep, test |
|---|---|---|---|---|
| 0.90 | 0.47 | 2.12 | 2.493 | 1.410 |
| 0.95 | 0.48 | 2.12 | 2.489 | 1.381 |
| 0.99 | 0.48 | 2.09 | 2.470 | 1.316 |

Two readings, both damning for a premature refit:

- **R swings 4.4× between adjacent 4-day windows** (0.48 → 2.12). That is the sampling noise of
  ~10 tail observations, not a property of the model.
- **`tail_q` barely moves the deep-tail miss** (2.47–2.49 train). The threshold is not the lever.

> **Reporting a "refitted and validated" tail from this window would be fitting noise.** The
> honest stage-2 result is that the estimate does not exist yet.

### 3.3 Stage 3 — blocked by the same limit

With 5 well-powered quotes, the SELECTED arm is empty (n=0), so the residual selection bias under
refitted probabilities cannot be measured. The diagnosis's finding stands unrevised: on the
incumbent model, SELECTED R = 4.03 [2.22, 6.67] against REJECTED 1.00.

### 3.4 What unblocks stages 2 and 3

Retention. `crypto_spot_candles` was pruned at `trail_days + 1` = 6 days, which is why the
diagnosis could not reconstruct a spot path and why nothing here can be fitted.

| retained history | independent blocks at h=35 | tail excesses at q=0.95 | verdict |
|---|---|---|---|
| 6 days (before) | ~205 | ~10 | not estimable |
| 10 days | ~410 | ~20 | bare minimum |
| **90 days (now)** | **~3,700** | **~185** | a real fit |

Storage cost: ~1,440 rows/day/product, ~260k rows for two products. **The pruner, not the
estimator, was the binding constraint.**

---

## 4. Stage 4 — the selection-rule A/B (design; not registered, not running)

The diagnosis showed `excess = mid − 100·P_model` ranks candidates by **model error**: it is
large exactly where the model is most wrong in the direction that makes selling look attractive.
Selecting on it selects the right tail of that error. A better model shrinks the error; it does
not stop the ranking from finding whatever error remains.

**Design.** Two paper arms on the same candidate stream, identical in every other respect:

| arm | selection score |
|---|---|
| control | `excess = mid − 100·P_model` (today's rule) |
| treatment | a score that is not the model-error residual |

Three treatment candidates, to be chosen **before** the data exists:

1. **Shrunk excess.** `excess − λ·SE(P_model)`, penalising candidates whose probability is itself
   uncertain. Requires a usable SE, which is stage 2's output — so this arm cannot be specified
   until stage 2 completes.
2. **Rank on price, not disagreement.** Sell the cheap tail on a fixed band and let the model act
   only as a *veto* (`P_model ≤ ceiling`), never as the ranker. Selection then depends on the
   market's price, and the model can no longer pick its own errors.
3. **Split-sample.** Rank on a model fitted to one half of the trailing window; price and gate on
   a model fitted to the disjoint half. The ranking error and the pricing error become
   independent, which is the textbook remedy for winner's curse.

**Recommendation: candidate 3.** It is the only one that attacks the mechanism directly rather
than damping its symptom, it needs no SE estimate, and it is measurable — the SELECTED/REJECTED
ratio should collapse toward 1 if selection was the cause.

**Pre-registration, to be fixed before the first trade.** Primary metric: the SELECTED/REJECTED R
ratio. Minimum useful effect: a fall from ~4.0 to ≤1.5. Independent unit: the settled market.
Evidence floor and horizon: to be set from stage 2's measured variance, using the standing 99%
sequential bounds and the inclusive maximum-evidence horizon from #245/#247.

**This arm cannot be registered yet**, because both its treatment definition and its floor depend
on numbers stage 2 does not yet have.

---

## 5. Stage 5 — telemetry (implemented)

All additive. **No existing semantic changes**: no probability, entry, fill or gate reads any new
column, and `refresh_spot_model` still loads exactly `trail_days` of closes.

**`crypto_ladder_snapshots`** (migration `b1d5e9f3a7c2`, eight nullable columns):

| column | what it answers |
|---|---|
| `trailing_vol_15m/60m/240m` | realized vol at the decision, bps/minute — the regime the trade was placed into |
| `trailing_move_15m/60m` | **signed** trailing move, bps — a tail sold into a rally is not the same trade as one sold into a selloff, and the diagnosis could only bucket on \|move\| |
| `spliced_model_p` | the replacement model's answer for the same strike at the same instant |
| `spliced_upper_xi`, `spliced_n_eff` | so a floor-dominated probability is never pooled with an estimated one |

**`theta_spot_retention_days = 90.0`** — retention decoupled from the model window, per §3.4.

Why this is the right shape: the diagnosis could not test momentum because only 11,435 of 63,758
tail quotes had a usable trailing move and the high-momentum buckets carried expected counts below
1. Recording the context at decision time makes it a measurement instead of a reconstruction.
The shadow probability means the replacement model's calibration accrues on live data from the
day this ships, while deciding nothing.

---

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

## 7. What was deliberately not done

`mult` was not increased — the evidence says the failure is shape, not level. No theta parameter
changed at all; `SpotModel` is untouched and every book prices exactly as before. No refitted tail
is claimed as validated, because it is not. No book re-armed, retired or created; no gate written
or re-interpreted; the 50-market early-failure floor was not moved. No live restart. The stage-4
design is a proposal, not a registration.
