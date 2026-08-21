# Research Lab — MMSELL non-crypto paper design (pre-registration draft)

Successor to the invalid `Lmmsell8`-vs-`Lmmsell10` live A/B, per
`RESEARCH_MMSELL_UNIVERSE_DECONFOUNDING.md`. **Paper only. Not registered, not running, and
returned for approval before any arm is created.**

---

## 0. A correction to this document's own history

An earlier revision claimed the proposed 2×2's crypto cell "does not exist". **That was wrong,
and it misdescribed the design it was reviewing.** The 2×2 proposed in
`RESEARCH_MMSELL_UNIVERSE_DECONFOUNDING.md` §11 crossed universe with **`scheduled` versus
UNFILTERED**, not `scheduled` versus `non-scheduled`. An unfiltered crypto arm is perfectly
well populated — it is every crypto market in the band, ~140 in the 5–7¢ band over five weeks.
No cell was empty.

The measurement behind the claim was real; the conclusion drawn from it was not. What the supply
census actually shows is a **collapse** problem, which §2 states properly.

---

## 1. What the experiment is for

**Primary question.** Does restricting a maker-sell book to **scheduled-settlement** markets
improve its economics, holding the universe and the entry-price band constant?

**Treatment definition — the taxonomy, never a series list.** `mode=scheduled` selects on
`kalshi_bot/mmsell/market_types.py`. `only=BTCD+ETH+ASG+HRDERBY` does not: it is a
series-substring allowlist, 88% crypto, reaching **0 of the 5,176** non-crypto scheduled rows in
the history, and admitting ASG/HRDERBY props that are `in_play` — markets contradicting the name
of the rule that selects them. `only=` is not used as a treatment definition anywhere below.

---

## 2. Why the crypto column cannot carry the contrast

Candidate supply in the 5–7¢ band, `mmsell_candidate_ticks`, 2026-07-19 → 08-21, classified
through the taxonomy:

| crypto supply by settle mode | markets | per week |
|---|---|---|
| scheduled | 119 | 24.5 |
| in_play | **0** | **0** |
| discrete | **0** | **0** |
| unclassified (taxonomy gap) | 21 | 4.3 |

Two consequences, in order:

1. **Today, `scheduled` versus `unfiltered` inside crypto is partly a taxonomy-coverage
   comparison.** The unfiltered arm gets all 140; the scheduled arm gets 119 and loses 21 — not
   because those 21 settle differently, but because `SERIES_TYPES` has no prefix for KXSOLD,
   KXXRPD, KXBTCMAXMON, KXETHMAXMON, KXSOLMAXMON or KXXRPMAXMON. A delta between the arms would
   partly measure our own classification debt.
2. **After a Platform Change Review repairs the taxonomy, the two arms collapse.** Every one of
   those 21 is a scheduled instrument, so a repaired taxonomy makes `mode=scheduled` admit
   ~100% of crypto supply. Treatment and control then draw from the **same eligible
   population**, the crypto treatment margin is identically zero by construction, and the 2×2
   interaction is **not estimable** — not underpowered, not estimable.

So the crypto column is either contaminated (before repair) or degenerate (after). Neither is a
measurement. Hence the non-crypto-only design below, which the operator has approved in
principle.

**Routing, not editing.** `SERIES_TYPES` is a shared semantic read by `Tmmsell5`'s live `mode=`
filter, so a Research Lab session must not edit it — and this PR does not. It goes through
Platform Change Review as a durable Experiment OS issue.

**This session cannot open that issue**, and the refusal is correct rather than an obstacle:
issue writes are guarded against `DATABASE_URL_RO`, and the ops channel is always read-only. The
issue must be opened where a writable `DATABASE_URL` exists. Written out so it can be opened
verbatim rather than paraphrased:

```python
issues.create_issue(
    session,
    title="crypto series missing from the mmsell settle-mode taxonomy",
    opened_by_role="RESEARCH_LAB",
    classification="PLATFORM",
    current_owner_role="PLATFORM_CHANGE_REVIEW",
    detector="taxonomy.coverage",
    problem_statement=(
        "SERIES_TYPES has no prefix entry for KXSOLD, KXXRPD, KXBTCMAXMON, KXETHMAXMON, "
        "KXSOLMAXMON or KXXRPMAXMON. classify() returns ('unclassified','unknown') for all "
        "six, so any book filtering on mode= silently excludes them — Tmmsell5 does this "
        "live today. Measured 2026-08-21: 21 of 140 crypto markets in the 5-7c band over "
        "five weeks, i.e. 15% of crypto supply. All six are scheduled instruments, so the "
        "exclusion is classification debt rather than a settlement difference. "
        "Consequence for research: it makes any scheduled-vs-unfiltered crypto comparison "
        "partly a taxonomy-coverage comparison, and repairing it collapses those two arms "
        "onto the same population (docs/RESEARCH_MMSELL_2X2_PAPER_DESIGN.md §2)."
    ),
    owner_rationale=(
        "SERIES_TYPES is read by a live book's entry filter, so a change to it alters which "
        "markets an armed deployment admits. That is a platform semantic, not a research edit."
    ),
)
```

Two things a reviewer of that issue should weigh, and which this document does not presume to
settle: adding the prefixes changes `Tmmsell5`'s eligible universe mid-run, which is an epoch
question for that book; and `KX*MAXMON` are month-long instruments whose settle mode may deserve
its own classification rather than being folded into `scheduled`.

---

## 3. Pre-registration

### 3.1 Primary estimand — disjoint, one comparison

The scientific question is a settlement-mode contrast, so the arms must **partition** the same
universe rather than nest:

