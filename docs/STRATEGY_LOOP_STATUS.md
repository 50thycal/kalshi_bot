# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 2-hourly status loop (trigger `2-hourly strategy status loop`).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run, updated as data accumulates.*

---

## Snapshot — 2026-07-04 06:13 UTC (run #6)

**Books actively trading (settled n / settled P&L / open) — Δ vs run #5:**
- **mmsell** — **241 settled** (was 218), **+$2.99** (was +$0.97) = **+1.2¢/trade**, 13 open.
  Drifting slightly positive across the swings (−1.8 → +2.0 → −2.8 → +0.4 → +1.2 ¢/trade).
  Still short of the n≈300 needed to call it; leaning mildly encouraging.
- **theta** — **26 settled** (was 19), **−$9.75** (−37¢/trade), 2 open. **The important nuance:
  the last 7 trades added only −$0.58 (≈ −8¢/trade)** vs −37…−46¢/trade in earlier runs — so
  the deep cumulative loss is **front-loaded from a few early tail hits**, and recent trades are
  near breakeven. Tail-hit ≈ 27% vs ~20% priced. n=26 ≈ the decision zone.
- **weather `con`** — 211 settled, **+$9.67**, 17 open. Flat (pre next ~14:00 UTC settlement).
- **weather (rest pooled)** — 4,596 settled, **−$226.07**, 63 open. Unchanged bleeders.

**Data collection — ALL FRESH ✓ (last-24h rows / latest UTC):**
| collector | 24h rows | latest | status |
|---|---|---|---|
| crypto_spot_candles | 2,876 | 06:12 | ✓ fresh, 2 products (BTC+ETH) |
| crypto_ladder_snapshots | 22,320 | 06:12 | ✓ fresh, **100% model-priced** |
| weather_forecasts | 11,081 | 06:13 | ✓ fresh |
| weather_observations | 652 | 06:13 | ✓ fresh |
| weather_ensembles | 1,720 | 06:13 | ✓ fresh (hourly cadence) |
| weather_bucket_snapshots | 13,254 | 06:13 | ✓ fresh |

**Headline:** collectors all fresh. **theta's read softened** — cumulative −$9.75 is dominated
by early tail hits; the last 7 trades were ~breakeven, which is exactly why cumulative P&L
misleads on a negative-skew book and the **win-rate-vs-tail-loss decomposition is now the
deciding read** (theta is at n=26). mmsell mildly positive at n=241. Still no action warranted.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta · STILL VALID — at the decision zone] Judge by decomposition, not cumulative P&L.**
   n=26, cumulative −$9.75 but **recent 7 ≈ breakeven** → the loss is front-loaded early
   variance, not (yet) a persistent bleed. Don't read the −$ headline literally on a
   negative-skew book. Hold `THETA_*` config.

2. **[theta · NOW ACTIONABLE] Build the theta PnL slice (this is the pivotal read).** At n≈26–30:
   win-rate vs average tail-loss, model-P(YES) vs realized hit-rate by price band × time-to-
   expiry. That decomposition — not the cumulative total — tells whether the +4.4¢ backtest
   edge is showing up. If model-P is systematically *below* realized hit-rate, the fix (for
   fable) is the vol model underestimating settlement tail risk (Coinbase 1-min vol vs BRRNY).

3. **[theta · STILL VALID] Velocity fine** — ~3.5 settling/hr, 2 open. No action.

4. **[mmsell · STILL VALID — inconclusive, mildly +] Watch, don't judge.** +1.2¢/241, drifting
   slightly positive through the swings. Needs n≈300+ to confirm/refute +5.2¢ backtest.

5. **[weather · STILL VALID, 07-03] Consider pruning confirmed-bleeder weather books**
   (−$226/4,596 vs `con` +$9.67). Judgment call, not urgent.

6. **[infra · STILL VALID, 07-03] Highest-value next build is the theta PnL slice** (#2) — now
   the single most useful thing to build; theta has crossed into judgable-n territory.

*(Resolved/dropped: none. #1/#2 updated — recent theta trades near breakeven reframe the loss
as front-loaded; the decomposition is now the decisive, buildable read.)*
