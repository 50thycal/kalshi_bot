# Post-repair measurement — census, supply, and the event-clustered calendar

Run 2026-08-24, after the 194 classifications in `REVIEW_20260824.md`. Two censuses are
reported: the **canonical** window the design is pre-registered on, and a **separately labelled
current/fresh** sensitivity window. The canonical result is the one the gate is read on; the
fresh window does not replace it.

---

## 0. How these numbers were produced, and why not through the audit script

The ops runner **always executes code from the default branch**, never from the `ops` transport
branch — a deliberate fix for XOS-000005, so the runner cannot drift behind `main`. The
consequence for this workstream is a genuine ordering constraint: `mmsell_taxonomy_audit` cannot
see the repaired `SERIES_TYPES` until this PR merges, and this PR must not merge until the
platform revision is registered. Running the audit against the repaired branch was tried
(request `mmt-post-canonical`) and correctly returned the **pre-repair** census, because the
default-branch table is what it loaded.

So the census was recomputed rather than re-run, from the **same rows, over the same
construction**, with the classification applied locally:

```sql
-- request mmt-series-1 (read-only). The census is a function of (candidate rows, taxonomy);
-- this returns the rows, and the shipped SERIES_TYPES supplies the taxonomy.
SELECT upper(coalesce(nullif(series,''), split_part(market_ticker,'-',1))) AS s,
       count(DISTINCT market_ticker) FILTER (WHERE captured_at >= '2026-07-19'
                                               AND captured_at <  '2026-08-21') AS canon,
       count(DISTINCT market_ticker) FILTER (WHERE captured_at >= '2026-07-22'
                                               AND captured_at <  '2026-08-24') AS fresh
  FROM mmsell_candidate_ticks
 WHERE mid IS NOT NULL AND mid >= 5 AND mid <= 7
   AND captured_at >= '2026-07-19' AND captured_at < '2026-08-24'
 GROUP BY 1 ORDER BY 2 DESC, 1
```

Counting distinct markets per series is equivalent to the audit's
`SELECT DISTINCT ON (market_ticker) … WHERE mid BETWEEN 5 AND 7 ORDER BY market_ticker,
captured_at ASC`: the audit keeps the **first tick at which** a market is in band, and the
series is a per-market constant, so the per-series market counts are identical. Markets that
drift into the band later are included, which is the whole point of that construction.

**The method is validated against the baseline it must reproduce.** Applying the *pre-repair*
table (`git show HEAD~1:kalshi_bot/mmsell/market_types.py`) to these same rows returns:

| | recomputed | audit run `mmt-base-1` |
|---|---|---|
| total eligible | 6,018 | 6,018 ✅ |
| `in_play` | 4,374 | 4,374 ✅ |
| `unknown` | 861 | 861 ✅ |
| `scheduled` | 671 | 671 ✅ |
| `discrete` | 112 | 112 ✅ |
| unclassified prefixes | 198 | 198 ✅ |
| `unclassified_excluded_pct` | 14.31% | 14.31% ✅ |

Every cell matches. The post-repair numbers below are the same computation with the same rows
and the shipped table. **`mmsell_taxonomy_audit` must still be re-run end-to-end after merge**,
as the reproducible artifact of record; this recomputation is what could be done before it.

---

## 1. CANONICAL census — 2026-07-19 → 2026-08-21, post-repair

| settle mode | markets | share | before the repair |
|---|---|---|---|
| `in_play` | **5,028** | **83.55%** | 4,374 / 72.68% |
| `scheduled` | **807** | **13.41%** | 671 / 11.15% |
| `discrete` | **172** | **2.86%** | 112 / 1.86% |
| **`unknown`** | **11** | **0.18%** | 861 / 14.31% |
| **total eligible** | **6,018** | | 6,018 |

> **`unclassified_excluded_pct` = 0.18% against the unchanged 5% bar → PASS.**

Remaining unknown prefixes: **4** — `KXTRUEV` (6), `KXDIESELD` (3), `KXDIESELW` (1), `KXMC` (1).
These are exactly the four deferrals; no prefix was missed.

Rules-document coverage behind the classifications: 198/198 prefixes fetched, **1,561 unique
Kalshi markets inspected, 1,561 distinct rule documents**, up to 8 markets per prefix drawn from
`settled` then `open`. Kalshi's `settlement_source` field was empty on all 1,561, so every
classification rests on published title + rules text.

## 2. CURRENT / FRESH census — 2026-07-22 → 2026-08-24 (sensitivity only)

Same construction, same band, 33 days ending today. **This does not replace the canonical
result.** The last day is partial.

| settle mode | markets | share |
|---|---|---|
| `in_play` | 6,137 | 83.85% |
| `scheduled` | 875 | 11.96% |
| `discrete` | 211 | 2.88% |
| **`unknown`** | **96** | **1.31%** |
| **total eligible** | **7,319** | |

