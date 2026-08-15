# Experiment OS — foundation implementation notes (PR 1)

Spec (the source of truth): `docs/EXPERIMENT_OPERATING_SYSTEM_SPEC.md`.
This document records what the foundation release actually ships, the rules it
enforces today, and the **compatibility boundary** with the existing books. When this
document and the spec disagree, the spec wins and the disagreement is a bug here.

## What ships in PR 1

Package `kalshi_bot/experiment_os/`:

| module | contents |
|---|---|
| `lifecycle.py` | all lifecycle/verdict/role/impact/legacy enums + `validate_transition`, the pure legal-transition validator |
| `models.py` | the 16 foundation tables (below), on the shared declarative Base like `kalshi_bot/evo/models.py` |
| `service.py` | validated write helpers (spec §32 names), the platform-snapshot resolver, and the append-only/frozen-record **flush guard** |
| `read.py` | the shared read path: listings, trees, lineage, affected-experiment queries |
| `cli.py` | read-only inspector: `python -m kalshi_bot.experiment_os.cli list\|show\|transitions\|platform\|tag` |

Plus: migration `b4c5d6e7f8a9` (inert: creates empty tables, seeds nothing), the
ops-channel script `{"type":"script","name":"experiment_os_status"}`
(`scripts/experiment_os_status.py`, allowlisted), and three proving-shape fixtures in
`tests/test_experiment_os_shapes.py`.

## Tables (spec §31)

```
experiments                     durable research identity; cached lifecycle state
experiment_versions             immutable scientific contract; frozen_at locks it
experiment_arms                 treatment/control/secondary/benchmark per version
experiment_epochs               operating interval per version; PINS a platform snapshot (NOT NULL)
experiment_deployments          concrete running implementation at a stage; twin_of link
experiment_deployment_arms      (deployment × arm) → concrete strategy tag
experiment_gates                structured executable gate specs + immutable spec hash
experiment_gate_results         immutable evaluations (manual now, evaluator in PR 3)
experiment_state_transitions    append-only lifecycle audit
experiment_integrity_events     contamination flags (append-only + resolution fields)
experiment_legacy_evidence      pre-migration performance, classed certified/context_only
platform_components             FEE_MODEL, FILL_MODEL, MARKET_TAXONOMY, ... (open set)
platform_revisions              immutable component versions, pending/active/retired
platform_snapshots              content-addressed complete bundles of active revisions
platform_snapshot_items         (snapshot × component) → revision pins
platform_impact_actions         per-experiment impact classification (schema only; engine in PR 5)
```

The canonical lineage is NOT NULL foreign keys all the way down:
`deployment → epoch → version → experiment`, with every epoch pinning a complete
snapshot — so `read.strategy_tag_lineage("freeze3")` resolves a concrete tag to
experiment/version/arm/epoch/deployment/snapshot with no archaeology.

## State machine (enforced now)

```
IDEA → PROBE → PAPER → LIVE_CANARY → PRODUCTION      (adjacent steps only)
any active state → PAUSED → (only the state it paused from | RETIRED)
any state except RETIRED → RETIRED;  RETIRED is terminal
```

- `PASS/FAIL/HOLD/BLOCKED_*` are **gate verdicts**, never states.
- PAPER→LIVE_CANARY and LIVE_CANARY→PRODUCTION require `approved_by` **and** a PASS
  gate result belonging to the experiment; the audit row records both.
- PAPER entry requires a frozen version. Backward moves are refused with the spec's
  "no silent rollback" rule; skips name the legal next stage.
- Every transition (including creation and legacy import) appends an
  `experiment_state_transitions` row; an illegal transition writes nothing.

## Immutability (enforced now, via the ORM flush guard)

`service.py` installs a `before_flush` guard on the SQLAlchemy Session class (active
in any process that imports the service; the trading worker doesn't). It rejects:

- UPDATE/DELETE on state transitions, gate results, legacy evidence, snapshot items;
- any edit to a **frozen** version (a changed contract is a NEW version);
- any edit to a gate's spec after registration, and any edit at all (or delete) once
  `evidence_started_at` is set — a mis-specified gate is recorded and superseded,
  never rescued by moving the threshold;
- any edit to a platform revision's **semantic declaration** (fingerprint,
  description, reason, backward compatibility, normalization judgment, safety class,
  PR reference) once the revision has been activated — only the lifecycle fields
  (`status`, `activated_at`, `retired_at`) stay mutable, and `activated_at` only so
  an initially unknown boundary can be *established* from measurement
  (`establish_activation_boundary`), never moved once set;
- deleting snapshots, un-freezing, un-starting evidence.

Activation boundaries are never fabricated: `activate_platform_revision(...,
boundary_unknown=True)` records an active revision with `activated_at` NULL —
explicitly unknown — rather than stamping an import or merge timestamp. Gates whose
evidence spans an unestablished boundary must evaluate `BLOCKED_PLATFORM` (spec
§17.4; enforced by the PR 3 evaluator).

Core `update()`/`delete()` statements bypass ORM events; nothing issues them against
these tables. DB-level protection can come with the enforcement PR if ever needed.

## Version vs epoch (spec §5.3 vs §5.5)

- **New version** = the scientific question changed (hypothesis, arms/control,
  independent variable, entry/exit semantics, gate meaning). Requires
  `change_reason`; predecessor recorded; epoch numbering restarts per version.
- **New epoch** = same question, changed world (taxonomy/fee/fill/provenance...).
  Same version, fresh `platform_snapshot_id`, `impact_class` recorded (e.g. I2).
  One open epoch per version at a time — close the interval before opening the next.

