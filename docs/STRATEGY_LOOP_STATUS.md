# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST). Retired/fully-resolved
books (pin15) and confirmed-stable data items are dropped from the table below once settled —
nothing new to report on those unless flagged again.*

---

## Snapshot — 2026-07-18 12:05 PM CDT (run #55)

**Trading books (settled n / P&L / per-trade / open) — paper only; live P&L still not tracked
here, see #1 below:**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell2 (paper) | 1,252 | +$33.57 | +2.7 | 10 | flat batch (+1c/trade), unaffected by the dip below |
| mmsell1 (paper) | 1,912 | +$38.04 | +2.0 | 16 | negative batch (−5.7¢/trade) |
| mmsell (control, paper) | 3,079 | +$46.88 | +1.5 | 20 | negative batch (−4.7¢/trade) |
| mmsell3 (paper shadow) | 634 | +$9.89 | +1.6 | 10 | negative batch (−7.6¢/trade) — still see #1 for the real (live) number |
| mmsell6 | 48 | +$0.09 | +0.2 | 7 | negative batch (−10.3¢/trade), cumulative dropped sharply |
| mmsell4/5/7/8 | 38/23/9/13 | — | +2.2/+8.7/−4.3/+0.5 | no new settlements this run |
| theta4 (fat-tail) | 30 | +$15.87 | +52.9 | 0 | unchanged, 2nd run with no new settles since the tail hit |
| weather con (all) | 425 | −$15.04 | −3.5 | 12 | rough batch (−21.5¢/trade) |
| weather_concity | 48 | −$7.43 | −15.5 | 4 | rough batch too (−34.5¢/trade) — moved with con(all) again |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — the mmsell family dipped again this run, and the pattern across runs #53-55 (dip →
recover → dip) is itself the finding: this is batch-to-batch variance at small n, not a trend in
either direction.** mmsell6/mmsell3/mmsell1/control all had negative batches (−4.7¢ to −10.3¢/
trade); mmsell2 was flat; mmsell4/5/7/8 had no new settlements at all. This mirrors run #53's dip
→ run #54's recovery almost exactly — **recommend not reading any single run's direction here as
signal until n is much larger.** mmsell5 remains the standout on a cumulative basis (+8.7¢,
unchanged this run) but hasn't been tested by a bad batch yet since it had no new settlements.

weather con(all) and weather_concity both had a rough batch together again (−21.5¢ and −34.5¢/
trade respectively) — consistent with prior runs' observation that they move together when a
shared adverse settlement window hits (they share underlying markets).

theta4 remains quiet — no new settlements for the second straight run since the tail hit in run
#53. Live P&L visibility remains the top unresolved item — unchanged, restating.

**FREEZE gate check:** settled grain=0, soft=5 (5 of the n≥100 trigger) — unchanged across 7 runs
now, not fired.

**Gate sweep (step 3b):** theta4 **30/80** (38%, quiet) · mmsell4-8 gates (n≥150, or n≥100 for
5/8) — mmsell6 most-advanced at n=48 despite this batch's dip · weather_concity **48/120** (40%)
· FREEZE **5/100** (not fired).

**Data (last-24h / latest CDT):** crypto_spot, crypto_ladder, weather forecasts/obs/ensembles/
buckets all fresh (11:59 AM–12:04 PM ✓). xgame_tapes continuing to collect normally (18,314
rows/24h); xgame_matches still dark, unchanged.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** mmsell family dipped again (3rd swing in 3 runs: dip/recover/dip) — read as
ongoing batch variance, not a trend, until n is larger. weather books had another shared rough
batch. theta4 quiet, no new settles. Live P&L gap and FREEZE gate both unchanged.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[STILL TOP PRIORITY, UNCHANGED · get live P&L into this loop] mmsell3 continues trading real
   money; this loop still has no live-P&L visibility.** No new investigation this run — restating.
   Recommend a fable/build session add a live-P&L slice to step 1's query.

2. **[mmsell4-8 (+control/mmsell3) · dip-recover-dip pattern across runs #53-55 — treat as
   variance, not signal] mmsell6 −10.3¢, mmsell3 −7.6¢, mmsell1 −5.7¢, control −4.7¢/trade this
   batch; mmsell2 flat; mmsell4/5/7/8 no new settlements.** This is the third distinct swing in
   three runs (down/up/down) — **recommend the loop stop reading single-run direction as signal
   for this cohort** until n is meaningfully larger (same lesson as pin15's and mmsell2-vs-3's
   earlier whipsaws). mmsell5 remains the standout on cumulative P&L (+8.7¢) but hasn't yet had a
   bad batch to test that against — worth watching specifically when it does.

3. **[theta4 · quiet, 2nd run with no new settles] n=30/80 (38%), +52.9¢/trade, unchanged since
   the tail hit in run #53.** No new action. Continue watching the realized hit rate as more
   trades settle.

4. **[idea-model queue · MMX/NEST unchanged] MMX's premise still worth checking against
   mmsell4-8 for redundancy (unresolved since run #49).** NEST still behind theta4's n≥80 gate
   (38% there). RTPIN/BOXPIN behind unbuilt scraper infra. RATELAG behind a live Fed event.

5. **[weather_concity / con(all) · shared rough batch again] concity −15.5¢/trade (40% to gate),
   con(all) −3.5¢/trade — both took a hit together this batch (−34.5¢ and −21.5¢/trade
   respectively), consistent with the pattern of moving together on shared adverse settlement
   windows.** Carry forward, nothing to decide yet.

6. **[xgame_tapes / xgame_matches · stable, unchanged] xgame_tapes continuing to collect normally;
   xgame_matches still dark.** Both low-urgency, no new information.

7. **[FREEZE gate · unchanged, not fired] Settled grain+soft = 5 of the n≥100 trigger, unchanged
   across 7 runs now.** Standing background check, nothing to act on.

*(Changed this run: #2 mmsell4-8 — reframed from run-by-run narration to explicitly calling out
the dip-recover-dip pattern as variance, recommending the loop stop over-reading single-run
swings for this cohort (same lesson learned earlier from pin15 and mmsell2-vs-mmsell3). #3 theta4
— unchanged, still quiet. #5 weather — another shared rough batch, consistent pattern. #1/#4/#6/#7
restated/unchanged.)*
