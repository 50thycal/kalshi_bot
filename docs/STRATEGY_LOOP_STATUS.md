# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 2-hourly status loop (trigger `2-hourly strategy status loop`).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run, updated as data accumulates.*

---

## Snapshot — 2026-07-04 00:13 UTC (run #3)

**Books actively trading (settled n / settled P&L / open) — Δ vs run #2:**
- **mmsell** — **104 settled** (was 65), **+$2.03** (was −$1.18) = **+2.0¢/trade**, 53 open.
  **Flipped positive.** Early but encouraging — the maker-sell edge starting to show as
  settlements accrue (still below the +5.2¢ backtest, but positive and n growing fast).
- **theta** — **8 settled** (was 3), **−$3.68** (−46¢/trade), 0 open. Still noise-level n.
  The negative is consistent with **1 tail hitting** (negative skew — many small wins, rare
  big loss, exactly the thesis's risk profile); it is **not** yet evidence the edge is
  absent. 0 open right now = no model-overpriced tail in the last entry window (lumpy).
- **weather `con`** — 211 settled, **+$9.67**, 17 open. Unchanged (settles on weather-event
  clock; no new settlements this window).
- **weather (rest pooled)** — 4,596 settled, **−$226.07**, 63 open. Unchanged bleeders.

**Data collection — ALL FRESH ✓ (last-24h rows / latest UTC):**
| collector | 24h rows | latest | status |
|---|---|---|---|
| crypto_spot_candles | 2,874 | 00:11 | ✓ fresh, 2 products (BTC+ETH) |
| crypto_ladder_snapshots | 6,960 | 00:11 | ✓ fresh, **100% model-priced** (6960/6960) |
| weather_forecasts | 11,292 | 00:12 | ✓ fresh |
| weather_observations | 656 | 00:12 | ✓ fresh |
| weather_ensembles | 1,744 | 00:12 | ✓ fresh (hourly cadence) |
| weather_bucket_snapshots | 13,248 | 00:12 | ✓ fresh |

**Headline:** mmsell turned positive (+$2.03/104); theta at 8 settled −$3.68 (negative-skew
noise, watch); all collectors fresh. Ladder-snapshot 24h count jumped (1.7k→7.0k) as theta's
full day of collection now fits the window — expected, not an anomaly.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta · STILL VALID, 07-03] Let theta accumulate before judging.** 8 settled, −$3.68 —
   still noise, and the sign is dominated by negative skew (a single tail hit ≈ −$4 on 5
   contracts). Target ~30–100 settled before reading anything into P&L. Do **not** touch
   `THETA_*` config yet.

2. **[theta · STILL VALID, 07-03] Build a theta PnL slice once ~30+ settle** — and make it
   **decompose win-rate vs average tail-loss** (band × time-to-expiry, model-overpriced cohort
   win% vs priced-in prob). That's the read that tells whether −P&L is a broken edge or just
   variance from the occasional tail. Not there yet (8 settled).

3. **[theta · STILL VALID, 07-03 run#2] Watch theta's sample-build velocity.** 3→8 settled in
   2h (~2–3/hr), 0 open right now. On track for ~30–50/day → judgable in ~2–3 days. If it
   stalls (few opens over a full day), the entry gates (`theta_min_edge_cents` / entry window /
   `theta_max_per_event`) are the tuning lever — fable candidate, **not now**.

4. **[mmsell · STILL VALID, 07-03 — improving] Watch the +5.2¢ tape edge in paper.** Now
   **+2.0¢/trade on 104 settled** (was −1.8¢ on 65). Trending toward the backtest; keep
   watching as n grows — if it holds ≥ ~+2–3¢ at n≈300+, that's a real forward-validation.

5. **[weather · STILL VALID, 07-03] Consider pruning more confirmed-bleeder weather books.**
   weather-other = −$226 over 4,596 vs `con` +$9.67. Judgment call (cuts noise + API load but
   stops cross-validation accrual), not urgent.

6. **[infra · STILL VALID, 07-03] Highest-value next build is theta analysis tooling** (see #2)
   — pipeline is healthy; the missing piece is the lens to read theta's live edge at real n.

*(Resolved/dropped this run: none. #4 upgraded to "improving" as mmsell flipped positive.)*
