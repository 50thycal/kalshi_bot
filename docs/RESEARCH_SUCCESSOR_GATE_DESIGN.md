# Research Lab — statistical design for the mmsell and theta4 v2 gates

**Session:** Research Lab, 2026-08-19. **Status:** design. **No lifecycle state
was changed and nothing here is frozen.** Third in the series after
`RESEARCH_LIVE_CANARY_CONTRACT_DEFECT.md` (the defect) and
`RESEARCH_LIVE_CANARY_SUCCESSOR_INPUTS.md` (the planning inputs).

Every threshold below is labelled **inherited**, **historical**, or **new** in
§8, because the point of this pass is to avoid outcome-aware redesign wearing
the costume of pre-registration.

---

## 0. A correction to my own earlier numbers

The floors I priced in the previous pass counted **contracts** as independent
observations. They are not. Contracts held in one market share one settlement,
so they are perfectly correlated for the outcome that matters. **The independent
unit is the settled MARKET.**

Measured contracts per settled market: `Lmmsell10` 1.97, `Lmmsell8` 1.43,
`theta4` 3.02. Treating theta4's contracts as independent understates its
standard error by √3.02 ≈ 1.74×, i.e. overstates precision by ~3× in sample
terms. Everything below is denominated in **settled markets**, with the contract
equivalent given for continuity.

Per-market dispersion, measured over each book's own settled set:

| leg | n markets | mean ¢/ct | **SD ¢/ct** | pooled ¢/ct |
|---|---|---|---|---|
| live `Lmmsell10` | 252 | +1.980 | **23.93** | +1.957 |
| twin `Lmmsell10_pt3` | 129 | +1.462 | **24.59** | +1.462 |
| live `Lmmsell8` | 11 | −15.273 | **49.49** | −15.273 |
| twin `Lmmsell8_pt3` | 17 | −7.630 | **40.40** | −7.630 |
| live `theta4` | 86 | +1.477 | **35.14** | +1.292 |
| twin `theta4_pt3` | 44 | +6.748 | **29.85** | +7.126 |

σ ≈ 24¢ per market on mmsell and ≈ 35¢ on theta4. That number sets everything
that follows.

Accrual rates, from live history: `Lmmsell8` **≈4.9 settled markets/day**,
`Lmmsell10` ≈76/day, `theta4` **≈4.5/day**.

---

## 1. mmsell — the paired metric measures the wrong thing

This is the finding that decides the mmsell design, and it nearly went the other
way.

Pairing live against its twin **market by market** collapses the variance
spectacularly:

| book | paired markets | SD live | SD twin | **SD of paired gap** |
|---|---|---|---|---|
| `Lmmsell10` | 64 | 18.00 | 17.92 | **1.17** |
| `Lmmsell8` | 11 | 49.49 | 47.94 | **3.83** |
| `theta4` | 37 | 31.84 | 31.83 | **0.06** |

A 15–530× reduction in SD is a ~200–280,000× reduction in required sample. It
looks like the answer to every power problem in this document.

**It is not, because the paired gap answers a different question.** Compare the
two gap definitions on theta4:

| | value |
|---|---|
| paired gap (same market, both legs settled) | **−0.224¢** |
| unpaired gap (twin's rate over its set − live's rate over its set) | **+5.834¢** |

They disagree by **6.06¢**, and in sign.

The reason is structural. A maker's adverse selection operates through **which
orders fill**, not through the P&L of the ones that do. Conditional on live
having filled the same market the twin traded, both legs see the same settlement
and realize nearly the same P&L — hence SD 0.06¢ on theta4. The quiet winners a
resting order never captures are exactly the markets where live has **no**
observation, so they are excluded from the paired set by construction.

**The paired gap is an execution-fidelity check, not an adverse-selection
measure.** It is worth computing — a large value would mean live and twin are not
really twins, which is a data-integrity signal — but it cannot be the primary
evidence for a hypothesis that is entirely about fill selection. A v2 gate built
on it would have looked wonderfully powered while measuring the wrong quantity.

### Twin coverage is itself a problem

Of live settled markets, the share with any twin row at all:

| book | live settled | with a twin row | coverage |
|---|---|---|---|
| `Lmmsell10` | 252 | 64 | **25%** |
| `Lmmsell8` | 11 | 11 | 100% |
| `theta4` | 86 | 37 | 43% (twin armed 2026-08-12) |

The control arm's twin covers a quarter of its live markets. A same-instant twin
is supposed to mirror the live book; a 25% mirror rate means the twin is not
seeing what live sees, most likely a per-cycle or per-event cap binding on the
busy arm. **This must be verified before any successor is armed** — a twin that
mirrors a quarter of the book cannot be the execution control, whatever the gate
says.

---

## 2. mmsell — the three designs, priced

The quantity the hypothesis is about: **does `scheduled_settle` suffer less
adverse selection than `price_ceiling`** — a difference-in-differences on the
*unpaired* twin gap, which is the shape `docs/BOOK_REGISTRY.md` already
pre-registered as primary for the `mmsell10a`/`mmsell10b` offset A/B.

