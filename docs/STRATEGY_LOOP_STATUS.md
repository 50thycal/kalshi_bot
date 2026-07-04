# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 2-hourly status loop (trigger `2-hourly strategy status loop`).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run, updated as data accumulates.*

---

## Snapshot — 2026-07-04 14:13 UTC (run #10) — **revision round DEPLOYED**

PR #8 merged; the theta1/2/3 revision books are live as of ~14:05 UTC (theta3 already
trading). The pre-registered A/B/C/D experiment vs the untouched control is now running.

**Books actively trading (settled n / settled P&L / open) — Δ vs run #9:**
- **theta (control)** — 48 settled, **−$12.08** (−25¢/trade), 2 open. Slightly recovered
  (+$0.91 over the last 8). Unchanged config, as designed.
- **theta3** (wide band, edge≥12¢, mult 1.25) — **first 2 settled (−$1.27), 2 open.** Fires
  most often of the revisions (it shares the control's wide gates).
- **theta1 / theta2** (band 3-20¢ + tte 10-35m; theta2 thresholds-only) — no rows yet.
  **Expected**: their gates are much tighter, so they trade less often; give them a day
  before reading anything into "no entries." The loop will flag if they NEVER fire.
- **mmsell** — 247 settled, **+$4.16** (+1.7¢/trade), 17 open. Continuing its slow positive
  drift; n≈300 verdict approaching.
- **weather `con`** — 228 settled, **+$10.82**, 4 open. Steady. **weather (rest)** — 4,659
  settled, −$235.77, 28 open. Unchanged bleeders.

**Data collection — ALL FRESH ✓ (last-24h rows / latest UTC):**
| collector | 24h rows | latest | status |
|---|---|---|---|
| crypto_spot_candles | 2,876 | 14:12 | ✓ fresh, 2 products |
| crypto_ladder_snapshots | 41,280 | 14:12 | ✓ fresh, 100% model-priced |
| weather_forecasts | 10,976 | 14:12 | ✓ fresh |
| weather_observations | 645 | 14:12 | ✓ fresh |
| weather_ensembles | 1,712 | 14:06 | ✓ fresh (hourly) |
| weather_bucket_snapshots | 13,110 | 14:12 | ✓ fresh |

**Headline:** revision round is live and trading (theta3 first). Everything healthy. The
experiment clock starts now — evaluation at ≥~60 settled per revision book per the
pre-registered rule in `docs/THETA_THESIS.md`.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta · IN FLIGHT] Let the revision experiment run untouched.** Pre-registered rule:
   evaluate at ≥~60 settled per book; keep only books with positive P&L AND realized
   tail-hit ≤ modeled; all negative (incl. control) → shelve the family. **No parameter
   tweaks mid-window** — the books ARE the experiment. The loop reports per-book each run.

2. **[theta · WATCH] theta1/theta2 entry rate.** Their tight gates (3-20¢ band, 10-35m
   window; theta2 thresholds-only) mean fewer trades — that's the design. But if they have
   ~zero entries after ~24h, the 3-20¢ band may simply be too sparse at T-30 in this calm
   regime (the snapshot data showed the mid-band is thin); that would itself be a finding:
   the surgical config can't reach sample size, and the evaluation window stretches.

3. **[mmsell · STILL VALID — inconclusive, mildly +] Watch, don't judge.** +1.7¢/247 and
   drifting up; n≈300 verdict likely within ~a day.

4. **[weather · STILL VALID, 07-03] Consider pruning confirmed-bleeder weather books**
   (−$235.77/4,659 vs `con` +$10.82). Judgment call, not urgent.

*(Resolved this run: "diagnose theta" and "build the fix" — both done and DEPLOYED as the
theta1/2/3 experiment (PR #8). Replaced by #1 run-the-experiment and #2 entry-rate watch.)*
