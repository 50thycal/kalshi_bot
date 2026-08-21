# Research Lab — MMSELL 2×2 paper design (pre-registration draft)

Successor to the invalid `Lmmsell8`-vs-`Lmmsell10` live A/B, per
`RESEARCH_MMSELL_UNIVERSE_DECONFOUNDING.md`. **Paper only. Not registered, not running.**

> ## STATUS: RETURNED FOR A DECISION — one cell of the 2×2 does not exist
>
> The brief said to return the design if anything about the four-cell construction is ambiguous.
> It is, and the ambiguity is structural rather than a matter of taste: **the crypto ×
> non-scheduled cell has no market supply at all.** §2 measures it. Everything else in this
> document is complete and ready to run the moment §2 is decided.

---

## 1. What the experiment is for

**Primary question.** Does restricting a maker-sell book to **scheduled-settlement** markets
improve its economics, holding the universe and the entry-price band constant?

The previous design could not ask this. `Lmmsell8` and `Lmmsell10` differed in universe, band and
settle-mode mix at once, so 49% of their apparent gap was asset class and the remainder was not
distinguishable from zero.

**Treatment definition — the taxonomy, never a series list.** `mode=scheduled` selects on
`kalshi_bot/mmsell/market_types.py`. `only=BTCD+ETH+ASG+HRDERBY` does not: it is a series-substring
allowlist that is 88% crypto, reaches **0 of the 5,176** non-crypto scheduled-settle rows in the
history, and admits ASG/HRDERBY props that are `in_play` — markets that contradict the name of the
rule they define. `only=` is not used as a treatment definition anywhere below.

---

## 2. The blocking ambiguity: cell B is empty

Candidate supply in the common 5–7¢ band, measured from `mmsell_candidate_ticks` over
2026-07-19 → 08-21 and classified through the taxonomy:

| crypto series | markets in band | settle mode |
|---|---|---|
| KXBTCD | 70 | scheduled |
| KXBTC | 37 | scheduled |
| KXETHD | 8 | scheduled |
| KXBTCMAXMON | 4 | scheduled |
| KXSOLD, KXXRPD, KXXRPMAXMON, KXSOLMAXMON, KXETHMAXMON, KXETH | 21 | **unclassified** |

| crypto supply by settle mode | markets | per week |
|---|---|---|
| scheduled | 119 | 24.5 |
| in_play | **0** | **0** |
| discrete | **0** | **0** |
| unclassified (taxonomy gap) | 21 | 4.3 |

**There is no non-scheduled crypto.** The 21 "non-scheduled" markets are not settled differently —
they are scheduled instruments the `SERIES_TYPES` table has no prefix for yet. A `mode=scheduled`
filter would exclude them because nobody added the row, so a crypto treatment-vs-control contrast
built that way would measure **taxonomy coverage**, not settlement mode.

So the 2×2 as briefed has a structurally empty cell:

|  | crypto | non-crypto |
|---|---|---|
| **scheduled** | A — 24.5/week | C — supply below |
| **non-scheduled** | **B — does not exist** | D — supply below |

### The three ways forward

| option | what it measures | cost |
|---|---|---|
| **1. Drop to a 1×2 in non-crypto** (recommended) | the rule effect, cleanly, inside one universe | the crypto margin is not estimated — but §3 shows it was never estimable anyway |
| 2. Keep the 2×2, fill B from the taxonomy gap | rule effect in non-crypto; **taxonomy coverage** in crypto | an interaction term that does not mean what it is named |
| 3. Fix the taxonomy first, then re-measure | possibly a real B, if any crypto series is genuinely non-scheduled | `SERIES_TYPES` is read by `Tmmsell5`'s live `mode=` filter — a **shared semantic**, so a Platform Change Review, not a Research Lab edit |

**Recommendation: option 1.** §3's power arithmetic says the crypto cell could not have resolved a
plausible effect on any horizon worth planning, so the 2×2's crypto column was going to be
decorative even if cell B existed. A clean 1×2 in the universe that has supply answers the primary
question; the crypto margin is then a separate, explicitly under-powered question rather than a
silent passenger.

**Option 3 is a real finding regardless of which option is chosen**: six crypto series
(KXSOLD, KXXRPD, and four KX*MAXMON) are missing from the taxonomy, and any book using `mode=`
is silently excluding them today. That is worth fixing on its own merits, by the owning role.

---

## 3. Pre-registration (complete, pending §2)

### 3.1 Cells

All arms share `lo=5,hi=10,maxyes=7` — `mmsell10`'s exact band — so band never travels with
universe or rule. Written against knobs that already exist; **no code change is required to run
this.**

```
C  (treatment, non-crypto)   lo=5,hi=10,maxyes=7,mode=scheduled,skip=BTC+ETH+SOL+DOGE+XRP+CRYPTO
D  (control,   non-crypto)   lo=5,hi=10,maxyes=7,skip=BTC+ETH+SOL+DOGE+XRP+CRYPTO
A  (treatment, crypto)       lo=5,hi=10,maxyes=7,mode=scheduled,only=BTC+ETH+SOL+DOGE+XRP
B  (control,   crypto)       — DOES NOT EXIST, see §2
```

