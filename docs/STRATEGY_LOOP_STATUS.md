# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 2-hourly status loop (`kalshi_Loop_checker` skill). Suggestions
are **recommendations only** — the loop never acts on them; the user reviews and runs
fable to change anything. Newest snapshot replaces the one above it; the suggestion list
carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-04 05:14 PM CDT (run #14)

*(Short window — only ~40 min after the delayed run #13; deltas small by construction.)*

**Theta experiment:**
| book | settled | P&L | open | trend |
|---|---|---|---|---|
| theta (control) | 74 | −$6.36 | 2 | mild bleed |
| theta1 (3-20¢, 10-35m) | 4 | −$2.07 | 0 | 4th trade won (+$1.03) |
| theta2 (thr-only) | 1 | −$4.35 | 0 | idle |
| theta3 (wide, edge≥12, ×1.25) | 19 | **+$3.25** | 2 | leader; small dip, still green |

**mmsell** — 366 settled, **−$0.14** (≈0.0¢/trade), 58 open. Breakeven verdict from run
#13 unchanged.

**weather `con`** — 228 settled, +$10.82, 14 open (evening entries opening). **weather
(rest)** — 4,659 settled, −$235.77, 50 open. Unchanged.

**Data collection — ALL FRESH ✓ (last-24h rows / latest CDT):**
| collector | 24h rows | latest | status |
|---|---|---|---|
| crypto_spot_candles | 2,868 | 05:08 PM | ✓ fresh, 2 products |
| crypto_ladder_snapshots | 60,000 | 05:09 PM | ✓ fresh, 100% model-priced |
| weather_forecasts | 10,966 | 05:12 PM | ✓ fresh |
| weather_observations | 646 | 05:11 PM | ✓ fresh |
| weather_ensembles | 1,712 | 05:12 PM | ✓ fresh (hourly) |
| weather_bucket_snapshots | 13,194 | 05:11 PM | ✓ fresh |

**Headline:** quiet, healthy window. theta3 at 19/60 toward its gate (still the only green
theta book); theta1 nudged up on a win; mmsell stays breakeven. 14/14 runs all-fresh.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta · IN FLIGHT] Run the revision experiment untouched to ≥~60 settled/book.**
   theta3 19/60 (pacer, ETA ~2 days); theta1 4/60 (slow by design); theta2 1/60 —
   near-idle; if theta2 stays this sparse through the window, close its cell for
   sparsity (a finding, not a failure).

2. **[theta · correlation note] Judge each book vs its own modeled-vs-realized tail
   rate**; the four books overlap on markets — never sum them or read as replications.

3. **[mmsell · VERDICT stands] Breakeven at n=366; naive proxy doesn't carry the tape
   edge.** Fable options (unchanged): (a) restructure entries to the tape's strong cells
   (<60min-to-close and/or 10-35¢ band); (b) collect to n≈600 for a tighter CI; (c)
   deprioritize. Not supported: going live on mmsell as-is.

4. **[weather · STILL VALID] Consider pruning confirmed-bleeder weather books**
   (−$235.77/4,659 vs `con` +$10.82). Judgment call, not urgent.

*(No changes to the suggestion set this run; cadence back on schedule.)*
