# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-15 12:05 PM CDT (run #46)

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **theta4** (fat-tail) | 25 | +$16.85 | **+67.4** | 0 | +8 trades, magnitude holding — 31% to gate, calibration check still pending |
| mmsell3 (5-10c) | 494 | +$14.51 | +2.9 | 13 | essentially tied with mmsell2 again (0.015c) — not tracking this per-run per #45 |
| mmsell2 | 1,076 | +$31.44 | +2.9 | 15 | |
| mmsell1 | 1,657 | +$40.13 | +2.4 | 19 | |
| mmsell (control) | 2,689 | +$46.58 | +1.7 | 26 | |
| pin15 | 359 | −$10.19 | −2.8 | 0 | flat batch (−0.5c/trade) — oscillation paused, not resolved |
| weather_concity | 31 | −$3.84 | −12.4 | 5 | small positive batch (n=3), still 26% to gate |
| weather con (all) | 382 | −$9.38 | −2.5 | 9 | negative batch (−27.7c/trade) — diverged from concity again |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — theta4 keeps growing with its edge intact (31% to gate now); everything else is
quiet/unchanged from the patterns already established.**

**theta4** added 8 more trades, all consistent with its ~65-70¢/trade magnitude (cumulative now
+67.4¢ at n=25, 31% to the n≥80 gate). This is no longer a 1-2 trade blip — three consecutive
runs at a stable, very large per-trade edge. **The calibration check (realized-tail-hit ratio vs
modeled) is now the single most valuable open question in this whole report** — if theta4 is
actually calibrated, it may be the strongest book in the portfolio by the time it gates; if it's
just riding a favorable stretch before a tail hit, that won't show up until it's too late to
un-ring the bell. Worth prioritizing over the mmsell/pin15 noise below.

mmsell3/mmsell2 essentially tied again (per run #45's note, not re-narrating this weekly coin
flip). pin15 had a flat batch (−0.5¢/trade) — the oscillation didn't produce a new extreme this
run, nothing new to add to the standing T-window-slice recommendation. weather_concity and
weather con(all) diverged again in opposite directions, consistent with prior runs.

**Gate sweep (step 3b):** theta4 **25/80** (31%, calibration check now the priority) · pin15
**359/150** (T-window slice still recommended) · mmsell3 **494/150** (own bar cleared, family
ranking still noise) · weather_concity **31/120** (26%).

**Data (last-24h / latest CDT):** all collectors fresh (crypto/weather 12:00–12:04 PM ✓,
xgame_tapes 12:04 PM ✓, 60,680 rows). xgame_matches: unchanged, still frozen (8th consecutive
run) — no new detail per run #45's note.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** theta4's edge is holding steady across 3 runs now (31% to gate) — the calibration
check is the priority open item in this report. Everything else (mmsell ranking, pin15,
weather divergence, xgame_matches) continues the patterns already established, nothing new.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta4 · calibration check — now the top-priority open item] n=25/80 (31%), +67.4c/trade,
   stable across 3 consecutive runs (was +70.1c, +70.1c, now +67.4c — essentially flat, not
   decaying).** This has moved from "worth checking eventually" to "the most consequential open
   question in the report" — a fat-tail-sell book with a persistently huge per-trade edge is
   exactly the profile that looks great until a tail event proves the model was miscalibrated.
   Recommend a fable session run the realized-tail-hit-ratio check (`docs/THETA_THESIS.md`'s
   gate: keep only if per-trade > 0 AND tail-hit ≤ 1.25x modeled) before n climbs much further —
   catching a calibration problem at n=25-30 is far cheaper than at n=80 with live capital ideas
   (NEST, #4) already queued behind it.

2. **[pin15 · T-window slice still recommended, oscillation paused not resolved] n=359,
   −2.8c/trade (flat vs −3.0c last run, batch was −0.5c/trade — no new extreme).** Same standing
   recommendation from runs #44-45: run the P&L-by-T-at-entry slice rather than keep watching
   batches. Nothing new this run to change that.

3. **[mmsell2 vs mmsell3 · still not tracking per-run, per #45] Essentially tied again this run
   (2.94c vs 2.92c).** Consistent with the "don't re-narrate weekly" call from run #45 — no
   change to that recommendation.

4. **[idea-model queue · MMX/NEST] MMX (`IDEA_MODEL_20260710_run2.md`) still shouldn't assume
   either mmsell variant as template (#3).** NEST behind theta4's calibration check (#1) — given
   theta4's edge is holding steady, NEST's blocker may resolve sooner than expected IF the
   calibration check comes back clean; if it doesn't, NEST should probably be shelved along with
   theta4. RTPIN/BOXPIN behind unbuilt scraper infra. RATELAG behind a live Fed event.

5. **[weather_concity / con(all) · continuing to diverge batch-to-batch] concity −12.4c/trade
   (26% to gate, improving), con(all) −2.5c/trade (worsening this batch).** Same pattern as
   recent runs — the two books are not moving together despite shared underlying markets. Carry
   forward, nothing new to decide yet.

6. **[xgame_matches · unchanged, 8th consecutive run] No new detail — still frozen at the same
   pre-crash timestamp, long-standing per run #42/45's notes.**

7. **[mmsell existing · context, unchanged] control +1.7c/trade, mmsell1 +2.4c/trade** — all four
   variants remain in a fairly tight band, consistent with run #45's read that this may be one
   edge with sampling noise rather than genuinely different sub-strategies.

*(Changed this run: #1 theta4 — ELEVATED to top priority; 3 consecutive runs of a stable, large
edge makes the calibration check materially more important than "eventually get to it." #2/#3
unchanged, oscillations paused but not resolved, no new narration needed. #4 MMX/NEST — updated
to note NEST's fate is now tied to theta4's calibration outcome specifically. #5/#6/#7
unchanged/compressed per the "stop re-narrating settled noise" commitments from run #45.)*
