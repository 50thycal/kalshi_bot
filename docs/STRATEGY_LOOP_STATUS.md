# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-16 08:04 PM CDT (run #50)

**Trading books (settled n / P&L / per-trade / open) — paper only; live P&L still not tracked
here, see #1 below:**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **mmsell4-8 (all 5 now trading)** | 14/9/19/4/6 | +$1.04/+0.78/+1.25/+0.26/+0.49 | **+7.4/+8.7/+6.6/+6.5/+8.2** | 10/4/9/2/5 | early but strong — all comfortably above mmsell3/mmsell2's own cumulative; mmsell5 resolved from zero rows to trading |
| mmsell2 (paper) | 1,193 | +$33.98 | +2.9 | 16 | family leader, unchanged rank |
| mmsell1 (paper) | 1,828 | +$40.03 | +2.2 | 23 | |
| mmsell (control, paper) | 2,928 | +$46.72 | +1.6 | 31 | |
| mmsell3 (paper shadow) | 593 | +$10.88 | +1.8 | 16 | good batch (+7.5c/trade) — still see #1 for the real (live) number |
| theta4 (fat-tail) | 26 | +$17.55 | +67.5 | 0 | no new settles since run #49, still 33% to gate |
| weather con (all) | 396 | −$12.59 | −3.2 | 14 | unchanged settled/P&L, +6 new opens |
| weather_concity | 37 | −$5.20 | −14.1 | 5 | unchanged settled/P&L, +3 new opens |
| pin15 | 445 | −$19.74 | −4.4 | 0 | RETIRED — fully quiet ~12h now, confirmed stable |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — mmsell4-8 are all now trading, and early numbers are strongly encouraging (though
still too small to trust): every one of the 5 new variants is running well above both mmsell3 and
mmsell2's own cumulative per-trade (6.5-8.7¢ vs ~2-3¢).** This is directionally exactly what
`docs/MMSELL_VARIANTS_THESIS.md` predicted — stripping World Cup/cricket/tennis (mmsell4), or
allowlisting totals/spreads/props (mmsell5), should lift the edge back toward the non-WC live
number (+5.6¢). mmsell5 specifically resolved from "zero rows at all" (run #49's flag) to n=9 —
that concern is closed. **None of these are near their gates yet** (n≥150, or n≥100 for
mmsell5/8) — this is an early-signal note, not a verdict; the small-n discipline applies loudly
here given how good the numbers look.

**Live P&L visibility is still the top unresolved item (from run #49).** mmsell3 continues
trading real money; this loop still only reports the paper shadow. No new attempt made this run
to fix the query — restating, not re-investigating, since nothing about the situation changed.

theta4 had zero new settlements (flat since run #49, still n=26/80). pin15 has now been fully
quiet for ~12 hours since retiring — stable, no residual activity, closing this out further (see
suggestion #3). weather books both quiet (new opens only).

**FREEZE gate check:** settled grain=0, soft=5 (5 of the n≥100 trigger) — unchanged from run #49,
not fired.

**Gate sweep (step 3b):** theta4 **26/80** (33%, unchanged) · mmsell4-8 gates (n≥150, or n≥100 for
5/8) — all still far off but now genuinely accruing · weather_concity **37/120** (31%) · FREEZE
**5/100** (not fired).

**Data (last-24h / latest CDT):** crypto_spot, crypto_ladder, weather forecasts/obs/ensembles/
buckets all fresh (07:36–08:01 PM ✓). **xgame_tapes is now genuinely at zero rows in the last 24h**
(latest still 2026-07-15 04:06:59 PM CDT, unchanged since run #47) — the trailing-window volume
that made this look "just modestly stale" in earlier runs has now fully aged out; this is a real,
confirmed stall, not a lull. **xgame_matches** has also had no new matches in ~22h (latest still
2026-07-15 10:12 PM CDT) and will likely read zero next run too. Both still on the shelved xgame
book — low-urgency, but both halves of the collector are now quiet at the same time, which is a
cleaner (if still unimportant) picture than the flip-flopping seen in runs #46-49.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** mmsell4-8 all trading now with strong early per-trade numbers (6.5-8.7¢, still very
small n) — directionally confirms the WC-drag decomposition thesis, watch as n grows. Live P&L
tracking gap unresolved (restated, not re-investigated). theta4/pin15/weather quiet. xgame
collectors both now genuinely stale together. FREEZE gate unchanged.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[STILL TOP PRIORITY, UNCHANGED · get live P&L into this loop] mmsell3 continues trading real
   money; this loop still has no live-P&L visibility.** No new investigation this run — restating
   run #49's finding rather than re-deriving it. Recommend a fable/build session add a live-P&L
   slice to step 1's query (join `live_orders`/`fills` to settlement outcomes) so this report
   covers the capital actually at risk, not just the paper shadow.

2. **[mmsell4-8 · promising early signal, all now trading — WATCH, do not over-read] n=4-19 across
   the five variants, all running +6.5 to +8.7¢/trade** — well above mmsell3(+1.8¢)/mmsell2(+2.9¢)
   paper, and mmsell5 (the one flagged last run as "zero rows") is now trading at n=9. This is
   consistent with the thesis (removing WC/cricket/tennis, or allowlisting totals/spreads, should
   lift the edge) but n is far too small to promote anything yet — could easily be an early lucky
   stretch, same lesson as mmsell3's own early-run swings. Gates are n≥150 (mmsell4/6/7) / n≥100
   (mmsell5/8); watch the trend as they accrue, don't act on this reading.

3. **[pin15 · fully resolved, consider dropping from active tracking] Fully quiet ~12h since
   retiring, no residual activity.** This has been stable for two consecutive runs now — unless
   something changes, this item can likely drop off the suggestion list entirely next run rather
   than continue as a "watch" item; it's done.

4. **[theta4 · unchanged, still steady] n=26/80 (33%), +67.5¢/trade, zero new settlements this
   run.** No new action beyond the standing watch — calibration was checked clean 2026-07-15 (0/25
   tail-hits vs 6.9% modeled); keep watching the realized hit rate as n climbs.

5. **[idea-model queue · MMX/NEST unchanged] MMX's premise still worth checking against mmsell4-8
   for redundancy (run #49's flag, unresolved).** NEST still behind theta4's n≥80 gate (33%
   there, calibration clean so far). RTPIN/BOXPIN behind unbuilt scraper infra. RATELAG behind a
   live Fed event.

6. **[weather_concity / con(all) · quiet, no new settles] concity −14.1¢/trade (31% to gate),
   con(all) −3.2¢/trade — both flat this run (new opens only).** Carry forward.

7. **[xgame collectors · BOTH now genuinely stale together] `xgame_tapes` confirmed at zero rows
   in the last 24h (not just an old timestamp with residual trailing volume, as in runs #48-49) —
   real stall since run #47. `xgame_matches` has had no new matches in ~22h and will likely read
   zero next run too.** Both on the shelved book, still low-urgency, but worth a look together if
   anyone revisits xgame — this is a cleaner, fully-quiet picture than the flip-flopping seen
   recently.

8. **[FREEZE gate · unchanged, not fired] Settled grain+soft = 5 of the n≥100 trigger, same as
   run #49.** Standing background check, nothing to act on.

*(Changed this run: #2 mmsell4-8 — all five now trading (was 0-1 settled last run), early numbers
strongly positive but explicitly flagged as too-small-n to trust. #3 pin15 — fully stable for 2
runs, flagged as likely droppable next run. #7 xgame — upgraded from "stall persisting" to
"confirmed genuinely zero, both halves quiet together." #1/#4/#5/#6/#8 restated/unchanged, no new
information this run.)*
