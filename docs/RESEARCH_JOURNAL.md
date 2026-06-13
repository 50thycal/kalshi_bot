# Weather-markets research journal

A running log of every edge hypothesis we've tested on Kalshi daily-temperature
markets, what the data said, and the verdict. Newest entries at the top. The
goal: a durable record of *why* each live book exists and why each dead idea was
abandoned, so we don't re-litigate settled questions.

Conventions:
- **EV/trade** is in cents per contract, **fees on both legs** (Kalshi
  `ceil(0.07·qty·P·(1−P)·100)`), unless noted.
- "backfill" = `backfill_weather_markets` / `backfill_weather_candles` (Kalshi
  REST history, hourly candles, spring Apr–Jun, last ~48h per market — separate
  provenance from the live-collected `weather_*` tables; never mixed silently).
- Probes live in `scripts/weather_backfill_edges.py` (structural edges) and
  `scripts/weather_window_sweep.py` (entry-window EV); run via the `ops` channel.

---

## Standing verdict / themes

**Kalshi's temperature ladder is efficient on everything derivable from price
history.** Overround, persistence/autocorrelation, calibration across the price
range — all priced. The only positive-EV we've found comes from *information the
market is briefly slow to incorporate* (obs-confirmed late entry once the day's
real thermometer reading is in; cross-market signal from Polymarket) and from
*city-specific entry timing* (the city × window map). Everything that only uses
the ladder's own prices has come back dead or sub-fee.

Live books currently running: `fav` (control), `nws`, `cal`, `pm`
(Polymarket-cross), `cwin` (per-city high windows), `obs` (running-extreme late
entry). The legacy h12 window is flagged in the DB and excluded from the PnL
report.

### Build backlog — the bucket-probability edge model (NOT yet live)
The probability *engine* exists but only offline. Status:
- **Live:** ensemble-distribution collection — `OpenMeteoEnsembleClient` stores
  GFS+ECMWF member daily extremes (the forecast distribution) in `weather_ensembles`.
- **Offline only:** `scripts/weather_model_check.py` is the actual model —
  `member_bucket_prob` (Gaussian kernel, sigma = forecast error beyond ensemble
  spread) → `model_bucket_probs` (blend models → per-bucket P) → Brier/log-loss
  grading + a cost-aware trade sim (`edge = model_prob − implied`, trade buckets
  beyond min-edge). It grades the ensemble vs market.
- **Missing:** a *live paper book* that trades that distribution edge. The live
  `nws`/`cal` books are crude — they buy only the single bucket containing the
  point forecast (`forecast_in_bucket`), not the full mispriced-bucket set.
- **When built, bake in #6:** trust the market's implied mean but shrink its
  variance (~×0.78²) — equivalently tune the sigma kernel so the model's
  distribution is tighter than the raw ladder, since the market is overdispersed.

---

## Backfill structural-edge hunt (Apr–Jun history, 964 complete events)

### #8 — Diurnal-range coupling — strong coupling, PERFECTLY priced (no trade)
Within a city/day the overnight low (settles first, ~morning) and the afternoon
high are physically coupled; does the low inform the high beyond its price? (482
paired city-days)

- **Coupling is strong:** corr(high_anom, low_anom) = **+0.83** — a warm/cold
  airmass lifts both, as expected.
- **The market prices the spread almost perfectly:** implied diurnal range vs
  realized — mean(realized − implied) = **+0.04 °F**, corr = **+0.97**. The market
  nails the day's high-minus-low gap.
- **The low adds nothing to the high.** corr(high market_error, low anomaly) =
  **−0.07** (both realized *and* implied) — the high market has already priced
  whatever the low tells you. Even knowing the *realized* morning low (an obs-style
  entry) gives no predictive power on the high's error.
- **Tradeable test:** shifting the high by the low's anomaly (implied_high +
  k·low_anom) collapses win% 64% (k=0) → 29% → 18%, all negative; favorite 65% /
  −3.1¢. The k>0 "less-negative" pnl is the cheap-bucket artifact, not accuracy.

**Verdict:** strong real coupling, but Kalshi is *jointly* calibrated across a
city's high and low markets (range corr 0.97) — nothing to arb. Same theme. Probe:
`--analysis diurnal`.

### #7 — Cross-city W→E lead-lag — mechanism REAL, but FULLY PRICED (no trade)
Weather propagates west→east, so a western city's daily anomaly might lead an
eastern city's. Cities ordered by longitude (LAX→DEN→AUS→CHI→MIA→PHIL→NYC).