```
T (treatment)  lo=5,hi=10,maxyes=7, mode=scheduled,             skip=BTC+ETH+SOL+DOGE+XRP+CRYPTO
C (control)    lo=5,hi=10,maxyes=7, mode=in_play+discrete,      skip=BTC+ETH+SOL+DOGE+XRP+CRYPTO
```

**Primary metric:** `delta.cents_per_contract`, T − C, per **settled market** — the independent
unit, since contracts on one market share one settlement. Paper fills, so no execution or
fill-selection term enters: this is a rule question, not an execution question.

There is exactly **one** primary estimand. Everything else below is explicitly secondary.

### 3.2 Taxonomy handling for unclassified markets — stated, not discovered

`classify()` returns `("unclassified", "unknown")` for any series with no prefix entry, and
`mode=` admits nothing it cannot classify. That would silently route every unclassified market
into neither arm, making the partition incomplete in a way that grows with our classification
debt.

**Rule:** unclassified markets are **excluded from both arms** and **counted**. The count is
reported at every read as `unclassified_excluded_pct`. If it exceeds **5%** of eligible supply,
the comparison is `BLOCKED_DATA` until the taxonomy is repaired — because at that point the
partition is no longer a partition of the universe we care about. This is pre-registered so the
threshold cannot be chosen after seeing which side the exclusions fall on.

### 3.3 Secondary composite — operational concentration

The operational question ("should the book concentrate on scheduled markets?") is a
subset-versus-superset comparison, and confusing it with a treatment effect is precisely the
error this whole line of work exists to stop. It is therefore **derived, not measured
separately**, from the primary arms:

```
concentration_delta = mean(T) − [ w_T · mean(T) + w_C · mean(C) ]
                    = w_C · [ mean(T) − mean(C) ]
```

where `w_T`, `w_C` are the realized market shares of the two arms. It is a **deterministic
rescaling of the primary delta by the control's share**, carries no independent evidence, and
gets **no gate of its own**. Reporting it as a second finding would be double-counting one
comparison.

### 3.4 Universe and band, fixed

- **Universe:** every series the mmsell scan reaches, minus the crypto regime
  (`kalshi_bot/mmsell/regimes.py`, the same map both books already use).
- **Band:** yes price 5–7¢ effective (`lo=5, hi=10, maxyes=7`) — the incumbent control's band,
  so C stays directly comparable to `mmsell10`'s existing history.

### 3.5 Minimum useful effect

**+2¢/contract.** Provenance, all pre-existing: the deconfounded historical estimates
(`RESEARCH_MMSELL_UNIVERSE_DECONFOUNDING.md` §8) put the rule effect at +1.35¢ (`Tmmsell5`) and
+2.33¢ (`Tmmsell1`) in non-crypto. +2¢ sits inside that range and is the smallest effect that
would change what we trade. It is **not** chosen to be reachable.

### 3.6 Sample floor and expected supply

Per-market sd 23.2¢ (non-crypto). Two-sample, equal cells, 80% power, settled markets per arm:

| effect | @95% | @99% (the standing promotion bound) |
|---|---|---|
| +1¢ | 8,483 | 10,845 |
| **+2¢** | **2,121** | **2,711** |
| +3¢ | 943 | 1,205 |
| +5¢ | 339 | 434 |

**Supply is the binding constraint and is pre-registered as such.** `mmsell10` accrues ~86
non-crypto settled markets/day in this band. Its flow is ~15% scheduled and ~70% in_play, so:

| arm | est. markets/day | days to 2,711 |
|---|---|---|
| T (scheduled, non-crypto) | ~13 | **~209** |
| C (in_play + discrete, non-crypto) | ~64 | ~42 |

**At +2¢ this experiment takes about seven months, bounded by the treatment arm.** If that is
unacceptable the effect size must move **before** the first trade — at +3¢ T needs ~93 days, at
+5¢ ~33 days. Stating the calendar now is the point; discovering it at month four is how floors
get quietly relaxed.

### 3.7 Stopping rule

- **Evidence floor:** 2,711 settled markets in the **smaller** arm (T). No verdict below it.
- **Bound:** one-sided 99%, per the standing sequential-testing decision (#245) — continuous
  evaluation at 95% costs ~18% lifetime false promotion; 99% holds it near 5%.
- **Maximum evidence horizon:** 4,000 settled markets in the smaller arm, **inclusive** (#247) —
  the last permitted look, then `HORIZON_EXHAUSTED` rather than an indefinite peek.
- **Early failure:** a separate `fail_any` floor of 800 settled markets in the smaller arm, so a
  materially negative treatment stops without waiting for the promotion floor.
- **Data block:** `unclassified_excluded_pct > 5%` → `BLOCKED_DATA` (§3.2).
- **No re-interpretation:** metric, effect size and every floor are fixed at freeze time.

### 3.8 Implementation knobs

Both arms are expressible with knobs that already exist (`Settings.mmsell_variant_list`); **no
code change is required to run this.** `mode=` accepts a `+`-joined allowlist, `skip=` a
series-substring blocklist. Exact specs are in §3.1 and are the whole implementation.

---

## 4. What is still open, and needs the operator

1. **Approve or move the +2¢ effect**, knowing it implies ~7 months (§3.6).
2. **Approve the disjoint partition** (T vs C) as the primary, with concentration as a derived
   secondary (§3.1, §3.3).
3. **Approve the 5% unclassified block threshold** (§3.2).
4. **Route the taxonomy repair** through Platform Change Review (§2).

Nothing is created until these are settled.

---

## 5. What this design deliberately does not do

It does not use `only=` as the treatment. It does not report a subset-versus-superset delta as an
independent treatment effect. It does not choose the effect size to fit the available calendar.
It does not touch `SERIES_TYPES`. And it registers nothing: **no Version, no epoch, no deployment
and no arm exists for this.**
