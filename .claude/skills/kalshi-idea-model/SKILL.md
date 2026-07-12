---
name: kalshi-idea-model
description: Generate and screen NEW Kalshi trading-strategy ideas, then decide which are worth validating. Use whenever the user wants new trading ideas, edges, angles, or mechanics to test on Kalshi; asks "what should we try next", "find me new strategies", "brainstorm edges", "any new ideas for the bot"; wants to review the Kalshi market landscape for opportunities; or wants to judge whether an idea is novel/uncorrelated and plausible enough to promote to backtesting/paper-trading. This is the divergent front-end feeding the build-and-validate pipeline: it grounds in what's already been tried, surveys the board, generates a broad anti-anchored candidate slate across market types and mechanics, screens each on novelty-vs-existing-books, edge plausibility, cost, and testability, and promotes survivors as pre-registered falsifiable theses with a probe plan. Runs in chat. Trigger for idea generation, edge brainstorming, market surveys, or "is this worth testing" screening on Kalshi / prediction markets.
---

# Kalshi Idea Model — generate, screen, promote

The divergent front-half of the trading pipeline. Its job is breadth then judgment:
enumerate many candidate edges, then cut hard to the few worth a probe. It is deliberately
**not a validator** — validation is the repo's job (ops-runner probes → paper book → live
gate) and the `kalshi-strategy` skill's Phases 2–6. This skill produces the artifact that
pipeline consumes: a **pre-registered, falsifiable thesis + a probe plan**.

**Runs in chat.** Every step here is doable with chat tools — reading the repo over GitHub,
web research, reasoning. The only things that need the repo's ops channel are pulling live-DB
numbers and running probes; this skill stops at *specifying* the probe, so it never needs
Railway/Postgres access itself.

