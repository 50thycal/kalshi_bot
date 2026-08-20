# Experiment OS — metrics + gate evaluation (PR 3)

Spec: `docs/EXPERIMENT_OPERATING_SYSTEM_SPEC.md` §11–12. Foundation:
`docs/EXPERIMENT_OS_FOUNDATION.md`. Migration: `docs/EXPERIMENT_OS_MIGRATION.md`.
This layer answers **"what is this experiment's current evidence, and what does its
pre-registered gate say?"** from structured state, with no per-book status logic.

## The metric addressing contract (never guess scope)

Every evaluated value is computed for a fully resolved `MetricScope`: experiment /
version / epoch / arm / deployment kind / concrete strategy tags / evidence window /
platform snapshot — plus the metric-engine revision stamped on every persisted
result. A gate clause must address its scope explicitly:

| clause form | meaning |
|---|---|
| `"arm": "<key>"` | one declared arm |
| `"arm": "*"` | every declared arm individually (pass_all: each must pass; fail_any: any arm trips) |
| `"scope": "experiment"` | the version as a whole (probe-instrument quantities) |
| *(no arm)* | legal only on a single-arm version — a unique referent is resolution, not inference |
| `"treatment"/"control"` | in-experiment delta between two declared arms |
| `"arm" + "external_control": {"experiment_key","arm_key"}` | cross-experiment control, first-class (the mmsellA4/Tmmsell → mmsell10 shape) — matched window, and the external epoch's snapshot must equal the evaluating one or the delta is BLOCKED_PLATFORM |
| `"deployment_kind"` | which of the arm's deployments back the value (default `paper`; live and twin tags are never silently mixed in) |

Anything ambiguous — no arm on a multi-arm version, an undeclared arm, a delta
without a control, an unresolvable external reference, a metric key outside the
registry — **refuses evaluation as BLOCKED_INTEGRITY naming the exact problem**.
New gates cannot even register/freeze in that state (`validate_gate_scopes` runs at
freeze, and at registration on already-frozen versions); grandfathered rows block at
evaluation instead of being quietly reinterpreted — the fix is a corrected gate on a
new version, never an in-place guess.

The migrated manifest was normalized to this contract at `2026-08-15.2` **before
any production registration existed** (verified via the ops channel: zero
experiments recorded) — a pre-first-registration translation of the registry's
prose, with thresholds/metrics/floors untouched. `docs/BOOK_REGISTRY.md` remains
the authoritative historical pre-registration text.

## The registry (`metrics.py`)

One namespace for every metric key a gate may reference — an unregistered key is
always a typo or an undeclared quantity, never accepted. Universal metrics are
computed from `paper_trades` through deployment-arm tags:

