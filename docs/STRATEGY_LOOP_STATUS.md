# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12.
As of run #56, the loop also pulls real live P&L for any LIVE book via a direct SQL query. As of
run #65 (corrected), the live P&L query filters `strategy='mmsell3'` exactly — NOT `LIKE
'mmsell%'`, which incorrectly sweeps in the `mmsell3_closeout` wind-down tag and double-counts
tickers. mmsell3 LIVE trading itself was wound down 2026-07-19 (see run #65's snapshot and
`docs/MMSELL_LIVE_POSTMORTEM.md`) — new live settlements should be rare/none going forward; flat
live P&L is expected, not a red flag. **CLOSED 2026-07-22 (post-run-#68 investigation): the
account has been genuinely, verifiably 100% flat since 2026-07-20 10:20:56 CT — confirmed via
`live_orders`' last-ever row (closeout retries stopped 08:58 CT) and `positions`' last-ever row
(the final two stuck NO positions, `KXTRUMPSAY-26JUL20-URAN` and `KXRT-ODY-95`, settled
NATURALLY — not via the broken closeout mechanism — at 9:51 AM and 10:20:56 AM CT respectively,
realizing +$0.06/+$0.11, both already included in the running total). `mmsell3_closeout` is
gated by `mmsell_closeout_enabled` (defaults False in code, toggled via a Railway env var not
visible in git) — it silently returns 0 every cycle now, which is why the retry-storm stopped
with no errors logged. The loop's flat live P&L across runs #65-68 was accurate the whole time,
not stale — do not re-flag this as a data-staleness concern going forward unless something
actually changes.** Suggestions are **recommendations only** — the loop never acts on them; the
user reviews and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-22 08:01 PM CDT (run #68)

**Second consecutive run with zero new settlements across the entire mmsell cohort AND weather
books (~16 hours now). Confirmed healthy, not stalled** — checked `bot_runs` directly: 84
completed cycles in the last 2 hours, zero non-completed, and every mmsell book's **open**
position count rose this run (new entries ARE happening, e.g. mmsell open 25→29, mmsell10
12→14, mmsell11 14→15). This reads as entries currently outpacing the rate markets are closing
— a timing artifact, not a worker or data problem.

**Live P&L (real money — mmsell3, corrected query):** stable, no change — expected given the
2026-07-19 wind-down.
| bucket | n settled | wins | total P&L | ¢/contract |
|---|---|---|---|---|
| non-WC | 202 | 185 | +$1.75 | +0.86¢ |
| World Cup | 165 | 150 | −$0.41 | −0.25¢ |
| **TOTAL** | **367** | **335** | **+$1.33** | **+0.36¢** |

**Trading books (settled n / P&L / per-trade / open) — PAPER only, separate from live above:**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **mmsell6** | 345 | +$8.95 | +2.59 | 14 | PROMOTE confirmed, no new settlements, +2 open |
| **mmsell11** | 246 | +$8.41 | +3.42 | 15 | PROMOTE confirmed, no new settlements, +1 open |
| mmsell10 | 119 | +$4.45 | +3.74 | 14 | 79% to its own gate, no new settlements, +2 open |
| mmsell9 | 25 | +$1.36 | +5.4 | 6 | no new settlements |
| mmsell control (paper) | 3,879 | +$68.30 | +1.76 | 29 | no new settlements, +4 open |
| mmsell2 (paper) | 1,650 | +$50.47 | +3.06 | 16 | no new settlements, +1 open |
| mmsell1 (paper) | 2,508 | +$53.78 | +2.14 | 21 | no new settlements, +2 open |
| mmsell3 (paper shadow) | 1,020 | +$16.77 | +1.64 | 15 | no new settlements, +1 open |
| mmsell5 | 115 | −$0.09 | −0.08 | 0 | no new settlements |
| mmsell4 | 194 | +$1.77 | +0.91 | 14 | KILLED (run #61) — still not recorded, +1 open |
| mmsell7 | 53 | −$0.20 | −0.38 | 2 | no new settlements |
| mmsell8 | 29 | −$0.65 | −2.24 | 7 | no new settlements |
| **theta4** (fat-tail) | 44 | +$15.78 | +35.9 | 0 | no new activity, 55% to gate |
| weather con (all) | 489 | −$13.67 | −2.79 | 15 | no new settlements, +4 open |
| weather_concity | 76 | −$8.88 | −11.68 | 8 | no new settlements, +2 open |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — a quiet-for-settlements but active-for-entries run: no book settled anything new,
but every book grew its open count, and the worker's own run history is clean (84/84 completed
in the last 2h). No gate crossings, live P&L unchanged as expected.**

Worth a note for next run: if the settlement drought continues into run #69 (24+ hours with zero
settlements), that would be worth a closer look even with a clean bot_runs history — right now
one extra data point (rising open counts) fully explains it.

**Gate sweep (step 3b):** theta4 **44/80** (55%, no new activity) · **mmsell6
CLEARED-PROMOTE** · **mmsell11 CLEARED-PROMOTE** · **mmsell4 KILLED** (unchanged, still not
recorded — now 7 runs) · mmsell7 gate n≥150 (35%) · mmsell8 gate n≥100 (29%) · mmsell9 gate
n≥100 (25%) · mmsell10 gate n≥150 (79%, unchanged) · weather_concity **76/120** (63%, unchanged)
· FREEZE **5/100** (not fired, unchanged, 20 runs).

**Data (last-24h / latest CDT, ~8:01 PM run):** crypto_spot, crypto_ladder, weather forecasts/
obs/ensembles/buckets all fresh (7:27–8:02 PM ✓). xgame_matches still dark (expected — book
KILLED, collector-only). xgame_tapes still 0 rows/24h — consistent with the healthy-lull
explanation confirmed several runs back, not re-flagging. All collectors clean.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** quiet run for settlements (second in a row, ~16h), but confirmed healthy — bot_runs
clean, open positions rising across every book. No gate crossings. Live P&L unchanged as
expected. Watching whether the settlement drought continues into run #69.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[mmsell6 AND mmsell11 still PROMOTE — top actionable item] mmsell6: n=345, +2.59¢/trade.
   mmsell11: n=246, +3.42¢/trade.** No new settlements for two runs now, unchanged. Unchanged
   recommendation: a fable session should decide whether to promote one, both, or combine the
   mechanisms into the paper config — live mmsell3 itself is currently wound down, so any
   promotion is about the paper book / a future live restart.

2. **[mmsell3_closeout retry-loop — cosmetic bug, low priority] Still retrying/failing per the
   2026-07-19 wind-down; zero fills, ~$0.24 stray exposure.** Worth a fable session fixing the
   closeout body eventually; no real money or urgency behind it.

3. **[mmsell4 · KILL verdict — still not recorded, 7 runs now] n=194, +0.91¢/trade cumulative,
   still below mmsell3's +1.64¢.** Recommend a fable session record the verdict in
   `docs/MMSELL_VARIANTS_THESIS.md`/`RESEARCH_JOURNAL.md`.

4. **[mmsell10 · very close, gate 79%, unchanged] n=119/150, +3.74¢/trade cumulative.** Likely
   resolves once settlements resume.

5. **[weather_concity · gate 63%, unchanged] n=76/120, −11.68¢/trade cumulative.** No change this
   run; still approaching its decision point.

6. **[theta4 · 55% to gate, no new activity] n=44/80, cumulative +35.9¢/trade.** Continue
   tracking toward the gate.

7. **[idea-model queue · MMX/NEST] MMX's premise (extend the mmsell edge into new categories)
   should be built against whichever of mmsell6/mmsell11 gets promoted.** NEST still behind
   theta4's n≥80 gate (55% there).

8. **[FREEZE gate · unchanged, not fired] Settled grain+soft = 5 of the n≥100 trigger, unchanged
   across 20 runs now.** Standing background check, nothing to act on.

*(Changed this run: #1-8 all restated/unchanged — a genuinely quiet run for settlements, though
open-position counts rose across the board confirming the worker is healthy and actively
trading. New process note added to the snapshot (not a numbered suggestion): if the settlement
drought continues into run #69, worth a closer look even though bot_runs is clean right now.)*
