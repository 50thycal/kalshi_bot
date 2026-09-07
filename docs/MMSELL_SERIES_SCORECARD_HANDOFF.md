# Handoff — score every mmsell series, and bar trading below a threshold

**Written 2026-09-06, revised 2026-09-07 against measured data.** Session role: **Research Lab**.
Scope: **mmsell family only** — do not touch perps, pin15, tfav, theta, wcprop, weather, xgame.

## What the operator asked for

Replace the binary `graduated`/not bar with a **numeric score per series**, where higher means a
better expected outcome, and **bar mmsell from entering markets below a threshold**. Score on
**edge**, not raw P&L, so every series is compared apples to apples. And explicitly: *"we don't
need eight hundred data points just to give it a score."*

**That is correct, and an earlier draft of this handoff was wrong to imply otherwise.** The
n≈800 figure in `docs/MMSELL_ROADMAP.md` §1 powers a test on **mean per-trade P&L** — a heavy
tailed money variable. That is not what we are scoring. The measurements below replace it.

## The measured facts this design rests on

All from production, mmsell family, settled + closed_sl, twins excluded, 2026-09-07.
**75,542 trades · 534 series · 8,866 contests.**

### 1. Break-even is nearly determined by the entry price, so `edge` has ONE noisy term

| entry band | be% | loss% | **edge** | contests | se(loss%) |
|---|---:|---:|---:|---:|---:|
| ≤7¢ | 6.69 | 6.05 | **+0.64pp** | 3,649 | 0.39pp |
| 8–10¢ | 8.28 | 6.86 | **+1.42pp** | 3,460 | 0.43pp |
| 11–15¢ | 11.89 | 10.56 | **+1.33pp** | 3,087 | 0.55pp |
| 16–25¢ | 18.24 | 16.39 | **+1.85pp** | 3,015 | 0.67pp |
| >25¢ | 28.52 | 26.80 | **+1.72pp** | 4,259 | 0.68pp |

`be% = avg_win/(avg_win+|avg_loss|)` lands almost exactly on the entry band every time. It is
structural, not estimated. So **`edge = be% − loss%` is a break-even constant minus a binomial
proportion** — and a proportion is far better powered than a mean of money. This is why the
n≈800 number does not apply.

**Every band is positive**, +0.64 to +1.85pp. The family edge is real but *thin*.

### 2. Per-series data is far thinner than anyone assumed

| series with ≥N contests | count (of 534) |
|---|---:|
| ≥300 | 8 |
| ≥100 | 12 |
| ≥50 | 28 |
| ≥20 | 69 |
| **<20** | **465 (87%)** |

**Median contests per series: 3.** Contests, not markets, is the independence unit — one blowout
settles a whole nested ladder against a seller at one instant (`KXNFLSPREAD`'s 382 markets were
44 games; 2 games carried 48% of the loss).

### 3. What a series' own record can and cannot resolve

| own contests | se(loss%) | own edge resolvable only if |
|---:|---:|---|
| 3 | 17.3pp | \|edge\| > ~35pp |
| 20 | 6.7pp | \|edge\| > ~13pp |
| 50 | 4.2pp | \|edge\| > ~8.5pp |
| 100 | 3.0pp | \|edge\| > ~6pp |
| 300 | 1.7pp | \|edge\| > ~3.5pp |
| 635 (the max) | 1.2pp | \|edge\| > ~2.4pp |

Against a family edge of ~1–2pp, **no series has enough of its own data to resolve a small
difference.** What a series' own record *can* do is expose a disaster: a −8.5pp series is
detectable at 50 contests, a −6pp one at 100.

## The design this implies

**Partial pooling.** Every series is scored immediately — a thin series simply inherits its
**entry band × market type** rate, which has thousands of contests behind it, and its own record
pulls it away from that prior only as far as its sample justifies:

```
loss_hat(series) = (own_losses + k · prior_rate) / (own_contests + k)
edge(series)     = be%(series, from its own realized prices) − loss_hat(series)
```

`k` is the pooling strength in units of contests; derive it from the between-series variance
within a band, do not pick it. Report what each of the 138 scores at the chosen `k`.

### The score, concretely

```
loss_hat  = (own_losing_contests + k · band_loss_rate) / (own_contests + k)
edge      = be% − loss_hat                    be% = avg_win / (avg_win + |avg_loss|)
score     = 50 + 50 · (edge − band_expected_edge) / spread
```

**50 means "behaves exactly like its band."** Below 50 is worse than its band, above is better.
A series with 3 contests lands at ~50 whatever its record; a series with 300 is mostly its own.
The hard floor applies on top: no recorded rules review caps the score below the threshold
regardless of edge.

`spread` is the between-series sd of edge within the band — the same quantity `k` is derived
from, so the two are consistent by construction rather than tuned separately.

**Present `k` as a decision the operator can actually make.** Do not ask them to approve a
number in contest units; convert it and ask the real question: *"a series running at edge −10pp
gets barred after N contests."* That N is what a person can judge, and it is the whole
risk/latency trade-off in one figure — too small and the bar fires on noise, too large and a
bleeding series keeps trading for months.

