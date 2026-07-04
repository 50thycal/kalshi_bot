# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 2-hourly status loop (trigger `2-hourly strategy status loop`).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run, updated as data accumulates.*

---

## Snapshot — 2026-07-04 16:13 UTC (run #11)

**Theta experiment (control + revisions, deployed ~14:05 UTC):**
| book | settled | P&L | open | Δ this run |
|---|---|---|---|---|
| theta (control) | 56 | **−$4.99** | 1 | **+$7.09 over last 8** — sharp recovery |
| theta1 (3-20¢, 10-35m) | 2 | **+$1.25** | 0 | first trades, both won |
| theta2 (theta1 + thr-only) | 0 | — | 0 | not fired yet (rarest gates) |
| theta3 (wide, edge≥12, ×1.25) | 7 | **+$2.01** | 0 | positive start |

All theta books positive THIS window — including the control, whose recent recovery
(−$12.08 → −$4.99) says the early bleed had a heavy bad-luck/regime component on top of the
diagnosed model miss. All revision n's are tiny; the ≥~60-settled evaluation gate stands.

**Other books:**
- **mmsell** — 256 settled, **+$5.21** (+2.0¢/trade), 19 open. Steady climb; ~n=300 verdict
  imminent. Trending toward (though below) the +5.2¢ backtest.
- **weather `con`** — 228 settled, **+$10.82**, 7 open. Steady.
- **weather (rest)** — 4,659 settled, −$235.77, 43 open. Unchanged bleeders.

**Data collection — ALL FRESH ✓ (last-24h rows / latest UTC):**
| collector | 24h rows | latest | status |
|---|---|---|---|
| crypto_spot_candles | 2,870 | 16:08 | ✓ fresh, 2 products |
| crypto_ladder_snapshots | 45,840 | 16:08 | ✓ fresh, 100% model-priced |
| weather_forecasts | 10,974 | 16:13 | ✓ fresh |
| weather_observations | 644 | 16:11 | ✓ fresh |
| weather_ensembles | 1,712 | 16:07 | ✓ fresh (hourly) |
| weather_bucket_snapshots | 13,086 | 16:13 | ✓ fresh |

**Headline:** revision experiment fully alive (theta1 + theta3 both trading and green so
far; theta2 awaiting its rarer setup). Control bounced hard. Everything fresh, 11/11 runs.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta · IN FLIGHT] Let the experiment run untouched to ≥~60 settled per revision
   book** (theta3 pace ≈ 1-2 days; theta1 slower; theta2 slowest). Keep-if-positive-AND-
   calibrated rule as pre-registered. The control's bounce is a reminder that 2-hour windows
   swing hard — only the gate decides.

2. **[theta · WATCH, softened] theta2 entry rate.** theta1 firing confirms the tight band
   isn't empty; theta2's extra thresholds-only filter makes it the rarest. If still zero
   after another ~24h, that's the finding: threshold tails ≤20¢ with ≥5¢ model edge are
   near-nonexistent in this regime, and theta2's cell is effectively untradeable (evaluation
   would then close it for sparsity, not P&L).

3. **[mmsell · verdict imminent] n≈300 within ~a day.** +2.0¢/256 and climbing. At the
   verdict: if ≥ +2¢ holds, mmsell forward-validates as the first durable +EV book beyond
   weather-con — sizing/live questions become relevant (fable discussion, not loop action).

4. **[weather · STILL VALID] Consider pruning confirmed-bleeder weather books**
   (−$235.77/4,659 vs `con` +$10.82). Judgment call, not urgent.

*(No items resolved/dropped this run; #2 softened after theta1 fired, #3 upgraded to
"verdict imminent.")*
