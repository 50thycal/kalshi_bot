# MARKTANGLE-2 — Conditional Dependence Alpha (pre-registration)

**Experiment (canonical):** `marktangle-2-conditional-dependence` · v1 frozen at
registration · stage **PROBE** · predecessor `marktangle-conditional-reversion`
(MARKTANGLE-1). Experiment OS is the source of truth for its standing, arms,
gates and verdicts. Nothing in this document is a status.

**Experiment class:** historical strategy discovery / falsification
**Trading:** historical/paper only. No live authorization.
**Package:** `kalshi_bot/experiment_os/marktangle2.py`
**Instrument:** `scripts/marktangle2_probe.py` (ops: `{"type":"script","name":"marktangle2_probe","id":"m2-run-1"}`)
**Package splitter:** `scripts/marktangle2_package.py`
**Workstream:** `docs/workstreams/WS-013-marktangle-2-conditional-dependence.md`

**Primary question.** Can serial dependence in recurring Kalshi markets produce a
repeatable, tradeable edge after fees and realistic execution assumptions?

Part I is the operator's preregistration as received (2026-09-02), lightly
formatted. Part II freezes every implementation choice the preregistration left
open, **before any data is pulled**. Two runs against the same frozen data and
configuration must produce identical trade and result fingerprints (§21).

---

# Part I — Preregistration

## 1. Origin

MARKTANGLE-1 tested whether recurring Kalshi markets that resolve near 50/50
exhibit increasing reversal probability after runs of consecutive same-side
outcomes. It remains **HOLD** because no eligible exact family reached the
preregistered 100-entry holdout minimum.

The study produced two observations that justify a separate experiment rather
than a modification of MARKTANGLE-1:

1. Several potentially suitable sports/weather families are too thin
   individually to test but may contain common market-type effects that can be
   studied using preregistered hierarchical/panel pooling.
2. Daily crypto threshold families showed strong **persistence**, not reversion.
   A family can resolve nearly 50/50 marginally while being extremely
   non-independent sequentially.

Representative observation, `KXETHD|T2464.99`: n = 83, YES rate 49.4%,
P(Y|Y) = 97.5%, P(N|N) = 97.6%. Similar persistence was independently observed
in BTC and SOL threshold families.

MARKTANGLE-2 therefore asks a broader but more precise question: **can
empirically demonstrated serial dependence — reversion OR continuation — predict
the next resolution better than Kalshi's tradable price?** Previous losses must
never determine sizing.

## 2. Non-negotiable principles

- **2.1 No gambler's-fallacy assumption.** A streak by itself is not evidence
  of reversal. Conditional probabilities are estimated directly from history.
- **2.2 No Martingale.** Position size never increases to recover prior losses.
  Sizing may depend only on estimated edge, model confidence,
  liquidity/execution constraints and bankroll/risk constraints.
- **2.3 Prediction is not sufficient.** The target is economic profitability.
  Every arm must beat the actual obtainable Kalshi price after fees, spread,
  modeled slippage and execution eligibility.
- **2.4 MARKTANGLE-1 remains frozen.** Its 100-entry holdout floor, universe,
  preregistration and HOLD are untouched. MARKTANGLE-2 is a separate experiment.

## 3. Research architecture

Two independent research tracks. **Neither may rescue the other.**

### Track A — cross-family conditional reversion

Do homogeneous recurring-market classes exhibit a shared streak-dependent
reversal effect too weak to measure at the individual-family level? Candidate
classes: sports totals, weather buckets/ranges, other recurring fresh-event
families surfaced by the universe scan. **Daily fixed crypto price thresholds are
explicitly excluded from Track A.**

- **A1.** For at least one preregistered homogeneous class,
  `P(reversal | streak length k)` changes systematically with k after
  controlling for family-level characteristics.
- **A2 (economic).** The model-estimated probability of the reversing side
  exceeds the contemporaneous Kalshi-implied probability by enough to produce
  positive net EV after fees/execution costs.

Direction stays explicit: `P(NO next | k consecutive YES)` and
`P(YES next | k consecutive NO)` are estimated separately, never combined.