This gives the operator exactly what they asked for: **a score for every series on day one, no
800-observation gate**, without pretending a 3-contest series has evidence of its own.

**Score the deviation, not the level.** Because every band is positive and the spread is thin,
a score built on absolute edge would rank noise. Score each series against **its own band's
expected edge**, so the question is "does this series behave worse than its band?" — which is
the only question the data can answer.

**Bar on evidence of harm, never on absence of evidence.** A thin series sits at its band prior,
which is positive, so it is not barred. A series is barred only when it has accumulated enough
contests for its own bad record to pull it below the line. Get this backwards and the bar
quarantines every new market permanently by construction, since a barred series stops
accumulating the history that would clear it.

### Proposed components — confirm weights and threshold with the operator

| component | source | notes |
|---|---|---|
| **pooled edge vs band** | above | the core; the operator's ask |
| **own-sample weight** | contests | how much of the score is the series' own evidence |
| **loss concentration** | `worst3%` | broad drift vs two bad afternoons — different findings |
| **mechanism understood** | `rules_reviewed_at` | **hard floor**, see below |

**The hard floor:** a series with no recorded rules review **cannot clear the threshold**,
however good its edge. Otherwise a contract nobody has read trades live on a lucky month — which
is what `KXNFLSPREAD` did (classified, 1,486 settled markets, −$151.26). This is the surviving
half of the old two-part bar.

## Known problems — do not rediscover these

1. **The rules audit rests on ONE signal.** Kalshi's `settlement_source` fired **zero** times
   across all 138 series, so every verdict is a single regex over rules text. That is why **no
   `rules_reviewed_at` has been recorded** and the backlog is still 138 of 138. Scoring
   "mechanism understood" off that alone would launder a regex into a human's signature.
   **Either find a second signal, or make that component require a human sign-off.** This is the
   first real decision of the task.
2. **Dedup does not collapse.** `docs = 8` for every series audited — rules text embeds
   per-market specifics, so "distinct documents" is really "8 near-identical markets".
3. **The audit worklist is graduated-only**, so it cannot see a misclassification below the top
   tier. Five of the six series in the `KXUE` prefix collision were never audited.
4. **The scan sees 4,005 series; the manifest holds 138**; 265 traded series have no manifest
   row. Filter to series *mmsell* has traded before treating that as the queue.
5. **`markets` has not been written since 2026-06-08.** Do not build on that table.

## Reuse, do not rebuild

| thing | where |
|---|---|
| per-series edge, break-even, contests, worst3%, live flag | `scripts/mmsell_series_pnl.py` |
| the decision ledger (state, reviewer, review date) | `kalshi_bot/registry/series_manifest.json` |
| the entry gate a threshold would bind | `registry.admits()`, `mmsell_live_min_tier`, per-book `universe=` |
| rules-vs-taxonomy verification | `scripts/series_rules_audit.py` |
| the review queue, ranked by real exposure | `scripts/series_registry_review.py` |
| settlement evidence gathering | `scripts/mmsell_taxonomy_audit.py` |

## The work, in order

**Phase 1 — score the 138.** Build `scripts/mmsell_series_scorecard.py` (ops-allowlisted,
stdlib + psycopg, read-only). Emit every series with its components, its own-sample weight, and
its total. **Gate nothing.** Bring the operator the score distribution and a threshold justified
by where scores actually fall — including how many series the threshold would bar today and
which ones.

**Phase 2 — settle the mechanism-understood component.** Resolve problem (1).

**Phase 3 — wire the threshold to the gate.** Add score + verdict to the manifest row schema;
let `registry.admits()` require a minimum score. **Platform Change Review**, and expect an epoch
decision for any book whose universe moves. Re-check `LIVE_STRATEGIES` (empty as of 2026-09-06)
before assuming no live exposure.

**Phase 4 — the collection plan for the rest.** For each mmsell-traded series below scoring
weight, state how many contests it needs to detect a −6pp deviation and how long that takes at
its current rate. Paper trades everything regardless — that is how the history accrues — so this
is a waiting list, not a work backlog.

## Rules that bind this task

- **Gates decide promotions, not P&L.** A score is a report until Phase 3; even then the
  threshold is a governance bar, not a strategy.
- **Never weaken a live safeguard.** The score may narrow what trades live; widening needs
  explicit operator confirmation.
- Changes to `SERIES_TYPES`, metric definitions, fees, fills or execution are shared platform
  semantics → **Platform Change Review**.
- Problems belong in an **Experiment OS issue**, not prose.
- The manifest moves **only by PR**. No script may write a review or a passing score.

## Context docs

`docs/SERIES_REGISTRY.md` · `docs/SERIES_RULES_AUDIT.md` · `docs/MMSELL_UNIVERSE_REVIEW.md` ·
`docs/MMSELL_ROADMAP.md` §1 (the variance numbers — note the scope correction above) ·
`docs/OPS_RUNBOOK.md` ·
`docs/IDEA_MODEL_SCORECARD.md` — **read for calibration.** This repo's existing scoring ledger
records **18 idea-model promotions → 0 currently-live paper books**. A scoring system here that
passes most of what it scores is probably mis-calibrated.
