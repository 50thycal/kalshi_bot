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
- **Follow-up (the 65-80c "pocket") — CONFIRMED A MIRAGE.** The one sub-signal that looked
  horizon-robust (+0.02..+0.05/trade) collapsed under a bigger sample + focus deep-dive
  (`--focus-band`): at n=280 it fell to +0.004/trade; 5c sub-bands alternate sign (65-70c
  = -0.259, 60-65c = +0.131 — noise, not a monotone edge); it is 100% Sports; split-half OOS
  = +0.008 / +0.001 (both ~0). Textbook small-n mirage (the 5th time — see methodology notes).
  Taker chapter is CLOSED: back-the-favorite ≈ -0.002..-0.010/trade at every horizon.

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

## *** FIRST +EV EDGE FOUND *** — Market-making / maker-SELL (`scripts/kalshi_mm.py`)

The mirror of every taker result: takers lose the spread+fee, so the resting (MAKER) side
collects it — IF adverse selection doesn't eat it. Measured assumption-light from the real
trade tape (`/markets/trades?ticker=`): for settled markets, each trade's passive counterparty
P&L held to settlement (`taker_side=yes` ⇒ maker sold yes at p ⇒ pnl=p−settle; `no` ⇒ maker
bought yes ⇒ settle−p), minus maker fee. 70,861 trades / 349 markets / 68M contracts.

**Providing liquidity is net +EV, and the structure exactly matches FLB:**
- Maker-SELL wins (+0.0067 gross), maker-BUY loses (−0.008). Selling the OVERPRICED side is
  the edge.
- **Maker-SELL yes by price (net of WORST-CASE 1c/contract fee = `net_ceil`):** 5-10c +0.050,
  10-20c **+0.125**, 20-35c **+0.222**, 35-50c +0.292, 50-65c +0.324 — then flips hard negative
  ≥65c (selling underpriced favorites = adverse selection). Robust core = **selling yes priced
  ~5-35c** (moderate overpriced longshots), +5..+22c/contract.
- **Passed every stress test:** survives worst-case per-contract fee; spread over 70-100 distinct
  markets per band (not a whale); **split-half OOS +0.187 / +0.185** (near-identical — the
  opposite of the FLB mirage); and **survives OFF sports** (non-sports 10-35c maker-sell still
  +0.14..+0.18 net_ceil, though thinner n).
- **What dies:** 0-3c penny longshots (net_ceil −0.005 — the 1c fee eats them; they dominate
  volume so the raw ALL net_ceil is −0.006, misleading); selling favorites ≥65c; maker-BUY.

**The ONE untested assumption = FILL REALISM.** The backtest assumes we're the maker on every
realized trade; live we rest ONE ask with queue competition and only capture a subset of fills
(possibly an adverse subset). Everything else checks out. Next step to validate: a PAPER
maker-sell book (rest asks on 5-35c contracts, hold to settlement) forward-tested against this
backtest's prediction — reuses the existing paper infra + strategy seam. If paper fills capture
even a fraction of +0.15c/contract, that's the first real path to the $100/mo goal.

## Maker-SELL exit study (`scripts/kalshi_mm_exits.py`) — HOLD-TO-SETTLEMENT WINS

Replayed each maker-sell fill's real post-entry yes-price path (candles) through a TP / relative-SL
/ absolute-stop grid, over a 21-day window (unbiased: entries span the uncertain regime), 3230
fills / 63 markets, entries yes 5-45c. Metric = cents/contract net of worst-case fees, with std
+ 5th-pctile tail + Sharpe.
- **Hold-to-settlement is best on BOTH mean (+8.96c) and Sharpe (+0.359).** The tail is real
  (p5 = -65c, ~8% of sold longshots hit) — the user's all-or-nothing worry is valid at the
  single-trade level.
- **But NO exit rule improves it — all make it worse:** relative SL is catastrophic (-45..-52c,
  whipsawed out by mean-reverting noise then it settles NO anyway; p5 -96 = WORSE tail);
  absolute stops (abandon at yes-ask 50-80c) equally bad (-46..-53c) — tight levels whipsaw,
  and the true tail events are GAPS with no intermediate fill a stop could catch; take-profit
  just costs mean ~1:1 (TP20 ties hold on Sharpe but lower mean).
- **Conclusion:** the edge is a carry/mean-reversion play that only pays if held through the
  noise to settlement. Exit rules can't cut the inherent tail. **Correct risk control = small
  per-position size + diversification across uncorrelated markets** (so the ~8% hits are
  swamped by the 92% wins), NOT stops. Build the paper book hold-to-settlement; keep TP/SL/abs
  as config, default OFF.

## Methodology lessons (don't repeat)
- Use REAL identifiers (Kalshi ticker strike), never title-parsed numbers — false pairs fake
  divergence.
- Precision over recall when matching across venues.
- Measure EVENT-CONDITIONAL, not averages — averages hide the news-driven moments.
- Everything gets OOS / train-test validation; treat small-n signals as mirages (we've been
  fooled ~4×: LAX favband@8 events, obs-entry, A-conviction, per-city favorite).
- Research that proves a book is −EV is a *win* — it tells us what to stop trading.
