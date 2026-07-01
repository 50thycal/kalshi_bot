# Edge-hunt research log (cross-venue + weather)

Running log of the search for a +EV strategy toward the **$100/month** goal (see CLAUDE.md).
Live real-money trading is currently **OFF** (`KILL_SWITCH=true`, `LIVE_ENABLED=false` on
Railway) — flip back only once something is forward-validated +EV. Data collection / paper
books keep running.

## Status: no validated +EV edge yet. What's been ruled out

**Weather (directional taking)** — efficient at our resolution; every lever reverted or
inverted out-of-sample:
- Entry timing → no edge (`weather_entry_timing_study`, `_backfill`, `weather_obs_backfill_test`).
- Bucket/price calibration, highs → one OOS+window-robust edge: **LAX favorite 50–70¢** →
  built as the `favband` paper book (`weather_calibration_map`, `_validate`). Lows → nothing.
- Exits / TP / SL / 25¢ stop → hold-to-settlement is optimal for the +EV cells; stop-losses
  are poison (`weather_exit_backfill`, `weather_exit_sweep` per-book/per-city optimization).
- Per-city favorite returns are unstable (backfill vs live rankings invert = noise).

**Weather (market-making / providing liquidity)** — `weather_maker_study` (buy-at-bid upper
bound looked +1¢) but `weather_maker_fills` showed **adverse selection** kills it: filled
win% 47% vs 60% unconditional, realistic ≈ −8.6¢/trade. Dead.

**Cross-venue Kalshi↔Polymarket** — the current thread:
- `kalshi_market_survey`: weather is a backwater; liquidity is in Sports/Elections/Politics/Crypto.
- `xvenue_probe`: confirmed both public price-history APIs work from the ops runner
  (Kalshi candlesticks `period_interval=1`; Polymarket CLOB `prices-history?market=<full
  clobTokenId>&fidelity=1`). **Use the FULL clobTokenId** (don't truncate).
- `xvenue_leadlag` (World Cup winner) + `xvenue_crypto` (crypto EOY, all assets): on the
  liquid markets we can cleanly match, cross-venue divergence is **~1–1.6¢ — below our
  ~2–4¢ round-trip cost.** Efficient. (Big "divergences" in the first crypto run were a
  strike-parse bug — fixed by reading the strike from the Kalshi ticker + matching only
  MAX/MIN threshold series.)
- `xvenue_shock` (event-conditional, the RIGHT method): averaging divergence washes out the
  signal, so condition on one-minute SHOCKS and measure the other venue's follow-through.
  Result on WC-winner + crypto-EOY: **no tradeable lead-lag** — but because those markets
  don't shock on news (WC winner: 0 shocks in 4d; crypto EOY: shocks are mean-reverting
  noise, follow% < 50%). Method is validated (would detect a real lead); markets were wrong.

## NEXT STEP (pending) — in-play game-market shock test, sub-minute

The faithful test of "a goal happens → Polymarket pops → Kalshi catches up 1–2 min later"
needs **live in-play game markets** (sharp, informative repricing), which we have NOT tested.

Plan:
1. **Match live game moneylines** across venues by team-pair: Kalshi `KXWCGAME`/`KXWCROUND`
   (World Cup, if games are in the window) or a current MLB/NBA series; Polymarket per-game
   markets. Extract the two team names on each side and match.
2. **Go finer than 1-minute** — a goal pop + 1–2 min catch-up is borderline at 1-min. Both
   venues expose trade tapes: Kalshi `GET /markets/{ticker}/trades`, Polymarket trades →
   build ~10-second bars during the game window.
3. Run the `xvenue_shock` event-conditional analysis on in-game scoring events: PM→Kalshi vs
   Kalshi→PM, same-minute% (no lag = no edge), follow% and follow-size (the tradeable lag).
4. **Edge exists only if**: PM→Kalshi follow% > ~55%, follow-size clears Kalshi's ~2–4¢
   round-trip, low same-bar%, and > the reverse direction. Then it's a ONE-VENUE play (watch
   Polymarket, trade only Kalshi — no Polymarket KYC/gas/on-chain needed).

