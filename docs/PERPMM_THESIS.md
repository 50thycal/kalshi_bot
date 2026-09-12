# PERPMM — both-legs-passive premium reversion on Kalshi's liquid crypto perps

*Thesis written 2026-09-12, before any validation ran; the falsifiable predictions below are
pre-registered and must not be re-scoped post-hoc. Promoted from `docs/IDEA_MODEL_20260912.md`
(candidate P1). Status: **WITHDRAWN 2026-09-12, before any probe ran.** The same-day, operator-approved
PASSIVE-PERP census (WS-017, `docs/PASSIVE_PERP_CENSUS.md`) measured this exact premise's outright
price return under *instant* maker fills on the retained tape: BTC −4.84 bps / ETH +0.05 bps after
2 bps/leg, both trailing a random-direction control → HOLD, revisit only with a mechanically
distinct premise. P1–P4 below use a stricter (through-price) fill rule, so they can only score lower;
the thesis is dominated and its census was not shipped. Kept unedited as the pre-registration it was.*

**This is a riff, and it says so.** PERP-V1 (`docs/PERP_V1_THESIS.md`, RETIRED 2026-09-02) is
the parent. Its arm A measured the premium-reversion mechanism and was killed by execution
economics — taker/taker under a 24 bps tier-0 round trip. The 2026-09-03 per-ticker split
(`RESEARCH_JOURNAL.md`) recorded that the mechanism is present on the *liquid* books too (BTC
+6.75 bps gross on 0.52 bps of spread) and named a both-legs-passive variant as "the only
surviving thread … a new Version or a new experiment with a fill model in its pre-registration —
an operator decision." This document is that pre-registration. Under `NEW_ONLY` it is a **new
experiment** (`perpmm`), not a reopening of `perp-v1`: the question changed from "does the
premium revert" (answered: yes) to "can a resting order capture it net of fills and fees".

## One-liner

Rest a bid (ask) on KXBTCPERP/KXETHPERP when the perp's premium to its index is more than 2.5σ
below (above) its rolling mean, rest the exit at the mean, and earn the reversion minus 4 bps of
maker fee — provided the resting orders actually fill at a rate and quality the mechanism survives.

## Mechanism

- **What mispricing:** the perp's `price` drifts from `reference_price` (both on the market row,
  both in the retired tape) and reverts within minutes; PERP-V1 measured +6.75 / +8.57 bps gross
  on BTC / ETH at a 2.5σ entry with 3–4 minute holds, against a random-direction control that
  earned exactly minus the spread.
- **Why it exists / who's on the other side:** leveraged retail flow crossing the spread in
  bursts on a young venue; the reversion is the book's own market makers refilling. Arm A took
  liquidity *from* those makers and paid for it; this variant tries to *be* the refill.
- **Why it persists:** at tier 0 the taker fee is 2.7× the entire BTC spread, so nobody can
  arbitrage the premium by taking; only resting flow can, and the venue is weeks old.
- **Edge family:** maker / liquidity provision on a mean-reverting signal — the one family that
  has ever produced a realizable +EV book here (`mmsell10`), and the one with the mandatory
  adverse-selection haircut. The prior is **not** "passive wins"; it is "passive is the only
  configuration whose fee arithmetic is positive, so it is the only one worth a fill model."

## Confronting the graveyard (required)

- **PERP-V1 arm A** (FAIL): taker/taker. This variant changes both legs to passive, which is the
  exact combination the close-out table computed as the only positive one (+10.5 bps on the
  universe mean, +2.75 to +8 on liquid names). Material difference: execution, not signal.
- **mmsell6 / mmsell11 / the offset A/B** (fill-model mirages): every one of them assumed a
  resting order fills whenever price touches it. **P2 below forbids touch fills**; only a minute
  that trades *through* the resting price counts, and P3 measures what the touch-vs-through gap
  costs so the haircut is measured, not assumed.
- **PERPBASIS / PERPFUND (killed at screen 2026-07-12)**: those were funding/basis *directional*
  ideas; this is spread capture on a measured reversion. Funding is irrelevant at 3–5 minute
  holds (≈1% of a funding window) and is not used.

## Pre-registered predictions (bps of notional per round trip, net of tier-0 maker fee 2 × 2.0 bps)

- **P1 — The passive entry fills often enough to matter.** Over ≥ 200 signal events per ticker
  on BTC and ETH, a resting order one tick inside the far side at the 2.5σ excursion is filled
  (through-price rule) on **≥ 35%** of events within 5 minutes. **KILL if < 20%** — a 20% fill
  rate on a 3-minute hold cannot build a track record at any sane size.
