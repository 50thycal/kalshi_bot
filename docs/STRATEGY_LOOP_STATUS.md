# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 2-hourly status loop (`kalshi_Loop_checker` skill). Suggestions
are **recommendations only** — the loop never acts on them; the user reviews and runs
fable to change anything. Newest snapshot replaces the one above it; the suggestion list
carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-04 04:35 PM CDT (run #13)

**Theta experiment:**
| book | settled | P&L | open | trend |
|---|---|---|---|---|
| theta (control) | 71 | −$5.77 | 3 | mild bleed |
| theta1 (3-20¢, 10-35m) | 3 | −$3.10 | 0 | idle since 12:34 PM |
| theta2 (thr-only) | 1 | −$4.35 | 0 | idle |
| theta3 (wide, edge≥12, ×1.25) | 16 | **+$3.98** | 3 | leader, still green |

**mmsell — VERDICT AT n≈300 REACHED: breakeven, not validated.** 356 settled (+75 this
window, weekend sports batch), **−$0.15 total ≈ 0.0¢/trade**. The pre-registered check
was "≥+2¢/trade at n≈300 → forward-validated"; it landed at ~0. The paper proxy (enter
at the ask whenever the mid is in 5-40¢) does NOT capture the tape's +5.2¢ maker-sell
edge — consistent with the tape edge being *fill-selective* (makers get filled at good
moments; a naive always-enter proxy doesn't).

**weather `con`** — 228 settled, +$10.82, 13 open. Steady. **weather (rest)** — 4,659
settled, −$235.77, 49 open. Unchanged bleeders.

**Data collection — ALL FRESH ✓ (last-24h rows / latest CDT):**
| collector | 24h rows | latest | status |
|---|---|---|---|
| crypto_spot_candles | 2,876 | 04:33 PM | ✓ fresh, 2 products |
| crypto_ladder_snapshots | 60,000 | 04:33 PM | ✓ fresh, 100% model-priced |
| weather_forecasts | 10,966 | 04:34 PM | ✓ fresh |
| weather_observations | 647 | 04:34 PM | ✓ fresh |
| weather_ensembles | 1,720 | 04:24 PM | ✓ fresh (hourly) |
| weather_bucket_snapshots | 13,188 | 04:34 PM | ✓ fresh |

**Headline:** mmsell's n≈300 verdict is in — breakeven, the tape edge doesn't survive the
naive paper proxy. theta3 keeps leading (+$3.98/16, ~44 to its gate); theta1/2 idle since
midday (their tight cells fire rarely). All collectors fresh, 13/13 runs.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta · IN FLIGHT] Run the revision experiment untouched to ≥~60 settled/book.**
   theta3 is the pacer (16/60, ETA ~2-3 days at current rate); theta1 (3) and theta2 (1)
   accumulate very slowly — if they stay near-idle through the gate window, close their
   cells for sparsity (that's a finding, not a failure).

2. **[theta · correlation note] Judge each book vs its own modeled-vs-realized tail rate**;
   the four books overlap on markets — never sum them or read them as replications.

3. **[mmsell · VERDICT — reframed] Breakeven at n=356; the naive proxy doesn't carry the
   tape edge.** Options for a fable session, in rough order of value: (a) apply the tape's
   OWN structure to the book — restrict entries to <60min-to-close and/or the 10-35¢ band
   (the tape's strongest cells) instead of any-mid-5-40¢-any-horizon; (b) keep collecting
   to n≈600 for a tighter CI before changing anything; (c) deprioritize mmsell and let
   theta3/weather-con carry the goal. The one thing NOT supported: going live on mmsell
   as-is.

4. **[weather · STILL VALID] Consider pruning confirmed-bleeder weather books**
   (−$235.77/4,659 vs `con` +$10.82). Judgment call, not urgent.

*(Updated this run: #3 flipped from "verdict imminent" to the actual verdict + options.
Run #13 fired late — ~4:35 PM CDT vs the 3:13 PM schedule — due to a transient tool
outage at fire time; cadence resumes normally next run.)*
