# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12.
As of run #56, the loop also pulls real live P&L for any LIVE book via a direct SQL query. As of
run #65 (corrected), the live P&L query filters `strategy='mmsell3'` exactly — NOT `LIKE
'mmsell%'`, which incorrectly sweeps in the `mmsell3_closeout` wind-down tag and double-counts
tickers. mmsell3 LIVE trading itself was wound down 2026-07-19 (see run #65's snapshot and
`docs/MMSELL_LIVE_POSTMORTEM.md`) — new live settlements should be rare/none going forward; flat
live P&L is expected, not a red flag. Suggestions are **recommendations only** — the loop never
acts on them; the user reviews and runs fable to change anything. Newest snapshot replaces the
one above it; the suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-22 12:48 PM CDT (run #67)

**Quiet run for mmsell — zero new settlements across the ENTIRE mmsell cohort (all 11 books
unchanged n).** Unusual but not alarming: likely a slow midday-weekday window with fewer
qualifying markets settling, not a collector or worker problem (data freshness below is clean
and `weather_con`/`weather_concity` both settled new trades normally in the same window).

**Weather books had a rough batch — worth a one-line flag, not overreacting (still small-n):**
| book | n | ¢/trade (cum) | this batch |
|---|---|---|---|
| weather_con(all) | 489 (+17) | −2.79¢ (was −2.25¢) | −18.1¢/trade batch |
| weather_concity | 76 (+8) | −11.68¢ (was −8.26¢) | **−40.8¢/trade batch** |

weather_concity's batch was its weakest in a while — still well within normal variance for a
book this size, but the cumulative moved enough to note. Now 76/120 (63%) to its gate; the
weaker batch doesn't change the direction of that number much, worth watching whether it
continues down toward the gate at a less-favorable level than the previous 68/120 read.

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
| **mmsell6** | 345 | +$8.95 | +2.59 | 12 | PROMOTE confirmed, no new settlements this run |
| **mmsell11** | 246 | +$8.41 | +3.42 | 14 | PROMOTE confirmed, no new settlements this run |
| mmsell10 | 119 | +$4.45 | +3.74 | 12 | 79% to its own gate, no new settlements |
| mmsell9 | 25 | +$1.36 | +5.4 | 6 | no new settlements |
| mmsell control (paper) | 3,879 | +$68.30 | +1.76 | 25 | no new settlements |
| mmsell2 (paper) | 1,650 | +$50.47 | +3.06 | 15 | no new settlements |
| mmsell1 (paper) | 2,508 | +$53.78 | +2.14 | 19 | no new settlements |
| mmsell3 (paper shadow) | 1,020 | +$16.77 | +1.64 | 14 | no new settlements |
| mmsell5 | 115 | −$0.09 | −0.08 | 0 | no new settlements |
| mmsell4 | 194 | +$1.77 | +0.91 | 13 | KILLED (run #61) — still not recorded, no new settlements |
| mmsell7 | 53 | −$0.20 | −0.38 | 2 | no new settlements |
| mmsell8 | 29 | −$0.65 | −2.24 | 7 | no new settlements |
| **theta4** (fat-tail) | 44 | +$15.78 | +35.9 | 0 | no new activity, 55% to gate |
| weather con (all) | 489 | −$13.67 | −2.79 | 11 | +17 new settled, weak batch (see note above) |
| weather_concity | 76 | −$8.88 | −11.68 | 6 | +8 new settled, weak batch, 63% to gate |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — a genuinely quiet run for the mmsell cohort (zero settlements across all 11 books,
likely just a slow window) alongside a weaker-than-usual batch for both weather books. Live P&L
unchanged as expected. No gate crossings.**

**Gate sweep (step 3b):** theta4 **44/80** (55%, no new activity) · **mmsell6
CLEARED-PROMOTE** · **mmsell11 CLEARED-PROMOTE** · **mmsell4 KILLED** (unchanged, still not
recorded — now 6 runs) · mmsell7 gate n≥150 (35%) · mmsell8 gate n≥100 (29%) · mmsell9 gate
n≥100 (25%) · mmsell10 gate n≥150 (79%, unchanged) · weather_concity **76/120** (63%, up from
57%) · FREEZE **5/100** (not fired, unchanged, 19 runs).

**Data (last-24h / latest CDT, ~12:48 PM run):** crypto_spot, crypto_ladder, weather forecasts/
obs/ensembles/buckets all fresh (12:21–12:48 PM ✓). xgame_matches still dark (expected — book
KILLED, collector-only). xgame_tapes still 0 rows/24h — consistent with the healthy-lull
explanation confirmed a few runs back, not re-flagging. All collectors clean, confirming the
mmsell settlement drought is a market-activity lull, not an infrastructure problem.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** quiet run, no gate events. Zero new mmsell settlements across all 11 books this
run (data collectors otherwise clean, so this reads as a slow window not a problem). Weather
books had a weaker batch than usual — weather_concity now 63% to its gate. Live P&L unchanged
as expected post-wind-down.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[mmsell6 AND mmsell11 still PROMOTE — top actionable item] mmsell6: n=345, +2.59¢/trade.
   mmsell11: n=246, +3.42¢/trade.** No new settlements this run, unchanged from run #66.
   Unchanged recommendation: a fable session should decide whether to promote one, both, or
   combine the mechanisms into the paper config — live mmsell3 itself is currently wound down, so
   any promotion is about the paper book / a future live restart.

2. **[mmsell3_closeout retry-loop — cosmetic bug, low priority] Still retrying/failing per the
   2026-07-19 wind-down; zero fills, ~$0.24 stray exposure.** Worth a fable session fixing the
   closeout body eventually; no real money or urgency behind it.

3. **[mmsell4 · KILL verdict — still not recorded, 6 runs now] n=194, +0.91¢/trade cumulative,
   still below mmsell3's +1.64¢.** Recommend a fable session record the verdict in
   `docs/MMSELL_VARIANTS_THESIS.md`/`RESEARCH_JOURNAL.md`.

4. **[mmsell10 · very close, gate 79%, unchanged] n=119/150, +3.74¢/trade cumulative.** Likely
   resolves within the next run or two once mmsell activity picks back up.

5. **[weather_concity · gate 63%, weaker batch] n=76/120, −11.68¢/trade cumulative (was
   −8.26¢) — this run's batch was notably weak (−40.8¢/trade over 8 trades).** Getting closer to
   its gate at a somewhat less favorable level than before; worth a closer look once it crosses
   n≥120, and watching whether the next batch reverses or continues the slide.

6. **[theta4 · 55% to gate, no new activity] n=44/80, cumulative +35.9¢/trade.** Continue
   tracking toward the gate.

7. **[idea-model queue · MMX/NEST] MMX's premise (extend the mmsell edge into new categories)
   should be built against whichever of mmsell6/mmsell11 gets promoted.** NEST still behind
   theta4's n≥80 gate (55% there).

8. **[FREEZE gate · unchanged, not fired] Settled grain+soft = 5 of the n≥100 trigger, unchanged
   across 19 runs now.** Standing background check, nothing to act on.

*(Changed this run: #1 mmsell6/mmsell11 — restated, no new activity. #2 mmsell3_closeout —
restated, cosmetic/low-priority. #3 mmsell4 — restated, 6 runs unrecorded. #4 mmsell10 —
unchanged (79%), no new activity. #5 weather_concity — closer to gate (63%, was 57%) but flagged
weaker batch. #6 theta4 — unchanged, no new activity. #7 MMX/NEST — restated. #8
restated/unchanged. NEW note: the entire mmsell cohort had zero new settlements this run — flagged
as a likely market lull given clean data collectors, not carried as its own numbered suggestion.)*
