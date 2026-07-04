# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 2-hourly status loop (trigger `2-hourly strategy status loop`).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run, updated as data accumulates.*

---

## Snapshot — 2026-07-04 10:13 UTC (run #8) — **theta diagnosed**

**Books actively trading (settled n / settled P&L / open) — Δ vs run #7:**
- **theta** — **37 settled**, **−$13.14** (−36¢/trade), 2 open. **Root cause found this run
  (read-only decomposition):**

  | band | n | model P(YES) | realized tail-hit | win% | avg win / avg loss |
  |---|---|---|---|---|---|
  | ALL | 37 | **18.7%** | **35.1%** | 65% | +$1.39 / −$3.57 |
  | yes 20–40c | 35 | 19.1% | 37.1% | — | — |

  **The spot-vol model underestimates settlement tail probability ~2×** (says 18.7%, reality
  35.1%). Negative-skew math: with a 2.6:1 loss:win ratio, break-even needs **72% win-rate**;
  theta gets 65%. The extra tail hits the model didn't price = the entire −EV. Not a code bug
  (the backtest's P3 separation was real) — a **live calibration miss**: the trailing
  return distribution is too thin-tailed for the current regime / settlement window.
- **mmsell** — **243 settled**, **+$3.30** (+1.4¢/trade), 17 open. Flat; still mildly +, still
  short of n≈300.
- **weather `con`** — 211 settled, **+$9.67**, 20 open (new morning entries; next settlement
  batch ~14:00 UTC). **weather (rest)** — 4,596 settled, −$226.07, 73 open. Unchanged.

**Data collection — ALL FRESH ✓ (last-24h rows / latest UTC):**
| collector | 24h rows | latest | status |
|---|---|---|---|
| crypto_spot_candles | 2,876 | 10:12 | ✓ fresh, 2 products |
| crypto_ladder_snapshots | 31,680 | 10:12 | ✓ fresh, 100% model-priced |
| weather_forecasts | 11,023 | 10:14 | ✓ fresh |
| weather_observations | 644 | 10:10 | ✓ fresh |
| weather_ensembles | 1,696 | 10:03 | ✓ fresh (hourly) |
| weather_bucket_snapshots | 13,194 | 10:14 | ✓ fresh |

**Headline:** theta's problem is now **diagnosed, not just observed** — the model prices tails
at ~½ their true rate, so its "overpriced tails" were actually fair/underpriced. This is a
fixable calibration miss and the data to recalibrate is accumulating. It's **paper**, so no real
money at risk — no urgency, but a clear fable task. Everything else steady; collectors all fresh.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta · DIAGNOSED — was "build the slice", now DONE] The vol model under-prices tails ~2×
   (18.7% modeled vs 35.1% realized).** That single miss explains the −EV (needs 72% win-rate,
   gets 65%). No further diagnosis needed at this n; the question is now the fix, not the cause.

2. **[theta · fable fix — well-grounded now] Widen the model's tail, then re-validate before
   trusting it.** Concrete levers (fable's call; confirm on held-out settled data that modeled
   P ≈ realized before redeploying): (a) fatten/scale the empirical return distribution (e.g.
   Student-t fit or a ×k vol multiplier) so sub-hour tails aren't understated; (b) a sigma
   floor; (c) meanwhile raise `theta_min_edge_cents` a lot (only trade huge apparent
   mispricings, which survive a 2× tail correction) or pause entries. **Recalibrate against the
   now-accumulating settled theta trades — don't hand-tune blind.**

3. **[theta · keep collecting] Do NOT stop the theta data collectors.** It's paper (no money
   lost), and every settled trade with `model_probability` vs `resolved_value` is exactly the
   labeled data needed to refit the vol model offline. Pausing *entries* is optional; keep the
   spot + ladder feeds.

4. **[mmsell · STILL VALID — inconclusive, mildly +] Watch, don't judge.** +1.4¢/243. Needs
   n≈300+ (a day or two more) before the +5.2¢ backtest is confirmed/refuted.

5. **[weather · STILL VALID, 07-03] Consider pruning confirmed-bleeder weather books**
   (−$226/4,596 vs `con` +$9.67). Judgment call, not urgent.

*(Resolved this run: the "build a theta PnL slice to diagnose" suggestion — done, read-only,
in-loop; result above. Replaced by the concrete fix path #2 + keep-collecting #3.)*
