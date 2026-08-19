# Research Lab — the three inputs the successor decisions were waiting on

**Session:** Research Lab, 2026-08-18. **Status:** analysis. **No production
lifecycle state was changed.** Companion to
`docs/RESEARCH_LIVE_CANARY_CONTRACT_DEFECT.md` (PR #239), which established that
both imported live-canary contracts are structurally unevaluable.

This document answers the three questions the operator attached to the v2
approvals:

1. is the mmsell treatment arm's opportunity rate high enough that reaching its
   pre-registered floor is operationally realistic? (§1)
2. is `scripts/theta_fill_model.py` trustworthy enough to make
   `realized_tail_hit_ratio_vs_modeled` a canonical promotion-gate provider? (§2)
3. what live sample floor should theta4 v2 pre-register, from a principled
   basis? (§3)

Every number below is read from production through the ops channel and is stated
with the query behind it. **Nothing here proposes changing a threshold.**

---

## 0. The canonical live picture, corrected

An earlier ad-hoc query in this session produced per-arm live figures that
disagreed with the established ones. It had a join defect (a `fills` aggregate
keyed by `(market, strategy)` re-joined to `live_orders`, multiplying rows on any
market with more than one order). The corrected accounting, which now matches the
canonical provider's own definition:

| | `Lmmsell10` (control) | `Lmmsell8` (treatment) | `theta4` |
|---|---|---|---|
| markets entered | 363 | 19 | 103 |
| contested, excluded | 3 | 3 | 0 |
| orders never filled | 114 | 4 | 17 |
| still open | 19 | 8 | 0 |
| **settled markets** | **230** | **7** | **86** |
| **settled contracts** | **453** | **10** | **260** |
| **realized P&L** | **+$5.31** | **−$4.96** | **+$3.36** |
| **¢/contract (honest)** | **+1.172** | **−49.6** | **+1.292** |

`Lmmsell8`'s −49.6¢/contract is over **ten contracts**. It is not a result; it is
what a per-contract rate looks like before there is a sample.

Note `theta4`: **+1.292¢/contract** on the honest definition versus **+1.477¢**
as the mean of per-market rates. Same data, two definitions — the divergence the
new provider exists to make explicit.

### 0b. …and what the EPOCH can actually count is much less

The table above is each **book's whole live history**. It is not what Experiment
OS may count, and running the canonical provider against production made the
difference concrete.

Both imported epochs `e1` start at the **migration instant**,
`2026-08-16 14:14:43.720928+00`, not at live arming. A gate's window is
`[max(epoch start, gate evidence_started_at), …]`, so every row before the
migration falls outside it. Verified provider output over the epoch window:

| | `Lmmsell10` | `Lmmsell8` | `theta4` |
|---|---|---|---|
| live arming | 2026-08-15 | 2026-08-15 | **2026-07-30** |
| settled markets, whole history | 230 | 7 | 86 |
| **settled markets, in-epoch** | **119** | **5** | **9** |
| **settled contracts, in-epoch** | **235** | **10** | **28** |
| **realized P&L, in-epoch** | **+$0.8195** | **−$4.96** | **+$2.44** |
| **¢/contract, in-epoch** | **+0.349** | **−49.6** | **+8.714** |

`theta4` is the striking one: **77 of its 86 settled markets sit outside its own
epoch**, because the book traded live for 17 days before Experiment OS imported
it. Its in-epoch rate reads **+8.714¢/contract on 28 contracts** against
**+1.292¢ on 260** across its life. Both are true; only the first is countable,
and it is far too small to mean anything.

Two consequences for the decisions below:

* **Time-to-floor is unaffected.** A v2 starts at n=0 regardless, so what matters
  is the accrual *rate*, which is measured over the book's live history and is
  unchanged.
* **The "80 is already cleared" concern is about the book, not the epoch.**
  theta4 has 86 settled markets *in its lifetime* and 9 *in its epoch*. Reusing
  80 would still be wrong — it was registered against paper trades, and a floor
  should never be anchored on observed history — but the reason is the anchoring,
  not that the epoch has already passed it.

---

## 1. mmsell — is the 150-contract floor operationally reachable?

### Opportunity, not fill rate

Daily live entries since arming (2026-08-15 12:56:54Z):

| day | `Lmmsell10` orders / contracts | `Lmmsell8` orders / contracts |
|---|---|---|
| 08-15 | 96 / 146 | 3 / 2 |
| 08-16 | 111 / 154 | 4 / 6 |
| 08-17 | 111 / 121 | 10 / 18 |
| 08-18 (partial) | 80 / 68 | 6 / 4 |
| **total** | **398 / 489** | **23 / 30** |

The treatment arm is starved of **opportunities**, not of fills. Fill rates are
comparable — control 244/398 = **61%**, treatment 15/23 = **65%**. The
scheduled-settle allowlist is admitting roughly **1/17th** as many entries. The
twin confirms this is structural rather than a live-execution artifact: the
`Lmmsell8_pt3` twin, which faces no fill constraint at all, opened 24 orders over
the same window against the control twin's 139.

### Time to floor

Two rates bracket the answer, and they differ because the settlement pipeline is
still filling:

* **observed settled-contract rate:** 10 settled contracts in 3.13 days =
  **3.2/day** → 150 contracts in **~47 days**. This is the transient: 8 of 15
  filled markets have not settled yet, so the settled count lags the real rate.
* **steady-state (filled-contract) rate:** 30 filled contracts in 3.13 days =
  **9.6/day** → 150 contracts in **~16 days**, plus the settlement lag once.

**Estimate: ~2.5 to 3 weeks** for the treatment arm to reach 150 settled
contracts from a fresh v2 start, if the opportunity rate holds. The control
reaches the same floor in **~1 day** (146 settled contracts/day), so the pair
completes when the treatment does.

That is operationally realistic. **The floor is reachable.**

### But reaching it does not decide the gate

This is the part the operator should weigh, and it is not an argument for
changing anything.

The v2 gate's binding clause is `delta.live_cents_per_contract >= 1.0` — an
**unpaired difference between two arms**. Per-contract P&L on these books has
σ ≈ **30–35¢** (measured: 35.1¢ live on theta4, 29.0¢ on theta4 paper at n=217;
mmsell's price mix is similar). At 150 contracts per arm the standard error of
that difference is

> σ·√(2/n) = 32·√(2/150) = **±3.70¢**

against a **1.0¢** threshold. A pair with *no true difference at all* clears
`delta >= 1.0` roughly **39%** of the time at that sample size. Getting the
delta's standard error down to ~⅓ of the threshold would take on the order of
**19,000 contracts per arm** — at 9.6 contracts/day, several years.

This is not a new discovery about this gate; it is the same finding
`docs/BOOK_REGISTRY.md` already records for the `mmsell10a`/`mmsell10b` offset
A/B: *"the between-arm P&L race never became decidable and never could have
(−2.77¢, 95% CI [−7.09, +1.54], needs ~30,391 contracts/arm) — which the doc
predicted, and is why the twin-paired read was pre-registered as primary."*

The mmsell v2 gate has two clauses and they are not equally powered:

| clause | comparison | 150 contracts is |
|---|---|---|
| `twin_live_winrate_gap_pp <= 1.0` | **paired** — twin sees the same tickers, so market-level variance cancels | plausibly informative |
| `delta.live_cents_per_contract >= 1.0` | **unpaired** between arms | ~150× too small |

**Nothing here is a proposal.** The floor and thresholds stay as approved. The
operator asked whether reaching the floor is realistic; the honest answer is that
reaching it takes about three weeks, and that the paired clause is the one those
three weeks will actually inform.

---

## 2. `realized_tail_hit_ratio_vs_modeled` — validation

### 2.1 What the metric measures

It has **no implementation**, so what follows is what the name, the registry
description ("observed tail-hit frequency over the model's prediction") and the
thesis jointly specify:

> (share of sold tails that resolved in-the-money) ÷ (mean modeled probability of
> those same tails)

1.0 = the model priced the tails exactly right. Above 1.0 = tails hit more often
than modeled, i.e. the model under-prices tail risk — the specific failure that
killed the original theta family.

### 2.2 Does it correspond to the thesis?

Yes — more directly than theta4's P&L clause does. `docs/THETA_THESIS.md` states
the family's pre-committed rule:

> **Decision rule (pre-committed):** … Keep a book only if its P&L/trade is
> positive **AND its realized tail-hit rate is at or below its modeled
> probability**; kill the others.

The metric is precisely the operationalization of that second condition. Note the
thesis's bar is **≤ 1.0**. The gate's is **≤ 1.25**. See §2.6.

### 2.3 What data it trusts — and the live gap

The modeled probability is `paper_trades.model_probability`, stamped at entry by
`theta/tracker.py` (`model_probability=p_book` on both `create_paper_trade` and
`open_twin_entry`). Coverage is complete on paper: **217/217** theta4 rows and
**44/44** twin rows carry it.

**`live_orders` has no `model_probability` column.** At live scope the modeled
side is therefore not directly available. Measured recovery by joining live
orders to a same-ticker paper row within ±600s:

| join target | matched |
|---|---|
| paper `theta4` | **0 of 103** |
| twin `theta4_pt3` | **44 of 103** — and exactly 44 live orders exist since the twin was armed (2026-08-12) |

So the modeled probability is recoverable for **100% of live orders inside the
twinned window and 0% before it**. This has a consequence for the v2 design that
is easy to miss: it makes the **same-instant twin a structural requirement of the
metric**, not merely the control design. Without a twin covering the whole live
window, this clause is uncomputable at live scope by construction.

It also means the metric's real definition at live scope is *"live outcomes
against the twin's modeled probability"* — a cross-source join. That belongs in
the v2 contract in writing, not buried inside a provider.

### 2.4 Censoring and open positions

theta4 currently has **0 open live markets**, **17 markets with orders that never
filled**, and 86 settled. Both exclusions matter and both bias the same way if
mishandled: an unfilled order was never a tail we sold, and an open position's
outcome is unknown. Counting either as "did not hit" pushes the ratio **down**,
i.e. **toward passing**. Any implementation must exclude and count them, as the
new live providers do for their own exclusions.

### 2.5 Look-ahead and calibration leakage

No look-ahead: the modeled probability is stamped at entry and the outcome
arrives at settlement.

There is a **selection** effect, and it is intentional. theta only enters when
`edge = p_market − p_model` clears a threshold (6¢ for theta4), so the entered set
is deliberately the subset where the model most disagrees with the market. The
ratio is therefore conditional on that selection. That is the right read for a
gate — "were we right where we actually bet" — but it is **not** a general
calibration statistic and must never be reported as one.

No calibration leakage in the mmsell sense: unlike `realizable_cents_per_trade`,
this metric does not pass through a fill calibration at all.

### 2.6 Was ≤ 1.25 genuinely pre-registered before outcome knowledge?

**For theta4, yes.** Traced through git:

| date | commit | event |
|---|---|---|
| 2026-07-10 | `1e24d19` | theta4 created as a paper book |
| 2026-07-11 | `ad949e6` | theta4 edge loosened 10¢→6¢ *because it had **0 trades*** at edge=10 |
| 2026-07-11 | `f7de452` | BOOK_REGISTRY adds the gate: `> 0` **AND** `realized-tail-hit ≤ 1.25× modeled` |
| 2026-07-30 | `a78b379` | theta4 goes live |

The bar was written when theta4 had essentially no evidence, ~3 weeks before it
traded real money. It is not fitted to theta4's outcomes.

**But it is a 25% relaxation of the family's own stated rule** (≤ 1.0), made with
the earlier theta family's failure data already in hand — including the recorded
"40–55min entries carried the losses (−11.6¢/ct, 40% tail-hit, n=30)". Worth
noting without over-reading: `1.25` is also the `vol mult` column value for
`theta3` in the thesis's variants table. I cannot demonstrate transcription and
do not claim it; the coincidence is recorded so the operator can weigh it.

**Verdict: pre-registered for theta4, but a relaxation of the stated family rule
rather than an inheritance of it.** A v2 that re-registers this threshold should
do so deliberately, from the thesis, rather than by copying the number forward.

### 2.7 Is the script trustworthy enough to be the canonical provider?

**No — and not because it is untrustworthy. Because it does not implement this
metric.**

`scripts/theta_fill_model.py` computes a **maker-fill realizable projection**:
per-price-cell fill rates and realizable P&L, projected over a book's entry-price
histogram, borrowed from mmsell3's live calibration while theta has no live
history. It is the theta counterpart of `mmsell_fill_model.py`.

It contains **no tail-hit logic at all** — searching it for `tail`, `hit`,
`modeled` or `percentile` returns nothing. Nor does anything else in the
repository: `realized_tail_hit_ratio_vs_modeled` appears only in its registry
declaration and in the imported gate.

The registry's `reference="scripts/theta_fill_model.py (docs/THETA_THESIS.md)"`
for this metric is a **mis-citation**. There is no reference implementation for
this metric anywhere. That is a finding in its own right: a BLOCKED_DATA message
has been telling readers to consult a script that cannot answer the question.

### 2.8 Recommendation: **redesign the tail clause in v2** (option 2)

Not option 1 (keep and canonicalize), and not option 3 (omit).

**Why not keep-and-canonicalize:** there is nothing to canonicalize. No reference
implementation exists, so a provider built now would have no parity check against
a trusted second answer — precisely the situation in which an implementation bug
is invisible. "Canonicalize the reference" is not available as an action.

**Why not omit:** the tail clause is the *more* thesis-faithful of theta4's two
clauses, and it is the only one that survives the fee boundary — a frequency ratio
is immune to the 2026-08-11 maker-fee re-baseline that forces the
"read `> 0` as `> +0.87¢`" caveat onto the P&L clause. Decisively, §3 shows the
P&L clause **cannot decide theta4 in any realistic horizon**: confirming its
observed +1.29¢/contract edge needs roughly 3,700 contracts, about nine months.
Dropping the tail clause leaves a gate that cannot conclude.

**What redesign means concretely** — for the operator to approve, not for me to
enact:

1. define the metric explicitly in the v2 contract: numerator (settled live
   markets whose sold tail resolved in-the-money), denominator (sum of modeled
   probabilities over the same markets), and the exclusions from §2.4;
2. state in the contract that the modeled probability comes from the
   **same-instant twin** — which makes the twin structural, per §2.3;
3. re-register the threshold from the thesis deliberately (the family rule is
   ≤ 1.0; the imported bar is ≤ 1.25), rather than carrying 1.25 forward
   unexamined;
4. build the provider with pinned tests rather than a script parity check, since
   no script exists to check against;
5. note that a `MODEL` platform revision breaks this metric's comparability — the
   denominator is the model's own output — even though a `FEE_MODEL` revision
   does not.

**This recommendation was not chosen on what makes theta4 pass.** At the current
sample it cannot: the observed paper ratio is **0.0922 / 0.0792 = 1.164**, and at
n=217 the ratio's own standard error is **±0.231**. `1.16 ± 0.23` cannot be
distinguished from 1.0 or from 1.25. The clause is currently uninformative
against *either* bar — which is an argument for making it computable and
adequately powered, not for choosing whichever bar it would clear.

---

## 3. theta4 v2 — candidate live sample floors

**Not 80.** That number was registered against *paper trades*, and theta4 already
holds 86 settled live markets — a bar the book has cleared before the gate opens
is not a bar. Nothing below is anchored on the existing live count.

### 3.1 Inputs

| input | value | source |
|---|---|---|
| per-contract P&L σ | **35.1¢** live (260 contracts) / **29.0¢** paper (n=217) | production |
| observed live edge | **+1.292¢/contract** (+$3.36 / 260) | production |
| entry price band | yes 8–21¢ (p10–p90) | production |
| modeled tail probability | **0.0792** | production |
| observed tail-hit rate | **0.0922** (paper, n=217) | production |
| contract rate | **13.7/day** (260 contracts, 2026-07-31 → 08-18) | production |

σ = **32¢** is used below as the central estimate; the candidate table shows the
29–35¢ spread.

### 3.2 One structural point first

The proposed clause is `live_cents_per_contract > 0` — a **point estimate**. A
sample floor does not control false promotion for a point estimate: at a true edge
of exactly zero, the sample mean exceeds zero **50% of the time at every n**. The
floor only controls how often a genuinely *negative* book passes.

If the operator wants false-promotion risk actually bounded, the clause form has
to carry it — e.g. a one-sided 95% lower bound `> 0`, which caps false promotion
at 5% by construction and leaves n to set power. Both readings are priced below.
This is a v2 design question, raised because the operator asked for floors derived
partly from false-promotion risk; it is not a proposal to alter anything existing.

### 3.3 Candidates

Sized as `n = ((z₀.₉₅ + z₀.₈₀)·σ / MDE)²` — one-sided 95%, 80% power — where MDE
is the smallest per-contract edge worth promoting on.

| | **A — screening** | **B — balanced** | **C — decisive** |
|---|---|---|---|
| minimum effect worth detecting | **+5¢/contract** | **+3¢/contract** | **+2¢/contract** |
| floor (σ=32¢) | **250 contracts** | **700 contracts** | **1,600 contracts** |
| range over σ 29–35¢ | 208 – 305 | 578 – 846 | 1,300 – 1,904 |
| time at 13.7 contracts/day | **~3 weeks** | **~7 weeks** | **~4 months** |
| capital cycled (~85¢/contract) | ~$215 | ~$600 | ~$1,360 |
| SD of total P&L over the run | ~$5 | ~$8 | ~$13 |
| a truly −1¢ book passes `mean > 0` | ~30% | ~20% | ~11% |
| settled markets at that n (≈3.02 contracts/market) | ~83 | ~232 | ~530 |
| tail-ratio SE at that n | ±0.38 | ±0.22 | ±0.15 |

**A — screening (250).** Cheapest in time and attention. Detects only a large
edge; a book performing at theta4's observed rate would not be distinguishable
from noise. Appropriate if the intent is "kill quickly if clearly bad" rather
than "promote confidently".

**B — balanced (700).** Roughly seven weeks. My recommendation if a single
number is wanted.

Note the tail clause counts **markets**, not contracts — a tail either hits or it
does not, once per market, and the ~3 contracts held on one market are perfectly
correlated for that event. theta4 runs ~3.02 contracts per settled market, so a
contract-denominated floor buys only a third as many tail observations. Even at
candidate C the ratio's standard error is ±0.15, which separates 1.0 from 1.25
only barely. **If the operator wants the tail clause to be the deciding one, its
floor should be pre-registered in settled markets, separately from the P&L
clause's contract floor.**

**C — decisive (1,600).** Four months. The P&L clause becomes genuinely
informative; the tail clause reaches ±0.15, which is usable but not comfortable.

### 3.4 The number the operator most needs

**theta4's observed edge is below all three MDEs.** Confirming a true +1.29¢
edge at 80% power needs ~**3,700 contracts ≈ 9 months** (~4,500 at σ=35¢).

So under any floor above, a book performing *exactly as theta4 has actually
performed* is not promotable. That is not an argument for lowering the floor —
lowering it would only mean promoting on noise. It is the real decision in front
of the operator, and it has three honest answers:

1. **accept a screening floor (A)** and treat theta4 v2 as a kill-test rather
   than a promotion path;
2. **accept the horizon (B or C)** and let it run, noting the capital at risk is
   tens of dollars — the cost is time and attention, not money;
3. **decide theta4 on the tail clause instead**, which asks the thesis's actual
   question and is better behaved across the fee boundary — which is what §2.8
   recommends making possible.

The money is genuinely small: at 1,600 contracts the standard deviation of total
P&L is about **$13**. What a longer floor really costs is months of operator
attention on a book whose measured edge is ~1.3¢/contract.

---

## 3.5 Provider production verification

Run through the ops channel against production, `2026-08-18`.

**Values match an independently written SQL reference exactly.** For
`mmsell-scheduled-settle-live/v1/e1/price_ceiling` at `kind=live`:

| | provider | independent SQL |
|---|---|---|
| settled markets | 119 | 119 |
| settled contracts | 235 | 235 |
| realized P&L | $0.8195 | $0.8195 |
| ¢/contract | **0.3487** | **0.3487** |
| contested, excluded | 2 | 2 |

**The addressing refusal was verified on the real malformed scope.** Asking for
`live_settled_contracts` at `kind=paper` on the actual imported contract returns:

```
value:       None  (contracts)   n=0   MISSING
reason:      'live_settled_contracts' measures real-money execution and is only
             defined at deployment_kind='live'; this clause addresses 'paper'.
             The provider will not substitute a different deployment kind —
             correct the gate's addressing
    addressing_error: True
    strategy_tags: []
```

That is the malformed contract's defect, reproduced from production through the
canonical code path rather than argued.

Two refinements the production run exposed, both now fixed:

* the control arm reported **82 "unpriced" markets**, which reads as a data
  incident. They are almost entirely maker orders that never filled — normal, and
  the largest single exclusion on any maker book. Unfilled orders are now counted
  separately from settled positions with a missing price; merging two exclusions
  with opposite meanings makes a healthy book look broken.
* `live_cents_per_contract` reported `n` in **markets** while its value is per
  **contract**. It now reports contracts, so `value × n` reproduces the realized
  total.

---

## 4. What was deliberately not done

No threshold changed. No frozen gate edited. No lifecycle transition. No
deployment armed, retired, paused or re-armed. No imported history rewritten. The
mmsell floor stays at 150 and its thresholds stay as approved, including where
§1 shows one of its clauses is underpowered at that floor. The theta4 floor is
**not** chosen here — three candidates are priced, and the operator
pre-registers one before v2 is frozen.
