# Idea-model run 2026-10-01: size up on MMSELL's best markets

```
SESSION: Research Lab
MODE: research-write (docs, pre-registration); no probe built, no book changed, no live action
ENFORCEMENT: NEW_ONLY since 2026-08-16 14:34:42Z (cutover prod-new-only-20260816)
AS OF: 2026-10-01 03:33 UTC (ops `sizeup-census-1001`, `sizesel-census-1001`, `sizesel-census-1001b`)
```

**Scope.** Operator request (Calvin, 2026-10-01): "review which markets give us the best results
and increase our position size on those … run the idea model on that." Scope was named, so the
Phase 0.5 menu was skipped. The live book is `Fmmsell10` (1 contract, post-only NO bid at
93–97¢, 4 h timeout, hold to settlement), kept running by operator instruction for its fill data.

**Result in one paragraph.** Eleven in-scope candidates were screened; **one is promoted:
[TYPE-RANK](MMSELL_TYPE_RANK_THESIS.md)**, which ranks the five readable market types on real
fills and tests whether the ranking persists, retrospectively (can only kill) and forward from
10-01 (decides). Two outcome-blind censuses settled the rest of the question before any P&L was
read. **Per-series selection cannot be tested on this book:** only 5 series have ≥ 20 live fills in
both windows. **Sizing itself is a near-pure multiplier:** the takers that fill us sweep a median
59 contracts, so a 3-lot fills on the same events as a 1-lot. Everything therefore rests on
whether some *type* of market reliably earns more per fill. If it does not, sizing is only a
capital decision on a per-fill edge indistinguishable from zero.

Nothing here changes a lifecycle state, a gate, an envelope or any book. No probe was written.

---

## Phase 0: grounding

**Settled already (do not regenerate):**

| lever | verdict | where |
|---|---|---|
| series keep/drop from **paper** history | null: keep −0.98¢ vs drop −0.45¢ on live fills | ops `mmnext-oos-0929` |
| size ×3 in a **price cell** | no cell passes; unfilled beats filled by 5–6¢ in every cell | `MMSELL_CELL_SIZE_CENSUS.md` |
| series **age** | KILL, young −0.92¢ vs mature −0.30¢ | `MMSELL_YOUNG_SERIES_THESIS.md` |
| queue depth | KILL on independent sample | `MMSELL_QUEUE_DEPTH_THESIS.md` |
| adding into winners late | dead: doubles tail for +55% premium | `MMSELL_ROADMAP.md` §8 |
| tail-weighted sizing by series | spec'd, never built; cells need ≥ 50 events | roadmap §8; 09-30 run M4 |

**The power fact that frames this run** (`MMSELL_SERIES_SCORECARD_HANDOFF.md` §3): the family
edge in the ≤ 7¢ band is +0.64pp of loss rate. A series needs ~70 contests for its own record to
flag a *disaster* (a series 10pp worse than its band); a series *better* than its band by a
realistic 1–3pp is not resolvable on any series this book has traded. The scorecard's
partial-pooling design (`k ≈ 30`) is the right tool for barring bad series, and its verdicts are
proposed but unsigned (`MMSELL_SERIES_APPROVAL_REVIEW.md`).

**Real money now** (`mmideas-truth-0930`): +$2.49 realized over 689 settled, ~+0.36¢/fill,
≈ $3/month. CELL-SIZE's pooled cells 93–94 read +0.02¢/fill. Either way the per-fill edge is
indistinguishable from zero, which is what any multiplier multiplies.

## Phase 1: the censuses (outcome-blind; counts only)

**Series overlap** (`sizeup-census-1001`; window A = 7 pre-9/7 live books from 07-26, 1,428 fills;
window B = Fmmsell10 to 10-01, 739 fills):

| floor | series ≥ floor in A | of which also ≥ floor in B | B fills covered |
|---|---|---|---|
| 10 fills | 30 | 14 | 62% |
| 20 | 15 | 5 | 44% |
| 30 | 6 | 2 | 24% |
| 50 | 4 | 1 | 17% |

Ten series with B fills had none in A; `KXNFLSPREAD`, `KXNCAAFSPREAD` and `KXCLUBFGAME` traded in A
and not in B (bars and season). The universe churns faster than a series record can mature.

**Type cells** (same census, through `scripts/mmsell_market_types.py`): h2h 263 / 359,
player_prop 358 / 138, price_strike 189 / 102, total 198 / 59, spread 148 / 24 (A / B fills);
everything else 272 / 57. Five cells are readable.

**Taker size** (`sizesel-census-1001b`, 301 Fmmsell10 fills matched to the WS-019 tape): the
first attempt (`sizesel-census-1001`) read every matched trade as size 1, because Kalshi records
each maker match separately. Summing all matches at the same instant on the same side gives the
taker's order: **p25 10, median 59, p75 232, p90 904 contracts**; 240 of 301 fills (80%) came from
takers of ≥ 3 contracts. A 3-lot fills on the same events as a 1-lot.

## Phase 2: the slate (11 candidates, all inside the scope)

