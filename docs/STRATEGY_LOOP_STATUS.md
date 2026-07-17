# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-17 05:36 AM CDT (run #51)

**Trading books (settled n / P&L / per-trade / open) — paper only; live P&L still not tracked
here, see #1 below:**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell4 | 21 | +$1.56 | +7.4 | 8 | steady batch (+7.4c/trade), holding at its early level |
| mmsell5 | 13 | +$1.06 | +8.2 | 0 | steady batch (+7.0c/trade) |
| mmsell6 | 27 | +$1.77 | +6.6 | 8 | steady batch (+6.5c/trade) |
| mmsell7 | 4 | +$0.26 | +6.5 | 5 | no new settles this run |
| mmsell8 | 6 | +$0.49 | +8.2 | 7 | no new settles this run |
| **theta4** (fat-tail) | 28 | +$19.42 | **+69.4** | 0 | +2 trades at ~93.5c/trade this batch — edge holding, 35% to gate |
| mmsell2 (paper) | 1,203 | +$33.44 | +2.8 | 15 | family leader, unchanged rank |
| mmsell1 (paper) | 1,847 | +$39.08 | +2.1 | 17 | |
| mmsell (control, paper) | 2,961 | +$46.60 | +1.6 | 20 | |
| mmsell3 (paper shadow) | 604 | +$10.71 | +1.8 | 12 | flat — still see #1 for the real (live) number |
| weather con (all) | 396 | −$12.59 | −3.2 | 17 | unchanged settled/P&L, +3 new opens |
| weather_concity | 37 | −$5.20 | −14.1 | 6 | unchanged settled/P&L, +1 new open |
| pin15 | 445 | −$19.74 | −4.4 | 0 | RETIRED — fully quiet ~21h, dropping from active tracking (see footer) |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — mmsell4/5/6 are holding their early edge steady as n grows (still small, but
consistent, not a one-batch fluke anymore); theta4 keeps compounding at a large, stable
per-trade edge.**

mmsell4 (+7.4¢), mmsell5 (+8.2¢), and mmsell6 (+6.6¢) each added a batch this run at almost
exactly their prior per-trade rate — the encouraging early read from run #50 didn't regress on
the very next batch, which is a small but real point in its favor (still nowhere near n≥150/100
gates). mmsell7/8 had no new settlements — just slower accrual, not a concern on its own.

theta4 added 2 more trades at ~93.5¢/trade this batch, pushing cumulative to +69.4¢/trade at
n=28/80 (35%) — the edge continues to hold at a large, consistent magnitude. Calibration was
checked clean 2026-07-15 (0/25 tail-hits vs 6.9% modeled); still watching the realized hit rate
as n climbs toward the gate.

**pin15 has now been fully quiet for ~21 hours** (3 consecutive runs with zero activity) —
dropping it from the active suggestion list per run #50's flag; the retirement is settled and
doesn't need further tracking unless something changes.

weather books both quiet (new opens only). Live P&L visibility remains the top unresolved item
— unchanged from runs #49-50, restating without re-investigating.

**FREEZE gate check:** settled grain=0, soft=5 (5 of the n≥100 trigger) — unchanged, not fired.

**Gate sweep (step 3b):** theta4 **28/80** (35%, edge holding) · mmsell4-8 gates (n≥150, or n≥100
for 5/8) — all still early but mmsell4/5/6 now show 2 consecutive consistent batches ·
weather_concity **37/120** (31%) · FREEZE **5/100** (not fired).

**Data (last-24h / latest CDT):** crypto_spot, crypto_ladder, weather forecasts/obs/ensembles/
buckets all fresh (10:21 PM–10:35 PM UTC / 05:21–05:35 AM CDT ✓). xgame_matches now confirmed at
zero rows in 24h (as predicted last run) alongside xgame_tapes, also zero — both halves of the
shelved collector fully quiet, consistent with run #50's note, nothing new to add.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** mmsell4/5/6 held their early edge steady on a second batch — a small positive
signal, still far from any gate. theta4 keeps compounding at a large, stable edge (35% to gate).
pin15 fully resolved, dropped from active tracking. Live P&L gap still the top unresolved item.
xgame collectors confirmed fully quiet, as expected.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[STILL TOP PRIORITY, UNCHANGED · get live P&L into this loop] mmsell3 continues trading real
   money; this loop still has no live-P&L visibility.** No new investigation this run — restating
   run #49's finding. Recommend a fable/build session add a live-P&L slice to step 1's query (join
   `live_orders`/`fills` to settlement outcomes) so this report covers the capital actually at
   risk, not just the paper shadow.

2. **[mmsell4/5/6 · early edge held steady on a 2nd batch — still watch, not act] mmsell4 +7.4¢
   (n=21), mmsell5 +8.2¢ (n=13), mmsell6 +6.6¢ (n=27)** — each within ~0.1¢ of their prior-run
   rate, a small point toward this being real rather than a one-batch fluke, but n is still far
   below any gate (150, or 100 for mmsell5). mmsell7 (n=4) and mmsell8 (n=6) haven't added
   settlements in two runs — just slow accrual so far, no concern yet. Keep watching, do not act.

3. **[theta4 · edge holding, steady growth] n=28/80 (35%), +69.4¢/trade, +2 trades this batch at
   ~93.5¢/trade.** No new action — calibration already checked clean (2026-07-15). Keep watching
   the realized hit rate as n climbs; the first tail hit is still the real test.

4. **[idea-model queue · MMX/NEST unchanged] MMX's premise still worth checking against
   mmsell4-8 for redundancy (unresolved since run #49).** NEST still behind theta4's n≥80 gate
   (35% there, calibration clean so far). RTPIN/BOXPIN behind unbuilt scraper infra. RATELAG
   behind a live Fed event.

5. **[weather_concity / con(all) · quiet, no new settles] concity −14.1¢/trade (31% to gate),
   con(all) −3.2¢/trade — both flat this run (new opens only).** Carry forward.

6. **[xgame collectors · confirmed fully quiet, no new information] Both `xgame_tapes` and
   `xgame_matches` at zero rows in 24h, as predicted last run.** Shelved book, low-urgency;
   nothing new to report going forward unless it changes — will stop restating each run.

7. **[FREEZE gate · unchanged, not fired] Settled grain+soft = 5 of the n≥100 trigger, unchanged
   across 3 runs now.** Standing background check, nothing to act on.

*(Dropped this run: pin15 — fully quiet for 3 consecutive runs (~21h), retirement fully settled,
no longer tracked as an active item per run #50's flag. Changed: #2 mmsell4/5/6 — 2nd consistent
batch at similar per-trade rate, small positive signal noted. #3 theta4 — steady continued growth,
edge holding. #6 xgame — will stop restating each run now that it's confirmed stable/quiet.
#1/#4/#5/#7 restated/unchanged.)*