## 4. Track A — data construction

- **4.1 Family identity.** `SERIES | STRIKE / RUNG / CONTRACT DEFINITION`.
  Family identity stays observable in the model even when pooled. No blind
  concatenation of outcomes.
- **4.2 Pooling eligibility.** Families pool only when they share a
  preregistered economic/event structure (game totals of equivalent
  construction; repeated temperature buckets; structurally identical recurring
  contracts). Invalid: weather + sports; crypto thresholds + sports totals; any
  grouping by similar historical behaviour. Pooling is decided from market
  structure **before** inspecting conditional-streak returns.
- **4.3 Minimum base history.** ≥ 40 settled resolutions per exact family.

## 5. Track A — normalization and controls

Near-50/50 resolution is no longer the inclusion criterion. Each family's
unconditional probability `p_family` is estimated and conditional behaviour is
measured relative to it. Controls at minimum: exact family, market type,
unconditional family YES rate, calendar/time ordering, streak direction, streak
length. Season, entity, geography, strike location and regime are controls, not
post-hoc strategy selectors.

## 6. Track A — modeling

- **A-SIMPLE.** Per class: observations, YES rate, transition matrix,
  P(reversal | k YES), P(reversal | k NO), confidence interval, average entry
  price, net expected edge, realized net P&L, for k = 1, 2, 3, … up to the
  largest supportable streak length. No extrapolation beyond observed support.
- **A-HIERARCHICAL.** A hierarchical logistic (or documented equivalent) with
  streak direction, streak length, family baseline, family effect, class effect
  and a preregistered direction × length interaction. Primary question: after
  controlling for family effects, does streak length contain reproducible
  predictive information?

## 7. Track B — crypto threshold persistence

Daily fixed-price threshold markets are repeated observations of a slow-moving
underlying relative to a fixed threshold — a different stochastic process from
fresh Bernoulli trials.

- **B1 (persistence).** `P(same resolution tomorrow | same resolution today)` is
  materially greater than the unconditional continuation probability; state
  duration may further affect it.
- **B2 (underreaction — the trading hypothesis).** Kalshi prices do not always
  fully incorporate this persistence: `model P(continuation) > market-implied
  probability + costs` often enough to support a profitable strategy. B1 alone
  is not sufficient.

## 8. Track B — state-duration model

Per family: unconditional YES rate, P(Y|Y), P(N|N), duration of the current
state, continuation and reversal probability conditional on duration
(`P(Y next | Y for k settlements)`, `P(N next | N for k settlements)`). Do not
assume persistence is monotone in k: initial persistence may be very high while
the reversal hazard rises as the regime ages. Both are tested.

## 9. Track B — distance to threshold

At every decision point compute a normalized distance to the strike — percent
distance `(spot − strike) / strike` and/or volatility-normalized distance —
without lookahead. The core state hypothesis: continuation probability depends
**jointly** on state duration and distance from the threshold. A YES state 8%
above the strike is fundamentally different from one 0.1% above it.

## 10. Track B — candidate model

`P(next = current state | state duration, threshold distance, direction)`, with
optional controls (realized volatility, asset, time to settlement, prior-day
move, day-of-week) where historically available. No arbitrary technical
indicators: this tests the state-duration mechanism, not a feature search.

## 11. Train / validation / holdout

Chronological splits only. 60/20/20 or 70/30, frozen before fitting. Training
fits and discovers supported streak lengths; validation (if used) selects among
preregistered candidates; holdout is untouched until the rule is frozen. After
holdout inspection nothing changes: no k thresholds, distance bands, classes,
minimum edge or sizing rule.

## 12. Trading simulation

Every prediction becomes a trade decision at the actual price available at the
decision timestamp. `edge = model probability − executable market probability`;
trade only when `net edge ≥ minimum edge` (≥ 3 percentage points after fees,
consistent with MARKTANGLE-1). Taker simulation is primary (fee, ask/bid,
slippage/liquidity screen). Maker simulation is exploratory only and can never
produce a PASS if taker economics fail.

## 13. Position sizing