- **The physics is visible in the data.** Pooled corr(anomaly), downwind (west
  leads east) vs an upwind placebo (east leads west): lag 0 = +0.39 / +0.39
  (same-day cities just share regional weather — symmetric, as expected), but
  **lag 1 = +0.43 downwind vs +0.30 upwind**, **lag 2 = +0.40 vs +0.23**. The
  downwind-minus-upwind asymmetry (~+0.13–0.17 at lag 1–2) is the genuine
  fingerprint of airmasses moving W→E. This is a *real* detected signal — most
  lead-lag fishing finds nothing.
- **But the eastern market has already priced it.** corr(east market_error[d+lag],
  west anomaly[d]) = **−0.03 @ lag 1**, **−0.01 @ lag 2** (n≈2,800) — dead zero.
  By the time the east is at h12 the airmass is essentially overhead and the
  price reflects it (same public forecasts).
- **Tradeable test confirms no edge.** Shifting the east prediction by the western
  anomaly (pred = implied_east + k·west_anom[d−1]) *destroys* accuracy: win% 63%
  (k=0) → 25% (k=0.5) → 17% (k=1.0), all negative; favorite 65% / −2.8¢. The shift
  moves you off the (already-correct) implied center.

**Verdict:** we detected the propagation physics, but it's public information the
eastern market prices efficiently by h12. Same theme — derivable signal, no gap.
(Caveat: only tested at h12; the airmass is already imminent then. A much earlier
horizon, h36–48, is the only place this could conceivably be unpriced, but the
zero error-correlation even at lag 2 argues against it, and our backfill barely
reaches that far.) Probe: `--analysis leadlag`.

### #6 — Distribution shape / tail mispricing — REAL bias, NOT a standalone trade (→ model feature)
Normalize each event's h12 ladder into an implied PDF over bucket temperatures,
standardize the actual outcome (z = (actual − implied_mean) / implied_sd), and
check the shape. **This is the most actionable result so far.** (940 events)

- **Location is unbiased:** mean z = **−0.02 σ** — no systematic hot/cold lean in
  the market's implied mean.
- **The implied distribution is too WIDE (overdispersed):** SD(z) = **0.78** (< 1).
  Actual outcomes land *closer* to the implied mean than the prices say they
  should. **PIT tail mass lo=5% / hi=3%** (vs ~10%/10% if calibrated) confirms it
  — only ~8% of outcomes fall in the tails the market prices at ~20%. Reality is
  more predictable at h12 than Kalshi prices it; the **shoulders/near-tails are
  too rich and the center is too cheap.**
