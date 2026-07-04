# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 2-hourly status loop (trigger `2-hourly strategy status loop`).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run, updated as data accumulates.*

---

## Snapshot — 2026-07-04 18:13 UTC (run #12)

**Theta experiment — all four books now trading:**
| book | settled | P&L | Δ this run |
|---|---|---|---|
| theta (control) | 63 | −$5.22 | ~flat window |
| theta1 (3-20¢, 10-35m) | 3 | −$3.10 | 3rd trade was a tail hit |
| theta2 (thr-only) | 1 | −$4.35 | **first trade** — a tail hit |
| theta3 (wide, edge≥12, ×1.25) | 11 | **+$3.00** | best so far |

Correlation note: theta1's and theta2's 17:34 losses look like the **same market** — the
revision books overlap by design on threshold tails ≤20¢, so their samples are NOT
independent; judge each vs its own modeled tail-hit rate at the ≥60 gate, don't sum them.

**Other books:**
- **mmsell** — **281 settled, +$6.89 (+2.5¢/trade), 50 open** (entries surged — weekend
  sports volume). n≈300 verdict likely next run; per-trade has climbed 4 runs straight
  (+0.4 → +1.2 → +1.7 → +2.0 → +2.5¢).
- **weather `con`** — 228 settled, +$10.82, 7 open. Steady. **weather (rest)** — 4,659
  settled, −$235.77, 48 open. Unchanged.

**Data collection — ALL FRESH ✓ (last-24h rows / latest UTC):**
| collector | 24h rows | latest | status |
|---|---|---|---|
| crypto_spot_candles | 2,870 | 18:09 | ✓ fresh, 2 products |
| crypto_ladder_snapshots | 51,120 | 18:09 | ✓ fresh, 100% model-priced |
| weather_forecasts | 10,934 | 18:13 | ✓ fresh |
| weather_observations | 644 | 18:09 | ✓ fresh |
| weather_ensembles | 1,712 | 18:09 | ✓ fresh (hourly) |
| weather_bucket_snapshots | 13,110 | 18:13 | ✓ fresh |

**Headline:** experiment fully populated (theta2's rare cell finally traded); theta3 leads
at +$3.00/11; mmsell's climb continues into its verdict window. All fresh, 12/12.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta · IN FLIGHT] Run untouched to ≥~60 settled per revision book.** theta3 will get
   there first (~1-2 days); theta1/theta2 accumulate slowly (theta2 slowest — its first and
   only trade was a tail hit; at n=1 that is pure noise). Keep-if-positive-AND-calibrated.

2. **[theta · NEW note] Remember the books are correlated** — theta1/2/3 often sell the same
   markets as each other and the control. At evaluation, judge each book against its OWN
   modeled-vs-realized tail rate and per-trade P&L; don't treat the four as independent
   replications, and don't sum their P&L as if diversified.

3. **[mmsell · verdict imminent — strengthening] +2.5¢/281, per-trade rising 4 runs
   straight.** At n≈300: if ≥+2¢ holds, mmsell forward-validates as the first durable +EV
   book beyond weather-con → sizing/live-test discussion becomes worthwhile (fable).

4. **[weather · STILL VALID] Consider pruning confirmed-bleeder weather books**
   (−$235.77/4,659 vs `con` +$10.82). Judgment call, not urgent.

*(Dropped: "theta2 entry-rate watch" — resolved, it fired. Added #2 correlation note.)*