| # | candidate | what it needs to be true |
|---|---|---|
| Z1 | SERIES-RANK: size ×3 on the top-tercile series by live ¢/fill | per-series live records persist and are resolvable |
| Z2 | SCORE-SIZE: size by the scorecard's partial-pooled edge score (`k ≈ 30`) | paper-history loss rates predict live fills |
| Z3 | **TYPE-RANK**: size ×3 on the top two market types by live ¢/fill | type-level live records persist |
| Z4 | SIZE-SELECTION: are fills from large takers worse, so extra contracts earn less? | big sweeps are more informed than small lifts |
| Z5 | SIZE-ALL ×3 | the book-wide per-fill edge is positive |
| Z6 | DROP-DISASTERS: bar series the scorecard flags (the statistically resolvable half) | the scorecard's disaster threshold catches real losers |
| Z7 | SEASON-RANK: size up the sport in season | the in-season sport has a stable live record |
| Z8 | RELAX-TIER: admit tier-barred series (never-ordered bucket +3.14¢ paper) | the barred universe fills like the reachable one |
| Z9 | INDEPENDENCE-SIZE: more contracts where the contest has no correlated siblings | risk reduction converts to dollars |
| Z10 | TIME-SIZE: size up in high-flow hours | timing carries edge for a maker |
| Z11 | PRICE-CELL ×3 | a fill-neutral price cell exists |

## Phase 3: the screen

Axes: Corr (all share the live book's driver; this axis only separates multipliers from
refinements), Edge (prior), Cost, Test-NOW, Cap ($/month at realistic size), Reuse. −− to ++.

| # | Corr | Edge | Cost | Test-NOW | Cap | Reuse | call |
|---|---|---|---|---|---|---|---|
| Z1 SERIES-RANK | o | o | ++ | **−−** (5 series ≥ 20 fills in both windows) | o | ++ | **KILL as a test**: unresolvable at this flow; the scorecard showed winners are not detectable per series |
| Z2 SCORE-SIZE | o | − (paper predictor; `mmnext-oos-0929` null) | ++ | + | o | ++ | **KILL**: regeneration of the paper keep/drop read with a better estimator; the predictor is still paper |
| Z3 **TYPE-RANK** | o | o (no live→live type test exists; price cells and paper types were flat) | ++ | **+** retrospective leg now; forward leg accrues | − (≈ $18/month ceiling) | ++ | **PROMOTE** (kill-capable now, promotion only forward) |
| Z4 SIZE-SELECTION | o | o | ++ | − (61 small-taker fills, under any floor) | n/a | + | **HOLD, low value**: the census already shows 80% of fills come from takers ≥ 3; even a 5¢ gap moves a marginal contract by ~1¢ |
| Z5 SIZE-ALL ×3 | o | −− (per-fill edge ≈ 0) | ++ | ++ | − (~$10/month, triple tail) | ++ | **KILL as an edge**; it is a capital decision on a zero-edge book |
| Z6 DROP-DISASTERS | o | + (the resolvable direction) | ++ | + | − (removes losses, adds no size) | ++ | **EXISTING LINE**: the scorecard and series-review verdicts exist and await operator signature; not regenerated |
| Z7 SEASON-RANK | o | − | ++ | −− (the season has just turned; no in-season live record) | o | + | **FOLD** into Z3's forward leg (it reports sport mix by type) |
| Z8 RELAX-TIER | o | o | ++ | −− (only a live tag answers it) | o | + | **DECISION** carried from 09-30 (M2); hard stop |
| Z9 INDEPENDENCE-SIZE | o | −− (risk, not edge) | ++ | + | −− | + | **KILL** |
| Z10 TIME-SIZE | o | −− (maker timing died at the fill haircut) | ++ | + | − | + | **KILL** (`MMSELL_TIMING_STUDY.md`) |
| Z11 PRICE-CELL ×3 | o | −− | ++ | ++ | − | ++ | **KILLED 09-30** (CELL-SIZE) |

## Phase 4: promotion

**[TYPE-RANK](MMSELL_TYPE_RANK_THESIS.md).** Cells = market type; TOP = the two readable types
with the highest realized ¢/fill in the ranking window. Leg R ranks on window A and scores on
window B, and can only kill (window-B per-series outcomes were seen in reports on 09-29/30).
Leg F ranks on everything before 10-01 and scores on fills from 10-01, and alone decides
promotion: separation ≥ +1.5¢ with a date-block bootstrap 5th percentile > 0 and TOP ≥ +1.0¢/fill,
at ≥ 150 TOP and ≥ 100 REST settled fills (roughly two to four weeks at current flow). A PASS
makes ×3-in-TOP a candidate sizing treatment for a successor canary, an operator hard stop with a
restated envelope.

**Recon census:** done in this run (the type table above): five cells are readable in both
windows, so Leg R is runnable today and Leg F accrues from today.

### Holds (MMSELL-scoped)

| hold | trigger | state |
|---|---|---|
| SIZE-SELECTION (Z4) | ≥ 100 fills from takers < 3 contracts | parked; low decision value |
| QUEUE-DEPTH forward (from 09-30) | operator schedules a forward `at_rest` pre-registration | parked, unscheduled |
| INVERSE-OFFSET, STRANGLE v2, ENDGAME-TAKER, BARRED-UNIVERSE (from 09-30) | unchanged | carried |

## What this means for the operator's question

Sizing up works mechanically: the flow that fills us is large enough to fill 3-lots. What it
multiplies is the problem. The book earns roughly a third of a cent per fill, indistinguishable
from zero. "Which markets are best" cannot be answered per series on this book's flow. It can be
asked per market type, and TYPE-RANK is that question with a kill line. If it passes, ×3 on the
top two types is worth on the order of $15–20/month. If it fails, sizing is closed as a return
lever until something lifts the per-fill edge first.

## Ops requests made by this run (all read-only, all outcome-blind)

`sizeup-census-1001` (fills per series per window), `sizesel-census-1001` (fill↔trade match, read
as size 1: measurement trap, kept for the record), `sizesel-census-1001b` (taker aggregate size).
Channel reset to `noop`.