- **P2 — Filled entries are not the losers.** Net edge on *filled* round trips (through-price
  entry, through-price exit at the rolling mean, else taker exit at 10 minutes charged 12 bps)
  is **≥ +2.0 bps/trade** on both BTC and ETH, n ≥ 100 filled trips each. **KILL if ≤ 0 on
  either.** Fills correlated with the wrong outcome (the mmsell live lesson) would show up here.
- **P3 — The touch/through gap is bounded.** Net edge under the touch-fill assumption minus net
  edge under P2's through-fill rule is **≤ 3 bps**. **KILL if > 6 bps** — a gap that large means
  the fill model, not the mechanism, is producing the number, and no live test would be
  believable.
- **P4 — Not a wide-spread artifact.** P2 holds on BTC *and* ETH (spread < 1 bps) without
  needing the SOL/XRP/alt names. If only alts clear, HOLD; do not pool.
- **Decision rule:** paper book only if P1 ∧ P2 ∧ P3 ∧ P4. Any KILL closes the passive thread
  and, with arm A's FAIL, closes premium reversion on Kalshi perps at tier 0 entirely; record
  it so it is not re-proposed. Gold/silver perps (launched 2026-09-09) are **co-measured only**
  and HOLD by venue age whatever they show.

## Probe plan (staged — recon census FIRST)

- **Recon census (step 1, cheap, built):** `scripts/perp_candle_census.py` — reads
  `/margin/markets/{t}/candlesticks?period_interval=1` for BTC/ETH/SOL/XRP (+ metals) and
  reports (C1) whether candles carry high/low — the field a through-price fill rule needs,
  (C2) the share of active minutes, (C3) intra-minute range vs spread. **If C1 is false the
  verdict is BLOCKED_DATA, not a probe**: with closes only, any fill rule is a guess, and this
  repository has been burned by optimistic fill models twice.
- **Full probe (step 2, only if C1 passes):** a candle-backed replay — recompute the premium
  z-score from `reference_price` (the retired `perp_market_snapshots` tape can seed the
  rolling window; forward candles carry price only, so the probe must first confirm the
  reference is recoverable at 1-minute resolution, else it needs the collector restarted at
  60 s — `perps_collector_enabled` is off since 2026-09-02 and turning it on is a
  read-only collector, not a book). Score P1–P4 with the through-price rule. Reuses
  `perp_arm_scores.py`'s scoring frame. Needs allowlisting: **yes** (new script).
- **Dataset + provenance:** public perp candles (forward) + the retired 72 h snapshot tape
  (`perp_market_snapshots`, `perp_orderbook_snapshots`), never mixed silently — the probe
  labels each row's source.
- **No-lookahead construction:** the z-score at minute *t* uses only candles ending ≤ *t*; a
  resting order placed at the close of minute *t* can fill only in minutes > *t*; the exit
  order is placed after the entry fill's minute, never in it.
- **Measurement:** fill rate (P1), net bps/trade through-fill (P2), touch-minus-through gap
  (P3), per-ticker split (P4), plus mean hold and the 12 bps taker-exit share.
- **Promotion result:** P1–P4 pass → register `perpmm` in Experiment OS as a paper book with a
  fresh contract; live is a separate hard-stop decision and would require a Platform Revision
  for perp fee/fill/leverage semantics (`PERP_V1_THESIS.md` §7).

## Cost + capacity

- **Fee math:** tier-0 maker 0.020%/side → **4 bps round trip**; a forced taker exit costs 12
  bps and is charged in P2 whenever the passive exit fails within 10 minutes.
- **Adverse selection:** the whole question — P1/P3 measure it rather than assume it away. A
  resting order at an extreme fills when the excursion *extends*; the replay must show the
  reversion still pays after that selection.
- **Capacity:** KXBTCPERP $42M/day and KXETHPERP $172M/day (PERP-V1 depth read); one signal
  every few hours per ticker → tens of trips/week, a readable track record in ~6 weeks.

## Correlation

- **Vs current book:** the live line is `Fmmsell10` (sports-heavy cheap-tail maker-sell). The
  perp premium's driver is intra-minute leveraged-retail flow on crypto perps — no shared
  settlement, clock or venue-flow with sports longshots. Crypto beta exposure is minutes long
  and hedged by construction (long perp below index / short above).
- **Value to $100/mo:** the portfolio's binding constraint is a **second independent +EV
  stream** (PORT 2026-07-22). This is the one thread in the record with a measured positive
  gross on a liquid book and a fee structure that can be positive; it is either that second
  stream or a clean, cheap close of the whole perp line.
