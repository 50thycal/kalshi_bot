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

## 2A. PRE-START CHECK — FAILED. Do not create the arms.

The operator's approval was conditional on returning the measured `unclassified_excluded_pct`
for the eligible non-crypto 5–7¢ population before any arm exists.

**Measured: `unclassified_excluded_pct` = 14.31%, against a pre-registered block threshold of
5%.** The design would be `BLOCKED_DATA` on its first evaluation. **The arms must not be
created.** The census, the audit and the full evidence package are in §2A.1 and §2A.1b; run
`tax-6`, reproducible with one command:

```
{"type":"script","name":"mmsell_taxonomy_audit","args":[
  "--since","2026-07-19","--until","2026-08-21","--top","200","--dump-text"]}
```

That threshold was fixed in §3.2 before any of this was measured, precisely so the number could
not be chosen after seeing which side the exclusions fell on. It did its job: it says stop, and
it still says stop after the largest repair the evidence supports.

### 2A.0 STATUS — read this before anything else

- The experiment is **`BLOCKED_DATA`**.
- It is **not ready to register.** No Version, no epoch, no deployment, no arm exists.
- The **taxonomy repair must happen first.** It is a prerequisite, not a parallel task.
- **Supply and calendar time must be remeasured after the repair**, because the repair changes
  which markets are eligible.
- The **event-clustered sample requirements are outstanding.** Every floor in §3.6 is an iid
  count and must be recomputed with the event as the unit before any arm is registered.
- **No decision between +2¢, +3¢ and +5¢ is being requested yet.** That question is downstream
  of the two measurements above and asking it now would be asking the operator to choose a
  horizon from numbers that are known to be wrong.

### 2A.0b Why the census moved: 2,139 versus 6,018

The two constructions differ by **where the price-band filter sits**, and the difference is not
cosmetic. Both read `mmsell_candidate_ticks` over 2026-07-19 → 08-21, non-crypto:

```sql
-- unclass-2:  first tick of the market, THEN require 5-7c
SELECT DISTINCT ON (market_ticker) ... ORDER BY market_ticker, captured_at ASC   -- first tick
...  WHERE mid BETWEEN 5 AND 7                                                   -- filter after

-- audit:      first tick AT WHICH the market is 5-7c
SELECT DISTINCT ON (market_ticker) ... WHERE mid BETWEEN 5 AND 7                 -- filter first
     ORDER BY market_ticker, captured_at ASC
```

Measured side by side (run `census-recon-1`):

| construction | markets | series |
|---|---|---|
| A — first tick already in band (`unclass-2`) | 2,070 | 264 |
| B — first tick at which in band (audit) | **6,018** | **319** |
| C — ever in band, at any tick (sanity check on B) | 6,018 | 319 |
| D — in B but not in A: **entered the band later** | **3,948** | 236 |
| E — all non-crypto candidates in the window | 19,968 | 440 |

B and C being identical confirms what B measures: *was in band at some point*. D is the gap —
**3,948 markets, 66% of the eligible population**, that opened outside 5–7¢ and drifted in.

(`unclass-2` reported 2,139 rather than 2,070 because it had no upper date bound and ran a day
longer. That is a footnote; the construction is the finding.)

**Which one matches the arms?** The mmsell scan evaluates every candidate every cycle and takes
it when it is in band *at that moment*. A market that drifts into 5–7¢ **is** eligible and would
be traded. So **B is the population the arms would actually see**, and A measured something the
design never proposed: markets *born* in the band. The audit's census is canonical because it
matches the arms' behaviour — not because it is newer.

Both fail the 5% bar, so the operator decision is unchanged. The figure is not, and neither is
the supply estimate in §2A.2, which was computed on the narrower population.

| settle mode | markets | share |
|---|---|---|
| `in_play` | 4,374 | 72.68% |
| **`unknown`** | **861** | **14.31%** |
| `scheduled` | 671 | 11.15% |
| `discrete` | 112 | 1.86% |

> **`unclassified_excluded_pct` = 14.31%, against a pre-registered block threshold of 5%.**
> `BLOCKED_DATA`. **The arms must not be created.**

**The taxonomy repair is a prerequisite for this experiment, not a task running beside it.** Any
`mode=`-defined arm today silently discards a seventh of its own universe, and which seventh is
decided by classification debt rather than by settlement behaviour.

### 2A.1 The taxonomy debt is 198 series prefixes, not six

The Platform Change Review scope in §2 was drawn from the crypto universe alone. The audit
(`scripts/mmsell_taxonomy_audit.py`) puts it two orders of magnitude higher: **861 unclassified
markets across 198 series prefixes**, against six.

### 2A.1b The Platform Change Review package