Primary test: fixed 1 unit per qualifying trade. Secondary study may use capped
fractional Kelly or capped edge-proportional sizing. Prohibited: Martingale,
loss recovery, doubling after losses, any sizing based on drawdown or the
previous outcome. A losing previous trade has zero direct effect on the next
trade's size.

## 14. Preregistered arms

| arm | track | rule |
|---|---|---|
| **A0** | A | independence baseline: family unconditional probability only |
| **A1** | A | one-step transition: `P(next \| previous resolution)` |
| **A2** | A | streak-length reversion, direction-specific by k |
| **A3** | A | hierarchical reversion with family-level controls |
| **B1** | B | crypto one-step persistence |
| **B2** | B | crypto state duration |
| **B3** | B | crypto state + duration + normalized threshold distance (primary) |
| **CONTROL** | both | mirror: the exact opposite directional signal under identical eligibility and execution |

## 15. Primary metrics

Statistical: N predictions, N trades, accuracy, Brier, conditional estimates
with CIs, family/class dispersion. Economic: gross P&L, fees, slippage, net
P&L, return on risk, average edge at entry, realized edge, max drawdown, longest
losing streak, profit factor, EV/trade. Robustness: train vs holdout,
per-family, leave-one-family-out where sample permits, YES/NO decomposition.

## 16. Sample floors

- Track A pooled class: ≥ 500 eligible prediction points in train and ≥ 100
  holdout trade opportunities for a treatment to receive PASS/FAIL.
- Track B: ≥ 500 eligible prediction points across qualifying crypto families
  and ≥ 100 holdout trade opportunities.
- Below floor: **HOLD — insufficient data. Do not interpret.** Individual
  families may be reported descriptively below these floors but cannot drive the
  primary verdict.

## 17. Profitability bar

PASS only if the untouched holdout shows **all** of: (1) positive net P&L after
modeled costs; (2) positive EV per trade; (3) the minimum trade count; (4) the
treatment beats its independence/base-rate comparator; (5) it beats or
materially separates from its mirror control; (6) profitability is not produced
entirely by one family unless that family is separately validated; (7) no
single trade or tiny subset accounts for the majority of profits. Robustness:
remove the highest-profit family and rerun; a pooled strategy stays positive.

## 18. Verdict rules

- **PASS** — every preregistered economic, sample-size and control requirement
  clears on untouched holdout data. Candidate alpha; **does not authorize live
  trading**. A separate prospective paper/twin/canary process follows.
- **FAIL** — adequately powered holdout with non-positive net EV, or failure to
  beat the comparator, or failure of the mirror test, or a signal that vanishes
  out of sample.
- **HOLD** — the test cannot fairly answer: holdout below floor, historical
  prices unavailable, execution reconstruction inadequate, or another
  preregistered sufficiency requirement fails. Insufficient data is never a PASS
  or a FAIL.

## 19. Kill rules

- **Track A.** If pooled streak information fails to improve materially over
  family/base-rate probabilities on adequately powered holdout data, retire
  pooled conditional reversion. Do not widen to unrelated classes to rescue it.
- **Track B.** If persistence exists statistically but Kalshi prices already
  incorporate it (net executable EV ≤ 0), retire the persistence trading thesis.
  Predictable outcomes do not imply profitable markets.
- **General.** Profitability that appears only after post-hoc thresholds,
  cherry-picked families, dropped losers, relaxed fees, unrealistic maker fills
  or increased exposure after losses is a FAIL.

## 20. Required outputs

One research package: `MARKTANGLE_2_SPEC.md` (this), `MARKTANGLE_2_DATA_REPORT.md`,
`MARKTANGLE_2_TRACK_A.md`, `MARKTANGLE_2_TRACK_B.md`, `MARKTANGLE_2_TRADES.csv`
(one row per simulated trade, sufficient to reproduce every economic result),
`MARKTANGLE_2_SUMMARY.md` (track verdicts, surviving arms, statistical-only vs
economically tradeable, exact next gate).

## 21. Reproducibility

Record code SHA, data cutoff, universe manifest/fingerprint, trade/result
fingerprints, model configuration, exclusions, split dates, fee model and
execution assumptions. Identical runs on the same frozen data and configuration
produce identical fingerprints.

