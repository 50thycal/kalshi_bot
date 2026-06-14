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

### The bucket-probability edge model — NOW LIVE as the `dist` book
Built and shipped (`weather_dist` / `weather_low_dist`). How it works:
- **Engine:** `kalshi_bot/weather/distribution.py` — `member_bucket_prob` (Gaussian
  kernel, sigma = forecast error beyond ensemble spread) → `model_bucket_probs`
  (blend GFS+ECMWF → per-bucket P) → `best_bucket_by_edge` (buy the single bucket
  whose model prob most beats its ask, net of fee, by ≥ `weather_dist_min_edge_cents`).
  The live counterpart of the offline grader in `scripts/weather_model_check.py`
  (math duplicated there so the ops script stays self-contained).
- **Inputs:** reads the latest stored ensemble for (city, date, kind) from
  `weather_ensembles` (collected live by `OpenMeteoEnsembleClient`); self-gates —
  no ensemble → no trade. Enters per entry-window like fav/nws/cal/pm.
- **#6 baked in:** `weather_dist_sigma` default 1.5 °F keeps the model distribution
  tighter than the market's overdispersed ladder — the edge is precisely that the
  ensemble-grounded distribution is sharper than the crowd's.
- **Config:** `weather_dist_enabled` (true), `weather_dist_sigma` (1.5),
  `weather_dist_min_edge_cents` (5). Watch its settled P&L vs `fav` in the PnL table.
- **Still crude (kept as controls):** `nws`/`cal` buy only the single point-forecast
  bucket; `dist` is the real distribution model.

---

## First real-money live test ($1, Jun 14) — buy path CONFIRMED; 2 parser bugs fixed

Ran a tiny live round-trip via the env channel (relaxed spread + a fresh entry window to
force entries on near-close low favorites). Outcome:

- **The buy path works end-to-end on real money.** 3 BUY orders placed and FILLED on Kalshi
  (DEN low @82¢, CHI @99¢, LAX @88¢, 1 contract each), with real `kalshi_order_id`s captured
  and reconciled to `filled`. The env-channel control, place_order, fill reconciliation, and
  the risk gate (it correctly blocked `SPREAD_TOO_WIDE` until relaxed) all validated live.
- **Two reconciliation parser bugs — only real API data could reveal them — found & fixed:**
  1. **Fills**: Kalshi sends `yes_price_dollars`/`no_price_dollars` (dollar strings),
     `count_fp` (fixed-point), `fee_cost` (dollars) — not `yes_price`/`count`/`fee`. Fixed
     (reuse `price_to_cents`/`_to_count`). Confirmed: DEN @82¢/qty1/$0.0104 fee.
  2. **Positions**: fields are `position_fp` (signed FP), `market_exposure_dollars`,
     `realized_pnl_dollars` — not `position`/`market_exposure`/`realized_pnl`. The old parser
     left `realized_pnl` null, **silently disabling the daily-loss circuit breaker**. Fixed.
- **Known remaining issue — SELL/exit orders rejected** (`invalid_parameters`, 400). The
  exit-order param format is wrong for Kalshi. NOT needed for the validated books (they're
  hold-to-settlement), but must be fixed before using live TP/SL/break-even exits. The sl=1
  in the test was only a contrivance to force a round trip.
- **Lesson:** the demo/shape probe caught the read shapes (orders/balance) but the fill/
  position element fields could only be confirmed with a real fill. Always do a tiny live
  buy-and-reconcile before trusting the parsers.

Aftermath: kill switch back ON, config restored (spread 5, windows 20/14/8, exit settlement);
3 tiny low-favorite positions (~$2.69) left to settle (also validates the settlement path).

### Follow-up fixes (both confirmed)
- **Settlement path CONFIRMED on real money.** Added `/portfolio/settlements` reconciliation
  (settled positions vanish from get_positions, so realized losses could escape the daily-loss
  breaker). Verified against the test settlements: CHI B64.5 **+$0.005** (bought 99¢, won),
  DEN B54.5 **−$0.8304** (bought 82¢, lost = 0 − 0.82 − 0.0104 fee) — both computed correctly
  and now feed `live_realized_pnl_today`. Settlements shape (revenue cents, *_total_cost_dollars,
  fee_cost, market_result) confirmed via the probe before parsing.
- **SELL/exit bug FIXED.** A raw `action="sell"` yes order returns Kalshi `invalid_parameters`.
  Exits now close a YES position by **BUYing the opposite (NO) side** at `100 − yes_bid` —
  reusing the proven buy path; Kalshi nets the opposing position (same P&L as selling at the
  bid). Unit-tested; verified-by-construction (identical mechanics to the working entry buy).
- Net realized cost of the whole live test ≈ **−$0.83** (CHI +0.005, DEN −0.83; LAX pending).
  Worker restored to clean baseline: BOT_MODE=weather, KILL_SWITCH=true, live fully disarmed.

