# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 2-hourly status loop (trigger `2-hourly strategy status loop`).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run, updated as data accumulates.*

---

## Snapshot — 2026-07-04 04:13 UTC (run #5)

**Books actively trading (settled n / settled P&L / open) — Δ vs run #4:**
- **mmsell** — **218 settled** (was 155), **+$0.97** (was −$4.32) = **+0.4¢/trade**, 26 open.
  Oscillating around breakeven (−1.8 → +2.0 → −2.8 → +0.4 ¢/trade across runs). Net ≈ flat;
  nearing a judgable sample (n≈300+ target).
- **theta** — **19 settled** (was 13), **−$9.17** (−48¢/trade), 4 open. **Negative every run,
  monotonically** (−0.79 → −3.68 → −6.96 → −9.17). Implied tail-hit ≈ **29% vs ~20% priced**
  — a persistent adverse lean (~1 SD above priced at n=19, not yet significant). **Approaching
  the n≈30–50 decision point (~1 more run).**
- **weather `con`** — 211 settled, **+$9.67**, 17 open. Flat (next weather settlement batch
  ~14:00 UTC); collectors underneath are live.
- **weather (rest pooled)** — 4,596 settled, **−$226.07**, 63 open. Unchanged bleeders.

**Data collection — ALL FRESH ✓ (last-24h rows / latest UTC):**
| collector | 24h rows | latest | status |
|---|---|---|---|
| crypto_spot_candles | 2,878 | 04:12 | ✓ fresh, 2 products (BTC+ETH) |
| crypto_ladder_snapshots | 17,040 | 04:13 | ✓ fresh, **100% model-priced** |
| weather_forecasts | 10,922 | 04:13 | ✓ fresh |
| weather_observations | 654 | 04:13 | ✓ fresh |
| weather_ensembles | 1,720 | 04:11 | ✓ fresh (hourly cadence) |
| weather_bucket_snapshots | 13,182 | 04:13 | ✓ fresh |

**Headline:** collectors all fresh. **theta's negative lean is now persistent (5/5 runs, tails
~29% vs 20% priced)** and one run from the n≈30–50 decision point — the leading hypothesis is
the spot-vol model *underestimating live tail probability*. mmsell remains a breakeven coin-flip
at n=218. Still too early to act on either; the next 1–2 runs are the ones that matter for theta.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta · STILL VALID, 07-03 — watch closely, ~1 run to decision] Hold to n≈30–50, then
   judge.** 5/5 runs negative, tails hitting ~29% vs ~20% priced. If this holds at n≈30–50 the
   prime suspect (for fable) is the **vol model underestimating settlement tail risk** — e.g.
   Coinbase 1-min realized vol < BRRNY settlement vol, or the trailing window lagging a vol
   expansion. Concrete first check: on settled theta trades, compare model P(YES) to realized
   hit-rate by band. Hold `THETA_*` config until then.

2. **[theta · STILL VALID — becomes actionable next run] Build the theta PnL slice at ~30+**
   (win-rate vs tail-loss, band × time-to-expiry, model-P vs realized). This is now the pivotal
   read and theta is ~1 run from n=30. Highest-value next build.

3. **[theta · STILL VALID] Velocity fine** — 13→19 in 2h (~3/hr), 4 open. No action.

4. **[mmsell · STILL VALID — inconclusive] Watch, don't judge.** Oscillating ±3¢ around
   breakeven (now +0.4¢/218). Needs n≈300+ to confirm/refute the +5.2¢ backtest. No action.

5. **[weather · STILL VALID, 07-03] Consider pruning confirmed-bleeder weather books**
   (−$226/4,596 vs `con` +$9.67). Judgment call, not urgent.

6. **[infra · STILL VALID, 07-03] Highest-value next build is theta analysis tooling** (#2).

*(Resolved/dropped: none. #1/#2 sharpened — theta's persistent lean gives a concrete model
hypothesis + a decision point one run out.)*
