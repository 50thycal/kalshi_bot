# Experiment OS — the Platform Change Impact Engine (PR 5)

Shared platform changes (fees, fills, taxonomy, execution, data provenance…)
are now **first-class research events** with a canonical workflow, instead of a
human reasoning step scattered across docs. The question the engine answers
mechanically:

> If this shared platform component changes, which active experiments are
> affected, how materially, and what must happen before/when the revision
> activates?

Module: `kalshi_bot/experiment_os/platform_impact.py`. This is the final major
core layer: after it, the work shifts from *building* Experiment OS to
*operating* it (production import → readiness → recorded NEW_ONLY cutover).

## The workflow

```
register_platform_revision(...)                 pending, immutable semantics
        ↓
affected_experiments(revision)                  discovery from PINNED SNAPSHOTS
        ↓                                         (never from scanning docs)
propose_impact(...)  per affected experiment    I0–I4 + required action + rationale
        ↓
accept_impact(...)                              review sign-off; classification FREEZES
        ↓
activate_platform_revision(...)                 REFUSES until every affected active
        ↓                                         experiment is accounted and every
        ↓                                         blocking (I4) disposition is APPLIED
apply_no_action / apply_recompute /             the mechanically safe applications;
apply_new_epoch / apply_new_version /             each records what it produced
apply_pause / apply_retire                        (resulting epoch/version ids)
        ↓
new active PlatformSnapshot                     minted at activation (content-addressed)
```

Discovery may *propose* candidates automatically; **classification is never
invented** — every record carries impact class, action, rationale, decider,
acceptor, timestamps. No silent "probably unaffected": unaffected experiments
get an explicit `NO_ACTION` with a written reason.

## Impact classes → allowed actions

| class | meaning | allowed actions | evidence consequence |
|-------|---------|-----------------|----------------------|
| I0 | no scientific/material impact (logging-only, proven-identical refactor) | NO_ACTION | prior results stay valid; the applied record LICENSES the pinned-vs-active snapshot difference |
| I1 | exactly/safely normalizable (fee re-fee from recorded fields) | RECOMPUTE (or PAUSE/RETIRE) | same epoch may continue **under a NAMED normalizer**; prior results are immutable history — authorization requires a FRESH post-boundary evaluation |
| I2 | sample/environment boundary (taxonomy expansion, candidate-population change, fill-observation regime) | NEW_EPOCH (or PAUSE/RETIRE) | old epoch closes at the MEASURED boundary; evidence never pools across it; old results are historical only in the new epoch |
| I3 | the scientific contract itself changed | NEW_EXPERIMENT_VERSION (or PAUSE/RETIRE) — **never NEW_EPOCH** | the old version's results cannot authorize the successor; the engine never fabricates the new contract |
| I4 | safety / invalidation | PAUSE or RETIRE; `blocks_activation` defaults true | results never override the protective disposition; activation refuses until the protection is APPLIED |

`I1`'s `normalization_ref` is validated, not trusted: it must name a metric in
the canonical registry (`metric:<key>`) or the current metrics engine revision
(`metrics_engine:<rev>`). `normalization_available=True` on the revision is a
claim, not permission — the normalizer must exist and be named.

## The impact record (`platform_impact_actions`, canonical since PR 5)

One row per (revision × experiment): revision + superseded revision + the prior
pinned snapshot, the experiment's version/epoch at decision time, class, action,
`status` (`proposed → accepted → applied` | `exempted`), rationale,
`normalization_ref`, decided/accepted/applied actor+timestamp, resulting
epoch/version ids, `blocks_activation`, details payload. Flush-guard rules:
classification **freezes at acceptance** (only application fields may change);
`applied`/`exempted` are **terminal**; only an unaccepted proposal may be
withdrawn (deleted). `exempted` is the audited "we explicitly decline to act
here" escape — it never licenses comparability.

## Activation gating

`activate_platform_revision` (and `register_platform_revision(activate=True)`)
now refuses while `activation_gate()` reports:

- an affected ACTIVE experiment (current epoch pins the superseded revision)
  with no accepted-or-beyond disposition — a `proposed` row is NOT accounting;
- any `blocks_activation` disposition (I4) not yet APPLIED — protect first,
  activate second.

Retired experiments never block (historical only); pre-epoch experiments have
nothing pinned and inherit the new snapshot at their first epoch. Forcing past
the gate requires `force=True` **and** `force_reason`, and is durable: a system
event plus one UNRESOLVED `PLATFORM_ACTIVATION_FORCED` integrity event per
skipped experiment — which keeps each of them gate-blocked until someone
actually classifies the impact. Activation-first-discover-later is no longer a
silent path.

On activation the engine mints (or resolves — content-addressed) the snapshot
for the new active set.

## Measured boundaries remain mandatory

