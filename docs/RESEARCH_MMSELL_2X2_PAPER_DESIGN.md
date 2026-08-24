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
  "--since","2026-07-19","--until","2026-08-21","--top","200","--dump-text"]}   # run tax-9
```

That threshold was fixed in §3.2 before any of this was measured, precisely so the number could
not be chosen after seeing which side the exclusions fell on. It did its job: it says stop, and
it still says stop after the largest repair the evidence supports.

### 2A.0 STATUS — read this before anything else

**Updated 2026-08-24.** The taxonomy repair described below as a prerequisite has been
performed and is in review (PR #257). The `BLOCKED_DATA` condition in §3.2 **no longer holds**:
`unclassified_excluded_pct` is **0.18%** on the canonical window and **1.31%** on a fresh
sensitivity window, against the unchanged 5% bar. Everything from §2A.0b to §2A.3 below is
preserved as the record of the state that made the repair necessary — it is history now, not
current status. **§2A.4 is the current status**, and the design is still **not ready to
register**: the calendar at +2¢ is ~17 months under a transferred design effect, and no
Version, epoch, deployment or arm exists.

The original status block, unedited:

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
no key, so the audit fetches the rules text per series — **198 of 198 prefixes retrieved, 1,558
unique markets inspected** (up to eight per prefix, drawn from settled and open status).

**Evidence is counted over DOCUMENTS, never markets.** Settlement semantics are a property of the
series, so one rule document answers for every market under a prefix — but it answers **once**.
Run `tax-6` fetched one market per prefix, copied its blob onto all forty-six markets under it,
and reported *"100% of 46 texts"*: one document counted forty-six times. Deduplicated, the 1,558
inspected markets yield **1,558 distinct documents** — Kalshi writes team names and strikes into
each rule text, so eight markets really are eight independently-worded statements, and a prefix
proposed on eight unanimous documents is proposed on eight observations rather than one.

Four signals. Two are strong and come from Kalshi itself (`settlement_source`, and title + rules
text); two corroborate but cannot decide alone (median |expiration − close|, and whether the price
path is still mid-book at the last tick — a scheduled print and a discrete announcement both look
like a jump). A proposal needs a strong signal, no strong signal pointing elsewhere, **no
disagreement among the documents inspected**, and no *lone* corroborator against it.

| | prefixes | markets |
|---|---|---|
| proposed mode | **43** | 501 |
| INSUFFICIENT_EVIDENCE | 155 | 360 |

> **If every one of the 43 proposals is accepted, `unclassified_excluded_pct` falls from 14.31%
> to 5.98% — still above the 5% bar.** The design stays `BLOCKED_DATA`.

The refusals are mostly long-tail series carrying two or three markets each, below the
five-market floor at which a per-prefix read stops being anecdote, plus a handful where the eight
documents do not agree with each other. They are not unclassifiable — Kalshi's rules text for each
is printed verbatim in the run (`--dump-text`), and a human can decide them in minutes. The audit
declines to, and that is deliberate: **an unknown series recorded as `scheduled` would enter the
treatment arm and make the primary comparison measure the very confound this design controls
for.**

**Three defects in the audit's own classifier, found by running it and disclosed rather than
quietly fixed:**

- **One document reported as N.** The accounting error above. Fixed by sampling and
  deduplicating; the run now prints unique markets and unique documents beside every proposal.
- **A bare `at 8:10 PM EDT` read as a scheduled settlement.** Kalshi writes *"the game originally
  scheduled for Aug 22, 2026 at 8:10 PM EDT"* on **in-play** markets, so MLB player props and KBO
  baseball came back `scheduled` — precisely the error described above. A clock time does not
  discriminate; genuinely scheduled markets name the close they settle to (*"the end-of-day S&P
  500 index value"*, *"the close price of the 1-minute candlestick"*). Seven verbatim texts are
  now pinned as tests, one per mode they must separate.
- **`can_close_early` tried as a strong signal.** It is set on **100%** of these markets,
  including index-close ones: it proposed `in_play` for `KXINX` and `KXNASDAQ100` while blocking
  four prefixes whose rules text correctly said `scheduled`. It is now reported and votes on
  nothing.

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
   largest repair the evidence supports leaves 5.98% — still above the bar;
2. at measured supply the approved effect size implies a ~13-month horizon, and that estimate is
   an iid one;
3. the sample floor has not been recomputed with the event as the independent unit.

None is repairable by a Research Lab session: (1) is a Platform Change Review decision on 198
series, (2) is an operator decision about whether the horizon is worth spending, and (3) waits on
(1) because the eligible population changes when the taxonomy does. Until they are settled,
**nothing is created**.


### 2A.4 THE TAXONOMY REPAIR — done, measured, and what it changed (2026-08-24)

Run as a Platform Change Review workstream. Full record in `docs/mmsell_taxonomy_repair/`:
`CENSUS_AND_MANIFEST_20260824.md` (the frozen batch), `REVIEW_20260824.md` (the prefix-by-prefix
review), `EVIDENCE_20260824.txt` (Kalshi's own words, verbatim, for all 198),
`PLATFORM_IMPACT_20260824.md`, `POST_REPAIR_MEASUREMENT_20260824.md`.

**The batch was frozen before anything was classified.** §2A.1's worry — that reviewing prefixes
one at a time and stopping when the census crosses 5% would make the taxonomy target-driven — was
answered by making the batch the **whole** unresolved population: all 198 prefixes, 861 markets,
committed with its selection rule before the first decision. With no cutoff there is no cutoff to
move. Review continued past the point where the running census crossed the bar.

**Accepted, rejected, deferred.**

| | prefixes | candidate markets |
|---|---|---|
| **ACCEPTED** — added to `SERIES_TYPES` | **194** | **850** |
| → `in_play` | 144 | 654 |
| → `scheduled` | 31 | 136 |
| → `discrete` | 19 | 60 |
| **DEFERRED** — stay `unknown`, in neither arm | **4** | **11** |
| **REJECTED** | **0** | **0** |

The four deferrals: `KXTRUEV`, `KXDIESELD`, `KXDIESELW` (rules name a referent and a date and
nothing else — no publisher, no publication instant) and `KXMC` (evidence unambiguous, refused on
prefix generality: four characters mapping to a treatment-eligible mode would sweep every future
`KXMC*` series in unseen). §2A.1's estimate that the long tail "takes seconds to read" was
optimistic about three of them; they are not unclassifiable, they are *undocumented*.

**Post-repair census, both constructions, against the unchanged 5% bar.**

| | canonical 07-19 → 08-21 | current/fresh 07-22 → 08-24 |
|---|---|---|
| eligible candidates | 6,018 | 7,319 |
| `in_play` | 5,028 · 83.55% | 6,137 · 83.85% |
| `scheduled` | 807 · 13.41% | 875 · 11.96% |
| `discrete` | 172 · 2.86% | 211 · 2.88% |
| **`unknown`** | **11 · 0.18%** | **96 · 1.31%** |
| verdict | **PASS** | **PASS** |

The fresh window is a **sensitivity read and does not replace the canonical one.** Its extra 85
unknown markets come from 32 series absent from the canonical window entirely — the European
football seasons and the NFL starting. **The repair is a snapshot, not a steady state**, and
because §3.2 is evaluated at *every* read, an unmaintained taxonomy would re-block a long run
months in. That is the single most important thing this measurement found and it needs an
operator decision about cadence, not more code.

**Supply and floors, recomputed from scratch. The 2,711/4,000 iid floors and the 6.7/day estimate
in §2A.2 and §3.6 are withdrawn and are not reused.**

| | T — `mode=scheduled` | C — `mode=in_play+discrete` |
|---|---|---|
| candidates | 807 markets · 207 events | 5,199 markets · 3,970 events |
| supply | **24.5 markets/day** (was 6.7) | 157.5 markets/day |
| markets/event | 3.90 | 1.31 |
| Kish `m_A` | **8.54** | 2.21 |
| arm overlap | **zero** — the modes partition by construction | |

At the minimum useful effect of **+2¢** (unchanged), sd 23.2¢, one-sided 99%, 80% power, the iid
requirement is **2,701 per arm**; event-clustered it is `2,701 × DEFF`, and ρ for MMSELL is
**unmeasured**. At the planning value ρ = 0.50 — which puts the treatment arm at DEFF 4.77,
inside the 4–8 band `RESEARCH_THETA_REMEDIATION.md` measured on structurally identical strike
ladders — the floors are:

| | value |
|---|---|
| promotion evidence floor | **12,883 settled markets in T** (≈3,305 events) |
| maximum evidence horizon (#247, inclusive) | **19,325 settled markets in T** |
| early-failure floor (`fail_any`) | **800 settled markets in T**, deliberately uninflated |
| **calendar, governed by the slower arm (T)** | **~527 days (~17 months) to the floor; ~790 days (~26 months) to the horizon** |

**That is worse than the ~13 months §2A.2 retracted, and the reason matters.** The repair made
the treatment arm's supply 3.7× larger and event clustering makes each of its markets worth 4.77×
less than an iid count assumed. The second effect is bigger. **More markets did not buy a shorter
experiment, because the markets the repair added are ladders on shared events.** Across the full
ρ grid the calendar spans 0.4–2.6 years, so the choice between +2¢, +3¢ and +5¢ still should not
be put to the operator: it would be picking a horizon from a range four times as wide as the
choice. The effect size was **not** moved after seeing the supply.

**A defect in this document's own arm spec, found by applying it exactly.** `skip=` is a
substring blocklist, and `KXHEGSETHANNOUNCEOUT` contains `ETH`. Both arms drop the Pete Hegseth
departure market as though it were an Ethereum market. One market today; an open-ended collision
class for any future series containing `ETH`, `SOL` or `XRP`. Platform Change Review does not
edit an experiment's contract on the researcher's behalf, so it is **flagged, not fixed**.

**What §2A.3 said had to be settled, and where each stands:**

1. *the universe cannot be partitioned while 14.31% is unclassifiable* — **settled.** 0.18%
   canonical, 1.31% fresh, with a maintenance question attached.
2. *the horizon* — **measured, and it is ~17 months at +2¢ under a transferred ρ.** Operator
   decision, unchanged in nature.
3. *the floors must be recomputed on the event* — **done**, and they are conditional on a ρ that
   is still MMSELL's to measure.

**Nothing is created.** No Version, no epoch, no deployment, no arm, and the taxonomy repair
itself is not merged: the `MARKET_TAXONOMY` platform revision must be registered and its I2
dispositions accepted first (`PLATFORM_IMPACT_20260824.md` §7).


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
| 2 | 5% unclassified coverage block | **approved, and it FIRED** — 14.31% measured, 5.98% even after the largest repair the evidence supports |
| 3 | +2¢ minimum useful effect | approved at a ~7-month horizon; the measured horizon is **~13 months**, and that figure is an iid one (§2A.2) |
| 4 | route six crypto series to Platform Change Review | approved, but the real scope is **198 series prefixes / 861 markets** (§2A.1) |

So the open questions are:

1. **Repair the taxonomy first, or relax the block?** Repair is the honest path — a seventh of the
   universe silently excluded is not a partition — and it makes decision 4 a prerequisite rather
   than a parallel task. Relaxing the threshold after seeing it fire is the thing the threshold
   exists to prevent, and is not recommended. But note what the audit found: **even accepting all
   43 evidence-backed proposals, the census lands at 5.98%.** Clearing 5% needs a human pass over
   the long tail — 155 prefixes whose rules text is printed verbatim in the run and each of which
   takes seconds to read, but which the audit will not guess at.
2. **Accept ~13 months at +2¢, or move the effect size?** At +3¢ the treatment arm needs ~180
   days, at +5¢ ~65 — but all three figures are **iid** counts, and §2A.2 explains why the
   event-clustered floor can only be larger. Recomputing it is cheap and should precede this
   decision rather than follow it. This is a scope decision, not a statistical one.
3. **Scope of the Platform Change Review** — six crypto series, or all 198 prefixes? The crypto
   six block the crypto column (§2); the non-crypto 198 block this design. The package for the
   latter is ready: proposals with evidence for 43, Kalshi's own words for all 198, and 1,558
   rule documents behind them.

**Nothing is created until these are settled.** No Version, no epoch, no deployment, no arm.

## 5. What this design deliberately does not do

It does not use `only=` as the treatment. It does not report a subset-versus-superset delta as an
independent treatment effect. It does not choose the effect size to fit the available calendar.
It does not touch `SERIES_TYPES`. And it registers nothing: **no Version, no epoch, no deployment
and no arm exists for this.**
