# Generation Grid — the divergent engine

Read in Phase 2. The goal is **coverage**: walk this grid deliberately so generation doesn't
tunnel on one corner (the corner it wants to tunnel on is "another weather/crypto variant").
Generate breadth first; the screen cuts later.

**Two modes (set in Phase 0.5):**
- **Scoped dive (default)** — the user picked one venue/mechanic focus. Do NOT walk the whole
  grid. Instead, *go deep in the chosen cell*: generate ≥ ~8 candidates that are variants,
  sub-cells, adjacent mechanics on the same underlying, and the honest failure modes *within* the
  focus. Use the grid axes below only to make sure you've covered the focus thoroughly (e.g. for a
  scoped "commodity TWAP" dive, walk each mechanic against commodities, not against every
  category). Depth over breadth — this is the mode that produced the pipeline's one paper book.
- **Broad sweep** — the user asked for a full-board search. Walk the entire grid below as written,
  ≥ ~12 candidates across mechanics × categories with forced anti-anchor slots.

The grid has three axes. A candidate is a **mechanic × category anchored by a data-edge** (the
fresh signal you'd compute). Walk **mechanics as the outer loop** — they're the part the
portfolio is most likely blind to.

## Axis 1 — Trading mechanics (the outer loop; walk all of these)

How you extract edge, independent of what you trade. The portfolio's two real edges are both
mechanic #2; the others are under-explored.

1. **Directional taking** — form a better probability than the market, cross the spread, hold to
   settlement. **Prior: LOW** on mature liquid markets (efficient on price-history; weather
   directional is a graveyard except the narrow favband cell). Only viable where you have a
   genuine forecasting edge the crowd lacks.
2. **Model-vs-quote staleness (information-lag)** — compute fresh fair value from a fast signal
   and trade quotes that haven't caught up yet. **Prior: HIGH** — this is the portfolio's edge
   DNA (obs: observations pin the outcome before quotes move; theta: model-overpriced tails
   after spot moves). Generalize the shape: any market where a public signal updates faster than
   the Kalshi quote. Ask, for each category: *what's the fastest-moving input, and does the
   quote lag it?*
3. **Maker / liquidity provision** — rest offers, capture spread instead of paying it (fees
   favor the resting side). **Prior: DANGER** — adverse selection. Passive weather died at −8.6¢
   realized despite +1¢ gross. Only viable where you can measure realized passive fills and gate
   entries with a model (how theta survives). Any maker idea must carry an explicit
   adverse-selection haircut.
4. **Lead-lag / relative value (cross-venue or cross-market)** — one venue/market reprices
   first; trade the follower before it catches up. **Prior: MIXED** — cost-gated. Averaged
   cross-venue divergence is below round-trip cost on liquid non-shocking markets; the live open
   question is **in-play game markets** where a scoring event is a genuine information shock
   (goal → Polymarket pops → Kalshi lags 1–2 min). Requires event-conditional (shock-triggered)
   measurement, not averaging. Reuses `xvenue_*` + Polymarket snapshots already collected.
5. **Structural / mechanical** — edges from market construction, not forecasting: ladder
   monotonicity, MECE bracket coherence, settlement-rule quirks, expiry/roll mechanics,
   fee-structure asymmetries. **Prior: LOW** for locked arb (882-event Dutch-book scan =
   artifacts), but worth a look for soft structural mispricings the arb scan wouldn't catch
   (e.g. a bracket ladder that's internally coherent but collectively mis-centered vs a model; a
   settlement-timing window traders misread — the local-standard-time high window is exactly
   this kind of quirk).
6. **Event-conditional reaction** — a scheduled or unscheduled event triggers a repricing the
   market is slow to complete (data release, game event, headline). **Prior: MEDIUM,
   latency-gated.** This is mechanic #2 or #4 conditioned on a discrete event. Edge exists only
   if the post-event repricing is slow enough to act on and the follow-through clears cost.

## Axis 2 — Market categories (walk against each mechanic)

From the live survey (re-confirm; liquidity drifts). The prior survey found liquidity in
Sports / Elections / Politics / Crypto; weather is a backwater but is where the portfolio's
forecasting infra already lives.

- **Weather / temperature** — recurring daily settles, physical settlement (NWS), free
  probabilistic model data (NBM). Portfolio-saturated (many books); directional is efficient.
  New angles must be a **new mechanic** on it, not a new cell.
- **Crypto price ladders** — recurring hourly settles, deep retail lottery flow, a fast public
  signal (spot). Portfolio has theta here. Fertile for staleness, but check correlation with
  theta.
- **Sports** — mature sharp betting ecosystem; Kalshi often tracks external lines. **In-play/live
  game markets are the interesting frontier** (informative sub-minute repricing) — the untested
  lead-lag hypothesis lives here. Pre-game is efficient vs sportsbooks.
- **Elections / Politics** — high liquidity but lumpy outcomes (few independent bets → hard to
  size/validate), narrative-driven, headline-shocked. Event-conditional reaction is the only
  plausible mechanic; directional is a variance trap.
- **Economics / rates (CPI, jobs, Fed)** — the most-analyzed numbers in finance; efficient. Only
  a post-release reaction-latency angle is even worth screening, and it's contested.
- **Company / tech / misc.** — grab-bag; occasionally a thin, under-analyzed niche where a
  specific informational edge exists. Case-by-case.

## Axis 3 — Data-edge (the anchor for every candidate)

An edge needs a **fresh signal the marginal trader doesn't price rigorously.** For each
mechanic × category, ask what you could compute. Candidates without a concrete data-edge are
just opinions — don't put them on the slate.

- **Faster/better public model** — NBM probabilistic guidance, HRRR for same-day, GEFS ensemble
  spread (weather); realized-vol / spot models (crypto). The staleness family runs on these.
- **Faster observation of the resolving quantity** — station obs pinning a temperature; a game's
  live score; an on-chain/spot print. The obs edge shape.
- **Cross-venue price** — Polymarket (already collected), sportsbooks, spot exchanges — used as
  the fast leg in a lead-lag, not as an arb target.
- **Microstructure** — the shape of resting flow, tape imbalance, quote staleness after a print —
  a signal about *when* to rest or take, not *what* the outcome is.
- **Structural coherence** — a model's full bracket distribution vs the ladder's collective
  pricing (mis-centering), or a settlement-rule detail traders misapply.

## How to walk the grid (Phase 2 procedure)

1. For each mechanic (Axis 1), ask: which categories (Axis 2) is it viable in, given the live
   survey and the priors?
2. For each viable cell, ask: what data-edge (Axis 3) would power it — what fresh signal,
   computable faster/better than the crowd?
3. Write the candidate as one line: mechanic × category, signal, one-sentence edge.
4. Explicitly include ≥ a few cells in mechanics/categories the portfolio has **no exposure to**
   (lead-lag in-play sports; structural mis-centering; a non-crypto staleness play).
   Anti-anchoring is a hard requirement of the phase.

## Seed ideas (illustrative starting points, not an exhaustive or pre-approved list)

Use these to prime the pump, then generate beyond them. Each still must pass the Phase 3 screen
— several deliberately probe the priors.

- **In-play game lead-lag** (sports × lead-lag × live score). A scoring event reprices
  Polymarket per-game markets before Kalshi; trade Kalshi on the shock, one-venue (watch PM,
  trade Kalshi — no PM KYC/gas). This is the repo's own pending NEXT STEP — sub-minute,
  shock-conditional, on live in-play markets. Low correlation to weather/theta; high `xvenue_*`
  reuse.
- **Same-day weather via HRRR staleness** (weather × staleness × HRRR). Not a new directional
  cell — a mechanic shift: intraday, does the bucket quote lag the latest HRRR run / running
  observation on high-variance days? Extends the obs family; check it isn't already covered by
  obs.
- **Crypto favorite-buy** (crypto × directional/staleness × spot-vol). The parked theta
  side-finding: hourly 65–90¢ favorites ran ~9–11¢ underpriced (small n). Mirror of the
  tail-sell; correlated with theta flow — screen the correlation hard.
- **Structural ladder mis-centering** (any recurring ladder × structural × full model
  distribution). Not locked arb (proven dead) — a soft edge: when a model's full distribution
  says the ladder's mass is collectively mis-centered (too wide / off-center), trade the coherent
  correction. Note this is exactly the failure that bit theta live (near-money range buckets
  mis-centered) — which cuts both ways: it's a real phenomenon, but the model must be right about
  the center.
- **Non-crypto, non-weather staleness** (e.g. a market with a fast public data feed the quote
  lags). The genuine anti-anchor slot — find a category the portfolio doesn't touch where a
  public signal updates faster than the Kalshi quote. Deliberately empty here; fill it during
  generation.