Unknown means unknown (the PR 2 rule, unchanged): `boundary_unknown=True`
activation leaves `activated_at` NULL; `apply_new_epoch` **refuses** until
`establish_activation_boundary()` records the measured instant, and the
evaluator keeps cross-boundary evidence at `BLOCKED_PLATFORM` meanwhile. An
explicit boundary passed to `apply_new_epoch` must equal the measured one. A
revision with an unestablished boundary can never license an I0/I1 promotion
(there is no instant to re-evaluate after).

## Gate results across a platform change (integration with PR 3)

Old gate results are **never mutated** — staleness is structural:

- **I0/I1 (applied)**: the epoch may keep accumulating; `platform_block_reasons`
  suppresses the pinned-vs-active staleness for revisions licensed by an APPLIED
  `NO_ACTION`/`RECOMPUTE` record. Promotion accepts the epoch-snapshot/active-
  snapshot divergence **only** when every differing component is licensed AND
  the authorizing result was computed AFTER the licensed boundary — a pre-change
  PASS is refused with "predates the licensed platform boundary"; re-evaluate.
- **I2**: results bind to their epoch (PR 3); a new epoch makes them historical
  automatically. Cross-boundary windows in the old epoch stay BLOCKED_PLATFORM.
- **I3**: results bind to their version (PR 3); the successor never inherits.
- **I4**: PAUSED/RETIRED states gate transitions regardless of any PASS.
- **Unresolved dispositions block evidence**: a `proposed` record, or an
  `accepted` material action not yet applied, adds BLOCKED_PLATFORM reasons via
  `evidence_block_reasons()` — classification cannot be left dangling while
  evidence quietly accumulates.

## Integration with PR 4 enforcement

- Under **STRICT**, tags of experiments with unresolved material impact are
  blocked at the entry path (mirrors config-drift blocking). Under NEW_ONLY they
  keep trading (continuity) while their evidence stays blocked — integrity vs
  continuity, same doctrine as drift.
- Readiness gained `no_unresolved_platform_impact`; the ops status script
  (`experiment_os_status` §8) lists pending revisions with activation safety,
  open dispositions, and unresolved forced activations.
- A config-drift event that turns out to be a platform-level semantic change is
  promoted into this workflow with `classify_drift(event, revision=...)` — the
  drift event resolves with the revision reference and the impact records take
  over. Not every code diff is a PlatformRevision: the registry covers declared
  shared semantics (the standard components), not incidental refactors.

## The review surface

One canonical read for the future Platform Change Review session:

- `revision_review(session, revision)` — component, old → new, boundary state,
  affected actives (with their snapshots/epochs), every impact record with
  status, and the activation gate verdict (`safe`, `unaccounted`,
  `blocking_unapplied`).
- CLI: `python -m kalshi_bot.experiment_os.cli platform review FEE_MODEL:fee-v2`
  (also accepts a numeric revision id).
- `pending_revisions()`, `unresolved_impacts()` feed the status surfaces.

## Proving shapes (tests, on real repo history)

`tests/test_experiment_os_platform_impact.py`:

1. **Maker-fee correction** (`FEE_MODEL`, measured 2026-08-11T15:00Z): I1 +
   RECOMPUTE with `metric:pnl_cents_per_trade` named; refuses an unnamed or
   unregistered normalizer; same-epoch history retained (n unchanged) and
   re-evaluated rather than reset; the pre-change PASS cannot promote — the
   fresh post-boundary PASS can.
2. **Taxonomy expansion** (`MARKET_TAXONOMY`, measured 2026-08-13T18:09:40Z):
   I2 NEW_EPOCH for the mmsell-type experiment (closed at the boundary; new
   epoch pins exactly the old set with only the taxonomy swapped; pre-boundary
   trades stay out), explicit NO_ACTION for the unaffected weather-type
   experiment (which keeps evaluating, licensed).
3. **Fill-model change** (fixture — the depth-proxy queue model PR #218
   measured and REJECTED is deliberately NOT canonized): I3 discipline — cannot
   masquerade as an epoch; requires a frozen researcher-authored successor
   version; records the resulting version id.
4. **Execution/safety change** (`EXECUTION_ENGINE`, safety_critical): I4 PAUSE
   blocks activation until APPLIED; the experiment pauses audited
   (`paused_from` preserved) before the revision may go active.

Plus: forced-activation durability, unknown-boundary refusal, boundary
contradiction, STRICT tag blocking, record immutability, duplicate/retired
proposal refusal, drift classification, and the review surface.

## Scope boundary

No Evo integration, no Claude session roles, no automatic promotions, no
auto-authored scientific contracts, and the old checker skills stay on. Next
(unchanged from the enforcement doc): run the production import, resolve
readiness, record the NEW_ONLY cutover — then PR #220's session system and the
Control Tower / Platform Change Review sessions operate on these surfaces.
