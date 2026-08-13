# PMDIV — does Polymarket know something Kalshi doesn't about tomorrow's temperature?

*Thesis written 2026-08-13, before any validation ran; the falsifiable predictions below are
pre-registered so the result can't be quietly re-scoped afterward. Status: **KILLED 2026-08-13**
(P0 PASS / P1 FAIL / **P2 KILL** / P3 FAIL) — see RESULTS.*

## RESULTS (2026-08-13 probe run — `pm_divergence_study`, defaults, htc ≤ 24h)

Sample: 39,740 scored cycles across **198 settled events**, 3 cities (AUS/LAX/MIA), Jun–Aug 2026.
Polymarket ladders reconstructed into 41,192 batches of **exactly 11 buckets** (min 11, max 11) —
the clustering sanity guard, so none of the numbers below are the single-bucket artifact. Median
Polymarket ladder age at the decision point: 5.3 min.

- **P0 — PASS.** Corrected `pm_err = 1.29°F`; the single-bucket artifact recomputed on the same
  cycles gives **9.83°F**, reproducing `weather_validation`'s 9.38°F. **Confirmed: that number was
  the defect, not Polymarket.** Polymarket is a perfectly reasonable forecaster in absolute terms.
- **P1 — FAIL** (not a kill). `pm_err 1.29` vs Kalshi `mkt_err 0.74` = **1.75×** (bar ≤1.5×, kill
  >2.0×). Polymarket is meaningfully worse than Kalshi but not catastrophically so.
- **P2 — KILL.** The pre-registered kill criterion fired: **both outer bands ≤50% pm-better.**

  | band (°F) | n | pm_err | mkt_err | pm_better% |
  |---|---|---|---|---|
  | (−inf,−3) | 146 | 4.60 | 1.02 | **1%** |
  | [−3,−1) | 9,797 | 1.90 | 0.65 | 9% |
  | [−1,+1) | 27,945 | 1.05 | 0.76 | 27% |
  | [+1,+3) | 1,840 | 1.44 | 0.85 | 42% |
  | [+3,+inf) | 12 | 3.48 | 1.40 | **8%** |

  The gradient is the finding: pm_better% falls monotonically as disagreement grows on the
  negative side (27% → 9% → 1%). **The more Polymarket disagrees with Kalshi, the more reliably
  Polymarket is the one that's wrong** — the identical signature the NWS forecast produced in
  `weather_validation` (0% / 4% in its outer bands).
- **P3 — FAIL.** Buying the Kalshi bucket Polymarket favors: +0.83¢/trade (n=59) and −7.36¢
  (n=11) in the outer bands, against a +2¢ bar, on n far too small to mean anything anyway.
- **P4 — confirms, doesn't rescue.** Outer-band pm_better% by half: 3% / 1%. By city: AUS 0%
  (n=144), MIA 8% (n=12), LAX 100% (n=2 — noise, not a counterexample).

**Decision (per the pre-registered rule):** `pm_divergence` is **refuted as a concept**. Do not
build the grid-free `pm_mean_gap_f` metric or the Polymarket backtest dataset — two PRs saved.
Strip `pm_divergence` from the DSL rather than leave agents a metric we now know predicts the
*opposite* of what its name implies. Family closed.

**Independently of this verdict:** fix `validation.py::_pm_implied_mean` and re-materialize
`weather_forecast_outcomes.pm_implied_mean_f` — a corrupt column is a defect regardless of whether
anyone trades on it. The insert-side fix (hoisting `now` in `insert_polymarket_snapshots`) shipped
with this probe.

## One-liner

Two venues price the same daily-temperature event. When their implied distributions **disagree**,
one of them is closer to the actual extreme — and if it is systematically Polymarket, then
Kalshi's price is stale relative to public information and the gap is tradable on Kalshi alone.

## Why this probe exists, specifically

`pm_divergence` already ships as a DSL metric for the evolutionary agents
(`kalshi_bot/evo/signals.py`). It was built on an assumption-free premise — *"do two venues
disagree?"* needs no forecasting skill of our own, which is why it came before any model-based
metric. But the premise was never tested. Nobody has ever checked whether Polymarket disagreeing
with Kalshi predicts anything.

Two things now make that check urgent rather than academic:

1. **The forecast-based alternative just died.** `weather_validation` (run 2026-08-13) shows our
   NWS/ensemble forecast is worse than the Kalshi market in every window and kind (h8: fc_err 1.49
   vs mkt_err 0.57 on highs), the ensemble loses on log-loss by 0.59–1.35, and — decisively — the
   forecast is right **0% and 4%** of the time in the two extreme forecast-vs-market divergence
   bands. Bigger disagreement, more wrong. Divergence-from-market is therefore *not* automatically
   a signal in this domain; the same test must be run on the Polymarket side before building on it.

2. **The one number we have is not trustworthy.** The same validation run reports Polymarket
   `pm_err=9.38` vs `mkt_err=0.72` (n=39,282), i.e. Polymarket 13× less accurate. Taken at face
   value that kills this family outright. But it is very likely a measurement artifact — see
   *The pm_err defect* below. A probe that computes the implied mean correctly resolves it.

This probe gates roughly two PRs of build (a grid-free `pm_mean_gap_f` DSL metric to replace the
bucket-matching `pm_divergence`, plus a Polymarket backtest dataset so it becomes validatable).
Better to spend one probe than two PRs on an untested premise.

## The pm_err defect (why the existing number is suspect)

`kalshi_bot/weather/validation.py::_pm_implied_mean` selects the newest ladder like this:

```python
latest = max(r.captured_at for r in eligible)
for r in eligible:
    if r.captured_at != latest or r.yes_prob is None:
        continue
```

but `kalshi_bot/repository.py::insert_polymarket_snapshots` stamps `captured_at=_now()`
**per row inside the loop**, so every bucket of a single ladder capture carries a distinct
microsecond timestamp. Exact-equality against the max therefore retains **one row** — whichever
bucket was inserted last — and `pm_implied_mean_f` collapses to that single bucket's midpoint
temperature rather than the probability-weighted mean of the ladder.

**CONFIRMED 2026-08-13, before the probe was written**, by direct query over the whole table:

```
rows_per_ts  n_timestamps
1            451198
```

Every one of the 451,198 distinct `captured_at` values holds exactly **one** row — no ladder ever
shares a timestamp, so the exact-equality filter always retains a single bucket. Therefore all
140,428 `weather_forecast_outcomes.pm_implied_mean_f` values are this artifact, and `pm_err=9.38`
measures nothing about Polymarket. The column needs re-materializing after a fix.

The contrast confirms the cause: `insert_weather_bucket_snapshots` (`repository.py:733`) hoists
`now = _now()` **outside** its loop, so Kalshi ladders share one timestamp and group correctly;
`insert_polymarket_snapshots` (`repository.py:1316`) calls `_now()` **inside** it. Hoisting it
fixes future captures; historical rows keep their per-row stamps, so the probe must cluster
regardless.

The probe therefore computes the Polymarket implied mean by **capture batch** (rows clustered into
a ladder by a time gap, not exact timestamp equality) and reports the corrected `pm_err` beside the
broken one, so P0 is measured rather than asserted.

Note the live path is *not* affected: `kalshi_bot/weather/consensus.py::pm_implied_mean` receives
the bucket list directly and weights it correctly. The defect is confined to the validation
dataset materialization.

## Mechanism — who is on the other side, and why it would persist

Polymarket's weather markets are thinner and draw a different crowd (crypto-native, global) than
Kalshi's (US retail + the handful of us running bots). Neither venue is obviously the informed
one. Two directions are plausible and the probe distinguishes them:

- **Polymarket leads** — its traders react faster to an NWS update or a morning observation, and
  Kalshi's ladder lags. Then `pm − kalshi` predicts the Kalshi move, and buying the Kalshi side
  Polymarket favors is +EV.
- **Polymarket is noise** — thin books, wide spreads, stale quotes on a 2°F grid. Then disagreement
  is mostly Polymarket being wrong, and trading toward it is trading toward noise. This is what the
  NWS forecast turned out to be, and it is the null hypothesis here.

For the edge to persist it needs a reason nobody has closed it: cross-venue weather arbitrage
requires accounts, capital and bucket-mapping work on both sides for a market that settles a few
hundred dollars of volume a day — plausibly below the threshold anyone bothers with.

## Pre-registered, falsifiable predictions

