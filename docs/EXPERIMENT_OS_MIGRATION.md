# Experiment OS — legacy migration (PR 2)

Spec: `docs/EXPERIMENT_OPERATING_SYSTEM_SPEC.md` §22–24. Foundation notes:
`docs/EXPERIMENT_OS_FOUNDATION.md`. This document covers the importer that maps the
pre-Experiment-OS repository into the new tables, and how to run and read it.

## The manifest is the migration

`kalshi_bot/experiment_os/legacy_manifest.py` is a hand-authored, reviewed
declaration of every legacy book: identity, class, integrity level, verdicts,
gates, and coverage. It is deliberately **not** a registry parser — a scraper
guessing at prose is exactly how invented metadata happens. Changing the
classification means editing the manifest by PR, same as the registry it condenses.

Classification shipped at manifest `2026-08-15.1` — 25 experiments:

| class | n | examples |
|---|---|---|
| ACTIVE_LIVE | 2 | `theta4-fat-tail` (twin `theta4_pt3`); `mmsell-scheduled-settle-live` (`Lmmsell8` treatment vs `Lmmsell10` control, twins `_pt3`) |
| ACTIVE_PAPER | 8 | `freeze-dark-window-pin`, `mmsell-wide-control`, `mmsell-variants-2026-07`, `mmsell-price-ceiling`, `mmsell-anchor-vol-entry`, `mmsell-anchor-strangle`, `mmsell-type-tight`, plus the PAUSED `theta-tail-sell` family (shelved, resumable) |
| RETIRED_OR_KILLED | 15 | `pin15`, `mmsell-first-cohort`, `mmsell-maker-offset-ab`, `mmsell-scan-depth`, `mmsell-anchor-stops`, `mmsell-type-wide`, `weather-consensus`(+`-city`), `tfav`, `wcprop`, `xgame`, … |

Integrity grades: **B ×9** (active books — rules reconstructed, some historical
dependency uncertain), **C ×15** (retired context, not certifiable for promotion),
**D ×1** (`weather-legacy-cells`, the registry's lump row — per-cell contracts are
not reconstructable). **No record is graded A**: nothing in this migration is
certified as an exact reconstruction, and no tool may upgrade a grade silently.

## Reconstruction rules (uniform, enforced by tests)

- **Retired books get the §22.5 minimum**: identity, thesis refs, the registry
  verdict verbatim-in-substance, kill date where recorded (day precision, 00:00Z),
  `context_only` evidence with the recorded numbers. No versions, arms, epochs, or
  gates are reconstructed for dead books.
- **Active books get §22.2/22.3**: a frozen version of the *current* semantics,
  arms with their concrete strategy tags, the registry's pre-registered gate as
  structured clauses, a **migration epoch opening at the import instant** (the
  clean evidence floor) pinned to the baseline snapshot, and grandfathered
  deployments. Live canaries get first-class twin deployments (`twin_of` links).
- **Gates keep their historically recorded evidence floors** — the 2026-08-11
  15:00Z fee boundary (mmsellA4's own registry reading), the 2026-08-13 18:09:40Z
  taxonomy cohort restart (Tmmsell), the 2026-08-14 14:31:12Z A5 pairing fix.
  Those are recorded facts, not reconstructions, and importing them immediately
  locks the gates against editing.
- **Two runtime lookups only, both measured**: a twin's exact start instant from
  `live_paper_twins` (falls back to the manifest's day-precision date if absent),
  and the import instant itself — used only as the migration boundary, never
  back-dated.
- **Dates the registry doesn't record stay NULL** (e.g. `wcprop`'s kill date, the
  weather lump's retirement). Unknown ≠ import time.
- Cross-book controls that the registry uses (mmsellA4/Tmmsell read against the
  `mmsell10` *book*) are recorded as explicit `control_exemption_reason` text —
  the control relationship crosses experiments, which is part of why those books
  grade B, and the PR 3 evaluator must respect it rather than pretend an
  in-version control exists.

## The baseline platform snapshot

`seed_baseline` registers + activates one revision per standard component and
resolves the first complete snapshot (`baseline-2026-08`). Boundary policy, per
review: **measured instants stored exactly; everything else explicitly unknown**
(`activated_at` NULL + provenance in `reason`) — never the merge/import timestamp.

| component | revision | boundary |
|---|---|---|
| FEE_MODEL | `maker_rate_2026_08_11` | **measured** 2026-08-11T15:00Z (registry's mmsellA4 reading); exactly normalizable (per-row fees) |
| MARKET_TAXONOMY | `coverage_2026_08_13` | **measured** 2026-08-13T18:09:40Z (`COHORT_START`); NOT normalizable — I2 |
| FILL_MODEL | `assumed_fill_plus_mmsell3_calibration` | unknown (composite): paper assumed-fill + live-calibrated realizable projection (mmsell3 n=359). The depth-proxy queue model was measured and **REJECTED** (PR #218 closed the paper-side queue route) — recorded history, not active truth |
| EXECUTION/SETTLEMENT/RISK/DATA_PROVENANCE/KALSHI_API_SCHEMA/METRICS/EXPERIMENT_ENGINE | current semantics described | unknown (accreted across deploys); establish later via `establish_activation_boundary` if a measurement ever exists |

## Running the import

The importer is **idempotent** (skips existing keys/revisions) and reaches
production through a flag-gated worker boot hook that can never stop trading
(errors are logged to `system_events` and swallowed):

```
{"type": "env", "set": {"EXPERIMENT_OS_IMPORT_ON_BOOT": "true"}}   # ops channel; triggers redeploy
# one boot runs the import; then optionally:
{"type": "env", "set": {"EXPERIMENT_OS_IMPORT_ON_BOOT": "false"}}
```

Leaving it on is safe (re-runs no-op; manifest additions import incrementally).
Default is `false`, so merging this PR deploys inert. Locally:
`python -c "..."` via `importer.import_legacy(session)` against any DB.

## Reading the result

- `{"type":"script","name":"experiment_os_status"}` — now ends with **§5 LEGACY
  COVERAGE**: every `paper_trades`/`live_orders` strategy tag resolved against the
  imported classification (deployment-arm tags + each experiment's declared
  `covered_tags`/`covered_tag_prefixes`). A non-empty UNMAPPED list means the
  migration is not done; nothing is auto-stubbed. Evo tags never appear — evo
  trades live in `evo_*` tables under evo lineage (spec §22.7).
- The full migration report (classes, integrity, unmapped, seeded revisions,
  snapshot fingerprint) is persisted to `system_events`
  (`component='experiment_os'`) on any run that did work.
- `python -m kalshi_bot.experiment_os.cli list|show|tag` — per-experiment trees
  and tag lineage.

## What this PR still does not do

No automatic stage promotions, no runtime enforcement, no trade-row lineage —
the books trade exactly as before. PR 3 builds the metrics + gate evaluator
(including the stricter gate-result binding and `BLOCKED_PLATFORM` over
unestablished boundaries); PR 4 records the enforcement cutover.
