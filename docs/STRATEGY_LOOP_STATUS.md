# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 2-hourly status loop (trigger `2-hourly strategy status loop`).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run, updated as data accumulates.*

---

## Snapshot — 2026-07-04 08:13 UTC (run #7)

**Books actively trading (settled n / settled P&L / open) — Δ vs run #6:**
- **theta** — **32 settled** (was 26), **−$10.83** (−34¢/trade), 2 open. **Crossed the n≈30
  decision zone.** Verdict emerging: **leaning negative, opposite the +4.4¢ backtest.** Tails
  have hit ~26–27% vs ~20% priced in *every* run — a small but consistent adverse gap (still
  <1 SD at n=32, so "lean," not "proven"). Recent 6 trades −$1.08 (−18¢/trade) — moderated
  from the early deep hits but still red.
- **mmsell** — **242 settled** (was 241), **+$3.19** = **+1.3¢/trade**, 15 open. Barely moved
  overnight (few markets settle at night). Still mildly positive, still short of n≈300.
- **weather `con`** — 211 settled, **+$9.67**, 17 open. Flat (next settlement batch ~14:00 UTC,
  ~6h out). Collectors underneath live.
- **weather (rest pooled)** — 4,596 settled, **−$226.07**, 63 open. Unchanged bleeders.

**Data collection — ALL FRESH ✓ (last-24h rows / latest UTC):**
| collector | 24h rows | latest | status |
|---|---|---|---|
| crypto_spot_candles | 2,870 | 08:09 | ✓ fresh, 2 products (BTC+ETH) |
| crypto_ladder_snapshots | 26,880 | 08:09 | ✓ fresh, **100% model-priced** |
| weather_forecasts | 11,049 | 08:13 | ✓ fresh |
| weather_observations | 650 | 08:11 | ✓ fresh |
| weather_ensembles | 1,696 | 08:02 | ✓ fresh (hourly cadence) |
| weather_bucket_snapshots | 13,206 | 08:11 | ✓ fresh |

**Headline:** collectors all fresh. **theta reached judgable n (32) and is leaning negative —
the opposite of its backtest** — with a consistent tail-hit gap (~26% realized vs ~20% priced)
that points at the vol model **underestimating settlement tail probability**. This is the run
where the diagnostic PnL slice (suggestion #1/#2) becomes the concrete, worth-doing next action
for fable. Still not touching config — diagnose before changing anything.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta · ELEVATED — diagnose now] Build the theta PnL slice; the edge is inverted at n=32.**
   Live −34¢/trade vs +4.4¢ backtest, tails ~26% vs ~20% priced every run. The single concrete
   next action: slice settled theta trades by price band × time-to-expiry and compare **model
   P(YES) to realized hit-rate**. Expectation from the pattern: model P sits *below* realized —
   i.e. the spot-vol model under-prices tail probability. Read-only; no config change.

2. **[theta · fable fix candidate, AFTER #1 confirms] If model underestimates tails, widen the
   distribution.** Likely levers (do not apply blind — confirm with #1 first): the 5-day
   overlapping-return window under-captures sub-hour fat tails; Coinbase 1-min vol may run below
   BRRNY settlement vol. Candidate fixes: a fatter-tailed / longer-window return model, a sigma
   floor, or a higher `theta_min_edge_cents` so only larger mispricings trade. Fable's call.

3. **[theta · STILL VALID] Velocity fine** — ~3 settling/hr, 2 open. No action.

4. **[mmsell · STILL VALID — inconclusive, mildly +] Watch, don't judge.** +1.3¢/242, flat
   overnight. Needs n≈300+ to confirm/refute the +5.2¢ backtest.

5. **[weather · STILL VALID, 07-03] Consider pruning confirmed-bleeder weather books**
   (−$226/4,596 vs `con` +$9.67). Judgment call, not urgent.

6. **[infra · SUPERSEDED by #1] theta PnL slice is the build** — no longer "future," it's the
   now-action given theta crossed n=30 leaning wrong.

*(Resolved/dropped: old #6 folded into #1. #1 elevated from "watch" to "diagnose now" —
theta reached judgable n leaning opposite its backtest; added #2 as the conditional fix path.)*
