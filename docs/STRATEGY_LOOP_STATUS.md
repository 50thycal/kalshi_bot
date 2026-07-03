# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 2-hourly status loop (trigger `2-hourly strategy status loop`).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run, updated as data accumulates.*

---

## Snapshot — 2026-07-03 22:13 UTC (run #2)

**Books actively trading (settled n / settled P&L / open) — Δ vs run #1 noted:**
- **theta** — **3 settled** (was 0), **−$0.79** (−26¢/trade), 1 open. **First settlements
  landed.** n=3 is pure noise (one tail that hits swamps three wins); no signal yet. It's
  opening + settling on the expected hourly cadence, which is what matters this early.
- **mmsell** — 65 settled (was 64), **−$1.18** (−1.8¢/trade), **47 open** (was 9). Entries
  **ramping hard** (diversifying across many small positions, as the exit study prescribed);
  settlements lag because these markets settle over days. Still a tiny settled sample.
- **weather `con` (consensus)** — **211 settled, +$9.67**, 17 open. Steady, still the only
  consistently +EV weather book.
- **weather (everything else pooled)** — **4,596 settled, −$226.07**, 62 open. The documented
  bleeders (fav/nws/cal/dist/all lows), unchanged in character.

**Data collection — ALL FRESH ✓ (last-24h rows / latest UTC):**
| collector | 24h rows | latest | status |
|---|---|---|---|
| crypto_spot_candles | 2,870 | 22:09 | ✓ fresh, 2 products (BTC+ETH) |
| crypto_ladder_snapshots | 1,680 | 22:09 | ✓ fresh, **100% model-priced** (1680/1680) |
| weather_forecasts | 11,356 | 22:13 | ✓ fresh |
| weather_observations | 644 | 22:13 | ✓ fresh |
| weather_ensembles | 1,776 | 22:13 | ✓ fresh (hourly cadence) |
| weather_bucket_snapshots | 13,194 | 22:13 | ✓ fresh |

**Headline:** everything healthy. theta crossed from 0→3 settled (noise-level, negative, ignore
the sign at n=3); mmsell is fanning out to 47 open positions. No stale or zero collectors.

---

## Carried-over suggestions (review these; do not expect the loop to act)

Each tagged with status and the date first raised. Kept while valid, dropped when
resolved/invalidated, updated as data grows.

1. **[theta · STILL VALID, 07-03] Let theta accumulate before judging.** Now 3 settled at
   −$0.79 — n=3 is noise, do not read the sign. Still target ~30–100 settled before comparing
   live P&L to the +4.4¢/contract backtest. Do **not** touch `THETA_*` config yet.

2. **[theta · STILL VALID, 07-03] Build a theta PnL slice once ~30+ settle** (a read-only
   analysis like `weather_pnl`, sliced by price band × time-to-expiry, comparing the
   model-overpriced cohort's live win% to its priced-in probability). Not there yet (3 settled).

3. **[theta · NEW 07-03 run#2] Watch theta's sample-build velocity.** In ~26 min it went 3
   open → 3 settled + 1 open, i.e. it opens a few per hour (lumpy — depends on hourly windows
   hitting the 10–55 min entry zone AND a model-overpriced tail). If after ~a day it's opening
   too few to reach a judgable sample in reasonable time, the entry gates (`theta_min_edge_cents`,
   the entry window, `theta_max_per_event`) would be the tuning lever — a fable candidate, **not
   now**. Just monitoring the rate for now.

4. **[mmsell · STILL VALID, 07-03] Watch whether the +5.2¢ tape edge shows in paper.** 65
   settled at −1.8¢/trade, 47 open ramping. Settlements lag entries (multi-day markets), so the
   settled sample builds slower than theta's — give it more n before any verdict.

5. **[weather · STILL VALID, 07-03] Consider pruning more confirmed-bleeder weather books.**
   Now quantified: weather-other = **−$226 over 4,596 settled** vs `con` +$9.67. Pruning cuts
   noise + API load but stops cross-validation accrual — judgment call for the user, not urgent.

6. **[infra · STILL VALID, 07-03] Highest-value next build is theta analysis tooling** (see #2),
   not more strategies — pipeline is healthy; the missing piece is the lens to read theta's live
   edge once its sample is real.

*(Resolved/dropped this run: none. Added: #3 theta entry-rate watch.)*
