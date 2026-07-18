# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST). Retired/fully-resolved
books (pin15) and confirmed-stable data items are dropped from the table below once settled —
nothing new to report on those unless flagged again.*

---

## Snapshot — 2026-07-18 05:36 AM CDT (run #54)

**Trading books (settled n / P&L / per-trade / open) — paper only; live P&L still not tracked
here, see #1 below:**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell5 | 23 | +$1.99 | +8.7 | 0 | strongest variant holds, good batch (+8.8c/trade) |
| mmsell control (paper) | 3,061 | +$47.72 | +1.6 | 22 | strong recovery batch (+8.3c/trade) |
| mmsell3 (paper shadow) | 627 | +$10.42 | +1.7 | 9 | recovery batch (+7.9c/trade) — still see #1 for the real (live) number |
| mmsell4 | 38 | +$0.84 | +2.2 | 5 | recovery batch (+7.9c/trade) |
| mmsell2 (paper) | 1,245 | +$33.50 | +2.7 | 10 | recovery batch (+4.3c/trade) |
| mmsell1 (paper) | 1,900 | +$38.72 | +2.0 | 16 | recovery batch (+4.9c/trade) |
| mmsell6 | 42 | +$0.71 | +1.7 | 8 | recovery batch (+6.3c/trade) |
| mmsell7 | 9 | −$0.39 | −4.3 | 1 | unchanged, no new settles |
| mmsell8 | 13 | +$0.07 | +0.5 | 1 | unchanged, no new settles |
| theta4 (fat-tail) | 30 | +$15.87 | +52.9 | 0 | unchanged, no new settles since the tail hit (run #53) |
| weather con (all) | 410 | −$11.82 | −2.9 | 17 | unchanged settled/P&L, +2 new opens |
| weather_concity | 42 | −$5.36 | −12.8 | 7 | unchanged settled/P&L, +1 new open |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — the mmsell family broadly recovered this run, confirming run #53's shared-batch
read rather than a persistent trend.** Every variant with new settlements posted a solidly
positive batch this run (4.3¢ to 8.8¢/trade across mmsell1/2/3/4/6/control) — the dip flagged
last run really was a one-batch adverse event, not a degradation. mmsell5 continues to hold as
the strongest performer (+8.7¢/trade cumulative, n=23) with another good batch of its own.
mmsell7/8 had no new settlements — still just slow accrual.

theta4 had no new settlements — still sitting at +52.9¢/trade post-tail-hit (run #53), nothing
new to report on the calibration watch this run.

weather books both quiet (new opens only). Live P&L visibility remains the top unresolved item —
unchanged, restating without re-investigating.

**FREEZE gate check:** settled grain=0, soft=5 (5 of the n≥100 trigger) — unchanged across 6 runs
now, not fired.

**Gate sweep (step 3b):** theta4 **30/80** (38%, unchanged) · mmsell4-8 gates (n≥150, or n≥100
for 5/8) — mmsell6 most-advanced at n=42, mmsell5 strongest per-trade at n=23 · weather_concity
**42/120** (35%) · FREEZE **5/100** (not fired).

**Data (last-24h / latest CDT):** crypto_spot, crypto_ladder, weather forecasts/obs/ensembles/
buckets all fresh (05:22–05:35 AM ✓). xgame_tapes continuing to collect normally since its
resumption (12,793 rows/24h, fresh) — confirmed stable, not a one-off blip. xgame_matches still
dark, unchanged.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** mmsell family broadly recovered this run, confirming last run's dip was a shared
one-batch event, not a trend — mmsell5 remains the standout. theta4 quiet post-tail-hit, nothing
new. Live P&L gap still the top unresolved item. FREEZE gate unchanged.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[STILL TOP PRIORITY, UNCHANGED · get live P&L into this loop] mmsell3 continues trading real
   money; this loop still has no live-P&L visibility.** No new investigation this run — restating.
   Recommend a fable/build session add a live-P&L slice to step 1's query.

2. **[mmsell4-8 · family recovered broadly this run, mmsell5 remains standout] Every variant with
   new settlements posted a positive batch (4.3-8.8¢/trade), confirming run #53's dip was a
   shared one-batch event.** mmsell5 (+8.7¢, n=23) continues as the strongest per-trade performer
   across two consecutive good batches; mmsell6 is the most-advanced by n (42). mmsell7/8 still
   slow to accrue (n=9/13, no new settles this run) — not a concern yet, just early.

3. **[theta4 · quiet post-tail-hit, nothing new] n=30/80 (38%), +52.9¢/trade, no new settlements
   since the tail hit in run #53.** No new action — continue watching the realized hit rate as
   more trades settle; one hit at a modeled-consistent rate doesn't change the standing gate
   status.

4. **[idea-model queue · MMX/NEST unchanged] MMX's premise still worth checking against
   mmsell4-8 for redundancy (unresolved since run #49).** NEST still behind theta4's n≥80 gate
   (38% there). RTPIN/BOXPIN behind unbuilt scraper infra. RATELAG behind a live Fed event.

5. **[weather_concity / con(all) · quiet, no new settles] concity −12.8¢/trade (35% to gate),
   con(all) −2.9¢/trade — both flat this run (new opens only).** Carry forward.

6. **[xgame_tapes · confirmed stable after resumption] Continuing to collect normally since run
   #53's resumption (12,793 rows/24h).** xgame_matches remains dark, unchanged. Both low-urgency;
   no longer flagging each run unless something changes.

7. **[FREEZE gate · unchanged, not fired] Settled grain+soft = 5 of the n≥100 trigger, unchanged
   across 6 runs now.** Standing background check, nothing to act on.

*(Changed this run: #2 mmsell4-8 — broad recovery batch confirms run #53's dip was one-off, not
a trend; mmsell5 continues as standout. #3 theta4 — quiet, nothing new post-tail-hit. #6
xgame_tapes — confirmed stable, dropping to a one-line note going forward. #1/#4/#5/#7
restated/unchanged.)*
