# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 2-hourly status loop (trigger `2-hourly strategy status loop`).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run, updated as data accumulates.*

---

## Snapshot — 2026-07-03 21:47 UTC (run #1, loop setup)

**Books actively trading (settled P&L / open):**
- **theta** — 0 settled, **3 open**. Just deployed with PR #7; positions settle within the
  hour, so first settled P&L expected within ~1–2h. Model is pricing (see data below).
- **mmsell** — 64 settled, **−$0.42 (−0.7¢/trade)**, 9 open. **Now collecting** after the
  fill_assumption fix deployed (was zero rows before). Near-breakeven, tiny sample.
- **weather `con` (consensus)** — the standout +EV weather book: h20 +$3.00, h17 +$2.82,
  h14 +$2.64, h8 +$1.21 (**≈ +$9.7 pooled**). Still trading (6 open across windows).
- **weather everything else** — net negative as documented (fav −$40ish, nws/cal/dist/all
  lows bleed the cost floor). `fav_h8` (+$4.91) is the lone bright non-con cell.

**Data collection — ALL FRESH ✓ (last-24h rows / latest):**
| collector | 24h rows | latest | status |
|---|---|---|---|
| crypto_spot_candles | 2,878 | 21:45:00 | ✓ fresh, 2 products (BTC+ETH) |
| crypto_ladder_snapshots | 720 | 21:45:29 | ✓ fresh, **100% model-priced** |
| weather_forecasts | 11,129 | 21:46:40 | ✓ fresh |
| weather_observations | 632 | 21:42:53 | ✓ fresh |
| weather_ensembles | 1,704 | 21:23:06 | ✓ fresh (hourly cadence) |
| weather_bucket_snapshots | 12,900 | 21:46:40 | ✓ fresh |

**Headline:** theta deploy + mmsell fix both landed and are collecting cleanly. Nothing stale,
nothing zero. No settled theta trades yet — the whole point of the next few runs is to watch
that sample build.

---

## Carried-over suggestions (review these; do not expect the loop to act)

Each tagged with status and the date first raised. Kept while still valid, dropped when
resolved/invalidated, updated as data grows.

1. **[theta · NEW 07-03] Let theta accumulate before judging.** Target ~30–100 settled trades
   (a few days) before comparing live realized P&L to the +4.4¢/contract backtest. Until then,
   just confirm it keeps opening positions and that entries cluster in the final hour (the P2
   window). Do **not** touch `THETA_*` config yet — early noise will mislead.

2. **[theta · NEW 07-03] Build a theta PnL slice once ~30+ settle.** A dedicated read-only
   analysis (like `weather_pnl`) slicing theta by price band × time-to-expiry, and comparing
   the model-overpriced cohort's live win% to its priced-in probability, to check the P3 edge
   shows up live and not just in backtest. Worth building when the sample justifies it.

3. **[mmsell · NEW 07-03] Watch whether the +5.2¢ tape edge shows in paper.** Now collecting
   again (n=64, −0.7¢/trade so far — small). Give it more n before any verdict; the backtest
   said the edge is real but paper adds fill realism.

4. **[weather · NEW 07-03] Consider pruning more confirmed-bleeder weather books.** `con` is
   the only consistently +EV weather book; fav/nws/cal/dist and all lows bleed. Pruning them
   would cut noise and API load — but it's a judgment call, not urgent, and pruned books stop
   accumulating cross-validation data. Flag for the user, don't rush.

5. **[infra · NEW 07-03] Highest-value next build is theta analysis tooling** (see #2), not more
   strategies — the pipeline is healthy; what's missing is the lens to read theta's live edge.

*(Resolved/dropped this run: none — first run.)*