**North star** (from the repo's `CLAUDE.md`): **$100/month in realized dollars across the
whole portfolio.** Rank everything by expected contribution to that number, and value
**uncorrelated ballast** — a mediocre edge that doesn't move with the existing book is often
worth more than a better edge that does. **Ruling an idea out is a win.**

**Idea generation ≠ idea validation.** The failure mode this skill exists to prevent is
rigorously validating a narrow, anchored idea set — garbage-in-validated-garbage-out with
excellent process hygiene. **Diverge first (Phase 2), converge second (Phase 3).** Do not let
the screen contaminate the generation: get breadth on the table before judging any of it.

| Phase | | Gate |
|---|---|---|
| **Phase 0** | Ground in what's tried + read the scorecard | know the graveyard, the live books, the promote→verdict base rate |
| **Phase 0.5** | **Choose the dive** — scoped (default) or broad sweep | user has picked a focus from a data-grounded 5-option menu, or explicitly chosen the full-board sweep |
| **Phase 1** | Survey the live board (within the chosen scope) | know where liquidity + activity actually are |
| **Phase 2** | Diverge — generate a slate | scoped: ≥ ~8 deep candidates in the focus; broad: ≥ ~12 across mechanics × categories |
| **Phase 3** | Screen — score against the rubric (incl. testability-NOW + venue-age gates) | each candidate scored, not vibes-ranked |
| **Phase 4** | Promote — recon census → pre-registered thesis + probe; reconcile the holds queue | survivors handed off; holds ledger updated |

**Two modes.** The **scoped dive** (Phase 0.5, the default) narrows the whole run to one
venue/mechanic the user picks from a data-grounded menu, then goes *deep*; the **broad sweep**
walks the full generation grid. The one paper book this pipeline has ever produced (PIN15) came
from a scoped run; broad sweeps have increasingly returned kills and untestables (one run
promoted 0/28 and called the board "mined out"). Prefer scoped unless the user asks to sweep.

## Phase 0 — Ground in what's already been tried (do this FIRST, always)

The single most valuable thing this skill does is **not regenerate settled questions.** The
user has a documented research history; read it before generating anything. Pull these from
the repo (GitHub, no ops channel needed):

- `docs/RESEARCH_JOURNAL.md` and `docs/edge_research.md` — every hypothesis tested, the EV
  verdict, and why each dead idea was abandoned. Treat these as **the graveyard**: an idea
  already ruled out here is not a candidate unless you can name a specific, material difference
  from what was tested.
- `docs/THETA_THESIS.md` (and any sibling thesis docs) — the format promoted ideas must match,
  and the current forward-tests in flight.
- **The live book roster** — from `full-update`'s list and `paper/strategies.py` /
  `kalshi_bot/*/`: which books exist, which are +EV, which are bleeding, which are too new to
  read. This is the **correlation baseline** for Phase 3.
- `CLAUDE.md` — the goal and the operating conventions (ops channel, fee formula, provenance
  rules).
- **`docs/IDEA_MODEL_SCORECARD.md` — the promote→verdict ledger (read AND update it).** This is
  the *quantitative* companion to the meta-lessons: every past promotion, its verdict, and the
  running base rate (how many promotions became paper books vs died at probe vs came back
  untestable), plus a per-**family** hit-rate (which families keep failing). Read it to calibrate
  how skeptical to be *this* run, and to see which families are 0-for-everything (don't re-promote
  them without a genuinely new mechanism). You will append this run's promotions to it in Phase 4,
  and record their verdicts when they land.

Then extract the **meta-lessons** — the durable patterns across the graveyard, which become
priors for generation. From the current record they are:

1. **Efficient-on-price-history.** Kalshi's mature/liquid ladders (temperature especially) are
   efficient on everything derivable from price history alone — calibration, persistence,
   autocorrelation, overround. Naive price-only edges on mature markets start with a low prior.
2. **The staleness/information-lag family is where the real edges live.** Both surviving edges
   (obs = station observations pin the outcome before quotes update; theta = model-overpriced
   tails after spot moves) are the same shape: **you compute fresh fair value faster than the
   quote updates.** This is a high prior — dig here.
3. **Passive/maker on informative markets dies to adverse selection.** Weather maker looked
   +1¢ gross; realized fills were −8.6¢ because you get filled exactly when you're wrong. Any
   "provide liquidity / rest an offer" idea must confront adverse selection explicitly (theta
   survives it only because the tape measured realized passive fills and the model gates entries).
4. **No locked arbitrage.** An 882-event Dutch-book scan found only artifacts. Don't propose
   "find the arb" — propose relative-value/lead-lag with a measured cost gate instead.
5. **Cost dominates on liquid markets.** Cross-venue divergence on liquid non-shocking markets
   is ~1–1.6¢, below the ~2–4¢ round-trip. Edges must clear cost by a margin, not on average.

Re-derive these from the live docs each time — the record evolves. If a lesson has been
overturned since, use the current version.

**Gate:** You can state the current live books (the correlation baseline), the ruled-out set
(the graveyard), the meta-lessons (the priors), and the promote→verdict base rate (the
scorecard). Generating before you've read the journal is the cardinal sin here.

## Phase 0.5 — Choose the dive: scoped (default) or broad sweep

**The record says a narrow, deep dive beats a broad board sweep.** The only paper book this
pipeline has produced (PIN15) came from a run the user scoped to a single venue (15-minute
crypto); the broad sweeps have trended toward kills and untestables. So unless the user asks for
a full-board sweep — or their invocation already names a scope (e.g. "new commodity ideas",
"look at in-play sports"), in which case honor it and skip this menu — **offer a scoped dive.**

**Present ~5 high-level scoped-dive options for the user to choose from, grounded in real data,
not vibes.** Build them from:

- **What we've tested + how it went** — the graveyard, the live books, and especially the
  **scorecard** (which families/venues have edge signal vs which are mined out) and the **holds
  queue** in the latest idea-model doc (which parked ideas have had their **trigger fire** and are
  now actionable — those are prime scoped dives).
- **Live DB-derived state** — the numbers the bot is actually producing. You don't need your own
  DB access: read the **latest `docs/STRATEGY_LOOP_STATUS.md`** (per-book paper P&L + data
  freshness) and the newest entry on the **`digest-archive`** branch. If those are stale or you
  want fresh numbers, pull a lightweight snapshot via the **ops channel** (e.g. a `weather_pnl`
  or a small `db` query, or `kalshi_market_survey`) — this is grounding context, still short of
  running a probe.
- **Board drift** — any newly-launched venue or market family (a light web check), since a fresh
  venue is a natural scoped dive (but carry the venue-age caution from Phase 3 into the framing).

Each option is one line: **the focus (venue × mechanic) + a one-clause data-grounded reason it's
worth a deep dive now** (e.g. "PIN15 variants — the one book that passed; mine its ladder for
PIN60/ALT twins", "Commodity TWAP pin — COMPIN's trigger fires ~Jul 14 when the first contracts
settle", "MMX non-sports maker-sell — mmsell3 near its gate + FLB calibration confirmed off-sports").

**Then ask the user to pick**, using `AskUserQuestion`. The tool allows up to 4 selectable
options, so: list all ~5 in a short preamble with their rationales, offer the **4 strongest as
selectable choices**, and **always include "Broad full-board sweep" as one selectable option** so
the divergent mode stays one click away (the 5th scoped option and any custom focus are reachable
via "Other"). Do **not** proceed to generation until the user has chosen — the scope determines
everything downstream.

**Once scoped:** Phases 1–4 run *within that focus and go deep* — Phase 1 surveys only the chosen
venue/mechanic in detail; Phase 2 generates ≥ ~8 candidates *inside* the scope (variants,
sub-cells, adjacent mechanics on the same underlying) rather than ~12 across the whole board;
Phases 3–4 are unchanged. If the user picks the broad sweep, run Phases 1–4 as the full-grid
divergent search described below.

**Gate:** The user has explicitly chosen a scope (one of the menu options, a custom focus, or the
broad sweep). The rest of the run honors it.

## Phase 1 — Survey the live board

Ground the generation in what's actually tradeable *right now*, not an abstraction of Kalshi.
Where liquidity and activity sit determines which mechanics are even viable (you can't make
markets where there's no flow; you can't trade convergence where nothing converges).

Do this with whatever retrieval is available in chat:

- **Web:** kalshi.com category/hub pages, the public market-data API JSON (series, events,
  markets, orderbooks — no auth), and docs.kalshi.com for any new market families or mechanics.
- **Repo** (optional, when live numbers help): reuse `scripts/kalshi_market_survey.py` via the
  ops channel — the user's prior survey found weather is a backwater; liquidity concentrates in
  Sports / Elections / Politics / Crypto. Re-confirm, since it drifts.

Note per active category: rough liquidity/volume, spread width, bracket/ladder structure,
settlement cadence (one-off vs recurring/hourly/daily), and what fresh information drives
repricing. Recurring high-frequency settles (hourly crypto, daily weather) are the fertile
ground for the staleness family and for building a Kelly-sizable track record; one-off lumpy
events (a single election) are hard to validate and hard to size.

**Gate:** You know, for each live category, roughly how liquid it is and what information moves
it — enough to judge which mechanics fit where.

## Phase 2 — Diverge: generate a broad candidate slate

Now generate widely. Read `references/generation-grid.md` — it's the fountain: a grid of
trading mechanics (directional taking, model-vs-quote staleness, maker/liquidity provision,
lead-lag / relative value, structural/mechanical, event-conditional reaction) crossed with
market categories and data-edge axes (what fresh signal could you compute that the marginal
trader doesn't?). Walk the grid deliberately so you don't tunnel on one corner.

Rules for this phase:

- **Breadth before judgment.** Get ≥ ~12 candidates on the table before scoring any. Include
  some you suspect are weak — the point is coverage, and weak-looking ideas sometimes survive
  screening for a non-obvious reason.
- **Anti-anchor.** The gravity here pulls toward variants of what already works (more weather
  cells, more crypto ladders). Force yourself off the island: for every candidate that extends
  an existing book, generate one in a category or mechanic the portfolio has no exposure to.
- **Weight the priors, don't obey them.** Lean toward the staleness family (meta-lesson 2) and
  away from naive price-history edges (meta-lesson 1) — but still populate the whole grid. A
  prior is a starting weight, not a filter; the screen does the cutting.
- **Each candidate is one line:** mechanic × category, the fresh signal, and the one-sentence
  edge ("the market underweights X because Y").

**Gate:** ≥ ~12 candidates spanning multiple mechanics and multiple categories, at least a few
with zero correlation to the existing book. A slate that's all weather or all one mechanic
fails this gate — go back and widen.

## Phase 3 — Screen: score every candidate against the rubric

Now converge. Read `references/screening-and-handoff.md` for the full rubric. Score each
candidate — don't rank on vibes. The rubric weights, in order of how often they kill an idea
here:

1. **Correlation to existing books** (the portfolio lens). Does it share a return driver with a
   live book? Weather brackets all move together in a heat wave; more crypto-ladder tails
   correlate with theta. Shared driver → heavily penalized even if the edge is real, because it
   adds variance without diversification. Genuinely uncorrelated → bonus toward the $100/mo goal.
2. **Edge plausibility given the meta-lessons.** Does it fit a family that has worked
   (staleness/information-lag → high) or one that's proven dead (naive price-history
   calibration, passive-on-informative, locked arb → low)? Name who's on the other side and why
   they haven't corrected it.
3. **Cost survival.** Estimate EV vs the real cost: the fee on both legs
   (`ceil(0.07·qty·P·(1−P)·100)` cents), the spread you cross, and for cross-venue the ~2–4¢
   round-trip. For any passive/maker idea, the adverse-selection haircut is mandatory — assume
   filled-when-wrong until a tape says otherwise. An edge that only survives gross is dead.
4. **Testability — including testability *NOW* (a hard pre-promotion gate).** Two parts: (a) *can*
   it be validated with data you can get via a probe you can write (a read-only `scripts/` study
   or web-fetchable history)? and (b) **does enough *settled* data exist *today* to grade it?**
   Part (b) is the gate the record most needs: FREEZE and COMPIN both promoted, got full probes
   written, and returned **UNTESTABLE because the settled tape didn't exist yet** — two wasted
   probe cycles. Before promoting, estimate the settled sample available now against the probe's
   own n-floor (the P1/P2 trade minimums). **If it's below the floor, the call is HOLD (pending
   data accrual) with a data-growth trigger — not PROMOTE.** Untestable-in-principle → not
   promotable however clever; untestable-*yet* → HOLD, not a probe.
5. **Capacity / liquidity — and venue age.** From Phase 1: can it absorb meaningful size, and
   settle often enough to build a track record? A great edge on a $50 market is a hobby. **New
   venues are a specific trap: they're attractive (uncorrelated, launch-retail counterparty) but
   structurally data-poor** — both of the record's UNTESTABLE verdicts chased a venue launched
   that same month. **For any candidate on a venue younger than ~2 months, HOLD-by-default until a
   settled-liquidity census shows a gradeable tape** (this is testability-NOW applied to the venue,
   not the idea).
6. **Infra reuse.** Which existing probe / module / dataset does it extend (`xvenue_*`,
   `weather_*`, theta, the scanner, Polymarket snapshots)? High reuse → faster to a verdict →
   cheaper to test.

Produce a **scored table** (candidate × the six axes + a promote/hold/kill call). Be blunt;
most candidates should not promote. Killing an idea here, before a probe, is the cheapest
possible win.

**Gate:** Every candidate scored on all six axes with an explicit call. Typically 1–3 promote.

## Phase 4 — Promote: pre-registered thesis + probe plan

For each survivor, produce the handoff artifact in the repo's format (template in
`references/screening-and-handoff.md`), matching `THETA_THESIS.md`:

- **One-liner** — the edge in a sentence a trader would recognize.
- **Mechanism** — what mispricing, why it exists, who's on the other side, why it persists.
- **Pre-registered falsifiable predictions (P1…Pn)** — written *before* any validation, each
  with a concrete pass/fail threshold and a kill criterion, so the test can't be quietly
  re-scoped after the fact. This is non-negotiable and is the whole reason to write the thesis
  before probing.
- **Probe plan, staged: recon census FIRST, then the full probe.** Do **not** spec a
  hundreds-of-line probe as step one. Spec a **cheap ~20-line recon census** that answers only
  "does the settled data exist, and how much?" (settled-market count, volume, price-type/field
  presence) — the full probe is written *only if the census clears the n-floor*. FREEZE's own
  enumeration co-deliverable proved this: COMPIN could have been a one-run "0 settled TWAP
  markets → UNTESTABLE" with zero probe code. Name the census, then the full probe: dataset +
  provenance, what it measures, no-lookahead construction, the promotion result, the existing
  probe/dataset it reuses, and whether it needs allowlisting in `ops_runner.py`.
- **Cost + capacity note** — the fee/spread/adverse-selection math and the rough size the market
  absorbs.
- **Correlation note** — what it's (un)correlated with in the current book, and what that's
  worth to the $100/mo goal.