**The database cannot supply the evidence.** `markets` holds no row for any of the 861
unclassified markets, so the first pass of the audit had no strong signal for any prefix and
correctly refused to propose anything at all. Kalshi's market-data endpoints are public and need
no key, so the audit now fetches the rules text per series — **198 of 198 prefixes retrieved**.

Four signals per prefix. Two are strong and come from Kalshi itself (`settlement_source`, and
title + rules text); two corroborate but cannot decide alone (median |expiration − close|, and
whether the price path is still mid-book at the last tick — a scheduled print and a discrete
announcement both look like a jump). A proposal needs a strong signal, no strong signal pointing
elsewhere, and no *lone* corroborator against it.

| | prefixes | markets |
|---|---|---|
| proposed mode | **45** | 526 |
| INSUFFICIENT_EVIDENCE | 153 | 335 |

> **If every one of the 45 proposals is accepted, `unclassified_excluded_pct` falls from 14.31%
> to 5.57% — still above the 5% bar.** The design stays `BLOCKED_DATA`.

The 153 refusals are almost all long-tail series carrying two or three markets each, below the
five-market floor at which a per-prefix read stops being anecdote. They are not unclassifiable —
Kalshi's rules text for each is printed verbatim in the run (`--dump-text`), and a human can
decide them in minutes. The audit declines to, and that is deliberate: **an unknown series
recorded as `scheduled` would enter the treatment arm and make the primary comparison measure the
very confound this design controls for.**

**Two defects in the audit's own classifier, found by dumping the corpus and disclosed rather
than quietly fixed:**

