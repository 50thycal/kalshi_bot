# Research Lab — statistical design for the mmsell and theta4 v2 gates

> ## WITHDRAWN 2026-08-21 — do not freeze either design from this document
>
> The operator withdrew both v2 contracts after
> `RESEARCH_LIVE_FILL_SELECTION_STUDY.md` and
> `RESEARCH_MMSELL_UNIVERSE_DECONFOUNDING.md`. What is invalidated is the
> **scientific question**, not the machinery: the bound clauses, the promotion /
> failure evidence-floor split, the 99% sequential bounds and the inclusive
> maximum-evidence horizon (PRs #245/#247) all remain valid and in force.
>
> * **MMSELL Design D — the 291-market delta gate is NOT to be frozen.**
>   Treatment and control differ in universe, entry-price band and settle-mode
>   mix simultaneously, so `delta.live_cents_per_contract` has no single
>   interpretation. See `RESEARCH_MMSELL_UNIVERSE_DECONFOUNDING.md` §0, §4, §5.
>   No sample size repairs it.
> * **theta4 v2 — not to be created.** theta4 is not eligible for rearm in its
>   current form: a tail-shape miscalibration *and* a fill-selection haircut,
>   two independent failures. The strategy needs research before another live
>   canary, not a different gate. See
>   `RESEARCH_THETA_TAIL_MODEL_DIAGNOSIS.md`.
>
> The **50-market historical v2 early-failure floor stays as pre-registered**
> and was not moved by any of these outcomes. Sections below are kept for the
> record and for the reusable statistical machinery; read them as history, not
> as a design to pick up.

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
* **power.** *Corrected:* this clause is a **two-leg** delta between the two live
  arms, so SE = σ√(2/n) = **33.94/√n**, not the four-leg twin-gap SE of 48/√n an
  earlier draft used. The corrected numbers are about 2× kinder and the
  conclusion is unchanged:

| true delta | markets/arm for 80% power (single look) | treatment calendar time |
|---|---|---|
| +1¢ | **7,125** | ~4 years |
| +2¢ | 1,781 | ~1 year |
| +3¢ | 792 | ~5.5 months |
| +5¢ | **285** | **~8.5 weeks** |
| +7¢ | 145 | ~4 weeks |

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
| lifetime false promotion at true delta = 0 | **~5%** (99% bound, §9g) |
| lifetime power to promote at +5¢ / +3¢ | 99.0% / 74.8% |
| lifetime power to promote at +1¢ | 17.6% (correctly low) |
| **lifetime power to KILL at true delta = −8¢** | **~100%** |
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

## 9a. Evidence floors — a promotion floor must not silence a safety clause

### The defect, stated generally

The evaluator's precedence is (`evaluator.py`, step 5 before step 6):

```
4. any required metric missing   -> BLOCKED_DATA
5. sample floors unmet           -> HOLD        <-- returns here
6. any fail_any clause true      -> FAIL
7. all pass_all clauses true     -> PASS
```

`sample` is a **promotion** floor — "how much evidence before a PASS may
authorize advancement" — but it currently gates *everything*. A `fail_any` clause
cannot fire while any floor is unmet. Observed on `mmsell-anchor-strangle`: a
clause on the failing side, verdict HOLD, because the floor was short.

For theta4 v2 that is not a nuisance, it is a defect: a shared 600-market floor
would make a catastrophic tail failure at n = 50, 100 or 300 **unable to fail the
experiment**, which is the entire reason the tail clause was retained.

These are two different questions and they deserve two different numbers:

| | question | current key |
|---|---|---|
| **promotion evidence floor** | how much evidence before PASS may authorize advancement? | `sample` |
| **failure evidence floor** | how much evidence before sufficiently bad evidence may terminate? | *(none)* |

### The generic mechanism

Add an **optional** per-clause `min_evidence` to `fail_any` clauses:

```json
{
  "list": "fail_any",
  "metric": "realized_tail_hit_ratio_vs_modeled",
  "bound": {"direction": "lower", "confidence": 0.99, "method": "poisson_exact"},
  "op": ">", "value": 1.0,
  "arm": "theta4", "deployment_kind": "live",
  "min_evidence": {"metric": "live_settled_markets", "op": ">=", "value": 50}
}
```

Semantics:

* a `fail_any` clause with **`min_evidence`** becomes *eligible* when its own floor
  is met, **independently** of the promotion floor;
* a `fail_any` clause **without** `min_evidence` inherits the promotion floor —
  **exactly today's behavior**;
* an eligible-and-true `fail_any` clause yields FAIL even when the promotion floor
  is unmet.

Revised precedence:

```
1. structural ambiguity                        -> BLOCKED_INTEGRITY
2. platform incomparability                    -> BLOCKED_PLATFORM
3. unresolved integrity events                 -> BLOCKED_INTEGRITY
4. metric missing for an ELIGIBLE clause       -> BLOCKED_DATA
5. any ELIGIBLE fail_any clause true           -> FAIL          <-- moved up
6. promotion floor unmet                       -> HOLD
7. any remaining fail_any clause true          -> FAIL
8. all pass_all true                           -> PASS
9. otherwise                                   -> HOLD
```

**This is backwards compatible by construction.** No existing frozen gate carries
`min_evidence`, so every existing clause remains gated by the promotion floor and
no recorded verdict changes — including `mmsell-anchor-strangle`'s HOLD. The
change is additive and opt-in per clause, which matters because imported
contracts must not be reinterpreted.

**`sample` is not renamed.** Every frozen gate's `spec_hash` binds to the current
key; renaming it to `promotion_floor` would either break those hashes or require
two spellings forever. It is documented as the promotion floor instead.

**The general rule for when an early-failure floor is warranted:** when the
failure mode is one the **risk envelope cannot detect**. A book that simply loses
money is caught by exposure limits and the kill switch. A book whose *model* is
wrong about tail frequency looks fine on P&L until the tail arrives. That is the
distinction, and it is why theta4 needs this and mmsell does not (§9c).

---

## 9b. theta4 early-tail-failure floor

### The estimator at small n

The normal approximation is wrong here: at n = 50 the expected hit count is
E ≈ 4. The clause uses the **exact one-sided Poisson lower bound** on the hit
rate, `λ_L(O)`, with `LCB(R) = λ_L(O) / E`. O is Poisson-binomial over
heterogeneous pᵢ; the Poisson form is an excellent approximation for rare events
and is **conservative here** — the measured mix is under-dispersed
(Var/mean = 15.544/17.181 = **0.905**), so the true tail is thinner than Poisson
and the test fires slightly *less* often than nominal. The conservatism is in the
direction of not falsely killing.

**Valid versus stable — the distinction matters.** The bound is mathematically
valid at *every* n, including n = 25 where E < 2: it controls the per-look error
rate by construction. What degrades at tiny n is **resolution and denominator
stability**, not validity:

| n | E | k_min (99%) | smallest realized ratio that can fire | largest single market's p as a share of E |
|---|---|---|---|---|
| 25 | 1.98 | 7 | **3.54×** | 7.0% |
| 50 | 3.96 | 10 | **2.53×** | 3.5% |
| 100 | 7.92 | 16 | 2.02× | 1.8% |
| 150 | 11.88 | 22 | 1.85× | 1.2% |

At n = 25 the test can express only "≥3.5× miscalibration" and one market carries
7% of the denominator. It is not wrong; it is coarse.

### The sequential problem, and why the confidence level changes

A `fail_any` bound is re-evaluated on every cadence as evidence accrues. That is a
**sequential test**, and a nominal 5% per-look rate is not a 5% lifetime rate.
Simulated over continuous evaluation from the floor to 600 markets, 40,000 trials:

| bound | floor 25 | 50 | 100 | 150 | 300 |
|---|---|---|---|---|---|
| **95%** — lifetime false kill at true R = 1.0 | 20.3% | 19.2% | 15.9% | 14.6% | 11.0% |
| **99%** — lifetime false kill at true R = 1.0 | **5.0%** | **4.8%** | **3.9%** | **3.4%** | **2.2%** |

A 95% bound evaluated continuously kills a **perfectly calibrated** book about one
time in six. A 99% bound restores the intended ~5% *lifetime* rate.

Detection is barely affected where it matters — lifetime, at the 99% bound and a
floor of 50: R = 1.25 → 40%, R = 1.5 → **88%**, R = 2.0 → **~100%**, R = 5.0 →
**100%**.

> **Recommendation: the early-failure clause uses a 99% one-sided bound.**

Note the floor barely moves the false-kill rate (5.0% → 2.2% across 25 → 300).
**The error rate is controlled by the confidence level, not by the floor.** The
floor is therefore an *operational* choice: the smallest denominator on which we
are willing to let a statistic terminate a real-money experiment.

### Recommended floor

> **`N_tail_fail` = 50 settled markets (~11 days).**

* E ≈ 4 — the smallest denominator at which no single market exceeds ~3.5% of it
* lifetime false kill **4.8%**, inside the 5% budget
* catches R = 5.0 (the failure that killed the original theta family) at **100%**,
  R = 2.0 at ~100%, R = 1.5 at 88%
* eligible **~12 weeks before** the 600-market promotion floor — which is the
  entire point of separating the two
* n = 25 is rejected not as invalid but as coarse (3.5× to fire, 7% denominator
  concentration) for a clause allowed to terminate a live experiment

Chosen from the statistic's stability and the sequential error budget. No theta4
outcome enters the derivation — the current evidence does not fire it in any case
(O = 20, E = 17.18, LCB₉₅(R) = **0.771**, well under 1.0).

---

## 9c. Twin coverage interaction — conservative, with the catastrophe elsewhere

> **The tail clause is MISSING — BLOCKED_DATA — whenever
> `twin_model_coverage_pct < 90`. It can neither PASS nor FAIL below that.**

This matches the conservative default and I did not find a justification to
override it inside the gate. Coverage loss selects a subset by a data defect; R
computed on it has an unknown bias *direction*, which is worse than a wide
interval because an interval advertises its own width. Failing on such a subset
would turn missing model data into evidence against the experiment, which is
exactly the inference to refuse.

The one argument *for* an exception is worth recording and then declining:
coverage is determined **at entry**, before any outcome exists, so it cannot be
correlated with outcomes except through entry-time characteristics. That makes
the bias channel weaker than outcome-based selection — but "weaker" is not
"absent", and it does not license terminating a real-money experiment.

**Where the catastrophic case belongs instead.** If coverage is short *and* the
covered subset is catastrophically bad, that is an **operational alert, not a
gate verdict**. The recommendation is that the tail provider record an
**integrity event** for Live Ops in that case, leaving the gate honestly
BLOCKED_DATA. This uses machinery that already exists, keeps the gate's
evidential standard intact, and still means nobody has to notice a five-alarm
fire by reading a HOLD.

---

## 9d. mmsell floor semantics — one floor, intentionally

**Confirmed: both promotion and kill wait for the 291-market floor, and no
early-failure machinery is added.**

Three reasons, recorded so it reads as a decision rather than an oversight:

1. The −8¢ kill power (88%) was **derived at that horizon**. An earlier floor
   would be a different test with characteristics nobody has priced.
2. mmsell's failure mode is *underperformance*, which the **risk envelope already
   watches** — $2 / 2-contract clips, exposure limits, the kill switch. The gate
   is not the safety mechanism there. theta4's tail miscalibration is invisible to
   a P&L-based safeguard until the tail arrives, which is why it needs one.
3. Both mmsell clauses read the **same** metric at the same floor, so splitting
   them would create two floors on one quantity for no statistical gain.

Per §9a this needs no new syntax: a `fail_any` clause with no `min_evidence`
inherits the promotion floor, which is exactly the intended behavior.

---

## 9e. Generic bound-clause schema

**Clause form:**

```json
{
  "metric": "<canonical metric key>",
  "bound": {
    "direction": "lower" | "upper",
    "confidence": 0.95 | 0.99,
    "method": "poisson_exact" | "normal" | "clopper_pearson"
  },
  "op": ">" | ">=" | "<" | "<=",
  "value": <threshold>,
  "arm": "<arm_key>",
  "deployment_kind": "live",
  "min_evidence": {"metric": "...", "op": ">=", "value": N}   // optional
}
```

The clause compares the **computed bound** to `value` — never the point estimate.
`direction` is required and never inferred from the operator: an upper bound with
`>` and a lower bound with `>` mean opposite things, and a metric's own
`direction` field (§2) does not determine which bound a contract wants.

**Recorded provenance, per clause:**

```json
{
  "estimator": "R = observed_tail_hits / sum(model_probability)",
  "bound_direction": "lower",
  "confidence": 0.99,
  "method": "poisson_exact_one_sided",
  "estimate": 1.1641,
  "uncertainty_inputs": {
    "observed": 20,
    "expected": 17.1808,
    "variance_basis": "poisson_binomial sum p(1-p) = 15.5440",
    "dispersion_ratio": 0.9047
  },
  "standard_error": 0.2295,
  "computed_bound": 0.7715,
  "threshold": 1.0,
  "fired": false,
  "provider_revision": "tail_v1",
  "evidence_n": 217,
  "evidence_unit": "settled markets",
  "min_evidence": {"metric": "live_settled_markets", "value": 50, "observed": 217, "met": true},
  "evaluations_since_eligible": 41
}
```

`evaluations_since_eligible` is there because §9b showed a continuously-evaluated
bound inflates its nominal per-look error rate. Recording the look count lets a
reader see how many chances a clause had, rather than reading a 99% bound as a 1%
lifetime claim.

**The sentence a fresh Control Tower session should be able to write from this,
without recomputing anything:**

> `FAIL — the 99% lower bound on the tail ratio (1.34) exceeded 1.0 after 112
> eligible settled markets (observed 21 hits against 8.9 expected, provider
> tail_v1, 9th evaluation since the 50-market failure floor).`

Contrast with what a bare number licenses today: `FAIL — realized_tail_hit_ratio_vs_modeled=1.34 > 1.0`,
which says nothing about how much evidence stood behind it or which bound was
taken.

---

## 9g. Sequential false promotion — and a retraction

A previous draft argued that promotion bounds could stay at 95% "because a human
reads every PASS". **That was wrong and is retracted.** Human review prevents
*automation*; it does not undo *repeated testing*. A recorded PASS is explicitly
capable of authorizing a transition, so evaluating a 95% bound on every cadence
until one clears zero inflates the lifetime false-promotion rate whether or not a
person clicks the button.

### Measured, for the two actual promotion gates

Monte Carlo, 20,000 trials, true effect exactly zero, evaluated on every market
from the floor to the horizon. mmsell uses σ_delta = 24·√2 = 33.94¢ (two live
arms); theta4 uses σ = 35.14¢.

| gate | horizon | **95% continuous** | **99% continuous** |
|---|---|---|---|
| MMSELL | 291 → 582 (2×) | 14.31% | **3.54%** |
| MMSELL | 291 → 873 (3×) | 17.73% | **4.95%** |
| THETA4 | 600 → 1200 (2×) | 14.62% | **3.60%** |
| THETA4 | 600 → 1800 (3×) | 17.71% | **5.01%** |

A continuously-evaluated 95% bound promotes a **worthless** book about one time
in six.

### 95% at preregistered checkpoints

| gate | looks | checkpoints | lifetime false promotion |
|---|---|---|---|
| MMSELL | 1 | 291 | 5.17% |
| MMSELL | 2 | 291, 582 | 7.67% |
| MMSELL | 3 | 291, 436, 582 | 8.98% |
| MMSELL | 4 | 291, 388, 485, 582 | 9.51% |
| THETA4 | 1 | 600 | 5.10% |
| THETA4 | 2 | 600, 1200 | 8.04% |
| THETA4 | 3 | 600, 900, 1200 | 9.06% |
| THETA4 | 4 | 600, 800, 1000, 1200 | 10.54% |

Checkpoints reach ≤5% only at a **single** look. That is operationally brittle —
one shot, on one day, and a book a few markets short of the floor waits for a
horizon that no longer has a second look in it. Two looks already cost 8%.

### The power comparison, done honestly

The instinct is that 99% costs power. Compared like-for-like — **lifetime against
lifetime**, which is the only fair comparison once evaluation is continuous — the
99% bound is *stronger* than the single-look 95% numbers quoted earlier:

| gate | true effect | 95% single look | **99% lifetime** |
|---|---|---|---|
| MMSELL (floor 291) | +3¢ | 44.5% | **74.8%** |
| MMSELL | +5¢ | 80.7% | **99.0%** |
| MMSELL | +1¢ | 12.7% | 17.6% |
| THETA4 (floor 600) | +2¢ | 40.1% | **69.3%** |
| THETA4 | +3¢ | 67.2% | **94.7%** |
| THETA4 | +5¢ | 96.7% | **100%** |

So the move to 99% is a **strict improvement on both axes**: lifetime false
promotion falls from ~18% to ~5%, *and* power rises above the single-look 95%
figures the floors were originally justified on. A 95% lifetime looks more
powerful still, but it buys that power with the 18% false-promotion rate this
whole section exists to refuse.

**The floors do not move.** 291 and 600 stand.

### Recommendation

> **Option 1 — continuous 99% one-sided bounds on both promotion clauses, and on
> mmsell's kill clause.** No new machinery beyond the confidence level, which the
> bound schema already carries as a field.

Option 3 (anytime-valid / alpha-spending) is not needed: option 1 already hits
the target, and a full sequential-testing framework would be substantial scope for
no measured gain here.

**One caveat, and its fix.** The ~5% figure is calibrated to a **3× horizon**. Run
longer and the rate creeps up — that is the nature of any fixed-α sequential
procedure. So the contract should **pre-register a maximum evidence horizon**:

| gate | promotion floor | max evidence horizon | calendar |
|---|---|---|---|
| MMSELL | 291 markets/arm | **873 markets/arm** | ~6 months |
| THETA4 | 600 markets | **1,800 markets** | ~13 months |

Past the horizon the gate stops accruing looks and the experiment goes to an
explicit operator decision rather than continuing to peek. That makes the ≤5%
claim exact for the contract's own lifetime instead of aspirational, and it costs
one line in each spec.

**Kill clauses go to 99% too**, for the same reason and at no cost — mmsell's kill
power at true −8¢ is ~100% lifetime under either bound, and theta4's tail blocker
was already set to 99% in §9b.

---

## 9f. Final gate definitions

### MMSELL v2 — `mmsell-scheduled-settle-live` v2/e1

```text
orientation:  delta.<metric> = treatment - control
              treatment = scheduled_settle (Lmmsell8v2)
              control    = price_ceiling   (Lmmsell10v2)
              live_cents_per_contract is higher_better, so a POSITIVE delta
              means the treatment earned more per contract.

sample (PROMOTION floor):
              live_settled_markets >= 291        [kind=live]  on BOTH arms
max evidence horizon:
              live_settled_markets  = 873        [kind=live]  on BOTH arms

promote (pass_all):
              delta.live_cents_per_contract
                bound:     {direction: lower, confidence: 0.99, method: normal}
                condition: LCB99(delta) > 0                   [kind=live]

block (fail_any):
              delta.live_cents_per_contract
                bound:     {direction: upper, confidence: 0.99, method: normal}
                condition: UCB99(delta) < 0                   [kind=live]
                min_evidence: NONE — inherits the promotion floor, intentionally
                              (see 9d: mmsell's failure mode is underperformance,
                               which the risk envelope already watches)

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
sample (PROMOTION floor):
              live_settled_markets >= 600        [kind=live]
max evidence horizon:
              live_settled_markets  = 1800       [kind=live]

promote (pass_all):
              live_cents_per_contract
                bound:     {direction: lower, confidence: 0.99, method: normal}
                condition: LCB99(live_cents_per_contract) > 0 [kind=live]
              twin_model_coverage_pct >= 90                  [kind=live]

block (fail_any):
              realized_tail_hit_ratio_vs_modeled
                bound:        {direction: lower, confidence: 0.99,
                               method: poisson_exact}
                condition:    LCB99(R) > 1.0                 [kind=live]
                min_evidence: live_settled_markets >= 50     [kind=live]
                MISSING while twin_model_coverage_pct < 90

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
| lifetime false promotion at true edge = 0 | ~5% (99% bound, 3x horizon) |
| lifetime power at +2¢ / +3¢ / +5¢ | 69% / 95% / 100% |
| lifetime power at the inherited +0.87¢ | ~24% |
| **lifetime** false block at true R = 1.0 | 4.8% (99% bound, continuous evaluation) |
| block power at R = 1.5 / 2.0 / 5.0 | 88% / ~100% / 100% |
| earliest possible block | **~11 days** (50-market failure floor) |
| time to promotion decision | ~19 weeks |

---

## 10. What was deliberately not done

No Version created or frozen. No epoch opened. No deployment armed. No tag
registered. No lifecycle transition. No exposure changed. No threshold altered on
any existing contract — v1's frozen gates are untouched, and both v1 deployments
continue under their existing risk envelopes with their contract defects still
surfaced by the Control Tower.