That artifact is the bridge: it enters the repo's pipeline as a probe run via ops → (if +EV) a
paper book in `paper/strategies.py` + `kalshi_bot/<name>/` → forward-test → live gate.
Generically, it enters the `kalshi-strategy` skill at its Phase 2 (data pipeline) / Phase 4
(backtest) with the thesis already articulated.

**Also in Phase 4 — two bookkeeping steps that keep the pipeline honest across runs:**

- **Reconcile the holds queue** (in the idea-model run doc). The queue grows every run and
  accumulates duplicates and stale entries. Each run: collapse duplicate holds, retire ones whose
  premise is dead, and — importantly — **flag which holds' triggers have FIRED** (a fired trigger
  is an actionable dive, not a parked one; COMPIN's trigger fired off FREEZE's enumeration).
  Carry the reconciled queue forward with explicit trigger-state per hold.
- **Append to the scorecard** (`docs/IDEA_MODEL_SCORECARD.md`). Add a row for each idea you
  promote this run (date, family, thesis doc, "pending probe"). When a verdict later lands, update
  its row (paper-book / killed / untestable) so the base rate stays current for the next run's
  Phase 0.

**A promote is not the only good outcome.** If the screen (with the testability-NOW and venue-age
gates) clears nobody, the honest output is often **"promote nothing this run; here's the
highest-value *hold* to advance"** — surface the fired-trigger hold or the next data-accrual date.
The record has a run that promoted 0/28 and that was a *good* run.

