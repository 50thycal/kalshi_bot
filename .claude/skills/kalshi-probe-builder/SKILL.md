---
name: kalshi-probe-builder
description: |
  Turn a promoted Kalshi trading-idea into a runnable, pre-registered validation probe — the bridge phase BETWEEN idea generation (`kalshi-idea-model`) and strategy building (`kalshi-strategy`). Use this whenever an idea has cleared the idea-model screen and needs to become real repo artifacts: a pre-registered thesis doc, a self-contained ops-runnable probe script, an ops-runner allowlist entry, scorecard rows, and a PR — then, after merge, actually running the probe and logging its verdict. Trigger on "build the probe", "write the probe script", "turn this idea/thesis into a probe", "promote this to a probe", "materialize the thesis", "hand this off to validation", "wire up the probe for <IDEA>", "run the probe and log the verdict", or when a `kalshi-idea-model` run has just produced promotions and the user says to go build them. It does NOT generate ideas (that's `kalshi-idea-model`), does NOT build paper books or touch live money (that's `kalshi-strategy` Phase 2+), and never re-scopes predictions after results. It produces a falsifiable, cost-aware, testable probe and drives it to a logged verdict; a passing probe hands off to `kalshi-strategy`.
---

# Kalshi Probe Builder — idea → runnable probe → logged verdict

The connective tissue of the trading pipeline. `kalshi-idea-model` diverges and screens, ending
at a **probe spec**. `kalshi-strategy` takes a *validated* edge and builds it into a paper→live
book. This skill is the deterministic step in between: it **materializes a promoted thesis into
the repo's validation machinery and runs it**, so the pipeline is idea → *probe* → verdict →
(only if it survives) strategy build.

**Why it exists.** Doing this by hand is error-prone and easy to do inconsistently: the thesis
must be pre-registered *before* the probe runs, the probe must be self-contained and read-only to
run through the `ops` channel, the allowlist and scorecard must be updated, and — the part people
forget — a new probe script only becomes runnable after it's **merged to the default branch and
`ops` is refreshed from it**. This skill encodes that whole path.

**North star (from `CLAUDE.md`):** $100/month realized. A probe that cleanly *rules an idea out*
is a win — it tells us what to stop considering. Never soften a kill criterion to save an idea.

```
Phase 0  Ground: read the promoted thesis + the repo's probe/thesis patterns
Phase 1  Write the pre-registered thesis doc(s)        → gate: falsifiable predictions + kill criteria
Phase 2  Write the self-contained probe script(s)      → gate: compiles, imports, ruff-clean, prints a verdict
Phase 3  Wire it in: allowlist + scorecard + journal   → gate: ops can find it; the ledger records it
Phase 4  Ship + run: commit → push → PR → run → log     → gate: PR open; verdict logged or handed off with exact commands
```

---

## Phase 0 — Ground in the promotion and the patterns

You need two things before writing anything.

1. **The promoted idea(s)** — from the `kalshi-idea-model` output in the conversation, or from the
   user. For each, extract: the one-liner, the mechanism (what's mispriced / who's on the other
   side / why it persists), the **pre-registered predictions with pass/fail thresholds and kill
   criteria**, the data source, and the correlation-to-existing-books note. If any of these is
   missing, get it *now* — you cannot pre-register after the probe runs.

