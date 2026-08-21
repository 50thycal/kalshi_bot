# Research Lab — MMSELL universe/regime deconfounding

**Question.** How much of the apparent `Lmmsell8`-vs-`Lmmsell10` difference is explained by
crypto-vs-non-crypto universe exposure rather than by the scheduled-settlement entry rule?

**Evidence.** Historical, paper and twin only. 53,876 settled mmsell paper rows,
2026-07-03 → 2026-08-21, plus the live order/fill tape and the live decision tape. No live
restart, no strategy parameter changed, no Version created or frozen.

**Reproduce.** `{"type":"script","name":"mmsell_deconfound_study"}` over the ops channel
(`scripts/mmsell_deconfound_study.py`). Every number below is from that run.

The unit of evidence throughout is the **settled market**, not the trade: contracts on one
market share one settlement, so trades within a market are not independent.

---

## 0. The comparison was confounded three ways, not one

Read straight off `mmsell_variants` in `kalshi_bot/config.py`:

```
mmsell8  / Lmmsell8    lo=5, hi=12, only=BTCD+ETH+ASG+HRDERBY
mmsell10 / Lmmsell10   lo=5, hi=10, maxyes=7
```

| factor | treatment (`Lmmsell8`) | control (`Lmmsell10`) |
|---|---|---|
| series universe | 4-series allowlist | unrestricted |
| entry-price band | 5–12¢ (mean entry **9.61¢**) | effective 5–7¢ (mean entry **6.6¢**) |
| settle-mode mix | 74% scheduled, 0% in-play | 10.5% scheduled, 70% in-play |
| realized hold, median | **19.3 h** | **1.7 h** (non-crypto slice) |

A delta between these two books has no single interpretation. Every one of those four rows
moves P&L on its own, and they move together. **This is a design defect, not a power problem:
no sample size separates factors that never vary independently.**

---

## 1. `scheduled_settle` as implemented is a proxy for crypto — the *concept* is not

`only=` is a case-insensitive **series-substring allowlist**, not a settle-mode filter. What it
actually admits, across every mmsell book:

| series | regime | settle mode | rows |
|---|---|---|---|
| KXBTCD | Crypto | scheduled | 2,325 |
| KXETHD | Crypto | scheduled | 179 |
| KXETH | Crypto | *unknown* | 91 |
| KXMLBASGHR, KXMLBHRDERBY* … | MLB | **in_play** | 355 |

- **88.0%** of `mmsell8`-eligible flow (2,605 / 2,960 rows) is crypto.
- The remaining 12% is All-Star-Game and Home-Run-Derby props, which are **`in_play`, not
  scheduled** — the allowlist admits markets that contradict its own name.
- Of the **5,176** non-crypto scheduled-settle rows in the history, the allowlist admits
  **0 (0.0%)**. Series it structurally cannot see: KXRAIN (867), KXWTI (612), KXGOLDD (491),
  KXWTIW (417), KXINXU (344), KXRT (324), KXNATGASD (324), KXNASDAQ100U (267), KXAAAGASD,
  KXAAAGASW, KXBRENTW …

So the answer has two halves, and they point opposite ways:

> **The scheduled-settlement *concept* is not a crypto proxy** — the non-crypto scheduled
> universe is twice the size of the crypto one. **`mmsell8`'s *implementation* of it is** —
> it reaches none of that universe and is 88% crypto by construction.

A precision note: these are crypto **dailies** (`KXBTCD`), not hourlies. Median hold is 14.8 h.
"Crypto hourlies" is the theta universe, not this one.

---

## 2. Same logic, different universe — no long-run asset-class penalty

`mmsell5` and `mmsell8` are the cleanest natural experiment in the history: identical band
(`lo=5,hi=12`), identical management, differing **only** in the `only=` list —
TOTAL+SPREAD (non-crypto) against BTCD+ETH (crypto).

