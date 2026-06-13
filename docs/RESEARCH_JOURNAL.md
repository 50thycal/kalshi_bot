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

---

## Backfill structural-edge hunt (Apr–Jun history, 964 complete events)

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