> **`unclassified_excluded_pct` = 1.31% against the unchanged 5% bar → PASS.**

Remaining unknown prefixes: **36**, and the split is the finding:

| | prefixes | markets |
|---|---|---|
| the four reviewed deferrals | 4 | 11 |
| **series absent from the canonical window entirely** | **32** | **85** |

### 2.1 The fresh window's real finding: the repair is a snapshot, not a steady state

Those 32 prefixes contributed **zero** markets in the canonical window and 85 in the fresh one.
They are not obscure: `KXEPLSCORE` (17), `KXEPLTOTAL` (9), `KXEPLSPREAD` (6), `KXSERIEAGAME`,
`KXLIGUE1GAME`, `KXNFL1H`, `KXNASCARTOP5`, `KXSILVERMON`. The European football seasons and the
NFL start in the days the fresh window adds, and each new competition arrives as a family of
series the taxonomy has never seen.

The mechanism is the same one that produced the original 14.31%: **an unclassified series is
admitted by no `mode=` book, so classification debt is a silent, continuously accruing exclusion.**
Over the canonical 33 days that debt accrued at ~26 markets/day; the fresh window shows it did
not stop when the repair was written.

The consequence for the design, stated numerically: the 5% bar is **301 markets** on the
canonical population and **366** on the fresh one. Today's residual is 11. The gap is real head-
room, but it is a few weeks of unmaintained listings, not a permanent margin — and
`unclassified_excluded_pct` is evaluated at **every read**, not once at registration. A
`BLOCKED_DATA` verdict months into a 500-day experiment would cost the whole run.

**This is why the durable ledger item must not be resolved when the code merges.** The repair
needs a maintenance cadence, and naming one is an operator decision, not a code change.

---

## 3. Arm supply and event structure, recomputed from scratch

Computed on the **canonical** window, applying the arms exactly as pre-registered — `mode=`
against the repaired taxonomy, `skip=BTC+ETH+SOL+DOGE+XRP+CRYPTO` as the substring blocklist the
config actually implements. Event = the ticker with its final `-<strike>` segment removed, the
same clustering the audit uses (request `mmt-events-1`).

| | **T — `mode=scheduled`** | **C — `mode=in_play+discrete`** |
|---|---|---|
| eligible candidate markets | **807** | **5,199** |
| distinct events | **207** | **3,970** |
| distinct series | 71 | 243 |
| candidate supply | **24.5 markets/day** · 6.3 events/day | **157.5 markets/day** · 120.3 events/day |
| markets per event (mean) | **3.90** | **1.31** |
| Kish average cluster size `m_A = Σm²/Σm` | **8.54** | **2.21** |

**Arm overlap: zero.** No series is in both arms — `scheduled` and `in_play+discrete` partition
the mode axis by construction, and the modes are mutually exclusive per series. 11 markets (the
deferrals) are in **neither** arm and are the whole of `unclassified_excluded_pct`. 278 markets
are dropped by the crypto `skip=` and are outside both arms by design.

**The withdrawn 6.7 candidates/day for the treatment arm is replaced by 24.5/day** — 3.7× higher,
because the repair moved 136 markets into `scheduled` and because this is measured on the
drift-into-band population the arms actually see.

### 3.1 A defect in the pre-registered arm spec, found by applying it exactly

`skip=` is a **substring** blocklist, and `KXHEGSETHANNOUNCEOUT` contains `ETH` (…H-EGS-**ETH**…).
The Pete Hegseth departure market is therefore dropped by both arms as though it were an Ethereum
market. It is 1 market and changes nothing measured here, but the collision class is open-ended —
any future series containing `ETH`, `SOL` or `XRP` as a substring is silently excluded.

**Not fixed here.** The arm spec is the experiment's scientific contract; Platform Change Review
does not edit one on the researcher's behalf. Routed to Research Lab, recorded in the durable
ledger item.

## 4. Event-clustered sample requirements

The old **2,711 / 4,000 iid floors and the 6.7-candidates/day estimate are withdrawn** and are
not reused. Recomputed from scratch:

**iid requirement**, two-sample, equal cells, minimum useful effect **+2¢/contract** (unchanged),
per-market sd **23.2¢**, one-sided **99%** bound (the standing sequential-testing decision #245),
**80%** power:

```
n_iid = 2 (z_0.01 + z_0.20)² σ² / δ²  =  2 (2.3263 + 0.8416)² (23.2)² / 2²  =  2,701 per arm
```

**Event-clustered requirement** = `n_iid × DEFF`, with `DEFF = 1 + (m_A − 1)ρ` and `m_A` measured
above. **ρ — the within-event intraclass correlation of `cents_per_contract` — is UNMEASURED for
MMSELL, and cannot be measured before the arms exist**, so the floor is reported as a function of
it rather than as one number:

| ρ | DEFF_T | floor_T (markets) | DEFF_C | floor_C | days at T's supply | calendar |
|---|---|---|---|---|---|---|
| 0.05 | 1.38 | 3,720 | 1.06 | 2,864 | 152 | 0.4 yr |
| 0.10 | 1.75 | 4,738 | 1.12 | 3,028 | 194 | 0.5 yr |
| 0.25 | 2.89 | 7,795 | 1.30 | 3,518 | 319 | 0.9 yr |
| **0.50** | **4.77** | **12,883** | **1.60** | **4,335** | **527** | **1.4 yr** |
| 1.00 | 8.54 | 23,076 | 2.21 | 5,968 | 944 | 2.6 yr |

**Planning case ρ = 0.50, and it is a transfer, not a measurement.** The treatment arm's events
are overwhelmingly *strike ladders* — one `KXINX` event carries 21 markets that all settle off a
single index print — which is structurally the case `RESEARCH_THETA_REMEDIATION.md` §4.2.3 and
§3.2 measured at **DEFF 4–8** on crypto ladders. ρ = 0.50 puts the treatment arm at DEFF 4.77,
inside that measured band. It is the best-anchored value available; it is not MMSELL's own.

### 4.1 The floors, at the planning case

| | value | note |
|---|---|---|
| **promotion evidence floor** | **12,883 settled markets in the smaller arm (T)** ≈ **3,305 events** | 2,701 iid × DEFF 4.77 |
| **maximum evidence horizon** (inclusive, #247) | **19,325 settled markets in T** | 1.5 × the floor, the same convention the theta redesign used (1,141/760) |
| **early-failure floor** (`fail_any`) | **800 settled markets in T**, uninflated | deliberately **not** multiplied by DEFF: it is a stopping clause, and requiring *less* evidence to stop a materially negative treatment errs toward stopping a good arm, which is the safe direction. Same reasoning as `RESEARCH_THETA_REMEDIATION.md` §4.2.3. Reached in ~33 days. |
| **binding arm** | **T (treatment)** | 24.5 markets/day against C's 157.5; C reaches its floor in ~28 days |

### 4.2 Expected calendar time, governed by the slower arm

| milestone | days | calendar |
|---|---|---|
| early-failure floor (T) | ~33 | ~1 month |
| control arm's own floor (C) | ~28 | ~1 month |
| **promotion evidence floor (T)** | **~527** | **~17 months** |
| maximum evidence horizon (T) | ~790 | ~26 months |

**Report the horizon, do not move the effect size.** The +2¢ minimum useful effect is unchanged,
and the answer at +2¢ is **~17 months to a verdict, ~26 months to `HORIZON_EXHAUSTED`**.

That is *worse* than the ~13 months the withdrawn iid estimate implied, and the reason is worth
stating plainly: the repair made the treatment arm's supply **3.7× larger** (6.7 → 24.5
markets/day), and event clustering makes each market worth **4.77× less** than the iid count
assumed. The second effect is larger than the first. **More markets did not buy a shorter
experiment, because the markets the repair added are ladders on shared events.**

Across the full ρ grid the calendar spans **0.4 to 2.6 years**. Choosing between +2¢, +3¢ and +5¢
still should not be asked yet: it would be picking a horizon from a range four times as wide as
the choice.

### 4.3 What must still be measured before any arm is registered

1. **ρ for MMSELL's `cents_per_contract`**, event-clustered — the single number that collapses a
   0.4–2.6 year range to a calendar. Measurable from the existing paper books' settled trades
   without creating an arm.
2. **Per-market sd on the repaired universe.** 23.2¢ was measured before the repair moved 850
   markets between modes; the treatment arm's composition has changed materially.
3. **Candidate → settled conversion.** Every figure above treats a candidate as a settled market.
   Paper fills assume a resting maker order always fills, so this is close to right for a paper
   arm, but it is an assumption and it is an upper bound.

---

## 5. Verdict against the unchanged 5% gate

> **PASS on both constructions.** Canonical **0.18%**, current/fresh **1.31%**, against a bar
> that was not moved. The `BLOCKED_DATA` condition of
> `docs/RESEARCH_MMSELL_2X2_PAPER_DESIGN.md` §3.2 no longer holds on either window.

**The taxonomy gate passing is not permission to start the experiment.** It clears one of the
three preconditions §2A.3 listed. The other two are addressed above and both return answers that
belong to the operator, not to this session: the horizon at +2¢ is ~17 months under a *transferred*
design effect, and the residual unknown share is a snapshot that resumes accruing the day after
the repair merges.

Nothing here registers, arms, promotes or starts anything.