The probe (`scripts/pm_divergence_study.py`, ops-runnable, read-only, reads our own
`polymarket_snapshots` / `weather_bucket_snapshots` / `weather_settlements`) measures over the
~200 settled events where both venues quoted, Jun 12 – Aug 12 2026.

- **P0 — the artifact is real (diagnostic, not an edge claim).** Recomputing the Polymarket
  implied mean by capture batch yields `pm_err` **materially below 9.38°F** — concretely, under
  3.0°F. PASS makes the remaining predictions meaningful. If `pm_err` stays ≈9.38 with correct
  batching, Polymarket really is that bad and **P1–P3 are moot: KILL**.
  *(The batching defect itself was confirmed by direct query before this thesis was finished — see
  above. What remains open, and what P0 actually measures, is the corrected error value.)*

- **P1 — Polymarket is competitive at all.** Correctly computed, Polymarket's implied mean is
  within **1.5×** the Kalshi market's absolute error on the same cycles (i.e. `pm_err ≤ 1.5 ×
  mkt_err`), pooled. **KILL if `pm_err > 2.0 × mkt_err`** — a venue that much worse cannot inform
  a Kalshi price regardless of what the divergence bands say.

- **P2 — divergence is informative (THE prediction).** Binning cycles by `pm_mean − kalshi_mean`
  into bands (−inf,−3), [−3,−1), [−1,+1), [+1,+3), [+3,+inf), Polymarket is closer to the actual
  extreme in the **outer bands** at a rate materially above chance: `pm_better% ≥ 55%` in at least
  one outer band with n ≥ 300, and not below 45% in the opposing outer band.
  **KILL if `pm_better%` in both outer bands is ≤ 50%** — that is the exact signature the NWS
  forecast produced (0% / 4%), and it means disagreement predicts *Polymarket* being wrong.

- **P3 — it survives the cost hurdle.** Among cycles in a passing outer band, buying the Kalshi
  bucket Polymarket's distribution favors, at the then-current Kalshi ask, held to settlement,
  returns **> +2¢/contract net of the entry fee** (`ceil(0.07·P·(1−P)·100)`). **KILL if ≤ 0¢** —
  a directional signal that doesn't clear the fee is not a book.

- **P4 — robustness.** P2's sign holds on both halves of the date range and on at least two of the
  three cities (LAX / MIA / AUS) separately. A result carried by one city in one fortnight is not
  promotable.

**Decision rule.** Build the two PRs (grid-free `pm_mean_gap_f` metric + Polymarket backtest
dataset) only if **P1 and P2 both pass and P3 > 0¢**. If P2 fails, `pm_divergence` is refuted as a
concept: strip it from the DSL rather than leave a metric agents can gate on that we know predicts
nothing, and close the family. P0 gets fixed and the column re-materialized **either way**, since a
corrupt validation column is a defect independent of this idea's fate.

## Cost / capacity

Weather buckets trade 1–99¢; the fee is quadratic and worst mid-ladder — `ceil(7·p·(1−p))` gives
**2¢** at 50¢ and **1¢** at 10¢ or 90¢. Entry-only if held to settlement. Capacity is the binding constraint, not the fee:
Polymarket covers 3 cities and only ~200 settled events exist over two months, so even a real edge
is a handful of contracts a day. **This can be a *component* of $100/month, not a book that gets
there alone** — which is itself a reason not to spend two PRs on it without P2 passing.

## Correlation to existing books

Uncorrelated with everything live: mmsell is cheap-tail selling on sports/crypto, theta is crypto
ladders. All weather books were retired 2026-08-12 (`RESEARCH_JOURNAL.md`), so this shares a
*domain* with dead books but not a mechanism — those died on forecast skill, which this
deliberately does not use. It does share the domain's demonstrated hazard: weather markets here
have been efficient against every signal we've brought.

## Honest limitations

- **n is small.** ~200 settled events × ~6 buckets, 3 cities, 63 days, one season. P4 exists
  because of this, and even passing it leaves a summer-only result.
- **Our Polymarket capture is throttled**, so the "latest ladder" at a given cycle can be minutes
  stale; that biases *against* finding Polymarket-leads, so a positive P2 is conservative but a
  negative P2 is partly confounded with capture lag. The probe reports ladder age so this is
  visible rather than assumed.
- **No lookahead**: each cycle uses only ladder rows captured at or before that cycle's timestamp;
  settlement is used solely to label the outcome, never to select or price a point.
