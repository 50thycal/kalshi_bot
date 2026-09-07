# Handoff — score every mmsell series, and gate trading on the score

**Written 2026-09-06.** Session role: **Research Lab**. Scope: **mmsell family only** — do not
touch perps, pin15, tfav, theta, wcprop, weather or xgame.

## What the operator asked for

Replace the current binary `graduated` / not-graduated bar with a **numeric score per series**,
where a higher score means a better expected outcome for us, and a series must clear a
**threshold** to be tradable. Two things feed the score:

1. **Do we understand the contract** — is there research/rules evidence behind it.
2. **Does it actually make us money** — a series the mmsell books win on should score higher.

Then: work through the **138 grandfathered series** first, and produce a plan for grading
everything else we will eventually collect data on.

## Read this before you design the score

The operator has asked, deliberately and more than once, for **profitability in the score**.
Take that as decided. But the repo contains a specific, measured warning that you must design
*around* rather than ignore:

- `kalshi_bot/registry/__init__.py`: *"Graduation says 'we know what this contract is and we
  have history on it', never 'this contract makes money'. Conflating them is how a governance
  rule turns into an unvalidated strategy."*
- `scripts/mmsell_series_pnl.py` and `docs/MMSELL_ROADMAP.md` §1: measured per-trade
  **sd = $0.2343 against a mean of $0.0065** — noise is 36× signal. **A single series needs
  n ≈ 800 distinct markets before its confidence interval excludes break-even.** A gate scoring
  series on raw P&L "would fire constantly on noise and would have killed profitable cells long
  before it caught" `KXNFLSPREAD`.

Both statements are still true. They do **not** mean "refuse to score on P&L". They mean the
P&L component must be **shrunk toward zero by sample size**, so that a series with 30 trades and
a great run cannot outrank one with 3,000 trades and a real edge. Build that in from the start;
it is the difference between a scorecard and a noise amplifier.

**Do not silently drop this requirement, and do not implement it naively.** If you conclude the
shrinkage makes the P&L component near-useless at current sample sizes, say so with the numbers
rather than quietly weighting it to nothing.

## Proposed score — a starting point, not a decision

Total 0–100. Confirm the weights and the threshold with the operator before anything gates.

| component | pts | source | notes |
|---|---:|---|---|
| **Mechanism understood** | 30 | `rules_reviewed_at` + `settle_mode` confirmed | See "the hard floor" below |
| **Sample sufficiency** | 20 | distinct **contests**, not markets | `contests` is the independence unit |
| **Realized edge, shrunk** | 40 | `edge = be% − loss%`, shrunk by n | The operator's ask; see shrinkage |
| **Loss concentration** | 10 | `worst3%` | Broad drift vs two bad afternoons |

**The hard floor:** a series with **no recorded rules review cannot clear the threshold**,
however good its P&L. That preserves the existing two-part bar inside the new score — otherwise
a series nobody has read its rulebook for can trade live purely on a lucky month, which is the
exact failure `KXNFLSPREAD` demonstrated (classified, 1,486 settled markets, −$151.26).

**Shrinkage.** Score the edge as `edge_shrunk = edge × n / (n + k)` where `n` is distinct
contests and `k` is a prior strength to be chosen from the measured variance — with sd/mean at
36×, `k` will be large (order hundreds). Pick `k` from the data, write down why, and show what
each of the 138 series scores at that `k` before proposing a threshold.

**`edge`, not raw P&L.** `mmsell_series_pnl.py` already computes it: each series is entered at a
different premium, so a raw loss rate is scored against a different break-even — 12% is a
disaster at 6¢ and comfortable at 17¢. `edge = be% − loss%` is the one column comparable across
series. Reuse that function; do not re-derive it.

**`contests`, not `mkts`.** One NFL game carries a nested spread ladder that a blowout settles
against a seller at one instant. `KXNFLSPREAD`'s 382 markets were 44 games, and 2 of those games
carried 48% of the loss. Sample sufficiency and shrinkage must both count contests.

## What already exists — reuse, do not rebuild