Under option 1, the design is **C vs D**, and A is recorded as a descriptive arm with no gate.

### 3.2 Eligible universe

Any Kalshi series the mmsell scan reaches, minus the crypto regime for C/D (per
`kalshi_bot/mmsell/regimes.py`, the same map both books already use). An **unclassified** series is
admitted by no `mode=` filter, so C is strictly narrower than D by construction — the arms are
nested, and §5's estimand is written for that.

### 3.3 Common price band

Yes price 5–7¢ effective (`lo=5, hi=10, maxyes=7`). Chosen because it is the incumbent control's
band, so D is directly comparable to `mmsell10`'s existing history rather than to nothing.

### 3.4 Primary metric

`delta.cents_per_contract` between C and D, per **settled market** — the independent unit, since
contracts on one market share one settlement. Paper fills, so no execution or fill-selection term
enters; this is a rule question, not an execution question.

### 3.5 Minimum useful effect

**+2¢/contract.** Rationale, all pre-existing: the deconfounded historical estimates in
`RESEARCH_MMSELL_UNIVERSE_DECONFOUNDING.md` §8 put the rule effect at +1.35¢ (`Tmmsell5`) and
+2.33¢ (`Tmmsell1`) in non-crypto. +2¢ sits inside that range and is the smallest effect that
would change what we trade. It is **not** chosen to be reachable — §3.6 shows what it costs.

### 3.6 Sample requirement and calendar time

Per-market sd is 23.2¢ (non-crypto). Two-sample, equal cells, 80% power, settled markets per arm:

| effect | @95% | @99% (the standing promotion bound) |
|---|---|---|
| +1¢ | 8,483 | 10,845 |
| **+2¢** | **2,121** | **2,711** |
| +3¢ | 943 | 1,205 |
| +5¢ | 339 | 434 |

`mmsell10` accrues ~86 non-crypto settled markets/day, so **arm D reaches 2,711 in ~32 days**.
Arm C is the scheduled subset — from `Tmmsell5`'s composition, ~64% of a scheduled book's flow is
non-crypto and scheduled markets are a minority of the stream, so C is the binding arm at an
estimated **~25–30 markets/day → ~95–110 days to 2,711**.

**Pre-registered consequence, stated now rather than discovered later:** at the +2¢ effect this
experiment takes about **three months**, and the treatment arm's supply is what sets that. If
three months is unacceptable, the effect size must move *before* the first trade, not after.

For contrast, the crypto arm A supplies ~24.5 markets/week; at +2¢ it would need ~2,700 markets,
i.e. **over two years**. That is the arithmetic behind §2's recommendation.

### 3.7 Stopping rule

- **Evidence floor:** 2,711 settled markets in the smaller arm. No verdict is read below it.
- **Bound:** one-sided 99%, per the standing sequential-testing decision (#245) — continuous
  evaluation at 95% costs ~18% lifetime false promotion; 99% holds it near 5%.
- **Maximum evidence horizon:** 4,000 settled markets per arm, **inclusive** (#247) — the last
  permitted look, after which the verdict is `HORIZON_EXHAUSTED` rather than an indefinite peek.
- **Early failure:** a separate `fail_any` floor of 800 settled markets per arm, so a materially
  negative treatment can be stopped without waiting for the promotion floor.
- **No re-interpretation:** the effect size, the metric and the floors above are fixed at freeze
  time and are not revisited after seeing results.

### 3.8 Estimand, given nesting

C's markets are a subset of D's universe, so C−D is a **subset-vs-superset** contrast: it answers
"does concentrating on scheduled markets beat the unrestricted book", which is the operational
question. It does **not** estimate a randomized treatment effect on a common population. If the
disjoint version is wanted, D must be redefined as `mode`-excluding-scheduled — cheap to do, and
worth deciding at freeze time rather than later.

---

## 4. Generating the missing cell

Arm C is the scheduled-settle/non-crypto cell that has never existed: `mmsell8`'s allowlist admits
**0** of the 5,176 non-crypto scheduled rows in the history. It is generated, not extrapolated —
that is the point of running it. The series it will reach, from the measured supply: KXRAIN,
KXWTIW, KXNATGASD, KXWTI, KXNASDAQ100U, KXINXU, KXGOLDD, KXAAAGASD, KXBRENTW.

---

## 5. What this design deliberately does not do

It does not use `only=` as the treatment. It does not report a pooled C-vs-D number as a treatment
effect while the universes differ. It does not choose the effect size to fit the available
calendar. It does not touch `SERIES_TYPES` — that is a shared semantic and a Platform Change
Review, even though §2 found a genuine gap in it. And it registers nothing: **no Version, no
epoch, no deployment and no arm exists for this until the §2 decision is made.**
