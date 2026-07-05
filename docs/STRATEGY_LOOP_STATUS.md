# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 2-hourly status loop (`kalshi_Loop_checker` skill). Suggestions
are **recommendations only** — the loop never acts on them; the user reviews and runs
fable to change anything. Newest snapshot replaces the one above it; the suggestion list
carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-04 10:11 PM CDT (run #15)

*(Covers ~5h — two scheduled fires collapsed into one iteration after a session pause.)*

**Theta experiment — an evening cluster of tail hits swung the family:**
| book | settled | P&L | open | Δ this window |
|---|---|---|---|---|
| theta (control) | 91 | −$9.74 | 2 | −$3.38 over 17 trades |
| theta1 (3-20¢, 10-35m) | 6 | **−$0.09** | 0 | +$1.98 over 2 — best calibrated so far |
| theta2 (thr-only) | 2 | −$3.79 | 0 | 2nd trade won |
| theta3 (wide, edge≥12, ×1.25) | 30 | **−$5.04** | 0 | **flipped red**: −$8.29 over trades 20-30 |

theta3 is exactly at the halfway mark (30/60) and just gave back its lead in one bad
stretch — the same evening window hit several books at once (correlated tail exposure to
one underlying move). This is why the gate is 60, not 20: no conclusions yet, in either
direction.

**mmsell** — **445 settled (+79), +$5.85 (+1.3¢/trade)**, 27 open. Bounced back positive
from the n≈356 breakeven read. Running estimate keeps oscillating in 0..+2.5¢ — weakly
positive, CI still wide; the n≈600 checkpoint (option b) is effectively in progress.

**weather `con`** — 228 settled, +$10.82, 14 open. **weather (rest)** — 4,659, −$235.77,
50 open. No new weather settlements this window (normal for the hour).

**Data collection — ALL FRESH ✓ (last-24h rows / latest CDT):**
| collector | 24h rows | latest | status |
|---|---|---|---|
| crypto_spot_candles | 2,870 | 10:06 PM | ✓ fresh, 2 products |
| crypto_ladder_snapshots | 61,200 | 10:06 PM | ✓ fresh, 100% model-priced |
| weather_forecasts | 11,353 | 10:10 PM | ✓ fresh |
| weather_observations | 651 | 10:10 PM | ✓ fresh |
| weather_ensembles | 1,696 | 10:10 PM | ✓ fresh (hourly) |
| weather_bucket_snapshots | 13,500 | 10:09 PM | ✓ fresh |

**Headline:** an evening tail-hit cluster pushed every theta book except theta1 into the
red — theta3 gave back its lead at 30/60; theta1 sits near breakeven (n=6) as the only
book whose realized tails have roughly matched its model. mmsell re-bounced to +1.3¢/445.
All collectors fresh, 15/15 runs.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta · IN FLIGHT — hold the line] No conclusions at the halfway mark.** theta3's
   flip from +$3.98 to −$5.04 inside ~5h is exactly the run-to-run variance the ≥60 gate
   exists for. Let all four books reach the gate (theta3 ETA ~1-2 days; theta at 91 is
   past it and still negative — its verdict is effectively forming: negative and
   miscalibrated unless the next ~days reverse it).

2. **[theta · correlation note — reinforced] One spot move hits all books at once** (this
   evening's cluster). At evaluation, judge per-book calibration; also consider that
   max-per-event caps don't cap FAMILY-wide exposure to a single hour's move — a fable
   topic if multiple books graduate.

3. **[mmsell · extend to n≈600] Weakly positive, oscillating.** +1.3¢/445 after the
   breakeven read at 356. Keep collecting to ~600 before structural changes; the
   restructure option (tape's strong cells: <60min, 10-35¢) stays on the table.

4. **[weather · STILL VALID] Consider pruning confirmed-bleeder weather books**
   (−$235.77/4,659 vs `con` +$10.82). Judgment call, not urgent.

*(Updated: #1 reframed around theta3's red flip + theta control nearing a negative
verdict at n=91; #3 softened from "breakeven verdict" to "weakly positive, extend to
600" after the bounce.)*