| window | `mmsell8` (crypto) | `mmsell5` (non-crypto) | delta | 95% CI | p |
|---|---|---|---|---|---|
| full history | +0.98¢ (n=180) | +0.81¢ (n=2,791) | **+0.17¢** | [−3.92, +4.25] | 0.937 |
| window-matched | +0.57¢ (n=170) | +0.92¢ (n=2,707) | **−0.36¢** | [−4.66, +3.95] | 0.872 |

**Over five weeks, crypto and non-crypto perform the same under identical logic.** Whatever
happened in August is not a standing property of the asset class.

---

## 3. Same book, different universe — the penalty is real but regime-shaped

Books with no `only=` filter see both universes under one rule, so their internal split is an
unconfounded asset-class effect.

| book | crypto | non-crypto | delta | 95% CI | p |
|---|---|---|---|---|---|
| `mmsell10` | −0.02¢ (166) | +0.70¢ (2,910) | −0.73¢ | [−4.44, +2.99] | 0.702 |
| `mmsell9` | −0.26¢ (130) | +0.58¢ (1,198) | −0.84¢ | [−5.18, +3.49] | 0.703 |
| `mmsell6` | −1.61¢ (204) | +1.32¢ (3,882) | −2.93¢ | [−6.79, +0.93] | 0.136 |
| `mmsell7` | −1.02¢ (182) | +5.61¢ (547) | −6.63¢ | [−10.96, −2.30] | **0.003** |
| `mmsell` | −6.08¢ (312) | +1.81¢ (8,097) | −7.89¢ | [−12.46, −3.32] | **0.001** |
| **`Lmmsell10`** (Aug 15–20) | **−11.09¢ (51)** | **+0.72¢ (1,167)** | **−11.81¢** | [−22.49, −1.13] | **0.030** |

Every sign is the same and the two largest samples are significant, so crypto **is** the worse
universe. But the magnitude is 1–3¢ over July–August and **−11.8¢ in the live window alone**.
Reconciling this with §2: the penalty is small on average and concentrated in the August
rally — a regime effect that the live canary happened to be armed into.

The tail confirms it. Long-run crypto loss rate is **6.0–8.8%**; in the live window it is
**17.6%** (`Lmmsell10` crypto) and **32.3%** (`Lmmsell8`) — three to five times higher, at
roughly double the dispersion (sd 38–48¢ against 24–28¢).

---

## 4. Same universe, different rule — the deconfounded read

