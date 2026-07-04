# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 2-hourly status loop (trigger `2-hourly strategy status loop`).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run, updated as data accumulates.*

---

## Snapshot — 2026-07-04 12:13 UTC (run #9)

**Books actively trading (settled n / settled P&L / open) — Δ vs run #8:**
- **theta** — **40 settled**, **−$12.99** (−32¢/trade), 2 open. Diagnosis from run #8 stands
  (model prices tails at ~18.7% vs ~35% realized). Cumulative ticked up +$0.15 over 3 new
  trades = noise; nothing changes the read. Awaiting a fable recalibration.
- **mmsell** — **245 settled**, **+$3.86** (+1.6¢/trade), 17 open. Creeping positive; ~n=300
  (judgable) about a day out.
- **weather `con`** — **228 settled** (+17), **+$10.82** (+$1.15 on the new batch = **+6.8¢/
  trade**), 3 open. **Fresh settlement batch confirms con is +EV on new data.**
- **weather (rest pooled)** — **4,659 settled** (+63), **−$235.77** (−$9.70 on the new batch =
  **−15.4¢/trade**), 22 open. The same batch shows the rest still bleeding hard.

**Data collection — ALL FRESH ✓ (last-24h rows / latest UTC):**
| collector | 24h rows | latest | status |
|---|---|---|---|
| crypto_spot_candles | 2,876 | 12:11 | ✓ fresh, 2 products |
| crypto_ladder_snapshots | 36,240 | 12:12 | ✓ fresh, 100% model-priced |
| weather_forecasts | 10,996 | 12:13 | ✓ fresh |
| weather_observations | 646 | 12:09 | ✓ fresh |
| weather_ensembles | 1,696 | 12:04 | ✓ fresh (hourly) |
| weather_bucket_snapshots | 13,134 | 12:13 | ✓ fresh |

**Headline:** picture stable. This run's fresh weather settlements gave a clean side-by-side on
new data — **con +6.8¢/trade vs the rest −15.4¢/trade** — reinforcing both the "con is the only
+EV weather book" finding and the prune suggestion. theta unchanged (diagnosed, awaiting fix);
mmsell inching positive. All 9/9 runs: collectors fully fresh.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta · DIAGNOSED, holds at n=40] Vol model under-prices tails ~2× (18.7% vs 35.1%)** —
   the entire −EV. No new diagnosis needed; the +$0.15 uptick this run is noise.

2. **[theta · fable fix — the actionable one] Widen the model tail + re-validate before trusting.**
   Fatten/scale the empirical return distribution (Student-t or ×k vol), and/or a sigma floor;
   or raise `theta_min_edge_cents` hard / pause entries. **Recalibrate against the accumulating
   settled trades so modeled-P ≈ ~35% realized on held-out data — don't hand-tune blind.**

3. **[theta · keep collecting] Don't stop the theta feeds** — it's paper (no loss), and each
   settled trade is labeled data to refit the model offline.

4. **[mmsell · STILL VALID — inconclusive, mildly +] Watch, don't judge.** +1.6¢/245, trending
   up. About a day from n≈300 where the +5.2¢ backtest gets a real verdict.

5. **[weather · STILL VALID — reinforced] Consider pruning confirmed-bleeder weather books.**
   This run's fresh batch: con +6.8¢/trade vs rest −15.4¢/trade — the cleanest live restatement
   yet of "keep `con`, drop fav/nws/cal/dist/lows." Cuts ~−$10/settlement-batch of paper bleed
   + API load; the only cost is losing cross-validation on already-dead books. Judgment call.

*(Resolved/dropped: none. #5 reinforced by a fresh settlement batch; theta items unchanged —
diagnosis is stable, ball is in fable's court for the fix.)*
