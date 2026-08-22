# Research Lab — theta tail-model failure diagnosis

**Question.** The live canary measured a realized-tail-hit ratio of **R = 4.14** (11 observed
hits against 2.66 modeled, LCB99 = 1.79) over 38 settled markets. Which mechanism explains it?

**Scope.** Diagnosis only. **No parameter is proposed and nothing is changed.** The output is a
cause and the paper-only validation it implies.

**Evidence base.** theta4's **paper** history — 341 settled trades, 2026-07-11 → 2026-08-21 —
plus 111,242 ladder quotes in theta's entry window, traded or not. The mechanism question needs
no real money, and the paper book has ~9× the live canary's sample.

**Reproduce.** `{"type":"script","name":"theta_tail_diagnosis"}`
(`scripts/theta_tail_diagnosis.py`). All intervals are two-sided 99% Poisson, by inversion.

> **TWO CORRECTIONS, both established after this document was written. Read them before
> quoting any interval or any calibration verdict below.**
>
> 1. **Every interval here is too narrow, by about 2.5×.** A Poisson interval treats each
>    market as an independent observation, but a crypto ladder publishes ~66 markets and
>    settles all of them against ONE spot print. The measured design effect is 4–7
>    (`RESEARCH_THETA_REMEDIATION.md` §3.2). The **point estimates are unaffected** — R was
>    never wrong, the confidence in it was — so the mechanism findings stand and the
>    *significance* of any contrast below does not. Event-clustered replacements are in §3 of
>    the remediation document.
> 2. **The outcome label was a proxy, and the proxy fails its own bar.** §B reports
>    derived-vs-recorded settlement agreement of 96.9% against its own 97% bar and proceeds
>    anyway. The label is no longer derived at all: Kalshi's public market-data endpoint serves
>    a real settled `result` for **67,022 of 67,022 markets (100.00%)** of the scored universe,
>    and that is what everything downstream now scores against
>    (`scripts/theta_settlement_labels.py`). Measured against those real results the derivation
>    agrees 99.90% — but 54 of 1,000 events carry a disagreement, so its exact clustered 99%
>    lower bound is **92.70%**, below the 97% bar. The proxy was good and not good *enough*;
>    sections B/C/E/F/G, which rest on it, are advisory for that reason. The near-strike
>    exclusion an earlier revision proposed is retired with the proxy — with real labels there
>    is nothing to exclude.
> 3. **Disjoint marginal intervals are not a test of a contrast.** §2.4 below argues from two
>    separately-computed intervals failing to overlap. That is not the estimand: the estimand is
>    `log(R_selected / R_rejected)`, and it has to be resampled as one quantity so the two
>    groups' covariance is retained. Done directly and event-clustered
>    (`RESEARCH_THETA_REMEDIATION.md` §3.7), residual selection bias is **established for the
>    spliced model** (+1.486, 99% CI [+0.660, +1.982], 4.42×) and **suggestive only for the
>    incumbent** (+1.510, 99% CI [−0.764, +2.534] — spans zero). §2.4's *mechanism* survives;
>    its claim to be decisive does not.
>
> Kept as written. The corrections are the useful part of the record.

**What R is measured against.** `model_probability` as the book **actually used it** — theta4
runs `mult=2.0`, so its model tails are already doubled before anything is sold. Every R below
is a miss *on top of a doubling*. "Fatten further" is therefore a parameter change, not a
diagnosis, and this study exists to avoid making that mistake twice.

---

## 1. The headline: the live R is real but the live number overstates it

| sample | n | expected | observed | R | 99% CI |
|---|---|---|---|---|---|
| theta4 **live canary** | 38 mkts | 2.66 | 11 | **4.14** | [1.62, 8.56] |
| theta4 **paper** | 341 | 25.63 | 44 | **1.72** | [1.12, 2.50] |
| theta4_pt3 (Aug twin) | 103 | 6.74 | 18 | 2.67 | [1.33, 4.76] |

Both exclude 1.0, so **the miss is real and it is not a small-sample artifact**. But the paper
point estimate is **less than half** the live one, and the live sample is drawn almost entirely
from the 15–19 August crypto rally. The honest reading: a standing miss of roughly **1.7×**,
amplified to ~4× by the window the canary happened to occupy.

The whole family misses, and `mult=2.0` did not fix it:

| book | R | 99% CI | note |
|---|---|---|---|
| `theta` (control) | 1.43 | [1.12, 1.80] | base model |
| `theta1` | 1.89 | [1.01, 3.20] | band + window surgery |
| `theta2` | 2.67 | [1.19, 5.11] | thresholds only |
| `theta3` | 1.83 | [1.18, 2.69] | mult=1.25 |
| **`theta4`** | **1.72** | [1.12, 2.50] | **mult=2.0** |

theta4 doubled the modeled tails and landed at 1.72 against the control's 1.43. **A scale
multiplier did not move the miss** — the first hard evidence against the volatility-level
hypothesis.

---

## 2. Six mechanisms, tested

### 2.1 Stale underlying price — **REFUTED**

The model's own `spot` against the independent Coinbase candle feed at the same minute,
324,333 matched minutes:

| offset | mean abs difference |
|---|---|
| −2 min | 35.81 |
| −1 min | 27.71 |
| **0 min** | **5.38** |
| +1 min | 12.25 |
| +2 min | 25.44 |
| +5 min | 46.11 |

Zero lag fits best by a factor of >2 against every alternative. Bias −0.92 on a 65,243 level
(**−0.14 bps**), mean |difference| **0.82 bps**. The price the model reads is current.

### 2.2 Wrong volatility level (a scale error) — **REFUTED**

R by Gaussian-equivalent standardized strike distance, z = Φ⁻¹(1 − model_p). A pure scale error
is **flat** in z; a shape error **rises**:

| z | n | expected | observed | R | 99% CI |
|---|---|---|---|---|---|
| 0.0–0.5 | 3,446 | 3,090.35 | 3,079 | 1.00 | — |
| 0.5–1.0 | 315 | 70.82 | 69 | 0.97 | [0.70, 1.32] |
| 1.0–1.5 | 410 | 43.58 | 54 | 1.24 | [0.85, 1.74] |
| 1.5–2.0 | 620 | 25.15 | 40 | 1.59 | [1.02, 2.36] |
| 2.0–2.5 | 975 | 12.45 | 26 | 2.09 | [1.18, 3.39] |
| **2.5+** | 1,908 | 3.49 | 16 | **4.58** | [2.17, 8.44] |

**Monotone, and the body is exactly calibrated.** R = 1.00 where the model is near the money and
4.58 where it is furthest out. No single multiplier reproduces that curve — raising vol enough
to fix the 2.5+ bucket would push the body to R ≈ 0.4.

### 2.3 Model probability calibration error (tail **shape**) — **SUPPORTED**

The same monotonicity appears in probability space, over every quote in the window:

| modeled P | n | expected | observed | R | 99% CI |
|---|---|---|---|---|---|
| 0.00–0.02 | 62,513 | 13.74 | 38 | **2.77** | [1.75, 4.15] |
| 0.02–0.05 | 559 | 17.94 | 32 | 1.78 | [1.08, 2.77] |
| 0.05–0.10 | 345 | 24.28 | 34 | 1.40 | [0.86, 2.15] |
| 0.10–0.20 | 341 | 48.55 | 49 | 1.01 | [0.68, 1.44] |
| 0.20–0.35 | 253 | 67.37 | 69 | 1.02 | [0.73, 1.39] |
| 0.35–0.50 | 184 | 77.82 | 61 | 0.78 | [0.55, 1.08] |

Calibrated from 10% up; increasingly wrong below it. **The deficiency is in the tail shape, not
in the level.**

A structural finding underneath it: **the model reports exactly 0 for 53.7% of ladder quotes and
exactly 1 for 39.4%** — 93.1% of its output is a hard 0 or 1, not a probability. The
0.00–0.02 bucket has a mean modeled probability of 0.00022 and hits at 0.00061. In absolute
terms both are tiny; as a ratio it is 2.8×, and a strike the model calls impossible is a strike
the edge filter will always find attractive.

### 2.4 Threshold-selection bias — **SUPPORTED as a mechanism; see correction 3**

> The mechanism is real and it is the largest single factor found here. What this section
> cannot do is *establish* it: the argument below is two marginal intervals failing to
> overlap, which is not a test of their ratio. The direct, event-clustered test of
> `log(R_selected / R_rejected)` lives in `RESEARCH_THETA_REMEDIATION.md` §3.7. Under it the
> effect is established for the spliced model and **suggestive only for this one**.