## 22. What success would mean

1. **Reversion alpha** — fresh-event classes exhibit direction-specific streak
   reversion that Kalshi underprices.
2. **Persistence alpha** — crypto thresholds exhibit predictable continuation
   that prices insufficiently incorporate.
3. **Efficient market** — sequences are predictable but prices carry it.
   *Forecastability ≠ trading alpha.* Recorded as FAIL, not optimized further.

## 23. Research priority

1. Reconstruct historical price/execution data. 2. Build the structural family
classifier. 3. Track B descriptive persistence/duration analysis. 4. Track A
pooled/hierarchical analysis. 5. Freeze models and entry rules. 6. Open the
untouched holdout. 7. Conservative taker economics. 8. Grade each track
independently. 9. Only on PASS, design a prospective paper/twin experiment.
Never proceed directly from historical PASS to live trading.

## 24. Central research question

When a recurring Kalshi market exhibits measurable serial dependence, does the
market price fully account for that dependence — or can knowing the current
state, its duration and relevant structural variables generate executable
positive expected value?

---

# Part II — Frozen implementation (decided before data)

Everything below is a module constant in `scripts/marktangle2_probe.py`, is
echoed in the run's reproducibility block, and is asserted equal to the frozen
Experiment OS contract by `tests/test_marktangle2_package.py`. None of it is an
argument that could be chosen after seeing output.

## II.1 Universe and structural classifier

- **Acquisition.** Per-series settled history from Kalshi's public API (the
  two-stage lesson of MARKTANGLE-1: enumeration and history are different
  queries). A default hand-picked series list guarantees the families
  MARKTANGLE-1 already surfaced are never lost to an enumeration accident; the
  live-board enumeration adds any further series the classifier recognises.
  Volume is not filtered (as in MARKTANGLE-1 run 8).
- **Family.** `SERIES|SUFFIX` from the ticker, ordered by close time. Same-close
  ties are dropped, never ordered by guess. Floor: 40 resolutions.
- **Classes (structure only, by ticker pattern and strike type):**

  | class | members | track |
  |---|---|---|
  | `CRYPTO_DAILY:<ASSET>` | `KX<ASSET>D` daily series, threshold (greater/less) markets only | B |
  | `WEATHER_HIGH_BUCKET` / `_THRESHOLD`, `WEATHER_LOW_*` | `KXHIGH<CITY>` / `KXLOW<CITY>`, split by strike type | A |
  | `<SPORT>_TOTAL`, `<SPORT>_SPREAD` | `KX<LEAGUE>TOTAL` / `SPREAD`, league mapped to sport by a fixed table (SOCCER, BASKETBALL, FOOTBALL, BASEBALL, HOCKEY) | A |

  Unknown leagues, non-recurring ladders and everything else are **unclassified**:
  reported in the data report, never pooled. Constant families (0%/100% YES) are
  excluded. Track B additionally requires ≥ 5 observations of each outcome.
  Crypto bucket (between) markets are not level crossings and are excluded.

## II.2 Prediction points, decision time, split

- A prediction point is position *i ≥ 1* of a family: the market at *i* is
  predicted from strictly earlier resolutions (previous outcome, streak
  direction, streak length k).
- **Decision time** T−60 min before the predicted market's close.
- **Split: 70/30 chronological per class**, cut at the 70% quantile of decision
  times; boundary ties go to holdout. No validation segment — every
  model-selection degree of freedom below is fixed, so there is nothing to
  select. Family effects and base rates for holdout predictions come from TRAIN
  only; a family unseen in TRAIN receives the class rate.

## II.3 Models