- A bare `at 8:10 PM EDT` was read as a scheduled settlement. Kalshi writes *"the game originally
  scheduled for Aug 22, 2026 at 8:10 PM EDT"* on **in-play** markets, so MLB player props and KBO
  baseball came back `scheduled` — precisely the error described above. A clock time does not
  discriminate; genuinely scheduled markets name the close they settle to (*"the end-of-day S&P
  500 index value"*, *"the close price of the 1-minute candlestick"*). Seven verbatim texts are
  now pinned as tests, one per mode they must separate.
- `can_close_early` was tried as a strong signal on the theory that Kalshi sets it where
  settlement follows the event. It is set on **100%** of these markets, including index-close
  ones: it proposed `in_play` for `KXINX` and `KXNASDAQ100` while blocking four prefixes whose
  rules text correctly said `scheduled`. It is now reported and votes on nothing.

**Nothing here edits `SERIES_TYPES`, in either copy.** That table is shared platform semantics
read by every `mode=` book, so a change to it is a Platform Change Review event with its own
impact review — not a side effect of an analysis script.

### 2A.2 The treatment arm is scarcer than §3.6 estimated

§3.6's ~13 markets/day for the treatment arm was derived from `mmsell10`'s settled-trade
composition. Measured against the **candidate stream** — the thing that actually limits an arm —
it is **6.7/day**. The control arm runs at 41.1/day.

| | earlier estimate | measured |
|---|---|---|
| T supply | ~13/day | **6.7/day** |
| days to 2,711 markets at +2¢ | ~209 | **~404** |

**At the +2¢ minimum useful effect the experiment takes about thirteen months, not seven.** The
earlier figure was wrong and is retracted here rather than quietly replaced.

Two qualifications, both of which push the horizon further out rather than in. The supply figure
predates the census correction in §2A.1 and is measured on the *settled* stream; and the floor of
2,711 markets is an **iid** count. Markets that share an event share an outcome, and §4.2.3 of
`RESEARCH_THETA_REMEDIATION.md` shows what that costs a floor derived the same way — a design
effect of 1.87 on a thinly-spread selected set, and 4.6 on a dense one. **The MMSELL floor has
not yet been recomputed on the event**, and it must be before any arm is registered, because the
correction runs in one direction only: up.

### 2A.3 What this changes, and what it does not

The **design** is unaffected — the disjoint partition, the single primary estimand, the derived
secondary and the stopping rule all stand. What has changed is that three of its preconditions
are not met today:

1. the universe cannot be partitioned cleanly while 14.31% of it is unclassifiable, and the
   largest repair the evidence supports leaves 5.57% — still above the bar;
2. at measured supply the approved effect size implies a ~13-month horizon, and that estimate is
   an iid one;
3. the sample floor has not been recomputed with the event as the independent unit.

None is repairable by a Research Lab session: (1) is a Platform Change Review decision on 198
series, (2) is an operator decision about whether the horizon is worth spending, and (3) waits on
(1) because the eligible population changes when the taxonomy does. Until they are settled,
**nothing is created**.


---

## 3. Pre-registration

### 3.1 Primary estimand — disjoint, one comparison

The scientific question is a settlement-mode contrast, so the arms must **partition** the same
universe rather than nest:

```
T (treatment)  lo=5,hi=10,maxyes=7, mode=scheduled,             skip=BTC+ETH+SOL+DOGE+XRP+CRYPTO
C (control)    lo=5,hi=10,maxyes=7, mode=in_play+discrete,      skip=BTC+ETH+SOL+DOGE+XRP+CRYPTO
```

**Primary metric:** `delta.cents_per_contract`, T − C. Paper fills, so no execution or
fill-selection term enters: this is a rule question, not an execution question.

**Independent unit: the EVENT, not the settled market.** An earlier revision called the settled
market the independent unit "since contracts on one market share one settlement". That is true
and insufficient — markets that share an **event** share the *thing being settled*, so their
outcomes are correlated even though each settles separately. A four-way MLB total on one game is
not four independent draws on whether that game went over. The same correction the theta work
had to make (`RESEARCH_THETA_REMEDIATION.md` §1.1, measured design effect 4–7 on crypto ladders),
in a different place. Every interval on this estimand must be event-clustered, and the floors in
§3.6 are iid counts that have not yet been recomputed on that basis.

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

> **Every number in that table is an IID count and is therefore too small.** Markets sharing an
> event share an outcome, so the effective sample is smaller than the market count by the design
> effect. The theta work measures 4–7 on crypto ladders and 1.87 on a thinly-spread selected set
> (`RESEARCH_THETA_REMEDIATION.md` §4.2.3). MMSELL's own design effect has **not been measured**,
> and it must be before any arm is registered. The correction runs in one direction: up.

**Supply is the binding constraint and is pre-registered as such.** Measured on the CANDIDATE
STREAM, which is what actually limits an arm — an earlier estimate derived from settled-trade
composition was roughly double the truth and is retracted in §2A.2:

| arm | measured markets/day | days to 2,711 (iid) |
|---|---|---|
| T (scheduled, non-crypto) | **6.7** | **~404** |
| C (in_play + discrete, non-crypto) | 41.1 | ~66 |

> **This supply figure is also provisional**, on two counts. It was measured on the narrower
> `unclass-2` population (§2A.0b), which excluded the 66% of markets that drift into the band
> rather than opening in it; and it predates the taxonomy repair, which will move markets between
> the arms. It must be remeasured after the repair, on the population the arms actually see.

**So the calendar is not yet known.** At +2¢ on iid counts and the old supply figure it is about
thirteen months; the event-clustered floor is larger and the repaired supply is unknown. **No
choice between +2¢, +3¢ and +5¢ is being requested** — asking the operator to pick a horizon from
numbers known to be wrong is worse than not asking. Stating the calendar before the first trade
is still the point; stating a calendar that will move is not.

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

The four decisions were approved **subject to the pre-start measurement**. That measurement came
back at 14.31% against a 5% threshold (§2A), so three of them now need revisiting rather than
executing:

| # | decision | status |
|---|---|---|
| 1 | disjoint `scheduled` vs `in_play+discrete` primary | **approved, unaffected** |
| 2 | 5% unclassified coverage block | **approved, and it FIRED** — 14.31% measured, 5.57% even after the largest repair the evidence supports |
| 3 | +2¢ minimum useful effect | approved at a ~7-month horizon; the measured horizon is **~13 months**, and that figure is an iid one (§2A.2) |
| 4 | route six crypto series to Platform Change Review | approved, but the real scope is **198 series prefixes / 861 markets** (§2A.1) |

So the open questions are:

1. **Repair the taxonomy first, or relax the block?** Repair is the honest path — a seventh of the
   universe silently excluded is not a partition — and it makes decision 4 a prerequisite rather
   than a parallel task. Relaxing the threshold after seeing it fire is the thing the threshold
   exists to prevent, and is not recommended. But note what the audit found: **even accepting all
   45 evidence-backed proposals, the census lands at 5.57%.** Clearing 5% needs a human pass over
   the long tail — 153 prefixes whose rules text is printed verbatim in the run and each of which
   takes seconds to read, but which the audit will not guess at.
2. **Accept ~13 months at +2¢, or move the effect size?** At +3¢ the treatment arm needs ~180
   days, at +5¢ ~65 — but all three figures are **iid** counts, and §2A.2 explains why the
   event-clustered floor can only be larger. Recomputing it is cheap and should precede this
   decision rather than follow it. This is a scope decision, not a statistical one.
3. **Scope of the Platform Change Review** — six crypto series, or all 198 prefixes? The crypto
   six block the crypto column (§2); the non-crypto 198 block this design. The package for the
   latter is ready: proposals with evidence for 45, Kalshi's own words for all 198.

**Nothing is created until these are settled.** No Version, no epoch, no deployment, no arm.

## 5. What this design deliberately does not do

It does not use `only=` as the treatment. It does not report a subset-versus-superset delta as an
independent treatment effect. It does not choose the effect size to fit the available calendar.
It does not touch `SERIES_TYPES`. And it registers nothing: **no Version, no epoch, no deployment
and no arm exists for this.**