Same model, same window, same markets — split only by whether theta4's entry
filter (excess ≥ 6¢, yes 3–20¢, volume ≥ 100) would have fired:

| modeled P | **SELECTED** R | 99% CI | **REJECTED** R | 99% CI |
|---|---|---|---|---|
| 0.00–0.02 | **14.97** | [3.83, 39.06] | 2.40 | [1.45, 3.72] |
| 0.02–0.05 | **5.80** | [2.28, 12.02] | 1.31 | [0.69, 2.24] |
| 0.05–0.10 | 2.36 | [0.69, 5.78] | 1.27 | [0.73, 2.04] |
| 0.10–0.20 | 0.00 (n=6) | [0.00, 7.55] | 1.02 | [0.69, 1.46] |
| **pooled** | **4.03** (n=142) | **[2.22, 6.67]** | 1.00 (n=111,100) | — |

In the 0.02–0.05 bucket the intervals do not overlap: [2.28, 12.02] against [0.69, 2.24]. An
earlier revision called that disjointness decisive. It is not: non-overlap is not necessary for
the ratio to exclude 1, and it is only reliably sufficient when the two estimates are independent
— which these are not, since the split partitions the same ladders. These intervals are also
unclustered (correction 1). Read the table as what it is: a point-estimate gap of
**4–5×** within one probability bucket, tested properly elsewhere.

This is winner's curse, and it is structural rather than incidental. `excess = mid − 100·P_model`
is large exactly when the model is most wrong *in the direction that makes selling look
attractive*. Ranking on that quantity ranks on model error. **No re-fit of the model removes
it** — a better model shrinks the error but the filter still selects its residual right tail.

### 2.5 Unmodeled momentum / regime dependence — **NOT IDENTIFIED**

R by trailing 30-minute |move| at entry, restricted to the tail population (modeled P < 0.20):

| trailing move | n | expected | observed | R | 99% CI |
|---|---|---|---|---|---|
| 0.00–0.10% | 5,137 | 7.45 | 7 | 0.94 | [0.27, 2.30] |
| 0.10–0.25% | 4,131 | 5.98 | 16 | 2.68 | [1.27, 4.93] |
| 0.25–0.50% | 1,478 | 1.75 | 3 | 1.72 | [0.19, 6.28] |
| 0.50–1.00% | 563 | 0.77 | 0 | 0.00 | [0.00, 6.85] |
| >1.00% | 126 | 0.22 | 0 | 0.00 | [0.00, 24.58] |

Not monotone, and the high-momentum buckets carry expected counts below 1 — they cannot reject
anything. Only 11,435 of 63,758 tail quotes have a usable 30-minute lookback. **This test is
under-powered and returns no verdict**, which is a data limitation, not evidence of absence. It
matters because the live canary's window was a momentum regime; §1's live-vs-paper gap is
consistent with a regime effect this design cannot yet measure.

### 2.6 Time to expiry — **WEAK, DIRECTIONAL**

| minutes to close | n | expected | observed | R | 99% CI |
|---|---|---|---|---|---|
| 25–30 | 6,651 | 9.98 | 10 | 1.00 | [0.37, 2.15] |
| 30–35 | 57,107 | 94.54 | 143 | 1.51 | [1.21, 1.87] |

Entries further from expiry miss more, which is the expected sign — more time is more chance for
the tail to be reached. The 25–30 cell is thin and the population is concentrated at 30–35 by
the sampling rule (one quote per market, earliest in window), so this is a hint, not a result.

---

## 3. Diagnosis

**Two mechanisms compose; neither alone produces R ≈ 4.**

1. **A tail-shape error in the model** — calibrated at the money, understating by ~1.3–2.8× in
   the deep tail, worsening monotonically as the strike moves out. Independent of the entry
   filter, so it is a genuine model defect. `mult=2.0` cannot address it because the defect is
   in the shape of the distribution, not its scale.
2. **Threshold-selection bias on top** — worth a further ~2–5× within the same probability
   bucket. The edge filter ranks candidates by model error, so the traded set is drawn from the
   right tail of that error. This is a property of *selecting on a noisy score*, not of the
   score's average quality.

Multiplicatively: a standing ~1.7× on the traded population, pushed to ~4× when the window is
also adverse.