Both directions are pinned by tests (`test_environment_change_is_a_new_epoch_same_version`,
`test_scientific_change_is_a_new_version`).

## Platform snapshots

Snapshots are **content-addressed**: `resolve_active_platform_snapshot()` hashes the
current (component → active revision) set and reuses the existing row on a match, so
an unchanged platform never mints duplicates and a changed one can never be confused
with the old. Completeness = one active revision per **registered** component;
anything less refuses experiment/epoch creation by name. New experiments and epochs
therefore inherit "the current fee model" as a system property, not a convention.

`register_platform_revision(..., activate=True)` / `activate_platform_revision()`
retire the prior active revision at a caller-supplied (preferably measured)
`activated_at`. The affected-experiment query exists now
(`read.experiments_using_revision`); the impact classification **engine** is PR 5.

## Compatibility boundary (read this before wiring anything)

The foundation runs at **enforcement mode OFF** (`lifecycle.CURRENT_ENFORCEMENT_MODE`):

- **No runtime path touches these tables.** The trading worker, paper engine, twin
  harness, evo fleet, scanners and all existing scripts neither read nor write
  experiment OS state. Deploying this PR changes no trading behavior.
- **Existing conventions remain authoritative for now**: `docs/BOOK_REGISTRY.md` rows,
  thesis docs, `paper_trades.strategy` tags, `live_paper_twins` epochs, and the
  fee/taxonomy boundary notes continue to govern the running books until the importer
  (PR 2) maps them and later PRs move reads over.
- **Where the systems will meet**: `experiment_deployment_arms.strategy_tag` is the
  join key to `paper_trades.strategy` / `live_orders.strategy`. Trade rows get no new
  columns in this PR; lineage lands with PR 4 (mapping tables first, per spec §14).
- **The spec's conventions win inside the new tables** even where they differ from
  repo habit: e.g. the twin relationship is a first-class `twin_of_deployment_id`
  (not a `_pt` tag suffix), "cohort boundary" becomes an epoch (the word cohort stays
  with `evo_cohorts`), and gate rules are structured JSON, not registry prose. The
  old forms stay valid where they live today; the importer translates, it does not
  retrofit.
- **Legacy import invents nothing**: `import_legacy_experiment` records exactly the
  metadata history supports — no synthetic versions/arms/epochs/snapshots, integrity
  level A–D always caller-stated and visible, unknown dates stay NULL, historical
  evidence attaches as `context_only` unless a future migration explicitly certifies
  it. `pin15` in the shape tests is the template.

## Proving shapes (spec §34 acceptance)

`tests/test_experiment_os_shapes.py` represents three materially different real
experiments end to end, with data from `docs/BOOK_REGISTRY.md`:

1. **FREEZE** — multi-arm paper experiment: 4 arms (`freeze3` the open-window
   control), the freeze1−freeze3 ≥ 3¢ gate as structured clauses, probe→paper walk
   with the probe PASS recorded, epoch + paper deployment with all four tags.
2. **mmsell maker-offset A/B** — live canary + paper twin: paper epoch 1 →
   operator-approved promotion on the fill-model PASS → I2 live epoch 2 with fresh
   `mmsell10a/b` deployments and their `_pt` twin (same instant, `twin_of` link),
   through the real 2026-08-14 kill verdict and audited retirement.
3. **pin15** — retired historical experiment imported as legacy: integrity C,
   real 2026-07-16 retirement date, kill verdict preserved verbatim, `context_only`
   evidence, and — deliberately — no tag mapping and no invented history.

## What remains for PR 2 (legacy importer + cutover baseline)

- Migration classification for every live tag in `BOOK_REGISTRY` /
  `paper_trades.strategy` (legacy classes §22.1, integrity levels A–D §23).
- The **baseline Platform Snapshot** describing current deployed semantics, with
  **measured activation boundaries where they exist and explicitly-unknown ones
  where they don't** (never the merge/import timestamp): the 2026-08-11 maker-fee
  model; the 2026-08-13 18:09:40Z taxonomy (measured); and FILL_MODEL described as
  the semantics actually deployed — the paper engine's assumed-fill plus the
  live-calibrated realizable projection (`docs/MMSELL_FILL_MODEL.md`). The
  depth-proxy queue model was **measured and rejected** (PR #218 closed the
  paper-side queue route) — it is recorded as history, not registered as active
  platform truth.
- Import mapping: registry rows + thesis docs + `live_paper_twins` → experiments,
  versions where reconstructable, arms/controls, grandfathered deployment records,
  migration epochs at the import boundary, `legacy_evidence` attachments.
- A migration report listing unmapped/uncertain books (nothing silently upgraded).
- No automatic stage promotions — same as this PR.

Later PRs per the spec: metrics + gate evaluator (PR 3), NEW_ONLY enforcement +
trade lineage (PR 4, where the enforcement cutover is recorded), platform impact
engine (PR 5), loop/docs/evo integration (PR 6–7), STRICT (PR 8+).

Explicitly on the PR 3 list (review follow-up from the foundation PR): **stricter
gate-result binding on promotions**. Today a real-money transition verifies the
supplied result is PASS and belongs to the experiment; PR 3's evaluator must also
verify the PASS came from the promotion gate registered for that exact transition
(`from_state`/`to_state`), on the active version, over the correct epoch and
platform snapshot — so a PASS from an unrelated gate (or a stale epoch) can never
justify a promotion.