`settled_trades`, `settled_contracts`, `entries`, `open_trades`, `voided_trades`,
`realized_pnl_usd`, `pnl_cents_per_trade`, `pnl_cents_per_contract`,
`win_rate_pct`, plus `delta.<any of these>` and the paired
`relative_entry_deficit_pct` (the registry's own entry-count-differential method
for mmsellA4's rejection rate).

### `realizable_cents_per_trade` — the fill-model provider

The metric the mmsell cohort's promotion decisions actually turn on. Paper assumes
a resting maker order always fills; live it fills ~70% of the time and the ~30% it
misses are disproportionately the winners, so paper's headline averages in P&L a
maker can never capture. `docs/MMSELL_FILL_MODEL.md` has the analysis and the
mistake this prevents: on blended paper, mmsell6 and mmsell11 looked promotable
and are **mirages** under the correction.

The provider projects the scope's settled entry-price mix through the live
calibration in `kalshi_bot/fill_calibration.py` — one shared module, because the
evo sandbox's backtests and the promotion gates must read the same numbers. Its
companion `fill_model_coverage_pct` is not decoration: **read the realizable
number against its coverage**, since an estimate speaking for a fifth of a book is
not the same claim as one speaking for all of it.

Rules that keep it honest, each pinned by a test:

* a price cell with fewer than 8 live fills is **untrusted** — excluded from the
  estimate, never borrowed from a neighbouring cell;
* a book whose price mix no trusted cell reaches is **MISSING**, not zero and not
  paper's number. Answering anyway would restore the exact mirage the model exists
  to catch;
* a trade with no recorded entry price cannot be placed in a cell, so it counts as
  uncovered rather than being dropped from the denominator (which would inflate
  coverage);
* fillability is keyed by the market's **yes-equivalent cent**, so a resting NO bid
  at 92¢ and a resting YES bid at 8¢ are one book event.

**This is not a platform change.** The calibration is already declared in the
active `FILL_MODEL` revision (`assumed_fill_plus_mmsell3_calibration`), and
`METRICS_ENGINE` (`pnl_scripts_2026_08`) already names `mmsell_fill_model` as part
of the measurement layer. The provider implements what the active revision already
declares; it moves a pinned platform fact out of a script and into the canonical
engine. No snapshot changes, so no epoch goes `BLOCKED_PLATFORM`. **Changing the
calibration numbers is a different act** — that is a new `FILL_MODEL` revision with
an impact classification, never an edit in place, and every computed value records
`fill_calibration_version` in its provenance so evidence from two calibrations can
never be pooled silently.

### `clean_pairs` / `pair_win_rate_95lb_pct` — the strangle's unit of evidence

A5 is a two-sided position: one cheap-YES leg and one cheap-NO leg on the same
event. One settlement can never lose both, which is the whole thesis — so the unit
of evidence is the **pair**, not the trade, and the gate's confidence-interval math
assumes every counted observation carries the hedge.

`docs/MMSELL_ANCHOR_SET.md` records what happens when it does not: before the
2026-08-14 pairing boundary, one event opened four same-side legs on four strikes
of one game. Those are positively correlated — precisely the risk a strangle
exists to avoid.

A **clean pair** is an event holding *exactly one* settled YES leg and *exactly
one* settled NO leg, resolved through `mmsell_settlement_meta.event_ticker` — the
same canonical ticker→event mapping the tracker's own one-leg-per-side cap uses
(`repository.event_has_strangle_leg`). Market tickers are never string-parsed into
events. Everything excluded is counted in provenance rather than dropped:
`one_sided_events`, `multi_leg_events`, `incomplete_pairs_censored`,
`trades_without_event_mapping`.

A **part-settled pair is censored, not a loss.** One open leg means the pair's
outcome is unknown; scoring it zero would be the missing-is-not-zero error on the
exact unit that decides the gate.

`pair_win_rate_95lb_pct` is the **Clopper-Pearson exact one-sided 95% lower bound**
on the share of complete pairs with positive *combined* P&L. Exact, not
normal-approximate, because the approximation error *is* the decision: the
strangle backtest observed 23/23 — a 100% win rate — whose exact lower bound is
**87.79%**, which is why it FAILED the 93.9% bar. A Wald interval has zero width at
100% and would have passed it. The provider reproduces that documented 87.8%
exactly, and the closed form `0.05 ** (1/n)` at a perfect record is pinned as an
independent check.

**The pairing boundary is not in this code.** It is carried by the gate's
`evidence_started_at`, so moving it is a contract change, not a code change.

### Provider revisions — versioning at the right granularity

Two things need versioning, and they are not the same thing:

* **`METRICS_ENGINE_REVISION`** — engine-wide semantics. Bumping it de-authorizes
  every standing recorded result until re-evaluated, which is correct when the
  meaning of an existing metric moves, and far too blunt when a previously
  unavailable provider is simply implemented.
* **`MetricDefinition.revision`** — that provider's OWN implementation revision:
  `universal_v1`, `fill_model_v1`, `pair_metrics_v1`, and the sentinel
  `unprovided` while no implementation exists.

Every computed value records `provider_revision` in its provenance; every gate
result records the `provider_revisions` it actually used; and the gate runner's
dedupe fingerprint binds to that set. Three consequences, each pinned by a test:

1. **"Provider unavailable" and "provider implemented" never share an identity.**
   A result recorded while `clean_pairs` had no provider carries `unprovided`; one
   computed by the implementation carries `pair_metrics_v1`. The recorded evidence
   distinguishes them, which a single engine-wide string could not.
2. **Changing one provider re-records exactly the gates that read it.** Shipping
   `pair_metrics_v2` changes the fingerprint of every gate with a pair clause and
   leaves every other gate's standing result untouched.
3. **Adding a provider needs no engine bump.** `mmsell-anchor-strangle` re-records
   because its verdict, clause shape and provider set all changed; nothing else
   is disturbed.

`pr6_fill_model_v1` (the fill-model bump) predates this mechanism and stays as
recorded history — it is not rewritten. Future provider work versions the provider,
and `METRICS_ENGINE_REVISION` is reserved for genuine engine-wide semantic changes.

**`METRICS_ENGINE_REVISION` bumped to `metrics_engine:pr6_fill_model_v1`** — the
engine can now compute what it previously refused, and a verdict must never
outlive the semantics that produced it. `ALLOWED_METRIC_REVISIONS` tracks the
single current revision, so every result recorded under `pr3_v1` stops authorizing
promotions until it is re-evaluated. That is the intended "re-evaluate" path, and
it self-heals: the gate runner's fingerprint includes the metric revision, so the
next evaluation cycle re-records every gate under the new one.

**Settled** = every terminal status carrying real P&L (`settled`, `closed_sl`,
`closed_tp`, `closed_timeout`); filtering `settled` alone silently drops
stop-closed trades — the recorded mmsellA1–A3 reading error, pinned by test.
`closed_void` (annulled market) is censored: surfaced as its own metric, never
pooled into n. Windowing is on `created_at` (entry time), the repo's cohort-floor
convention.

Zero / empty / missing are three different facts:
- a count over a healthy source with no rows is a **meaningful zero**;
- a mean over an empty sample is **undefined** (never coerced to 0) — floors/HOLD
  handle it;
- a metric with **no provider** is `missing` → the gate evaluates **BLOCKED_DATA**
  naming the metric and its reference implementation.

Declared-but-unprovided metrics keep the existing analysis scripts as their
**reference implementations**: `realized_tail_hit_ratio_vs_modeled`,
`twin_live_gap_cents`, and the FREEZE probe quantities. Those scripts stay the
parity checks and deep reads; they are not the source of lifecycle truth — a gate
needing one of these is honestly BLOCKED_DATA until its canonical provider lands.

### Live-execution providers (`live_exec_v1`)

`live_settled_contracts`, `live_cents_per_contract` and `twin_live_winrate_gap_pp`
read real-money execution: `live_orders` × `fills` × `positions`.

**Addressing is obeyed, never repaired.** All three are defined only at
`deployment_kind="live"`. Requested at `paper` or `paper_twin` they return
**MISSING**, with the mismatch named and `addressing_error` in provenance — they
do **not** substitute the live deployment. Two imported live-canary gates are
malformed in exactly that way, and a provider that inferred "they probably meant
live" would make those gates appear to work, hide the defect a corrected Version
exists to fix, and let a promotion turn on evidence the registered contract never
asked for. This routes *before* the empty-scope fallback, which answers `0` for a
count — a confident wrong "no live contracts" that a `>=` floor would read as
real evidence, and a `<=` clause could even pass on.

**`live_cents_per_contract` is per CONTRACT.** `scripts/mmsell_live.py` divides
realized P&L by settled *positions* and labels it `live_$/ct`; that equals per
contract only while every position is a 1-lot. The canonical definition is **total
realized live P&L / actual filled contracts**, so a 5-lot losing $1.00 is five
contracts losing 20¢ — not one −100¢ observation. Parity on 1-lots and the
divergence on multi-lots are both pinned in
`tests/test_experiment_os_live_metrics.py`.

Three exclusions, each counted in provenance rather than dropped silently:

| excluded | why |
|---|---|
| still-open markets | realized P&L exists only for closed positions; counting open contracts in the denominator would make the rate worsen simply because a position opened. Numerator and denominator come from the same market set. |
| contested markets | `positions` is keyed by market, not strategy, so a market two arms traded cannot be split. The reference script attributes it fully to *both*; for an A/B promotion gate that is double-counting. |
| closed-but-unpriced markets | a settled position with no `realized_pnl` is unknown, not zero. |

Only **entry** fills count (`action='buy'`): a position is entered by buys and
closed by sells, and both write fill rows, so counting both would double the
denominator. Only the **newest** `positions` snapshot decides, because the table is
append-only and an older `quantity=0` row may simply predate a re-entry.

### Twin-resolved providers (`twin_coverage_v1`, `tail_v1`, `twin_gap_v1`)

All are addressed at the **live** scope and resolved against that deployment's
registered twin — the structural `twin_of_deployment_id` edge, never a `_pt`
naming convention.

**The twin is the measurement instrument.** `live_orders` carries no
`model_probability`, and a live position exited early under TP/SL carries a P&L
sign that is *not* a settlement outcome. So both the modeled probability and the
tail-hit outcome come from the twin, which holds to settlement on the same
market. Settlement is a property of the market, not of who held it.

| metric | what it measures |
|---|---|
| `live_settled_markets` | the **independent unit** — contracts on one market share one settlement, so a floor in contracts overstates precision by 1.4–3.0× on these books |
| `twin_mirror_coverage_pct` | share of live markets **entered** that the twin also entered. The denominator is entries, not settlements, because the mirror fires at entry — which is where a 25%-coverage twin hid in production |
| `twin_model_coverage_pct` | share of **settled** live markets whose modeled probability resolves from the twin — the evidence set itself as denominator |
| `realized_tail_hit_ratio_vs_modeled` | `R = O / Σpᵢ` over settled markets. Exposes `observed`/`expected` for the `poisson_exact` bound. **MISSING below 90% model coverage** |
| `twin_live_gap_cents` | twin rate − live rate over **each leg's own** settled set — the adverse-selection read |
| `twin_live_paired_gap_cents` | per-market **paired** difference — an execution *fidelity* check |

**The two gaps are not interchangeable, and this is the trap.** Pairing collapses
the variance ~15×, which makes the paired gap look like the answer to every power
problem. It isn't: it conditions on live having **filled**, and a maker's adverse
selection operates through *which orders fill*. Measured on theta4, the paired gap
read **−0.22¢** against an unpaired **+5.83¢** — six cents apart and opposite in
sign. Use the unpaired gap for the hypothesis and the paired one to check the twin
is really a twin.

Both gaps are **lower-is-better**: a gap *is* adverse selection, so a positive
`delta.twin_live_gap_cents` means the treatment is **worse** — the opposite
reading from `delta.live_cents_per_contract`.

Missing model data is never imputed. Imputing the book's mean pulls `R` toward 1,
which is toward **passing**; below the coverage threshold the metric is MISSING
instead, because the surviving markets were selected by a data defect and the
bias then has an unknown *direction* — worse than a wide interval, which at least
advertises its own width.

`twin_live_winrate_gap_pp` resolves the twin through `twin_of_deployment_id` and
the matching `arm_key` — the structural edge, never a `*_pt3` naming convention —
and is **MISSING** when no twin is registered. Its `n` binds on the **smaller**
leg, so a sample floor binds on the side that limits the comparison. With no
settled evidence on a leg it is undefined, not zero: two books with nothing
settled are not two books that agree.

## Bound clauses (`bounds.py`)

A clause normally compares a **point estimate** to a threshold. That is right for
a count or a coverage percentage and wrong for a promotion decision: `mean > 0`
passes half the time when the true effect is exactly zero, **at every sample
size**, so a sample floor controls variance without ever defining a
false-promotion standard.

A clause carrying `bound` compares the **bound** instead:

```json
{"metric": "live_cents_per_contract",
 "bound": {"direction": "lower", "confidence": 0.99, "method": "normal"},
 "op": ">", "value": 0, "arm": "theta4", "deployment_kind": "live"}
```

- **`direction` is never inferred from the operator.** `UCB > t` and `LCB > t` are
  different tests, and a metric's own higher/lower-is-better direction does not
  decide which bound a contract wants.
- **`method` must match the statistic.** `normal` needs the provider to supply a
  standard error. `poisson_exact` exists for rare-event ratios — the theta tail
  statistic has an expected count near 4 at its failure floor, where a normal
  bound is simply wrong — and reads `observed`/`expected` from provenance.
- **An uncomputable bound is MISSING**, never a fallback to the point estimate.
  Falling back would restore exactly the error rate the bound was bought to
  remove.
- Standard errors count the **independent unit**. `live_cents_per_contract` is a
  ratio estimator over settled *markets*, so its SE is the ratio-estimator
  variance on markets even though its `n` is contracts. A delta adds its two
  arms' variances, and is None if either leg lacks one.

**Sequential caveat.** A bound re-evaluated every cadence is a sequential test and
its per-look confidence is not its lifetime rate. Measured on the two live-canary
promotion gates, a 95% bound evaluated continuously over a 3× horizon carries
~18% lifetime false promotion against ~5% for a 99% bound. Clause provenance
records the look count so a reader does not mistake one for the other.

## The evidence horizon

A bound clause fixes the error rate **per look**. A gate evaluated on every
cadence takes many looks, and the lifetime rate is not the per-look rate —
measured on the two live-canary promotion gates, a 99% bound holds ~5% lifetime
false promotion over a 3× horizon and creeps upward past it.

`max_evidence_horizon` is the half of that calibration that lives in the
contract:

```json
"sample": {"theta4": {"metric": "live_settled_markets", "op": ">=", "value": 600}},
"max_evidence_horizon": {"metric": "live_settled_markets", "value": 1800}
```

At the horizon the verdict is **`HORIZON_EXHAUSTED`**, and:

- **no further authorization look accrues** — never auto-PASS, never auto-FAIL;
- it is deliberately **not HOLD**. A HOLD says "wait for more evidence"; past the
  horizon more evidence will never come, and reading it as a hold is how a
  decision gets deferred forever;
- every clause's standing **is named in the explanation**, so the operator decides
  on the evidence rather than on silence;
- it can never authorize a transition — every authorization path tests `== PASS`.

Freeze-time validation refuses a horizon that is **below its own promotion floor**
(the gate could never render a verdict) or **denominated in a different unit from
the floor it bounds** (a horizon in contracts bounding a floor in markets cannot
be reasoned about). A gate with **no** horizon is unaffected — every contract
frozen before this existed behaves exactly as before.

## Promotion floors versus failure floors

`sample` is the **promotion** evidence floor: how much evidence before a PASS may
authorize advancement. That is a different question from how much evidence before
bad evidence may terminate, and one number for both silently makes safety clauses
unreachable — a catastrophic failure at a fifth of the promotion floor sat at
HOLD while real money kept trading (observed on `mmsell-anchor-strangle`).

A `fail_any` clause may carry its own floor:

```json
{"metric": "realized_tail_hit_ratio_vs_modeled",
 "bound": {"direction": "lower", "confidence": 0.99, "method": "poisson_exact"},
 "op": ">", "value": 1.0,
 "min_evidence": {"metric": "live_settled_markets", "op": ">=", "value": 50}}
```

Such a clause becomes **eligible** on that floor alone and is checked *before* the
promotion floor. A clause **without** `min_evidence` inherits the promotion floor
— the behavior of every gate frozen before this existed, so none of them changes.
`min_evidence` is refused outside `fail_any`: a promotion clause's floor is the
gate's `sample`, and two floors on the pass side would be two answers to one
question.

An ineligible clause is **inert, not false** — it has not been tested, so it
neither fails the gate nor counts as satisfied.

**When an early-failure floor is warranted:** when the failure mode is one the
risk envelope cannot detect. A book that simply loses money is caught by exposure
limits and the kill switch. A book whose *model* is wrong about tail frequency
looks fine on P&L until the tail arrives.

## Evaluation semantics (`evaluator.py`)

`evaluate_gate(session, gate, window_end=None, epoch=None, persist=True)`:

- **Evidence window** = `[max(epoch start, gate evidence_started_at),
  min(requested end, epoch end)]`. Hard floors: pre-epoch rows never count after
  an I2 boundary (tested with 500 poisoned pre-epoch trades); recorded gate floors
  (fee boundary for A4, the 2026-08-13 18:09:40Z cohort restart for Tmmsell, the
  A5 pairing fix) bind tighter where they are later than the epoch start.
- **Verdict order** (deterministic, most-structural first):
  structural ambiguity → BLOCKED_INTEGRITY; platform incomparability →
  BLOCKED_PLATFORM; unresolved integrity events → BLOCKED_INTEGRITY; missing
  metrics → BLOCKED_DATA; floors unmet → **HOLD (underpowered is never FAIL)**;
  any `fail_any` true → FAIL; any `hold_if` true → HOLD; all `pass_all` true →
  PASS; else HOLD. An undecidable clause (empty sample) can never pass.
- **BLOCKED_PLATFORM** (spec §17.4): if the active revision set has moved past the
  epoch's pinned snapshot, evidence is interpretable only when the change's
  activation boundary is **established and beyond the window's end**. An
  unestablished boundary always blocks — the import/merge time is never
  substituted. The remedy is `establish_activation_boundary` and/or a fresh epoch
  under the new snapshot. I1-normalizable history (the 2026-08-11 fee model)
  needs no machinery here: every migration epoch starts after that boundary, so
  epoch-floored evidence is single-fee-model by construction (noted in metric
  provenance).
- **Persistence**: every evaluation (including BLOCKED) writes an immutable
  `experiment_gate_results` row, `computed_by="system"`, carrying epoch, snapshot,
  window, `metric_revision` (`metrics_engine:pr3_v1`), per-clause detail with full
  provenance, and a human-readable explanation. `persist=False` is the CLI's
  dry-run and writes nothing. *Who* runs the evaluator on a cadence, and when a
  new row is a new decision point rather than a heartbeat, is
  `docs/EXPERIMENT_OS_GATE_RESULTS.md`.

## Strict gate-result → transition binding

A PASS is not a reusable permission slip. `PAPER → LIVE_CANARY` and
`LIVE_CANARY → PRODUCTION` verify the supplied result is: from the **promotion
gate registered for exactly that transition**; on the **current version**; bound
to the **current operating epoch** (epoch-less results never promote); carrying
that epoch's pinned snapshot, which must still be the **active** snapshot; and the
**latest** result for that gate (a newer FAIL/HOLD/BLOCKED invalidates an older
PASS). Each refusal path is adversarially tested (stale epoch, stale version,
kill-gate PASS, wrong-transition reuse, superseded PASS, platform drift,
epoch-less manual PASS).

## Reading it

- `python -m kalshi_bot.experiment_os.cli scoreboard [key] [--evaluate]` — per-arm
  current-epoch metrics, gate floors, latest recorded verdicts; `--evaluate` runs
  started gates in dry-run (persists nothing).
- `{"type":"script","name":"experiment_os_status"}` — §6 SCOREBOARD: the same
  aggregates in self-contained SQL plus each gate's latest verdict.
- `read.experiment_scoreboard(session, experiment)` — the shared read path both
  consume; metric snapshots live immutably inside gate results (the spec-optional
  `experiment_metric_snapshots` table is deferred until a consumer needs
  standalone snapshots).

## Operational prerequisite, and what remains for PR 4

The production import has **not** run yet (`EXPERIMENT_OS_IMPORT_ON_BOOT` defaults
off; verified empty via ops before this PR). Until the flag is flipped and one
boot imports, production evaluations have nothing to evaluate — everything here is
fully exercised against test databases. After import, the freeze and A4 gates
evaluate on universal metrics immediately; theta4/Lmmsell/A5/realizable-gated
books report BLOCKED_DATA until their canonical providers land.

PR 4 (unchanged scope): `NEW_ONLY` enforcement, experiment lineage on new
paper/live orders/trades, registration requirement for new books, the stage
transition service in the runtime, live-canary fresh-deployment + twin
enforcement, and the recorded enforcement cutover. Still no automatic promotions
anywhere in this PR — the evaluator computes verdicts; only the operator moves
experiments.