- **But the mispricing is below the cost floor.** Per implied-σ shell: center
  (0–0.5σ) wins 73% priced 72¢ (1¢ cheap), the 1.0–1.5σ shell wins 16% priced 18¢
  (2¢ rich), the far 1.5σ+ tail wins 2% priced 2.2¢ (fair — agrees with #4). The
  gaps are 1–2¢; after bid/ask + fees **every** YES and NO entry is negative
  (best cells ≈ −1.2¢). No naive buy-center / sell-shoulders book clears the floor.

**Verdict:** a *robust, mechanism-plausible* shape bias (retail spreads bets/lottery
tickets across tails; the market is slow to tighten its distribution as the day
constrains the outcome — same family as the `obs` thermometer-lag edge), but too
small to trade directly. **High value as a calibration feature for the forecast
model: trust the market's implied mean, then SHRINK its variance (~×0.78²).** The
resulting bucket probabilities should be better calibrated than the raw ladder,
especially by de-weighting the over-fat shoulders. This is the first finding that
feeds the edge model rather than just closing a door. Probe: `--analysis distshape`.

### #5 — Favorite momentum / convergence — REFUTED
Does the favorite's price drift predictably, and do bucket price moves trend?

- **Price moves are a random walk.** Autocorr of Δprice[h24→h12] vs Δprice[h12→h6]
  = **−0.03** (n=4798) — no momentum, no intraday reversion.
- **The favorite is fairly-to-richly priced and gets *worse* to buy as the day
  shortens:** buy-favorite-YES-hold-to-settle EV is **−1.8¢ @ h24**, **−3.5¢ @
  h12**, **−6.3¢ @ h6**. Win% rises (50→67→63%) but price rises faster — the
  market firms up *correctly* and the price already reflects it. No "favorite
  underpriced and converging" edge.
- **Chasing risers doesn't beat the favorite:** at h12 the biggest recent riser
  wins 63% (−4.3¢) vs the favorite's 67% (−3.5¢). The biggest faller wins only 6%
  — priced low, so its −2.8¢ isn't an edge either.

Corroborating note: the h24 favorite (−1.8¢) is the *least*-negative entry,
reinforcing the `cwin` finding that **earlier entry on highs beats late entry**
(late favorites pay up). That structural timing edge is already captured by the
city × window map; there is no separate momentum/convergence book to build.
Probe: `--analysis convergence`.

### #4 — Favorite-longshot bias (tail harvesting) — REFUTED
Bin every bucket at h12 by its mid price; sell cheap longshots (buy NO at
100−bid) and buy heavy favorites (YES at ask); grade on the actual winner.

Cheap tails are **well-calibrated** — actual win% sits on top of the implied
price (1–3¢ band wins 0.9%, 3–5¢ wins 4.3%, 5–10¢ wins 5.0%, 10–20¢ wins 13.6%).
After per-leg fees every sell-the-longshot band is negative (−0.3 to −2.4¢).
Heavy-favorite buys are non-monotone (80–90¢ −1.9¢, 90–95¢ +2.6¢, 95–100¢
−3.0¢) — the lone positive cell is noise at n≈90 (binary SE ~±5pts). **No
exploitable favorite-longshot bias from price alone.** Probe: `--analysis longshot`.

### #3 — City × entry-window map — VALIDATED (live as `cwin`)
Different cities firm up at different hours-to-close. A per-city window map beat a
flat window **out-of-sample**: pick the best window per city on the first 60% of
dates, evaluate on the last 40% → map **+2.9¢** vs flat **−2.4¢**. Significance +
month-stability + train/test holdout all checked. Built into the live `weather_cwin`
book (highs): `CHI:18, LAX:18, DEN:18, NYC:10, MIA:24, AUS:24, PHIL:10`.
Probe: `weather_window_sweep.py --validate`.

### #2 — Persistence / seasonal drift — PRICED (no trade)
Temperature is strongly autocorrelated, but the market already prices it. The
market's open-implied mean tracks yesterday's outcome essentially as well as the
actual does (implied corr 0.92 ≥ persistence corr 0.91); the naive "buy
yesterday's-outcome bucket at the open" strategy loses. A faint mean-reversion
residual exists (see below) but is too small to trade on its own.
Probe: `--analysis persistence`.

### #2b — Mean-reversion (fade after extreme) — REAL but TOO SMALL
After an anomalously hot/cold day the market slightly over-extrapolates;
corr(market_error, yesterday's anomaly) ≈ −0.18 pooled. Physically concentrated
in **continental cities** (DEN, PHIL, CHI) as expected. But the best fade
correction only adds **~+0.4¢/trade** — below the fee+noise floor to trade alone.
Kept as a candidate *model feature*, not a standalone book. Probe: `--analysis meanrev`.

### #1 — Ladder overround / sell-the-ladder arb — DEAD
In an N-bucket exclusive market the YES prices "should" sum to ~100¢. Across
every event × snapshot the bids sum < 100 on the tight CLOB; **0% of snapshots**
offered a risk-free sell-the-ladder credit net of per-leg fees. No structural vig
to harvest. Probe: `--analysis overround`.

---

## Earlier findings (pre-backfill, live paper books)

- **Simple TA has no edge.** Phases 3–3.5: over 1,000+ paper trades, `momentum`,
  `reversion`, and `buy_favorite` all lost ≈ −5¢/contract (the round-trip
  spread+fee cost floor). This is what drove the pivot to weather.
- **`buy_favorite` weather baseline** also bleeds the cost floor — confirms the
  ladder is fairly priced; an edge has to come from forecast/timing/cross-market
  information, not from buying the crowd's favorite.
- **NWS forecast & bias-corrected (`cal`) books** collect forecast data and trade
  the forecast bucket; the model-check harness grades the ensemble vs market
  (Brier/log-loss/EV) before any size goes on.
- **Polymarket cross (`pm`)** trades Kalshi toward a divergent Polymarket price
  on the same city (LAX/MIA/AUS, discovered via tags).
- **Obs-confirmed late entry (`obs`)** buys the bucket containing the day's
  running max/min once the real thermometer reading is in past a local cutoff
  hour and the ask is still ≤ cap — the thermometer-lag edge.

---
