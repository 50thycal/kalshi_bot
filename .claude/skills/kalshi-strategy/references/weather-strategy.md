# Weather / Temperature Strategy (Worked Example)

The leading candidate thesis. Read this in Phase 1–2 if pursuing temperature markets. It is a worked example, not a mandate — but it scores highest on the four-part edge test in `market-edge-map.md`.

## The thesis in one sentence

Kalshi's daily high/low temperature bracket markets settle on a precise physical quantity (the NWS Daily Climate Report), and NOAA publishes **calibrated probabilistic** temperature guidance that maps almost directly onto the brackets — so a disciplined probability model built from that guidance can out-price discretionary traders who eyeball raw model runs, especially in the noisier mid-probability brackets.

**Who you're beating:** other retail/discretionary weather traders without a calibrated bracket distribution. Not insiders (none exist for tomorrow's weather), not fast desks (the edge is in the estimate, not latency).

**What would falsify it:** if, on out-of-sample data, your model's bracket probabilities are no better calibrated than the market price itself (i.e. the market already reflects NBM as well as you do), there's no edge — abandon or narrow (e.g. to specific cities/regimes where you *are* better).

---

## How the markets work (mechanics you must respect)

- **Contract:** "Highest temperature in {city} on {date}" (KXHIGH* families) and daily low (KXLOW*). Each event is split into **temperature brackets** (e.g. 68–69°F), each a binary YES/NO settling at $1.00. You're pricing a **distribution over brackets** that sums to 1.
- **Settlement source — the ONLY one:** the **NWS Daily Climate Report (CLI product)** for the city's designated station, released the following morning. AccuWeather / iOS Weather / Google are *not* settlement sources — ignore them for pricing, however convenient.
- **Station mapping gotchas (these cost real traders real money):** each city settles on a specific ICAO station, and it's often *not* the one you'd guess:
  - Chicago = **Midway (KMDW)**, not O'Hare.
  - Dallas = **DFW**, not Love Field.
  - Houston = **Hobby (KHOU)**, not Bush.
  - NYC = **Central Park (KNYC)**.
  - ~20 cities total; **confirm each city's station from the individual market rules** before trading it. Build a verified city→station map and treat it as settlement-critical config.
- **Local-standard-time high window:** NWS climate reports record the daily high in **local standard time**. During Daylight Saving Time this means the "day" runs 1:00 AM to 12:59 AM the following day — **not** midnight-to-midnight. A high that lands in the wrong window settles the wrong bracket. Bake this into both settlement joins and any intraday "running high" logic.
- **Trading is 24/7** (minus maintenance), so markets are live overnight as the observation window closes.

---

## Data sources (all free, public, no API key)

You need three streams, archived point-in-time (Phase 2).

### 1. Signal — NOAA/NWS forecast guidance
- **National Blend of Models (NBM)** — NOAA's flagship post-processed blend (GFS, HRRR, RAP, GEFS, ECMWF IFS + international, combined via MOS / quantile mapping / ensemble weighting, bias-corrected against URMA). **v5.0 as of 2026-05-05.** Crucially, NBM outputs **probabilistic daytime-max (MaxT) and nighttime-min (MinT) temperature** as **percentiles and probability-of-exceedance** — this is the product that maps onto Kalshi brackets. Also emits temperature **standard deviations** for CONUS. Issued **hourly**, 1-h resolution through 36h, 3-h through 192h, 6-h to 264h (11 days).
  - Access: raw **GRIB2** and **ASCII text** via **NOMADS** (`nomads.ncep.noaa.gov`); archived on **AWS Open Data** (`registry.opendata.aws/noaa-nbm`); third-party convenience APIs exist (e.g. GribStream) if you'd rather not parse GRIB2 yourself. The NBM text product gives station-point guidance for ~9,000 locations including most CONUS airports — often the easiest path to per-station MaxT percentiles.