Reuse: `xvenue_shock.shock_study`, `xvenue_leadlag.align/pm_series`, `xvenue_crypto.kalshi_candles`.
All `xvenue_*` + `kalshi_market_survey` scripts are allowlisted in `scripts/ops_runner.py`.

## RULED OUT — No-arbitrage / Dutch-book scanner (`scripts/kalshi_arb.py`)

Scanned 882 open multi-outcome events for locked arbitrage (Dutch book on MECE sets:
Σ(yes_ask)<$1 or Σ(yes_bid)>$1; monotonicity on 'ge' threshold ladders), net of the
ceil(7p(1-p)) per-leg fee. **No real arb.** The 15 initial "hits" were all artifacts:
- 12 MONO-VERTICAL "arbs" were a parser bug — dropped minus signs ('Above -0.3%'→0.3) and
  ignored K/M/B units ('Above 1M'→1.0), scrambling monotone CPI/GDP/album ladders. Fixed the
  parser (signed + unit-scaled strikes) → all 12 vanished (ladders are correctly priced).
- 3 BUY-ALL-YES hits were non-fillable/non-exhaustive: Peru president (19-way, every leg quoted
  0–1¢ pre-liquidity, Σask=$0.044), Netflix top-movie (15¢ stale spreads, set not provably
  exhaustive), Fed combo (4 legs, space not provably complete). None is fillable free money.
- Decisive evidence: every *liquid* MECE set sits right at the no-arb boundary (Fear&Greed
  Σask=0.99, Trump pardons 0.98, FDA 0.98, World Cup 1.00). Kalshi's liquid markets are
  arbitraged clean. Scanner kept as a correct standing monitor (flags a real dislocation if one
  ever appears), but nothing to harvest.

## RULED OUT — Favorite-longshot bias, taker side (`scripts/kalshi_flb.py`)

Backtested settled markets (discover liquid series → pull settled history → price each at
multiple horizons before close from real candlestick yes_bid/ask → bin by price). ~900
markets collected, priced at T-30 / T-120 / T-360 min.
- **FLB is REAL on Kalshi (calibration level):** cheap longshots (0–10¢) settle YES ~0% vs
  priced 1–8¢ (overpriced) at ALL horizons; favorites mildly underpriced. Matches the
  literature.
- **NOT harvestable as a taker.** Back-the-favorite P&L/trade (net fee): **T-30 +0.036**, but
  **T-120 −0.008**, **T-360 −0.010**. The positive appears ONLY near close — the
  "already-decided favorites win" artifact (a 90¢ sports favorite at T-30 is nearly settled).
  At genuine lead times (2–6h) backing favorites LOSES ~1¢/trade. Fading longshots (buy NO) is
  tiny-edge / huge-variance / capital-heavy — the bias accrues to MAKERS, not takers.
- Meta: same verdict as every taker avenue — the edge exists but fees + variance eat it.

## Meta-conclusion after this round (arb + FLB)

We have now exhaustively tested TAKER strategies: weather directional, crypto directional,
crypto options-replication (vs Deribit), cross-venue divergence + lead-lag (vs Polymarket),
structural no-arb, and favorite-longshot fade. **All efficient / untradeable for a taker.**
The recurring theme across ALL of them: any real edge accrues to the **maker** (who earns the
spread + the longshot premium), not the taker (who pays spread + fee). The unexplored frontier
consistent with this is disciplined **market-making that manages adverse selection** — but our
one MM test (weather) lost to adverse selection, and Kalshi maker fees are charged not rebated.
Open question for the user: pursue maker/liquidity provision (harder, inventory + adverse
selection risk) or accept that a reliable automated taker edge on Kalshi isn't there.

## Methodology lessons (don't repeat)
- Use REAL identifiers (Kalshi ticker strike), never title-parsed numbers — false pairs fake
  divergence.
- Precision over recall when matching across venues.
- Measure EVENT-CONDITIONAL, not averages — averages hide the news-driven moments.
- Everything gets OOS / train-test validation; treat small-n signals as mirages (we've been
  fooled ~4×: LAX favband@8 events, obs-entry, A-conviction, per-city favorite).
- Research that proves a book is −EV is a *win* — it tells us what to stop trading.