### Round-trip test #2 (buy → ~1min hold → close) — mechanics CONFIRMED; exit edge case
Sped cycles to 60s, forced fresh h18 entries, sl=1 to close. Result:
- **Full round trip works end-to-end:** DEN-T69 entry **buy YES @85¢ (filled)** → ~15s hold →
  exit **buy-NO @17¢ (FILLED)**, closing the position. The buy-NO close (the fix) executes on
  real money. ✓
- **Exit reliability edge case:** 2 of 3 exits (CHI high B69.5, CHI low B58.5) were rejected
  `400 invalid_parameters` on payloads STRUCTURALLY IDENTICAL to DEN's (differ only by ticker +
  no_price) — so it's not a code bug; likely a transient market-state condition for those
  buckets at that instant. A duplicate DEN exit got `409 order_already_exists` (Kalshi's
  client_order_id idempotency caught a fast-cycle retry race — a non-issue at the normal 300s
  cadence). **Takeaway: the buy-NO close is proven but not yet 100% reliable across markets;
  harden it (retry/fallback, confirm before relying on live TP/SL) before using non-settlement
  exits.** The validated books are hold-to-settlement (no exits), which is fully proven.
- Aftermath: 2 open CHI positions (@45, @71) left to settle; baseline restored, fully disarmed.

### Exit hardening (Phase 6) — robust close, not a new method
Research confirmed buy-NO-to-close is Kalshi's *canonical* close (it nets the pair → credits $1);
the CHI rejections were validation/market-state, not a wrong method. So the fix is robustness:
- **Position snapshot is the source of truth** (reconcile runs before manage_exits each cycle):
  an exit is "done" only when Kalshi shows the position flat; otherwise re-attempt.
- **Dedup bug fixed:** `live_exit_order_exists` counted `rejected` as committed, permanently
  blocking re-attempts. Split into `live_exit_in_flight` (non-terminal only) + `count_exit_attempts`
  (ladder/cap); `open_live_positions` no longer hides a position behind a rejected exit.
- **Price-escalation ladder:** base marketable buy-NO → slippage-buffered limit on re-attempts →
  optional best-effort market order (flag-off; market fields unconfirmed) → hold to settlement.
- **409 order_already_exists → success** (it landed); unique `client_order_id` per attempt
  (`exit:{strategy}:{ticker}:{n}`) avoids self-409. **Partial fills** sized to remaining qty.
  **Full Kalshi error body + payload logged** on rejection (the hook to finally RCA the CHI 400).
  **Bounded attempts** (default 3/day) then CRITICAL + hold to settlement.
- Config: `live_exit_slippage_cents` (0), `live_exit_use_market_fallback` (false),
  `live_exit_max_attempts` (3). 11 new exit tests. Still inert until tp_sl mode + live switches on.

### Exit verification #2 — hardening WORKS; root cause = bucket-market closes rejected
A live `tp_sl` round trip confirmed the hardened mechanics: re-attempts (`exit:...:2`, `:3`),
price escalation, **409 order_already_exists treated as success**, full error body captured,
bounded attempts. But the **close still fails on weather bucket markets**, and now we know why
the error body is uninformative: Kalshi returns a GENERIC `{"code":"invalid_parameters",
"message":"invalid parameters"}` with **no field detail** — even fully logged.