| arm | estimator |
|---|---|
| A0 / B0 | family YES rate, shrunk toward the class rate with **m = 20** pseudo-observations |
| A1 / B1 | family `P(YES \| previous)`, shrunk (m = 20) toward the class transition rate for that direction |
| A2 | class-pooled `P(YES \| direction, k)` with **k = 1..5 individually, k ≥ 6 pooled**, shrunk toward the class one-step rate for that direction; reversal = 1 − continuation |
| B2 | class-pooled `P(continuation \| state, duration bucket)`, buckets **1, 2, 3, 4–5, 6–9, 10–19, 20+** |
| A3 (primary) | penalized logistic `logit P(YES) = b0 + b1·dir + b2·ln k + b3·dir·ln k + u_family`, dir = +1 for a YES streak / −1 for NO; `u_family` ridge-penalized with **λ = 1.0** (the fixed-variance random-effects estimator); slopes ridge 1e-3 for stability only; Newton–Raphson, deterministic. `b3` is the coefficient the track turns on: negative = reversion rising with k |
| B3 (primary) | logistic `logit P(continuation) = c0 + c1·state + c2·ln d + c3·z_dir + c4·z_dir·ln d`; fitted on ≥ 50 train points with spot data; abstains (no prediction, no trade) when spot data is missing |

- **z_dir** = `ln(spot / strike) / σ_daily`, signed so that positive means spot
  sits on the side that continues the current state (strike type respected);
  |z| capped at 6. **spot** = close of the last *completed* Coinbase hourly
  candle before T−60m. **σ_daily** = standard deviation of the trailing **20**
  daily log returns from candles that closed before the decision's UTC day
  (≥ 10 returns required). Coinbase is a proxy for Kalshi's settlement index; the
  proxy error is stated, not hidden.

## II.4 Execution model

- Quote = the 1-minute Kalshi candle whose period ends at or before T−60m
  (live archive first, then historical). YES costs the ask; NO costs 100 − bid.
- **Liquidity screen:** both sides within [1, 99] and spread ≤ **10c**; else the
  point is unpriced (counted, no trade).
- **Fee** = worst-case taker `ceil(7·p·(1−p))` cents per contract, entry only.
  **Slippage** = **1c** per contract. Net edge = `100·P(side) − price − fee − 1`.
- **Entry:** the side with the larger net edge, only when it is **≥ 3c**.
  **Size:** 1 contract. Hold to settlement.
- **Mirror control:** exactly the treatment's entries, the opposite side of the
  same book at the same instant, same fee model.
- **Secondary sizing (reported, never gated):** quarter-Kelly, one unit per 2%
  bankroll fraction, capped at 4 units — a function of (edge, price, p) only.
- Candle fetch budget: holdout points first (Track A, then B, chronological),
  then train, up to `--max-fetch`. Coverage is reported per class and split.

## II.5 Grading

Per (class, arm) on HOLDOUT, every clause reported:

- **HOLD** if train prediction points < 500, or holdout priced coverage < 50%,
  or holdout trades < 100.
- Otherwise **PASS** iff all of: net P&L > 0; EV/trade > 0; Brier < the
  baseline's Brier on the same points **and** net P&L > the baseline's net P&L;
  EV/trade − mirror EV/trade ≥ **3c**; net P&L > 0 without the most profitable
  family; net P&L > 0 without the top 1% of trades (rounded up). Else **FAIL**.
- **Track verdict** is decided by the **primary treatment only** (A3, B3), fixed
  here: PASS if it passes in ≥ 1 preregistered class (the number of classes is
  reported beside it); FAIL if adequately powered and failing in every class;
  otherwise HOLD. Other arms are read, never promoted.
- Track B clustering: all strikes on one asset resolve off one spot print, so
  the report states distinct settlement days beside the trade count. The
  independent unit is the day; the floors are on trades because that is what
  was preregistered, and the day count is the honesty check beside them.

## II.6 Outputs and reproducibility

The instrument prints the package as marked sections; `marktangle2_package.py`
splits them into `docs/marktangle2/`. Fingerprints: sha256 of the trades CSV,
of the universe manifest (`ticker,close,result` sorted) and of the per-arm
verdicts with holdout net P&L. Code SHA is the runner's `OPS_CODE_SHA`.

## II.7 What is deliberately not here

- No maker simulation: no reliable resting-fill model exists for these books,
  and §12 forbids a maker PASS where taker fails.
- No validation-set search over penalties, buckets or bands.
- No family selected on its returns, no class widened, no floor lowered.
- No change to MARKTANGLE-1.
