# Handoff — set up a 72h scorecard review + HTML dashboard for the mmsell books

From: the LIMM (liquidity-incentive, `Alimm1`) monitoring session, 2026-10-29.
To: the session that owns the mmsell strategy (Live Ops / Research Lab role).
Goal: give mmsell the same loop LIMM has — every 72h, a read-only scorecard, a durable review
file in the repo, recommendations, and a refreshed HTML dashboard the operator can open.

## What the LIMM version is (copy the shape, not the content)

| Piece | LIMM file / object | What it does |
|---|---|---|
| Scorecard script | `scripts/limm_scorecard.py` (+ `tests/test_limm_scorecard.py`) | Read-only (stdlib + psycopg, `default_transaction_read_only=on`). Prints P&L by market type and by event, losing markets and how they filled, last line `JSON {...}`. `--json` prints one object (`by_type`, `by_event`, `losers`) for the dashboard. `--days N` sets the "recent" window. |
| Ops allowlist | `scripts/ops_runner.py` `ALLOWED_SCRIPTS` | The script must be listed to run via `{"type":"script","name":"...","args":[...]}`. A test asserts it is listed. |
| Protocol | `docs/liquidity_incentive/scorecards/README.md` | The 6 steps below. One file per run: `scorecards/YYYY-MM-DD.md`. |
| First run | `docs/liquidity_incentive/scorecards/2026-10-25.md` (baseline), `2026-10-28.md` | Fenced scorecard output, then Read + Recommendations, under 60 lines of prose. |
| Routine | a self-bound one-shot trigger, re-armed +72h on every firing | The stored prompt is the whole procedure (below). |
| Dashboard | https://claude.ai/artifact/V4SBppmpJee6qzMAgdi1vQ (Dashboard Artifact type) | Page code is fixed; data lives in uploaded JSON assets that `datasets/<id>` docs in its ArtifactData store point at. |

## Build steps for mmsell

1. **Scorecard script** `scripts/mmsell_scorecard.py`, modelled on `limm_scorecard.py`.
   Decide with the operator what the rows are: per live tag (e.g. `Vmmsell10`, the WIN-set
   canary), per series/category, per event; settled P&L from Kalshi settlements, closed pairs,
   open cost; fees separated. Paper twins: show them only where the live/paper parity is valid
   (`scripts/live_paper_parity.py`), never pool epochs Experiment OS calls non-poolable. Add a
   pure-function test and the allowlist entry. Docs + read-only script = paper scope; open a
   PR, merge on green.
2. **Protocol** `docs/mmsell/scorecards/README.md` (copy the LIMM README; swap the metrics).
3. **Baseline run**: run the script via ops, write `docs/mmsell/scorecards/YYYY-MM-DD.md`.
4. **Dashboard**: `Artifact action:"quickstart" intent:"other"`, start from the **Dashboard**
   type. Datasets: `by_type` (or `by_tag`), `by_event`, `losers`, `history` (one row per type
   per run, appended each review), `recommendations` (`{id, area, call, item, evidence,
   change_if}`). Upload each JSON with `Artifact publish url=<dash> asset:true file_path=<f>`
   — **one JSON per call** (multi-file upload refuses JSON) — then `ArtifactData update`
   `datasets/<id>` with `source {kind:"file", url:"/_blob/<id>", name}` and
   `updated {at, by}`, pinned with `if_version`. Delete the replaced assets after.
   Gotcha: the page's scripts run in separate scopes — a helper used by several charts must
   be put on `window` (LIMM's page broke with "money is not defined" until `window.money`).
5. **Routine**: `create_trigger` self-bound, `run_once_at` +72h, prompt = the procedure below.

## The routine prompt (adapt names)

> MMSELL 72h scorecard + trend review. FIRST re-arm this routine: update_trigger run_once_at =
> now + 72h, enabled:true. Then follow docs/mmsell/scorecards/README.md: (1) run the read-only
> ops script `{"type":"script","name":"mmsell_scorecard","args":["--days","3"],"id":"<unique
> id incl. date>"}`; (2) read every earlier file in docs/mmsell/scorecards/ and compare the
> JSON lines: net per type/tag lifetime and last window, loss markets per window, average
> loss and how they filled; note which rule changes fall in the window; (3) recommendations in
> three groups — EDGE/UNIVERSE, FILL PROTECTION, PROFIT — each keep / try / revert with
> evidence and what result would change the call; (4) write docs/mmsell/scorecards/YYYY-MM-DD.md
> (output fenced, prose under 60 lines), docs-only PR from the dev branch reset to the
> default branch, merge on green; (5) refresh the dashboard <URL>: run the script with
> `--json --days 7`, upload by_type/by_event/losers, append to history, replace
> recommendations, update each datasets/<id> doc (pin if_version), delete old assets;
> (6) tell the operator in plain words: net this window, the one trend that matters, top
> recommendations, the link. Build or change nothing in the bot without the operator's
> explicit go. No model identifiers in commits or PRs.

## Rules that carry over

- Read-only end to end: the ops channel never writes; the review decides nothing.
- Gates decide promotions, not P&L; a review recommendation is not a gate verdict and never
  moves a lifecycle state (Experiment OS stays canonical — the review file links, never restates).
- Small samples: prefer "consistent across three windows" over one swing.
- Overlap: since 2026-10-27 the WIN-set canary also trades KXTRUMPMENTION, which LIMM quotes
  for rewards. Worth one row in both reviews (shared books can cross or thin each other).
