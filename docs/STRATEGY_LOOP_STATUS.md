# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 2-hourly status loop (trigger `2-hourly strategy status loop`).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run, updated as data accumulates.*

---

## Snapshot — 2026-07-04 02:13 UTC (run #4)

**Books actively trading (settled n / settled P&L / open) — Δ vs run #3:**
- **mmsell** — **155 settled** (was 104), **−$4.32** (was +$2.03) = **−2.8¢/trade**, 57 open.
  **Swung back negative** — last run's +$2.03 was itself small-sample noise. Net read: mmsell
  is hovering slightly negative and **high-variance run-to-run**; no conclusion at this n.
- **theta** — **13 settled** (was 8), **−$6.96** (−53¢/trade), 2 open. Negative every run so
  far (−0.79 @3 → −3.68 @8 → −6.96 @13). Back-of-envelope: ~4 of 13 tails hit ≈ **31% vs
  ~20% priced** — leaning unlucky/adverse, but still inside noise for a negative-skew book at
  n=13. **Decision point is n≈30–50, not now.**
- **weather `con`** — 211 settled, **+$9.67**, 17 open. Flat since run #2 — weather settles on
  a ~daily clock (overnight ~14:00 UTC batch), so con/other won't move between most runs. Not
  stale; just no new weather settlements this window.
- **weather (rest pooled)** — 4,596 settled, **−$226.07**, 63 open. Unchanged bleeders.

**Data collection — ALL FRESH ✓ (last-24h rows / latest UTC):**
| collector | 24h rows | latest | status |
|---|---|---|---|
| crypto_spot_candles | 2,872 | 02:10 | ✓ fresh, 2 products (BTC+ETH) |
| crypto_ladder_snapshots | 11,760 | 02:10 | ✓ fresh, **100% model-priced** |
| weather_forecasts | 11,046 | 02:13 | ✓ fresh |
| weather_observations | 651 | 02:11 | ✓ fresh |
| weather_ensembles | 1,728 | 02:10 | ✓ fresh (hourly cadence) |
| weather_bucket_snapshots | 13,134 | 02:13 | ✓ fresh |

**Headline:** collectors all fresh; **both maker books (theta, mmsell) are negative at small n
and swinging between runs** — exactly the discipline case for *not* acting yet. theta −$6.96/13,
mmsell −$4.32/155. Wait for real n before reading either P&L.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta · STILL VALID, 07-03 — watch closely] Let theta accumulate; decision point n≈30–50.**
   Negative every run (−0.79→−3.68→−6.96) with tails hitting ~31% vs ~20% priced at n=13 —
   leaning adverse but not yet conclusive. Hold `THETA_*` config; if it's still clearly
   negative at n≈30–50, the likely culprits (for fable, later) are fill realism (paper assumes
   our ask fills) or spot-vs-BRRNY model basis, not the code.

2. **[theta · STILL VALID, 07-03 — now the key deliverable] Build a theta PnL slice at ~30+**
   that **decomposes win-rate vs average tail-loss** (band × time-to-expiry; model-overpriced
   cohort win% vs priced-in prob). Given the negative trend, this is the read that will settle
   "broken edge vs variance." Highest-value next build.

3. **[theta · STILL VALID] Watch sample-build velocity.** ~2.5 settling/hr (8→13 in 2h), 2
   open — on track for n≈30–50 in ~1 more day. No action.

4. **[mmsell · STILL VALID, 07-03 — downgraded to inconclusive] Watch, don't judge.** Went
   −1.8¢ (65) → +2.0¢ (104) → −2.8¢ (155): pure small-sample swing. Needs n≈300+ before the
   +5.2¢ backtest is confirmed or refuted. No action.

5. **[weather · STILL VALID, 07-03] Consider pruning confirmed-bleeder weather books**
   (−$226/4,596 vs `con` +$9.67). Judgment call, not urgent.

6. **[infra · STILL VALID, 07-03] Highest-value next build is theta analysis tooling** (see #2).

*(Resolved/dropped: none. #4 downgraded — mmsell's positive run was noise; #1 elevated to
"watch closely" with an explicit n≈30–50 decision point.)*