**Gate:** Each promoted idea is a self-contained pre-registered thesis with falsifiable
predictions and a staged (recon-census-first) probe plan; the holds queue is reconciled and the
scorecard updated — ready to hand to the validation machinery with no further generative work.

## Guardrails

- **Ground before you generate.** Read the journal *and the scorecard* first; never regenerate a
  ruled-out idea (or re-promote a 0-for-everything family) without naming a specific material
  difference.
- **Ask for the scope before generating.** Default to a scoped deep dive chosen from the
  data-grounded 5-option menu (Phase 0.5); only sweep the full board when the user asks. Don't
  generate before the user has picked a scope.
- **Breadth before judgment (within the chosen scope).** Don't let the screen suppress
  generation; get the slate on the table first.
- **Correlation is a first-class screen.** Variants of existing books are not new ideas —
  measure the shared return driver, not the surface.
- **Cost and adverse selection are hard gates.** Evaluate net of both-leg fees and realistic
  fills; haircut every passive idea for adverse selection by default.
- **Testability-NOW and venue-age are pre-promotion gates.** Don't promote an idea whose settled
  data doesn't exist *yet*, or one on a <~2-month-old venue, until a settled-liquidity census
  clears the probe's n-floor — HOLD it with a data-growth trigger instead. Spec the cheap recon
  census before any full probe.
- **Pre-register predictions.** Promoted theses state falsifiable predictions with kill criteria
  before validation runs — no post-hoc re-scoping.
- **Close the loop.** Reconcile the holds queue and update the scorecard every run; "promote
  nothing, advance a hold" is a legitimate, sometimes-best outcome.
- **This skill stops at the probe spec.** It does not run probes or touch live money. It *may*
  pull lightweight DB-derived context via the ops channel (or read the loop-status / digest docs)
  to ground the Phase 0.5 menu — that's grounding, not validation. It hands a pre-registered
  thesis to the pipeline. Not investment advice; validated edges still go through forward-testing
  and small live sizing.

## Reference files

- `references/generation-grid.md` — the divergent engine: mechanics × categories × data-edge
  axes, with the current meta-lessons as priors and worked seed-ideas, plus the scoped-vs-broad
  mode note. Read for Phase 2.
- `references/screening-and-handoff.md` — the six-axis scoring rubric (incl. the testability-NOW
  and venue-age gates), the staged recon-census→probe plan, and the pre-registered-thesis
  template for handoff. Read for Phases 3–4.
- `docs/IDEA_MODEL_SCORECARD.md` — the promote→verdict ledger + base rate + per-family hit-rate.
  Read in Phase 0 to calibrate; update in Phase 4.