Refuted along the way: stale price (2.1), volatility level (2.2). Unresolved: momentum/regime
(2.5) — the one hypothesis the current telemetry cannot test.

**Consequence for the strategy.** theta's thesis is that a fair-value anchor lets it sell only
genuinely overpriced tails. The anchor is calibrated where it is not needed (near the money) and
wrong where the entire strategy operates. **A book whose selection rule is its miscalibration
detector cannot be repaired by recalibrating alone** — both the model and the selection rule
have to change, and the second is the harder problem.

---

## 4. Proposed paper-only validation plan

Ordered; each stage gates the next. **All paper. No live canary is justified until stage 4
passes**, and nothing here is a registration.

**Stage 1 — make the model produce a probability.** 93.1% of the model's ladder output is
exactly 0 or exactly 1. Establish a floor (the empirical frequency at which "impossible" strikes
actually resolve) and re-measure §2.3. *Passes when* no bucket's modeled probability is a hard
0/1 and the 0.00–0.02 bucket's R falls inside [0.7, 1.4].

**Stage 2 — re-fit the tail shape, not the scale.** Replace the multiplier with a fitted tail
(the study's z-curve is the target: R ≈ 1 across every z bucket, not just in aggregate).
Validate **out of sample** — fit on 2026-07-11 → 08-10, test on 08-11 → 08-21. *Passes when*
out-of-sample R ∈ [0.7, 1.4] in every z bucket with expected ≥ 5.

**Stage 3 — quantify the residual selection bias directly.** Re-run §2.4 against the refitted
model. The SELECTED-vs-REJECTED ratio *will not reach 1*; the question is what it settles at.
*Passes when* the SELECTED/REJECTED R ratio is ≤ 1.5 and its 99% interval excludes 3.0.

**Stage 4 — a selection rule that does not rank on model error.** Stage 3 bounds the bias but
does not remove it; a shrunk or purpose-penalized entry score does. Paper-trade the refitted
model with the revised rule beside the current one on the same candidate stream, as a
pre-registered paper A/B. *Passes when* the new arm's R ∈ [0.7, 1.4] **and** its cents/contract
is positive over ≥ 300 settled markets.

**Stage 5 — the momentum question, which needs new telemetry.** §2.5 cannot be answered from
what is persisted. Record, per candidate at decision time: trailing realized vol over several
horizons, the spot path since the window opened, and the model's own vol input. Without it,
"unmodeled regime dependence" stays untestable, and it is the mechanism most likely to explain
the live-vs-paper gap in §1.

**The 50-market historical v2 early-failure floor is unchanged**, and none of this reinterprets
it. It stands as pre-registered.

---

## 5. Limitations

1. **Derived-vs-recorded settlement agreement is 96.9% (63 of 65)**, below the 97% bar the
   script sets, so §2.2–§2.6 are formally advisory. Two things bound the damage: the derivation
   is applied identically to SELECTED and REJECTED, so a symmetric error cannot manufacture a
   4–5× ratio between them; and the REJECTED deep-tail arm observes 32 hits in 62,472 quotes —
   a uniform 3% misclassification rate would have produced ~1,900. The real error rate on
   far-from-strike markets is therefore far below 3%, and the 65-market validation sample is
   concentrated in the near-strike markets where the derivation is hardest.
2. **The settlement proxy is the last observed ladder spot**, not Kalshi's settlement print.
3. **One quote per market** (the earliest in the entry window) approximates a rule theta
   re-evaluates every cycle, so SELECTED (n=142) is smaller than the 341 paper trades.
4. **`crypto_spot_candles` covers only 2026-08-15 → 08-21**, so the independent-feed check in
   §2.1 spans 6 days; everywhere else the spot series is the model's own.
5. **`theta_min_volume ≥ 100` is itself a selection mechanism** — a strike that trades is a
   strike someone disagrees about. It is applied in §2.4 (154 → 142 quotes) but not separated
   from the edge filter's contribution.
6. **No causal test of fill selection.** The randomized quote-and-decline experiment remains the
   clean instrument and is deliberately not run.

## 6. What was deliberately not done

No theta parameter was changed or proposed — in particular no new `mult`. No book was re-armed,
retired or created. No gate was written or re-interpreted, and the 50-market floor was not
moved. No live restart. The plan in §4 is a proposal for the operator to accept or reject.