- **Raw ensemble/deterministic models** (optional, for your own blend): **GEFS** (Global Ensemble Forecast System — the ensemble members let you build your own probability distribution), **HRRR** (high-res, short-range, great for same-day), **GFS**, **ECMWF**. Traders reference these directly ("GFS shows 78 but HRRR has 82") — disagreement between models is itself signal about forecast uncertainty. Available via NOMADS / AWS Open Data.
- **`api.weather.gov`** — official NWS API (no key): gridpoint forecasts, and (below) station observations. Good for programmatic per-point forecast pulls without GRIB2 wrangling.

### 2. Market data — Kalshi
Prices, bid/ask, order book, volume, OI per bracket over time. Public endpoints (no auth); snapshot on a schedule and/or stream via WebSocket. See `kalshi-api.md`. Note the bid-only book. Third-party datasets (e.g. Apify KXHIGH/KXLOW actors) exist for convenience, but you can pull it directly and free.

### 3. Ground truth — settlement + live observations
- **Settlement (for backtesting/scoring):** historical **NWS CLI / Daily Climate Reports** per station-day. The **Iowa Environmental Mesonet (IEM)** archives NWS text products (including CLI) and station climatology — the practical free source for historical settled highs/lows to join against your forecasts by (station_id, date).
- **Live running observation (for same-day/intraday edge):** station observations (METAR / NWS) via `api.weather.gov/stations/{ICAO}/observations` — the running high/low at the settlement station as the window closes. As the observation window nears its end, the realized running high increasingly pins the outcome; this is where late-session mispricings appear and where the local-standard-time window matters most.

---

## Modeling approach (Phase 3)

Goal: a **calibrated probability distribution over the day's brackets** for each city, updated as new model cycles and observations arrive.

1. **Base distribution from NBM.** Convert NBM's MaxT/MinT percentile / probability-of-exceedance guidance into a probability mass for each Kalshi bracket. This is the backbone — NBM is already bias-corrected and calibrated, so start there rather than reinventing it.
2. **Ensemble spread (optional refinement).** Use GEFS member spread (or NBM's temperature standard deviation) to widen/narrow the distribution — quantify forecast uncertainty rather than assuming it. Cross-model disagreement (GFS vs HRRR vs NBM) is a useful uncertainty signal.
3. **Intraday update from observations.** As the day progresses and the running high is observed at the settlement station, collapse the distribution accordingly — brackets below the already-realized running high (for a high-temp market) go to ~0; the remaining mass concentrates. Respect the local-standard-time window when deciding whether the window is "closed."
4. **Calibration is the whole point.** A model that says 70% and is right ~70% of the time is tradeable; an overconfident one loses even with a good central estimate. Continuously check reliability (predicted vs realized frequency by bucket) and recalibrate. Prefer being well-calibrated over being sharp.
5. **Edge and sizing** then follow the general pipeline: your bracket prob vs market implied prob (net of the quadratic fee, worst near 50¢), fractional-Kelly sizing per `backtest-sizing-risk.md`.

**Where the edge concentrates:** mid-probability brackets (the market is least sure), and the time dimension — being early with a better NBM-derived distribution, or trading the late-session convergence as observations pin the outcome. The tails are efficient; don't expect edge buying near-certainties.

---

## City/regime notes

- **Liquidity varies** by city and day — concentrate on cities with tradeable volume for meaningful size; a great model on a $50 market is a hobby.
- **Summer vs winter regimes differ sharply** (variance, model skill, event frequency). Backtest and calibrate within regime; don't pool a January model with a July one.
- **Coastal/marine-influenced stations** (SF, coastal cities) have quirky micro-climate behavior — model skill and NBM calibration differ from inland continental stations.

---

## Backtest specifics for weather

- Join **archived NBM guidance (by cycle time)** ↔ **Kalshi bracket prices (point-in-time)** ↔ **IEM settled highs/lows (by station-day)**.
- Enforce that you only ever use the model cycle available at the decision timestamp (e.g. the 06Z run for a morning decision) — the most common leak here is scoring against a later run or the settled temperature.
- Apply the exact per-contract fee on entry and exit, assume you cross to the opposing bid, and model thin-book partial fills.
- Report calibration (reliability curve) alongside net P&L, split by city and by regime. A profitable-looking curve with poor calibration is overfit or leaking.
