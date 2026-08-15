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

Declared-but-unprovided metrics (the model-based book metrics) keep the existing
analysis scripts as their **reference implementations**: `realizable_cents_per_trade`
and `fill_model_coverage_pct` (`scripts/mmsell_fill_model.py`),
`realized_tail_hit_ratio_vs_modeled` (theta), `live_settled_contracts` /
`live_cents_per_contract` / `twin_live_winrate_gap_pp` (`mmsell_live`,
`live_paper_parity`), `clean_pairs` / `pair_win_rate_95lb_pct` (A5 pairing audit),
and the FREEZE probe quantities. Those scripts stay the parity checks and deep
reads; they are no longer the source of lifecycle truth — a gate needing one of
these is honestly BLOCKED_DATA until its canonical provider lands.

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
  dry-run and writes nothing.

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