| comparison | treatment | control | delta | 95% CI | p |
|---|---|---|---|---|---|
| **naive** (the gate's number) | `Lmmsell8` −22.80¢ (31) | `Lmmsell10` all +0.35¢ (1,161) | **−23.15¢** | [−40.22, −6.07] | **0.008** |
| **deconfounded** (crypto fixed) | `Lmmsell8` −22.80¢ (31) | `Lmmsell10` crypto −11.09¢ (51) | **−11.71¢** | [−31.76, +8.34] | 0.252 |
| universe alone, no rule change | `Lmmsell10` crypto −11.09¢ | `Lmmsell10` non-crypto +0.88¢ | −11.97¢ | [−22.65, −1.28] | **0.028** |

**49% of the naive gap is asset class alone**, and once it is removed the remaining difference
is **not distinguishable from zero**. The number the frozen v2 gate would have decided on was
significant; the number that answers the actual question is not.

That is the finding in one line: **the gate would have measured a real effect and attributed it
to the wrong cause.**

---

## 5. The two books barely share a market

The cell both filters admit is `mmsell8`-eligible **and** yes-price 5–7¢:

| pair | treatment in shared cell | control in shared cell |
|---|---|---|
| `mmsell8` / `mmsell10` | 57 / 180 (**31.7%**) | 120 / 3,076 (**3.9%**) |
| `Lmmsell8` / `Lmmsell10` | 8 / 31 (**25.8%**) | 25 / 1,218 (**2.1%**) |

**97.9% of the control's flow is in markets the treatment structurally cannot trade**, and
74.2% of the treatment's flow is in markets the control cannot. The naive delta is almost
entirely a comparison of disjoint populations.

Inside the shared cell, both books lose: `Lmmsell8` −56.23¢ (n=8) against `Lmmsell10` −21.47¢
(n=25), delta −34.76¢ (p=0.089) — and note `Lmmsell10` is **+0.35¢ overall** but **−21.47¢** in
this cell. The cell is bad, not the book.

---

## 6. Fill rate and fill selection, by asset class

Order → fill, from the live order tape (2026-08-15 →):

| live tag | asset | markets ordered | filled | rate |
|---|---|---|---|---|
| `Lmmsell8` | crypto | 26 | 22 | **84.6%** |
| theta4 | crypto | 48 | 38 | **79.2%** |
| `Lmmsell10` | non-crypto | 386 | 269 | 69.7% |
| `Lmmsell10` | crypto | 19 | 12 | 63.2% |

A **higher** fill rate on the same kind of resting maker order is the classic adverse-selection
signature: the order is lifted precisely when someone wants that side.

Fill-selection haircut (twin's own P&L, unfilled minus filled — execution price never enters):

| twin | asset | filled | unfilled | haircut | 95% CI |
|---|---|---|---|---|---|
| `mmsell10a_pt2` | crypto | +6.10¢ (8) | +7.61¢ (8) | +1.51¢ | [+0.33, +2.68] |
| `mmsell10a_pt2` | non-crypto | +2.23¢ (154) | +4.99¢ (96) | +2.75¢ | [−1.62, +7.13] |
| `mmsell10b_pt3` | non-crypto | −1.37¢ (99) | +1.20¢ (114) | +2.57¢ | [−4.42, +9.56] |
| `Lmmsell8_pt3` | crypto | −31.86¢ (17) | −19.17¢ (10) | +12.68¢ | [−25.82, +51.19] |
| `Lmmsell10_pt3` | non-crypto | +4.58¢ (65) | −0.38¢ (74) | −4.97¢ | [−12.66, +2.73] |

**The mmsell family's haircut is +1 to +3¢ in both universes** — an order of magnitude below
theta4's **+26.17¢**. One cell (`mmsell10a_pt2` crypto) excludes zero, so a small real haircut
exists; nothing here resembles theta4's. **Fill selection is a theta problem, not a universal
maker-book problem**, and it is not what separates the mmsell books.

---

## 7. The other requested distributions

**Time to settlement** — the largest structural difference between the universes:

| book | asset | mean | p50 | p90 | ≤2 h |
|---|---|---|---|---|---|
| `mmsell8` | crypto | 30.7 h | 14.8 h | 101.7 h | 1.7% |
| `mmsell10` | crypto | 35.7 h | 12.6 h | 102.6 h | 1.8% |
| `mmsell10` | non-crypto | 10.1 h | 2.0 h | 22.6 h | **49.2%** |
| `mmsell5` | non-crypto | 3.7 h | 1.4 h | 3.2 h | **70.5%** |

Crypto positions are held roughly **ten times longer**. Half the control's flow settles inside
two hours; almost none of the treatment's does. Longer exposure accumulates more of whatever the
underlying is doing — which is why a multi-day rally lands so asymmetrically on these two books.

**Entry price** — `mmsell8` 9.47¢ (p10 6, p90 13) against `mmsell10` 6.59¢ (p10 6, p90 7).
The treatment sells tails ~2.9¢ richer, an independent economic difference.

**Model edge** — `paper_trades.edge` is **0.000 for 100% of mmsell rows**. mmsell is a
price-band rule, not a model-anchored one, so there is no edge distribution to compare. Stating
that is the honest answer; there is no edge evidence for this family.

**Market-family composition** — `mmsell8` is a single cell: Crypto / scheduled / price_strike,
92.2% of its flow, +0.73¢. `mmsell10` spans 27 cells ≥20 markets, the largest being MLB /
in_play / player_prop at 24.8%. Its own Crypto / scheduled / price_strike cell is 5.1% at
+0.25¢ — and in the live cohort that same cell reads **−11.22¢** while the book overall is flat.

---

## 8. Can MMSELL be deconfounded historically? Yes — a valid successor already has history

`mode=` is an existing knob (`Settings.mmsell_variant_list`) that filters on the **taxonomy**
rather than on series substrings. Books already running it share `mmsell10`'s exact band:

| book | spec | scheduled | crypto | markets | c/ct |
|---|---|---|---|---|---|
| `Tmmsell5` | `lo=5,hi=10,maxyes=7,mode=scheduled+discrete,xmtype=…` | 89.5% | **35.8%** | 285 | +0.66 |
| `Tmmsell1` | `lo=5,hi=10,maxyes=7,mtype=price_strike` | 100% | **47.0%** | 217 | +0.76 |
| `Wmmsell4` | wide band, scheduled | 100% | 47.2% | 106 | +0.83 |
| — vs — | | | | | |
| `mmsell8` | `lo=5,hi=12,only=BTCD+ETH+ASG+HRDERBY` | 92.2% | **100.0%** | 180 | +0.98 |

`Tmmsell5` and `Tmmsell1` are what "scheduled settle" should have meant: the **same band as the
control**, a taxonomy-driven rule, and a universe that is roughly half non-crypto — so the rule
and the asset class stop being the same variable.

Run against `mmsell10` over the common window (2026-08-04 → 08-20), **within one universe at a
time**:

| contrast | treatment | control | delta | 95% CI | p |
|---|---|---|---|---|---|
| `Tmmsell5` vs `mmsell10`, **crypto** | −1.57¢ (102) | −1.86¢ (111) | **+0.29¢** | [−7.02, +7.60] | 0.938 |
| `Tmmsell5` vs `mmsell10`, **non-crypto** | +1.91¢ (183) | +0.56¢ (2,594) | **+1.35¢** | [−1.76, +4.46] | 0.395 |
| `Tmmsell1` vs `mmsell10`, **crypto** | −1.57¢ (102) | −1.86¢ (111) | **+0.29¢** | [−7.02, +7.60] | 0.938 |
| `Tmmsell1` vs `mmsell10`, **non-crypto** | +2.83¢ (115) | +0.50¢ (2,570) | **+2.33¢** | [−1.17, +5.83] | 0.192 |
| pooled, universe-confounded (for contrast) | +0.66¢ (285) | +0.46¢ (2,705) | +0.20¢ | [−2.62, +3.02] | 0.887 |

> **Deconfounded, a scheduled-settle rule is worth about nothing inside crypto (+0.29¢) and
> possibly +1.4 to +2.3¢ outside it — neither significant.** Both signs are the opposite of the
> naive `Lmmsell8`-vs-`Lmmsell10` story, which is what a confound does: it puts the universe's
> effect on the rule's account.

The two treatment books report an identical crypto slice because crypto ladder markets are all
`price_strike` **and** all `scheduled`, so both filters select the same 102 markets. That is a
property of the universe, not a bug — and it is the reason a `mode=`-defined treatment can still
be compared to itself across universes while `only=BTCD+ETH` never could.

**So: the scheduled-settle idea does not need a different control.** It needs to stop being
defined by a four-series allowlist. With `mode=` the 2×2 is constructible, and three of its four
cells already have paper history.

---

## 9. Sample requirement

Per-market sd measured over the cheap-band books: **25.8¢ crypto, 23.2¢ non-crypto**. Two-sample,
equal cells, 80% power, settled **markets per arm**:

| detect | crypto @95% | crypto @99% | non-crypto @95% | non-crypto @99% |
|---|---|---|---|---|
| +1¢ | 10,488 | 13,407 | 8,483 | 10,845 |
| +2¢ | 2,622 | 3,352 | 2,121 | 2,711 |
| +3¢ | 1,165 | 1,490 | 943 | 1,205 |
| +5¢ | 420 | 536 | 339 | 434 |

At the frozen v2 gate's **291 markets per arm** and the standing one-sided 99% bound, the
smallest resolvable effect is **≈6.1¢ (non-crypto) to ≈6.8¢ (crypto)**. The deconfounded rule
effects in §8 are +0.3¢ to +2.3¢ — an order of magnitude below that. **A live canary cannot
resolve this question at any plausible cadence.** Paper can: `mmsell10` alone accrues ~3,000
settled markets in five weeks, and the non-crypto arms reach 1,000 in under two.

---

## 10. What is NOT answerable from history

1. **`Lmmsell10`'s crypto slice has 51 settled markets and its twin has 7.** The within-book
   crypto haircut cannot be estimated in the live cohort.
2. **The treatment has no non-crypto arm at all.** `Lmmsell8` is 100% crypto and `mmsell8` is
   100% crypto; the 4-series allowlist never admitted one. The scheduled-rule-on-non-crypto cell
   must be *generated*, not recovered.
3. **`Tmmsell5`/`Tmmsell1` are nested inside `mmsell10`'s universe**, so the §8 contrasts are
   subset-vs-superset tests, not randomized ones — the control contains the treatment's markets.
   They bound the rule effect and they are honest about the universe; they do not isolate the
   rule the way a disjoint randomized split would.
4. **No live-fill counterfactual exists for the non-crypto scheduled universe** — those series
   were never armed live, so fill rate and haircut there are unknown.
5. **Asset class and hold time are themselves collinear** (crypto p50 14.8 h vs non-crypto
   1.7 h). Even a perfect universe control leaves duration confounded with it.

## 11. The paper-only experiment that generates the missing counterfactual

§8 shows the question is answerable in principle but answered only weakly by the nested books we
happen to have. A **2×2 paper design** answers it directly. All four arms share `lo=5,hi=10,
maxyes=7` — the control's band — so band and universe stop travelling together:

```
                 crypto universe                  non-crypto universe
scheduled   mode=scheduled,only=BTCD+ETH+SOL   mode=scheduled,skip=BTCD+ETH+SOL+DOGE+XRP
unfiltered  only=BTCD+ETH+SOL                  skip=BTCD+ETH+SOL+DOGE+XRP
```

Every arm is expressible with knobs that already exist; no code change is required to run it.
Both margins become estimable, and so does the **interaction** — whether the scheduled rule helps
*more* in one universe — which is the quantity the current design cannot even express.

What this adds over §8: those arms are **disjoint**, where `Tmmsell5`/`Tmmsell1` are nested
inside `mmsell10` and share its markets. A disjoint split makes the delta a difference between
independent samples rather than between a set and its superset.

**Pre-register the supply constraint, do not discover it.** At observed cadence the two
non-crypto arms reach ~1,000 settled markets in under two weeks (`mmsell10` non-crypto runs
~86/day). The crypto arms are supply-limited to **~35 markets/week**, so a +2¢ effect inside
crypto needs ~2,700 markets per arm at 99% — over a year. **The crypto cell is not resolvable at
2¢ on any horizon worth planning around**, and the design should say so up front and set its
detectable effect where the supply actually lands (≈+5¢ at ~530 markets/arm, ~15 weeks) rather
than let a floor be chosen after the data arrives.

## 12. What was deliberately not done

No aggregate `Lmmsell8` − `Lmmsell10` treatment effect is reported as a treatment effect; that
number is the confound and appears only labelled as such. No threshold was moved and no
strategy parameter changed. No Version was created or frozen — in particular the 291-market
delta gate is **not** frozen. Nothing was retired, re-armed, promoted or paused. No live
restart. The four-arm design in §11 is a proposal, not a registration.
