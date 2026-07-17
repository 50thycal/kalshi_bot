# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST). Retired/fully-resolved
books (pin15) and confirmed-stable data items (xgame collectors) are dropped from the table
below per runs #50-51's closures — nothing new to report on either.*

---

## Snapshot — 2026-07-17 12:05 PM CDT (run #52)

**Trading books (settled n / P&L / per-trade / open) — paper only; live P&L still not tracked
here, see #1 below:**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell6 | 29 | +$1.88 | +6.5 | 10 | 3rd consistent batch (+5.5c/trade), holding steady |
| mmsell4 | 21 | +$1.56 | +7.4 | 9 | unchanged this run, no new settles |
| mmsell5 | 13 | +$1.06 | +8.2 | 0 | unchanged this run, no new settles |
| mmsell7 | 4 | +$0.26 | +6.5 | 5 | unchanged this run, no new settles |
| mmsell8 | 6 | +$0.49 | +8.2 | 7 | unchanged this run, no new settles |
| **theta4** (fat-tail) | 29 | +$19.93 | **+68.7** | 0 | +1 trade, edge holding, 36% to gate |
| mmsell2 (paper) | 1,207 | +$32.02 | +2.7 | 14 | family leader, unchanged rank |
| mmsell1 (paper) | 1,853 | +$37.80 | +2.0 | 18 | |
| mmsell (control, paper) | 2,975 | +$47.92 | +1.6 | 25 | |
| mmsell3 (paper shadow) | 606 | +$10.85 | +1.8 | 14 | flat — still see #1 for the real (live) number |
| weather con (all) | 410 | −$11.82 | −2.9 | 10 | good batch (+5.5c/trade), cumulative improving |
| weather_concity | 42 | −$5.36 | −12.8 | 4 | mild negative batch (−3.2c/trade), still far better than its own history |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — nothing urgent this run; steady, incremental accrual across the board.** mmsell6
added its 3rd consistent batch at ~5.5¢/trade (still comfortably above mmsell3/mmsell2's own
paper rate). theta4 added 1 more trade at the same large magnitude (36% to gate now). mmsell4/5/7/8
had no new settlements — just slower accrual, not a concern. weather con(all) had a genuinely good
batch (+5.5¢/trade) pulling its cumulative up; weather_concity's batch was mildly negative but
still far better than its own historical average, so its cumulative improved too on net.

Live P&L visibility remains the top unresolved item — unchanged, restating without
re-investigating this run.

**FREEZE gate check:** settled grain=0, soft=5 (5 of the n≥100 trigger) — unchanged across 4 runs
now, not fired.

**Gate sweep (step 3b):** theta4 **29/80** (36%, edge holding) · mmsell4-8 gates (n≥150, or n≥100
for 5/8) — mmsell6 now the most-advanced at n=29 · weather_concity **42/120** (35%) · FREEZE
**5/100** (not fired).

**Data (last-24h / latest CDT):** crypto_spot, crypto_ladder, weather forecasts/obs/ensembles/
buckets all fresh (11:58 AM–12:04 PM ✓). No new data-health items to report — xgame collectors
remain confirmed stable/quiet per run #51's closure.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** quiet, steady run — mmsell6's 3rd consistent batch, theta4's edge still holding at
36% to gate, weather books both improved on net. Live P&L gap still the top unresolved item.
FREEZE gate unchanged.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[STILL TOP PRIORITY, UNCHANGED · get live P&L into this loop] mmsell3 continues trading real
   money; this loop still has no live-P&L visibility.** No new investigation this run — restating.
   Recommend a fable/build session add a live-P&L slice to step 1's query (join
   `live_orders`/`fills` to settlement outcomes) so this report covers the capital actually at
   risk, not just the paper shadow.

2. **[mmsell4-8 · steady accrual, mmsell6 now most-advanced] mmsell6 added a 3rd consistent
   batch (+5.5¢/trade, n=29) — the clearest accruing signal of the five.** mmsell4 (n=21), mmsell5
   (n=13), mmsell7 (n=4), mmsell8 (n=6) all flat this run, just waiting on more markets. Still far
   from any gate (150, or 100 for mmsell5/8); keep watching, no action.

3. **[theta4 · edge holding, steady growth] n=29/80 (36%), +68.7¢/trade, +1 trade this batch at
   the same large magnitude.** No new action — calibration already checked clean (2026-07-15).
   Keep watching the realized hit rate as n climbs toward the gate.

4. **[idea-model queue · MMX/NEST unchanged] MMX's premise still worth checking against
   mmsell4-8 for redundancy (unresolved since run #49).** NEST still behind theta4's n≥80 gate
   (36% there, calibration clean so far). RTPIN/BOXPIN behind unbuilt scraper infra. RATELAG
   behind a live Fed event.

5. **[weather_concity / con(all) · both improved on net] concity −12.8¢/trade (35% to gate,
   improved from −14.1¢ despite a mildly negative batch — blending effect, not a reversal),
   con(all) −2.9¢/trade (a genuinely good batch, +5.5¢/trade).** Carry forward, trend worth
   noting but not yet a decision point.

6. **[FREEZE gate · unchanged, not fired] Settled grain+soft = 5 of the n≥100 trigger, unchanged
   across 4 runs now.** Standing background check, nothing to act on.

*(Dropped/compressed this run: pin15 and xgame collector detail omitted per runs #50-51's
closures — both remain resolved/stable with nothing new to report. Changed: #2 mmsell6 now the
most-advanced variant with a 3rd consistent batch. #3 theta4 continued steady growth. #5 weather
books both improved on net this run — con(all) genuinely, concity via blending a less-bad batch
into a worse history. #1/#4/#6 restated/unchanged.)*