| thing | where | gives you |
|---|---|---|
| per-series edge, break-even, contests, worst3%, live flag | `scripts/mmsell_series_pnl.py` | the whole P&L half of the score |
| the decision ledger (state, reviewer, review date) | `kalshi_bot/registry/series_manifest.json` | where a score and threshold verdict should be recorded |
| the entry gate | `registry.admits()` via `mmsell_live_min_tier`, per-book `universe=` | where a threshold would bind |
| rules-vs-taxonomy verification | `scripts/series_rules_audit.py` | the mechanism-understood half |
| the review queue | `scripts/series_registry_review.py` | backlog + arrivals, ranked by real exposure |
| settlement evidence gathering | `scripts/mmsell_taxonomy_audit.py` | rule documents, hand-tuned patterns |

## Known problems you will hit — do not rediscover these

1. **The rules audit rests on ONE signal.** Kalshi's `settlement_source` fired **zero** times
   across all 138 series, so every verdict is a single regex over rules text. That is why **no
   `rules_reviewed_at` has been recorded yet** and the backlog is still 138 of 138. Scoring
   "mechanism understood" at 30 points off that single signal would launder a regex into a
   human's signature. **Either find a second signal, or make those 30 points require a human
   sign-off recorded in the manifest.** This is the first real decision of the task.
2. **Dedup does not collapse.** `docs = 8` for every series audited: rules text embeds
   per-market specifics, so "distinct documents" is really "8 near-identical markets".
3. **The audit worklist is graduated-only.** Five of the six series in the KXUE prefix collision
   were never audited because they classify as `econ_release` — wrong, but not *unclassified*.
   Widen the worklist when you score.
4. **The scan sees 4,005 series; the manifest holds 138.** 265 traded series have no manifest
   row at all. For mmsell scope, filter that list to series mmsell books have actually traded
   before treating it as the grading queue.
5. **`markets` has not been written since 2026-06-08.** Do not build on that table.

## The work, in order

**Phase 1 — score the 138 grandfathered series.**
Build `scripts/mmsell_series_scorecard.py` (ops-allowlisted, stdlib + psycopg, read-only).
Emit every series with its component breakdown and total, sorted by score. Run it via the ops
channel. **Do not gate anything yet.** Bring the operator the distribution and a proposed
threshold justified by where the scores actually fall — not a round number picked in advance.

**Phase 2 — settle the mechanism-understood half.**
Resolve problem (1) above. Whatever you decide, a score that can gate live money must not treat
an unreviewed series as reviewed.

**Phase 3 — wire the threshold to the gate.**
Add the score and its verdict to the manifest row schema, and make `registry.admits()` able to
require a minimum score. **This is a Platform Change** — it changes which series live mmsell
books admit. Platform Change Review, and expect an epoch decision for any book whose universe
moves. `LIVE_STRATEGIES` was empty as of 2026-09-06; check it again before assuming no live
exposure.

**Phase 4 — the grading plan for everything else.**
Series with too little history to score are not failures, they are **uncollected**. Produce a
plan that says: which series mmsell is already trading with no score, how many contests each
needs before its shrunk edge means anything at `k`, and roughly how long that takes at current
volume. Paper trades everything regardless — that is how history accumulates — so this is a
waiting list, not a backlog of work.

## Rules that bind this task

- **Gates decide promotions, not P&L** (`CLAUDE.md`). A score is a *report* until Phase 3, and
  even then the threshold is a governance bar, not a strategy.
- **Never weaken a live safeguard.** The score may narrow what trades live; it must not widen it
  without explicit operator confirmation.
- Anything that changes `SERIES_TYPES`, metric definitions, fees, fills or execution is shared
  platform semantics → **Platform Change Review**.
- Problems belong in an **Experiment OS issue** (`docs/EXPERIMENT_OS_ISSUES.md`), not in prose.
- The manifest moves **only by PR**. No script may write a review or a passing score.

## Context docs

`docs/SERIES_REGISTRY.md` (the two ledgers, states, the two-part bar) ·
`docs/SERIES_RULES_AUDIT.md` (the first full run and its limitations) ·
`docs/MMSELL_UNIVERSE_REVIEW.md` (the measurement that motivated the bar) ·
`docs/MMSELL_ROADMAP.md` §1 (the variance numbers) ·
`docs/OPS_RUNBOOK.md` (ops channel, standing analyses) ·
`docs/IDEA_MODEL_SCORECARD.md` — **read this one for calibration.** It is this repo's existing
scoring ledger, for ideas rather than series, and it records a base rate worth knowing before you
pick a threshold: **18 idea-model promotions → 0 currently-live paper books.** A scoring system
here that passes most of what it scores is probably mis-calibrated.