### Orientation, stated once and pinned in code

The canonical rule, enforced by `compute_paired_metric` and now recorded in every
delta's provenance:

> **`delta.<metric>` = treatment − control**

What a positive delta *means* depends on the base metric's own direction, and the
two mmsell quantities point opposite ways:

| metric | direction | positive delta means |
|---|---|---|
| `live_cents_per_contract` | higher is better | treatment **better** |
| `twin_live_gap_cents` (a gap *is* adverse selection) | lower is better | treatment **worse** |
| `twin_live_winrate_gap_pp` | lower is better | treatment **worse** |

`MetricDefinition.direction` now carries this, delta provenance records
`positive_delta_means`, and `tests/test_experiment_os_live_metrics.py` pins a
synthetic case where the treatment is known better and asserts both the sign and
the gate result. Arm ordering is never relied on implicitly.

**Correction to the previous draft.** It described the current pair as
"+8.14¢ pointing in the wrong direction" beside a gate reading
`LCB₉₅(delta.live_cents_per_contract) > 0`. Both statements were true and they
were about **different metrics**: +8.14¢ is the twin-*gap* delta, where positive
is worse. On the gate's own metric the current value is **−17.23¢**. The gate's
inequalities were correct; the prose mixed two senses of "positive".

Current state, for context only (the treatment has n=11 and this is not
evidence):