2. **The repo's patterns** (read these; match them, don't reinvent):
   - `docs/THETA_THESIS.md` — the thesis format every promoted idea matches (one-liner →
     mechanism → pre-registered predictions → probe plan → cost/capacity → correlation).
   - A worked probe script closest to your data shape: `scripts/kalshi_art_survey.py`
     (public-API census), `scripts/kalshi_flb.py` (settled markets → candlesticks → bin),
     `scripts/kalshi_theta_study.py` (candles + tape + a model), `scripts/econ_react_study.py` /
     `scripts/fed_rv_study.py` / `scripts/kalshi_stream_survey.py` (this skill's own first outputs).
   - `scripts/ops_runner.py` — the `ALLOWED_SCRIPTS` allowlist and the `mod.main(args)` contract.
   - `docs/IDEA_MODEL_SCORECARD.md` — the ledger you append a pending-probe row to.
   - `CLAUDE.md` — the ops channel, the fee formula, provenance rules.
   - `.claude/skills/kalshi-probe-builder/references/probe-and-ops.md` — the annotated probe
     skeleton, the Kalshi API/candlestick field cheatsheet, and the merge→refresh→run→log recipe.

**Gate:** For each idea you can state its pre-registered predictions and name the exact dataset +
measurement the probe will compute. If you can't, stop and pin it down.

---

## Phase 1 — Write the pre-registered thesis doc(s)

One `docs/<NAME>_THESIS.md` per promoted idea, in the `THETA_THESIS.md` format. If the idea is
**census-first** (a new market family whose settled history / instrument existence is itself in
doubt), write `docs/<NAME>_CENSUS.md` instead — a testability pre-stage with C1/C2/… gates and a
named trigger, matching `docs/STREAMPIN_CENSUS.md` / `scripts/kalshi_art_survey.py`. Do not write a
full thesis for something that may not be testable yet.

The load-bearing section is **Pre-registered predictions** — each a claim with a concrete PASS
threshold (in ¢/contract net of both-leg fees, or a measured rate) and a **KILL criterion**,
written now so results can't be re-scoped later. Include a **Decision rule** stating which
predictions must pass to build a book. Screen honestly against the graveyard: if the idea sits in
a family the record has killed (naive price-history calibration, passive-on-informative without an
adverse-selection model, locked arb, off-weather *inattention* pins), say so and state the prior.

Also fill: mechanism (who's on the other side + why it persists), probe plan (script + dataset +
provenance + no-lookahead construction + the promotion result), cost/capacity (both-leg fee at the
relevant price band, spread crossed, adverse-selection haircut if passive, settle frequency), and
correlation (shared return driver vs the live book; value to $100/mo).

**Gate:** Predictions are falsifiable with numeric thresholds and kill criteria, written before any
validation. A thesis without a kill criterion does not pass.

---

## Phase 2 — Write the self-contained probe script(s)

The probe must be runnable through the `ops` channel, which means: **read-only, self-contained,
stdlib-only** (plus `psycopg` *only if* it reads the bot DB, and `import xvenue_leadlag as xl` for
the browser-UA `_get`/`_num` when it hits Kalshi's public API — Cloudflare 1010s a default UA, so
never drop that). Signature is `def main(argv: list[str] | None = None) -> int` with an
`argparse` parser and a `if __name__ == "__main__": raise SystemExit(main())` guard. See
`references/probe-and-ops.md` for the annotated skeleton and the exact Kalshi API / candlestick
field names (`yes_ask.close_dollars`, `end_period_ts`, `volume`, …).

Design rules that keep probes correct and cheap:
- **Prefer Kalshi-public-API-only for v1.** Defer external feeds (futures, weather vendors,
  stream-count APIs) to a v2 that runs only if v1 shows life — this dodges data-plumbing risk and
  keeps the first probe self-contained. If the edge fundamentally needs an external signal, make v1
  a **census** that tests whether pursuing it is even worth it (the `kalshi_art_survey` pattern).
- **No lookahead.** Use only prices knowable at the decision point; an outcome label (`result`)
  may *select* a path but never price a point before it occurred. State this in the docstring.
- **Provenance separation.** Never silently mix live-collected tables with REST-backfill or
  cross-venue data; label the source.
- **Fail soft + diagnostic.** Tolerate missing fields (like the existing probes), and print a
  matched-`(series [category])` diagnostic so a mis-filter is visible, not silently folded in.
- **End in a verdict.** Print PROMOTE / HOLD / KILL-leaning against the thesis' pre-registered
  bar, with the numbers that decided it. The probe's job is to make the verdict unambiguous.

Then verify locally (you can't reach Kalshi from here, but you can catch the 90% of bugs that are
static): `python -m py_compile`, import from `scripts/` (exercises the `xvenue_leadlag` import),
and `ruff check`. Expect to iterate once against real output after the first ops run — that's
normal; the existing probes were all refined post-first-run.

**Gate:** Each script compiles, imports cleanly from `scripts/`, is ruff-clean, and prints a
verdict. It reads only; it places no orders and writes no tables.

---

## Phase 3 — Wire it in

Three edits so the probe is runnable and recorded:
1. **`scripts/ops_runner.py`** — add each script's module name to `ALLOWED_SCRIPTS` (the
   `importlib` dispatch keys on it; an un-allowlisted name is rejected).
