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
| **Phase 0** | Ground in what's already been tried | know the graveyard + the live books |
| **Phase 1** | Survey the live board | know where liquidity + activity actually are |
| **Phase 2** | Diverge — generate a broad slate | ≥ ~12 candidates spanning mechanics × categories |
| **Phase 3** | Screen — score against the rubric | each candidate scored, not vibes-ranked |
| **Phase 4** | Promote — pre-registered thesis + probe | survivors handed off in the repo's format |

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
(the graveyard), and the meta-lessons (the priors). Generating before you've read the journal
is the cardinal sin here.

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
4. **Testability.** Can it be validated with data you can actually get, via a probe you can
   write (a self-contained read-only `scripts/` study runnable through ops, or web-fetchable
   public price history)? Name the dataset and the measurement. Untestable → not promotable,
   however clever.
5. **Capacity / liquidity.** From Phase 1: can it absorb meaningful size, and settle often
   enough to build a track record? A great edge on a $50 market is a hobby.
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
- **Probe plan** — the exact `scripts/` study to write (or web pull to run): dataset +
  provenance, what it measures, no-lookahead construction, and the result that would promote it
  to a paper book. Note which existing probe/dataset it reuses and whether it needs allowlisting
  in `ops_runner.py`.
- **Cost + capacity note** — the fee/spread/adverse-selection math and the rough size the market
  absorbs.
- **Correlation note** — what it's (un)correlated with in the current book, and what that's
  worth to the $100/mo goal.

That artifact is the bridge: it enters the repo's pipeline as a probe run via ops → (if +EV) a
paper book in `paper/strategies.py` + `kalshi_bot/<name>/` → forward-test → live gate.
Generically, it enters the `kalshi-strategy` skill at its Phase 2 (data pipeline) / Phase 4
(backtest) with the thesis already articulated.

**Gate:** Each promoted idea is a self-contained pre-registered thesis with falsifiable
predictions and a runnable probe plan — ready to hand to the validation machinery with no
further generative work.

## Guardrails

- **Ground before you generate.** Read the journal first; never regenerate a ruled-out idea
  without naming a specific material difference.
- **Breadth before judgment.** Don't let the screen suppress generation; get the slate on the
  table first.
- **Correlation is a first-class screen.** Variants of existing books are not new ideas —
  measure the shared return driver, not the surface.
- **Cost and adverse selection are hard gates.** Evaluate net of both-leg fees and realistic
  fills; haircut every passive idea for adverse selection by default.
- **Pre-register predictions.** Promoted theses state falsifiable predictions with kill criteria
  before validation runs — no post-hoc re-scoping.
- **This skill stops at the probe spec.** It does not run probes, touch live money, or need
  Railway/DB access — it hands a pre-registered thesis to the pipeline. Not investment advice;
  validated edges still go through forward-testing and small live sizing.

## Reference files

- `references/generation-grid.md` — the divergent engine: mechanics × categories × data-edge
  axes, with the current meta-lessons as priors and worked seed-ideas. Read for Phase 2.
- `references/screening-and-handoff.md` — the six-axis scoring rubric grounded in the repo's
  reality, plus the pre-registered-thesis + probe-plan template for handoff. Read for Phases 3–4.
