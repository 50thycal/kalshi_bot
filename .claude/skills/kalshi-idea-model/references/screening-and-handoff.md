# Screening & Handoff

Read in Phases 3–4. Two parts: the **scoring rubric** (cut the slate) and the **handoff
template** (promote survivors in the repo's format).

## Part A — The six-axis screen

Score every candidate on all six. Order reflects how often each one kills an idea — correlation
and cost do most of the cutting. Use a simple scale per axis (e.g. −2 to +2, or
kill/weak/ok/strong); the point is an explicit, defensible call, not false precision. **Most
candidates should not promote.**

### 1. Correlation to existing books (the portfolio lens) — kills the most "new" ideas

Does the candidate share a return driver with a live book (fav/favband/nws/cal/dist/pm/con/cwin/obs,
the scanner books, theta/theta1/2/3)? Look *through the surface to the driver*:

- More weather brackets/cities → all move together in a heat wave → correlated with weather books.
- More crypto-ladder tails → correlated with theta.
- A different mechanic on the same underlying can still be uncorrelated (making vs taking the
  same market have different return drivers) — judge the driver, not the ticker.

**Scoring:** shared driver with a live book → **heavy penalty** even if the edge is real (adds
variance, not diversification). Zero correlation → **bonus** (uncorrelated ballast is worth more
than its raw edge toward the $100/mo goal). This axis can veto an otherwise-good idea.

### 2. Edge plausibility given the meta-lessons — the prior

Which family does it fall in?

- **Staleness / information-lag** (compute fair value faster than the quote) → **high prior**
  (portfolio's proven edge shape).
- **Naive price-history calibration/persistence** on mature liquid markets → **low prior**
  (proven efficient).
- **Passive/maker on informative markets** → **low** unless adverse selection is explicitly
  handled (measured passive fills + model gate).
- **Locked arbitrage** → **dead** (882-event scan). Reframe as cost-gated relative value or
  don't promote.

Also: **name who's on the other side and why the mispricing persists.** "Retail lottery flow
that doesn't run a vol model" is credible; "the market is just wrong" is not.

### 3. Cost survival — hard gate

Estimate EV against *real* cost, not gross:

- **Fee, both legs:** `ceil(0.07 · qty · P · (1−P) · 100)` cents per leg — quadratic, worst near
  50¢, ~free near the tails.
- **Spread crossed** on entry (and exit if not held to settlement).
- **Cross-venue round-trip ~2–4¢** if it's a lead-lag / relative-value play.
- **Adverse-selection haircut** (mandatory for any passive idea): assume filled-when-wrong until
  a tape proves otherwise — the weather-maker lesson (+1¢ gross → −8.6¢ realized).

An edge that only survives gross is dead. This axis alone kills many candidates.

### 4. Testability — promotable or not

Can it be validated with data you can actually get, via a probe you can write? Name:

- **The dataset + provenance** — a self-contained read-only `scripts/` study (stdlib + psycopg)
  runnable via the ops channel, or web-fetchable public price history (Kalshi candlesticks
  `period_interval=1`; Polymarket CLOB `prices-history?market=<FULL clobTokenId>&fidelity=1` —
  use the full clobTokenId), or an existing collected table.
- **The measurement** — the specific number that decides it, no-lookahead.

Untestable, however clever → not promotable. If it needs live in-play data the repo doesn't yet
collect (e.g. sub-minute game tapes), say so — that's a data-collection task the thesis must
specify.

### 5. Capacity / liquidity — from the Phase 1 survey

Can it absorb meaningful size, and does it settle often enough to build a Kelly-sizable,
statistically-readable track record? Recurring high-frequency settles (hourly crypto, daily
weather, in-play games) ≫ one-off lumpy events (a single election). A great edge on a $50 market
is a hobby, not $100/mo.

### 6. Infra reuse — speed-to-verdict multiplier

Which existing probe / module / dataset does it extend? `xvenue_*` (cross-venue), `weather_*`,
theta, the scanner, the Polymarket snapshots, the paper engine. High reuse → cheaper and faster
to a verdict → promote sooner among near-ties.

### Output of Phase 3

A **scored table**: candidate × the six axes + a **promote / hold / kill** call and a one-line
reason. Be blunt. Killing an idea here, before a probe, is the cheapest win available. Typically
1–3 promote.

## Part B — Handoff template (Phase 4)

For each survivor, write this. It matches `docs/THETA_THESIS.md` so it drops straight into the
repo's pipeline. The **pre-registered predictions are the load-bearing part** — written before
any validation so the test can't be quietly re-scoped after the fact.

```markdown
# <NAME> — <one-line description of the edge>

*Thesis written <date>, before any validation ran; the falsifiable predictions below are
pre-registered. Status: pending probe.*

## One-liner
<The edge in one sentence a trader would recognize — mechanic, market, and the signal.>

## Mechanism
- **What mispricing:** <what is priced wrong, and in which direction.>
- **Why it exists / who's on the other side:** <the counterparty and their behavior — e.g.
  retail lottery flow that doesn't run a vol model; a venue that reprices slower on news.>
- **Why it persists:** <why this hasn't been arbitraged away — structural, attention, speed.>
- **Edge family:** <staleness / lead-lag / structural / …> and why the prior supports it.

## Pre-registered predictions (write BEFORE validating; each with a kill criterion)
- **P1 — <claim>.** PASS if <concrete threshold, in ¢/contract net of both-leg fees or a
  measured rate>; FAIL / KILL if <threshold>.
- **P2 — <claim>.** PASS if <threshold>; KILL if <threshold>.
- **P3 — <claim, e.g. the effect is concentrated in a specific cell / time window>.** …
- **Decision rule:** build the paper book only if <which predictions must pass>; if <…>,
  shelve the family. (State it now so results can't be re-scoped.)

## Probe plan
- **Script / pull:** <the read-only `scripts/` study to write, or the web pull to run>;
  reuses <existing probe/dataset>; needs allowlisting in `ops_runner.py`? <yes/no>.
- **Dataset + provenance:** <exact source, and how it's kept separate from other tables —
  never mix provenance silently>.
- **No-lookahead construction:** <how point-in-time correctness is guaranteed — which
  model cycle / observation time is used at each decision point>.
- **Measurement:** <the specific numbers computed — sellEV/buyEV in ¢, win% vs implied,
  split-half, sliced by time-to-expiry / cell as relevant>.
- **Promotion result:** <what the probe must show to become a paper book>.

## Cost + capacity
- **Fee/spread math:** <both-leg fee at the relevant price band; spread crossed>.
- **Adverse selection:** <the haircut, if passive>.
- **Capacity:** <rough size the market absorbs; settle frequency → track-record rate>.

## Correlation
- **Vs current book:** <what return driver it shares or doesn't with live books>.
- **Value to $100/mo:** <why it helps — new uncorrelated edge, or incremental on a proven one>.
```

## Where the artifact goes

This thesis is the bridge into the validation machinery:

- **In the repo:** write the probe → run via the ops channel → log the verdict in
  `RESEARCH_JOURNAL.md` / `edge_research.md` → if +EV, build a paper book (`paper/strategies.py`
  + `kalshi_bot/<name>/`) riding the live cycle like mmsell/theta → forward-test at ~100s of
  trades/week → live gate (`live/executor.py` allowlist), small size.
- **Generically:** it enters the `kalshi-strategy` skill at Phase 2 (data pipeline) / Phase 4
  (backtest) with the thesis and predictions already articulated — no further generative work.

The idea model's job ends here: a **pre-registered, falsifiable, cost-aware, testable thesis,
ranked by its expected contribution to $100/month realized.** Validation takes it from there.