2. **`docs/IDEA_MODEL_SCORECARD.md`** — add a `pending probe` (or `pending census`) row to the
   ledger for each idea, and keep the base-rate table + per-family tallies in sync.
3. **`docs/RESEARCH_JOURNAL.md`** — a short dated entry at the top (newest-first) pointing to the
   new theses + probes and the one-line rationale, so the record is coherent before verdicts land.

Do **not** touch `docs/BOOK_REGISTRY.md` — that is for books that write `paper_trades`, which
these are not yet. A registry row is a Phase-2 `kalshi-strategy` action, only after a probe passes.

**Gate:** `ops_runner` will find the script(s); the scorecard and journal record the promotion.

---

## Phase 4 — Ship, then run and log the verdict

**Ship.** Develop on the session's designated feature branch (never the default). Commit the
theses + probes + wiring with a clear message, push `-u`, and open a PR **ready for review** (not
draft). Keep the diff clean — verify the PR base is the *live* default branch (its remote tip may
be ahead of a stale local ref; `git ls-remote origin refs/heads/<default>` is the source of
truth). Do not include unrelated commits.

**The ops wrinkle (do not skip).** Per `CLAUDE.md`, the `ops` branch runs the copy of a script
that exists **on the default branch**. A brand-new allowlisted script on your feature branch is
**not runnable yet** — the `ops` runner won't have it. So the run sequence is:
1. **Merge** the PR to the default branch (needs the human's review/merge — say so explicitly and
   stop here if you don't have merge rights).
2. **Refresh `ops`** from the updated default (`git checkout -B ops origin/<default> && git push
   -f origin ops`, or the recreate recipe in `CLAUDE.md`) so it picks up the new script +
   allowlist.
3. **Run the probe**: push `{"type":"script","name":"<name>","args":[...],"id":"<slug>"}` to
   `ops/request.json`; read your result from `ops/results/<slug>.txt` (see `references/probe-and-ops.md`
   for the worktree recipe). Reset to `{"type":"noop"}` when done.
4. **Log the verdict**: update the thesis' Status line, set the scorecard row's verdict + outcome,
   and add a `RESEARCH_JOURNAL.md` entry with the numbers. A failed pre-registered kill criterion
   is a **clean ruling-out — log it as a win** and close the family.

**Hand off.** A probe that PASSES its decision rule is where this skill ends and `kalshi-strategy`
begins (its Phase 2 data pipeline / Phase 3 build), with the thesis and predictions already
written. State that handoff explicitly.

**Gate:** PR is open and clean. Either the run loop has been executed and the verdict logged, or —
if merge/refresh needs the human — the user has the exact commands and knows the next action is
theirs.

---

## Guardrails (apply throughout)

- **Pre-register before you probe.** Predictions + kill criteria are written in the thesis before
  the probe runs. Never re-scope a window, EV bar, or kill line after seeing results.
- **Probes are read-only + self-contained.** Runnable through `ops`: stdlib (+psycopg for DB only),
  browser-UA via `xvenue_leadlag`, no order path, no table writes. A unit of the probe is a
  measurement, never a trade.
- **Prefer Kalshi-only v1; census when a new venue's testability is in doubt.** Defer external
  feeds to a v2 gated on v1 showing life.
- **No lookahead, ever; keep provenance separate.** Point-in-time correctness and un-mixed sources
  are the backbone of a valid probe.
- **Ruling out is a win.** Don't torture a probe toward a promote. Honest kills are the cheapest
  progress toward $100/mo.
- **This skill stops at a logged verdict / handoff.** It builds and runs the probe; it does not
  build paper books, size positions, or touch live money — that is `kalshi-strategy`.

---

## Reference files
- `references/probe-and-ops.md` — the annotated self-contained probe skeleton, the Kalshi public
  API + candlestick field cheatsheet (exact JSON paths), the fee formula, and the
  merge→refresh-`ops`→run→read→log recipe with the worktree commands. **Read for Phases 2 and 4.**