| quantity | value | reading |
|---|---|---|
| live ¢/ct, `price_ceiling` (control) | +1.957 | |
| live ¢/ct, `scheduled_settle` (treatment) | −15.273 | |
| **`delta.live_cents_per_contract`** (the gate's metric) | **−17.230¢** | treatment **worse** |
| gap = twin − live, control | −0.495 | live *beat* its twin |
| gap = twin − live, treatment | +7.643 | heavy adverse selection |
| `delta.twin_live_gap_cents` (diagnostic) | **+8.138¢** | treatment **worse** |

Both deltas agree that the treatment currently looks worse. They say so with
**opposite signs**, which is precisely why the direction is now a registry field
rather than a sentence.

### Design A — paired twin as primary evidence

**Rejected.** §1: it measures execution fidelity, not adverse selection, and the
theta4 disagreement (−0.22¢ vs +5.83¢) demonstrates the substitution empirically
rather than by argument.

* exact clause: `delta.twin_live_paired_gap_cents <= 0` — not proposed
* interpretation: conditional-on-both-filling P&L fidelity
* it *is* worth keeping as a **diagnostic**: a paired gap materially away from 0
  means the twin is not a twin

### Design B — confidence-bound delta

* **exact clause:** `LCB₉₅(delta.live_cents_per_contract) > 0`
* **interpretation:** promote only when the treatment's live per-contract rate is
  demonstrably above the control's
* **false promotion at true delta = 0: exactly 5%**, by construction, at every n.
  This is the property the raw `>= 1.0` clause never had — it passed ~39% of the
  time on an identical pair.
* **power** (σ = 24¢/market on each of four legs, equal n per arm, SE = 48/√n):

| true delta | markets/arm for 80% power | treatment calendar time |
|---|---|---|
| +1¢ | **14,246** | ~8 years |
| +2¢ | 3,562 | ~2 years |
| +3¢ | 1,583 | ~11 months |
| +5¢ | 570 | ~4 months |
| +7¢ | **291** | **~8.5 weeks** |
| +10¢ | 142 | ~4 weeks |

The bound fixes the **error rate** and does nothing for the **power**. At the
inherited 1¢ effect this design is unreachable by three orders of magnitude.

### Design C — raw delta demoted to diagnostic

**Correct, and it should go further.** The raw delta is not the only underpowered
clause: `twin_live_winrate_gap_pp <= 1.0` needs ~16,500 markets/arm for its
standard error to reach a third of its own threshold. *Every* between-arm clause
in the proposed v2 gate is unreachable at 1¢/1pp resolution, and that is a
property of the question, not of the wording.

### Design D — recommended: a bounded gate honest about which direction it can decide

Keep Design B's inferential form; set the floor at what is reachable; and add
the kill side, which is where the power actually is.

```text
sample:   live_settled_markets >= 291   [kind=live]  on BOTH arms
promote:  LCB95(delta.live_cents_per_contract) > 0    [kind=live]
kill:     UCB95(delta.live_cents_per_contract) < 0    [kind=live]
                                          treatment=scheduled_settle
                                          control=price_ceiling
diagnostics (recorded, never gating):
          twin_live_gap_cents per arm; twin_live_paired_gap_cents per arm;
          twin_live_winrate_gap_pp per arm
```

| property | value |
|---|---|
| false promotion at true delta = 0 | **5%** |
| power to promote at +7¢ | 80% |
| power to promote at +1¢ | ~10% (correctly near-zero) |
| **power to KILL at true delta = −8¢** | **88%** |
| treatment calendar time to floor | **~8.5 weeks** |
| control calendar time to floor | ~4 days |

**Why this is the right shape.** Two months of real money cannot tell +1¢ from 0,
and no clause wording changes that. It *can* tell −8¢ from 0 — and the current
n=11 reading, whatever its noise, points that way. A gate that can conclusively
kill in two months and will honestly refuse to promote on a small effect is worth
running. A gate that pretends 150 contracts can adjudicate a 1¢ advantage is not.

**The operator should also weigh not re-arming at all.** If the realistic outcome
of eight weeks is "killed" or "still can't tell", the value of the experiment is
the kill. That is a legitimate reason to run it and a legitimate reason to skip
it; it is not my call.

---

## 3. theta4 — bound-based profitability clause

**Exact clause:** `LCB₉₅(live_cents_per_contract) > 0` `[kind=live]`, with
`live_settled_markets` as the sample floor.

At true edge exactly 0 this promotes **5% of the time by construction**, at every
floor — the defined false-promotion standard the point-estimate form never had.

σ = 35.14¢/market, rate ≈ 4.5 settled markets/day:

| floor (markets) | ≈ contracts | calendar | true −1¢ | **true 0** | +1¢ | +2¢ | +3¢ | +5¢ |
|---|---|---|---|---|---|---|---|---|
| 150 | ~450 | 33 d | 2.3% | **5.0%** | 9.7% | 17.2% | 27.4% | 53.9% |
| 300 | ~900 | 67 d | 1.6% | **5.0%** | 12.5% | 25.5% | 43.4% | 79.4% |
| **600** | **~1,800** | **133 d** | **1.0%** | **5.0%** | **17.2%** | **40.1%** | **67.2%** | **96.7%** |
| 900 | ~2,700 | 200 d | 0.6% | **5.0%** | 21.4% | 52.5% | 82.0% | 99.6% |
| 1,200 | ~3,600 | 267 d | 0.4% | **5.0%** | 25.5% | 62.8% | 90.5% | 99.9% |

**Recommended floor: 600 settled markets (~1,800 contracts, ~19 weeks).**

**Reconfirmed after the tail-bound correction (§5.5), on a different
justification.** The earlier draft justified 600 as the floor that made the tail
clause strong. Once the tail criterion moves into `fail_any` it needs no floor of
its own to be valid (§5.7), so the shared floor is now set by the **profitability
clause alone**: 600 markets buys 67% power at +3¢ and 97% at +5¢, with false
promotion pinned at 5% by construction.

The number is unchanged; the reason it survives is that it was already the right
answer for the promotion clause, and the tail blocker happens to reach 98% against
R ≥ 1.5 there — a bonus rather than the derivation.

**The honest limitation, stated rather than buried.** theta4's *inherited*
minimum useful effect is **+0.87¢/contract** (the fee-re-baselined reading of the
original "> 0"). At 600 markets this gate has **~15% power** at that effect. A
book performing exactly at theta4's inherited bar will almost certainly not be
promoted. That is the gate behaving correctly — +0.87¢ against 35¢ of per-market
noise is not distinguishable from zero in any horizon this operation can fund
(~10,000 markets, ~6 years) — but it means **v2 is realistically a kill-test with
a narrow promotion path, exactly as mmsell v2 is.**

Note the previous pass's "~700 contracts" landed near 300 markets/900 contracts;
the shift to 600 markets comes from the independence correction in §0 plus
alignment with the tail clause, not from anything about the observed result.

---

## 4. Tail metric specification — `realized_tail_hit_ratio_vs_modeled`

Written before implementation, because no reference implementation exists to
canonicalize (`scripts/theta_fill_model.py` computes a maker-fill projection and
contains no tail logic at all).

### 4.1 Quantity

> **R = O / E**, where **O** is the number of settled markets in scope whose sold
> tail resolved in-the-money, and **E = Σᵢ pᵢ** is the sum of the modeled
> probabilities of those same markets.

R = 1 means realized tail frequency equals modeled. R > 1 means the model
under-prices tail risk.

* **Numerator O** — count of settled markets with a **tail hit**.
* **Denominator E** — `Σ model_probability` over *exactly the same market set*.
  Not `n × p̄`: the modeled probabilities are heterogeneous (measured range
  0.0022 – 0.1392), so the sum and the mean-times-count differ.
* **Unit of evidence — the settled MARKET.** A tail hits or does not, once per
  market. Contracts held on one market are perfectly correlated for that event
  and must never inflate n.

### 4.2 Realized tail-hit definition

theta enters by buying NO (selling YES) at the no-bid. The tail **hits** when YES
resolves — the position loses.

Canonical coding: **`resolved_value = 0`** on the position's own side. Verified
against production: on theta4's 217 settled paper markets, `resolved_value = 0`
occurs 20 times and `pnl < 0` occurs 20 times — the two codings agree exactly.
`resolved_value` is the definition; the P&L sign is the cross-check, because P&L
can go negative for reasons other than a tail hit (early close, fees on a
near-miss) and would silently over-count in a book with different exit rules.

### 4.3 Where the modeled probability comes from

`live_orders` carries **no** `model_probability`. Measured recovery on theta4:
**0 of 103** live orders join to a paper `theta4` row on the same ticker within
±600 s; **44 of 103** join to the `theta4_pt3` twin — and exactly 44 live orders
exist since the twin was armed.

So at `kind=live` the modeled probability is supplied by the **same-instant
paper twin**, matched on `(market_ticker, arm)` within the deployment pair.
This makes the twin **structural to the metric**, not merely the control design:
without a twin covering the whole live window, the clause is uncomputable by
construction, and the contract must say so in writing rather than leaving a
provider to discover it.

### 4.4 Missing model probability

A market whose modeled probability cannot be resolved is **excluded from both O
and E**, and counted in provenance. It is never imputed from the book's mean —
imputing the mean pulls R toward 1, i.e. **toward passing**, which is the
direction that must never happen silently.

If excluded markets exceed **10%** of the settled set, the metric returns
**MISSING** rather than a value: at that point R describes a subset chosen by a
data defect rather than by the experiment.

### 4.5 Open and censored markets

Excluded from both numerator and denominator, and counted. An open market's
outcome is unknown, not a miss. Counting an unresolved market as "did not hit"
biases R **downward**, toward passing. Orders that never held a position are
excluded separately — they are not tails we sold.

### 4.6 Selection conditioning

theta enters only where `edge = p_market − p_model` clears its threshold (6¢ for
theta4), so the evaluated set is deliberately the subset where the model most
disagrees with the market. R is **conditional on that selection**. This is the
correct read for a gate — "were we right where we actually bet" — and it is
**not** a general calibration statistic. The metric description must say so, so
no future reader quotes R as evidence the vol model is calibrated at large.

At `kind=live` there is a second conditioning: only markets where the live order
**filled** are observed. Fills are not random with respect to outcome, so live R
and paper R answer subtly different questions. Both are legitimate; the contract
must name which one it gates on (**live**, for a LIVE_CANARY → PRODUCTION gate).

### 4.7 Platform / MODEL revision binding

E is the model's own output, so **any `MODEL` platform revision breaks
comparability of R across the boundary** and must be treated as non-poolable.

Conversely R is a *frequency* ratio and is **immune to `FEE_MODEL` revisions** —
it is unaffected by the 2026-08-11 maker-fee re-baseline that forces the
"read `> 0` as `> +0.87¢`" caveat onto the P&L clause. That immunity is a genuine
advantage of this clause over the profitability one and is worth recording.

### 4.8 Uncertainty

O is Poisson-binomial over heterogeneous pᵢ:

> Var(O) = Σᵢ pᵢ(1 − pᵢ)  ·  **SE(R) = √(Σ pᵢ(1 − pᵢ)) / Σ pᵢ**

Measured exactly on theta4's 217 settled paper markets: Σp = **17.1808**,
Σp(1−p) = **15.5440**, O = **20**.

> **R = 20 / 17.1808 = 1.164**, **SE = 3.9426 / 17.1808 = 0.230**

Scaling for planning: **SE(R) ≈ 3.38 / √n_markets**.

Independence across markets is assumed. It is defensible here — theta's hourly
crypto ladders settle on different underlyings and hours — but the contract
should say it is assumed, because same-hour ladders on one underlying are not
independent and a future universe change could break it.

### 4.9 Threshold interpretation

The clause compares a **bound**, not the point estimate. See §5.

---

## 5. Tail threshold and market floor

### 5.1 Why 1.25 is not inherited

`docs/THETA_THESIS.md` states the family's pre-committed rule as *"realized
tail-hit rate is at or below its modeled probability"* — i.e. **R ≤ 1.0**. The
imported theta4 gate carries **≤ 1.25**, registered 2026-07-11 (commit
`f7de452`) when theta4 had ~0 trades, three weeks before it traded live. It is
genuinely pre-registered, and it is a **25% relaxation of the family rule**, made
with the earlier theta family's failure data in hand. That history stays in the
record; it is not rewritten.

It is rejected on **coherence**, using only pre-registered quantities:

> extra tail cost per contract = (R − 1) × p̄ × 100¢ = **7.92 (R − 1) ¢**

At R = 1.25 that is **1.98¢/contract** of extra loss. theta4's inherited
profitability bar is **+0.87¢/contract** — so miscalibration reaches the bar's
worth at **R ≈ 1.11**. A tail clause set at 1.25 therefore permits a state in
which the book cannot possibly clear its own profitability clause: **the tail
clause could never bind first.** A clause that cannot bind is not a clause.

Neither number in that derivation is the observed result. p̄ is the model's own
output and +0.87¢ is the inherited bar.

### 5.2 Bound direction — the previous recommendation was wrong

`LCB₉₅(R) ≤ 1.0` was recommended as a promotion clause. **That was a category
error**, and the challenge to it is correct.

R is a lower-is-better quantity. The two one-sided bounds answer different
questions, and only one of them is a promotion claim:

| clause | what a PASS licenses you to say |
|---|---|
| **A** `LCB₉₅(R) ≤ 1.0` | *"We have **not demonstrated** that the tails hit more often than modeled."* Absence of evidence. |
| **B** `UCB₉₅(R) ≤ 1.0` | *"We have 95% **evidence** that the tails hit no more often than modeled."* The thesis claim. |

A is a **kill** criterion wearing a promotion clause's clothes. Putting it in
`pass_all` means a book with no evidence either way is treated as having
demonstrated calibration — which is exactly the inference the thesis exists to
prevent.

### 5.3 The three designs, priced

SE(R) = 3.38/√n; ≈4.5 settled markets/day. Probability of **PASS**:

**A — `LCB₉₅(R) ≤ 1.0`**

| n | days | SE | R=0.75 | R=1.00 | R=1.10 | R=1.25 | R=1.50 |
|---|---|---|---|---|---|---|---|
| 300 | 67 | 0.195 | 99.8% | 95.0% | 87.1% | 64.2% | 18.0% |
| 600 | 133 | 0.138 | 100.0% | 95.0% | 82.1% | 43.4% | 2.4% |
| 900 | 200 | 0.113 | 100.0% | 95.0% | 77.6% | 28.3% | 0.3% |

*False-promotion reading:* a book at R = 1.25 — miscalibrated enough to consume
twice its own profitability bar — passes 43% of the time at n=600.
*False-fail reading:* 5% at perfect calibration, by construction.

**B — `UCB₉₅(R) ≤ 1.0`**

| n | days | SE | R=0.75 | R=1.00 | R=1.10 | R=1.25 | R=1.50 |
|---|---|---|---|---|---|---|---|
| 300 | 67 | 0.195 | 35.8% | 5.0% | 1.5% | 0.2% | 0.0% |
| 600 | 133 | 0.138 | 56.6% | 5.0% | 0.9% | 0.0% | 0.0% |
| 900 | 200 | 0.113 | 71.7% | 5.0% | 0.6% | 0.0% | 0.0% |

*False-promotion reading:* 5% at exactly R = 1.0, by construction — the correct
standard for a promotion claim.
*False-fail reading:* **95% at perfect calibration.** B does not ask whether the
model is calibrated; it asks whether the model is demonstrably *conservative*. A
model that is exactly right fails 19 times out of 20.

**C — `UCB₉₅(R) ≤ M` for a margin M > 1**

The margin must be justified without reference to theta4's outcomes. The only
independent anchor available is §5.1's arithmetic: extra tail cost is
`7.92 (R−1)` ¢/contract, and the inherited profitability bar is +0.87¢/contract,
so miscalibration reaches the bar's worth at **M = 1.11**.

| n | days | SE | R=0.75 | R=1.00 | R=1.10 | R=1.25 | R=1.50 |
|---|---|---|---|---|---|---|---|
| 300 | 67 | 0.195 | 57.9% | 14.0% | 5.5% | 0.9% | 0.0% |
| 600 | 133 | 0.138 | 83.2% | 19.8% | 5.8% | 0.4% | 0.0% |
| 900 | 200 | 0.113 | 93.9% | 25.2% | 6.0% | 0.2% | 0.0% |

Still 20–25% at perfect calibration, because M − 1 = 0.11 is under one standard
error at every reachable n.

### 5.4 Saying it plainly: the promotion form is unreachable

Sample needed for `UCB₉₅(R) ≤ M` to reach 80% power at a **perfectly calibrated**
model (true R = 1.0):

| margin M | markets needed | calendar |
|---|---|---|
| 1.05 | 28,256 | **~17 years** |
| **1.11** (the coherent margin) | **5,838** | **~3.6 years** |
| 1.25 (the imported bar) | 1,130 | ~0.7 years |

Only the imported 1.25 is reachable, and §5.1 rejects it on coherence: it permits
1.98¢/contract of tail cost against a +0.87¢ bar, so it could never bind before
the profitability clause. **Widening the margin to whatever the sample can carry
is precisely the move to refuse.**

**Conclusion: theta4 v2 cannot carry a positive calibration claim at any
practical horizon.** The correct response is not a permissive bound. It is to
stop asserting a claim the evidence cannot support, and to put the tail clause
where its statistics actually work.

### 5.5 Recommended structure — tail clause becomes a BLOCKER, not a promoter

```text
sample:               live_settled_markets >= 600               [kind=live]
promote (pass_all):   LCB95(live_cents_per_contract) > 0        [kind=live]
                      twin_model_coverage_pct >= 90             [kind=live]
block   (fail_any):   LCB95(realized_tail_hit_ratio_vs_modeled) > 1.0  [kind=live]
```

The tail criterion is unchanged in arithmetic from design A — but it is stated as
what it is: **a failure condition, evaluated in `fail_any`, that blocks promotion
when miscalibration is demonstrated.** It never contributes affirmative evidence.

**What a PASS of the whole gate licenses, exactly:**

> "Over the epoch's live evidence, this book's realized per-contract economics
> are positive at one-sided 95% confidence, **and** no demonstrated tail
> miscalibration was found. It is **not** established that the tail model is
> calibrated; the evidence is merely consistent with calibration."

That sentence belongs in the v2 contract's notes so that no future reader — human
or otherwise — upgrades "not disconfirmed" into "confirmed".

**Operating characteristics of the blocker** (probability of BLOCK):

| n | R = 1.0 (false block) | R = 1.25 | R = 1.5 | R = 2.0 |
|---|---|---|---|---|
| 300 | 5.0% | 35.8% | 82.0% | ~100% |
| 600 | 5.0% | 56.6% | 97.6% | ~100% |
| 900 | 5.0% | 71.7% | 99.7% | ~100% |

It catches the failure mode it exists for — the original theta family died at
R ≈ 5 (40% tail-hit against ~8% modeled) — and it does not kill a calibrated book
on noise.

### 5.6 Why not the literal point estimate

For completeness, the thesis's rule read literally:

| form | false-fail at true R = 1.0 | blocks R = 1.5 (n=600) |
|---|---|---|
| `R̂ ≤ 1.0` (thesis, literal) | **50%** | ~100% |
| `LCB₉₅(R) ≤ 1.0` (recommended blocker) | **5%** | 98% |

The point estimate coin-flips a perfectly calibrated book. As a *blocker* — the
role §5.5 assigns it — a 50% false-block rate is not defensible, and the bound
form achieves the same protection against real miscalibration at a tenth of the
false-block cost. **The threshold 1.0 is unchanged in both**; only the inferential
form differs, so nothing is re-derived from outcomes.

### 5.7 Tail sample floor — it no longer sets the shared floor

Under §5.5 the tail criterion sits in `fail_any`, and a `fail_any` clause with a
5%-by-construction false-block rate needs **no floor of its own to be valid** at
any n. Its precision only changes how much miscalibration it catches:

| floor (settled markets) | calendar | SE(R) | blocks R = 1.25 | R = 1.5 | R = 2.0 |
|---|---|---|---|---|---|
| 100 | ~22 d | 0.342 | 18% | 43% | 90% |
| 300 | ~67 d | 0.195 | 36% | 82% | ~100% |
| **600** | **~133 d** | **0.138** | **57%** | **98%** | **~100%** |
| 900 | ~200 d | 0.113 | 72% | ~100% | ~100% |

So the shared floor is now set by the **profitability clause alone** (§3), and the
tail blocker inherits whatever n that produces. At 600 markets it blocks R ≥ 1.5
at 98% — comfortably covering the failure mode that killed the original theta
family — which is a reason to be content with 600, not a reason to have chosen it.

---|---|---|---|
| floor (settled markets) | **100** | **300** | **600** |
| calendar | ~22 d | ~67 d | **~133 d** |
| SE(R) | 0.342 | 0.197 | **0.139** |
| false kill at true R = 1.0 | 5% | 5% | **5%** |
| kills R = 1.25 | 18% | 35% | **56%** |
| kills R = 1.5 | 43% | 81% | **97%** |
| kills R = 2.0 | 90% | ~100% | **~100%** |

**T3 = 600 settled markets**, matching the profitability floor exactly, so one
floor serves both clauses. T1 is a catastrophe detector only — it would have
caught the original theta family's R ≈ 5, and little else.

---

## 5.8 Twin coverage as an explicit prerequisite

A same-instant twin *existing* is not enough. The control arm's twin mirrors 25%
of its live settled markets (§1), and nothing in a gate's wording detects that.

### The metric

> **`twin_model_coverage_pct`** = 100 × (settled live markets in the evidence set
> whose modeled probability resolves from the registered same-instant twin) ÷
> (settled live markets in the evidence set)

**The denominator is the evidence set itself** — exactly the markets the gate
would otherwise count. That is the right denominator because it makes the metric
answer the question that matters: *of the evidence this gate is about to decide
on, how much carries the model information the decision requires?* A denominator
of "markets the twin traded" would be self-satisfying, and a denominator of "all
markets in the universe" would measure the strategy's selectivity instead.

**What missing coverage means:** the excluded markets were chosen by a data
defect, not by the experiment. R is then computed on a subset with unknown
selection, and the direction of the resulting bias is unknown — which is worse
than a wide interval, because an interval at least advertises its own width.

For mmsell, where the twin is an execution control rather than a model-probability
source, the parallel quantity is **`twin_mirror_coverage_pct`** over live markets
*entered* (not settled), since the mirror should fire at entry.

### Threshold, derived

Let `f` be the excluded fraction and suppose the excluded markets' true ratio
differs from the included ones by a factor `k`. The bias in R is approximately
`f · (k − 1) · R`. Taking `k = 2` as a deliberately pessimistic bound on how
different a data-defect-selected subset could be, and requiring the bias to stay
below **half a standard error at the recommended floor** (SE = 0.138 at n = 600,
so bias ≤ 0.069):

> f · 1 · 1.0 ≤ 0.069  →  f ≤ 6.9%  →  **coverage ≥ 93%**

Rounded down to a round number that is not derived from any current measurement:

> **`twin_model_coverage_pct >= 90`** — pre-registered.

At 90% the worst-case bias under the same pessimistic `k = 2` is 0.10, about
three-quarters of one standard error — visible in the interval rather than hidden
under it. The threshold is deliberately **not** set from theta4's observed 43% or
mmsell's observed 25%; both would fail it, which is the point.

### Where it binds

| book | clause | blocking? |
|---|---|---|
| theta4 v2 | `twin_model_coverage_pct >= 90` in `pass_all` | **yes** — the tail metric has no denominator without it |
| theta4 v2 | tail metric returns **MISSING** below 90% | **yes** — BLOCKED_DATA, never a promotable value |
| mmsell v2 | `twin_mirror_coverage_pct` **recorded and reported** | **no** — Design D's primary clause compares the two *live* arms and does not use the twin |

Being honest about the mmsell case matters: adding a blocking coverage clause
there would block promotion for a reason unrelated to the hypothesis being
tested. It is reported and alerted on, not gated. **If the operator ever makes a
twin-based read primary for mmsell, coverage becomes blocking there too** — but
§2 showed the twin-gap DiD is unreachable, so it is not primary.

---

## 6. Same-instant twin — requirement retained and strengthened

Retained, and §4.3 upgrades the reason. It is no longer only a control-design
preference: at `kind=live` the twin is the **only source of the modeled
probability**, so without it the tail clause has no denominator.

* live orders do not carry `model_probability` — 0 of 103 recovered from paper
* the pre-twin live history cannot be reconstructed into a valid same-window
  comparison — the twin armed 2026-08-12, live 2026-07-30
* v1 evidence therefore stays **reference-only**

Added requirement from §1: **twin mirror coverage must be verified after arming
and before the gate is trusted.** The existing control twin covers 25% of its
live markets. A twin that mirrors a quarter of the book is not an execution
control, and no clause wording detects that on its own.

---

## 7. Tag convention

The proposed `Lmmsell8b` / `Lmmsell10b` / `theta4b` / `theta4b_pt` are fine but
carry no information: `b` does not say what changed, and it collides on a v3.

Experiment OS has **no documented tag convention** today, so this is new. The
binding constraint is `paper_trades.strategy` — **`String(24)`** (`live_orders`
allows 32, but a twin tag must fit the paper column).

Proposed: **`<book><arm?>v<version>`**, twin suffix **`_pt`**.

| | live | twin |
|---|---|---|
| mmsell control | `Lmmsell10v2` | `Lmmsell10v2_pt` |
| mmsell treatment | `Lmmsell8v2` | `Lmmsell8v2_pt` |
| theta4 | `theta4v2` | `theta4v2_pt` |

Longest is 14 characters, well inside 24. The version suffix is the useful part:
a Version is exactly the boundary across which evidence must not pool, so a tag
that changes with the Version makes accidental pooling impossible to express.
Epoch is deliberately **not** encoded — a new epoch within a version reuses the
tags, and the epoch boundary is enforced by the evidence window, not the tag.

---

## 8. Provenance — what is inherited, historical, or new

| element | status |
|---|---|
| mmsell hypothesis (scheduled-settle reduces adverse selection) | **inherited** — v1 contract |
| mmsell 1¢ minimum useful effect | **inherited** — v1 `delta >= 1.0` |
| twin gap as the primary execution read | **historical** — `BOOK_REGISTRY` mmsell10a/b: "the twin-paired read was pre-registered as primary" |
| unpaired-delta underpowering | **historical** — same entry: "needs ~30,391 contracts/arm" |
| theta4 tail hypothesis R ≤ 1.0 | **inherited** — `THETA_THESIS` pre-committed decision rule |
| theta4 tail bar 1.25 | **historical** — imported, documented, **not** carried into v2 |
| theta4 minimum useful effect +0.87¢ | **inherited** — fee-re-baselined "> 0" |
| theta4 σ ≈ 35¢/market, p̄ = 0.0792, rates | **new measurement** — planning input only |
| market (not contract) as the independent unit | **new** — corrects my own earlier pass |
| paired gap ≠ adverse selection | **new** — measured, §1 |
| 25% twin mirror coverage | **new** — measured, §1 |
| `LCB₉₅ > 0` clause form (both books) | **new** — inferential form only; thresholds unchanged |
| mmsell floor 291 markets | **new** — derived from a reachable MDE, not inherited |
| theta4 floor 600 markets | **new** — derived; aligned to the tail clause |
| tail criterion `LCB₉₅(R) > 1.0` as a **blocker** | placement **new**; threshold **inherited** from the thesis |
| that v2 carries no positive calibration claim | **new** — a consequence of §5.4, not a choice |
| `twin_model_coverage_pct >= 90` | **new** — derived from a bias bound, not from observed coverage |
| tag convention | **new** |

Nothing in the "new" column was chosen by looking at whether it makes either book
pass. Where the derived answer is unflattering — theta4 unpromotable at its own
inherited effect, mmsell undecidable at 1¢ — that is what is reported.

---

## 9. Two provider additions this design requires

1. **`live_settled_markets`** — the sample-floor metric. The provider already
   computes it (`settled_markets` in provenance); it needs to be a registered
   metric so a floor can bind on the independent unit instead of on contracts.
2. **`realized_tail_hit_ratio_vs_modeled`** — per §4. No reference implementation
   exists, so it needs pinned tests rather than a parity check.
3. **`twin_model_coverage_pct`** and **`twin_mirror_coverage_pct`** — per §5.8.
   The tail provider must consult the first and return MISSING below the
   threshold, so they ship together.
4. **`twin_live_gap_cents`** and **`twin_live_paired_gap_cents`** — mmsell
   diagnostics. Both are `lower_better`; §2's orientation rules apply.

Bound-valued clauses (`LCB95(...)`, `UCB95(...)`) are a **clause form**, not new
metrics: the evaluator needs to express a one-sided bound on a metric's sampling
distribution. That is the one genuinely new piece of gate machinery this design
requires, and it should be built once rather than per metric.

All are ordinary metric work, blocked on nothing except the operator approving
the contracts they serve.

---

## 9b. Final gate definitions

### MMSELL v2 — `mmsell-scheduled-settle-live` v2/e1

```text
orientation:  delta.<metric> = treatment - control
              treatment = scheduled_settle (Lmmsell8v2)
              control    = price_ceiling   (Lmmsell10v2)
              live_cents_per_contract is higher_better, so a POSITIVE delta
              means the treatment earned more per contract.

sample:       live_settled_markets >= 291        [kind=live]  on BOTH arms

promote (pass_all):
              LCB95(delta.live_cents_per_contract) > 0        [kind=live]

block (fail_any):
              UCB95(delta.live_cents_per_contract) < 0        [kind=live]

diagnostics (recorded, never gating):
              twin_live_gap_cents           per arm   [lower_better]
              twin_live_paired_gap_cents    per arm   [lower_better]
              twin_live_winrate_gap_pp      per arm   [lower_better]
              twin_mirror_coverage_pct      per arm
```

**What a PASS licenses:** *"At one-sided 95% confidence, the scheduled-settle arm
realized more per live contract than the price-ceiling arm over this epoch."*
Nothing about magnitude, and nothing about why.

**What a BLOCK licenses:** *"At one-sided 95% confidence, the scheduled-settle arm
realized less per live contract."* That is the kill.

| | |
|---|---|
| false promotion at true delta = 0 | 5% |
| power at +7¢ / +1¢ | 80% / ~10% |
| power to block at −8¢ | 88% |
| time to decision | ~8.5 weeks (treatment-bound) |

### THETA4 v2 — `theta4-fat-tail` v2/e1

```text
sample:       live_settled_markets >= 600        [kind=live]

promote (pass_all):
              LCB95(live_cents_per_contract) > 0             [kind=live]
              twin_model_coverage_pct >= 90                  [kind=live]

block (fail_any):
              LCB95(realized_tail_hit_ratio_vs_modeled) > 1.0  [kind=live]

notes (frozen into the contract):
              A PASS does NOT establish that the tail model is calibrated.
              It establishes positive realized economics at 95% confidence,
              with no DEMONSTRATED tail miscalibration. Establishing
              calibration would need ~5,838 settled markets (~3.6 years) at
              the only economically coherent margin (1.11), and is therefore
              out of reach. Do not upgrade "not disconfirmed" to "confirmed".
```

**What a PASS licenses:** *"Over this epoch's live evidence, realized
per-contract economics are positive at one-sided 95% confidence, model-probability
coverage was adequate, and no tail miscalibration was demonstrated."*

**What a BLOCK licenses:** *"At one-sided 95% confidence, realized tails hit more
often than the model predicted."*

| | |
|---|---|
| false promotion at true edge = 0 | 5% |
| power at +3¢ / +5¢ | 67% / 97% |
| power at the inherited +0.87¢ | ~15% |
| false block at true R = 1.0 | 5% |
| block power at R = 1.5 / 2.0 | 98% / ~100% |
| time to decision | ~19 weeks |

---

## 10. What was deliberately not done

No Version created or frozen. No epoch opened. No deployment armed. No tag
registered. No lifecycle transition. No exposure changed. No threshold altered on
any existing contract — v1's frozen gates are untouched, and both v1 deployments
continue under their existing risk envelopes with their contract defects still
surfaced by the Control Tower.
