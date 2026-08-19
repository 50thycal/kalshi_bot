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

Current state, for context only (the treatment has n=11 and this is not
evidence):

| | gap = twin − live | SE |
|---|---|---|
| `price_ceiling` (control) | **−0.495¢** (live *beat* its twin) | ±2.64 |
| `scheduled_settle` (treatment) | **+7.643¢** | ±17.85 |
| difference-in-differences | **+8.138¢** — treatment looks *worse* | ±18.05 |

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

Derivation, not preference: 600 is the smallest floor at which the **tail clause**
(§5) also becomes strong — it detects R ≥ 1.5 at 97%. Running one floor that
makes both clauses informative is worth more than optimizing either alone. On the
profitability side it buys 67% power at +3¢ and 97% at +5¢, with false promotion
pinned at 5%.

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

### 5.2 Point estimate versus bound

| form | false-fail at true R = 1.0 | kills R = 1.5 (n=600) | kills R = 2.0 |
|---|---|---|---|
| `R̂ ≤ 1.0` (thesis, literal) | **50%** | ~100% | ~100% |
| `UCB₉₅(R) ≤ 1.0` | ~95% | ~100% | ~100% |
| **`LCB₉₅(R) ≤ 1.0`** (recommended) | **5%** | **97%** | **~100%** |

**Is 1.0 operationally too brittle?** As a point estimate, yes — a perfectly
calibrated model coin-flips the clause, and killing a good book half the time is
a real cost. `UCB ≤ 1.0` is worse in the other direction: it demands proof that
R is *below* 1 and fails a perfectly calibrated book ~95% of the time.

**Recommended: `LCB₉₅(R) ≤ 1.0`.** It keeps the thesis threshold **exactly** —
no re-derivation, no outcome-aware drift — and changes only the inferential form,
in the direction that stops noise from killing a calibrated book. Its role beside
a properly-bounded profitability clause is mechanism confirmation: fail only when
there is 95% evidence that realized tails exceed modeled. The historical failure
mode it exists to catch was R ≈ 5 (40% tail-hit against ~8% modeled), which it
catches essentially always.

False-pass behavior is its weakness and should be stated: at n = 600 a book with
true R = 1.25 still passes **43%** of the time. That is the price of a 5%
false-fail rate, and it is why the profitability clause carries the promotion
burden.

### 5.3 Candidate tail market floors

SE(R) = 3.38/√n; kill = P(LCB₉₅(R) > 1.0); ~4.5 settled markets/day.

| | **T1** | **T2** | **T3 (recommended)** |
|---|---|---|---|
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
| tail clause `LCB₉₅(R) ≤ 1.0` | form **new**, threshold **inherited** from the thesis |
| tail floor 600 markets | **new** — derived from SE(R) |
| tag convention | **new** |

Nothing in the "new" column was chosen by looking at whether it makes either book
pass. Where the derived answer is unflattering — theta4 unpromotable at its own
inherited effect, mmsell undecidable at 1¢ — that is what is reported.

---

## 9. Two provider additions this design requires

1. **`live_settled_markets`** — the sample-floor metric. The provider already
   computes it (`settled_markets` in provenance); it needs to be a registered
   metric so a floor can bind on the independent unit instead of on contracts.
2. **`realized_tail_hit_ratio_vs_modeled`** — per §4, with `twin_live_paired_gap_cents`
   as a diagnostic. No reference implementation exists, so it needs pinned tests
   rather than a parity check.

Both are ordinary metric work, blocked on nothing except the operator approving
the contracts they serve.

---

## 10. What was deliberately not done

No Version created or frozen. No epoch opened. No deployment armed. No tag
registered. No lifecycle transition. No exposure changed. No threshold altered on
any existing contract — v1's frozen gates are untouched, and both v1 deployments
continue under their existing risk envelopes with their contract defects still
surfaced by the Control Tower.