**The empirical pattern across all live tests is the real RCA:** *closing* a weather range-bucket
market (`...-B##.#` tickers) via API limit order is rejected `invalid_parameters` in BOTH
directions — `action=sell/side=yes` (test #1) AND `action=buy/side=no` (tests #2/#3) — while
*buying YES to open* the same bucket works fine, and the one THRESHOLD market (`...-T##`,
DEN-T69) accepted a buy-NO close. So it's specifically *closing the mutually-exclusive bucket
markets* that Kalshi rejects; the close method/code is not the problem.

**Implication:** live early-exit (TP/SL/break-even) is NOT viable on the weather bucket markets
with current knowledge (Kalshi's error gives no specifics; docs are 403-blocked). The reliable
path for the weather books is **hold-to-settlement** (the validated strategy — needs no closes).
Open question for later: try a market order, or ask Kalshi support, for the bucket-close param.

Ops note: the Railway env API had transient read-timeouts during cleanup; the timed-out writes
still landed (verified BOT_MODE=weather, KILL_SWITCH=true). Minor follow-up: mirror_entry should
also treat 409 as success (benign — Kalshi dedups on client_order_id). Baseline fully restored.

### The close was FIXABLE — wrong format, not the market (app screenshot proved it)
A screenshot of the Kalshi app cashing out a *bucket* position via "Slide to Sell" disproved the
"bucket markets can't be closed" theory. The working **pykalshi** SDK revealed the real issue: it
sends `action="sell", side="yes"` with **`count_fp` + `yes_price_dollars`** (strings) and **no
`type`/`no_price`** fields. Our close used integer `no_price`/`yes_price` + `type:"limit"`, which
Kalshi rejected for sells (generic `invalid_parameters`). Buys tolerated the integer format (entries
filled); sells did not — hence the asymmetry.
- **Fix:** `_place_exit` now SELLS the YES position like the app does — `action="sell", side="yes",
  count_fp="N.00", yes_price_dollars="0.NN"` at the bid (escalated re-attempts sell lower to force a
  fill; market fallback = `type="market"` sell). All exit tests updated to the new shape; green.
- **Live verification blocked:** Railway's env API had repeated read-timeouts, so the live tp_sl
  test couldn't be reliably configured (writes land but the config sequence kept getting cut). The
  fix is well-grounded (the app + a working SDK use exactly this format) and unit-tested, but a clean
  live confirmation is still pending. Worker left disarmed (weather, kill switch on).

## Exit & sizing studies (live settled trades)

### Inverse view — rank by BACKFILL, check live (the trustworthy direction)
`weather_strategy_compare.py --top`. Ranking the favorite by the robust backfill sample
(n≥15/cell) and showing live alongside flips the picture from the live-first ranking:

- **The best backfill favorite cells are almost all HIGHS** (12 of the top 14; only NYC h20
  and PHIL h14 lows appear). The favorite edge the history supports lives in *highs*, not
  lows — the opposite of the live-first ranking, confirming the low-book live profit was
  small-sample luck.
- **Cross-validated winners (both samples positive, high backfill win%):** late-window (h8)
  highs in stable cities — DEN h8 (+3.8¢/68 backfill 96% · +3.7¢ live), MIA h8 (+3.6¢/100% ·
  +1.0¢), AUS h8 (+2.9¢/95% · +10.6¢), CHI h8 (+1.2¢/95% · +10.0¢) — plus LAX highs across
  windows (LAX h20 +3.3¢·+11¢, LAX h14 +3.3¢·+33.8¢) and NYC h14 (+1.5¢·+20.5¢).
- **Sign agreement: 11 of the top 14 backfill cells are also positive live.** The 3
  disagreements are all *early-window* continental highs (DEN h20, CHI h20, DEN h14: backfill
  + but live − on n=4) — early entry is less reliable (the high hasn't formed), and the *late*
  (h8) versions of the same cities cross-validate positive. Window matters: late > early for highs.

**Go-live implication (validated from both directions):** the defensible first live book is
**late-window (h8) high favorites in stable cities (DEN/MIA/AUS/CHI/LAX)** — high historical
win rates (95-100%) AND positive live — not the low favorite. Live per-cell n is still tiny
(1-5), so treat magnitudes as noisy; the SIGN agreement is what matters.

### Backfill vs live cross-validation — the low-book edge does NOT survive (caution!)
`weather_strategy_compare.py` replays the favorite three ways: backfill (Kalshi REST
history, n~350-480/cell), live realized paper P&L (n~17-28/cell), and best TP/SL on the
live trades. **The low books contradict between samples:**

| kind/window | backfill (large n) | live (small n) |
|---|---|---|
| low h20 | **−6.4¢ (468)** | +12.7¢ (19, 89%) |
| low h14 | −3.7¢ (346) | +5.7¢ (18) |
| low h8 | −6.6¢ (354) | +2.2¢ (17) |
| high h20 | +1.3¢ (481) | −4.2¢ (28) |
| high h8 | −0.9¢ (230) | +6.1¢ (18, 100%) |

The robust 25×-larger backfill says the **low favorite LOSES ~4-7¢**; the tiny recent live
sample says it's our best winner (+12.7¢). This is the small-sample/recent-regime trap in
action — the live "low fav h20 = best strategy" headline does **not** cross-validate. Trust
the backfill: do NOT seed live trading with the low favorite on the strength of the live
numbers alone.

**The only sign-consistent (both samples agree) positive signal is highs in stable
marine/subtropical cities:** high LAX backfill +3.2¢ / live +22.4¢; high MIA backfill +1.0¢
/ live +18.1¢. Everywhere else the two samples disagree (low cities: backfill all negative,
live all positive; DEN high +4.6¢ backfill but −2.6¢ live). TP/SL barely moves anything —
hold is best in 5 of 6 window cells (only high h20 benefits: tp=20 → +2.9¢), echoing the
exit-sweep finding. **Implication for go-live: the cross-validated candidate is high
LAX/MIA, not low fav.** Gather more live low-book settlements before trusting that edge.



### Best-strategy breakdown (live settled, by city × window × book)
`weather_pnl.py --best`. Robust picks (n ≥ 15, ranked by P&L/trade):
**low fav h20 = +12.7¢ (n19, 89%)** — the standout; then low nws/cal h14 +7.7¢ (n15),
high fav h8 +6.1¢ (n18, 100%), low fav h14 +5.7¢ (n18). Finer city cells are all n≈4 —
noise, not picks.

**City pattern worth noting (n 7–9):** the HIGH books are net negative *pooled*
(fav −2.2¢, nws −1.3¢) but strongly positive in **stable-climate cities** — high nws
MIA +33¢ (n9, 100%), cal MIA +28.6¢, cal CHI +25.9¢, fav LAX +22.4¢. Miami/LA highs are
low-variance (subtropical / marine layer) so forecasts nail them; the high-book edge is
**city-dependent**, echoing the #3 city×window result. Candidate: gate the high books to
stable cities (MIA/LAX/CHI), drop the volatile ones — needs more n before acting.

### Exit dynamics on the profitable (low) books — HOLD-TO-SETTLEMENT WINS
Replayed 158 settled low-book trades (`low_fav/nws/cal/pm` + `high_pm`) through their
recorded 15-min bid paths under a TP/SL grid, a **break-even stop** (arm at +gain,
then exit at entry if it falls back), and a **size/fee** sweep. `weather_exit_sweep.py`.

- **Hold beats every exit rule:** hold = **+5.3¢/trade**; the best TP-only is +3.6¢
  (−1.6¢ vs hold), and every stop-loss is brutal (hold/5¢SL = −10.9¢, −16¢ vs hold).
  Any SL gets whipsawed out of trades that dip intraday but settle YES.
- **Break-even also loses:** best BE config (arm 12¢ / TP 15¢) = +2.4¢, still −2.9¢
  vs hold; BE-only −0.9 to +0.5¢. Same reason — it exits near-certain winners on a
  transient dip back to entry.
- **Why:** the low-book edge is *outcome certainty* (thermometer lag — the overnight
  low is already locked, the bucket settles YES ~93%), not price momentum. Exiting
  early on a price wiggle only forfeits the settlement payout. **Verdict: keep the
  weather books hold-to-settlement; do not add TP/SL/BE to them.**
- **Size/fee:** per-contract P&L is +5.29¢ (qty 1) → +5.78¢ (qty 5) → +5.87¢ (qty
  100). Kalshi fees scale with qty, so size only amortizes the `ceil` rounding —
  a one-time ~+0.5¢ from qty 1→5, then flat. The real fee lever is *price* (fee =
  0.07·P·(1−P), tiny near 0/100), which the low favorite already enjoys. Trading ≥5
  contracts is a free ~0.5¢, but size is NOT the cost fix; holding + high-price entries are.

## Backfill structural-edge hunt (Apr–Jun history, 964 complete events)

**Scorecard (all 9 probes complete):** #3 city×window VALIDATED (→ live `cwin`);
#6 distribution overdispersion REAL (→ model calibration feature, not a book);
#2b mean-reversion REAL but sub-fee (→ model feature). Everything else priced or
dead — #1 overround, #2 persistence, #4 longshot, #5 momentum/convergence, #7 W→E
lead-lag (real physics, priced), #8 diurnal coupling (real physics, priced), #9
liquidity (mid honest even when thin). **Conclusion: Kalshi temperature markets
are remarkably efficient on everything derivable from price/public data — the only
positive EV is timing (`cwin`) and last-mile information lag (`obs`, `pm`), plus
sharpening the forecast model with #6.** Next build: promote the offline
distribution model into a live book (see "Build backlog" above).

### #9 — Liquidity-conditioned mispricing — REFUTED (mid is honest even when thin)
Is mispricing concentrated in illiquid buckets? At h12, grade each bucket's mid
vs its realized win-rate, binned by bid/ask spread and by candle volume.

- **The mid is well-calibrated in EVERY liquidity bin.** By spread: ≤2¢ → mid_err
  −0.7, 3–5¢ → +1.6, 6–10¢ → −1.2, >10¢ → −0.8 (even the widest-spread buckets win
  42% at a 42.9¢ mid). By volume: 0 → −0.4, 1–50 → +0.4, 51–500 → −1.2, >500 → −0.1
  (even zero-volume deep longshots, avg mid 4.6¢, win 4% — perfectly priced). Thin
  books do **not** create stale/sloppy mids.
- **Illiquidity is pure cost, not edge.** yes/no EV degrades monotonically as the
  spread widens: YES −2.4¢ (≤2¢ spread) → −11.3¢ (>10¢ spread); NO likewise. The
  spread is wide *because* there's no edge, and crossing it bleeds you. Thin
  buckets are the most expensive to touch, not the most profitable.

**Verdict:** the "sloppy pricing in thin markets" hypothesis fails outright —
Kalshi's resting quotes are honest even in the corners (zero-volume tails, >10¢
spreads) where sloppiness is most expected. Probe: `--analysis liquidity`.

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
